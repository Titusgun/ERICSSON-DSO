-- DSO data contract v1.1. SHARED FILE: change only in a `contract:` PR.
-- Meaning of every column: docs/DATA_CONTRACT.md. Applied by dso/common/db.py on every open.
-- Hashes are stored as lowercase hex TEXT (SHA-256 = 64 chars, dHash = 16 chars),
-- because SQLite INTEGER is signed and 64-bit dHash values would turn negative.
-- CREATE TABLE IF NOT EXISTS never changes an existing table: after a contract change,
-- delete out/dso.db once and rerun the pipeline.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS files (
    id            INTEGER PRIMARY KEY,
    path          TEXT NOT NULL UNIQUE,   -- as ingest found it: relative to where dso ran, or absolute; "/" (contract 3)
    name          TEXT NOT NULL,          -- file name only, e.g. "deck (1).pdf"
    ext           TEXT NOT NULL,          -- lowercase, no dot: "pdf", "mp4", "pptx"
    kind          TEXT NOT NULL,          -- 'video' | 'document' | 'other'
    size_bytes    INTEGER NOT NULL,
    sha256        TEXT NOT NULL,          -- hash of the whole file
    source        TEXT NOT NULL DEFAULT 'local', -- 'sharepoint' | 'onedrive' | 'play' | 'local' | 'offboarding'
    owner         TEXT,                   -- employee / folder owner if known
    doc_number    TEXT,                   -- Ericsson document number if found
    revision      TEXT,
    author        TEXT,
    doc_date      TEXT,                   -- ISO 8601 if known
    duration_sec  REAL,                   -- video length in seconds, set by `dso video`; NULL otherwise
    scanned_at    TEXT NOT NULL           -- ISO 8601
);
CREATE INDEX IF NOT EXISTS idx_files_sha ON files(sha256);

-- One row per slide/page of a document (PDF, or PPTX converted to PDF).
-- Each distinct file content (sha256) is processed once; file_id = the canonical copy (contract 5).
CREATE TABLE IF NOT EXISTS slides (
    slide_id      TEXT PRIMARY KEY,       -- "<sha12>:<page>:<text_sha256[0:8]>", see contract 5
    file_id       INTEGER NOT NULL REFERENCES files(id),
    page_no       INTEGER NOT NULL,       -- 1-based
    text          TEXT NOT NULL,          -- extracted text (pypdfium2)
    text_sha256   TEXT NOT NULL,          -- SHA-256 of normalized text
    dhash         TEXT NOT NULL,          -- 64-bit dHash of the rendered image, 16 hex chars
    image_path    TEXT NOT NULL           -- relative to out/: slides/<sha12>/p<NNN>.webp
);
CREATE INDEX IF NOT EXISTS idx_slides_text ON slides(text_sha256);
CREATE INDEX IF NOT EXISTS idx_slides_dhash ON slides(dhash);

-- One row per kept key frame of a video.
CREATE TABLE IF NOT EXISTS frames (
    id            INTEGER PRIMARY KEY,
    file_id       INTEGER NOT NULL REFERENCES files(id),
    t_sec         REAL NOT NULL,          -- timestamp in the video
    image_path    TEXT NOT NULL,          -- relative to out/: frames/<sha12>/f<NNNN>.webp
    image_bytes   INTEGER NOT NULL,
    dhash         TEXT NOT NULL,
    slide_id      TEXT REFERENCES slides(slide_id) ON DELETE SET NULL, -- best matching slide (set by `dso link`)
    match_dist    INTEGER                 -- Hamming distance to that slide, NULL if none
);
CREATE INDEX IF NOT EXISTS idx_frames_file ON frames(file_id, t_sec);

-- One row per transcript segment (faster-whisper output).
CREATE TABLE IF NOT EXISTS segments (
    id            INTEGER PRIMARY KEY,
    file_id       INTEGER NOT NULL REFERENCES files(id),
    start_sec     REAL NOT NULL,
    end_sec       REAL NOT NULL,
    speaker       TEXT NOT NULL DEFAULT 'Speaker ?',
    text          TEXT NOT NULL,
    frame_id      INTEGER REFERENCES frames(id) ON DELETE SET NULL -- frame visible at start_sec (set by `dso link`)
);
CREATE INDEX IF NOT EXISTS idx_segments_file ON segments(file_id, start_sec);

-- Every duplicate / near-duplicate relation found.
CREATE TABLE IF NOT EXISTS duplicates (
    id            INTEGER PRIMARY KEY,
    kind          TEXT NOT NULL,          -- 'file_exact' | 'file_content' | 'slide_text' | 'slide_visual' | 'revision'
    a_ref         TEXT NOT NULL,          -- files.id (as text) or slides.slide_id
    b_ref         TEXT NOT NULL,          -- the canonical copy (keep this one)
    distance      INTEGER,                -- Hamming distance for slide_visual only, NULL for every other kind
    note          TEXT                    -- e.g. "name hint: (1)", "text ratio 0.93"
);

-- Before/after sizes per scenario, filled by `dso savings` (contract 10).
CREATE TABLE IF NOT EXISTS savings (
    scenario      TEXT PRIMARY KEY,       -- 'video_distill' | 'dedup' | 'offboarding'
    bytes_before  INTEGER NOT NULL,
    bytes_after   INTEGER NOT NULL
);

-- Full-text index over everything searchable. Filled by `dso index`.
-- kind = 'segment' (ref_id = segments.id) or 'slide' (ref_id = slides.slide_id)
CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
    text,
    kind UNINDEXED,
    ref_id UNINDEXED,
    file_id UNINDEXED
);
