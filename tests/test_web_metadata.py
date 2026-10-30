"""Tests for the tag-editor endpoints and the iTunes tag lookup."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from mutagen.id3 import ID3, TALB, TIT2, TPE1

from lyrics_finder import web
from lyrics_finder.artwork import ArtworkFinder
from lyrics_finder.metadata import read_metadata


def _make_mp3(path: Path, title="Song", artist="Artist", album=""):
    frame = b"\xff\xfb\x90\x00" + b"\x00" * 413
    path.write_bytes(frame * 4)
    tags = ID3()
    tags["TIT2"] = TIT2(encoding=3, text=title)
    tags["TPE1"] = TPE1(encoding=3, text=artist)
    if album:
        tags["TALB"] = TALB(encoding=3, text=album)
    tags.save(str(path))


@pytest.fixture()
def client():
    web.app.config["TESTING"] = True
    return web.app.test_client()


# ── /api/file/metadata ──────────────────────────────────────────────────────

def test_read_returns_editable_fields(client, tmp_path):
    p = tmp_path / "a.mp3"
    _make_mp3(p, album="LP")
    r = client.post("/api/file/metadata", json={"path": str(p)})
    assert r.status_code == 200
    data = r.get_json()
    assert data["title"] == "Song"
    assert data["album"] == "LP"
    assert set(data) == set(web.EDITABLE_FIELDS)
    assert "lyrics" not in data


def test_read_missing_file(client):
    r = client.post("/api/file/metadata", json={"path": "/nope.mp3"})
    assert r.status_code == 400


# ── /api/file/metadata/update ───────────────────────────────────────────────

def test_update_single_file(client, tmp_path):
    p = tmp_path / "a.mp3"
    _make_mp3(p)
    r = client.post("/api/file/metadata/update",
                    json={"paths": [str(p)], "fields": {"genre": "Rock", "year": "1985"}})
    assert r.status_code == 200
    data = r.get_json()
    assert data["updated"][0]["genre"] == "Rock"
    m = read_metadata(p)
    assert (m["genre"], m["year"], m["title"]) == ("Rock", "1985", "Song")


def test_update_many_files(client, tmp_path):
    paths = []
    for i in range(3):
        p = tmp_path / f"{i}.mp3"
        _make_mp3(p, title=f"S{i}", album="Old")
        paths.append(str(p))
    r = client.post("/api/file/metadata/update",
                    json={"paths": paths, "fields": {"album": "New", "album_artist": "Band"}})
    assert r.status_code == 200
    assert len(r.get_json()["updated"]) == 3
    for i, p in enumerate(paths):
        m = read_metadata(Path(p))
        assert (m["album"], m["album_artist"], m["title"]) == ("New", "Band", f"S{i}")


def test_update_ignores_unknown_fields(client, tmp_path):
    p = tmp_path / "a.mp3"
    _make_mp3(p)
    r = client.post("/api/file/metadata/update",
                    json={"paths": [str(p)], "fields": {"lyrics": "x", "bogus": "y"}})
    assert r.status_code == 400
    assert read_metadata(p)["lyrics"] == ""


def test_update_reports_missing_files(client, tmp_path):
    p = tmp_path / "a.mp3"
    _make_mp3(p)
    r = client.post("/api/file/metadata/update",
                    json={"paths": [str(p), "/nope.mp3"], "fields": {"genre": "Pop"}})
    assert r.status_code == 200
    data = r.get_json()
    assert len(data["updated"]) == 1
    assert data["errors"][0]["path"] == "/nope.mp3"


# ── /api/metadata/lookup ────────────────────────────────────────────────────

def test_lookup_returns_tags(client):
    fake = MagicMock()
    fake.lookup_tags.return_value = {"title": "Song", "genre": "Pop"}
    with patch("lyrics_finder.web.ArtworkFinder", return_value=fake):
        r = client.post("/api/metadata/lookup", json={"title": "Song", "artist": "A"})
    assert r.get_json() == {"found": True, "tags": {"title": "Song", "genre": "Pop"}}


def test_lookup_requires_title(client):
    r = client.post("/api/metadata/lookup", json={"artist": "A"})
    assert r.status_code == 400


# ── ArtworkFinder.lookup_tags ───────────────────────────────────────────────

def _itunes(*items):
    m = MagicMock()
    m.json.return_value = {"results": list(items)}
    m.raise_for_status.return_value = None
    return m


def test_lookup_tags_picks_best_match():
    finder = ArtworkFinder()
    resp = _itunes(
        {"trackName": "Imagine", "artistName": "Cover Band", "collectionName": "Tribute",
         "primaryGenreName": "Pop", "releaseDate": "2010-01-01T00:00:00Z", "trackNumber": 9},
        {"trackName": "Imagine", "artistName": "John Lennon", "collectionName": "Imagine",
         "primaryGenreName": "Rock", "releaseDate": "1971-09-09T00:00:00Z",
         "trackNumber": 1, "trackCount": 10, "discNumber": 1, "discCount": 1},
    )
    with patch.object(finder._session, "get", return_value=resp):
        tags = finder.lookup_tags("Imagine", "John Lennon")
    assert tags["artist"] == "John Lennon"
    assert tags["genre"] == "Rock"
    assert tags["year"] == "1971"
    assert (tags["track_number"], tags["track_total"]) == ("1", "10")
    assert tags["album_artist"] == "John Lennon"


def test_lookup_tags_rejects_unrelated_title():
    finder = ArtworkFinder()
    resp = _itunes({"trackName": "Completely Different", "artistName": "X"})
    with patch.object(finder._session, "get", return_value=resp):
        assert finder.lookup_tags("Imagine", "John Lennon") is None
