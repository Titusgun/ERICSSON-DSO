"""`dso watch <folder>` (owner: D) - run the pipeline automatically when files change.

Instead of typing `dso all` after every upload, `watch` checks the folder every
--interval seconds. When a file was added, changed or removed (and the folder
has stopped changing, so a half-copied video is not processed), it runs the same
steps as `dso all`: ingest, video, docs, dedup, link, index, export, savings.
Every step is safe to run twice and `video` skips videos it already did, so a
run with one new PDF takes seconds, not hours.

Where the files come from:
  - any local folder, e.g. a "drop" folder, or a OneDrive / SharePoint folder
    that the OneDrive sync client keeps up to date
  - --rclone REMOTE:PATH: before every check, `rclone copy` downloads new and
    changed files from OneDrive / SharePoint into <folder> (no sync client
    needed; rclone is a separate program, logged in once with `rclone config`)
  - --upload (only with --rclone): after a run, the AI-ready exports and the
    reports are copied back to REMOTE:PATH/_dso_distilled. This WRITES to the
    cloud, so it is off unless asked for (decision in docs/DECISIONS.md).

Nothing is ever deleted - not locally, not in the cloud.
Stop it with Ctrl+C.

This file does not import the other modules (AGENTS.md 3): dso/cli.py passes
the list of step functions in.
"""
import shutil
import subprocess
import time
from pathlib import Path

# After seeing a change, wait this long and look again: if the folder is still
# changing (a big video being copied or synced), wait for the next check.
STABLE_WAIT_SEC = 3

# Name of the folder in the cloud where --upload puts the results. It is also
# excluded from downloads, so our own results are not processed as input.
DISTILLED_FOLDER = "_dso_distilled"

RCLONE_TIMEOUT_SEC = 3600       # a big recording can take a while to download

# After a failed download, wait longer before the next try: 1, 2, 4, 8 ... checks,
# but at most this long. A wrong login retried every 30 s got the IP address
# blocked by the cloud service (13 Nov demo test).
RCLONE_MAX_BACKOFF_SEC = 600

# rclone storage types that mean Microsoft OneDrive / SharePoint. In `rclone
# config` "onedrive" sits right next to "opendrive" (a different company).
MICROSOFT_TYPES = ["onedrive"]


def snapshot(folder):
    """{relative path: (size, modified time)} for every file under `folder`.
    Two snapshots differ when a file was added, removed or changed."""
    result = {}
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            stat = path.stat()
            result[path.relative_to(folder).as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return result


def describe_changes(old, new):
    """Short text like "2 new, 1 changed, 0 removed" between two snapshots."""
    added = 0
    changed = 0
    removed = 0
    for path in new:
        if path not in old:
            added += 1
        elif new[path] != old[path]:
            changed += 1
    for path in old:
        if path not in new:
            removed += 1
    return f"{added} new, {changed} changed, {removed} removed"


def rclone_download_command(remote, folder):
    """`rclone copy` from the cloud into the local folder (only new/changed files).
    Our own _dso_distilled results are excluded."""
    return ["rclone", "copy", remote, str(folder), "--exclude", f"{DISTILLED_FOLDER}/**"]


def rclone_upload_commands(out_dir, remote):
    """`rclone copy` of the exports and reports to REMOTE/_dso_distilled/."""
    target = remote.rstrip("/") + "/" + DISTILLED_FOLDER
    return [
        ["rclone", "copy", str(out_dir / "export"), target + "/export"],
        ["rclone", "copy", str(out_dir / "reports"), target + "/reports"],
    ]


def run_rclone(command):
    """Runs one rclone command; prints and returns False if it fails."""
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=RCLONE_TIMEOUT_SEC)
    except (OSError, subprocess.SubprocessError) as error:
        print(f"[watch] rclone failed: {error}")
        return False
    if result.returncode != 0:
        print(f"[watch] rclone failed: {result.stderr.strip()[:300]}")
        return False
    return True


def run_pipeline(args, steps):
    """Runs every step like `dso all`. Stops this run at the first failing step,
    but never stops the watcher: the next change tries again. Returns True if
    every step worked."""
    for step in steps:
        name = step.__module__.split(".")[-1] + "." + step.__name__
        try:
            result = step(args)
        except Exception as error:          # keep watching even if one run crashes
            print(f"[watch] step {name} crashed: {error}")
            return False
        if result != 0:
            print(f"[watch] step {name} failed with code {result}")
            return False
    return True


