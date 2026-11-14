# Storage savings

Generated 2026-11-14 14:36 UTC by `dso savings`. Measured on the files in the database.

| Scenario | Before | After | Saved | Saved % |
|---|---:|---:|---:|---:|
| video_distill | 1.4 GB | 26.7 MB | 1.4 GB | 98.2% |
| dedup | 1.5 GB | 1.4 GB | 41.8 MB | 2.7% |
| offboarding | 36.7 MB | 303.1 KB | 36.4 MB | 99.2% |
| revisions | 6.8 MB | 4.1 MB | 2.7 MB | 40.3% |

- video_distill: 3 distinct video(s) measured.
- offboarding: 15 file(s) with source 'offboarding'.
- revisions: 1 older version(s) superseded by a newer one; their slides stay under out/slides/ (pictures + text in the DB and export).
