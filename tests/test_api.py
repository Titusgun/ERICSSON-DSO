"""Tests for the JSON API (dso/web/api.py). Rows are inserted by hand, so these
tests do not depend on the video/docs/dedup modules being finished."""
from pathlib import Path

import pytest

from dso.common.db import connect
from dso.web.app import create_app

FIXTURES = Path(__file__).parent / "fixtures"
SHA_VIDEO = "3fa9c0d1e2b4" + "0" * 52
SHA_DECK = "9b1c22aa01ff" + "0" * 52


@pytest.fixture()
def client(tmp_path):
    db_path = tmp_path / "dso.db"
    conn = connect(db_path)
    video_path = (FIXTURES / "video" / "talk.mp4").as_posix()
    conn.execute("INSERT INTO files (id, path, name, ext, kind, size_bytes, sha256, source, scanned_at) "
                 "VALUES (1, ?, 'talk.mp4', 'mp4', 'video', 1000, ?, 'play', 'now')", (video_path, SHA_VIDEO))
    conn.execute("INSERT INTO files (id, path, name, ext, kind, size_bytes, sha256, source, scanned_at) "
                 "VALUES (2, 'x/deck_a.pdf', 'deck_a.pdf', 'pdf', 'document', 41000, ?, 'sharepoint', 'now')", (SHA_DECK,))
    conn.execute("INSERT INTO files (id, path, name, ext, kind, size_bytes, sha256, source, scanned_at) "
                 "VALUES (3, 'x/deck_a (1).pdf', 'deck_a (1).pdf', 'pdf', 'document', 41000, ?, 'sharepoint', 'now')", (SHA_DECK,))
    conn.execute("INSERT INTO slides VALUES ('9b1c22aa01ff:2:9f2c01ab', 2, 2, 'Latency', 'h', '00ff00ff00ff00ff', 'slides/9b1c22aa01ff/p002.webp')")
    conn.execute("INSERT INTO frames (id, file_id, t_sec, image_path, image_bytes, dhash, slide_id, match_dist) "
                 "VALUES (1, 1, 8.0, 'frames/3fa9c0d1e2b4/f0002.webp', 500, '00ff00ff00ff00ff', '9b1c22aa01ff:2:9f2c01ab', 1)")
    conn.execute("INSERT INTO segments (id, file_id, start_sec, end_sec, text, frame_id) "
                 "VALUES (1, 1, 9.0, 12.5, 'As you see here, the latency drops', 1)")
    conn.execute("INSERT INTO duplicates (kind, a_ref, b_ref, note) VALUES ('file_exact', '3', '2', 'name hint: (1)')")
    conn.execute("INSERT INTO duplicates (kind, a_ref, b_ref) VALUES ('slide_text', '9b1c22aa01ff:2:9f2c01ab', '9b1c22aa01ff:2:9f2c01ab')")
    conn.execute("INSERT INTO savings VALUES ('video_distill', 1000, 20)")
    conn.commit()
    conn.close()
    app = create_app(db_path, tmp_path / "out")
    return app.test_client()


def test_stats(client):
    data = client.get("/api/stats").get_json()
    assert data == {"files": 3, "frames": 1, "segments": 1, "slides": 1, "duplicates": 2}


def test_savings_and_files(client):
    assert client.get("/api/savings").get_json() == [
        {"scenario": "video_distill", "bytes_before": 1000, "bytes_after": 20}]
    files = client.get("/api/files").get_json()
    assert len(files) == 3
    assert set(files[0].keys()) == {"name", "kind", "size_bytes", "source", "owner", "sha256"}


def test_duplicates_are_readable(client):
    data = client.get("/api/duplicates").get_json()
    assert data[0]["a"] == "deck_a (1).pdf" and data[0]["b"] == "deck_a.pdf"
    assert data[1]["a"] == "deck_a.pdf slide 2"


