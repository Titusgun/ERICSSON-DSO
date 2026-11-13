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

AI-ready corpus (data activation: "make content searchable and consumable by AI"):
    out/export/corpus.jsonl    ONE file with everything, one JSON object per line
                               ("chunk"), the usual input for vector databases,
                               RAG ("search + AI answers") and training data:
      - transcript chunks: consecutive sentences while the same key frame is on
        screen, at most CHUNK_MAX_SEC long, with time range, frame image and,
        if matched, the slide and its text ("as you see here" keeps its context)
      - slide chunks: one per slide with text, with where it was shown
        ("shown_in": recording + time) and "superseded_by" for old revisions
    out/reports/ai_readiness.md   measured coverage: what share of the content
                               is searchable, linked and in the corpus
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from dso.common.db import connect

SCHEMA_VERSION = 1

# A transcript chunk ends when the key frame changes or after this many seconds.
# 60 s of speech is ~150 words: small enough for AI search, big enough for context.
CHUNK_MAX_SEC = 60


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


# ---------- 3. AI-ready corpus (corpus.jsonl) ----------

def corpus_source(file_row):
    """The short "where does this come from" block every chunk carries."""
    return {
        "file_name": file_row["name"],
        "path": file_row["path"],
        "sha256": file_row["sha256"],
        "kind": file_row["kind"],
        "system": file_row["source"],
    }


def superseded_names(conn):
    """{file_id: name of the newer version} for documents that `dso dedup`
    found to be an older revision (a_ref of a revision / file_content row)."""
    result = {}
    rows = conn.execute(
        """SELECT d.a_ref, f.name FROM duplicates d
           JOIN files f ON f.id = CAST(d.b_ref AS INTEGER)
           WHERE d.kind IN ('revision', 'file_content')""").fetchall()
    for row in rows:
        result[int(row["a_ref"])] = row["name"]
    return result


def slide_texts(conn):
    """{slide_id: slide text} for every slide."""
    result = {}
    for row in conn.execute("SELECT slide_id, text FROM slides"):
        result[row["slide_id"]] = row["text"]
    return result


def shown_in(conn):
    """{slide_id: [{"file_name", "t_sec"}]}: in which recordings a slide was on
    screen, and from when (first matching key frame per recording)."""
    result = {}
    rows = conn.execute(
        """SELECT frames.slide_id, files.name, MIN(frames.t_sec) AS t_sec
           FROM frames JOIN files ON files.id = frames.file_id
           WHERE frames.slide_id IS NOT NULL
           GROUP BY frames.slide_id, files.id ORDER BY t_sec""").fetchall()
    for row in rows:
        if row["slide_id"] not in result:
            result[row["slide_id"]] = []
        result[row["slide_id"]].append({"file_name": row["name"], "t_sec": row["t_sec"]})
    return result


def transcript_chunks(data, file_row, texts_of_slides):
    """Groups a video's segments into chunks: a new chunk starts when the key
    frame changes or the chunk would get longer than CHUNK_MAX_SEC."""
    frame_images = {}
    for frame in data["frames"]:
        frame_images[frame["id"]] = frame["image"]
    source = corpus_source(file_row)
    sha12 = file_row["sha256"][:12]

    chunks = []
    current = None
    for segment in data["segments"]:
        starts_new = (current is None
                      or segment["frame_id"] != current["frame_id"]
                      or segment["end_sec"] - current["start_sec"] > CHUNK_MAX_SEC)
        if starts_new:
            current = {"frame_id": segment["frame_id"], "slide_id": segment["slide_id"],
                       "start_sec": segment["start_sec"], "end_sec": segment["end_sec"], "texts": [],
                       "speakers": []}
            chunks.append(current)
        current["texts"].append(segment["text"])
        if segment["speaker"] not in current["speakers"]:
            current["speakers"].append(segment["speaker"])     # who talks in this chunk, in order
        current["end_sec"] = segment["end_sec"]

    result = []
    for chunk in chunks:
        slide_id = chunk["slide_id"]
        result.append({
            "id": f"{sha12}:t{int(chunk['start_sec'])}",
            "type": "transcript",
            "text": " ".join(chunk["texts"]),
            "start_sec": chunk["start_sec"],
            "end_sec": chunk["end_sec"],
            "frame_image": frame_images.get(chunk["frame_id"]),
            "slide_id": slide_id,
            "slide_text": texts_of_slides.get(slide_id) if slide_id else None,
            "speakers": chunk["speakers"],
            "source": source,
        })
    return result


def slide_chunks(data, file_row, shown, superseded):
    """One chunk per slide WITH text (picture-only slides say nothing to an AI)."""
    source = corpus_source(file_row)
    result = []
    page = 0
    for slide in data["slides_referenced"]:       # in page order for documents
        page += 1
        if slide["text"].strip() == "":
            continue
        result.append({
            "id": slide["slide_id"],
            "type": "slide",
            "page": page,
            "text": slide["text"],
            "image": slide["image"],
            "shown_in": shown.get(slide["slide_id"], []),
            "superseded_by": superseded.get(file_row["id"]),
            "source": source,
        })
    return result


def write_corpus(path, chunks):
    """corpus.jsonl: one JSON object per line (JSON Lines), UTF-8."""
    with open(path, "w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")


def percent(part, whole):
    if whole == 0:
        return 0.0
    return 100.0 * part / whole


