"""Tests for `dso cleanup` (dso/cleanup.py): which cloud originals may go, the
integrity check before deleting, recycle bin, log. rclone is replaced by fakes,
so no real cloud is touched."""
import json
from argparse import Namespace

import pytest

from dso import cleanup
from dso.cli import main
from dso.common.db import connect
from dso.common.hashing import sha256_file


def make_file(folder, name, content):
    path = folder / name
    path.write_bytes(content)
    return path


@pytest.fixture()
def setup(tmp_path):
    """A watched cloud folder with: a fully distilled video, a video only
    transcribed for 2 of 5 minutes, deck.pdf + its exact copy, an older and a
    newer revision, a unique PDF; plus one file OUTSIDE the folder."""
    cloud = tmp_path / "cloud"
    cloud.mkdir()
    out = tmp_path / "out"
    (out / "export").mkdir(parents=True)
    (out / "transcripts").mkdir()
    db_path = tmp_path / "dso.db"
    conn = connect(db_path)
    files = {}
    specs = [("talk.mp4", "video", b"video one", 60.0, None),
             ("long.mp4", "video", b"video two", 300.0, 120.0),
             ("deck.pdf", "document", b"same deck", None, None),
             ("deck (1).pdf", "document", b"same deck", None, None),
             ("final.pptx", "document", b"old version", None, None),
             ("final2.pptx", "document", b"new version", None, None),
             ("notes.pdf", "document", b"unique notes", None, None)]
    for file_id, (name, kind, content, duration, limit) in enumerate(specs, start=1):
        path = make_file(cloud, name, content)
        sha = sha256_file(path)
        conn.execute("INSERT INTO files (id, path, name, ext, kind, size_bytes, sha256, scanned_at, duration_sec) "
                     "VALUES (?, ?, ?, 'x', ?, ?, ?, 'now', ?)",
                     (file_id, path.as_posix(), name, kind, len(content), sha, duration))
        files[name] = (file_id, sha)
        if kind == "video":
            conn.execute("INSERT INTO frames (file_id, t_sec, image_path, image_bytes, dhash) "
                         "VALUES (?, 0, 'f.webp', 1, '0000000000000000')", (file_id,))
            (out / "export" / f"{sha[:12]}.md").write_text("# export", encoding="utf-8")
            (out / "transcripts" / f"{sha[:12]}.json").write_text(
                json.dumps({"model": "small", "max_seconds": limit, "segments": []}), encoding="utf-8")
    outside = make_file(tmp_path, "elsewhere.mp4", b"not in the cloud folder")
    conn.execute("INSERT INTO files (id, path, name, ext, kind, size_bytes, sha256, scanned_at) "
                 "VALUES (99, ?, 'elsewhere.mp4', 'mp4', 'video', 1, ?, 'now')",
                 (outside.as_posix(), sha256_file(outside)))
    conn.execute("INSERT INTO duplicates (kind, a_ref, b_ref) VALUES ('file_exact', ?, ?)",
                 (str(files["deck (1).pdf"][0]), str(files["deck.pdf"][0])))
    conn.execute("INSERT INTO duplicates (kind, a_ref, b_ref, note) VALUES ('revision', ?, ?, 'x')",
                 (str(files["final.pptx"][0]), str(files["final2.pptx"][0])))
    (out / "slides" / files["final.pptx"][1][:12]).mkdir(parents=True)   # old version's slides kept
    conn.commit()
    conn.close()
    args = Namespace(db=str(db_path), out=str(out), folder=str(cloud), rclone="onedrive:DSO demo")
    return args, cloud


def planned_names(args):
    conn = connect(args.db)
    items = cleanup.plan(conn, args.folder, cleanup.Path(args.out))
    conn.close()
    return [item["name"] for item in items]


def test_plan_only_what_is_kept_elsewhere(setup):
    args, cloud = setup
    assert planned_names(args) == ["deck (1).pdf", "final.pptx", "talk.mp4"]
    # not planned: long.mp4 (transcript covers 2 of 5 minutes), deck.pdf (the kept
    # copy), final2.pptx (newest), notes.pdf (unique), elsewhere.mp4 (outside)


