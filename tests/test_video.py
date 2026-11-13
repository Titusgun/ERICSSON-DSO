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


def run_video(db_path, out, max_seconds=None, force=False):
    """Calls video.run() directly with the given options."""
    args = Namespace(db=str(db_path), out=str(out), model="tiny.en", max_seconds=max_seconds, force=force)
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
    run_video(db_path, out, max_seconds=10, force=True)     # forced second run with fewer frames
    rows = frame_rows(db_path)
    assert len(rows) == 2
    frames_dir = (out / rows[0]["image_path"]).parent
    assert len(list(frames_dir.glob("*.webp"))) == 2    # stale f0003.webp is gone
    assert not (out / "frames" / (frames_dir.name + ".new")).exists()   # temp folder swapped in


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


# ---------- which samples become key frames (keep_decision) ----------

A = "0000000000000000"
A_NOISE = "000000000000000f"     # 4 bits from A: same slide, a bit of noise
B = "ffffffffffffffff"
C = "00000000ffffffff"           # 32 bits from A and from B: a third picture


def kept_times(hashes):
    """Runs the same loop as extract_key_frames() on one hash per second and
    returns the times (= list positions) of the kept frames."""
    kept = []
    prev = None
    last_kept = None
    for t, h in enumerate(hashes):
        current = {"t_sec": float(t), "hash": h}
        if last_kept is None:
            to_keep = current
        else:
            decision = video.keep_decision(prev, current, last_kept)
            to_keep = {"prev": prev, "current": current, None: None}[decision]
        if to_keep is not None:
            kept.append(to_keep["t_sec"])
            last_kept = to_keep
        prev = current
    return kept


def test_slide_change_kept_at_the_time_it_happened():
    assert kept_times([A] * 5 + [B] * 5) == [0.0, 5.0]


def test_noise_on_the_same_slide_is_ignored():
    assert kept_times([A, A_NOISE, A, A_NOISE, A, A_NOISE]) == [0.0]


def test_one_second_transition_is_skipped():
    # A, then a 1-second blend/animation C, then B stays: keep A and B, not C.
    assert kept_times([A, A, C, B, B, B]) == [0.0, 3.0]


def test_short_flicker_is_skipped():
    # Teams shows the speaker (B) for one second, then the slide again.
    assert kept_times([A, A, B, A, A, A]) == [0.0]


def test_going_back_to_an_earlier_slide_is_kept():
    # A -> B -> A: the second A must be a key frame, or `dso link` would
    # say B was on screen.
    assert kept_times([A] * 3 + [B] * 3 + [A] * 3) == [0.0, 3.0, 6.0]


def test_never_settling_screen_keeps_one_frame_per_max_gap():
    # The picture changes every second (camera, video): fallback every 60 s.
    hashes = []
    for t in range(130):
        if t % 2 == 0:
            hashes.append(A)
        else:
            hashes.append(B)
    assert video.MAX_GAP_SEC == 60
    # 61 = first B after 60 s; 121 is B again (no change), so the next one is A at 122.
    assert kept_times(hashes) == [0.0, 61.0, 122.0]


# ---------- skip already processed videos (for `dso all` / `dso watch`) ----------

def test_second_run_skips_processed_video(paths, capsys):
    db_path, out = paths
    run_video(db_path, out)
    capsys.readouterr()
    run_video(db_path, out)
    assert "already processed - skipped" in capsys.readouterr().out
    assert len(frame_rows(db_path)) == 3


def test_force_redoes_the_video(paths, capsys):
    db_path, out = paths
    run_video(db_path, out)
    capsys.readouterr()
    args = Namespace(db=str(db_path), out=str(out), model="tiny.en", max_seconds=None, force=True)
    assert video.run(args) == 0
    assert "already processed" not in capsys.readouterr().out


def test_duration_is_stored(paths):
    db_path, out = paths
    run_video(db_path, out, max_seconds=5)       # the whole length, even with --max-seconds
    conn = connect(db_path)
    duration = conn.execute("SELECT duration_sec FROM files WHERE kind = 'video'").fetchone()[0]
    conn.close()
    assert duration == pytest.approx(24, abs=1)



# ---------- never replace finished results by a shorter run ----------

def test_max_seconds_run_keeps_full_results(paths, capsys):
    """Like `dso watch --max-seconds 120` on a DB with fully processed videos:
    the full results must stay (the bug of 13 Nov that cut real videos to 2 min)."""
    db_path, out = paths
    run_video(db_path, out)                               # full run
    capsys.readouterr()
    run_video(db_path, out, max_seconds=5)                # demo-style short run
    assert "already processed - skipped" in capsys.readouterr().out
    assert len(frame_rows(db_path)) == 3                  # not cut to the first 5 s
    cache_files = list((out / "transcripts").glob("*.json"))
    cache = json.loads(cache_files[0].read_text(encoding="utf-8"))
    assert cache["max_seconds"] is None                   # full transcript cache kept


def test_covers():
    assert video.covers(None, None) and video.covers(None, 120)      # full covers everything
    assert video.covers(300, 120) and not video.covers(120, 300)
    assert not video.covers(120, None)                               # 2 min never covers a full run


def test_stopped_run_keeps_old_frames_and_rows(paths, monkeypatch):
    """A run that stops halfway (Ctrl+C, crash) must not delete the old pictures
    while the DB still points at them."""
    import gc
    db_path, out = paths
    run_video(db_path, out)
    before = frame_rows(db_path)
    calls = []

    def crash_after_two(image):
        calls.append(1)
        if len(calls) > 2:
            raise KeyboardInterrupt
        return "0000000000000000"
    monkeypatch.setattr(video, "dhash_image", crash_after_two)
    with pytest.raises(KeyboardInterrupt):
        run_video(db_path, out, force=True)
    gc.collect()                                           # closes the interrupted connection
    after = frame_rows(db_path)
    assert [r["image_path"] for r in after] == [r["image_path"] for r in before]
    for row in after:
        assert (out / row["image_path"]).is_file()        # every row still has its picture
