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
uv run python scripts/prefetch_models.py small     # Whisper model, ~480 MB, once
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
Check: every video says `transcript: reusing ...` or `already processed - skipped`, `dedup`
prints the `final -> final2` revision, `link` prints about `41/393 frames show a slide`.

## 3. Connect the cloud (test it before the demo)

There is no official OneDrive app for Linux, so use **rclone** (talks to OneDrive /
SharePoint directly):
```fish
rclone config
```
`n` (new remote) → name `onedrive` → storage `onedrive` → Enter for the defaults → log in in
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

Terminal 3 (`systemd-inhibit` keeps the laptop awake while it runs):
```fish
cd ~/Projects/dso-py
# cloud: download new uploads, write the AI-ready results back to DSO demo/_dso_distilled
systemd-inhibit --what=sleep:idle --why="dso demo" uv run dso watch data/cloud --rclone "onedrive:DSO demo" --source onedrive --interval 10 --max-seconds 120 --upload
# offline fallback
systemd-inhibit --what=sleep:idle --why="dso demo" uv run dso watch data/inbox --interval 10 --max-seconds 120
```
`--interval 10` checks every 10 s; `--max-seconds 120` transcribes only the first 2 minutes
of a new video so it finishes on stage. The UI reloads its data every 10 s by itself.

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
6. In OneDrive, open `DSO demo/_dso_distilled/export/*.md`: the AI-ready text that replaces
   the video.

### If something goes wrong
- **Nothing happens after an upload**: read terminal 3. `rclone failed` → `rclone lsd onedrive:`
  (login expired? `rclone config reconnect onedrive:`). `files are still being copied` → wait.
- **The UI shows sample (mock) data**: terminal 1 (`dso web`) is not running.
- **A new video takes too long**: use a shorter one or a smaller `--max-seconds`.
- **Reset the demo**: Ctrl+C in terminal 3, `cp out/dso-before-demo.db out/dso.db`, delete the
  uploaded files from `DSO demo` (and `data/cloud/`), start terminal 3 again.
- **Stop everything**: Ctrl+C in each terminal. Nothing was deleted or moved anywhere.
