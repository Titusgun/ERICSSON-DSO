# AGENTS.md — rules for every AI coding agent (and human) in this repo

Read this whole file before changing anything. It is how the 4 of us (and our agents)
work in parallel without breaking each other.

## 1. What we are building
Ericsson "Data Storage Optimization" challenge, Campor × BUILD Hackathon, 13–15 Nov 2026.
A local tool `dso` (CLI + local web UI) that turns recordings and documents into compact,
searchable, AI-ready data and finds duplicates. Brief: `docs/BRIEF.md`.
Do not invent requirements; unclear things are questions for the organizers.

## 2. Stack (do not change without a team decision in docs/DECISIONS.md)
- Python 3.12, managed with **uv** (`uv sync`, `uv run ...`). Same on Windows, macOS, Linux.
- faster-whisper (transcripts), opencv-python-headless (video frames), pypdfium2 (PDF pages
  + text), imagehash (dHash), Pillow (WebP), sqlite3 + FTS5 (stdlib), Flask (local UI).
- No system ffmpeg needed. LibreOffice is optional (only for PPTX → PDF).
- Do not add dependencies without asking the team. Never add anything that sends data
  to an online service (no cloud APIs, no hosted AI, no telemetry).
- We do NOT train models or write codecs/parsers ourselves.

## 3. Architecture: modules talk only through the database
- Every module is one `dso` subcommand with a `run(args)` function in its own file.
- Modules communicate **only through the SQLite DB and files in `out/`**, never by
  importing each other. Exceptions: `dso/common/*` helpers, and `dso.web` may call
  `dso.search.search()`.
- `dso/cli.py` already routes every command, so nobody needs to edit it.
- Every command must be safe to run twice (delete its own old rows for a file first).

## 4. The data contract — read before writing code
`docs/DATA_CONTRACT.md` + `dso/common/schema.sql`. These are **shared**: do not change
them in a feature PR. To change: separate PR titled `contract: ...`, tell the team.

## 5. Who owns what (stay inside your files)
| Person | Area | Owns |
|---|---|---|
| A | Video: key frames, transcript | `dso/video.py` |
| B | Documents + duplicates: slides, hashes, revisions | `dso/ingest.py`, `dso/docs.py`, `dso/dedup.py`, `dso/common/hashing.py` |
| C | Linking, search, AI export | `dso/link.py`, `dso/search.py`, `dso/export.py`, `dso/common/db.py` |
| D | CLI, web UI, offboarding, savings, CI, pitch | `dso/cli.py`, `dso/web/`, `dso/offboard.py`, `dso/savings.py`, `scripts/`, `.github/`, `pitch/` |

An agent working for person X edits only X's files plus tests for X's feature.
If it needs a change elsewhere, it stops and writes that in the PR description.

## 6. Priorities — finish and integrate MUST before SHOULD/COULD
- MUST: transcript with timestamps; key frames; frames linked to text; exact + slide-level
  duplicates; search; savings numbers.
- SHOULD: frame ↔ PDF slide matching; revisions; offboarding report; web demo view.
- COULD: speaker identification; slide description prompts; rebuilding video from frames.

## 7. Code style (the team knows basic Python, PHP, JS)
- Plain functions, lists, dicts, for/while loops, `if/elif/else`. Readable over clever.
- No classes unless really needed, no decorators of our own, no metaprogramming,
  no nested comprehensions, no async.
- Use `pathlib.Path` for every path (Windows uses `\`). Store paths in the DB with
  `.as_posix()`. Open text files with `encoding="utf-8"`.
- English identifiers, comments, commit messages and docs. Short docstring per function.
- Print progress so a long video run shows it is alive.

## 8. Data safety (non-negotiable)
- **Never commit Ericsson sample files** or anything generated from them (frames,
  transcripts, DB). They live in `data/` and `out/`, both git-ignored.
- Never upload sample data anywhere (GitHub, cloud AI tools, Lovable, pastebins).
  Do not paste real transcript text into prompts of online tools either.
  Use `tests/fixtures/` (synthetic) for anything shared.
- Offboarding is always a DRY RUN. No code in this repo deletes or moves user files.
- The web UI binds to 127.0.0.1 only.

## 9. Workflow
1. Take a GitHub Issue. One issue = one small, testable step.
2. Branch `<letter>/<topic>`, e.g. `a/keyframes`.
3. Before pushing: `uv run pytest` must pass. When your feature works, remove the
   `@pytest.mark.skip` line of its test in `tests/test_pipeline.py`.
4. PR into `main` with `Closes #<n>`. CI (Windows + macOS + Linux) must be green.
   Merge yourself if you only touched your files; otherwise ask a teammate.
5. `git pull --rebase origin main` often. Small PRs, several per day.
6. Decisions others need (thresholds, naming): one line in `docs/DECISIONS.md`.
