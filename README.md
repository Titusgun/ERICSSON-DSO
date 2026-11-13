# dso — Data Storage Optimization

Ericsson challenge, Campor × BUILD Hackathon 2026 (Karlskrona, 13–15 Nov).
Turns long recordings and documents into compact, searchable, AI-ready data and finds
duplicates — without losing information. Runs fully locally on Windows, macOS and Linux.

```
 folder (SharePoint / OneDrive / Play / leaver's folder)
   │ dso ingest            path, size, SHA-256
   ├─ video ─► dso video   key frames (1 fps + dHash, WebP) + faster-whisper transcript
   ├─ docs  ─► dso docs    PDF → slides (text SHA-256 + dHash + slide_id)
   ▼
 SQLite ─► dso dedup   exact / shared slides / revisions
        ─► dso link    transcript ↔ frame ↔ slide
        ─► dso index + search (FTS5)
        ─► dso export  JSON + Markdown for RAG
        ─► dso offboard (DRY RUN) · dso savings (% saved)
        ─► dso web     local browser demo on 127.0.0.1
```

## Setup (every teammate, once)
1. Install **git** and **uv** (uv installs Python 3.12 itself):
   - Windows (PowerShell): `winget install Git.Git astral-sh.uv`
   - macOS: `brew install git uv`
   - Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
2. Install the GitHub CLI (`winget install GitHub.cli` / `brew install gh` / your package manager), then `gh auth login`.
3. ```bash
   gh repo clone <owner>/dso && cd dso
   uv sync                                     # creates .venv with exact versions from uv.lock
   uv run pytest                               # should pass (feature tests are skipped)
   uv run python scripts/prefetch_models.py    # Whisper models, do this at home
   ```
4. PPTX → PDF needs **LibreOffice or Microsoft PowerPoint**. If you have PowerPoint,
   install nothing. Otherwise install LibreOffice:
   - Arch: `sudo pacman -S libreoffice-fresh`
   - macOS: `brew install --cask libreoffice`
   - Windows: `winget install TheDocumentFoundation.LibreOffice`

   Manual fallback: if no converter works, `dso docs` skips the file and prints what to do:
   open it in PowerPoint, export as PDF, save it as `out/converted/<sha12>.pdf`, then run
   `dso docs` again.
5. Download the sample archive from the team chat and unpack it into `data/`
   (git-ignored; never commit or upload it).

## Daily use
```bash
git switch -c a/keyframes                  # your letter / topic
uv run dso --db out/dso.db all data/       # run on real data (data/ is git-ignored)
uv run dso search "latency"
uv run dso web                             # http://127.0.0.1:5000
uv run pytest && git push -u origin HEAD && gh pr create --fill
```

## Team
Read **AGENTS.md** (rules for humans and AI agents) and **docs/DATA_CONTRACT.md**.

| | Area | Files |
|---|---|---|
| A | Video: key frames, transcript | `dso/video.py` |
| B | Documents, hashing, duplicates | `dso/ingest.py`, `dso/docs.py`, `dso/dedup.py` |
| C | Linking, search, export | `dso/link.py`, `dso/search.py`, `dso/export.py` |
| D | CLI, web, offboarding, savings, pitch | `dso/cli.py`, `dso/web/`, `dso/offboard.py`, `dso/savings.py` |

## Data safety
Ericsson sample files are never committed or uploaded. Put them in `data/` (git-ignored).
Only synthetic files in `tests/fixtures/` (made by `scripts/make_fixtures.sh`) are in the repo.

## Licences of what we use
faster-whisper (MIT), PyAV/OpenCV (BSD/Apache, bundle LGPL ffmpeg), pypdfium2 (Apache-2.0/BSD),
imagehash (BSD), Pillow (HPND), Flask (BSD), SQLite (public domain).
