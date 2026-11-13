"""Tests for revisions / file_content in `dso dedup` (#14) and the document
metadata `dso docs` reads to know which revision is newest."""
import hashlib
import shutil
import zipfile
from pathlib import Path

from dso import dedup, docs
from dso.cli import main
from dso.common.db import connect

FIXTURES = Path(__file__).parent / "fixtures"

# Slide texts for the tests: each is clearly different from the others.
INTRO = "Welcome to the SD-WAN orchestration overview"
AGENDA = "Agenda market drivers architecture pricing next steps"
LATENCY_4 = "Latency budget per site is 4 ms on average"
LATENCY_3 = "Latency budget per site is 3 ms on average"
PRICING = "Pricing model per site licence and support tiers"
SUMMARY = "Summary questions and contact details for the team"
EXTRA = "Customer reference case from a large retail network"


def doc(doc_id, name, pages, date=None, revision=None, doc_number=None, sha=None):
    """A document dict like dedup.load_documents() returns, from slide texts.
    The dHash is made from the text, so only slides with the same text look the same."""
    hashes = []
    slides = []
    for text in pages:
        hashes.append(hashlib.sha256(text.encode("utf-8")).hexdigest())
        slides.append({"words": dedup.slide_words(text),
                       "dhash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]})
    return {"id": doc_id, "name": name, "sha256": sha or f"sha{doc_id}", "doc_number": doc_number,
            "revision": revision, "doc_date": date, "pages": hashes, "slides": slides}


def shuffled(text):
    """Same words, other order: what LibreOffice did with the real decks."""
    words = text.split()
    return " ".join(words[1:] + words[:1])


# ---------- which slides changed ----------

def test_slide_changes_changed_and_added():
    older = doc(1, "a.pptx", [INTRO, AGENDA, LATENCY_4, PRICING])
    newer = doc(2, "b.pptx", [INTRO, AGENDA, LATENCY_3, PRICING, EXTRA])
    # LATENCY 4 -> 3 is a number change: "minor", not "changed text".
    assert dedup.slide_changes(older, newer) == (3, [3], [], [5], [])


def test_inserted_slide_does_not_shift_everything():
    # A new slide 2: slides 3 and 4 of the new deck are the old 2 and 3, unchanged.
    older = doc(1, "a.pptx", [INTRO, AGENDA, PRICING])
    newer = doc(2, "b.pptx", [INTRO, EXTRA, AGENDA, PRICING])
    assert dedup.slide_changes(older, newer) == (3, [], [], [2], [])


def test_removed_slide():
    older = doc(1, "a.pptx", [INTRO, AGENDA, PRICING])
    newer = doc(2, "b.pptx", [INTRO, PRICING])
    assert dedup.slide_changes(older, newer) == (2, [], [], [], [2])


def test_real_pattern_text_order_differs_and_slides_removed():
    """Like the real "final" / "final2": the same slides, but every slide's text
    comes out in another order, and the newer deck dropped some slides."""
    older = doc(1, "deck final.pptx", [INTRO, AGENDA, LATENCY_4, EXTRA, PRICING, SUMMARY])
    newer = doc(2, "deck final2.pptx", [shuffled(INTRO), shuffled(AGENDA), shuffled(LATENCY_3),
                                        shuffled(PRICING), shuffled(SUMMARY)])
    kind, old, new, note = dedup.compare_documents(older, newer)
    assert kind == "revision" and new["name"] == "deck final2.pptx"
    assert "4 of 5 slides identical" in note
    assert "minor (numbers/footer only): 3" in note and "changed text: none" in note
    assert "removed: 4" in note



def test_real_word_change_is_changed_text():
    older = doc(1, "a.pptx", [INTRO, PRICING])
    newer = doc(2, "b.pptx", [INTRO, "Pricing model per site licence and premium support tiers"])
    assert dedup.slide_changes(older, newer) == (1, [], [2], [], [])


def test_minor_differences_are_not_real_word_changes():
    # Like the real decks: slide number, "final" -> "final2", single letters.
    assert not dedup.is_real_word_change({"5", "final2", "x"}, {"4", "final", "y"})
    assert dedup.is_real_word_change({"premium"}, {"pricing", "model"})

# ---------- which one is newest (kept) ----------

def test_newest_by_date_beats_file_name():
    final = doc(1, "deck final.pptx", [], date="2026-11-02T10:00:00")
    final2 = doc(2, "deck final2.pptx", [], date="2026-11-01T10:00:00")
    older, newer, reason = dedup.older_and_newer(final, final2)
    assert newer["name"] == "deck final.pptx" and reason == "date"


def test_newest_by_file_name_when_no_date():
    final = doc(1, "deck final.pptx", [])
    final2 = doc(2, "deck final2.pptx", [])
    assert dedup.older_and_newer(final2, final)[1]["name"] == "deck final2.pptx"
    rev9 = doc(3, "deck rev9.pdf", [])
    rev10 = doc(4, "deck rev10.pdf", [])
    assert dedup.older_and_newer(rev10, rev9)[1]["name"] == "deck rev10.pdf"   # 10 > 9


def test_revision_field_wins():
    a = doc(1, "z.pdf", [], date="2030-01-01T00:00:00", revision="B")
    b = doc(2, "a.pdf", [], date="2020-01-01T00:00:00", revision="C")
    older, newer, reason = dedup.older_and_newer(a, b)
    assert newer["id"] == 2 and reason == "revision"


# ---------- revision vs file_content vs unrelated ----------

def test_same_slides_different_bytes_is_file_content():
    a = doc(1, "deck.pptx", [INTRO, AGENDA])
    b = doc(2, "deck saved again.pptx", [INTRO, AGENDA])
    kind, older, newer, note = dedup.compare_documents(a, b)
    assert kind == "file_content" and "all 2 slides identical" in note


def test_decks_that_only_share_slides_are_not_revisions():
    # Like deck_a vs deck_b: 2 of 3 slides shared, the third is different.
    a = doc(1, "a.pdf", [INTRO, AGENDA, PRICING])
    b = doc(2, "b.pdf", [INTRO, AGENDA, EXTRA])
    assert dedup.compare_documents(a, b) is None


def test_same_doc_number_is_a_revision_even_if_very_different():
    a = doc(1, "a.pdf", [INTRO], doc_number="1/1551-ABC 123")
    b = doc(2, "b.pdf", [EXTRA], doc_number="1/1551-ABC 123")
    assert dedup.compare_documents(a, b)[0] == "revision"


def test_fixture_revision(tmp_path):
    """On the fixtures: only deck_a -> deck_a_rev2 is a revision (slide 2 changed);
    deck_b shares 2 of 3 slides with deck_a but is a different deck."""
    db_path = tmp_path / "dso.db"
    out = tmp_path / "out"
    base = ["--db", str(db_path), "--out", str(out)]
    assert main(base + ["ingest", str(FIXTURES / "docs")]) == 0
    assert main(base + ["docs"]) == 0
    assert main(base + ["dedup"]) == 0
    conn = connect(db_path)
    rows = conn.execute(
        """SELECT a.name AS older, b.name AS newer, d.note FROM duplicates d
           JOIN files a ON a.id = CAST(d.a_ref AS INTEGER)
           JOIN files b ON b.id = CAST(d.b_ref AS INTEGER)
           WHERE d.kind = 'revision'""").fetchall()
    visual = conn.execute("SELECT distance FROM duplicates WHERE kind = 'slide_visual'").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]["older"] == "deck_a.pdf" and rows[0]["newer"] == "deck_a_rev2.pdf"
    assert "minor (numbers/footer only): 2" in rows[0]["note"]       # "4 ms" -> "3 ms"
    assert "2 of 3 slides identical" in rows[0]["note"]
    for row in visual:
        assert row["distance"] is not None and row["distance"] <= 3   # in `distance`, not note


# ---------- metadata ----------

def test_pdf_date_to_iso():
    assert docs.pdf_date_to_iso("D:20240301101500+01'00'") == "2024-03-01T10:15:00"
    assert docs.pdf_date_to_iso("D:20240301") == "2024-03-01T00:00:00"
    assert docs.pdf_date_to_iso("") is None and docs.pdf_date_to_iso("yesterday") is None


def fake_office_file(path, modified, author, filler):
    """A zip with docProps/core.xml, like a real .pptx has (plus some filler,
    so two fake files get different SHA-256)."""
    core = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
            ' xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/">'
            f"<dc:creator>{author}</dc:creator>"
            f'<dcterms:modified>{modified}</dcterms:modified>'
            "</cp:coreProperties>")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("docProps/core.xml", core)
        archive.writestr("filler.txt", filler)


