"""`dso link` (owner: C)

Connects the three kinds of data we have, so a search hit in the transcript can
show the slide that was on screen:

    segment (what was said)  ->  frame (what was on screen)  ->  slide (which deck page)

Step 1 - frame -> slide (re-measured on the real Teams recording, issue #10):
    A Teams recording shows the shared slide in only part of the picture (window
    bar, participant strip on the right), so the WHOLE frame never looked like
    the slide: 0 of 393 real frames matched. Now, for every key frame:
    - two views are compared: the full frame and the Teams screen-share area
      (CROPS); the view that fits a slide best is used
    - nearly empty pictures (logo / title pages, black frames; "detail" below
      MIN_DETAIL) are skipped - they looked close to everything
    - the best slide is chosen with a finer 256-bit dHash (16x16), which tells
      apart slides with the same layout; it is a match if the normal 64-bit
      distance to that slide is <= MATCH_MAX_DIST
    Checked by eye on the real frames: every match up to distance 13 was the
    right slide; the first wrong one was at 14.
    If no match: frames.slide_id / match_dist stay NULL. That is normal: a film
    played in the meeting, a camera view, or a video without a deck.
    Frames are compared with slides from ALL documents, because one recording can
    present several decks (issue #8).
    The images are read from out/frames/ and out/slides/; if an image file is
    missing, the 64-bit dHash stored in the DB is used instead (full frame only).

Step 2 - segment -> frame:
    segments.frame_id = the last frame of the same video with t_sec <= start_sec,
    i.e. the frame that was on screen when the sentence started. So "as you see
    here" points at the right slide.

Reads: frames, slides, segments. Writes: frames.slide_id, frames.match_dist,
segments.frame_id. Safe to run twice: step 1 clears its old values first and
step 2 overwrites every segment.
"""
from pathlib import Path

import imagehash
from PIL import Image, ImageStat

from dso.common.db import connect

# DATA_CONTRACT 6: a frame shows a slide if the 64-bit distance to the best slide
# is <= this. Fixtures needed 6 (full-screen slides); on the real Teams frames
# (share area, see CROPS) true matches went up to 13, the first wrong one was 14.
MATCH_MAX_DIST = 13

# The finer hash: 16 x 16 = 256 bits. It picks WHICH slide; the 64-bit distance
# above decides WHETHER it is a match.
FINE_HASH_SIZE = 16

# Pictures with less detail than this (standard deviation of the brightness,
# 0-255) are skipped: on the real data the empty logo slide (12) matched black
# frames and one-word frames. Every real slide with content had 29 or more.
MIN_DETAIL = 20

# Parts of a frame to compare, as fractions (left, top, right, bottom).
# "Teams screen share" was measured on the real 1920x1080 Teams recording: the
# shared screen fills the left 85 %, between 3 % and 93 % of the height; the
# participant strip is on the right. Add a line here for other layouts.
CROPS = [
    ("full frame", (0.0, 0.0, 1.0, 1.0)),
    ("Teams screen share", (0.0, 0.03, 0.85, 0.93)),
]

# Print a progress line every N frames, so a long video shows it is alive.
PROGRESS_EVERY = 100


def image_detail(image):
    """How much is going on in a picture: the spread of brightness (0 = one
    flat colour). Measured on a small grey copy, so it is fast."""
    small = image.convert("L").resize((64, 36))
    return ImageStat.Stat(small).stddev[0]


def picture_hashes(image):
    """{"coarse": 64-bit dHash, "fine": 256-bit dHash} as integers.
    "coarse" is the same hash as hashing.dhash_image() (8 x 8)."""
    coarse = int(str(imagehash.dhash(image, hash_size=8)), 16)
    fine = int(str(imagehash.dhash(image, hash_size=FINE_HASH_SIZE)), 16)
    return {"coarse": coarse, "fine": fine}


def bits_different(a, b):
    """Hamming distance of two hashes stored as integers: the number of 1 bits
    in a XOR b (the bits where they differ)."""
    return (a ^ b).bit_count()


def open_image(out_dir, relative_path):
    """The picture at out/<relative_path>, or None if it cannot be read."""
    try:
        with Image.open(out_dir / relative_path) as image:
            return image.convert("RGB")
    except (OSError, ValueError):
        return None


def load_slides(conn, out_dir):
    """Every slide of every document with its hashes, as a list of dicts
    {"slide_id", "coarse", "fine"}. Nearly empty slides are left out.

    ORDER BY slide_id makes ties always resolve to the same slide, so two runs
    give the same result.
    """
    slides = []
    skipped = 0
    rows = conn.execute("SELECT slide_id, dhash, image_path FROM slides ORDER BY slide_id").fetchall()
    for row in rows:
        image = open_image(out_dir, row["image_path"])
        if image is None:
            # No image on this machine: use the 64-bit hash from the DB.
            slides.append({"slide_id": row["slide_id"], "coarse": int(row["dhash"], 16), "fine": None})
            continue
        if image_detail(image) < MIN_DETAIL:
            skipped += 1
            continue
        hashes = picture_hashes(image)
        slides.append({"slide_id": row["slide_id"], "coarse": hashes["coarse"], "fine": hashes["fine"]})
    if skipped > 0:
        print(f"[link] {skipped} nearly empty slides (logo / title pages) are not used for matching")
    return slides


