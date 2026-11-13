"""Tests for re-running `dso docs` and for picture-only slides in `dso dedup`.
The main slide checks are in test_pipeline.py (fixtures in tests/fixtures/docs)."""
from pathlib import Path

from PIL import Image, ImageDraw

from dso.cli import main
from dso.common.db import connect

FIXTURE_DOCS = Path(__file__).parent / "fixtures" / "docs"


def run(db_path, out, *command):
    assert main(["--db", str(db_path), "--out", str(out)] + list(command)) == 0


def count(db_path, sql):
    conn = connect(db_path)
    value = conn.execute(sql).fetchone()[0]
    conn.close()
    return value


def test_docs_rerun_after_link(tmp_path):
    """A frame linked to a slide must not make the next `dso docs` crash
    (frames.slide_id points into slides; SQLite checks that link)."""
    db_path = tmp_path / "dso.db"
    out = tmp_path / "out"
    run(db_path, out, "ingest", str(FIXTURE_DOCS))
    run(db_path, out, "docs")
    conn = connect(db_path)
    slide_id = conn.execute("SELECT slide_id FROM slides LIMIT 1").fetchone()[0]
    conn.execute("INSERT INTO files (id, path, name, ext, kind, size_bytes, sha256, scanned_at) "
                 "VALUES (99, 'v.mp4', 'v.mp4', 'mp4', 'video', 1, 'f', 'now')")
    conn.execute("INSERT INTO frames (file_id, t_sec, image_path, image_bytes, dhash, slide_id, match_dist) "
                 "VALUES (99, 0, 'f.webp', 1, '0000000000000000', ?, 0)", (slide_id,))
    conn.commit()
    conn.close()
    run(db_path, out, "docs")                       # used to fail: FOREIGN KEY constraint
    assert count(db_path, "SELECT COUNT(*) FROM slides") == 9    # deck_a, deck_a_rev2, deck_b
    # The link was cleared; `dso link` sets it again.
    assert count(db_path, "SELECT COUNT(*) FROM frames WHERE slide_id IS NOT NULL") == 0


def make_picture_pdf(path, shape):
    """A one-page PDF with only a picture (no text), like a photo slide."""
    image = Image.new("RGB", (640, 360), "white")
    draw = ImageDraw.Draw(image)
    if shape == "circle":
        draw.ellipse((200, 80, 440, 280), fill="black")
    else:
        draw.rectangle((40, 40, 300, 320), fill="black")
    image.save(path, "PDF")


def test_picture_only_slides_are_not_text_duplicates(tmp_path):
    """Two different picture-only slides both have the hash of "" as text hash.
    That must not make them a slide_text duplicate."""
    docs = tmp_path / "docs"
    docs.mkdir()
    make_picture_pdf(docs / "photo_a.pdf", "circle")
    make_picture_pdf(docs / "photo_b.pdf", "box")
    db_path = tmp_path / "dso.db"
    out = tmp_path / "out"
    run(db_path, out, "ingest", str(docs))
    run(db_path, out, "docs")
    run(db_path, out, "dedup")
    assert count(db_path, "SELECT COUNT(*) FROM slides") == 2
    assert count(db_path, "SELECT COUNT(*) FROM duplicates WHERE kind = 'slide_text'") == 0
