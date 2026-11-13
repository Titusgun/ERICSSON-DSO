"""`dso offboard <folder>` (owner: D). ALWAYS A DRY RUN.

The departing employee's folder is ingested first (`dso ingest <folder> --source
offboarding --owner <name>`). For each file inside that folder:
  - same sha256 exists outside the folder -> "safe to delete" (copy kept at <path>)
  - same sha256 earlier in the same folder -> "safe to delete" (copy inside folder)
  - otherwise                               -> "keep" (unique, keep / distill)
Writes out/reports/offboarding.md with a table and the bytes that could be freed.

Compares sha256 in the `files` table directly, so it does not depend on `dso dedup`.
This code never deletes, moves or modifies user files. It only reads the DB and
writes the report.
"""
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
      {"path", "name", "size_bytes", "verdict", "copy_at"}
    copy_at is the path of the copy that stays (None for "keep").
    """
    folder = Path(folder).resolve()
    rows = conn.execute("SELECT id, path, name, size_bytes, sha256 FROM files ORDER BY path, id").fetchall()

    inside = []
    outside_by_sha = {}   # sha256 -> path of the first file with that content outside the folder
    for row in rows:
        if is_inside(row["path"], folder):
            inside.append(row)
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
            "size_bytes": row["size_bytes"],
            "verdict": verdict,
            "copy_at": copy_at,
        })
    return result


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
    report = Path(args.out) / "reports" / "offboarding.md"
    simulated = getattr(args, "simulated", False)
    total, freeable = write_report(report, folder, items, simulated)
    print(f"[offboard] {human_size(freeable)} of {human_size(total)} could be freed")
    print(f"[offboard] report: {report.as_posix()}")
    return 0