def frame_views(out_dir, frame):
    """The parts of a key frame to compare (CROPS), each as {"coarse", "fine"}.
    Nearly empty views (e.g. a black screen) are left out."""
    image = open_image(out_dir, frame["image_path"])
    if image is None:
        # No image on this machine: the stored 64-bit hash of the full frame.
        return [{"coarse": int(frame["dhash"], 16), "fine": None}]
    width, height = image.size
    views = []
    for name, (left, top, right, bottom) in CROPS:
        part = image.crop((int(left * width), int(top * height), int(right * width), int(bottom * height)))
        if image_detail(part) >= MIN_DETAIL:
            views.append(picture_hashes(part))
    return views


def closest_slide(views, slides):
    """Returns (slide_id, 64-bit distance) of the slide most like any of the
    frame's views, or (None, None) if there is nothing to compare.

    "Most like" uses the fine 256-bit hash when both sides have it; otherwise
    the 64-bit distance x 4 (so both are on the same 0-256 scale).
    On a tie the first slide (lowest slide_id) wins.
    """
    best_id = None
    best_score = None
    best_coarse = None
    for view in views:
        for slide in slides:
            coarse = bits_different(view["coarse"], slide["coarse"])
            if view["fine"] is not None and slide["fine"] is not None:
                score = bits_different(view["fine"], slide["fine"])
            else:
                score = coarse * 4
            if best_score is None or score < best_score:
                best_id = slide["slide_id"]
                best_score = score
                best_coarse = coarse
    return best_id, best_coarse


def link_frames_to_slides(conn, out_dir):
    """Step 1: sets frames.slide_id and frames.match_dist. Returns (matched, total)."""
    # Clear old links first, so a frame that no longer matches (e.g. after a
    # threshold change or a re-run of `dso docs`) does not keep a stale slide.
    conn.execute("UPDATE frames SET slide_id = NULL, match_dist = NULL")

    slides = load_slides(conn, out_dir)
    frames = conn.execute(
        "SELECT id, dhash, image_path FROM frames ORDER BY file_id, t_sec").fetchall()
    total = len(frames)
    print(f"[link] matching {total} frames against {len(slides)} slides "
          f"(views: {', '.join(name for name, box in CROPS)}; match if distance <= {MATCH_MAX_DIST})")

    matched = 0
    done = 0
    for frame in frames:
        slide_id, dist = closest_slide(frame_views(out_dir, frame), slides)
        if slide_id is not None and dist <= MATCH_MAX_DIST:
            conn.execute("UPDATE frames SET slide_id = ?, match_dist = ? WHERE id = ?",
                         (slide_id, dist, frame["id"]))
            matched = matched + 1
        done = done + 1
        if done % PROGRESS_EVERY == 0:
            print(f"[link]   {done}/{total} frames done")
    return matched, total


def link_segments_to_frames(conn):
    """Step 2: sets segments.frame_id. Returns (linked, total).

    One SQL statement does it for every segment. For each segment row the
    sub-query looks in the same video (same file_id) for frames that appeared
    at or before the segment start, and takes the latest one (ORDER BY t_sec
    DESC LIMIT 1). If the segment starts before the first key frame, the
    sub-query finds nothing and frame_id becomes NULL.
    The index idx_frames_file(file_id, t_sec) from schema.sql makes this fast.
    """
    conn.execute(
        """UPDATE segments SET frame_id = (
               SELECT frames.id FROM frames
               WHERE frames.file_id = segments.file_id
                 AND frames.t_sec <= segments.start_sec
               ORDER BY frames.t_sec DESC
               LIMIT 1
           )""")
    linked = conn.execute(
        "SELECT COUNT(*) FROM segments WHERE frame_id IS NOT NULL").fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0]
    return linked, total


def run(args):
    conn = connect(args.db)

    matched, frame_total = link_frames_to_slides(conn, Path(args.out))
    print(f"[link] {matched}/{frame_total} frames show a slide "
          f"({frame_total - matched} without a match - normal for camera shots or videos without a deck)")

    linked, segment_total = link_segments_to_frames(conn)
    print(f"[link] {linked}/{segment_total} transcript segments linked to a frame")

    # Nothing is saved until commit(); if anything above crashed, the DB keeps
    # the old links instead of half-written new ones.
    conn.commit()
    conn.close()
    return 0
