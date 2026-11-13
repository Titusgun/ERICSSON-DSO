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
    assert names == ["3fa9c0d1e2b4.json", "3fa9c0d1e2b4.md", "9b1c22aa01ff.json", "9b1c22aa01ff.md"]


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
    assert len(list(run_export.iterdir())) == 4
