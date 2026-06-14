"""Tests for the Apple Music refresh helper (mocked — no real osascript needed)."""
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lyrics_finder.itunes_sync import refresh_paths, refresh_library, is_available


def _fake_run(stdout="ok", stderr="", returncode=0):
    m = MagicMock()
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    return m


# ── macOS path ─────────────────────────────────────────────────────────────

class TestRefreshPaths:
    def test_refreshed_track_counted(self, tmp_path):
        p = tmp_path / "song.mp3"
        p.write_bytes(b"")
        with patch("lyrics_finder.itunes_sync._macos", return_value=True), \
             patch("subprocess.run", return_value=_fake_run("ok")):
            result = refresh_paths([p])
        assert result["available"] is True
        assert result["refreshed"] == 1
        assert result["not_found"] == 0
        assert result["errors"] == []

    def test_not_found_counted(self, tmp_path):
        p = tmp_path / "ghost.mp3"
        p.write_bytes(b"")
        with patch("lyrics_finder.itunes_sync._macos", return_value=True), \
             patch("subprocess.run", return_value=_fake_run("not found")):
            result = refresh_paths([p])
        assert result["not_found"] == 1
        assert result["refreshed"] == 0

    def test_multiple_paths(self, tmp_path):
        paths = []
        for i in range(3):
            p = tmp_path / f"song{i}.mp3"
            p.write_bytes(b"")
            paths.append(p)
        with patch("lyrics_finder.itunes_sync._macos", return_value=True), \
             patch("subprocess.run", return_value=_fake_run("ok")):
            result = refresh_paths(paths)
        assert result["refreshed"] == 3

    def test_timeout_goes_to_errors(self, tmp_path):
        p = tmp_path / "song.mp3"
        p.write_bytes(b"")
        with patch("lyrics_finder.itunes_sync._macos", return_value=True), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired("osascript", 60)):
            result = refresh_paths([p])
        assert result["errors"]
        assert "timed out" in result["errors"][0]

    def test_non_macos_returns_unavailable(self, tmp_path):
        p = tmp_path / "song.mp3"
        p.write_bytes(b"")
        with patch("lyrics_finder.itunes_sync._macos", return_value=False):
            result = refresh_paths([p])
        assert result["available"] is False
        assert result["refreshed"] == 0


class TestRefreshLibrary:
    def test_returns_ok_with_count(self):
        with patch("lyrics_finder.itunes_sync._macos", return_value=True), \
             patch("subprocess.run", return_value=_fake_run("42")):
            result = refresh_library()
        assert result["available"] is True
        assert result["ok"] is True
        assert result["track_count"] == 42

    def test_non_macos_unavailable(self):
        with patch("lyrics_finder.itunes_sync._macos", return_value=False):
            result = refresh_library()
        assert result["available"] is False
        assert result["ok"] is False

    def test_timeout_returns_error(self):
        with patch("lyrics_finder.itunes_sync._macos", return_value=True), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired("osascript", 60)):
            result = refresh_library()
        assert result["ok"] is False
        assert "timed out" in result["error"]


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
