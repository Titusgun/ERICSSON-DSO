"""`dso index` and `dso search "<query>"` (owner: C)

index:
    DELETE FROM search_fts;
    INSERT INTO search_fts(text, kind, ref_id, file_id) SELECT text, 'segment', id, file_id FROM segments;
    INSERT INTO search_fts(text, kind, ref_id, file_id) SELECT text, 'slide', slide_id, file_id FROM slides;

search:
    SELECT kind, ref_id, file_id, snippet(search_fts, 0, '[', ']', '...', 12) AS snip
    FROM search_fts WHERE search_fts MATCH ? ORDER BY rank LIMIT 20;
  then join segments/frames/slides/files to get file name, t_sec, frame image, slide_id.
  search() returns a list of dicts in the format of DATA_CONTRACT 9 - the web UI
  calls the same function, so CLI and web always show the same results.
"""
import json

from dso.common.db import connect


def search(conn, query):
    """Returns a list of result dicts (DATA_CONTRACT 9). TODO owner C."""
    return []


def run_index(args):
    print("[index] TODO (owner C): fill FTS5 index")
    return 0


def run_search(args):
    conn = connect(args.db)
    results = search(conn, args.query)
    conn.close()
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        print(f"[search] {len(results)} results for: {args.query}")
        for r in results:
            print(r)
    return 0
