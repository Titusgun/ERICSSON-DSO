# Live demo on the Windows laptop

Everything below is **PowerShell** (Windows Terminal → PowerShell). Do parts 1–3 the day
before; part 4 is the demo itself. The final demo results come from this one laptop and one
converter (DECISIONS.md), so do the full run here, not on another machine.

## 1. One-time setup (≈ 30 min, needs internet)

```powershell
winget install Git.Git astral-sh.uv GitHub.cli OpenJS.NodeJS.LTS Rclone.Rclone
winget install TheDocumentFoundation.LibreOffice   # recommended, see "PPTX converter" below
setx PYTHONUTF8 1          # names like "Māris Līveris" print correctly; open a NEW terminal after this
```
Close and reopen the terminal (so the new programs are found), then:
```powershell
mkdir C:\dso; cd C:\dso
gh auth login
gh repo clone Titusgun/ERICSSON-DSO dso-py
git clone https://github.com/Titusgun/screenshot-to-interface-670.git dso-ui
cd C:\dso\dso-py
uv sync
uv run pytest -q                                   # must pass
uv run python scripts/prefetch_models.py small     # Whisper model, ~480 MB, once
cd C:\dso\dso-ui; npm install; cd C:\dso\dso-py
```

**PPTX converter.** With LibreOffice installed, `dso docs` uses it (the same converter as on
the Arch laptop). Without LibreOffice it uses PowerPoint through PowerShell - that path has not
been tested yet, so check it once: after part 2, the `dso docs` output must say
`converter used: LibreOffice` or `converter used: PowerPoint (Windows)` for the two PPTX files,
and `Word (Windows)` for the DOCX. If it prints a "Manual step", follow it.

**No sleep during long runs** (laptop on power): `powercfg /change standby-timeout-ac 0`
(after the event: `powercfg /change standby-timeout-ac 30`).

## 2. Prepare the real data (day before, ≈ 15 min with the transcripts from git)

Unpack the sample archive into `C:\dso\dso-py\data\`. The repo already contains
`out\transcripts\` (Whisper results, ~1 h of CPU on the Arch laptop); `dso video` reuses them
because the file contents are the same, so only key frames and slides are redone here.

```powershell
cd C:\dso\dso-py
git pull
Remove-Item out\dso.db, out\converted, out\slides -Recurse -ErrorAction SilentlyContinue   # fresh DB, this laptop's converter
uv run dso all data
uv run dso ingest "data/offboarding/Māris Līveris" --source offboarding --owner "Māris Līveris"
uv run dso offboard "data/offboarding/Māris Līveris" --simulated
uv run dso savings
Copy-Item out\dso.db out\dso-before-demo.db        # backup: lets you reset the demo
```
Check in the output: every video says `transcript: reusing ...` (not `transcribing`), the PPTX
files show a converter (see above), and `dedup` prints the `final -> final2` revision.

## 3. Connect the cloud (choose A or B, test it the day before)

**A. OneDrive sync client (simplest on Windows, built in).** Create a folder `DSO demo` in
OneDrive (or open a SharePoint library and click **Sync**). In Explorer, right-click the folder →
**Always keep on this device**, so files are really downloaded and not just placeholders.
The folder is `"$env:OneDriveCommercial\DSO demo"` (work/school account) or
`"$env:OneDrive\DSO demo"` (personal account). Check: `dir "$env:OneDriveCommercial\DSO demo"`.

**B. rclone, straight from the cloud (no sync client).**
```powershell
rclone config
```
`n` (new remote) → name `onedrive` → storage `onedrive` → Enter for the defaults → log in in
the browser → choose **OneDrive personal/business** or **SharePoint site** → pick the drive.
Check: `rclone lsd onedrive:` lists your folders. Create `DSO demo` there (browser or
`rclone mkdir "onedrive:DSO demo"`).

**Offline fallback** (venue Wi-Fi down): watch a local folder `C:\dso\dso-py\data\inbox` and drag
files into it in Explorer. Same demo, no cloud.

## 4. Demo day

Open **three** PowerShell windows, all in `C:\dso\dso-py` (the UI one in `C:\dso\dso-ui`):

| Window | Command | What it is |
|---|---|---|
| 1 | `uv run dso web` | API on http://127.0.0.1:5000 (local only) |
| 2 | `cd C:\dso\dso-ui; npm run dev` | the UI → open **http://localhost:8080** in Edge/Chrome |
| 3 | see below | `dso watch`: processes new uploads automatically |

Window 3, depending on part 3:
```powershell
# A: OneDrive / SharePoint sync folder
uv run dso watch "$env:OneDriveCommercial\DSO demo" --source onedrive --interval 10 --max-seconds 120
# B: rclone, and write the results back to the cloud (_dso_distilled)
uv run dso watch data\cloud --rclone "onedrive:DSO demo" --source onedrive --interval 10 --max-seconds 120 --upload
# offline fallback
uv run dso watch data\inbox --interval 10 --max-seconds 120
```
`--interval 10` checks every 10 s; `--max-seconds 120` transcribes only the first 2 minutes of a
new video, so it finishes on stage. The UI reloads its data every 10 s by itself.

### The script (≈ 5 min)
1. **Real data first** (already processed): dashboard with the savings %, the 2.5 h Teams
   recording → transcript next to the key frames, search a word → jumps to that moment.
2. **Duplicates**: the `final` → `final2` revision: *changed text: none, removed 4, 12, 18*;
   the newest version is the one to keep.
3. **Offboarding tab**: Māris Līveris (simulated) - what is safe to delete because a copy
   exists, and what must be kept. Dry run: nothing is deleted.
4. **Live**: in the browser (OneDrive / SharePoint) upload
   - a short video (1–2 min, prepared beforehand, e.g. a screen recording of a few slides), and
   - a copy of one of the real PDFs, renamed to `<name> (1).pdf`.
5. Window 3 prints `change detected: 2 new ...`, then the pipeline. Within ~1–2 min the UI shows
   the new files, the `(1)` copy as an exact duplicate, the higher savings %, and the new
   video's transcript is searchable.
6. With B + `--upload`: show `DSO demo/_dso_distilled/export/*.md` in OneDrive - the
   AI-ready text that would replace the video.

### If something goes wrong
- **Nothing happens after an upload**: look at window 3. `files are still being copied` = the
  sync is not finished (OneDrive cloud icon). Option A: is the folder set to "Always keep on
  this device"? Option B: `rclone lsd onedrive:` still works?
- **The UI shows sample (mock) data**: window 1 (`dso web`) is not running.
- **A new video takes too long**: use a shorter one or a smaller `--max-seconds`.
- **Reset the demo**: Ctrl+C in window 3, `Copy-Item out\dso-before-demo.db out\dso.db -Force`,
  delete the uploaded files from the demo folder, start window 3 again.
- **Stop everything**: Ctrl+C in each window. Nothing was deleted or moved anywhere.
