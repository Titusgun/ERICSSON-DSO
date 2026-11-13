"""`dso export` (owner: C)

For every video and document writes two files into out/export/:

    <sha12>.json   AI-ready, exactly the format of DATA_CONTRACT 8. Meant for RAG /
                   AI tools: transcript segments with timestamps, the key frame that
                   was on screen and the slide it shows.
    <sha12>.md     The same content for humans: one heading per key frame with
                   timestamp, image and the transcript said while it was on screen.
                   For a document: one heading per slide with image and text.

Notes:
- <sha12> = first 12 hex chars of the file's SHA-256 (DATA_CONTRACT 3). Exact
  duplicate files have the same sha12, so each content is exported only once.
- Image paths in the DB are relative to --out (e.g. "frames/3fa9c0d1e2b4/f0012.webp").
  The .md file lives in out/export/, so its image links start with "../".
- duration_sec of a video = the latest frame/segment time in the DB. We do not
  open the original video, so export also works on a machine without it.
- Safe to run twice: every run overwrites the files.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from dso.common.db import connect

SCHEMA_VERSION = 1


def format_time(seconds):
    """96.0 -> "01:36", 3725.0 -> "1:02:05".

    (Same as in search.py; modules must not import each other, AGENTS.md 3.)
    """
    total = int(seconds)
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def rows_to_dicts(rows):
    """sqlite3.Row objects -> plain dicts (json.dump cannot write Row objects)."""
    result = []
    for row in rows:
        result.append(dict(row))
    return result


# ---------- 1. Read from the DB ----------

def source_info(file_row):
    """The "source" block of DATA_CONTRACT 8: where the content came from."""
    return {
        "file_name": file_row["name"],
        "path": file_row["path"],
        "sha256": file_row["sha256"],
        "size_bytes": file_row["size_bytes"],
        "kind": file_row["kind"],
        "system": file_row["source"],      # sharepoint / onedrive / play / local / offboarding
        "doc_number": file_row["doc_number"],
        "revision": file_row["revision"],
        "author": file_row["author"],
        "date": file_row["doc_date"],
    }


def load_frames(conn, file_id):
    """Key frames of one video, in time order."""
    rows = conn.execute(
        """SELECT id, t_sec, image_path AS image, dhash, slide_id, match_dist
           FROM frames WHERE file_id = ? ORDER BY t_sec""", (file_id,)).fetchall()
    return rows_to_dicts(rows)


def load_segments(conn, file_id):
    """Transcript segments of one video, in time order.

    slide_id is not stored on the segment itself: it comes from the frame the
    segment points at (set by `dso link`), hence the LEFT JOIN. LEFT JOIN keeps
    segments that have no frame; their slide_id is then None.
    """
    rows = conn.execute(
        """SELECT segments.id, segments.start_sec, segments.end_sec, segments.speaker,
                  segments.text, segments.frame_id, frames.slide_id AS slide_id
           FROM segments LEFT JOIN frames ON frames.id = segments.frame_id
           WHERE segments.file_id = ? ORDER BY segments.start_sec""", (file_id,)).fetchall()
    return rows_to_dicts(rows)


def load_slides_of_video(conn, file_id):
    """Every slide shown in a video (via its frames), each slide once, in order of
    first appearance. The slides may come from several decks."""
    rows = conn.execute(
        """SELECT slides.slide_id, slides.text, slides.image_path AS image,
                  MIN(frames.t_sec) AS first_seen
           FROM frames JOIN slides ON slides.slide_id = frames.slide_id
           WHERE frames.file_id = ?
           GROUP BY slides.slide_id
           ORDER BY first_seen""", (file_id,)).fetchall()
    slides = []
    for row in rows:
        # first_seen was only needed for sorting; DATA_CONTRACT 8 has 3 fields.
        slides.append({"slide_id": row["slide_id"], "text": row["text"], "image": row["image"]})
    return slides


def load_slides_of_document(conn, file_id):
    """All slides (pages) of one document, in page order."""
    rows = conn.execute(
        """SELECT slide_id, text, image_path AS image
           FROM slides WHERE file_id = ? ORDER BY page_no""", (file_id,)).fetchall()
    return rows_to_dicts(rows)


def video_duration(frames, segments):
    """Latest timestamp we know: last frame time or last segment end."""
    duration = 0.0
    for frame in frames:
        duration = max(duration, frame["t_sec"])
    for segment in segments:
        duration = max(duration, segment["end_sec"])
    return duration


def build_export(conn, file_row, generated_at):
    """Builds the DATA_CONTRACT 8 dict for one video or document."""
    if file_row["kind"] == "video":
        frames = load_frames(conn, file_row["id"])
        segments = load_segments(conn, file_row["id"])
        slides = load_slides_of_video(conn, file_row["id"])
        duration = video_duration(frames, segments)
    else:
        # Documents: same shape, no frames/segments, and their own slides.
        frames = []
        segments = []
        slides = load_slides_of_document(conn, file_row["id"])
        duration = None
    return {
        "schema_version": SCHEMA_VERSION,
        "source": source_info(file_row),
        "generated_at": generated_at,
        "duration_sec": duration,
        "frames": frames,
        "segments": segments,
        "slides_referenced": slides,
    }


# ---------- 2. Write Markdown ----------

def segment_line(segment):
    """One transcript line: "- **[01:36] Speaker ?:** As you see here..."."""
    return f"- **[{format_time(segment['start_sec'])}] {segment['speaker']}:** {segment['text']}"


def video_markdown(data):
    """Markdown for a video: one section per key frame with the speech under it."""
    lines = []
    source = data["source"]
    lines.append(f"# {source['file_name']}")
    lines.append("")
    lines.append(f"- Duration: {format_time(data['duration_sec'])}")
    lines.append(f"- Key frames: {len(data['frames'])}, transcript segments: {len(data['segments'])}")
    lines.append(f"- Source: {source['system']}, SHA-256 `{source['sha256']}`")
    lines.append("")

    # Group segments by the frame they point at: {frame_id: [segment, ...]}.
    by_frame = {}
    before_first_frame = []
    for segment in data["segments"]:
        if segment["frame_id"] is None:
            before_first_frame.append(segment)
        else:
            if segment["frame_id"] not in by_frame:
                by_frame[segment["frame_id"]] = []
            by_frame[segment["frame_id"]].append(segment)

    # Speech before the first key frame (or when `dso link` has not run yet).
    if len(before_first_frame) > 0:
        lines.append("## Transcript without a key frame")
        lines.append("")
        for segment in before_first_frame:
            lines.append(segment_line(segment))
        lines.append("")

    for frame in data["frames"]:
        heading = f"## {format_time(frame['t_sec'])}"
        if frame["slide_id"] is not None:
            heading = heading + f" - slide `{frame['slide_id']}`"
        lines.append(heading)
        lines.append("")
        lines.append(f"![frame at {format_time(frame['t_sec'])}](../{frame['image']})")
        lines.append("")
        for segment in by_frame.get(frame["id"], []):
            lines.append(segment_line(segment))
        lines.append("")

    if len(data["slides_referenced"]) > 0:
        lines.append("## Slides shown in this recording")
        lines.append("")
        for slide in data["slides_referenced"]:
            lines.append(f"### `{slide['slide_id']}`")
            lines.append("")
            lines.append(f"![slide](../{slide['image']})")
            lines.append("")
            lines.append(slide["text"])
            lines.append("")
    return "\n".join(lines)


def document_markdown(data):
    """Markdown for a document: one section per slide with image and text."""
    lines = []
    source = data["source"]
    lines.append(f"# {source['file_name']}")
    lines.append("")
    lines.append(f"- Slides: {len(data['slides_referenced'])}")
    lines.append(f"- Source: {source['system']}, SHA-256 `{source['sha256']}`")
    lines.append("")
    number = 1
    for slide in data["slides_referenced"]:
        lines.append(f"## Slide {number} - `{slide['slide_id']}`")
        lines.append("")
        lines.append(f"![slide {number}](../{slide['image']})")
        lines.append("")
        lines.append(slide["text"])
        lines.append("")
        number = number + 1
    return "\n".join(lines)


# ---------- 3. The command ----------

def run(args):
    conn = connect(args.db)
    export_dir = Path(args.out) / "export"
    export_dir.mkdir(parents=True, exist_ok=True)

    # One timestamp for the whole run, in UTC, e.g. "2026-11-14T10:00:00Z".
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    files = conn.execute(
        """SELECT * FROM files WHERE kind IN ('video', 'document')
           ORDER BY kind DESC, id""").fetchall()     # 'video' before 'document'
    print(f"[export] {len(files)} videos/documents -> {export_dir.as_posix()}/")

    done_hashes = set()     # SHA-256 values already exported (exact duplicates)
    written = 0
    for file_row in files:
        if file_row["sha256"] in done_hashes:
            print(f"[export]   skip {file_row['name']} (exact duplicate, already exported)")
            continue
        done_hashes.add(file_row["sha256"])

        data = build_export(conn, file_row, generated_at)
        sha12 = file_row["sha256"][:12]

        json_path = export_dir / f"{sha12}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            # ensure_ascii=False keeps letters like "ā" or "ö" readable in the file.
            json.dump(data, f, indent=2, ensure_ascii=False)

        if file_row["kind"] == "video":
            markdown = video_markdown(data)
        else:
            markdown = document_markdown(data)
        md_path = export_dir / f"{sha12}.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(markdown)

        written = written + 1
        print(f"[export]   {file_row['name']} -> {sha12}.json + .md "
              f"({len(data['frames'])} frames, {len(data['segments'])} segments, "
              f"{len(data['slides_referenced'])} slides)")

    conn.close()
    print(f"[export] {written} exports written")
    return 0