"""Real data inventory (issue #2, owner D).

Prints a summary of a folder: file count and size per extension, total size,
and the total length of all videos. By default NO file names are printed, so the
output is safe to paste in the team chat. Nothing is written or changed.

    uv run python scripts/inventory.py              # summarises data/
    uv run python scripts/inventory.py data --names # also lists each video with its length
"""
import sys
from pathlib import Path

import cv2

VIDEO_EXTS = ["mp4", "mkv", "mov", "webm", "avi"]   # same list as DATA_CONTRACT 2


def human_size(n):
    """Bytes as a short readable string, e.g. 1.4 GB."""
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f} {unit}"
        n = n / 1024
    return f"{n:.1f} TB"


def human_time(seconds):
    """Seconds as h:mm:ss."""
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def video_seconds(path):
    """Length of a video in seconds, or None if OpenCV cannot read it."""
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    cap.release()
    if fps > 0 and frame_count > 0:
        return frame_count / fps
    return None


def main():
    folder = Path("data")
    show_names = False
    for arg in sys.argv[1:]:
        if arg == "--names":
            show_names = True
        else:
            folder = Path(arg)
    if not folder.is_dir():
        print(f"Folder not found: {folder}")
        return 1

    per_ext = {}        # "pdf" -> {"count": 3, "bytes": 12345}
    total_bytes = 0
    total_files = 0
    videos = []         # (name, seconds)
    unreadable_videos = 0

    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        ext = path.suffix.lower().lstrip(".")
        if ext == "":
            ext = "(none)"
        size = path.stat().st_size
        if ext not in per_ext:
            per_ext[ext] = {"count": 0, "bytes": 0}
        per_ext[ext]["count"] += 1
        per_ext[ext]["bytes"] += size
        total_bytes += size
        total_files += 1

        if ext in VIDEO_EXTS:
            print(f"  reading video length {len(videos) + unreadable_videos + 1}...", flush=True)
            seconds = video_seconds(path)
            if seconds is None:
                unreadable_videos += 1
            else:
                videos.append((path.relative_to(folder).as_posix(), seconds))

    print()
    print(f"Inventory of {folder.as_posix()}/")
    print(f"{'type':<8}{'files':>7}{'size':>12}")
    for ext in sorted(per_ext, key=lambda e: per_ext[e]["bytes"], reverse=True):
        print(f"{ext:<8}{per_ext[ext]['count']:>7}{human_size(per_ext[ext]['bytes']):>12}")
    print(f"{'TOTAL':<8}{total_files:>7}{human_size(total_bytes):>12}")

    total_video = 0
    longest = 0
    for name, seconds in videos:
        total_video += seconds
        longest = max(longest, seconds)
    print()
    print(f"Videos: {len(videos)} readable, total {human_time(total_video)}, longest {human_time(longest)}")
    if unreadable_videos > 0:
        print(f"  {unreadable_videos} video(s) OpenCV could not read - check them")

    office = 0
    for ext in ["pptx", "ppt", "docx", "odp"]:
        if ext in per_ext:
            office += per_ext[ext]["count"]
    if office > 0:
        print(f"Office files needing LibreOffice -> PDF: {office}")

    if show_names:
        print()
        for name, seconds in videos:
            print(f"  {human_time(seconds)}  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