def readiness_numbers(conn, chunks, corpus_bytes):
    """Measured coverage for out/reports/ai_readiness.md, as a dict."""
    def one(sql):
        return conn.execute(sql).fetchone()[0]
    videos = one("SELECT COUNT(DISTINCT sha256) FROM files WHERE kind = 'video'")
    videos_with_text = one("""SELECT COUNT(DISTINCT f.sha256) FROM files f
                              WHERE f.kind = 'video' AND EXISTS (SELECT 1 FROM segments s WHERE s.file_id = f.id)""")
    documents = one("SELECT COUNT(DISTINCT sha256) FROM files WHERE kind = 'document'")
    documents_with_text = one("""SELECT COUNT(DISTINCT f.sha256) FROM files f
                                 WHERE f.kind = 'document' AND EXISTS (SELECT 1 FROM slides s WHERE s.file_id = f.id)""")
    # Video length: files.duration_sec, or - for videos processed before it
    # existed - the end of the last transcript segment.
    hours = one("""SELECT COALESCE(SUM(d), 0) FROM (
                       SELECT MAX(COALESCE(f.duration_sec,
                                  (SELECT MAX(end_sec) FROM segments s WHERE s.file_id = f.id), 0)) AS d
                       FROM files f WHERE f.kind = 'video' GROUP BY f.sha256)""") / 3600
    original_bytes = one("""SELECT COALESCE(SUM(s), 0) FROM (SELECT MIN(size_bytes) AS s FROM files
                            WHERE kind IN ('video', 'document') GROUP BY sha256)""")
    words = 0
    transcript = 0
    for chunk in chunks:
        words += len(chunk["text"].split())
        if chunk["type"] == "transcript":
            transcript += 1
    return {
        "videos": videos, "videos_with_text": videos_with_text,
        "documents": documents, "documents_with_text": documents_with_text,
        "video_hours": hours,
        "searchable_items": one("SELECT COUNT(*) FROM search_fts"),
        "segments": one("SELECT COUNT(*) FROM segments"),
        "segments_with_frame": one("SELECT COUNT(*) FROM segments WHERE frame_id IS NOT NULL"),
        "segments_with_slide": one("""SELECT COUNT(*) FROM segments s JOIN frames f ON f.id = s.frame_id
                                      WHERE f.slide_id IS NOT NULL"""),
        "chunks": len(chunks), "transcript_chunks": transcript, "slide_chunks": len(chunks) - transcript,
        "words": words, "corpus_bytes": corpus_bytes, "original_bytes": original_bytes,
    }


def human_size(n):
    """Bytes as a short readable string, e.g. 1.4 GB."""
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f} {unit}"
        n = n / 1024
    return f"{n:.1f} TB"


def write_readiness(path, n, generated_at):
    """out/reports/ai_readiness.md: is the content searchable and AI-ready?"""
    per_hour = n["corpus_bytes"] / n["video_hours"] if n["video_hours"] > 0 else 0
    avg_words = n["words"] / n["chunks"] if n["chunks"] > 0 else 0
    lines = [
        "# AI readiness (data activation)",
        "",
        f"Generated {generated_at} by `dso export`. Measured on the files in the database.",
        "",
        "| Check | Result |",
        "|---|---|",
        f"| Videos turned into text (transcript) | {n['videos_with_text']} of {n['videos']} "
        f"({percent(n['videos_with_text'], n['videos']):.0f} %), {n['video_hours']:.1f} h of video |",
        f"| Documents turned into slide text | {n['documents_with_text']} of {n['documents']} "
        f"({percent(n['documents_with_text'], n['documents']):.0f} %) |",
        f"| Searchable items (transcript segments + slides) | {n['searchable_items']} |",
        f"| Transcript linked to the key frame on screen | {n['segments_with_frame']} of {n['segments']} "
        f"({percent(n['segments_with_frame'], n['segments']):.0f} %) |",
        f"| Transcript linked to a slide | {n['segments_with_slide']} of {n['segments']} "
        f"({percent(n['segments_with_slide'], n['segments']):.0f} %) |",
        f"| AI-ready chunks in corpus.jsonl | {n['chunks']} ({n['transcript_chunks']} transcript, "
        f"{n['slide_chunks']} slides), {n['words']} words, ~{avg_words:.0f} words per chunk |",
        "| Chunks with source file, SHA-256 and position (time or page) | 100 % |",
        f"| Size | corpus {human_size(n['corpus_bytes'])} for {human_size(n['original_bytes'])} of "
        f"originals; {human_size(per_hour)} per hour of video |",
        "",
        "corpus.jsonl is JSON Lines (one object per line): the usual input for vector",
        "databases, retrieval-augmented generation (RAG) and training data.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------- 4. The command ----------

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
    texts_of_slides = slide_texts(conn)
    shown = shown_in(conn)
    superseded = superseded_names(conn)
    chunks = []             # the AI-ready corpus, filled per file
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

        if file_row["kind"] == "video":
            chunks = chunks + transcript_chunks(data, file_row, texts_of_slides)
        else:
            chunks = chunks + slide_chunks(data, file_row, shown, superseded)

        written = written + 1
        print(f"[export]   {file_row['name']} -> {sha12}.json + .md "
              f"({len(data['frames'])} frames, {len(data['segments'])} segments, "
              f"{len(data['slides_referenced'])} slides)")

    corpus_path = export_dir / "corpus.jsonl"
    write_corpus(corpus_path, chunks)
    numbers = readiness_numbers(conn, chunks, corpus_path.stat().st_size)
    conn.close()
    report = Path(args.out) / "reports" / "ai_readiness.md"
    write_readiness(report, numbers, generated_at)

    print(f"[export] {written} exports written")
    print(f"[export] AI-ready corpus: {numbers['chunks']} chunks, {numbers['words']} words -> "
          f"{corpus_path.as_posix()} ({human_size(numbers['corpus_bytes'])})")
    print(f"[export] searchable items: {numbers['searchable_items']}, transcript linked to a slide: "
          f"{percent(numbers['segments_with_slide'], numbers['segments']):.0f} % -> {report.as_posix()}")
    return 0
