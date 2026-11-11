# Data contract v1

SHARED FILE. Change only in a `contract:` PR, together with `dso/common/schema.sql`.

## 1. Pipeline order and who reads/writes what
Every step is a `dso` subcommand (`uv run dso <command>`). Global options:
`--db` (default `out/dso.db`) and `--out` (default `out`), given BEFORE the command:
`uv run dso --db out/dso.db docs`.

| # | Command | Owner | Reads | Writes |
|---|---|---|---|---|
| 1 | `ingest <folder> [--source S] [--owner O]` | B | files on disk | `files` |
| 2 | `video [--model base.en]` | A | `files` kind='video' | `frames`, `segments`, `out/frames/`, `out/transcripts/` |
| 3 | `docs` | B | `files` kind='document' | `slides`, `out/slides/` |
| 4 | `dedup` | B | `files`, `slides` | `duplicates` |
| 5 | `link` | C | `frames`, `slides`, `segments` | `frames.slide_id/match_dist`, `segments.frame_id` |
| 6 | `index` | C | `segments`, `slides` | `search_fts` |
| 7 | `search "<q>" [--json]` | C | `search_fts` + joins | stdout |
| 8 | `export` | C | everything | `out/export/<sha12>.json` + `.md` |
| 9 | `offboard <folder>` | D | `files`, `duplicates` | `out/reports/offboarding.md` (DRY RUN) |
| 10 | `savings` | D | `files`, sizes under `out/` | `savings`, `out/reports/savings.md` |
| – | `web [--port 5000]` | D | DB + `out/` (read only) | – |
| – | `all <folder>` | D | runs 1–6, 8, 10 | |

Every command must be safe to run twice.

## 2. File classification (`files.kind`)
- `video`: mp4, mkv, mov, webm, avi
- `document`: pdf, pptx, ppt, docx, odp (non-PDF → PDF via LibreOffice if installed)
- `other`: everything else (still hashed, still used for exact duplicates)

## 3. Output folder layout
`sha12` = first 12 hex chars of the file's SHA-256 (content-addressed, stable).
All paths stored in the DB are relative to `--out` and use `/` (`Path.as_posix()`).
```
out/
  dso.db
  frames/<sha12>/f0001.webp        key frames of one video (WebP, quality 60)
  transcripts/<sha12>.json         raw faster-whisper segments
  slides/<sha12>/p001.webp         rendered PDF pages
  export/<sha12>.json, <sha12>.md  AI-ready export per recording / document
  reports/savings.md, offboarding.md
```

## 4. Hashes (all in `dso/common/hashing.py` — always use these functions)
- File hash: `sha256_file(path)`, lowercase hex.
- Slide text: `normalize_text()` = lowercase, all whitespace → one space, trimmed;
  `text_sha256 = sha256_text(normalize_text(text))`.
- Image: `dhash_image(pil_image)` = imagehash dHash 8×8 → 16 lowercase hex chars.
- Distance: `hamming(a, b)` = number of differing bits (0..64).

## 5. Slide ID
`slide_id = "<doc>:<page>:<text_sha256[:8]>"` where `<doc>` = `files.doc_number` if known,
else the file's `sha12`; `<page>` is 1-based. Example `3fa9c0d1e2b4:7:9f2c01ab`.
Identical slides in two decks have different slide_ids but the same `text_sha256`;
that relation goes into `duplicates`.

## 6. Thresholds (measured on fixtures 2026-11-11 — re-tune on real data, log in DECISIONS.md)
| Name | Value | Used by |
|---|---|---|
| frame sampling | 1 per second | `video` |
| keep a sampled frame if distance to last kept frame ≥ | 6 | `video` |
| frame ↔ slide match if closest slide distance ≤ | 6 | `link` |
| slide_visual duplicate if distance ≤ | 3 | `dedup` |
| revision: same deck, slide text similarity (difflib ratio) > | 0.8 | `dedup` |

Measured with imagehash on fixtures: same slide in two decks = 0; video frame vs its
own slide = 1–4; vs a different slide = 8–20; two different slides = 8–19.
A revision changing "4 ms" → "3 ms" has distance **0** — dHash cannot see small text
edits, so revisions are detected from text. ffmpeg scene scores for a slide change
were only 0.19–0.25 (the usual 0.3 finds nothing), which is why we sample at 1 fps.

## 7. Duplicates
- `file_exact`: same `files.sha256`. `b_ref` (canonical) = copy whose name has no
  copy-hint ("(1)", " - Copy", "Copy of"); tie → oldest `scanned_at`, then lowest id.
  `a_ref`/`b_ref` are `files.id` as text.
- Name hints only go into `note`; they never create a duplicate without a hash match.
- `slide_text`: same non-empty `text_sha256` in different files (refs = slide_ids).
- `slide_visual`: distance ≤ 3, text hash differs or text empty.
- `revision`: same doc_number, or ≥ 50% shared slides, different file hash.
  Canonical = highest revision / newest date.

## 8. AI-ready export (`out/export/<sha12>.json`)
```json
{
  "schema_version": 1,
  "source": {"file_name": "Event day 1.mp4", "path": "data/play/Event day 1.mp4",
             "sha256": "…", "size_bytes": 1043234567, "kind": "video", "system": "play",
             "doc_number": null, "revision": null, "author": null, "date": null},
  "generated_at": "2026-11-14T10:00:00Z",
  "duration_sec": 7200.0,
  "frames": [{"id": 12, "t_sec": 95.0, "image": "frames/3fa9c0d1e2b4/f0012.webp",
              "dhash": "f0e1d2c3b4a59687", "slide_id": "3fa9c0d1e2b4:7:9f2c01ab", "match_dist": 3}],
  "segments": [{"id": 40, "start_sec": 96.0, "end_sec": 101.5, "speaker": "Speaker ?",
                "text": "As you see here, the latency drops…", "frame_id": 12,
                "slide_id": "3fa9c0d1e2b4:7:9f2c01ab"}],
  "slides_referenced": [{"slide_id": "3fa9c0d1e2b4:7:9f2c01ab", "text": "…",
                         "image": "slides/3fa9c0d1e2b4/p007.webp"}]
}
```
Documents use the same shape with empty `frames`/`segments`. The `.md` file is the
same content for humans: one heading per frame, timestamp, image, transcript under it.

## 9. Search result (`dso.search.search(conn, q)` → list of dicts; `--json` prints it)
```json
[{"kind": "segment", "file": "Event day 1.mp4", "t_sec": 96.0,
  "frame_image": "frames/3fa9c0d1e2b4/f0012.webp", "slide_id": "…", "snippet": "…[latency]…"}]
```
`t_sec` and `frame_image` are null for slide results.
