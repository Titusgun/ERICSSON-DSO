"""`dso offboard <folder>` (owner: D). ALWAYS A DRY RUN.

The departing employee's folder is ingested first (`dso ingest <folder> --source
offboarding --owner <name>`). For each file inside that folder:
  - same sha256 exists outside the folder -> "safe to delete" (copy kept at <path>)
  - same sha256 earlier in the same folder -> "safe to delete" (copy inside folder)
  - otherwise                               -> "keep" (unique, keep / distill)
Writes one report per leaver, out/reports/offboarding/<folder name>.md + .json, and
the combined view of ALL leavers analysed so far: out/reports/offboarding.md and
out/reports/offboarding.json (for the web UI, GET /api/offboarding).
A copy in ANOTHER leaver's folder does not count as "kept elsewhere".

Compares sha256 in the `files` table directly, so it does not depend on `dso dedup`.
This code never deletes, moves or modifies user files. It only reads the DB and
writes the report.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from dso.common.db import connect

SAFE = "safe to delete"
KEEP = "keep"


def is_inside(path_text, folder):
    """True if the stored file path lies inside `folder` (both resolved)."""
    path = Path(path_text).resolve()
    return path.is_relative_to(folder)


def plan(conn, folder):
    """Decide keep / safe to delete for every ingested file inside `folder`.

    Returns a list of dicts sorted by path:
      {"path", "name", "owner", "size_bytes", "sha256", "verdict", "copy_at"}
    copy_at is the path of the copy that stays (None for "keep").
    """
    folder = Path(folder).resolve()
    rows = conn.execute(
        "SELECT id, path, name, owner, source, size_bytes, sha256 FROM files ORDER BY path, id").fetchall()

    inside = []
    outside_by_sha = {}   # sha256 -> path of the first file with that content outside the folder
    for row in rows:
        if is_inside(row["path"], folder):
            inside.append(row)
        elif row["source"] == "offboarding":
            # A copy in ANOTHER leaver's folder does not count: that person is
            # leaving too, so both copies could be cleaned up and the content lost.
            continue
        elif row["sha256"] not in outside_by_sha:
            outside_by_sha[row["sha256"]] = row["path"]

    result = []
    first_inside_by_sha = {}   # sha256 -> path of the first leaver file with that content
    for row in inside:
        sha = row["sha256"]
        if sha in outside_by_sha:
            verdict = SAFE
            copy_at = outside_by_sha[sha]
        elif sha in first_inside_by_sha:
            verdict = SAFE
            copy_at = first_inside_by_sha[sha]
        else:
            verdict = KEEP
            copy_at = None
            first_inside_by_sha[sha] = row["path"]
        result.append({
            "path": row["path"],
            "name": row["name"],
            "owner": row["owner"] or "",      # "" if ingested without --owner
            "size_bytes": row["size_bytes"],
            "sha256": sha,
            "verdict": verdict,
            "copy_at": copy_at,
        })
    return result


def build_summary(items, simulated):
    """The JSON for the web UI (GET /api/offboarding): totals + one entry per file.

    Shape (agreed with the dso-ui Offboarding tab):
      {dry_run, simulated, owners, total_files, total_bytes, safe_files, keep_files,
       freeable_bytes, percent_freed, files: [{name, path, owner, size_bytes,
       sha256, verdict, copy_at}]}
    percent_freed is a percentage (74.3 means 74.3 %), rounded to 1 decimal.
    """
    total_bytes = 0
    freeable_bytes = 0
    safe_files = 0
    owners = []
    for item in items:
        total_bytes += item["size_bytes"]
        if item["verdict"] == SAFE:
            freeable_bytes += item["size_bytes"]
            safe_files += 1
        if item["owner"] != "" and item["owner"] not in owners:
            owners.append(item["owner"])
    percent = 0.0
    if total_bytes > 0:
        percent = round(100.0 * freeable_bytes / total_bytes, 1)
    return {
        "dry_run": True,                  # always: this code never deletes anything
        "simulated": simulated,
        "owners": owners,
        "total_files": len(items),
        "total_bytes": total_bytes,
        "safe_files": safe_files,
        "keep_files": len(items) - safe_files,
        "freeable_bytes": freeable_bytes,
        "percent_freed": percent,
        "files": items,
    }


def write_json(path, summary):
    """Write the summary for the web UI to out/reports/offboarding.json."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        # ensure_ascii=False keeps names like "Māris Līveris" readable in the file.
        json.dump(summary, f, indent=2, ensure_ascii=False)


def human_size(n):
    """Bytes as a short readable string, e.g. 1.4 GB."""
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f} {unit}"
        n = n / 1024
    return f"{n:.1f} TB"