def remote_type(remote):
    """The rclone storage type of "name:path" (e.g. "onedrive"), from
    `rclone listremotes --long`. None if the remote does not exist."""
    name = remote.split(":", 1)[0]
    try:
        result = subprocess.run(["rclone", "listremotes", "--long"],
                                capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == name + ":":
            return parts[1]
    return None


def download_due(state, now):
    """True if it is time to try `rclone copy` again (see RCLONE_MAX_BACKOFF_SEC)."""
    return now >= state["next_try"]


def record_download(state, ok, now, interval):
    """Remembers a download result. After a failure the next try waits
    interval x 1, 2, 4, 8 ... (max RCLONE_MAX_BACKOFF_SEC); success resets it."""
    if ok:
        state["failures"] = 0
        state["next_try"] = now
        return
    state["failures"] += 1
    wait = min(interval * (2 ** (state["failures"] - 1)), RCLONE_MAX_BACKOFF_SEC)
    state["next_try"] = now + wait
    name = state["remote"].split(":", 1)[0]
    print(f"[watch] download failed {state['failures']}x - next try in {wait:.0f} s. "
          f"Check the login: rclone lsd {name}:")


def watch_cycle(args, steps, last, rclone_state=None):
    """One check: download (if --rclone), compare with the last snapshot, run
    the pipeline if something changed. Returns the snapshot to remember: the
    new one after a run, the old one if nothing was done (so we try again).
    rclone_state remembers failed downloads between checks (back-off)."""
    folder = Path(args.folder)
    remote = getattr(args, "rclone", None)
    if remote:
        if rclone_state is None:
            rclone_state = {"failures": 0, "next_try": 0.0, "remote": remote}
        now = time.time()
        if download_due(rclone_state, now):
            # Silent when it works: a line every 10 s pushed the useful output
            # (the pipeline) off the screen. "change detected" below shows
            # when new files arrived; failures print their own line.
            ok = run_rclone(rclone_download_command(remote, folder))
            record_download(rclone_state, ok, now, args.interval)

    current = snapshot(folder)
    if current == last:
        return last
    # Something changed. Is it finished (e.g. a video still being copied)?
    time.sleep(STABLE_WAIT_SEC)
    again = snapshot(folder)
    if again != current:
        print("[watch] files are still being copied - waiting for the next check")
        return last

    if last is None:
        print(f"[watch] first run: {len(current)} files in {folder.as_posix()}")
    else:
        print(f"[watch] change detected: {describe_changes(last, current)}")
    started = time.time()
    ok = run_pipeline(args, steps)
    print(f"[watch] pipeline {'done' if ok else 'stopped'} in {time.time() - started:.0f} s")

    if ok and remote and getattr(args, "upload", False):
        print(f"[watch] uploading results to {remote.rstrip('/')}/{DISTILLED_FOLDER} ...")
        for command in rclone_upload_commands(Path(args.out), remote):
            run_rclone(command)
    if not ok:
        return last                         # try again at the next check
    return current


def run_watch(args, steps):
    """`dso watch`: repeat watch_cycle() every --interval seconds until Ctrl+C."""
    folder = Path(args.folder)
    remote = getattr(args, "rclone", None)
    if remote and shutil.which("rclone") is None:
        print("[watch] --rclone needs the rclone program: https://rclone.org/install/ "
              "(Arch: sudo pacman -S rclone), then `rclone config` once to log in")
        return 1
    if getattr(args, "upload", False) and not remote:
        print("[watch] --upload only works together with --rclone REMOTE:PATH")
        return 1
    if remote:
        kind = remote_type(remote)
        name = remote.split(":", 1)[0]
        if kind is None:
            print(f"[watch] rclone has no remote called '{name}'. Create it with `rclone config` "
                  "(storage type: onedrive).")
            return 1
        if kind not in MICROSOFT_TYPES:
            print(f"[watch] the rclone remote '{name}' is of type '{kind}', not Microsoft OneDrive / "
                  "SharePoint ('onedrive').")
            if kind == "opendrive":
                print("[watch] 'opendrive' is OpenDrive, a different company - it sits next to "
                      "'onedrive' in the rclone list.")
            print(f"[watch] Fix: rclone config delete {name}, then rclone config -> n -> name "
                  f"{name} -> storage: onedrive.")
            return 1
    folder.mkdir(parents=True, exist_ok=True)
    print(f"[watch] watching {folder.as_posix()} every {args.interval} s - Ctrl+C to stop")
    if remote:
        print(f"[watch] cloud source: {remote}, checked every {args.interval} s"
              + (" (results uploaded back)" if getattr(args, "upload", False) else "")
              + " - quiet until new files arrive")

    last = None
    rclone_state = {"failures": 0, "next_try": 0.0, "remote": remote or ""}
    try:
        while True:
            last = watch_cycle(args, steps, last, rclone_state)
            if getattr(args, "once", False):
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[watch] stopped")
    return 0
