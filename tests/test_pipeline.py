"""End-to-end test on the synthetic fixtures. CI runs this on Windows, macOS and Linux.

Each feature has a test marked `skip`. When you finish your feature, delete the
`@pytest.mark.skip` line above its test. After that, nobody (human or AI agent)
can break it without CI going red.
"""
import shutil
from pathlib import Path

import pytest

from dso.cli import main
from dso.common.db import connect, has_fts5
from dso.common.hashing import hamming, normalize_text

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    """Runs the whole pipeline once on the fixtures and returns an open DB."""
    work = tmp_path_factory.mktemp("run")
    db_path = str(work / "dso.db")
    out = str(work / "out")
    base = ["--db", db_path, "--out", out]
    assert main(base + ["ingest", str(FIXTURES / "docs"), "--source", "sharepoint"]) == 0
    assert main(base + ["ingest", str(FIXTURES / "video"), "--source", "play"]) == 0
    assert main(base + ["ingest", str(FIXTURES / "offboarding" / "jane"),
                        "--source", "offboarding", "--owner", "jane"]) == 0
    for command in ["video", "docs", "dedup", "link", "index", "export", "savings"]:
        args = base + [command]
        if command == "video":
            args = args + ["--model", "tiny.en"]
        assert main(args) == 0, command
    assert main(base + ["offboard", str(FIXTURES / "offboarding" / "jane")]) == 0
    conn = connect(db_path)
    yield conn
    conn.close()
    shutil.rmtree(work, ignore_errors=True)


def count(conn, sql):
    return conn.execute(sql).fetchone()[0]


# ---------- already working ----------

def test_sqlite_has_fts5():
    assert has_fts5(), "This Python's SQLite has no FTS5 - search will not work"


def test_helpers():
    assert normalize_text("  Hello \n  World ") == "hello world"
    assert hamming("0000000000000000", "000000000000000f") == 4


def test_ingest(db):
    assert count(db, "SELECT COUNT(*) FROM files") == 7


def test_exact_duplicate_hashes_exist(db):
    assert count(db, "SELECT COUNT(*) FROM (SELECT sha256 FROM files GROUP BY sha256 HAVING COUNT(*) > 1)") == 2


# ---------- remove `skip` when your feature works ----------

def test_key_frames(db):
    times = [row[0] for row in db.execute("SELECT t_sec FROM frames ORDER BY t_sec")]
    assert len(times) == 3
    assert abs(times[1] - 8) <= 1 and abs(times[2] - 16) <= 1


@pytest.mark.skip(reason="TODO owner B: slides")
def test_slides(db):
    assert count(db, "SELECT COUNT(*) FROM slides") == 16


@pytest.mark.skip(reason="TODO owner B: dedup")
def test_duplicates(db):
    assert count(db, "SELECT COUNT(*) FROM duplicates WHERE kind='file_exact'") == 2
    assert count(db, "SELECT COUNT(*) FROM duplicates WHERE kind='slide_text'") >= 2


@pytest.mark.skip(reason="TODO owner B: revisions (SHOULD)")
def test_revision(db):
    assert count(db, "SELECT COUNT(*) FROM duplicates WHERE kind='revision'") >= 1


@pytest.mark.skip(reason="TODO owner C: link")
def test_frames_linked_to_slides(db):
    assert count(db, "SELECT COUNT(*) FROM frames WHERE slide_id IS NOT NULL") == 3


@pytest.mark.skip(reason="TODO owner C: search")
def test_search(db):
    from dso.search import search
    results = search(db, "latency")
    assert len(results) >= 1


def test_savings(db):
    assert count(db, "SELECT COUNT(*) FROM savings") >= 2
    rows = {}
    for row in db.execute("SELECT scenario, bytes_before, bytes_after FROM savings"):
        rows[row[0]] = (row[1], row[2])
    # Fixtures: deck_a and deck_b each have one exact copy.
    assert rows["dedup"][1] < rows["dedup"][0]
    # jane: "deck_b - Copy.pdf" exists elsewhere, my_notes.pdf is unique -> keep only my_notes.
    my_notes = (FIXTURES / "offboarding" / "jane" / "my_notes.pdf").stat().st_size
    assert rows["offboarding"][1] == my_notes
