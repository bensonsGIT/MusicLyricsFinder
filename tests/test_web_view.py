"""Tests for the file viewer endpoints: embedded lyrics + artwork."""
from pathlib import Path

import pytest
from mutagen.id3 import ID3, TIT2, TPE1

from lyrics_finder import web
from lyrics_finder.metadata import write_artwork, write_lyrics

_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 32 + b"\xff\xd9"


def _make_mp3(path: Path, title="Song", artist="Artist"):
    frame = b"\xff\xfb\x90\x00" + b"\x00" * 413
    path.write_bytes(frame * 4)
    tags = ID3()
    tags["TIT2"] = TIT2(encoding=3, text=title)
    tags["TPE1"] = TPE1(encoding=3, text=artist)
    tags.save(str(path))


@pytest.fixture()
def client():
    web.app.config["TESTING"] = True
    return web.app.test_client()


# ── /api/file/lyrics ────────────────────────────────────────────────────────

def test_lyrics_returns_embedded(client, tmp_path):
    mp3 = tmp_path / "song.mp3"
    _make_mp3(mp3)
    write_lyrics(mp3, "Line one\nLine two")
    r = client.post("/api/file/lyrics", json={"path": str(mp3)})
    assert r.status_code == 200
    data = r.get_json()
    assert data["has_lyrics"] is True
    assert data["lyrics"] == "Line one\nLine two"


def test_lyrics_empty_when_none(client, tmp_path):
    mp3 = tmp_path / "song.mp3"
    _make_mp3(mp3)
    r = client.post("/api/file/lyrics", json={"path": str(mp3)})
    assert r.status_code == 200
    data = r.get_json()
    assert data["has_lyrics"] is False
    assert data["lyrics"] == ""


def test_lyrics_missing_file(client):
    r = client.post("/api/file/lyrics", json={"path": "/no/such/file.mp3"})
    assert r.status_code == 400


# ── /api/file/artwork ───────────────────────────────────────────────────────

def test_artwork_serves_image(client, tmp_path):
    mp3 = tmp_path / "song.mp3"
    _make_mp3(mp3)
    write_artwork(mp3, _JPEG, "image/jpeg")
    r = client.get("/api/file/artwork", query_string={"path": str(mp3)})
    assert r.status_code == 200
    assert r.mimetype == "image/jpeg"
    assert r.data == _JPEG


def test_artwork_404_when_none(client, tmp_path):
    mp3 = tmp_path / "song.mp3"
    _make_mp3(mp3)
    r = client.get("/api/file/artwork", query_string={"path": str(mp3)})
    assert r.status_code == 404


def test_artwork_missing_file(client):
    r = client.get("/api/file/artwork", query_string={"path": "/no/such/file.mp3"})
    assert r.status_code == 400