def test_transcript_must_cover_the_whole_video(tmp_path):
    (tmp_path / "transcripts").mkdir()
    for limit, sha in [(None, "a" * 64), (120.0, "b" * 64)]:
        (tmp_path / "transcripts" / f"{sha[:12]}.json").write_text(
            json.dumps({"max_seconds": limit}), encoding="utf-8")
    assert cleanup.transcript_covers_video(tmp_path, "a" * 64, 9000)       # full run
    assert cleanup.transcript_covers_video(tmp_path, "b" * 64, 119.4)      # short demo video, all of it
    assert not cleanup.transcript_covers_video(tmp_path, "b" * 64, 321)    # only 2 of 5 minutes
    assert not cleanup.transcript_covers_video(tmp_path, "c" * 64, 10)     # no transcript


def fake_rclone(monkeypatch, cloud_hashes=None):
    """quickxor() gives the same hash for local and cloud unless cloud_hashes
    says otherwise; deletes are recorded instead of done."""
    deleted = []

    def fake_quickxor(target):
        if cloud_hashes is not None and target in cloud_hashes:
            return cloud_hashes[target]
        return "samehash"
    monkeypatch.setattr(cleanup, "quickxor", fake_quickxor)
    monkeypatch.setattr(cleanup, "delete_to_recycle_bin", lambda target: deleted.append(target) or (True, ""))
    return deleted


def test_dry_run_deletes_nothing(setup, monkeypatch, capsys):
    args, cloud = setup
    deleted = fake_rclone(monkeypatch)
    assert main(["--db", args.db, "--out", args.out, "cleanup", args.folder, "--rclone", args.rclone]) == 0
    assert deleted == []
    assert "DRY RUN - would move 3 cloud file(s)" in capsys.readouterr().out


def test_apply_deletes_and_logs(setup, monkeypatch):
    args, cloud = setup
    deleted = fake_rclone(monkeypatch)
    assert cleanup.cleanup(args, True) == 3
    assert deleted == ["onedrive:DSO demo/deck (1).pdf", "onedrive:DSO demo/final.pptx",
                       "onedrive:DSO demo/talk.mp4"]
    for name in ["deck (1).pdf", "talk.mp4"]:
        assert (cloud / name).is_file()                    # local files are never deleted
    log = (cleanup.Path(args.out) / "reports" / "cleanup.md").read_text(encoding="utf-8")
    assert "freed in the cloud" in log and "exact copy of deck.pdf" in log and "recycle bin" in log


def test_changed_cloud_file_is_not_deleted(setup, monkeypatch):
    args, cloud = setup
    deleted = fake_rclone(monkeypatch, cloud_hashes={"onedrive:DSO demo/talk.mp4": "different"})
    cleanup.cleanup(args, True)
    assert "onedrive:DSO demo/talk.mp4" not in deleted
    log = (cleanup.Path(args.out) / "reports" / "cleanup.jsonl").read_text(encoding="utf-8")
    assert "the cloud file differs" in log


def test_changed_local_file_is_not_deleted(setup, monkeypatch):
    args, cloud = setup
    deleted = fake_rclone(monkeypatch)
    (cloud / "talk.mp4").write_bytes(b"replaced after processing")
    cleanup.cleanup(args, True)
    assert "onedrive:DSO demo/talk.mp4" not in deleted


def test_delete_always_goes_to_recycle_bin(monkeypatch):
    commands = []

    class Done:
        returncode = 0
        stderr = ""
    monkeypatch.setattr(cleanup.subprocess, "run", lambda command, **kw: commands.append(command) or Done())
    assert cleanup.delete_to_recycle_bin("onedrive:DSO demo/a.mp4") == (True, "")
    assert commands == [["rclone", "deletefile", "onedrive:DSO demo/a.mp4", "--onedrive-hard-delete=false"]]


def test_watch_needs_upload_before_deleting(tmp_path):
    assert main(["--db", str(tmp_path / "d.db"), "--out", str(tmp_path / "out"), "watch", str(tmp_path / "in"),
                 "--rclone", "onedrive:x", "--delete-originals", "--once"]) == 1
