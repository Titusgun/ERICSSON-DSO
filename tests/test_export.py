"""Tests for `dso export` (dso/export.py). Rows are inserted by hand, so these
tests do not depend on the video/docs/link modules being finished."""
import json

import pytest

from dso.cli import main
from dso.common.db import connect
from dso.export import format_time

SHA_VIDEO = "3fa9c0d1e2b4" + "0" * 52
SHA_DECK = "9b1c22aa01ff" + "0" * 52
SLIDE_1 = "9b1c22aa01ff:1:aaaaaaaa"
SLIDE_2 = "9b1c22aa01ff:2:9f2c01ab"


@pytest.fixture()
def run_export(tmp_path):
    """One video (2 frames, 3 segments) showing slide 2 of a deck, the deck itself,
    and an exact copy of the deck. Runs `dso export` and returns the export folder."""
    db_path = tmp_path / "dso.db"
    out = tmp_path / "out"
    conn = connect(db_path)
    conn.execute("INSERT INTO files (id, path, name, ext, kind, size_bytes, sha256, source, scanned_at) "
                 "VALUES (1, 'play/talk.mp4', 'talk.mp4', 'mp4', 'video', 1000, ?, 'play', 'now')", (SHA_VIDEO,))
    conn.execute("INSERT INTO files (id, path, name, ext, kind, size_bytes, sha256, source, scanned_at) "
                 "VALUES (2, 'sp/deck.pdf', 'deck.pdf', 'pdf', 'document', 500, ?, 'sharepoint', 'now')", (SHA_DECK,))
    conn.execute("INSERT INTO files (id, path, name, ext, kind, size_bytes, sha256, source, scanned_at) "
                 "VALUES (3, 'sp/deck (1).pdf', 'deck (1).pdf', 'pdf', 'document', 500, ?, 'sharepoint', 'now')", (SHA_DECK,))
    conn.execute("INSERT INTO files (id, path, name, ext, kind, size_bytes, sha256, scanned_at) "
                 "VALUES (4, 'notes.png', 'notes.png', 'png', 'other', 10, ?, 'now')", ("c" * 64,))
    # Slides inserted in reverse page order, to check the export sorts by page.
    conn.execute("INSERT INTO slides VALUES (?, 2, 2, 'Latency budget', 'h2', '00ff00ff00ff00ff', "
                 "'slides/9b1c22aa01ff/p002.webp')", (SLIDE_2,))
    conn.execute("INSERT INTO slides VALUES (?, 2, 1, 'Agenda', 'h1', 'ff00ff00ff00ff00', "
                 "'slides/9b1c22aa01ff/p001.webp')", (SLIDE_1,))
    conn.execute("INSERT INTO frames (id, file_id, t_sec, image_path, image_bytes, dhash) "
                 "VALUES (11, 1, 0.0, 'frames/3fa9c0d1e2b4/f0001.webp', 100, '0000000000000000')")
    conn.execute("INSERT INTO frames (id, file_id, t_sec, image_path, image_bytes, dhash, slide_id, match_dist) "
                 "VALUES (12, 1, 95.0, 'frames/3fa9c0d1e2b4/f0012.webp', 100, '00ff00ff00ff00fe', ?, 1)", (SLIDE_2,))
    conn.execute("INSERT INTO segments (id, file_id, start_sec, end_sec, text, frame_id) "
                 "VALUES (40, 1, 96.0, 101.5, 'As you see here, the latency drops', 12)")
    conn.execute("INSERT INTO segments (id, file_id, start_sec, end_sec, text, frame_id) "
                 "VALUES (39, 1, 2.0, 4.0, 'Welcome everyone', 11)")
    conn.execute("INSERT INTO segments (id, file_id, start_sec, end_sec, text) "
                 "VALUES (41, 1, 120.0, 130.0, 'Not linked yet')")
    conn.commit()
    conn.close()
    assert main(["--db", str(db_path), "--out", str(out), "export"]) == 0
    return out / "export"


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_format_time():
    assert format_time(0) == "00:00"
    assert format_time(3725.9) == "1:02:05"


def test_one_export_per_content(run_export):
    names = sorted(p.name for p in run_export.iterdir())
    # video + deck; the exact copy "deck (1).pdf" and the PNG ('other') are not exported
    assert names == ["3fa9c0d1e2b4.json", "3fa9c0d1e2b4.md", "9b1c22aa01ff.json", "9b1c22aa01ff.md",
                     "corpus.jsonl"]


