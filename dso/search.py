"""`dso index` and `dso search "<query>"` (owner: C)

index:
    Copies every transcript segment and every slide text into the FTS5 table
    `search_fts` (a full-text index built into SQLite). Deletes the old index
    first, so it is safe to run twice.

search:
    Asks `search_fts` for the best matches (ORDER BY rank = most relevant first),
    then joins segments/frames/slides/files to get file name, t_sec, frame image
    and slide_id. search() returns a list of dicts in the format of DATA_CONTRACT 9.
    The web UI calls the same function, so CLI and web always show the same results.
"""
import json

from dso.common.db import connect

MAX_RESULTS = 20


def build_match_query(query):
    """Turns what the user typed into a safe FTS5 query.

    FTS5 has its own query language (AND, OR, NOT, "quotes", -, *, :), so
    input like `C++` or `what's "new"` would be a syntax error. We put every
    word in double quotes, which means "match this word literally". Words
    separated by spaces must all appear (implicit AND).
    Returns "" if the query has no words.
    """
    words = query.split()
    quoted = []
    for word in words:
        # Inside FTS5 quotes a double quote is written twice.
        quoted.append('"' + word.replace('"', '""') + '"')
    return " ".join(quoted)


def segment_result(conn, segment_id, file_row, snippet):
    """Builds one DATA_CONTRACT 9 dict for a transcript segment hit."""
    row = conn.execute(
        """SELECT segments.start_sec, frames.image_path, frames.slide_id
           FROM segments LEFT JOIN frames ON frames.id = segments.frame_id
           WHERE segments.id = ?""", (segment_id,)).fetchone()
    if row is None:
        return None     # index is older than the segments table; skip the hit
    return {
        "kind": "segment",
        "file": file_row["name"],
        "sha12": file_row["sha256"][:12],
        "t_sec": row["start_sec"],
        "frame_image": row["image_path"],
        "slide_id": row["slide_id"],
        "snippet": snippet,
    }


def slide_result(slide_id, file_row, snippet):
    """Builds one DATA_CONTRACT 9 dict for a slide hit (no time, no frame)."""
    return {
        "kind": "slide",
        "file": file_row["name"],
        "sha12": file_row["sha256"][:12],
        "t_sec": None,
        "frame_image": None,
        "slide_id": slide_id,
        "snippet": snippet,
    }


def search(conn, query):
    """Returns a list of result dicts (DATA_CONTRACT 9), best match first.

    Besides the contract fields every result has "sha12" (the file's short ID),
    which the web UI uses to open the recording (proposed contract change, #1).
    """
    match = build_match_query(query)
    if match == "":
        return []
    hits = conn.execute(
        """SELECT kind, ref_id, file_id,
                  snippet(search_fts, 0, '[', ']', '...', 12) AS snip
           FROM search_fts WHERE search_fts MATCH ?
           ORDER BY rank LIMIT ?""", (match, MAX_RESULTS)).fetchall()

    results = []
    for hit in hits:
        file_row = conn.execute(
            "SELECT name, sha256 FROM files WHERE id = ?", (hit["file_id"],)).fetchone()
        if file_row is None:
            continue
        if hit["kind"] == "segment":
            result = segment_result(conn, hit["ref_id"], file_row, hit["snip"])
        else:
            result = slide_result(hit["ref_id"], file_row, hit["snip"])
        if result is not None:
            results.append(result)
    return results


def run_index(args):
    """Rebuilds the full-text index from segments and slides."""
    conn = connect(args.db)
    conn.execute("DELETE FROM search_fts")
    conn.execute(
        """INSERT INTO search_fts(text, kind, ref_id, file_id)
           SELECT text, 'segment', id, file_id FROM segments WHERE text != ''""")
    conn.execute(
        """INSERT INTO search_fts(text, kind, ref_id, file_id)
           SELECT text, 'slide', slide_id, file_id FROM slides WHERE text != ''""")
    conn.commit()
    segment_count = conn.execute(
        "SELECT COUNT(*) FROM search_fts WHERE kind = 'segment'").fetchone()[0]
    slide_count = conn.execute(
        "SELECT COUNT(*) FROM search_fts WHERE kind = 'slide'").fetchone()[0]
    conn.close()
    print(f"[index] {segment_count} transcript segments + {slide_count} slides indexed")
    return 0


def format_time(seconds):
    """96.0 -> "01:36", 3725.0 -> "1:02:05"."""
    total = int(seconds)
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def run_search(args):
    conn = connect(args.db)
    results = search(conn, args.query)
    conn.close()
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return 0
    print(f"[search] {len(results)} results for: {args.query}")
    for r in results:
        if r["kind"] == "segment":
            where = f"{r['file']} @ {format_time(r['t_sec'])}"
        else:
            where = f"{r['file']} slide {r['slide_id']}"
        print(f"  [{r['kind']}] {where}")
        print(f"      {r['snippet']}")
    return 0
