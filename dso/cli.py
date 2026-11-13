"""`dso <command>` - routes each command to its owner's module (owner: D).

You should NOT need to edit this file to implement your module: every command
already calls `run(args)` in the owner's file.
"""
import argparse
import sys

from dso import cleanup, dedup, docs, export, ingest, link, offboard, savings, search, video, watch


def build_parser():
    parser = argparse.ArgumentParser(prog="dso", description="Data Storage Optimization")
    parser.add_argument("--db", default="out/dso.db", help="SQLite database (default out/dso.db)")
    parser.add_argument("--out", default="out", help="output folder (default out)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="scan a folder, hash every file")
    p.add_argument("folder")
    p.add_argument("--source", default="local", help="sharepoint | onedrive | play | local | offboarding")
    p.add_argument("--owner", default=None, help="employee who owns these files")

    p = sub.add_parser("video", help="key frames + transcript for every video")
    p.add_argument("--model", default="small",
                   help="whisper model (default small, multilingual): tiny, base, small, base.en, ...")
    p.add_argument("--max-seconds", type=float, default=None,
                   help="only process the first N seconds of each video (for fast testing)")
    p.add_argument("--force", action="store_true", help="redo videos that were already processed")

    sub.add_parser("docs", help="PDF -> slides with text hash + dHash")
    sub.add_parser("dedup", help="find exact, slide-level and revision duplicates")
    sub.add_parser("link", help="frames <-> slides, transcript -> frames")
    sub.add_parser("index", help="build the full-text search index")

    p = sub.add_parser("search", help="search text, get frame + timestamp")
    p.add_argument("query")
    p.add_argument("--json", action="store_true")

    sub.add_parser("export", help="AI-ready JSON + Markdown per recording/document")

    p = sub.add_parser("offboard", help="DRY-RUN report for a departing employee's folder")
    p.add_argument("folder")
    p.add_argument("--simulated", action="store_true",
                   help="mark the report as a simulated scenario (not a real leaver)")

    sub.add_parser("savings", help="storage before/after report")

    p = sub.add_parser("all", help="ingest, video, docs, dedup, link, index, export, savings")
    p.add_argument("folder")
    p.add_argument("--source", default="local")
    p.add_argument("--owner", default=None)
    p.add_argument("--model", default="small", help="whisper model (default small, multilingual)")
    p.add_argument("--max-seconds", type=float, default=None,
                   help="only process the first N seconds of each video (for fast testing)")
    p.add_argument("--force", action="store_true", help="redo videos that were already processed")

    p = sub.add_parser("watch", help="run `all` automatically whenever files in the folder change")
    p.add_argument("folder", help="folder to watch, e.g. data/inbox or a OneDrive sync folder")
    p.add_argument("--interval", type=float, default=30, help="seconds between checks (default 30)")
    p.add_argument("--source", default="local", help="sharepoint | onedrive | play | local | offboarding")
    p.add_argument("--owner", default=None)
    p.add_argument("--model", default="small", help="whisper model (default small, multilingual)")
    p.add_argument("--max-seconds", type=float, default=None,
                   help="only process the first N seconds of each video (for a fast live demo)")
    p.add_argument("--force", action="store_true", help="redo videos that were already processed")
    p.add_argument("--rclone", default=None, metavar="REMOTE:PATH",
                   help="download new files from OneDrive/SharePoint with rclone before each check")
    p.add_argument("--upload", action="store_true",
                   help="with --rclone: copy exports + reports back to REMOTE:PATH/_dso_distilled")
    p.add_argument("--delete-originals", action="store_true",
                   help="with --rclone and --upload: move cloud originals that are kept in reduced form "
                        "(distilled videos, exact copies, older revisions) to the cloud recycle bin")
    p.add_argument("--once", action="store_true", help="check once and stop (for tests)")

    p = sub.add_parser("cleanup", help="free cloud storage: plan (dry run) or --apply")
    p.add_argument("folder", help="the folder `dso watch --rclone` downloads into, e.g. data/cloud")
    p.add_argument("--rclone", required=True, metavar="REMOTE:PATH", help="the cloud folder, e.g. \"onedrive:DSO demo\"")
    p.add_argument("--apply", action="store_true",
                   help="really move the files to the cloud recycle bin (default: only show the plan)")

    p = sub.add_parser("web", help="start the local demo web UI")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--ui", default=None, help="folder with the built Lovable UI, e.g. ../dso-ui/dist")

    p = sub.add_parser("dhash", help="debug: dHash of images and their distance")
    p.add_argument("images", nargs="+")
    return parser


def pipeline_steps():
    """The steps of `dso all` and `dso watch`, in order."""
    return [ingest.run, video.run, docs.run, dedup.run, link.run,
            search.run_index, export.run, savings.run]


def run_dhash(args):
    from dso.common.hashing import dhash_file, hamming
    hashes = []
    for path in args.images:
        h = dhash_file(path)
        hashes.append(h)
        print(h, path)
    if len(hashes) >= 2:
        print("distance:", hamming(hashes[0], hashes[1]))
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    command = args.command

    if command == "ingest":
        return ingest.run(args)
    elif command == "video":
        return video.run(args)
    elif command == "docs":
        return docs.run(args)
    elif command == "dedup":
        return dedup.run(args)
    elif command == "link":
        return link.run(args)
    elif command == "index":
        return search.run_index(args)
    elif command == "search":
        return search.run_search(args)
    elif command == "export":
        return export.run(args)
    elif command == "offboard":
        return offboard.run(args)
    elif command == "savings":
        return savings.run(args)
    elif command == "dhash":
        return run_dhash(args)
    elif command == "web":
        from dso.web.app import run_web
        return run_web(args)
    elif command == "watch":
        return watch.run_watch(args, pipeline_steps(), cleanup.cleanup)
    elif command == "cleanup":
        return cleanup.run(args)
    elif command == "all":
        for step in pipeline_steps():
            result = step(args)
            if result != 0:
                print(f"Step {step.__module__} failed with code {result}", file=sys.stderr)
                return result
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