def test_video_json_matches_contract(run_export):
    data = read_json(run_export / "3fa9c0d1e2b4.json")
    assert set(data.keys()) == {"schema_version", "source", "generated_at", "duration_sec",
                                "frames", "segments", "slides_referenced"}
    assert data["schema_version"] == 1
    assert data["generated_at"].endswith("Z")
    assert data["source"] == {
        "file_name": "talk.mp4", "path": "play/talk.mp4", "sha256": SHA_VIDEO, "size_bytes": 1000,
        "kind": "video", "system": "play", "doc_number": None, "revision": None,
        "author": None, "date": None,
    }
    assert data["duration_sec"] == 130.0                    # end of the last segment
    assert data["frames"][1] == {"id": 12, "t_sec": 95.0, "image": "frames/3fa9c0d1e2b4/f0012.webp",
                                 "dhash": "00ff00ff00ff00fe", "slide_id": SLIDE_2, "match_dist": 1}
    starts = [s["start_sec"] for s in data["segments"]]
    assert starts == [2.0, 96.0, 120.0]                     # sorted by time
    assert data["segments"][1] == {"id": 40, "start_sec": 96.0, "end_sec": 101.5, "speaker": "Speaker ?",
                                   "text": "As you see here, the latency drops", "frame_id": 12,
                                   "slide_id": SLIDE_2}
    assert data["segments"][2]["slide_id"] is None          # segment without a frame
    assert data["slides_referenced"] == [
        {"slide_id": SLIDE_2, "text": "Latency budget", "image": "slides/9b1c22aa01ff/p002.webp"}]


def test_document_json(run_export):
    data = read_json(run_export / "9b1c22aa01ff.json")
    assert data["source"]["file_name"] == "deck.pdf"        # canonical copy, not "deck (1).pdf"
    assert data["frames"] == [] and data["segments"] == []
    assert data["duration_sec"] is None
    ids = [s["slide_id"] for s in data["slides_referenced"]]
    assert ids == [SLIDE_1, SLIDE_2]                        # page order


def test_video_markdown(run_export):
    md = (run_export / "3fa9c0d1e2b4.md").read_text(encoding="utf-8")
    assert md.startswith("# talk.mp4")
    assert "## 01:35 - slide `" + SLIDE_2 + "`" in md
    assert "![frame at 01:35](../frames/3fa9c0d1e2b4/f0012.webp)" in md
    assert "- **[01:36] Speaker ?:** As you see here, the latency drops" in md
    # The segment appears under its own frame, not under the first frame.
    assert md.index("## 01:35") < md.index("the latency drops")
    assert "## Transcript without a key frame" in md and "Not linked yet" in md
    assert "![slide](../slides/9b1c22aa01ff/p002.webp)" in md


def test_document_markdown(run_export):
    md = (run_export / "9b1c22aa01ff.md").read_text(encoding="utf-8")
    assert md.index("## Slide 1") < md.index("Agenda") < md.index("## Slide 2") < md.index("Latency budget")


def test_export_is_safe_to_run_twice(run_export):
    db_path = run_export.parent.parent / "dso.db"
    out = run_export.parent
    assert main(["--db", str(db_path), "--out", str(out), "export"]) == 0
    assert len(list(run_export.iterdir())) == 5      # 2 x (json + md) + corpus.jsonl


# ---------- AI-ready corpus (corpus.jsonl) + readiness report ----------

def read_corpus(export_dir):
    chunks = []
    with open(export_dir / "corpus.jsonl", encoding="utf-8") as f:
        for line in f:
            chunks.append(json.loads(line))
    return chunks


def test_corpus_transcript_chunks_follow_the_key_frame(run_export):
    chunks = read_corpus(run_export)
    transcript = [c for c in chunks if c["type"] == "transcript"]
    # 3 segments, each with a different frame (11, 12, none) -> 3 chunks, in time order.
    assert [c["start_sec"] for c in transcript] == [2.0, 96.0, 120.0]
    latency = transcript[1]
    assert latency["text"] == "As you see here, the latency drops"
    assert latency["frame_image"] == "frames/3fa9c0d1e2b4/f0012.webp"
    assert latency["slide_id"] == SLIDE_2 and latency["slide_text"] == "Latency budget"
    assert latency["source"]["file_name"] == "talk.mp4" and latency["source"]["sha256"] == SHA_VIDEO
    assert latency["id"] == "3fa9c0d1e2b4:t96"
    assert latency["speakers"] == ["Speaker ?"]           # filled by `dso speakers`
    assert transcript[2]["slide_id"] is None and transcript[2]["slide_text"] is None


