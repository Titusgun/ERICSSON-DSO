"""Shared test setup: every test is cut off from the developer's real settings
and cloud account.

13/14 Nov: on a laptop with a real dso.toml (rclone = "onedrive:DSO demo",
upload, delete_originals), tests that call `dso watch` picked those settings
up and ran a REAL watch cycle against OneDrive: they downloaded the cloud
files and uploaded their test results into _dso_distilled. CI never saw it,
because CI has no dso.toml. Two guards make that impossible:
"""
import pytest


@pytest.fixture(autouse=True)
def isolated_from_real_settings(tmp_path, monkeypatch):
    # 1. Run in an empty temporary folder: `dso watch` looks for dso.toml in the
    #    current folder, and must never find the project's real one.
    monkeypatch.chdir(tmp_path)
    # 2. An empty rclone configuration: no real OneDrive / SharePoint remote
    #    exists for the tests, so even a test bug cannot reach a real cloud.
    monkeypatch.setenv("RCLONE_CONFIG", str(tmp_path / "no-real-remotes.conf"))
