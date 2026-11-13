"""Tests for `dso speakers` (dso/speakers.py). No model is downloaded: the speaker
turns come from hand-written cache files or are given directly."""
import json
from argparse import Namespace

from dso import speakers
from dso.common.db import connect

SHA = "6affb57477a5" + "0" * 52


def turn(start, end, speaker):
    return {"start": start, "end": end, "speaker": speaker}


def test_speaker_for_picks_who_talks_longest():
    turns = [turn(0, 10, 1), turn(10, 14, 2)]
    assert speakers.speaker_for(2, 9, turns) == "Speaker 1"
    assert speakers.speaker_for(9, 14, turns) == "Speaker 2"        # 1 s of 1, 4 s of 2
    assert speakers.speaker_for(20, 25, turns) == "Speaker ?"       # nobody talks (music)


def test_short_speakers_dropped_and_numbered_by_first_appearance():
    raw = [turn(0, 60, 7), turn(60, 70, 4), turn(70, 130, 2), turn(130, 150, 7)]   # sherpa's own numbers
    turns = speakers.renumber(speakers.drop_short_speakers(raw))
    assert [t["speaker"] for t in turns] == [1, 2, 1]       # 7 -> 1, 2 -> 2; 4 (10 s < 30 s) is noise


def make_db(tmp_path, transcript_max_seconds):
    """One video with three segments and a transcript cache."""
    db_path = tmp_path / "dso.db"
    out = tmp_path / "out"
    (out / "transcripts").mkdir(parents=True)
    (out / "transcripts" / f"{SHA[:12]}.json").write_text(
        json.dumps({"model": "small", "max_seconds": transcript_max_seconds, "segments": []}), encoding="utf-8")
    conn = connect(db_path)
    conn.execute("INSERT INTO files (id, path, name, ext, kind, size_bytes, sha256, scanned_at) "
                 "VALUES (1, 'v.mp4', 'v.mp4', 'mp4', 'video', 1, ?, 'now')", (SHA,))
    for start, end in [(0, 5), (50, 58), (100, 105)]:
        conn.execute("INSERT INTO segments (file_id, start_sec, end_sec, text) VALUES (1, ?, ?, 'x')", (start, end))
    conn.commit()
    conn.close()
    return Namespace(db=str(db_path), out=str(out)), out


def write_speaker_cache(out, max_seconds, turns, model=None, threshold=None):
    (out / "speakers").mkdir(exist_ok=True)
    (out / "speakers" / f"{SHA[:12]}.json").write_text(json.dumps({
        "model": model or speakers.MODEL_ID, "threshold": threshold or speakers.CLUSTER_THRESHOLD,
        "max_seconds": max_seconds, "turns": turns}), encoding="utf-8")


def labels(args):
    conn = connect(args.db)
    result = [row[0] for row in conn.execute("SELECT speaker FROM segments ORDER BY start_sec")]
    conn.close()
    return result


def no_models(monkeypatch):
    """Fails loudly if the models would be loaded."""
    def refuse():
        raise RuntimeError("models must not be loaded in this test")
    monkeypatch.setattr(speakers, "ensure_models", refuse)


def test_cached_turns_label_the_segments(tmp_path, monkeypatch):
    args, out = make_db(tmp_path, None)
    write_speaker_cache(out, None, [turn(0, 40, 3), turn(40, 90, 0), turn(95, 130, 3)])
    no_models(monkeypatch)                       # the cache is used, nothing is analysed
    assert speakers.run(args) == 0
    assert labels(args) == ["Speaker 1", "Speaker 2", "Speaker 1"]
    assert speakers.run(args) == 0               # safe to run twice
    assert labels(args) == ["Speaker 1", "Speaker 2", "Speaker 1"]


def test_cache_for_another_part_is_not_used(tmp_path, monkeypatch, capsys):
    """Speakers analysed for the first 120 s must be redone when the transcript
    now covers the whole video - here the models are missing, so the step is
    skipped with a message and the segments stay unknown (pipeline goes on)."""
    args, out = make_db(tmp_path, None)
    write_speaker_cache(out, 120.0, [turn(0, 50, 0)])
    no_models(monkeypatch)
    assert speakers.run(args) == 0
    assert "skipped" in capsys.readouterr().out
    assert labels(args) == ["Speaker ?", "Speaker ?", "Speaker ?"]


def test_cache_from_other_models_is_not_used(tmp_path, monkeypatch):
    args, out = make_db(tmp_path, None)
    write_speaker_cache(out, None, [turn(0, 50, 0)], model="some other model")
    no_models(monkeypatch)
    speakers.run(args)
    assert labels(args) == ["Speaker ?", "Speaker ?", "Speaker ?"]


def test_video_without_transcript_is_ignored(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "dso.db"
    conn = connect(db_path)
    conn.execute("INSERT INTO files (id, path, name, ext, kind, size_bytes, sha256, scanned_at) "
                 "VALUES (1, 'v.mp4', 'v.mp4', 'mp4', 'video', 1, ?, 'now')", (SHA,))
    conn.commit()
    conn.close()
    no_models(monkeypatch)
    assert speakers.run(Namespace(db=str(db_path), out=str(tmp_path / "out"))) == 0
    assert "0 videos with a transcript" in capsys.readouterr().out
