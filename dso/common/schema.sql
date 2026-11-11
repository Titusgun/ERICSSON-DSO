-- DSO data contract v1. SHARED FILE: change only in a `contract:` PR.
-- Meaning of every column: docs/DATA_CONTRACT.md. Applied by dso/common/db.py on every open.
-- Hashes are stored as lowercase hex TEXT (SHA-256 = 64 chars, dHash = 16 chars),
-- because SQLite INTEGER is signed and 64-bit dHash values would turn negative.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS files (
    id            INTEGER PRIMARY KEY,
    path          TEXT NOT NULL UNIQUE,   -- path as scanned, relative to scan root
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
    scanned_at    TEXT NOT NULL           -- ISO 8601
);
CREATE INDEX IF NOT EXISTS idx_files_sha ON files(sha256);

-- One row per slide/page of a document (PDF, or PPTX converted to PDF).
CREATE TABLE IF NOT EXISTS slides (
    slide_id      TEXT PRIMARY KEY,       -- "<doc>:<page>:<text_sha256[0:8]>", see contract
    file_id       INTEGER NOT NULL REFERENCES files(id),
    page_no       INTEGER NOT NULL,       -- 1-based
    text          TEXT NOT NULL,          -- extracted text (pdftotext)
    text_sha256   TEXT NOT NULL,          -- SHA-256 of normalized text
    dhash         TEXT NOT NULL,          -- 64-bit dHash of the rendered image, 16 hex chars
    image_path    TEXT NOT NULL           -- out/slides/<file sha256[0:12]>/p<NNN>.webp
);
CREATE INDEX IF NOT EXISTS idx_slides_text ON slides(text_sha256);
CREATE INDEX IF NOT EXISTS idx_slides_dhash ON slides(dhash);

-- One row per kept key frame of a video.
CREATE TABLE IF NOT EXISTS frames (
    id            INTEGER PRIMARY KEY,
    file_id       INTEGER NOT NULL REFERENCES files(id),
    t_sec         REAL NOT NULL,          -- timestamp in the video
    image_path    TEXT NOT NULL,          -- out/frames/<file sha256[0:12]>/f<NNNN>.webp
    image_bytes   INTEGER NOT NULL,
    dhash         TEXT NOT NULL,
    slide_id      TEXT REFERENCES slides(slide_id), -- best matching slide, NULL if none
    match_dist    INTEGER                 -- Hamming distance to that slide, NULL if none
);
CREATE INDEX IF NOT EXISTS idx_frames_file ON frames(file_id, t_sec);

-- One row per transcript segment (whisper.cpp output).
CREATE TABLE IF NOT EXISTS segments (
    id            INTEGER PRIMARY KEY,
    file_id       INTEGER NOT NULL REFERENCES files(id),
    start_sec     REAL NOT NULL,
    end_sec       REAL NOT NULL,
    speaker       TEXT NOT NULL DEFAULT 'Speaker ?',
    text          TEXT NOT NULL,
    frame_id      INTEGER REFERENCES frames(id)  -- frame visible at start_sec (set by `dso link`)
);
CREATE INDEX IF NOT EXISTS idx_segments_file ON segments(file_id, start_sec);

-- Every duplicate / near-duplicate relation found.
CREATE TABLE IF NOT EXISTS duplicates (
    id            INTEGER PRIMARY KEY,
    kind          TEXT NOT NULL,          -- 'file_exact' | 'slide_text' | 'slide_visual' | 'revision'
    a_ref         TEXT NOT NULL,          -- files.id (as text) or slides.slide_id
    b_ref         TEXT NOT NULL,          -- the canonical copy (keep this one)
    distance      INTEGER,                -- Hamming distance for visual/revision, NULL for exact
    note          TEXT                    -- e.g. "name hint: (1)"
);

-- Before/after sizes per scenario, filled by `dso savings`.
CREATE TABLE IF NOT EXISTS savings (
    scenario      TEXT PRIMARY KEY,       -- e.g. 'video_distill', 'dedup', 'offboarding'
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
