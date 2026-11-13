"""`dso cleanup <folder> --rclone REMOTE:PATH [--apply]` (owner: D) - actually free
storage in the cloud, after the content has been kept in reduced form.

The brief: "clean out large files but still keep their information at minimum
cost" and "deleted only if a duplicate already exists". For files that
`dso watch --rclone` downloaded from OneDrive / SharePoint into <folder>, the
cloud original is removed when its information is safe elsewhere:

  video            the transcript AND the key frames cover the WHOLE video,
                   and its AI export exists (a --max-seconds run that stopped
                   early does not count)
  exact copy       `dedup` found it as a file_exact copy, and the kept copy
                   still exists
  older revision   `dedup` found a newer version (revision / file_content), the
                   newer file still exists, and this version's slides are kept
                   under out/slides/

Everything else (unique documents, the kept copies, the newest revisions) stays.

Safety rules (DECISIONS.md, AGENTS.md 8):
  - without --apply it only prints the plan (dry run)
  - only files inside the watched cloud folder; local files are never deleted
  - right before deleting, the cloud file must still be byte-identical to the
    file we processed (OneDrive's QuickXorHash of the cloud file = of our copy,
    and our copy's SHA-256 = the one in the DB)
  - files go to the OneDrive / SharePoint RECYCLE BIN (restorable, 93 days by
    default): --onedrive-hard-delete=false is always passed
  - every deletion is logged in out/reports/cleanup.jsonl + cleanup.md
"""
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from dso.common.db import connect
from dso.common.hashing import sha256_file, sha12

RCLONE_TIMEOUT_SEC = 600


def human_size(n):
    """Bytes as a short readable string, e.g. 1.4 GB."""
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f} {unit}"
        n = n / 1024
    return f"{n:.1f} TB"


def relative_to_folder(path_text, folder):
    """The file's path inside `folder` with "/" (e.g. "talks/a.mp4"), or None if
    the file is not inside that folder."""
    path = Path(path_text).resolve()
    folder = Path(folder).resolve()
    if not path.is_relative_to(folder):
        return None
    return path.relative_to(folder).as_posix()


def transcript_covers_video(out_dir, sha256, duration_sec):
    """True if out/transcripts/<sha12>.json was made for the whole video: no
    --max-seconds, or a limit at least as long as the video."""
    path = out_dir / "transcripts" / f"{sha12(sha256)}.json"
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    limit = data.get("max_seconds")
    if limit is None:
        return True
    return duration_sec is not None and limit >= duration_sec - 1     # 1 s rounding


def frames_cover_video(out_dir, sha256, duration_sec):
    """True if the key frames were taken for the whole video (frames_info.json
    written by `dso video`; frames from before that file existed were always
    taken for the whole video)."""
    path = out_dir / "frames" / sha12(sha256) / "frames_info.json"
    try:
        with open(path, encoding="utf-8") as f:
            limit = json.load(f).get("max_seconds")
    except (OSError, ValueError):
        return True
    if limit is None:
        return True
    return duration_sec is not None and limit >= duration_sec - 1


def reference_map(conn, kinds):
    """{older/copy file id: kept file id} from `duplicates` rows of these kinds
    (a_ref = the one that can go, b_ref = the one that stays)."""
    result = {}
    marks = ",".join("?" for k in kinds)
    for row in conn.execute(f"SELECT a_ref, b_ref FROM duplicates WHERE kind IN ({marks})", kinds):
        result[int(row["a_ref"])] = int(row["b_ref"])
    return result


def plan(conn, folder, out_dir):
    """Which files inside `folder` can be removed from the cloud, and why.
    Returns a list of dicts {"id", "name", "path", "relative", "size_bytes",
    "sha256", "reason", "kept_at"}."""
    files = {}
    for row in conn.execute("SELECT * FROM files"):
        files[row["id"]] = row
    copy_of = reference_map(conn, ["file_exact"])
    older_than = reference_map(conn, ["revision", "file_content"])

    items = []
    for file_id, row in files.items():
        relative = relative_to_folder(row["path"], folder)
        if relative is None:
            continue
        reason = None
        kept_at = None
        if file_id in copy_of and copy_of[file_id] in files:
            kept = files[copy_of[file_id]]
            if Path(kept["path"]).is_file():
                reason = f"exact copy of {kept['name']}"
                kept_at = kept["path"]
        elif file_id in older_than and older_than[file_id] in files:
            newer = files[older_than[file_id]]
            slides_kept = (out_dir / "slides" / sha12(row["sha256"])).is_dir()
            if Path(newer["path"]).is_file() and slides_kept:
                reason = f"older version of {newer['name']}; its slides are kept"
                kept_at = newer["path"]
        elif row["kind"] == "video":
            frames = conn.execute("SELECT COUNT(*) FROM frames WHERE file_id = ?", (file_id,)).fetchone()[0]
            export = out_dir / "export" / f"{sha12(row['sha256'])}.md"
            if (frames > 0 and export.is_file()
                    and frames_cover_video(out_dir, row["sha256"], row["duration_sec"])
                    and transcript_covers_video(out_dir, row["sha256"], row["duration_sec"])):
                reason = f"video distilled: {frames} key frames + full transcript + AI export"
                kept_at = f"_dso_distilled/export/{sha12(row['sha256'])}.md"
        if reason is not None:
            items.append({"id": file_id, "name": row["name"], "path": row["path"],
                          "relative": relative, "size_bytes": row["size_bytes"],
                          "sha256": row["sha256"], "reason": reason, "kept_at": kept_at})
    items.sort(key=lambda item: item["relative"])
    return items


