"""Tests for `dso link` (dso/link.py). Rows are inserted by hand, so these tests
do not depend on the video/docs modules being finished.

dHash values are written so the distances are easy to count: every hex digit is
4 bits, so "000000000000000f" vs "0000000000000000" differ in exactly 4 bits.
"""
import imagehash
import pytest
from PIL import Image, ImageDraw

from dso.cli import main
from dso.common.db import connect
from dso.common.hashing import hamming
from dso import link
from dso.link import MATCH_MAX_DIST, closest_slide, picture_hashes

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
    add_frame(conn, 4, 1, 30.0, "0000000000001fff")   # exactly 13 from A -> A (limit)
    add_frame(conn, 5, 1, 40.0, "0000000000003fff")   # 14 from A       -> no match
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
    assert MATCH_MAX_DIST == 13      # re-measured on the real Teams frames (#10)


def test_frames_matched_against_all_decks(db_path):
    links = frame_links(db_path)
    assert links[1] == (SLIDE_A, 4)
    assert links[2] == (SLIDE_B, 4)       # second deck in the same recording
    assert links[4] == (SLIDE_A, 13)      # distance == limit still matches


def test_unmatched_frames_are_null(db_path):
    links = frame_links(db_path)
    assert links[3] == (None, None)       # stale link was cleared
    assert links[5] == (None, None)       # distance 14 > limit
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
    zero = {"coarse": 0, "fine": None}
    slides = [{"slide_id": "a:1:x", "coarse": 0, "fine": None},
              {"slide_id": "b:1:x", "coarse": 0, "fine": None}]
    assert closest_slide([zero], slides) == ("a:1:x", 0)    # tie -> first slide
    assert closest_slide([zero], []) == (None, None)
    assert closest_slide([], slides) == (None, None)        # nothing to compare (black frame)


def test_bit_count_distance_equals_hamming():
    """link.py compares hashes as integers; the numbers must equal hashing.hamming()."""
    pairs = [("f0e1d2c3b4a59687", "0123456789abcdef"),
             ("0000000000000000", "ffffffffffffffff"),
             ("00ff00ff00ff00ff", "00ff00ff00ff00fe")]
    for a, b in pairs:
        assert link.bits_different(int(a, 16), int(b, 16)) == hamming(a, b)
        assert int(imagehash.hex_to_hash(a) - imagehash.hex_to_hash(b)) == hamming(a, b)


# ---------- real pictures: Teams layout, empty slides (issue #10) ----------

def draw_slide(title, boxes):
    """A synthetic 16:9 slide: white, a title bar and some coloured boxes."""
    image = Image.new("RGB", (960, 540), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 30, 920, 110), fill=(20, 40, 120))
    draw.text((60, 55), title, fill="white")
    for x0, y0, x1, y1, colour in boxes:
        draw.rectangle((x0, y0, x1, y1), fill=colour)
    return image


def teams_frame(slide):
    """A 1920x1080 "Teams" frame: the slide in the screen-share area (left 85 %,
    3-93 % of the height), a dark window around it and a participant strip."""
    frame = Image.new("RGB", (1920, 1080), (30, 30, 35))
    frame.paste(slide.resize((1632, 972)), (0, 32))
    draw = ImageDraw.Draw(frame)
    for k in range(4):                                     # participant tiles
        draw.rectangle((1660, 60 + k * 250, 1900, 280 + k * 250), fill=(90 + 40 * k, 70, 160 - 30 * k))
        draw.ellipse((1740, 110 + k * 250, 1820, 190 + k * 250), fill=(230, 190, 150))
    return frame


def test_teams_frame_matches_its_slide_through_the_share_area(tmp_path):
    out = tmp_path / "out"
    (out / "slides").mkdir(parents=True)
    (out / "frames").mkdir(parents=True)
    agenda = draw_slide("Agenda", [(80, 160, 400, 480, (200, 30, 30)), (520, 160, 880, 300, (30, 160, 60))])
    pricing = draw_slide("Pricing", [(80, 160, 880, 250, (240, 170, 0)), (80, 300, 880, 480, (0, 120, 200))])
    logo_only = Image.new("RGB", (960, 540), "white")
    ImageDraw.Draw(logo_only).rectangle((460, 250, 500, 290), fill="black")  # nearly empty page
    agenda.save(out / "slides" / "agenda.webp")
    pricing.save(out / "slides" / "pricing.webp")
    logo_only.save(out / "slides" / "logo.webp")
    teams_frame(pricing).save(out / "frames" / "f0001.webp")
    Image.new("RGB", (1920, 1080), "black").save(out / "frames" / "f0002.webp")    # black screen

    whole = picture_hashes(teams_frame(pricing))["coarse"]
    pricing_hash = picture_hashes(pricing)["coarse"]
    assert link.bits_different(whole, pricing_hash) > MATCH_MAX_DIST   # whole frame: no match

    db_path = tmp_path / "dso.db"
    conn = connect(db_path)
    add_file(conn, 1, "meeting.mp4", "video")
    add_file(conn, 2, "deck.pdf", "document")
    for slide_id, name, image in [("d:1:a", "agenda", agenda), ("d:2:p", "pricing", pricing),
                                  ("d:3:l", "logo", logo_only)]:
        conn.execute("INSERT INTO slides VALUES (?, 2, 1, 't', 'h', ?, ?)",
                     (slide_id, str(imagehash.dhash(image)), f"slides/{name}.webp"))
    conn.execute("INSERT INTO frames (id, file_id, t_sec, image_path, image_bytes, dhash) "
                 "VALUES (1, 1, 0, 'frames/f0001.webp', 1, '0000000000000000')")
    conn.execute("INSERT INTO frames (id, file_id, t_sec, image_path, image_bytes, dhash) "
                 "VALUES (2, 1, 9, 'frames/f0002.webp', 1, 'ffffffffffffffff')")
    conn.commit()
    conn.close()
    assert main(["--db", str(db_path), "--out", str(out), "link"]) == 0
    links = frame_links(db_path)
    assert links[1][0] == "d:2:p" and links[1][1] <= MATCH_MAX_DIST    # found via the share area
    assert links[2] == (None, None)       # black frame: nearly empty, never matched
