"""Tests for `dso index` + search() (dso/search.py). Rows are inserted by hand, so
these tests do not depend on the video/docs modules being finished."""
import pytest

from dso.cli import main
from dso.common.db import connect
from dso.search import MARK_END, MARK_START, build_match_query, estimate_word_time, format_time, search

SHA_VIDEO = "3fa9c0d1e2b4" + "0" * 52
SHA_DECK = "9b1c22aa01ff" + "0" * 52
SLIDE = "9b1c22aa01ff:2:9f2c01ab"


@pytest.fixture()
def db_path(tmp_path):
    """A small DB with one video (2 segments, 1 frame) and one deck (2 slides), indexed."""
    path = tmp_path / "dso.db"
    conn = connect(path)
    conn.execute("INSERT INTO files (id, path, name, ext, kind, size_bytes, sha256, scanned_at) "
                 "VALUES (1, 'v/talk.mp4', 'talk.mp4', 'mp4', 'video', 1000, ?, 'now')", (SHA_VIDEO,))
    conn.execute("INSERT INTO files (id, path, name, ext, kind, size_bytes, sha256, scanned_at) "
                 "VALUES (2, 'd/deck_a.pdf', 'deck_a.pdf', 'pdf', 'document', 500, ?, 'now')", (SHA_DECK,))
    conn.execute("INSERT INTO slides VALUES (?, 2, 2, 'Latency budget: 4 ms', 'h1', '00ff00ff00ff00ff', "
                 "'slides/9b1c22aa01ff/p002.webp')", (SLIDE,))
    conn.execute("INSERT INTO slides VALUES ('9b1c22aa01ff:3:11111111', 2, 3, 'Roadmap', 'h2', "
                 "'ff00ff00ff00ff00', 'slides/9b1c22aa01ff/p003.webp')")
    conn.execute("INSERT INTO frames (id, file_id, t_sec, image_path, image_bytes, dhash, slide_id, match_dist) "
                 "VALUES (7, 1, 95.0, 'frames/3fa9c0d1e2b4/f0012.webp', 500, '00ff00ff00ff00ff', ?, 1)", (SLIDE,))
    conn.execute("INSERT INTO segments (id, file_id, start_sec, end_sec, text, frame_id) "
                 "VALUES (40, 1, 96.0, 101.5, 'As you see here, the latency drops', 7)")
    conn.execute("INSERT INTO segments (id, file_id, start_sec, end_sec, text) "
                 "VALUES (41, 1, 3.0, 5.0, 'Welcome everyone')")
    conn.commit()
    conn.close()
    assert main(["--db", str(path), "index"]) == 0
    return path


def test_build_match_query():
    assert build_match_query("latency drops") == '"latency" "drops"'
    assert build_match_query('say "hi"') == '"say" """hi"""'
    assert build_match_query("   ") == ""


def test_format_time():
    assert format_time(96.0) == "01:36"
    assert format_time(3725.4) == "1:02:05"


def test_index_is_safe_to_run_twice(db_path):
    assert main(["--db", str(db_path), "index"]) == 0
    conn = connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM search_fts").fetchone()[0] == 4
    conn.close()


def test_search_finds_segment_and_slide(db_path):
    conn = connect(db_path)
    results = search(conn, "latency")
    conn.close()
    assert len(results) == 2
    by_kind = {}
    for r in results:
        by_kind[r["kind"]] = r
    segment = by_kind["segment"]
    assert segment == {
        "kind": "segment", "file": "talk.mp4", "sha12": "3fa9c0d1e2b4", "t_sec": 99.4,   # word at 62% of 96.0-101.5
        "frame_image": "frames/3fa9c0d1e2b4/f0012.webp", "slide_id": SLIDE,
        "slide_image": "slides/9b1c22aa01ff/p002.webp",
        "snippet": "As you see here, the [latency] drops",
    }
    slide = by_kind["slide"]
    assert slide["file"] == "deck_a.pdf" and slide["slide_id"] == SLIDE
    assert slide["t_sec"] is None and slide["frame_image"] is None
    assert slide["slide_image"] == "slides/9b1c22aa01ff/p002.webp"     # the UI shows + enlarges it
    assert "[Latency]" in slide["snippet"]


def test_search_segment_without_frame(db_path):
    conn = connect(db_path)
    results = search(conn, "welcome")
    conn.close()
    assert len(results) == 1
    assert results[0]["t_sec"] == 3.0
    assert results[0]["frame_image"] is None and results[0]["slide_id"] is None


def test_search_needs_all_words_and_survives_odd_input(db_path):
    conn = connect(db_path)
    assert len(search(conn, "latency drops")) == 1
    assert search(conn, "latency nonexistentword") == []
    assert search(conn, "") == []
    # FTS5 operators and quotes must not cause a syntax error.
    for odd in ['C++', 'what\'s "new"', "NOT", "a:b", "-x", "(", "*"]:
        assert isinstance(search(conn, odd), list)
    conn.close()


def test_search_cli_prints_results(db_path, capsys):
    assert main(["--db", str(db_path), "search", "latency"]) == 0
    out = capsys.readouterr().out
    assert "talk.mp4 @ 01:39" in out
    assert "deck_a.pdf slide" in out


# ---------- estimated word time inside a segment ----------

def mark(text, word):
    """Puts the highlight() markers around the first `word` in `text`."""
    return text.replace(word, MARK_START + word + MARK_END, 1)


def test_estimate_word_time():
    text = "0123456789"                                           # 10 characters
    assert estimate_word_time(100.0, 110.0, mark(text, "0")) == 100.0    # first word -> start
    assert estimate_word_time(100.0, 110.0, mark(text, "5")) == 105.0    # half way
    assert estimate_word_time(100.0, 110.0, mark(text, "9")) == 109.0    # last word, still < end
    assert estimate_word_time(100.0, 110.0, text) == 100.0               # no marker -> start
    assert estimate_word_time(100.0, 100.0, mark(text, "5")) == 100.0    # zero-length segment
    assert estimate_word_time(100.0, 110.0, "") == 100.0


def test_word_time_uses_first_match_and_swedish_letters(db_path):
    conn = connect(db_path)
    # 20 characters, 20 seconds: the character position = seconds after start.
    conn.execute("INSERT INTO segments (id, file_id, start_sec, end_sec, text) "
                 "VALUES (50, 1, 200.0, 220.0, 'vi pratar över kaffe')")
    conn.commit()
    conn.close()
    assert main(["--db", str(db_path), "index"]) == 0
    conn = connect(db_path)
    # FTS5 matches "over" to "över" (diacritics are ignored); "över" starts at char 10.
    assert search(conn, "over")[0]["t_sec"] == 210.0
    assert search(conn, "över")[0]["t_sec"] == 210.0
    # Two words: the time of the FIRST matched word in the sentence ("pratar", char 3).
    assert search(conn, "kaffe pratar")[0]["t_sec"] == 203.0
    conn.close()