def test_recording(client):
    data = client.get("/api/recording/3fa9c0d1e2b4").get_json()
    assert data["source"]["file_name"] == "talk.mp4"
    assert data["duration_sec"] == pytest.approx(24, abs=1)
    assert data["frames"][0]["image"] == "frames/3fa9c0d1e2b4/f0002.webp"
    assert data["segments"][0]["frame_id"] == 1
    assert data["segments"][0]["slide_id"] == "9b1c22aa01ff:2:9f2c01ab"
    assert client.get("/api/recording/ffffffffffff").status_code == 404


def test_search_empty_query(client):
    assert client.get("/api/search").get_json() == []
    assert client.get("/api/search?q=latency").status_code == 200


def test_video_supports_seeking(client):
    response = client.get("/video/talk.mp4", headers={"Range": "bytes=0-99"})
    assert response.status_code == 206          # 206 = partial content, needed for "Play at"
    assert client.get("/video/nope.mp4").status_code == 404
    assert client.get("/video/deck_a.pdf").status_code == 404   # only videos are served


def test_cors_only_for_local_origins(client):
    local = client.get("/api/stats", headers={"Origin": "http://localhost:8080"})
    assert local.headers.get("Access-Control-Allow-Origin") == "http://localhost:8080"
    remote = client.get("/api/stats", headers={"Origin": "https://example.com"})
    assert "Access-Control-Allow-Origin" not in remote.headers


def test_serves_built_ui(tmp_path):
    ui = tmp_path / "dist"
    (ui / "assets").mkdir(parents=True)
    (ui / "index.html").write_text("<html>dso-ui</html>", encoding="utf-8")
    (ui / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    client = create_app(tmp_path / "dso.db", tmp_path / "out", ui).test_client()
    assert b"dso-ui" in client.get("/").data
    assert b"console.log" in client.get("/assets/app.js").data
    assert b"dso-ui" in client.get("/recording/abc").data      # React route -> index.html
    assert client.get("/api/stats").get_json()["files"] == 0   # API still wins over the fallback
    assert b"Search" in client.get("/simple").data             # built-in page still reachable


# ---------- /api/offboarding ----------

def test_offboarding_before_any_run(client):
    data = client.get("/api/offboarding").get_json()
    assert data["total_files"] == 0 and data["files"] == [] and data["dry_run"] is True


def test_offboarding_after_dso_offboard(tmp_path):
    """Real flow on the jane fixture: ingest, offboard --simulated, then the API."""
    from dso.cli import main
    db_path = tmp_path / "dso.db"
    out = tmp_path / "out"
    base = ["--db", str(db_path), "--out", str(out)]
    jane = FIXTURES / "offboarding" / "jane"
    assert main(base + ["ingest", str(FIXTURES / "docs"), "--source", "sharepoint"]) == 0
    assert main(base + ["ingest", str(jane), "--source", "offboarding", "--owner", "jane"]) == 0
    assert main(base + ["offboard", str(jane), "--simulated"]) == 0

    client = create_app(db_path, out).test_client()
    data = client.get("/api/offboarding").get_json()
    copy_size = (jane / "deck_b - Copy.pdf").stat().st_size
    notes_size = (jane / "my_notes.pdf").stat().st_size
    assert data["dry_run"] is True and data["simulated"] is True
    assert data["owners"] == ["jane"]
    assert data["total_files"] == 2 and data["safe_files"] == 1 and data["keep_files"] == 1
    assert data["total_bytes"] == copy_size + notes_size
    assert data["freeable_bytes"] == copy_size
    assert data["percent_freed"] == round(100.0 * copy_size / (copy_size + notes_size), 1)

    by_name = {}
    for f in data["files"]:
        by_name[f["name"]] = f
    assert set(by_name["deck_b - Copy.pdf"].keys()) == {
        "name", "path", "owner", "size_bytes", "sha256", "verdict", "copy_at"}
    assert by_name["deck_b - Copy.pdf"]["verdict"] == "safe to delete"
    assert by_name["deck_b - Copy.pdf"]["copy_at"].endswith("docs/deck_b.pdf")
    assert by_name["deck_b - Copy.pdf"]["owner"] == "jane"
    assert by_name["my_notes.pdf"]["verdict"] == "keep"
    assert by_name["my_notes.pdf"]["copy_at"] is None
