"""JSON API for the dso-ui frontend (owner: D). Read-only.

    GET /api/stats                  {"files": 7, "frames": 3, ...}
    GET /api/savings                [{"scenario", "bytes_before", "bytes_after"}]
    GET /api/files                  [{"name", "kind", "size_bytes", "source", "owner", "sha256"}]
    GET /api/duplicates             [{"kind", "a", "b", "note", "distance"}]
    GET /api/search?q=latency       DATA_CONTRACT 9 (+ "sha12" of the file)
    GET /api/recording/<sha12>      DATA_CONTRACT 8 shape, built live from the DB
    GET /video/<file name>          the original video (supports seeking, for "Play at mm:ss")

A Blueprint is a group of routes that app.py attaches to the Flask app.
"""
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, request, send_file

from dso.common.db import connect
from dso.search import search

api = Blueprint("api", __name__)


def open_db():
    return connect(current_app.config["DSO_DB"])


def rows_to_dicts(rows):
    result = []
    for row in rows:
        result.append(dict(row))
    return result


def add_local_cors(response):
    """CORS: let the UI dev server (http://localhost:8080 etc.) call this server.

    Only local origins are allowed, so a random website open in the same browser
    cannot read our (confidential) data from 127.0.0.1.
    """
    origin = request.headers.get("Origin", "")
    if origin.startswith("http://localhost:") or origin.startswith("http://127.0.0.1:"):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    return response


@api.route("/api/stats")
def stats():
    conn = open_db()
    counts = {}
    for table in ["files", "frames", "segments", "slides", "duplicates"]:
        counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.close()
    return jsonify(counts)


@api.route("/api/savings")
def savings():
    conn = open_db()
    rows = conn.execute("SELECT scenario, bytes_before, bytes_after FROM savings").fetchall()
    conn.close()
    return jsonify(rows_to_dicts(rows))


@api.route("/api/files")
def files():
    conn = open_db()
    rows = conn.execute(
        "SELECT name, kind, size_bytes, source, owner, sha256 FROM files ORDER BY name").fetchall()
    conn.close()
    return jsonify(rows_to_dicts(rows))


def describe_ref(conn, kind, ref):
    """Turns a duplicates.a_ref / b_ref into text a human can read.

    File-level kinds store files.id; slide-level kinds store slides.slide_id.
    """
    if kind == "slide_text" or kind == "slide_visual":
        row = conn.execute(
            """SELECT files.name AS name, slides.page_no AS page_no
               FROM slides JOIN files ON files.id = slides.file_id
               WHERE slides.slide_id = ?""", (ref,)).fetchone()
        if row is not None:
            return f"{row['name']} slide {row['page_no']}"
    else:
        row = conn.execute("SELECT name FROM files WHERE id = ?", (ref,)).fetchone()
        if row is not None:
            return row["name"]
    return ref  # unknown reference: show it as it is


@api.route("/api/duplicates")
def duplicates():
    conn = open_db()
    rows = conn.execute("SELECT kind, a_ref, b_ref, distance, note FROM duplicates ORDER BY kind, id").fetchall()
    result = []
    for row in rows:
        result.append({
            "kind": row["kind"],
            "a": describe_ref(conn, row["kind"], row["a_ref"]),   # the copy / older revision
            "b": describe_ref(conn, row["kind"], row["b_ref"]),   # the canonical one we keep
            "distance": row["distance"],
            "note": row["note"],
        })
    conn.close()
    return jsonify(result)


@api.route("/api/search")
def search_route():
    query = request.args.get("q", "").strip()
    if query == "":
        return jsonify([])
    conn = open_db()
    results = search(conn, query)
    # The UI opens the Recording view by sha12, so add it to every result.
    for r in results:
        if "sha12" not in r:
            row = conn.execute("SELECT sha256 FROM files WHERE name = ?", (r.get("file"),)).fetchone()
            if row is not None:
                r["sha12"] = row["sha256"][:12]
            else:
                r["sha12"] = None
    conn.close()
    return jsonify(results)


def video_duration(path):
    """Length of a video in seconds, or None if it cannot be read."""
    try:
        import cv2
        cap = cv2.VideoCapture(str(path))
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        cap.release()
        if fps > 0 and frame_count > 0:
            return round(frame_count / fps, 1)
    except Exception:
        pass
    return None


@api.route("/api/recording/<sha12>")
def recording(sha12):
    conn = open_db()
    file_row = conn.execute(
        "SELECT * FROM files WHERE kind = 'video' AND sha256 LIKE ? ORDER BY id LIMIT 1",
        (sha12 + "%",)).fetchone()
    if file_row is None:
        conn.close()
        abort(404)

    frames = rows_to_dicts(conn.execute(
        """SELECT id, t_sec, image_path AS image, dhash, slide_id, match_dist
           FROM frames WHERE file_id = ? ORDER BY t_sec""", (file_row["id"],)).fetchall())
    segments = rows_to_dicts(conn.execute(
        """SELECT segments.id, start_sec, end_sec, speaker, text, frame_id,
                  frames.slide_id AS slide_id
           FROM segments LEFT JOIN frames ON frames.id = segments.frame_id
           WHERE segments.file_id = ? ORDER BY start_sec""", (file_row["id"],)).fetchall())
    conn.close()

    duration = video_duration(file_row["path"])
    if duration is None:
        # Original video not on this machine: use the last timestamp we know.
        duration = 0.0
        for f in frames:
            duration = max(duration, f["t_sec"])
        for s in segments:
            duration = max(duration, s["end_sec"])

    return jsonify({
        "schema_version": 1,
        "source": {
            "file_name": file_row["name"], "path": file_row["path"], "sha256": file_row["sha256"],
            "size_bytes": file_row["size_bytes"], "kind": file_row["kind"], "system": file_row["source"],
            "doc_number": file_row["doc_number"], "revision": file_row["revision"],
            "author": file_row["author"], "date": file_row["doc_date"],
        },
        "duration_sec": duration,
        "frames": frames,
        "segments": segments,
    })


@api.route("/video/<path:name>")
def video(name):
    """Serves an ORIGINAL video, but only one that is registered in the files table."""
    conn = open_db()
    row = conn.execute(
        "SELECT path FROM files WHERE kind = 'video' AND name = ? ORDER BY id LIMIT 1", (name,)).fetchone()
    conn.close()
    if row is None:
        abort(404)
    path = Path(row["path"]).resolve()
    if not path.is_file():
        abort(404)
    # conditional=True enables HTTP range requests, which the browser needs to jump to #t=96.
    return send_file(path, conditional=True)
