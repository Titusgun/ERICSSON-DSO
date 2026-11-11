"""`dso savings` (owner: D). Bytes before vs after, measured on the real files.

Scenarios:
  video_distill: sum(video sizes) vs sum(frames + transcript JSON + export files)
  dedup:         sum(all files)   vs sum(files with a unique sha256)
  offboarding:   sum(leaver files) vs sum(their unique files)
Store rows in `savings`, write out/reports/savings.md with percentages.
"""


def run(args):
    print("[savings] TODO (owner D): before/after per scenario")
    return 0
