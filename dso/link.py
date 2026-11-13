"""`dso link` (owner: C)

Connects the three kinds of data we have, so a search hit in the transcript can
show the slide that was on screen:

    segment (what was said)  ->  frame (what was on screen)  ->  slide (which deck page)

Step 1 - frame -> slide:
    For every key frame, find the slide with the smallest Hamming distance between
    the two dHashes (= how many of the 64 "picture fingerprint" bits differ).
    If that distance <= MATCH_MAX_DIST, store it in frames.slide_id / frames.match_dist,
    otherwise both stay NULL. NULL is normal: a camera shot, a demo, or a video
    that has no deck at all.
    Frames are compared with slides from ALL documents, because one recording can
    present several decks (issue #8).

Step 2 - segment -> frame:
    segments.frame_id = the last frame of the same video with t_sec <= start_sec,
    i.e. the frame that was on screen when the sentence started. So "as you see
    here" points at the right slide.

Reads: frames, slides, segments. Writes: frames.slide_id, frames.match_dist,
segments.frame_id. Safe to run twice: step 1 clears its old values first and
step 2 overwrites every segment.
"""
import imagehash

from dso.common.db import connect

# DATA_CONTRACT 6: a frame shows a slide if the closest slide has distance <= 6.
# Measured on fixtures (own slide 1-4, other slides 8-20). Will be re-tuned on the
# real Teams frames in issue #10 - change it here and in DATA_CONTRACT 6.
MATCH_MAX_DIST = 6

# Print a progress line every N frames, so a long video shows it is alive.
PROGRESS_EVERY = 500


def load_slides(conn):
    """Returns every slide of every document as a list of (slide_id, hash) pairs.

    The hash is converted from hex text to an imagehash object ONCE here.
    hashing.hamming() converts both hashes on every call, which is about 8x
    slower; with thousands of frames x hundreds of slides that adds up.
    `hash_a - hash_b` is exactly what hashing.hamming() computes (tested).
    """
    slides = []
    # ORDER BY slide_id makes ties (two slides with the same distance) always
    # resolve to the same slide, so two runs give the same result.
    rows = conn.execute("SELECT slide_id, dhash FROM slides ORDER BY slide_id").fetchall()
    for row in rows:
        slides.append((row["slide_id"], imagehash.hex_to_hash(row["dhash"])))
    return slides


def closest_slide(frame_hash, slides):
    """Returns (slide_id, distance) of the slide most similar to the frame.

    Returns (None, None) if there are no slides at all.
    """
    best_id = None
    best_dist = None
    for slide_id, slide_hash in slides:
        dist = int(frame_hash - slide_hash)   # number of differing bits, 0..64
        # Strictly smaller: on a tie the first slide (lowest slide_id) wins.
        if best_dist is None or dist < best_dist:
            best_id = slide_id
            best_dist = dist
            if dist == 0:
                break                         # cannot get better than identical
    return best_id, best_dist


def link_frames_to_slides(conn):
    """Step 1: sets frames.slide_id and frames.match_dist. Returns (matched, total)."""
    # Clear old links first, so a frame that no longer matches (e.g. after a
    # threshold change or a re-run of `dso docs`) does not keep a stale slide.
    conn.execute("UPDATE frames SET slide_id = NULL, match_dist = NULL")

    slides = load_slides(conn)
    frames = conn.execute("SELECT id, dhash FROM frames ORDER BY file_id, t_sec").fetchall()
    total = len(frames)
    print(f"[link] matching {total} frames against {len(slides)} slides "
          f"(match if distance <= {MATCH_MAX_DIST})")

    matched = 0
    done = 0
    for frame in frames:
        frame_hash = imagehash.hex_to_hash(frame["dhash"])
        slide_id, dist = closest_slide(frame_hash, slides)
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

    matched, frame_total = link_frames_to_slides(conn)
    print(f"[link] {matched}/{frame_total} frames show a slide "
          f"({frame_total - matched} without a match - normal for camera shots or videos without a deck)")

    linked, segment_total = link_segments_to_frames(conn)
    print(f"[link] {linked}/{segment_total} transcript segments linked to a frame")

    # Nothing is saved until commit(); if anything above crashed, the DB keeps
    # the old links instead of half-written new ones.
    conn.commit()
    conn.close()
    return 0
