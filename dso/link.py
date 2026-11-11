"""`dso link` (owner: C)

1. For every frame: find the slide with the smallest hamming(frame.dhash, slide.dhash).
   If that distance <= 6, set frames.slide_id and frames.match_dist.
   (Fixture: own slide 1-4, other slides 8-20.)
2. For every segment: frame_id = the last frame of the same video with t_sec <= start_sec,
   so "as you see here" points at the slide that was on screen at that moment.
"""


def run(args):
    print("[link] TODO (owner C): frame<->slide and segment->frame links")
    return 0
