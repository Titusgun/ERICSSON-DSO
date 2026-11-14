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


# ---------- rclone safety: remote type check + back-off after failures ----------

class FakeResult:
    def __init__(self, stdout):
        self.stdout = stdout
        self.returncode = 0


def test_remote_type(monkeypatch):
    listing = "onedrive:     opendrive\nwork:         onedrive\n"
    monkeypatch.setattr(watch.subprocess, "run", lambda *a, **k: FakeResult(listing))
    assert watch.remote_type("onedrive:DSO demo") == "opendrive"
    assert watch.remote_type("work:Shared/DSO") == "onedrive"
    assert watch.remote_type("missing:x") is None


def test_wrong_remote_type_stops_watch(tmp_path, monkeypatch, capsys):
    """The 13 Nov mistake: a remote called "onedrive" that is really OpenDrive."""
    monkeypatch.setattr(watch.shutil, "which", lambda name: "/usr/bin/rclone")
    monkeypatch.setattr(watch, "remote_type", lambda remote: "opendrive")
    assert main(["--db", str(tmp_path / "d.db"), "--out", str(tmp_path / "out"),
                 "watch", str(tmp_path / "in"), "--rclone", "onedrive:DSO demo", "--once"]) == 1
    printed = capsys.readouterr().out
    assert "type 'opendrive'" in printed and "storage: onedrive" in printed


def test_failed_downloads_back_off(tmp_path, monkeypatch):
    """After failures the next download waits 1x, 2x, 4x ... the interval (max 10 min)."""
    tries = []
    monkeypatch.setattr(watch, "download_new_files", lambda remote, folder: tries.append(1) or False)
    clock = [1000.0]
    monkeypatch.setattr(watch.time, "time", lambda: clock[0])
    args = Namespace(folder=str(tmp_path), out=str(tmp_path / "out"), rclone="onedrive:Demo", interval=30)
    state = {"failures": 0, "next_try": 0.0, "remote": "onedrive:Demo"}
    step, calls = recorder()
    for second in range(0, 300, 30):             # 10 checks, every 30 s
        clock[0] = 1000.0 + second
        watch.watch_cycle(args, [step], None, state)
    # tries at 0 s, 30 s (wait 30), 90 s (wait 60), 210 s (wait 120) -> 4, not 10
    assert len(tries) == 4
    watch.record_download(state, True, clock[0], 30)
    assert state["failures"] == 0
    for k in range(20):
        watch.record_download(state, False, 0.0, 30)
    assert state["next_try"] == watch.RCLONE_MAX_BACKOFF_SEC      # capped at 10 min



# ---------- what the next download brings ----------

DRY_RUN = """2026/11/13 22:40:35 NOTICE: Brief.png: Skipped copy as --dry-run is set (size 224.061Ki)
2026/11/13 22:40:35 NOTICE: talk.mp4: Skipped copy as --dry-run is set (size 1.5Gi)
2026/11/13 22:40:35 NOTICE:
Transferred:   	          0 B / 0 B, -, 0 B/s, ETA -
"""


def test_parse_dry_run():
    count, total = watch.parse_dry_run(DRY_RUN)
    assert count == 2
    assert total == round(224.061 * 1024) + round(1.5 * 1024 ** 3)
    assert watch.parse_dry_run("") == (0, 0)
    assert watch.parse_rclone_size("512") == 512 and watch.parse_rclone_size("2Mi") == 2 * 1024 ** 2


class Ran:
    def __init__(self, stderr=""):
        self.returncode = 0
        self.stderr = stderr
        self.stdout = ""


def test_nothing_new_means_no_download(monkeypatch, tmp_path, capsys):
    commands = []
    monkeypatch.setattr(watch.subprocess, "run", lambda command, **kw: commands.append(command) or Ran(""))
    assert watch.download_new_files("onedrive:Demo", tmp_path) is True
    assert len(commands) == 1 and "--dry-run" in commands[0]          # only the quick look
    assert capsys.readouterr().out == ""                              # and quiet


def test_new_files_are_announced_and_downloaded(monkeypatch, tmp_path, capsys):
    commands = []
    monkeypatch.setattr(watch.subprocess, "run", lambda command, **kw: commands.append(command) or Ran(DRY_RUN))
    assert watch.download_new_files("onedrive:Demo", tmp_path) is True
    assert len(commands) == 2 and "--stats-one-line" in commands[1]    # real download with progress
    assert "downloading 2 new/changed file(s), 1.5 GB" in capsys.readouterr().out


def test_changed_reports_are_uploaded(tmp_path, monkeypatch):
    """A `dso offboard` run while watch waits: the new report reaches the cloud."""
    uploads = []
    monkeypatch.setattr(watch, "run_rclone", lambda command: uploads.append(command) or True)
    out = tmp_path / "out"
    (out / "reports").mkdir(parents=True)
    (out / "reports" / "savings.md").write_text("old", encoding="utf-8")
    args = Namespace(out=str(out), upload=True)
    state = {}
    watch.upload_changed_reports(args, "onedrive:Demo", state)        # first check: only remembers
    assert uploads == []
    watch.upload_changed_reports(args, "onedrive:Demo", state)        # nothing changed
    assert uploads == []
    (out / "reports" / "offboarding.md").write_text("new leaver", encoding="utf-8")
    watch.upload_changed_reports(args, "onedrive:Demo", state)        # changed -> upload reports
    assert len(uploads) == 1 and uploads[0][-1] == "onedrive:Demo/_dso_distilled/reports"
