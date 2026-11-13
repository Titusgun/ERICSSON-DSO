"""Tests for `dso video` (dso/video.py): key frames and transcript, on the synthetic
fixture tests/fixtures/video/talk.mp4 (3 slides, changing at 0, 8 and 16 s)."""
import json
from argparse import Namespace
from pathlib import Path

import pytest

from dso import video
from dso.cli import main
from dso.common.db import connect

FIXTURE_VIDEO = Path(__file__).parent / "fixtures" / "video"


@pytest.fixture()
def paths(tmp_path):
    """A fresh DB with the fixture video ingested. Returns (db_path, out_dir)."""
    db_path = tmp_path / "dso.db"
    out = tmp_path / "out"
    assert main(["--db", str(db_path), "--out", str(out), "ingest", str(FIXTURE_VIDEO)]) == 0
    return db_path, out


def run_video(db_path, out, max_seconds=None):
    """Calls video.run() directly, so --max-seconds can be tested before
    dso/cli.py has the option (#17)."""
    args = Namespace(db=str(db_path), out=str(out), model="tiny.en", max_seconds=max_seconds)
    assert video.run(args) == 0


def frame_rows(db_path):
    conn = connect(db_path)
    rows = conn.execute("SELECT t_sec, image_path, image_bytes, dhash FROM frames ORDER BY t_sec").fetchall()
    conn.close()
    return rows


def test_key_frames_written(paths):
    db_path, out = paths
    run_video(db_path, out)
    rows = frame_rows(db_path)
    assert len(rows) == 3
    for i, row in enumerate(rows):
        # Path relative to --out, with "/" on every OS (DATA_CONTRACT 3).
        assert row["image_path"] == f"frames/{row['image_path'].split('/')[1]}/f{i + 1:04d}.webp"
        image_file = out / row["image_path"]
        assert image_file.is_file()
        assert row["image_bytes"] == image_file.stat().st_size
        assert len(row["dhash"]) == 16


def test_max_seconds(paths):
    db_path, out = paths
    run_video(db_path, out, max_seconds=5)
    times = [row["t_sec"] for row in frame_rows(db_path)]
    assert times == [0.0]                       # the slide changes at 8 and 16 s are cut off


def test_rerun_replaces_frames_and_files(paths):
    db_path, out = paths
    run_video(db_path, out)
    run_video(db_path, out, max_seconds=10)     # second run with fewer frames
    rows = frame_rows(db_path)
    assert len(rows) == 2
    frames_dir = (out / rows[0]["image_path"]).parent
    assert len(list(frames_dir.glob("*.webp"))) == 2    # stale f0003.webp is gone


def test_rerun_with_linked_segments(paths):
    """A transcript segment points at a frame; re-running must not fail on that link."""
    db_path, out = paths
    run_video(db_path, out)
    conn = connect(db_path)
    frame_id = conn.execute("SELECT id FROM frames ORDER BY t_sec LIMIT 1").fetchone()[0]
    conn.execute("INSERT INTO segments (file_id, start_sec, end_sec, text, frame_id) "
                 "VALUES (1, 1.0, 2.0, 'hello', ?)", (frame_id,))
    conn.commit()
    conn.close()
    run_video(db_path, out)
    assert len(frame_rows(db_path)) == 3


# ---------- transcript (step 2) ----------

def fixture_sha12(db_path):
    conn = connect(db_path)
    sha = conn.execute("SELECT sha256 FROM files WHERE kind = 'video'").fetchone()[0]
    conn.close()
    return sha[:12]


def segment_texts(db_path):
    conn = connect(db_path)
    rows = conn.execute("SELECT start_sec, end_sec, speaker, text FROM segments ORDER BY start_sec").fetchall()
    conn.close()
    return [(r["start_sec"], r["end_sec"], r["speaker"], r["text"]) for r in rows]


def write_cache(out, short_id, model, max_seconds, segments):
    path = out / "transcripts" / f"{short_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"model": model, "max_seconds": max_seconds, "language": "en",
            "language_probability": 1.0, "audio_sec": 24.0, "segments": segments}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_whisper_runs_on_fixture(paths):
    """The fixture audio is a tone: Whisper runs, finds no speech, writes the cache file."""
    db_path, out = paths
    run_video(db_path, out)
    assert segment_texts(db_path) == []
    cache = json.loads((out / "transcripts" / f"{fixture_sha12(db_path)}.json").read_text(encoding="utf-8"))
    assert cache["model"] == "tiny.en" and cache["max_seconds"] is None
    assert cache["segments"] == []


def test_cached_transcript_is_loaded(paths):
    db_path, out = paths
    short_id = fixture_sha12(db_path)
    write_cache(out, short_id, "tiny.en", None,
                [{"start": 1.0, "end": 3.5, "text": "Hello everyone"},
                 {"start": 9.0, "end": 12.0, "text": "As you see here, the latency drops"}])
    run_video(db_path, out)
    run_video(db_path, out)                             # second run: no duplicate rows
    assert segment_texts(db_path) == [
        (1.0, 3.5, "Speaker ?", "Hello everyone"),
        (9.0, 12.0, "Speaker ?", "As you see here, the latency drops")]


def test_cache_ignored_for_other_model_or_max_seconds(paths):
    """A cache made with another model or another --max-seconds is not reused."""
    db_path, out = paths
    short_id = fixture_sha12(db_path)
    fake = [{"start": 1.0, "end": 2.0, "text": "from the cache"}]
    write_cache(out, short_id, "small.en", None, fake)
    run_video(db_path, out)                             # model tiny.en -> transcribes again
    assert segment_texts(db_path) == []
    write_cache(out, short_id, "tiny.en", 300, fake)
    run_video(db_path, out)                             # whole file, cache was 300 s -> again
    assert segment_texts(db_path) == []


def test_whisper_options():
    # English-only models cannot switch language, so no multilingual flag.
    assert video.whisper_options("base.en") == {"vad_filter": True}
    # Multilingual models re-detect the language per window (Swedish breaks).
    assert video.whisper_options("small") == {"vad_filter": True, "multilingual": True}
    assert video.whisper_options("base") == {"vad_filter": True, "multilingual": True}
