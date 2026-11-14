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
    assert "DRY RUN - would move 3 file(s), 29.0 B, to the cloud recycle bin" in capsys.readouterr().out


def test_apply_deletes_and_logs(setup, monkeypatch):
    args, cloud = setup
    deleted = fake_rclone(monkeypatch)
    assert cleanup.cleanup(args, True) == 3
    assert deleted == ["onedrive:DSO demo/deck (1).pdf", "onedrive:DSO demo/final.pptx",
                       "onedrive:DSO demo/talk.mp4"]
    for name in ["deck (1).pdf", "talk.mp4"]:
        assert (cloud / name).is_file()                    # local files are never deleted
    log = (cleanup.Path(args.out) / "reports" / "cleanup.md").read_text(encoding="utf-8")
    assert "freed." in log and "exact copy of deck.pdf" in log and "recycle bin" in log


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


def test_video_with_frames_for_only_part_is_kept(setup, monkeypatch):
    """Full transcript, but key frames only for the first 10 min of a longer video."""
    args, cloud = setup
    conn = connect(args.db)
    sha = conn.execute("SELECT sha256 FROM files WHERE name = 'talk.mp4'").fetchone()[0]
    conn.execute("UPDATE files SET duration_sec = 9300 WHERE name = 'talk.mp4'")
    conn.commit()
    conn.close()
    frames_dir = cleanup.Path(args.out) / "frames" / sha[:12]
    frames_dir.mkdir(parents=True)
    (frames_dir / "frames_info.json").write_text(json.dumps({"max_seconds": 600}), encoding="utf-8")
    assert "talk.mp4" not in planned_names(args)


# ---------- files deleted by an earlier run ----------

def test_second_run_does_not_repeat_deleted_files(setup, monkeypatch, capsys):
    """After a restart, the files deleted last time are not checked or logged again."""
    args, cloud = setup
    deleted = fake_rclone(monkeypatch)
    cleanup.cleanup(args, True)
    assert len(deleted) == 3
    log = cleanup.Path(args.out) / "reports" / "cleanup.jsonl"
    lines_before = len(log.read_text(encoding="utf-8").splitlines())
    capsys.readouterr()
    assert cleanup.cleanup(args, True) == 0
    assert len(deleted) == 3                                          # nothing deleted again
    assert len(log.read_text(encoding="utf-8").splitlines()) == lines_before   # nothing logged again
    assert "nothing new to remove (3 removed by earlier runs" in capsys.readouterr().out


def test_new_upload_with_the_same_name_is_handled_again(setup, monkeypatch):
    args, cloud = setup
    deleted = fake_rclone(monkeypatch)
    cleanup.cleanup(args, True)
    # Someone uploads another "deck (1).pdf" - same name, different content.
    conn = connect(args.db)
    conn.execute("UPDATE files SET sha256 = ? WHERE name = 'deck (1).pdf'", ("f" * 64,))
    conn.commit()
    conn.close()
    items = cleanup.plan(connect(args.db), args.folder, cleanup.Path(args.out))
    done = cleanup.already_deleted(cleanup.Path(args.out))
    copy = [item for item in items if item["name"] == "deck (1).pdf"]
    assert len(copy) == 1                                             # dedup still calls it a copy
    assert not cleanup.was_deleted_before(copy[0], args.rclone, done) # new content -> handled again


def test_old_log_lines_without_sha_are_matched_by_size(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "cleanup.jsonl").write_text(json.dumps({
        "time": "t", "file": "a.mp4", "cloud_path": "onedrive:Demo/a.mp4", "size_bytes": 123,
        "reason": "x", "kept_at": "y", "result": "deleted (in the recycle bin)"}) + "\n", encoding="utf-8")
    done = cleanup.already_deleted(tmp_path)
    item = {"relative": "a.mp4", "sha256": "abc", "size_bytes": 123}
    assert cleanup.was_deleted_before(item, "onedrive:Demo", done)
    other = {"relative": "a.mp4", "sha256": "abc", "size_bytes": 999}
    assert not cleanup.was_deleted_before(other, "onedrive:Demo", done)


# ---------- local mode (no cloud): MOVE to <folder>.recycle ----------

def local(args):
    """The same setup, but without a cloud remote."""
    return Namespace(db=args.db, out=args.out, folder=args.folder, rclone=None)


def test_local_dry_run_moves_nothing(setup, capsys):
    args, cloud = setup
    cleanup.cleanup(local(args), False)
    assert "DRY RUN - would move 3 file(s)" in capsys.readouterr().out
    assert (cloud / "talk.mp4").is_file() and not cleanup.recycle_dir(cloud).exists()


def test_local_apply_moves_to_recycle_folder(setup):
    args, cloud = setup
    assert cleanup.cleanup(local(args), True) == 3
    bin_dir = cleanup.recycle_dir(cloud)
    assert bin_dir == cloud.parent / "cloud.recycle"                 # NEXT TO the folder, not inside
    for name in ["deck (1).pdf", "final.pptx", "talk.mp4"]:
        assert not (cloud / name).exists() and (bin_dir / name).is_file()   # moved, not deleted
    for name in ["deck.pdf", "final2.pptx", "notes.pdf", "long.mp4"]:
        assert (cloud / name).is_file()                              # kept copies + unique files stay
    log = (cleanup.Path(args.out) / "reports" / "cleanup.md").read_text(encoding="utf-8")
    assert "moved to" in log and "move it back to restore" in log
    assert cleanup.cleanup(local(args), True) == 0                   # second run: nothing new


def test_local_changed_file_is_not_moved(setup):
    args, cloud = setup
    (cloud / "talk.mp4").write_bytes(b"replaced after processing")
    cleanup.cleanup(local(args), True)
    assert (cloud / "talk.mp4").is_file()


def test_local_name_clash_in_recycle_folder(setup):
    args, cloud = setup
    bin_dir = cleanup.recycle_dir(cloud)
    bin_dir.mkdir()
    (bin_dir / "talk.mp4").write_bytes(b"an older recycled file")
    cleanup.cleanup(local(args), True)
    assert (bin_dir / "talk.mp4").read_bytes() == b"an older recycled file"   # never overwritten
    assert (bin_dir / "talk (2).mp4").is_file()


def test_watch_local_delete_originals_is_allowed(tmp_path, capsys):
    folder = tmp_path / "inbox"
    folder.mkdir()
    assert main(["--db", str(tmp_path / "d.db"), "--out", str(tmp_path / "out"),
                 "watch", str(folder), "--delete-originals", "--once", "--config", str(tmp_path / "none.toml")]) == 0
    assert "MOVED to" in capsys.readouterr().out
