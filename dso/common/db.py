"""Opening the SQLite database. The schema (schema.sql) is applied on every open,
so the database always has every table. SHARED FILE (owner: C)."""
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Columns added to EXISTING tables after contract v1. `CREATE TABLE IF NOT EXISTS`
# in schema.sql never changes a table that is already there, so an older
# out/dso.db would miss them (and `dso video` would crash writing duration_sec).
# connect() adds them. (table, column, SQL type)
ADDED_COLUMNS = [
    ("files", "duration_sec", "REAL"),          # contract v1.1
]


def add_missing_columns(conn):
    """Adds the ADDED_COLUMNS that an older database does not have yet.
    Existing rows get NULL in the new column."""
    for table, column, sql_type in ADDED_COLUMNS:
        names = []
        for row in conn.execute(f"PRAGMA table_info({table})"):
            names.append(row[1])                  # row[1] = column name
        if column not in names:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
            conn.commit()


def connect(db_path):
    """Open (or create) the database and make sure all tables exist.

    row_factory = sqlite3.Row lets you read columns by name: row["sha256"].
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    add_missing_columns(conn)
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
