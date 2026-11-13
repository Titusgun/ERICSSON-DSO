"""`dso <command>` - routes each command to its owner's module (owner: D).

You should NOT need to edit this file to implement your module: every command
already calls `run(args)` in the owner's file.
"""
import argparse
import sys

from dso import dedup, docs, export, ingest, link, offboard, savings, search, video


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

    p = sub.add_parser("web", help="start the local demo web UI")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--ui", default=None, help="folder with the built Lovable UI, e.g. ../dso-ui/dist")

    p = sub.add_parser("dhash", help="debug: dHash of images and their distance")
    p.add_argument("images", nargs="+")
    return parser


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
    elif command == "all":
        steps = [ingest.run, video.run, docs.run, dedup.run, link.run,
                 search.run_index, export.run, savings.run]
        for step in steps:
            result = step(args)
            if result != 0:
                print(f"Step {step.__module__} failed with code {result}", file=sys.stderr)
                return result
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
