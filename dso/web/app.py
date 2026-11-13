"""Local demo web server (owner: D). Runs on http://127.0.0.1:5000 - never exposed to the network.

It only READS the database and out/ - all processing happens in the CLI commands.

Two ways to show a UI:
  uv run dso web                          simple built-in page (templates/index.html)
  uv run dso web --ui ../dso-ui/dist      the Lovable UI, after `npm run build` in dso-ui
The JSON API (dso/web/api.py) is available in both cases.
"""
from pathlib import Path

from flask import Flask, abort, render_template, request, send_from_directory

from dso.common.db import connect
from dso.search import search
from dso.web.api import add_local_cors, api


def create_app(db_path, out_dir, ui_dir=None):
    app = Flask(__name__)
    app.config["DSO_DB"] = str(db_path)
    out_dir = Path(out_dir).resolve()
    app.config["DSO_OUT"] = str(out_dir)       # api.py reads reports from here
    if ui_dir is not None:
        ui_dir = Path(ui_dir).resolve()

    app.register_blueprint(api)
    # Runs after every request (API, images, video): adds the CORS header for local origins.
    app.after_request(add_local_cors)

    @app.route("/out/<path:filename>")
    def out_file(filename):
        # Serves frames, slide images and exports from the out/ folder.
        target = (out_dir / filename).resolve()
        if out_dir not in target.parents:
            abort(404)
        return send_from_directory(out_dir, filename)

    @app.route("/")
    def index():
        if ui_dir is not None:
            return send_from_directory(ui_dir, "index.html")
        return simple_page()

    @app.route("/simple")
    def simple_page():
        """The built-in page: works without the Lovable UI."""
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

    @app.route("/<path:filename>")
    def ui_file(filename):
        # Files of the built Lovable UI (JS, CSS, images). Anything else falls back
        # to index.html, because a React app handles its own page addresses.
        if ui_dir is None:
            abort(404)
        if (ui_dir / filename).is_file():
            return send_from_directory(ui_dir, filename)
        return send_from_directory(ui_dir, "index.html")

    return app


def run_web(args):
    ui_dir = getattr(args, "ui", None)
    if ui_dir is not None and not (Path(ui_dir) / "index.html").is_file():
        print(f"No index.html in {ui_dir} - run `npm run build` in dso-ui and pass its dist folder")
        return 1
    app = create_app(args.db, args.out, ui_dir)
    print(f"Open http://127.0.0.1:{args.port} (Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=args.port, debug=True)
    return 0
