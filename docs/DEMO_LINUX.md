# Live demo on Arch Linux

Commands are for the **fish** shell (they also work in bash unless noted). The Windows
version is [DEMO_WINDOWS.md](DEMO_WINDOWS.md). Do parts 1–3 once; part 4 is the demo.

fish tip: `<name>` in these docs is never typed literally - fish reads `<` and `>` as file
redirects. Use the real name, in quotes if it has spaces: `"data/offboarding/Māris Līveris"`.

## 1. One-time setup

```fish
sudo pacman -S git uv github-cli nodejs npm rclone libreoffice-fresh
cd ~/Projects
gh repo clone Titusgun/ERICSSON-DSO dso-py
git clone https://github.com/Titusgun/screenshot-to-interface-670.git dso-ui
cd dso-py
uv sync
uv run pytest -q                                   # must pass
uv run python scripts/prefetch_models.py           # Whisper small (~480 MB) + speaker models (~34 MB), once
cd ../dso-ui; npm install; cd ../dso-py
```
LibreOffice converts the PPTX/DOCX files (`dso docs` prints `converter used: LibreOffice`).

## 2. Prepare the real data

Put the sample archive into `data/` (already done on Kristofers' laptop). The Whisper
transcripts are in `out/transcripts/` (also in git), so this only redoes key frames, slides
and the rest - minutes, not hours:
```fish
uv run dso all data
uv run dso ingest "data/offboarding/Māris Līveris" --source offboarding --owner "Māris Līveris"
uv run dso offboard "data/offboarding/Māris Līveris" --simulated
uv run dso savings
cp out/dso.db out/dso-before-demo.db              # backup: lets you reset the demo
```
The FIRST run also analyses who speaks (`[speakers]`): about 25 minutes for the 2.5 h meeting,
once - later runs reuse `out/speakers/`. Do this well before the demo.
Check: every video says `transcript: reusing ...` or `already processed - skipped`, `dedup`
prints the `final -> final2` revision, `link` prints about `41/393 frames show a slide`.

## 3. Connect the cloud (test it before the demo)

There is no official OneDrive app for Linux, so use **rclone** (talks to OneDrive /
SharePoint directly):
```fish
rclone config
```
`n` (new remote) → name `onedrive` → storage **`onedrive` (Microsoft OneDrive)** - careful, **not `opendrive`**, which is the line next to it and a different company (`dso watch` refuses an `opendrive` remote) → Enter for the defaults → log in in
the browser → **OneDrive personal/business** or **SharePoint site** → pick the drive. Then:
```fish
rclone lsd onedrive:                    # lists your folders = login works
rclone mkdir "onedrive:DSO demo"
```
If the login expires later: `rclone config reconnect onedrive:`.

(Alternative: the community sync client `onedrive` - `paru -S onedrive-abraunegg`, then
`onedrive --monitor` keeps `~/OneDrive` in sync and you watch that folder like on Windows.)

**Offline fallback** (no Wi-Fi): watch a local folder `data/inbox` and drop files into it with
the file manager. Same demo, no cloud.

## 4. Demo

Three terminals:

| Terminal | Command | What it is |
|---|---|---|
| 1 | `cd ~/Projects/dso-py; uv run dso web` | API on http://127.0.0.1:5000 (local only) |
| 2 | `cd ~/Projects/dso-ui; npm run dev` | the UI → open **http://localhost:8080** |
| 3 | see below | `dso watch`: processes new uploads automatically |

**Settings once, then no flags:** `cp dso.example.toml dso.toml` (it already has the demo
settings below: data/cloud, onedrive:DSO demo, interval 10, whole videos, upload, delete originals).
Then terminal 3 is just `uv run dso watch`. Typed flags still win, e.g. `uv run dso watch --max-seconds 600`.

Terminal 3 with all flags written out (`systemd-inhibit` keeps the laptop awake while it runs):
```fish
cd ~/Projects/dso-py
# cloud: download new uploads, write the AI-ready results back to DSO demo/_dso_distilled,
# then move the originals whose content is kept (distilled videos, copies, old revisions)
# to the OneDrive recycle bin
systemd-inhibit --what=sleep:idle --why="dso demo" uv run dso watch data/cloud --rclone "onedrive:DSO demo" --source onedrive --interval 10 --upload --delete-originals
# offline fallback
systemd-inhibit --what=sleep:idle --why="dso demo" uv run dso watch data/inbox --interval 10
```
`--delete-originals` frees the space for real (recycle bin, restorable 93 days; log in
`_dso_distilled/reports/cleanup.md`). Videos are processed whole by default and only then
removed. Leave the flag out for a show-only run; `uv run dso cleanup data/cloud --rclone "onedrive:DSO demo"` shows the plan.
`--interval 10` checks every 10 s. Plan the time: 5 minutes of audio take about a minute; a
long upload can be capped with `--max-seconds 600` (first 10 min only - then it is NOT deleted). The UI reloads its data every 10 s by itself.

### The script (≈ 5 min)
1. **Real data** (already processed): savings per scenario (video 98 %, old revisions 40 %,
   offboarding 50 %), the 2.5 h Teams recording → transcript next to the key frames and the
   matched slide; search a word → jumps to that moment in the video.
2. **Duplicates**: `final` → `final2` revision: *changed text: none, removed 4, 12, 18*;
   the newest version is kept, the old one's slides stay in the distilled store.
3. **Offboarding tab**: Māris Līveris (simulated) - safe to delete only where a copy exists
   elsewhere. Dry run: nothing is deleted.
4. **Live**: in the browser (OneDrive / SharePoint), upload a short video (1–2 min, prepared)
   and a copy of a real PDF renamed to `<name> (1).pdf`.
5. Terminal 3 prints `change detected: 2 new ...` and runs the pipeline; within ~1–2 min the UI
   shows the new files, the `(1)` copy as an exact duplicate, the higher savings and the new
   video's transcript is searchable.
6. In OneDrive: the uploaded video and the `(1)` copy are gone (recycle bin), and
   `_dso_distilled/reports/cleanup.md` says why and how much space was freed. Open
   `DSO demo/_dso_distilled/export/*.md`: the AI-ready text that replaces
   the video.

### Run it automatically (no terminals)
`web` and `watch` as user services: they start at login and restart if they stop.
```fish
cp dso.example.toml dso.toml                      # once; adapt if needed
mkdir -p ~/.config/systemd/user
set uv_path (which uv)                            # systemd does not know your shell's PATH
for unit in dso-watch dso-web
    sed "s|/usr/bin/uv|$uv_path|" scripts/systemd/$unit.service > ~/.config/systemd/user/$unit.service
end
systemctl --user daemon-reload
systemctl --user enable --now dso-web dso-watch
journalctl --user -u dso-watch -f                 # live log (Ctrl+C stops only the log)
```
Stop: `systemctl --user stop dso-watch dso-web`; never start again: `systemctl --user disable dso-watch dso-web`.
The `sed` line puts your real uv path into the units (e.g. Homebrew's
`/home/linuxbrew/.linuxbrew/bin/uv`). They expect the project in `~/Projects/dso-py`; edit
`WorkingDirectory` in `~/.config/systemd/user/dso-*.service` if yours is elsewhere. The UI (`npm run dev`)
still runs in a terminal.

### If something goes wrong
- **Nothing happens after an upload**: read terminal 3. `rclone failed` → `rclone lsd onedrive:`
  (login expired? `rclone config reconnect onedrive:`). `files are still being copied` → wait.
- **The UI shows sample (mock) data**: terminal 1 (`dso web`) is not running.
- **A new video takes too long**: use a shorter one, or restart with `--max-seconds 120` (then it stays in the cloud).
- **Reset the demo**: Ctrl+C in terminal 3, `cp out/dso-before-demo.db out/dso.db`, delete the
  uploaded files from `DSO demo` (and `data/cloud/`), start terminal 3 again.
- **Stop everything**: Ctrl+C in each terminal. Nothing was deleted or moved anywhere.
