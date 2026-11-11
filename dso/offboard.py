"""`dso offboard <folder>` (owner: D). ALWAYS A DRY RUN.

The departing employee's folder is ingested with --source offboarding.
For each of their files:
  - same sha256 exists outside their folder -> "duplicate exists at <canonical> -> safe to delete"
  - otherwise                                -> "unique -> keep / distill"
Write out/reports/offboarding.md with a table and the bytes that could be freed.
This code must never delete, move or modify user files.

Fixture check: jane/"deck_b - Copy.pdf" -> safe to delete; jane/my_notes.pdf -> keep.
"""


def run(args):
    print("[offboard] TODO (owner D): dry-run report")
    return 0
