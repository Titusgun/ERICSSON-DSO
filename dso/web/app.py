"""Local demo web UI (owner: D). Runs on http://127.0.0.1:5000 - never exposed to the network.

It only READS the database and out/ - all processing happens in the CLI commands.
Search uses the same dso.search.search() function as the CLI.
"""
from pathlib import Path

from flask import Flask, abort, render_template, request, send_from_directory

from dso.common.db import connect
from dso.search import search


def create_app(db_path, out_dir):
    app = Flask(__name__)
    out_dir = Path(out_dir).resolve()

    @app.route("/")
    def index():
        query = request.args.get("q", "").strip()
        conn = connect(db_path)
        counts = {}
        for table in ["files", "frames", "segments", "slides", "duplicates"]:
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        files = conn.execute(
            "SELECT name, kind, size_bytes, source, sha256 FROM files ORDER BY kind, name").fetchall()
        savings = conn.execute("SELECT * FROM savings").fetchall()
        results = []
        if query:
            results = search(conn, query)
        conn.close()
        return render_template("index.html", counts=counts, files=files,
                               savings=savings, query=query, results=results)

    @app.route("/out/<path:filename>")
    def out_file(filename):
        # Serves frames, slide images and exports from the out/ folder.
        target = (out_dir / filename).resolve()
        if out_dir not in target.parents:
            abort(404)
        return send_from_directory(out_dir, filename)

    return app


def run_web(args):
    app = create_app(args.db, args.out)
    print(f"Open http://127.0.0.1:{args.port} (Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=args.port, debug=True)
    return 0