def test_read_office_metadata(tmp_path):
    path = tmp_path / "deck.pptx"
    fake_office_file(path, "2026-11-12T09:30:00Z", "Anna Andersson", "x")
    assert docs.read_office_metadata(path) == {"author": "Anna Andersson", "date": "2026-11-12T09:30:00"}
    not_a_zip = tmp_path / "old.ppt"
    not_a_zip.write_bytes(b"old binary format")
    assert docs.read_office_metadata(not_a_zip) == {"author": None, "date": None}


def test_newest_revision_by_office_date_end_to_end(tmp_path, monkeypatch):
    """Two PPTX versions, "final" and "final2", where "final" was saved LATER.
    The date from the PPTX properties decides, not the name."""
    monkeypatch.setattr(docs, "find_soffice", lambda: None)
    monkeypatch.setattr(docs, "convert_with_office_windows", lambda source, target, ext: False)
    monkeypatch.setattr(docs, "convert_with_office_mac", lambda source, target, ext: False)
    folder = tmp_path / "in"
    folder.mkdir()
    fake_office_file(folder / "deck final.pptx", "2026-11-12T15:00:00Z", "Anna", "one")
    fake_office_file(folder / "deck final2.pptx", "2026-11-10T09:00:00Z", "Anna", "two")
    db_path = tmp_path / "dso.db"
    out = tmp_path / "out"
    base = ["--db", str(db_path), "--out", str(out)]
    assert main(base + ["ingest", str(folder)]) == 0

    # Pretend both were exported to PDF by hand: deck_a / deck_a_rev2 as their content.
    conn = connect(db_path)
    sha_by_name = {}
    for row in conn.execute("SELECT name, sha256 FROM files"):
        sha_by_name[row["name"]] = row["sha256"][:12]
    conn.close()
    (out / "converted").mkdir(parents=True)
    shutil.copy(FIXTURES / "docs" / "deck_a.pdf", out / "converted" / f"{sha_by_name['deck final2.pptx']}.pdf")
    shutil.copy(FIXTURES / "docs" / "deck_a_rev2.pdf", out / "converted" / f"{sha_by_name['deck final.pptx']}.pdf")

    assert main(base + ["docs"]) == 0
    assert main(base + ["dedup"]) == 0
    conn = connect(db_path)
    row = conn.execute(
        """SELECT b.name AS newer, d.note FROM duplicates d
           JOIN files b ON b.id = CAST(d.b_ref AS INTEGER) WHERE d.kind = 'revision'""").fetchone()
    dates = conn.execute("SELECT name, doc_date, author FROM files ORDER BY name").fetchall()
    conn.close()
    assert row["newer"] == "deck final.pptx"           # saved later, although "final2" sounds newer
    assert "(by date)" in row["note"]
    assert dates[0]["doc_date"] == "2026-11-12T15:00:00" and dates[0]["author"] == "Anna"