def test_corpus_slide_chunks_know_where_they_were_shown(run_export):
    slides = [c for c in read_corpus(run_export) if c["type"] == "slide"]
    assert [c["id"] for c in slides] == [SLIDE_1, SLIDE_2]          # the exact copy adds nothing
    assert slides[1]["page"] == 2 and slides[1]["text"] == "Latency budget"
    assert slides[1]["shown_in"] == [{"file_name": "talk.mp4", "t_sec": 95.0}]
    assert slides[0]["shown_in"] == [] and slides[0]["superseded_by"] is None


def test_long_speech_on_one_frame_is_split(tmp_path):
    """One frame on screen for 3 minutes -> chunks of at most CHUNK_MAX_SEC."""
    db_path = tmp_path / "dso.db"
    out = tmp_path / "out"
    conn = connect(db_path)
    conn.execute("INSERT INTO files (id, path, name, ext, kind, size_bytes, sha256, scanned_at) "
                 "VALUES (1, 'talk.mp4', 'talk.mp4', 'mp4', 'video', 1000, ?, 'now')", (SHA_VIDEO,))
    conn.execute("INSERT INTO frames (id, file_id, t_sec, image_path, image_bytes, dhash) "
                 "VALUES (1, 1, 0, 'f.webp', 1, '0000000000000000')")
    for k in range(18):                                    # 18 sentences of 10 s = 180 s
        conn.execute("INSERT INTO segments (file_id, start_sec, end_sec, text, frame_id) VALUES (1, ?, ?, ?, 1)",
                     (k * 10.0, k * 10.0 + 10, f"sentence {k}"))
    conn.commit()
    conn.close()
    assert main(["--db", str(db_path), "--out", str(out), "export"]) == 0
    chunks = read_corpus(out / "export")
    assert len(chunks) == 3
    for chunk in chunks:
        assert chunk["end_sec"] - chunk["start_sec"] <= 60


def test_superseded_revision_is_marked(tmp_path):
    db_path = tmp_path / "dso.db"
    out = tmp_path / "out"
    conn = connect(db_path)
    for file_id, name, sha in [(1, "deck final.pptx", "a" * 64), (2, "deck final2.pptx", "b" * 64)]:
        conn.execute("INSERT INTO files (id, path, name, ext, kind, size_bytes, sha256, scanned_at) "
                     "VALUES (?, ?, ?, 'pptx', 'document', 100, ?, 'now')", (file_id, name, name, sha))
        conn.execute("INSERT INTO slides VALUES (?, ?, 1, 'Pricing tiers', 'h', '0000000000000000', 'p.webp')",
                     (f"{sha[:12]}:1:x", file_id))
    conn.execute("INSERT INTO duplicates (kind, a_ref, b_ref, note) VALUES ('revision', '1', '2', 'x')")
    conn.commit()
    conn.close()
    assert main(["--db", str(db_path), "--out", str(out), "export"]) == 0
    by_file = {}
    for chunk in read_corpus(out / "export"):
        by_file[chunk["source"]["file_name"]] = chunk
    assert by_file["deck final.pptx"]["superseded_by"] == "deck final2.pptx"
    assert by_file["deck final2.pptx"]["superseded_by"] is None


def test_readiness_report(run_export):
    report = (run_export.parent / "reports" / "ai_readiness.md").read_text(encoding="utf-8")
    assert "# AI readiness" in report
    assert "| Videos turned into text (transcript) | 1 of 1 (100 %)" in report
    assert "| Transcript linked to the key frame on screen | 2 of 3 (67 %) |" in report
    assert "| Transcript linked to a slide | 1 of 3 (33 %) |" in report
    assert "AI-ready chunks in corpus.jsonl | 5 (3 transcript, 2 slides)" in report


def test_old_database_gets_new_columns(tmp_path):
    """A DB made before contract v1.1 (no files.duration_sec) works after connect()."""
    import sqlite3
    db_path = tmp_path / "old.db"
    old = sqlite3.connect(db_path)
    old.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, name TEXT NOT NULL, "
                "ext TEXT NOT NULL, kind TEXT NOT NULL, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, "
                "source TEXT NOT NULL DEFAULT 'local', owner TEXT, doc_number TEXT, revision TEXT, "
                "author TEXT, doc_date TEXT, scanned_at TEXT NOT NULL)")
    old.execute("INSERT INTO files (path, name, ext, kind, size_bytes, sha256, scanned_at) "
                "VALUES ('v.mp4', 'v.mp4', 'mp4', 'video', 1, 'x', 'now')")
    old.commit()
    old.close()
    conn = connect(db_path)
    columns = [row[1] for row in conn.execute("PRAGMA table_info(files)")]
    assert "duration_sec" in columns
    assert conn.execute("SELECT duration_sec FROM files").fetchone()[0] is None   # old row kept
    conn.close()
    assert main(["--db", str(db_path), "--out", str(tmp_path / "out"), "export"]) == 0
