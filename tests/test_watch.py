"""Tests for `dso watch` (dso/watch.py): change detection, when the pipeline runs,
rclone commands, and one real end-to-end run on the fixture documents."""
import shutil
from argparse import Namespace
from pathlib import Path

import pytest

from dso import watch
from dso.cli import main
from dso.common.db import connect

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """Tests do not need to wait for the folder to become stable."""
    monkeypatch.setattr(watch, "STABLE_WAIT_SEC", 0)


def recorder():
    """A fake pipeline step that remembers how often it ran."""
    calls = []

    def step(args):
        calls.append(args.folder)
        return 0
    return step, calls


def test_snapshot_and_describe_changes(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"one")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.mp4").write_bytes(b"two")
    first = watch.snapshot(tmp_path)
    assert set(first) == {"a.pdf", "sub/b.mp4"}
    (tmp_path / "a.pdf").write_bytes(b"one, but longer now")
    (tmp_path / "c.pptx").write_bytes(b"three")
    (tmp_path / "sub" / "b.mp4").unlink()
    assert watch.describe_changes(first, watch.snapshot(tmp_path)) == "1 new, 1 changed, 1 removed"


def test_pipeline_runs_only_when_something_changed(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"one")
    args = Namespace(folder=str(tmp_path), out=str(tmp_path / "out"))
    step, calls = recorder()
    last = watch.watch_cycle(args, [step], None)       # first check: runs
    last = watch.watch_cycle(args, [step], last)       # nothing new: does not run
    assert len(calls) == 1
    (tmp_path / "b.pdf").write_bytes(b"new upload")
    watch.watch_cycle(args, [step], last)              # new file: runs again
    assert len(calls) == 2


def test_waits_while_files_are_still_being_copied(tmp_path, monkeypatch):
    snapshots = [{"video.mp4": (100, 1)}, {"video.mp4": (5000, 2)}]   # still growing
    monkeypatch.setattr(watch, "snapshot", lambda folder: snapshots.pop(0))
    args = Namespace(folder=str(tmp_path), out=str(tmp_path / "out"))
    step, calls = recorder()
    assert watch.watch_cycle(args, [step], None) is None
    assert calls == []


def test_failed_run_is_retried_at_the_next_check(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"one")
    args = Namespace(folder=str(tmp_path), out=str(tmp_path / "out"))
    assert watch.watch_cycle(args, [lambda a: 1], None) is None        # failed -> nothing remembered
    assert watch.watch_cycle(args, [lambda a: 1 / 0], None) is None    # crashed -> still watching


def test_rclone_commands():
    download = watch.rclone_download_command("onedrive:Demo", Path("data/inbox"))
    # str(Path(...)) uses "\\" on Windows, so compare with the same conversion.
    assert download == ["rclone", "copy", "onedrive:Demo", str(Path("data/inbox")),
                        "--exclude", "_dso_distilled/**"]
    upload = watch.rclone_upload_commands(Path("out"), "onedrive:Demo/")
    assert upload[0] == ["rclone", "copy", str(Path("out") / "export"), "onedrive:Demo/_dso_distilled/export"]
    assert upload[1] == ["rclone", "copy", str(Path("out") / "reports"), "onedrive:Demo/_dso_distilled/reports"]


def test_upload_needs_rclone_remote(tmp_path):
    assert main(["--db", str(tmp_path / "d.db"), "--out", str(tmp_path / "out"),
                 "watch", str(tmp_path / "in"), "--upload", "--once"]) == 1


def test_watch_end_to_end_on_fixture_documents(tmp_path):
    """`dso watch --once` on a folder with PDFs runs the whole pipeline; a new
    "(1)" copy dropped in later is found as an exact duplicate."""
    inbox = tmp_path / "inbox"
    shutil.copytree(FIXTURES / "docs", inbox)
    (inbox / "deck_a (1).pdf").unlink()
    base = ["--db", str(tmp_path / "dso.db"), "--out", str(tmp_path / "out")]
    assert main(base + ["watch", str(inbox), "--once"]) == 0
    conn = connect(tmp_path / "dso.db")
    assert conn.execute("SELECT COUNT(*) FROM slides").fetchone()[0] == 9
    assert conn.execute("SELECT COUNT(*) FROM duplicates WHERE kind = 'file_exact'").fetchone()[0] == 0
    conn.close()
    assert (tmp_path / "out" / "reports" / "savings.md").is_file()

    shutil.copy(inbox / "deck_a.pdf", inbox / "deck_a (1).pdf")            # someone uploads a copy
    assert main(base + ["watch", str(inbox), "--once"]) == 0
    conn = connect(tmp_path / "dso.db")
    assert conn.execute("SELECT COUNT(*) FROM duplicates WHERE kind = 'file_exact'").fetchone()[0] == 1
    conn.close()
