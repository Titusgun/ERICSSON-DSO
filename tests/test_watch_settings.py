"""Tests for `dso watch` settings from dso.toml (dso/cli.py): file values are
used when no flag is typed, typed flags win, built-in defaults are safe."""
import shutil
from pathlib import Path

from dso import cli, watch

EXAMPLE = Path(__file__).parent.parent / "dso.example.toml"


def captured_args(monkeypatch, argv):
    """Runs `dso ...` but stops before watching; returns the final settings."""
    seen = {}

    def fake_run_watch(args, steps, cleanup=None):
        seen["args"] = args
        return 0
    monkeypatch.setattr(watch, "run_watch", fake_run_watch)
    result = cli.main(argv)
    return result, seen.get("args")


def test_example_file_is_used_without_flags(tmp_path, monkeypatch):
    config = tmp_path / "dso.toml"
    shutil.copy(EXAMPLE, config)
    result, args = captured_args(monkeypatch, ["watch", "--config", str(config)])
    assert result == 0
    assert args.folder == "data/cloud" and args.rclone == "onedrive:DSO demo"
    assert args.interval == 10 and args.max_seconds == 120 and args.source == "onedrive"
    assert args.upload is True and args.delete_originals is True
    assert args.model == "small" and args.force is False          # not in the file -> built-in


def test_typed_flags_win(tmp_path, monkeypatch):
    config = tmp_path / "dso.toml"
    shutil.copy(EXAMPLE, config)
    result, args = captured_args(monkeypatch, ["watch", "data/inbox", "--config", str(config),
                                               "--interval", "60", "--max-seconds", "0"])
    assert args.folder == "data/inbox" and args.interval == 60
    assert args.max_seconds is None                               # 0 = whole video
    assert args.delete_originals is True                          # still from the file


def test_without_a_file_the_defaults_are_safe(tmp_path, monkeypatch):
    result, args = captured_args(monkeypatch, ["watch", "data/inbox", "--config", str(tmp_path / "none.toml")])
    assert args.upload is False and args.delete_originals is False and args.rclone is None
    assert args.interval == 30 and args.model == "small"


def test_no_folder_anywhere_is_an_error(tmp_path, monkeypatch, capsys):
    result, args = captured_args(monkeypatch, ["watch", "--config", str(tmp_path / "none.toml")])
    assert result == 1 and args is None
    assert "which folder?" in capsys.readouterr().out


def test_typo_in_the_file_is_reported(tmp_path, monkeypatch, capsys):
    config = tmp_path / "dso.toml"
    config.write_text('[watch]\nfolder = "data/x"\ninterval = \n', encoding="utf-8")
    result, args = captured_args(monkeypatch, ["watch", "--config", str(config)])
    assert result == 1 and "cannot read" in capsys.readouterr().out


def test_unknown_setting_is_reported(tmp_path, monkeypatch, capsys):
    config = tmp_path / "dso.toml"
    config.write_text('[watch]\nfolder = "data/x"\ndelete_orignals = true\n', encoding="utf-8")
    result, args = captured_args(monkeypatch, ["watch", "--config", str(config)])
    assert "unknown setting 'delete_orignals'" in capsys.readouterr().out
    assert args.delete_originals is False                         # the typo does not switch it on
