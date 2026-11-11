"""`dso dedup` (owner: B). Fills the `duplicates` table (DATA_CONTRACT 7).

1. file_exact: SELECT sha256 FROM files GROUP BY sha256 HAVING COUNT(*) > 1.
   Canonical copy (b_ref) = the name without "(1)", " - Copy", "Copy of".
   Name hints only go into `note` - they never create a duplicate on their own.
2. slide_text: slides in different files with the same non-empty text_sha256.
3. slide_visual: hamming(dhash_a, dhash_b) <= 3 but different text hash.
4. revision: same doc_number, or >= 50% shared slides, but different file sha256.
   dHash is 0 for small text edits ("4 ms" -> "3 ms"), so compare slide TEXT here
   (difflib.SequenceMatcher(None, a, b).ratio() > 0.8 = "small change").

Fixture check: 2 file_exact (deck_a / "deck_a (1)", deck_b / jane's copy),
deck_a and deck_b share 2 slides, deck_a_rev2 is a revision of deck_a.
Re-run safe: DELETE FROM duplicates first.
"""


def run(args):
    print("[dedup] TODO (owner B): exact, slide-level and revision duplicates")
    return 0
