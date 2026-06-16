"""Tests for the /api/scan and /api/process endpoints."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from mutagen.id3 import ID3, TIT2, TPE1

from lyrics_finder import web


def _make_mp3(path: Path, title="Song", artist="Artist"):
    frame = b"\xff\xfb\x90\x00" + b"\x00" * 413
    path.write_bytes(frame * 4)
    tags = ID3()
    if title:
        tags["TIT2"] = TIT2(encoding=3, text=title)
    if artist:
        tags["TPE1"] = TPE1(encoding=3, text=artist)
    tags.save(str(path))


@pytest.fixture()
def client():
    web.app.config["TESTING"] = True
    return web.app.test_client()


# ── /api/scan ────────────────────────────────────────────────────────────

def test_scan_lists_supported_files(client, tmp_path):
    _make_mp3(tmp_path / "song1.mp3", title="Song 1")
    _make_mp3(tmp_path / "song2.mp3", title="Song 2")
    (tmp_path / "notes.txt").write_text("not audio")

    r = client.post("/api/scan", json={"directory": str(tmp_path)})
    assert r.status_code == 200
    data = r.get_json()
    assert data["count"] == 2
    titles = {f["title"] for f in data["files"]}
    assert titles == {"Song 1", "Song 2"}


def test_scan_reports_lyrics_and_artwork_flags(client, tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p)
    from lyrics_finder.metadata import write_lyrics
    write_lyrics(p, "La la la")

    r = client.post("/api/scan", json={"directory": str(tmp_path)})
    data = r.get_json()
    assert data["files"][0]["has_lyrics"] is True
    assert data["files"][0]["has_artwork"] is False
    assert data["files"][0]["art_thumb"] is None


def test_scan_requires_directory(client):
    r = client.post("/api/scan", json={})
    assert r.status_code == 400


def test_scan_rejects_missing_path(client, tmp_path):
    r = client.post("/api/scan", json={"directory": str(tmp_path / "nope")})
    assert r.status_code == 400


def test_scan_rejects_non_directory(client, tmp_path):
    f = tmp_path / "file.mp3"
    _make_mp3(f)
    r = client.post("/api/scan", json={"directory": str(f)})
    assert r.status_code == 400


# ── /api/process ─────────────────────────────────────────────────────────

def _patched_finder(lyrics="Found lyrics", source="lrclib"):
    fake = MagicMock()
    fake.find.return_value = (lyrics, source)
    return fake


def test_process_writes_lyrics(client, tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p)
    with patch("lyrics_finder.web.LyricsFinder", return_value=_patched_finder()):
        r = client.post("/api/process", json={"path": str(p)})
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "success"
    assert data["source"] == "lrclib"
    from lyrics_finder.metadata import read_metadata
    assert read_metadata(p)["lyrics"] == "Found lyrics"


def test_process_skips_when_lyrics_present(client, tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p)
    from lyrics_finder.metadata import write_lyrics
    write_lyrics(p, "Already here")
    with patch("lyrics_finder.web.LyricsFinder", return_value=_patched_finder()) as ctor:
        r = client.post("/api/process", json={"path": str(p)})
    data = r.get_json()
    assert data["status"] == "skipped"
    assert data["reason"] == "already_has_lyrics"
    ctor.assert_not_called()


def test_process_overwrite_replaces_lyrics(client, tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p)
    from lyrics_finder.metadata import write_lyrics
    write_lyrics(p, "Old lyrics")
    with patch("lyrics_finder.web.LyricsFinder", return_value=_patched_finder("New lyrics")):
        r = client.post("/api/process", json={"path": str(p), "overwrite": True})
    assert r.get_json()["status"] == "success"
    from lyrics_finder.metadata import read_metadata
    assert read_metadata(p)["lyrics"] == "New lyrics"


def test_process_not_found(client, tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p)
    fake = MagicMock()
    fake.find.return_value = (None, None)
    with patch("lyrics_finder.web.LyricsFinder", return_value=fake):
        r = client.post("/api/process", json={"path": str(p)})
    assert r.get_json()["status"] == "not_found"


def test_process_dry_run_does_not_write(client, tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p)
    with patch("lyrics_finder.web.LyricsFinder", return_value=_patched_finder()):
        r = client.post("/api/process", json={"path": str(p), "dry_run": True})
    assert r.get_json()["status"] == "success"
    from lyrics_finder.metadata import read_metadata
    assert read_metadata(p)["lyrics"] == ""


def test_process_missing_file(client):
    r = client.post("/api/process", json={"path": "/no/such/file.mp3"})
    assert r.status_code == 400


def test_process_no_title(client, tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p, title="", artist="")
    r = client.post("/api/process", json={"path": str(p)})
    data = r.get_json()
    assert data["status"] == "skipped"
    assert data["reason"] == "no_title"
