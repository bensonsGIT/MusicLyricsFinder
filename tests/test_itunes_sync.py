"""Tests for the Apple Music force-refresh helper (mocked — no real osascript)."""
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from lyrics_finder.itunes_sync import force_refresh_paths, refresh_library, is_available


def _fake_run(stdout="1,0,0", stderr="", returncode=0):
    from unittest.mock import MagicMock
    m = MagicMock()
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    return m


# ── force_refresh_paths ────────────────────────────────────────────────────

class TestForceRefreshPaths:
    def test_refreshed_count_parsed(self, tmp_path):
        p = tmp_path / "song.mp3"
        p.write_bytes(b"")
        with patch("lyrics_finder.itunes_sync._macos", return_value=True), \
             patch("subprocess.run", return_value=_fake_run("1,0,0")):
            result = force_refresh_paths([p])
        assert result["available"] is True
        assert result["refreshed"] == 1
        assert result["added"] == 0
        assert result["errors"] == 0

    def test_added_count_parsed(self, tmp_path):
        p = tmp_path / "new.mp3"
        p.write_bytes(b"")
        with patch("lyrics_finder.itunes_sync._macos", return_value=True), \
             patch("subprocess.run", return_value=_fake_run("0,1,0")):
            result = force_refresh_paths([p])
        assert result["added"] == 1
        assert result["refreshed"] == 0

    def test_error_count_parsed(self, tmp_path):
        p = tmp_path / "bad.mp3"
        p.write_bytes(b"")
        with patch("lyrics_finder.itunes_sync._macos", return_value=True), \
             patch("subprocess.run", return_value=_fake_run("0,0,1")):
            result = force_refresh_paths([p])
        assert result["errors"] == 1

    def test_multiple_paths(self, tmp_path):
        paths = []
        for i in range(3):
            p = tmp_path / f"song{i}.mp3"
            p.write_bytes(b"")
            paths.append(p)
        with patch("lyrics_finder.itunes_sync._macos", return_value=True), \
             patch("subprocess.run", return_value=_fake_run("3,0,0")):
            result = force_refresh_paths(paths)
        assert result["refreshed"] == 3

    def test_touches_files(self, tmp_path):
        """Verify os.utime is called on each file before the script runs."""
        p = tmp_path / "song.mp3"
        p.write_bytes(b"")
        touched = []
        real_utime = __import__("os").utime

        def fake_utime(path, times):
            touched.append(path)

        with patch("lyrics_finder.itunes_sync._macos", return_value=True), \
             patch("subprocess.run", return_value=_fake_run("1,0,0")), \
             patch("os.utime", side_effect=fake_utime):
            force_refresh_paths([p])

        assert len(touched) == 1

    def test_skips_non_existing_files(self, tmp_path):
        ghost = tmp_path / "ghost.mp3"  # not created
        with patch("lyrics_finder.itunes_sync._macos", return_value=True):
            result = force_refresh_paths([ghost])
        assert result["errors"] == 1

    def test_timeout_returns_error(self, tmp_path):
        p = tmp_path / "song.mp3"
        p.write_bytes(b"")
        with patch("lyrics_finder.itunes_sync._macos", return_value=True), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired("osascript", 120)):
            result = force_refresh_paths([p])
        assert "timed out" in result.get("error_msg", "")
        assert result["errors"] == 1

    def test_non_macos_returns_unavailable(self, tmp_path):
        p = tmp_path / "song.mp3"
        p.write_bytes(b"")
        with patch("lyrics_finder.itunes_sync._macos", return_value=False):
            result = force_refresh_paths([p])
        assert result["available"] is False


# ── refresh_library ────────────────────────────────────────────────────────

class TestRefreshLibrary:
    def test_collects_paths_and_refreshes(self):
        collect_out = "/tmp/a.mp3\n/tmp/b.mp3\n"
        refresh_out = "2,0,0"
        calls = iter([_fake_run(collect_out), _fake_run(refresh_out)])
        with patch("lyrics_finder.itunes_sync._macos", return_value=True), \
             patch("subprocess.run", side_effect=lambda *a, **kw: next(calls)), \
             patch("lyrics_finder.itunes_sync._touch"):
            result = refresh_library()
        assert result["available"] is True
        assert result["track_count"] == 2

    def test_empty_library(self):
        with patch("lyrics_finder.itunes_sync._macos", return_value=True), \
             patch("subprocess.run", return_value=_fake_run("")):
            result = refresh_library()
        assert result["ok"] is True
        assert result["track_count"] == 0

    def test_non_macos_unavailable(self):
        with patch("lyrics_finder.itunes_sync._macos", return_value=False):
            result = refresh_library()
        assert result["available"] is False


# ── is_available ───────────────────────────────────────────────────────────

class TestIsAvailable:
    def test_false_on_non_macos(self):
        with patch("lyrics_finder.itunes_sync._macos", return_value=False):
            assert is_available() is False

    def test_true_when_music_responds(self):
        with patch("lyrics_finder.itunes_sync._macos", return_value=True), \
             patch("subprocess.run", return_value=_fake_run("Library")):
            assert is_available() is True

    def test_false_when_music_errors(self):
        with patch("lyrics_finder.itunes_sync._macos", return_value=True), \
             patch("subprocess.run", side_effect=OSError("not found")):
            assert is_available() is False
