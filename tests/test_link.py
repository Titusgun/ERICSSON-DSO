"""Tests for `dso link` (dso/link.py). Rows are inserted by hand, so these tests
do not depend on the video/docs modules being finished.

dHash values are written so the distances are easy to count: every hex digit is
4 bits, so "000000000000000f" vs "0000000000000000" differ in exactly 4 bits.
"""
import imagehash
import pytest

from dso.cli import main
from dso.common.db import connect
from dso.common.hashing import hamming
from dso.link import MATCH_MAX_DIST, closest_slide

SLIDE_A = "aaaaaaaaaaaa:1:00000000"   # deck A, all bits 0
SLIDE_B = "bbbbbbbbbbbb:1:11111111"   # deck B (a second PDF), all bits 1


def add_file(conn, file_id, name, kind):
    conn.execute("INSERT INTO files (id, path, name, ext, kind, size_bytes, sha256, scanned_at) "
                 "VALUES (?, ?, ?, 'x', ?, 1, ?, 'now')",
                 (file_id, "x/" + name, name, kind, str(file_id) * 64))


def add_frame(conn, frame_id, file_id, t_sec, dhash):
    conn.execute("INSERT INTO frames (id, file_id, t_sec, image_path, image_bytes, dhash) "
                 "VALUES (?, ?, ?, 'f.webp', 1, ?)", (frame_id, file_id, t_sec, dhash))


def add_segment(conn, segment_id, file_id, start_sec):
    conn.execute("INSERT INTO segments (id, file_id, start_sec, end_sec, text) VALUES (?, ?, ?, ?, 'x')",
                 (segment_id, file_id, start_sec, start_sec + 2))


@pytest.fixture()
def db_path(tmp_path):
    """Video 1 presents two decks; video 2 has no deck. Then `dso link` runs once."""
    path = tmp_path / "dso.db"
    conn = connect(path)
    add_file(conn, 1, "meeting.mp4", "video")
    add_file(conn, 2, "student.mp4", "video")
    add_file(conn, 3, "deck_a.pdf", "document")
    add_file(conn, 4, "deck_b.pdf", "document")
    conn.execute("INSERT INTO slides VALUES (?, 3, 1, 'a', 'h', '0000000000000000', 'a.webp')", (SLIDE_A,))
    conn.execute("INSERT INTO slides VALUES (?, 4, 1, 'b', 'h', 'ffffffffffffffff', 'b.webp')", (SLIDE_B,))

    add_frame(conn, 1, 1, 0.0, "000000000000000f")    # 4 bits from A  -> A
    add_frame(conn, 2, 1, 10.0, "fffffffffffffff0")   # 4 bits from B  -> B (other deck)
    add_frame(conn, 3, 1, 20.0, "00000000ffffffff")   # 32 from both   -> no match
    add_frame(conn, 4, 1, 30.0, "000000000000003f")   # exactly 6 from A -> A (limit)
    add_frame(conn, 5, 1, 40.0, "000000000000007f")   # 7 from A        -> no match
    add_frame(conn, 6, 2, 2.0, "0f0f0f0f0f0f0f0f")    # student video, 32 from both -> no match

    # A stale link from an "earlier run" that must be cleared.
    conn.execute("UPDATE frames SET slide_id = ?, match_dist = 0 WHERE id = 3", (SLIDE_A,))

    add_segment(conn, 1, 1, 5.0)     # frame 1 (t=0) is on screen
    add_segment(conn, 2, 1, 10.0)    # starts exactly when frame 2 appears -> frame 2
    add_segment(conn, 3, 1, 25.0)    # frame 3
    add_segment(conn, 4, 2, 1.0)     # before the first frame of video 2 -> NULL
    add_segment(conn, 5, 2, 3.0)     # frame 6 (same video only, not frame 1)
    conn.commit()
    conn.close()

    assert main(["--db", str(path), "link"]) == 0
    return path


def frame_links(path):
    """{frame_id: (slide_id, match_dist)} for every frame."""
    conn = connect(path)
    result = {}
    for row in conn.execute("SELECT id, slide_id, match_dist FROM frames"):
        result[row["id"]] = (row["slide_id"], row["match_dist"])
    conn.close()
    return result


def segment_links(path):
    """{segment_id: frame_id} for every segment."""
    conn = connect(path)
    result = {}
    for row in conn.execute("SELECT id, frame_id FROM segments"):
        result[row["id"]] = row["frame_id"]
    conn.close()
    return result


def test_threshold_matches_contract():
    assert MATCH_MAX_DIST == 6


def test_frames_matched_against_all_decks(db_path):
    links = frame_links(db_path)
    assert links[1] == (SLIDE_A, 4)
    assert links[2] == (SLIDE_B, 4)       # second deck in the same recording
    assert links[4] == (SLIDE_A, 6)       # distance == limit still matches


def test_unmatched_frames_are_null(db_path):
    links = frame_links(db_path)
    assert links[3] == (None, None)       # stale link was cleared
    assert links[5] == (None, None)       # distance 7 > limit
    assert links[6] == (None, None)       # video without a deck


def test_segments_point_at_frame_on_screen(db_path):
    assert segment_links(db_path) == {1: 1, 2: 2, 3: 3, 4: None, 5: 6}


def test_link_is_safe_to_run_twice(db_path):
    before_frames = frame_links(db_path)
    before_segments = segment_links(db_path)
    assert main(["--db", str(db_path), "link"]) == 0
    assert frame_links(db_path) == before_frames
    assert segment_links(db_path) == before_segments


def test_closest_slide_tie_and_empty():
    zero = imagehash.hex_to_hash("0000000000000000")
    slides = [("a:1:x", zero), ("b:1:x", zero)]
    assert closest_slide(zero, slides) == ("a:1:x", 0)    # tie -> first slide
    assert closest_slide(zero, []) == (None, None)


def test_preconverted_distance_equals_hamming():
    """link.py skips hashing.hamming() for speed; the numbers must be identical."""
    pairs = [("f0e1d2c3b4a59687", "0123456789abcdef"),
             ("0000000000000000", "ffffffffffffffff"),
             ("00ff00ff00ff00ff", "00ff00ff00ff00fe")]
    for a, b in pairs:
        fast = int(imagehash.hex_to_hash(a) - imagehash.hex_to_hash(b))
        assert fast == hamming(a, b)
