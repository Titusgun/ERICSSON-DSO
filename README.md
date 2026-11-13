# dso — Data Storage Optimization

Ericsson challenge, Campor × BUILD Hackathon 2026 (Karlskrona, 13–15 Nov).
Turns long recordings and documents into compact, searchable, AI-ready data and finds
duplicates — without losing information. Runs fully locally on Windows, macOS and Linux.

```
 folder (SharePoint / OneDrive / Play / leaver's folder)
   │ dso ingest            path, size, SHA-256
   ├─ video ─► dso video   key frames (1 fps + dHash, WebP) + faster-whisper transcript
   │           dso speakers who spoke when: Speaker 1, 2, ... (sherpa-onnx)
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

## Automatic mode: `dso watch`
Instead of running `dso all` after every new upload, let `dso watch` do it. It checks a
folder every 30 s; when something was added or changed (and has finished copying), it runs
the whole pipeline. Videos that were already processed are skipped (`--force` redoes them),
so a new PDF takes seconds. The web UI reloads its data every 10 s, so new results appear
live. Stop with Ctrl+C. Nothing is ever deleted.
```bash
uv run dso watch data/inbox                     # a local "drop" folder
uv run dso watch data/inbox --max-seconds 120   # only if needed: just the first 2 min of new videos
```
Tired of typing the flags? `cp dso.example.toml dso.toml`, adapt it, then just `uv run dso watch`
(typed flags still win). To start it with the computer, see "Run it automatically" in the demo guides.

## Live demo with OneDrive / SharePoint
**Step by step: [Windows demo laptop](docs/DEMO_WINDOWS.md) · [Arch Linux](docs/DEMO_LINUX.md).**

**A. With the OneDrive sync client** (Windows/macOS, built in): sync a OneDrive folder or a
SharePoint library ("Sync" button in SharePoint) and watch the synced folder:
```bash
uv run dso watch "C:/Users/<you>/OneDrive - <org>/DSO demo" --source onedrive
```
**B. Straight from the cloud with rclone** (any OS, no sync client). Install rclone
(`sudo pacman -S rclone`, `brew install rclone`, `winget install Rclone.Rclone`), then log in
once: `rclone config` → `n` (new remote) → name `onedrive` → storage `onedrive` → accept
the defaults → log in in the browser → pick "OneDrive personal/business" or "SharePoint site".
```bash
uv run dso watch data/cloud --rclone "onedrive:DSO demo" --source onedrive
uv run dso watch data/cloud --rclone "onedrive:DSO demo" --upload   # also write results back
```
`--rclone` downloads new/changed files from that cloud folder before every check.
`--upload` copies the AI-ready exports and reports back to `DSO demo/_dso_distilled/` in the
cloud (off unless you ask for it). Demo: upload a short video and a `deck (1).pdf` to the
cloud folder in the browser; about a minute later the transcript, the duplicate and the new
savings % show up in the web UI (and in `_dso_distilled/` with `--upload`).

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
