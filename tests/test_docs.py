"""Tests for `dso docs` (re-runs, PPTX/DOCX -> PDF) and picture-only slides in
`dso dedup`. The main slide checks are in test_pipeline.py (tests/fixtures/docs)."""
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from dso import docs
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


# ---------- PPTX / DOCX -> PDF (issue #5) ----------

def ingest_one(tmp_path, file_name, content_from=None):
    """Puts one document into a fresh DB. Returns (db_path, out, sha12)."""
    folder = tmp_path / "in"
    folder.mkdir()
    target = folder / file_name
    if content_from is None:
        target.write_bytes(b"not a real office file " + file_name.encode("utf-8"))
    else:
        shutil.copy(content_from, target)
    db_path = tmp_path / "dso.db"
    out = tmp_path / "out"
    run(db_path, out, "ingest", str(folder))
    conn = connect(db_path)
    sha = conn.execute("SELECT sha256 FROM files").fetchone()[0]
    conn.close()
    return db_path, out, sha[:12]


def no_converters(monkeypatch):
    """Pretend this machine has no LibreOffice and no Office."""
    monkeypatch.setattr(docs, "find_soffice", lambda: None)
    monkeypatch.setattr(docs, "convert_with_office_windows", lambda source, target, ext: False)
    monkeypatch.setattr(docs, "convert_with_office_mac", lambda source, target, ext: False)


def test_existing_converted_pdf_is_used(tmp_path, monkeypatch, capsys):
    """A PDF saved as out/converted/<sha12>.pdf (e.g. a manual export) is used as is."""
    no_converters(monkeypatch)
    db_path, out, short_id = ingest_one(tmp_path, "deck.pptx")
    (out / "converted").mkdir(parents=True)
    shutil.copy(FIXTURE_DOCS / "deck_a.pdf", out / "converted" / f"{short_id}.pdf")
    run(db_path, out, "docs")
    assert "already converted" in capsys.readouterr().out
    assert count(db_path, "SELECT COUNT(*) FROM slides") == 3
    assert count(db_path, f"SELECT COUNT(*) FROM slides WHERE slide_id LIKE '{short_id}:%'") == 3


def test_no_converter_prints_manual_step(tmp_path, monkeypatch, capsys):
    no_converters(monkeypatch)
    db_path, out, short_id = ingest_one(tmp_path, "notes.docx")
    run(db_path, out, "docs")                       # skips the file, does not fail
    printed = capsys.readouterr().out
    assert "Manual step: Open" in printed
    assert "in Word" in printed                     # .docx -> Word, .pptx -> PowerPoint
    assert f"converted/{short_id}.pdf" in printed
    assert count(db_path, "SELECT COUNT(*) FROM slides") == 0


def test_quoting_for_powershell_and_applescript():
    assert docs.powershell_quote("C:\\talks\\it's.pptx") == "'C:\\talks\\it''s.pptx'"
    assert docs.applescript_quote('/talks/say "hi".pptx') == '"/talks/say \\"hi\\".pptx"'
    assert docs.app_name("docx") == "Word" and docs.app_name("pptx") == "PowerPoint"


@pytest.mark.skipif(docs.find_soffice() is None, reason="LibreOffice is not installed here")
def test_libreoffice_converts_a_real_docx(tmp_path, capsys):
    """End to end with real LibreOffice: make a .docx, then `dso docs` converts it."""
    source_dir = tmp_path / "make"
    source_dir.mkdir()
    (source_dir / "memo.txt").write_text("Latency budget for the radio network\n", encoding="utf-8")
    # Let LibreOffice turn the text file into a real .docx (our test input).
    subprocess.run([docs.find_soffice(), "-env:UserInstallation=" + (tmp_path / "profile").resolve().as_uri(),
                    "--headless", "--convert-to", "docx", "--outdir", str(source_dir),
                    str(source_dir / "memo.txt")], capture_output=True, timeout=120)
    assert (source_dir / "memo.docx").is_file(), "LibreOffice could not create the test .docx"

    db_path, out, short_id = ingest_one(tmp_path, "memo.docx", content_from=source_dir / "memo.docx")
    run(db_path, out, "docs")
    printed = capsys.readouterr().out
    assert "converter used: LibreOffice" in printed
    assert (out / "converted" / f"{short_id}.pdf").is_file()
    assert not (out / "converted" / f"tmp-{short_id}").exists()     # temp folder cleaned up
    conn = connect(db_path)
    text = conn.execute("SELECT text FROM slides").fetchone()[0]
    conn.close()
    assert "Latency budget" in text

    run(db_path, out, "docs")                       # second run: no new conversion
    assert "already converted" in capsys.readouterr().out
