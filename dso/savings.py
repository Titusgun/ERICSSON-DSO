"""`dso savings` (owner: D). Bytes before vs after, measured on the real files.

Scenarios:
  video_distill: sum(video sizes) vs sum(frames + transcript JSON + export files)
  dedup:         sum(all files)   vs sum(one copy per distinct sha256)
  offboarding:   sum(leaver files) vs sum(their unique files)
Store rows in `savings`, write out/reports/savings.md with percentages.

Only measures: reads the DB and file sizes under --out, never changes user files.
Safe to run twice: the savings table is cleared and filled again.
"""
from datetime import datetime, timezone
from pathlib import Path

from dso.common.db import connect


def folder_bytes(folder):
    """Total size of all files inside a folder (0 if it does not exist)."""
    total = 0
    if folder.is_dir():
        for path in folder.rglob("*"):
            if path.is_file():
                total += path.stat().st_size
    return total


def file_bytes(path):
    """Size of one file, 0 if it does not exist."""
    if path.is_file():
        return path.stat().st_size
    return 0


def video_distill(conn, out):
    """Original videos vs what we keep of them: key frames + transcript + export.

    Only videos that already have output under out/ are counted, so a video that
    `dso video` has not processed yet does not show up as a fake 100% saving.
    Each distinct video (sha256) is counted once; copies are the dedup scenario.
    Returns (bytes_before, bytes_after, videos_counted, videos_skipped).
    """
    rows = conn.execute(
        "SELECT sha256, MIN(size_bytes) AS size_bytes FROM files "
        "WHERE kind = 'video' GROUP BY sha256").fetchall()
    before = 0
    after = 0
    counted = 0
    skipped = 0
    for row in rows:
        sha12 = row["sha256"][:12]
        kept = folder_bytes(out / "frames" / sha12)
        kept += file_bytes(out / "transcripts" / (sha12 + ".json"))
        kept += file_bytes(out / "export" / (sha12 + ".json"))
        kept += file_bytes(out / "export" / (sha12 + ".md"))
        if kept == 0:
            skipped += 1
            continue
        before += row["size_bytes"]
        after += kept
        counted += 1
    return before, after, counted, skipped


def dedup(conn):
    """All files vs keeping only one copy of each distinct content (sha256)."""
    before = conn.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM files").fetchone()[0]
    after = conn.execute(
        "SELECT COALESCE(SUM(size_bytes), 0) FROM "
        "(SELECT MIN(size_bytes) AS size_bytes FROM files GROUP BY sha256)").fetchone()[0]
    return before, after


def offboarding(conn):
    """Leaver's files vs only the ones whose content exists nowhere else.

    Leaver files = source 'offboarding'. A leaver file is a duplicate if the same
    sha256 exists in a file with any other source. Copies inside the leaver's own
    folder are kept once.
    Returns (bytes_before, bytes_after, file_count).
    """
    before_row = conn.execute(
        "SELECT COALESCE(SUM(size_bytes), 0), COUNT(*) FROM files "
        "WHERE source = 'offboarding'").fetchone()
    after = conn.execute(
        "SELECT COALESCE(SUM(size_bytes), 0) FROM "
        "(SELECT MIN(size_bytes) AS size_bytes FROM files "
        " WHERE source = 'offboarding' AND sha256 NOT IN "
        "   (SELECT sha256 FROM files WHERE source != 'offboarding') "
        " GROUP BY sha256)").fetchone()[0]
    return before_row[0], after, before_row[1]


def human_size(n):
    """Bytes as a short readable string, e.g. 1.4 GB."""
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f} {unit}"
        n = n / 1024
    return f"{n:.1f} TB"


def percent_saved(before, after):
    if before == 0:
        return 0.0
    return 100.0 * (before - after) / before


def write_report(path, results, notes):
    """Write out/reports/savings.md: one table row per scenario, then notes."""
    lines = [
        "# Storage savings",
        "",
        "Generated " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        + " by `dso savings`. Measured on the files in the database.",
        "",
        "| Scenario | Before | After | Saved | Saved % |",
        "|---|---:|---:|---:|---:|",
    ]
    for scenario, before, after in results:
        lines.append(f"| {scenario} | {human_size(before)} | {human_size(after)} | "
                     f"{human_size(before - after)} | {percent_saved(before, after):.1f}% |")
    lines.append("")
    for note in notes:
        lines.append("- " + note)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args):
    out = Path(args.out)
    conn = connect(args.db)
    results = []   # (scenario, bytes_before, bytes_after)
    notes = []

    print("[savings] video_distill: measuring frames, transcripts, exports...")
    before, after, counted, skipped = video_distill(conn, out)
    if counted > 0:
        results.append(("video_distill", before, after))
        notes.append(f"video_distill: {counted} distinct video(s) measured.")
    if skipped > 0:
        notes.append(f"video_distill: {skipped} video(s) skipped, no output yet (run `dso video`).")

    print("[savings] dedup: one copy per distinct file content...")
    before, after = dedup(conn)
    results.append(("dedup", before, after))

    before, after, leaver_files = offboarding(conn)
    if leaver_files > 0:
        print("[savings] offboarding: leaver files that exist elsewhere...")
        results.append(("offboarding", before, after))
        notes.append(f"offboarding: {leaver_files} file(s) with source 'offboarding'.")
    else:
        notes.append("offboarding: no files ingested with --source offboarding.")

    conn.execute("DELETE FROM savings")
    for scenario, before, after in results:
        conn.execute("INSERT INTO savings (scenario, bytes_before, bytes_after) VALUES (?, ?, ?)",
                     (scenario, before, after))
    conn.commit()
    conn.close()

    report = out / "reports" / "savings.md"
    write_report(report, results, notes)
    for scenario, before, after in results:
        print(f"[savings] {scenario}: {human_size(before)} -> {human_size(after)} "
              f"({percent_saved(before, after):.1f}% saved)")
    for note in notes:
        print("[savings] " + note)
    print(f"[savings] report: {report.as_posix()}")
    return 0
