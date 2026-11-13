"""Tests for the `revisions` savings scenario (dso/savings.py)."""
from dso.cli import main
from dso.common.db import connect


def add_file(conn, file_id, name, size, sha):
    conn.execute("INSERT INTO files (id, path, name, ext, kind, size_bytes, sha256, scanned_at) "
                 "VALUES (?, ?, ?, 'pptx', 'document', ?, ?, 'now')", (file_id, "x/" + name, name, size, sha))


def savings_rows(db_path):
    conn = connect(db_path)
    rows = {}
    for row in conn.execute("SELECT scenario, bytes_before, bytes_after FROM savings"):
        rows[row["scenario"]] = (row["bytes_before"], row["bytes_after"])
    conn.close()
    return rows


def test_revisions_keep_newest_plus_old_slides(tmp_path):
    db_path = tmp_path / "dso.db"
    out = tmp_path / "out"
    conn = connect(db_path)
    old_sha = "a" * 64
    add_file(conn, 1, "deck final.pptx", 10_000, old_sha)
    add_file(conn, 2, "deck final2.pptx", 9_000, "b" * 64)
    add_file(conn, 3, "deck final (1).pptx", 10_000, old_sha)     # exact copy: dedup, not counted twice
    conn.execute("INSERT INTO duplicates (kind, a_ref, b_ref, note) VALUES ('revision', '1', '2', 'x')")
    conn.commit()
    conn.close()
    slides = out / "slides" / old_sha[:12]
    slides.mkdir(parents=True)
    (slides / "p001.webp").write_bytes(b"x" * 300)                 # the old version's slides we keep
    assert main(["--db", str(db_path), "--out", str(out), "savings"]) == 0
    rows = savings_rows(db_path)
    assert rows["revisions"] == (19_000, 9_000 + 300)


def test_no_revisions_no_row(tmp_path):
    db_path = tmp_path / "dso.db"
    conn = connect(db_path)
    add_file(conn, 1, "a.pptx", 100, "a" * 64)
    conn.commit()
    conn.close()
    assert main(["--db", str(db_path), "--out", str(tmp_path / "out"), "savings"]) == 0
    assert "revisions" not in savings_rows(db_path)
