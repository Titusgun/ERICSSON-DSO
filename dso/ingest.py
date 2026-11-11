"""`dso ingest <folder>` (owner: B)

Scans a folder recursively and stores path, name, size and SHA-256 of every file.
Running it twice on the same folder updates the rows instead of duplicating them.
"""
from datetime import datetime, timezone
from pathlib import Path

from dso.common.db import connect
from dso.common.hashing import sha256_file

VIDEO_EXT = {"mp4", "mkv", "mov", "webm", "avi"}
DOCUMENT_EXT = {"pdf", "pptx", "ppt", "docx", "odp"}


def kind_from_ext(ext):
    if ext in VIDEO_EXT:
        return "video"
    elif ext in DOCUMENT_EXT:
        return "document"
    else:
        return "other"


def run(args):
    root = Path(args.folder)
    if not root.is_dir():
        print(f"Not a folder: {root}")
        return 1

    conn = connect(args.db)
    count = 0
    # sorted() makes the order the same on Windows, Mac and Linux.
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        ext = path.suffix.lower().lstrip(".")
        kind = kind_from_ext(ext)
        size = path.stat().st_size
        sha = sha256_file(path)
        # as_posix() stores "a/b/c.pdf" with forward slashes on every OS.
        conn.execute(
            """INSERT INTO files (path, name, ext, kind, size_bytes, sha256, source, owner, scanned_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET size_bytes=excluded.size_bytes,
                 sha256=excluded.sha256, source=excluded.source, owner=excluded.owner,
                 scanned_at=excluded.scanned_at""",
            (path.as_posix(), path.name, ext, kind, size, sha, args.source, args.owner,
             datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
        )
        count += 1
        print(f"{kind}\t{size}\t{sha[:12]}\t{path.as_posix()}")
    conn.commit()
    conn.close()
    print(f"Ingested {count} files from {root}")
    return 0