def cloud_file(remote, relative):
    """REMOTE:PATH + the file's path inside the watched folder."""
    return remote.rstrip("/") + "/" + relative


def quickxor(target):
    """OneDrive's QuickXorHash of a local file or a cloud file (via rclone),
    or None if it cannot be read (e.g. the cloud file is already gone)."""
    try:
        result = subprocess.run(["rclone", "hashsum", "quickxor", target],
                                capture_output=True, text=True, timeout=RCLONE_TIMEOUT_SEC)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or result.stdout.strip() == "":
        return None
    return result.stdout.split()[0]


def delete_to_recycle_bin(target):
    """`rclone deletefile`; --onedrive-hard-delete=false makes sure the file goes
    to the OneDrive / SharePoint recycle bin, whatever the rclone config says.
    Returns (ok, error text)."""
    command = ["rclone", "deletefile", target, "--onedrive-hard-delete=false"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=RCLONE_TIMEOUT_SEC)
    except (OSError, subprocess.SubprocessError) as error:
        return False, str(error)
    return result.returncode == 0, result.stderr.strip()[:300]


def apply(items, remote):
    """Deletes the planned cloud files after the integrity check. Returns one
    log entry per item: {"time", "file", "cloud_path", "size_bytes", "reason",
    "kept_at", "result"} with result "deleted" or "skipped: <why>"."""
    entries = []
    for item in items:
        target = cloud_file(remote, item["relative"])
        local = Path(item["path"])
        result = "deleted (in the recycle bin)"
        if not local.is_file():
            result = "skipped: our local copy is missing, cannot verify"
        elif sha256_file(local) != item["sha256"]:
            result = "skipped: the file changed since it was processed"
        else:
            local_hash = quickxor(str(local))
            cloud_hash = quickxor(target)
            if cloud_hash is None:
                result = "skipped: not in the cloud (already deleted?)"
            elif local_hash != cloud_hash:
                result = "skipped: the cloud file differs from what we processed"
            else:
                ok, error = delete_to_recycle_bin(target)
                if not ok:
                    result = f"skipped: rclone could not delete it ({error})"
        print(f"[cleanup] {result}: {item['relative']} ({human_size(item['size_bytes'])}) - {item['reason']}")
        entries.append({
            "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "file": item["name"], "cloud_path": target, "size_bytes": item["size_bytes"],
            "reason": item["reason"], "kept_at": item["kept_at"], "result": result,
        })
    return entries


def write_log(out_dir, entries):
    """Appends to out/reports/cleanup.jsonl and rewrites cleanup.md from it."""
    reports = out_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    log = reports / "cleanup.jsonl"
    with open(log, "a", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    all_entries = []
    with open(log, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                all_entries.append(json.loads(line))
    freed = 0
    for entry in all_entries:
        if entry["result"].startswith("deleted"):
            freed += entry["size_bytes"]
    lines = [
        "# Cloud cleanup log",
        "",
        f"**{human_size(freed)} freed in the cloud.** Deleted files are in the OneDrive / "
        "SharePoint recycle bin (restorable, 93 days by default).",
        "",
        "| Time (UTC) | File | Size | Why it could go | Kept | Result |",
        "|---|---|---:|---|---|---|",
    ]
    for entry in all_entries:
        lines.append(f"| {entry['time']} | {entry['file']} | {human_size(entry['size_bytes'])} | "
                     f"{entry['reason']} | `{entry['kept_at']}` | {entry['result']} |")
    (reports / "cleanup.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return freed


def cleanup(args, do_apply):
    """Plan (and with do_apply: delete) for args.folder / args.rclone.
    Returns the number of cloud files deleted."""
    out_dir = Path(args.out)
    conn = connect(args.db)
    items = plan(conn, Path(args.folder), out_dir)
    conn.close()
    if len(items) == 0:
        print("[cleanup] nothing to remove: no distilled videos, exact copies or older revisions")
        return 0
    total = 0
    for item in items:
        total += item["size_bytes"]
    if not do_apply:
        print(f"[cleanup] DRY RUN - would move {len(items)} cloud file(s), {human_size(total)}, "
              "to the recycle bin (add --apply):")
        for item in items:
            print(f"[cleanup]   {item['relative']} ({human_size(item['size_bytes'])}) - {item['reason']}")
        return 0
    entries = apply(items, args.rclone)
    freed = write_log(out_dir, entries)
    deleted = 0
    for entry in entries:
        if entry["result"].startswith("deleted"):
            deleted += 1
    print(f"[cleanup] {deleted} of {len(items)} deleted; {human_size(freed)} freed in the cloud "
          f"so far -> {(out_dir / 'reports' / 'cleanup.md').as_posix()}")
    return deleted


def run(args):
    """`dso cleanup`: dry run unless --apply."""
    cleanup(args, getattr(args, "apply", False))
    return 0