def write_report(path, folder, items, simulated):
    """Write the Markdown report: banner, summary, one table row per file."""
    total = 0
    freeable = 0
    safe_count = 0
    for item in items:
        total += item["size_bytes"]
        if item["verdict"] == SAFE:
            freeable += item["size_bytes"]
            safe_count += 1
    percent = 0.0
    if total > 0:
        percent = 100.0 * freeable / total

    lines = [
        "# Offboarding report",
        "",
        "> **DRY RUN.** Nothing was deleted or moved. This is a recommendation only.",
    ]
    if simulated:
        lines.append("> **SIMULATED SCENARIO.** This leaver folder was built by the team "
                     "from sample files; it is not a real employee's folder.")
    lines = lines + [
        "",
        f"Folder: `{Path(folder).as_posix()}`  ",
        "Generated " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") + " by `dso offboard`.",
        "",
        f"**{len(items)} files, {human_size(total)}. "
        f"{safe_count} safe to delete, {human_size(freeable)} could be freed ({percent:.1f}%).**",
        "",
        "| File | Size | Verdict | Copy kept at |",
        "|---|---:|---|---|",
    ]
    for item in items:
        copy_at = ""
        if item["copy_at"] is not None:
            copy_at = "`" + item["copy_at"] + "`"
        lines.append(f"| {item['name']} | {human_size(item['size_bytes'])} | {item['verdict']} | {copy_at} |")
    lines.append("")
    lines.append("Rule: a file is safe to delete only if a byte-identical copy (same SHA-256) "
                 "is kept elsewhere. Everything else is kept.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return total, freeable


def leaver_file_name(folder):
    """A safe file name for one leaver's report: the folder name without
    characters that are not allowed in file names ("Māris Līveris" stays)."""
    name = Path(folder).name
    for bad in '<>:"/\\|?*':
        name = name.replace(bad, "_")
    return name.strip() or "leaver"


def combine_reports(reports_dir):
    """Builds the combined offboarding.json + offboarding.md from all per-leaver
    reports in out/reports/offboarding/, so the web UI and OneDrive show every
    leaver, not only the last one analysed. Returns the combined summary."""
    leavers = []
    for path in sorted((reports_dir / "offboarding").glob("*.json")):
        with open(path, encoding="utf-8") as f:
            leavers.append(json.load(f))
    files = []
    simulated = len(leavers) > 0
    for leaver in leavers:
        if not leaver["simulated"]:
            simulated = False
        for item in leaver["files"]:
            if item["owner"] == "":
                item["owner"] = leaver["leaver"]     # ingested without --owner
            files.append(item)
    combined = build_summary(files, simulated)
    combined["leavers"] = []
    for leaver in leavers:
        combined["leavers"].append({"leaver": leaver["leaver"], "folder": leaver["folder"],
                                    "total_files": leaver["total_files"], "freeable_bytes": leaver["freeable_bytes"],
                                    "percent_freed": leaver["percent_freed"], "simulated": leaver["simulated"]})
    write_json(reports_dir / "offboarding.json", combined)

    lines = [
        "# Offboarding reports",
        "",
        "> **DRY RUN.** Nothing was deleted or moved. These are recommendations only.",
        "",
        f"**{len(leavers)} leaver(s), {combined['total_files']} files, {human_size(combined['total_bytes'])}. "
        f"{combined['safe_files']} safe to delete, {human_size(combined['freeable_bytes'])} could be freed "
        f"({combined['percent_freed']:.1f}%).**",
        "",
        "| Leaver | Files | Could be freed | Simulated |",
        "|---|---:|---:|---|",
    ]
    for leaver in combined["leavers"]:
        lines.append(f"| {leaver['leaver']} | {leaver['total_files']} | {human_size(leaver['freeable_bytes'])} "
                     f"({leaver['percent_freed']:.1f}%) | {'yes' if leaver['simulated'] else 'no'} |")
    lines.append("")
    lines.append("Details per leaver: `offboarding/<name>.md` next to this file.")
    (reports_dir / "offboarding.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return combined


def run(args):
    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"[offboard] Not a folder: {folder}")
        return 1

    conn = connect(args.db)
    print(f"[offboard] DRY RUN for {folder.as_posix()} - nothing will be deleted")
    items = plan(conn, folder)
    conn.close()
    if len(items) == 0:
        print("[offboard] No ingested files in this folder. Run first:")
        print(f'[offboard]   uv run dso ingest "{folder.as_posix()}" --source offboarding --owner <name>')
        return 1

    for item in items:
        print(f"[offboard] {item['verdict']:<15} {item['name']}")
    # One report per leaver (a new run for the same folder replaces only that one)...
    reports_dir = Path(args.out) / "reports"
    name = leaver_file_name(folder)
    report = reports_dir / "offboarding" / f"{name}.md"
    simulated = getattr(args, "simulated", False)
    total, freeable = write_report(report, folder, items, simulated)
    summary = build_summary(items, simulated)
    summary["leaver"] = Path(folder).name
    summary["folder"] = folder.as_posix()
    write_json(reports_dir / "offboarding" / f"{name}.json", summary)
    # ...and the combined view of ALL leavers for the web UI and the cloud.
    combined = combine_reports(reports_dir)
    print(f"[offboard] {human_size(freeable)} of {human_size(total)} could be freed")
    print(f"[offboard] report: {report.as_posix()}")
    print(f"[offboard] all leavers ({len(combined['leavers'])}): {(reports_dir / 'offboarding.md').as_posix()} "
          f"(web UI data: {(reports_dir / 'offboarding.json').as_posix()})")
    return 0
