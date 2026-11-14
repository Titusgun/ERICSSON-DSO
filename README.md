# dso — Data Storage Optimization

Ericsson challenge, Campor × BUILD Hackathon 2026 (Karlskrona, 13–15 Nov).

`dso` turns long meeting recordings and documents into compact, searchable, AI-ready data,
finds duplicates and older revisions, and frees the storage they take — without losing the
information. Everything runs locally on Windows, macOS and Linux; no cloud AI is used.

**Two repositories work together:**

| Repository | What it is |
|---|---|
| [Titusgun/ERICSSON-DSO](https://github.com/Titusgun/ERICSSON-DSO) (this one) | the `dso` command-line tool and the local web API (Python) |
| [Titusgun/ERICSSON-DSO-WEB](https://github.com/Titusgun/ERICSSON-DSO-WEB) | the web interface (React, built with Lovable); reads everything from the API above |

## What it does

| Input | What `dso` makes of it |
|---|---|
| Video (mp4, …) | key frames (only when the picture changes), a transcript with timestamps (Whisper, English + Swedish), who spoke when (Speaker 1, 2, …), each sentence linked to the frame and the slide that was on screen |
| Documents (PDF, PPTX, DOCX) | every page as a picture + text, a fingerprint per slide, author and date |
| All files | exact duplicates, slides shared between decks, older revisions of a deck (with the changed slides) |
| Everything | full-text search ("which meeting talked about X, and at what minute?"), an AI-ready export (`corpus.jsonl`), savings in %, a dry-run offboarding report for a departing employee |

```
 folder or OneDrive / SharePoint (dso watch)
   │ dso ingest            path, size, SHA-256
   ├─ video ─► dso video   key frames (1 fps + dHash, WebP) + faster-whisper transcript
   │           dso speakers who spoke when (sherpa-onnx)
   ├─ docs  ─► dso docs    PDF / PPTX / DOCX → slides (text hash + dHash + slide_id)
   ▼
 SQLite ─► dso dedup   exact copies / shared slides / revisions
        ─► dso link    transcript ↔ key frame ↔ slide
        ─► dso index   full-text search (FTS5)
        ─► dso export  JSON + Markdown per file, corpus.jsonl for AI
        ─► dso savings · dso offboard (dry run) · dso cleanup (cloud recycle bin)
        ─► dso web     local API on 127.0.0.1:5000 for the web interface
```

## Setup (once per computer)

### 1. Install the tools

| | Windows (PowerShell) | macOS | Arch Linux |
|---|---|---|---|
| git + uv (uv installs Python 3.12 itself) | `winget install Git.Git astral-sh.uv` | `brew install git uv` | `sudo pacman -S git uv` |
| Node.js (for the web interface) | `winget install OpenJS.NodeJS.LTS` | `brew install node` | `sudo pacman -S nodejs npm` |
| Office program as LibreOffice or Microsoft Office if not already installed.

|Can point to cloud storage or local/on site network drive. 
| rclone (only for OneDrive / SharePoint) | `winget install Rclone.Rclone` | `brew install rclone` | `sudo pacman -S rclone` |

### 2. Get both repositories, side by side

```bash
git clone https://github.com/Titusgun/ERICSSON-DSO.git dso
git clone https://github.com/Titusgun/ERICSSON-DSO-WEB.git dso-web
```

### 3. Set up the tool

```bash
cd dso
uv sync                                    # Python 3.12 + all packages, exact versions from uv.lock
uv run pytest                              # all tests must pass
uv run python scripts/prefetch_models.py   # Whisper "small" (~480 MB) + speaker models (~34 MB), once
```

### 4. Set up the web interface

```bash
cd ../dso-web
npm install
```

### 5. Your files

Put the recordings and documents to analyse into `dso/data/` (any sub-folders). `data/` is
git-ignored: your files are never committed. `out/` in this repository already contains the
results we produced from Ericsson's sample data, so the web interface shows real content
right away.

## Run it

### Process a folder

```bash
cd dso
uv run dso all data/       # ingest, video, speakers, docs, dedup, link, index, export, savings
```

The first run takes a while for long videos (on a laptop CPU roughly half the video's length
for the transcript, plus a few minutes per hour for speakers). Results are cached: running it
again only processes new or changed files.

### Open the web interface (two terminals)

```bash
# terminal 1 - the API, in the dso folder
uv run dso web                    # http://127.0.0.1:5000

# terminal 2 - the interface, in the dso-web folder
npm run dev                       # open http://localhost:8080
```

**How the two connect:** the web interface asks the API at `http://127.0.0.1:5000`. The API
only listens on this computer (127.0.0.1) and only answers pages from `localhost`, so nothing
is reachable from the network. To use another port:

```bash
uv run dso web --port 5001                                   # dso folder
VITE_API_URL=http://127.0.0.1:5001 npm run dev               # dso-web folder (bash/zsh)
env VITE_API_URL=http://127.0.0.1:5001 npm run dev           # fish
$env:VITE_API_URL="http://127.0.0.1:5001"; npm run dev       # PowerShell
```

If the interface shows sample ("mock") data, the API in terminal 1 is not running.
Without the interface, `uv run dso web` also serves a simple built-in page at
http://127.0.0.1:5000.

### Search from the terminal

```bash
uv run dso search "latency"            # every hit: file, time or slide, the matching text
uv run dso search "latency" --json
```

## Automatic mode: `dso watch`

`dso watch` checks a folder every few seconds and runs the whole pipeline when files are added
or changed. Already processed files are skipped, so a new PDF takes seconds. The web interface
refreshes itself every 10 s.

```bash
uv run dso watch data/inbox
```

Settings can live in `dso.toml` instead of flags: `cp dso.example.toml dso.toml`, adapt it,
then just `uv run dso watch` (typed flags still win). Videos are processed whole by default;
`--max-seconds N` processes only the first N seconds (for quick tests).

---

Changing the folder dso watch watches

dso watch can watch three kinds of places. Set the place in dso.toml (then just run
uv run dso watch) or give it on the command line, which wins over dso.toml. Restart
dso watch after changing dso.toml.

1. A local folder or a network drive. Point folder at it. dso reads the files where
they are; nothing is copied or changed.

[watch]
folder = "data/inbox"                    # a folder in the project
# folder = 'Z:\Recordings'               # Windows: mapped network drive
# folder = '\\fileserver\team\Meetings'  # Windows: network share (UNC path)
# folder = "/mnt/recordings"             # Linux: mounted network share
# folder = "/Volumes/Team/Meetings"      # macOS: mounted network share
source = "local"                         # label stored with every file: local, sharepoint, onedrive, play, ...

On the command line: uv run dso watch "Z:/Recordings".

2. OneDrive or SharePoint through the sync client (Windows / macOS, built in). Sync the
OneDrive folder or SharePoint library, then watch the synced folder like any local folder.
In Explorer, right-click the folder and choose "Always keep on this device", so the files
are really downloaded and not just placeholders.

[watch]
folder = 'C:\Users\you\OneDrive - Company\Recordings'
source = "onedrive"

3. OneDrive or SharePoint straight from the cloud, with rclone. Here rclone is the
cloud folder, and folder is the local folder the new files are downloaded into.

[watch]
rclone = "onedrive:Recordings"       # <remote name>:<folder in that cloud>
folder = "data/cloud-recordings"     # local copy; use one local folder per cloud folder
source = "onedrive"
upload = true                        # results back to Recordings/_dso_distilled/
delete_originals = true              # optional: originals kept in reduced form → cloud recycle bin

A SharePoint library needs its own rclone remote. Run rclone config → n → a name such as
sharepoint → storage onedrive → choose SharePoint site and the library. Then use, for
example, rclone = "sharepoint:Shared Documents/Recordings".
rclone lsd <remote>: lists the folders, so you can check the name before starting.

Good to know:
- Windows paths in dso.toml: use single quotes ('Z:\Recordings') or forward slashes
  ("Z:/Recordings"). Inside double quotes, a backslash starts an escape sequence and the
  file won't load (dso watch reports the error).
- Paths with spaces need quotes on the command line: uv run dso watch "Z:/Team Meetings".
- Switching folders keeps the earlier results. Files from the old folder stay in the
  database and the web interface; the new folder's files are added.
- Network drives and synced folders are only read. --delete-originals only removes
  files in the cloud folder given with rclone, into the cloud recycle bin. Local files and
  network drives are never touched.
- A large video still being copied to the folder isn't processed half-finished. dso watch
  waits until the file stops changing.

---


### With OneDrive / SharePoint

1. Log in once: `rclone config` → `n` (new remote) → name `onedrive` → storage **`onedrive`**
   ("Microsoft OneDrive" — not `opendrive`, which is a different company) → defaults → log in
   in the browser → choose OneDrive or a SharePoint site. Check with `rclone lsd onedrive:`.
2. Watch a cloud folder:

```bash
uv run dso watch data/cloud --rclone "onedrive:DSO demo" --upload --delete-originals
```

- `--rclone` downloads new files from that OneDrive folder before every check.
- `--upload` writes the results (AI-ready exports, reports) back to `DSO demo/_dso_distilled/`.
- `--delete-originals` frees the space: originals whose content is kept in reduced form go to
  the **OneDrive / SharePoint recycle bin** (restorable, 93 days by default) — fully processed
  videos, exact copies (the original stays) and older revisions (the newest stays). Right
  before removing, the cloud file must still be byte-identical to what was processed. Every
  removal is logged in `_dso_distilled/reports/cleanup.md`. **Local files are never deleted.**

Step-by-step guides for a live demo: [Windows](docs/DEMO_WINDOWS.md) · [Arch Linux](docs/DEMO_LINUX.md)
(including starting `dso watch` and `dso web` automatically at login).

## Offboarding (departing employee)

A dry run: nothing is deleted. A file is "safe to delete" only when an identical copy exists
elsewhere in the company — a copy in another leaver's folder does not count.

```bash
uv run dso ingest "data/offboarding/Anna Svensson" --source offboarding --owner "Anna Svensson"
uv run dso offboard "data/offboarding/Anna Svensson" --simulated   # --simulated: demo data
uv run dso savings
```

One report per leaver in `out/reports/offboarding/`, all leavers together in
`out/reports/offboarding.md` and in the web interface's Offboarding tab.

## Commands

| Command | What it does |
|---|---|
| `dso ingest <folder>` | registers files: path, size, SHA-256, kind |
| `dso video` | key frames + transcript for every video (`--model`, `--max-seconds`, `--force`) |
| `dso speakers` | who spoke when: Speaker 1, 2, … per transcript line |
| `dso docs` | PDF/PPTX/DOCX → slides with text, pictures and fingerprints |
| `dso dedup` | exact duplicates, shared slides, revisions with changed slides |
| `dso link` | transcript → key frame → slide |
| `dso index` / `dso search "<words>"` | full-text index and search |
| `dso export` | per file JSON + Markdown, `out/export/corpus.jsonl`, `out/reports/ai_readiness.md` |
| `dso savings` | before/after per scenario → `out/reports/savings.md` |
| `dso offboard <folder>` | dry-run report for a departing employee |
| `dso cleanup <folder> --rclone REMOTE:PATH [--apply]` | free cloud storage (recycle bin); dry run without `--apply` |
| `dso all <folder>` | ingest → … → savings in one go |
| `dso watch [folder]` | `all` automatically on every change (settings from `dso.toml`) |
| `dso web` | local API for the web interface |

Global options go before the command: `uv run dso --db out/dso.db --out out <command>`.

## Results on Ericsson's sample data (13–14 Nov 2026)

| | |
|---|---|
| Video → key frames + transcript | 1.4 GB → 26.7 MB (**98.2 % smaller**), 3 videos, 3 h |
| Older revisions (`final` → `final2`) | 6.8 MB → 4.1 MB (40.3 %); the old deck's changed/removed slides are kept as pictures + text |
| Actually freed in OneDrive (recycle bin) | 1.4 GB |
| Searchable | 4,225 items: every transcript line and every slide |
| Transcript linked to the key frame / a slide | 100 % / 28 % (films and camera views have no slide) |
| AI-ready corpus | 512 chunks, 31,453 words, 469 KB for 1.4 GB of originals |
| Speakers | 13 in the 2.5 h Teams meeting, 1 each in the two talks |

Details: `out/reports/savings.md`, `out/reports/ai_readiness.md`, `out/reports/cleanup.md`.

## Data and privacy

- Everything is processed on this computer. No cloud AI, no telemetry. The only online parts
  are opt-in: downloading the public models once, and rclone talking to *your own*
  OneDrive / SharePoint.
- `data/` (your files) is git-ignored. `out/` holds the results generated from them.
- The web API listens on 127.0.0.1 only.
- Nothing local is ever deleted; offboarding is always a dry run; cloud cleanup only moves
  files to the recycle bin, only with `--delete-originals` / `--apply`.

## For developers

- Rules for humans and AI agents: [AGENTS.md](AGENTS.md). Module boundaries and file formats:
  [docs/DATA_CONTRACT.md](docs/DATA_CONTRACT.md). Why things are the way they are:
  [docs/DECISIONS.md](docs/DECISIONS.md). The challenge: [docs/BRIEF.md](docs/BRIEF.md).
- `uv run pytest` runs all tests; CI runs them on Windows, macOS and Linux for every pull
  request.

| | Area | Files |
|---|---|---|
| A | Video: key frames, transcript, speakers | `dso/video.py`, `dso/speakers.py` |
| B | Documents, hashing, duplicates | `dso/ingest.py`, `dso/docs.py`, `dso/dedup.py` |
| C | Linking, search, export | `dso/link.py`, `dso/search.py`, `dso/export.py` |
| D | CLI, web API, watch, cleanup, offboarding, savings | `dso/cli.py`, `dso/web/`, `dso/watch.py`, `dso/cleanup.py`, `dso/offboard.py`, `dso/savings.py` |

## Licences of what we use

faster-whisper (MIT), sherpa-onnx (Apache-2.0), PyAV/OpenCV (BSD/Apache, bundle LGPL ffmpeg),
pypdfium2 (Apache-2.0/BSD), imagehash (BSD), Pillow (HPND), Flask (BSD), SQLite (public domain).
External programs, installed separately: LibreOffice (MPL-2.0), rclone (MIT).
