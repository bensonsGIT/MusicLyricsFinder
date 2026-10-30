"""Tests for the tag-editor endpoints and the iTunes tag lookup."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from mutagen.id3 import ID3, TALB, TIT2, TPE1

from lyrics_finder import web
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
    with patch("lyrics_finder.web.MusicBrainzTags", return_value=fake):
        r = client.post("/api/metadata/lookup", json={"title": "Song", "artist": "A"})
    assert r.get_json() == {"found": True, "tags": {"title": "Song", "genre": "Pop"}}


def test_lookup_requires_title(client):
    r = client.post("/api/metadata/lookup", json={"artist": "A"})
    assert r.status_code == 400


# ── /api/metadata/autofill ──────────────────────────────────────────────────

_SUGGESTED = {
    "title": "Song", "artist": "iTunes Artist", "album": "iTunes Album",
    "album_artist": "iTunes Artist", "genre": "Rock", "year": "1999",
    "track_number": "3", "track_total": "10", "disc_number": "1", "disc_total": "1",
}


def _fake_finder(tags=_SUGGESTED):
    fake = MagicMock()
    fake.lookup_tags.return_value = dict(tags) if tags else None
    return fake


def test_autofill_fills_only_empty_fields(client, tmp_path):
    p = tmp_path / "a.mp3"
    _make_mp3(p, artist="My Artist")  # artist set, album/genre/year empty
    with patch("lyrics_finder.web.MusicBrainzTags", return_value=_fake_finder()):
        r = client.post("/api/metadata/autofill", json={"path": str(p)})
    data = r.get_json()
    assert data["status"] == "filled"
    assert "artist" not in data["fields"]
    m = read_metadata(p)
    assert m["artist"] == "My Artist"          # never overwritten
    assert (m["album"], m["genre"], m["year"]) == ("iTunes Album", "Rock", "1999")
    assert (m["track_number"], m["track_total"]) == ("3", "10")
    assert m["title"] == "Song"


def test_autofill_asks_only_for_missing_fields(client, tmp_path):
    from lyrics_finder.metadata import write_metadata
    p = tmp_path / "a.mp3"
    _make_mp3(p, album="LP")
    write_metadata(p, genre="Jazz")
    fake = _fake_finder()
    with patch("lyrics_finder.web.MusicBrainzTags", return_value=fake):
        client.post("/api/metadata/autofill", json={"path": str(p)})
    need = fake.lookup_tags.call_args.kwargs["need"]
    assert "genre" not in need and "album" not in need and "artist" not in need
    assert "year" in need


def test_autofill_dry_run_writes_nothing(client, tmp_path):
    p = tmp_path / "a.mp3"
    _make_mp3(p)
    with patch("lyrics_finder.web.MusicBrainzTags", return_value=_fake_finder()):
        r = client.post("/api/metadata/autofill", json={"path": str(p), "dry_run": True})
    data = r.get_json()
    assert data["status"] == "filled" and data["dry_run"] is True
    assert data["fields"]["genre"] == "Rock"
    assert read_metadata(p)["genre"] == ""


def test_autofill_complete_file_skips_lookup(client, tmp_path):
    from lyrics_finder.metadata import write_metadata
    p = tmp_path / "a.mp3"
    _make_mp3(p, album="LP")
    write_metadata(p, album_artist="A", genre="Pop", year="2000", track_number="1",
                   track_total="9", disc_number="1", disc_total="1")
    fake = _fake_finder()
    with patch("lyrics_finder.web.MusicBrainzTags", return_value=fake):
        r = client.post("/api/metadata/autofill", json={"path": str(p)})
    assert r.get_json()["status"] == "complete"
    fake.lookup_tags.assert_not_called()


def test_autofill_not_found(client, tmp_path):
    p = tmp_path / "a.mp3"
    _make_mp3(p)
    with patch("lyrics_finder.web.MusicBrainzTags", return_value=_fake_finder(None)):
        r = client.post("/api/metadata/autofill", json={"path": str(p)})
    assert r.get_json()["status"] == "not_found"
    assert read_metadata(p)["genre"] == ""


def test_autofill_skips_file_without_title(client, tmp_path):
    p = tmp_path / "a.mp3"
    _make_mp3(p, title="")
    fake = _fake_finder()
    with patch("lyrics_finder.web.MusicBrainzTags", return_value=fake):
        r = client.post("/api/metadata/autofill", json={"path": str(p)})
    assert r.get_json()["status"] == "skipped"
    fake.lookup_tags.assert_not_called()
