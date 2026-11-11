"""Opening the SQLite database. The schema (schema.sql) is applied on every open,
so the database always has every table. SHARED FILE (owner: C)."""
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(db_path):
    """Open (or create) the database and make sure all tables exist.

    row_factory = sqlite3.Row lets you read columns by name: row["sha256"].
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn


def has_fts5():
    """True if this Python's SQLite supports full-text search (FTS5)."""
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        conn.close()
        return True
    except sqlite3.OperationalError:
        return False
