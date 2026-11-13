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


def watch_cycle(args, steps, last):
    """One check: download (if --rclone), compare with the last snapshot, run
    the pipeline if something changed. Returns the snapshot to remember: the
    new one after a run, the old one if nothing was done (so we try again)."""
    folder = Path(args.folder)
    remote = getattr(args, "rclone", None)
    if remote:
        print(f"[watch] downloading new files from {remote} ...")
        run_rclone(rclone_download_command(remote, folder))

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
    folder.mkdir(parents=True, exist_ok=True)
    print(f"[watch] watching {folder.as_posix()} every {args.interval} s - Ctrl+C to stop")
    if remote:
        print(f"[watch] cloud source: {remote}" + (" (results uploaded back)" if getattr(args, "upload", False) else ""))

    last = None
    try:
        while True:
            last = watch_cycle(args, steps, last)
            if getattr(args, "once", False):
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[watch] stopped")
    return 0
