import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from mutagen.id3 import ID3, TIT2, TPE1

from lyrics_finder import web
from lyrics_finder.artwork import ArtworkResult

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


def _post(client, url, payload):
    return client.post(url, json=payload)


def test_search_returns_results(client):
    fake = MagicMock()
    fake.search.return_value = [
        ArtworkResult("Queen", "A Night at the Opera", "", "thumb", "art_600", "album")
    ]
    with patch("lyrics_finder.web.ArtworkFinder", return_value=fake):
        r = _post(client, "/api/artwork/search", {"artist": "Queen", "album": "A Night at the Opera"})
    assert r.status_code == 200
    data = r.get_json()
    assert data["results"][0]["album"] == "A Night at the Opera"
    assert data["results"][0]["art_url"] == "art_600"


def test_search_requires_a_term(client):
    r = _post(client, "/api/artwork/search", {})
    assert r.status_code == 400


def test_apply_embeds_artwork(client, tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p)

    fake = MagicMock()
    fake.download.return_value = (_JPEG, "image/jpeg")
    with patch("lyrics_finder.web.ArtworkFinder", return_value=fake):
        r = _post(client, "/api/artwork/apply",
                  {"path": str(p), "art_url": "https://x/600x600bb.jpg"})

    assert r.status_code == 200
    assert r.get_json()["status"] == "applied"
    from lyrics_finder.metadata import read_metadata
    assert read_metadata(p)["has_artwork"] is True


def test_apply_missing_file(client):
    r = _post(client, "/api/artwork/apply", {"path": "/no/such/file.mp3", "art_url": "x"})
    assert r.status_code == 400


def _auto_finder():
    fake = MagicMock()
    fake.find.return_value = ArtworkResult(
        "Queen", "A Night at the Opera", "", "thumb_200", "art_600", "album")
    fake.download.return_value = (_JPEG, "image/jpeg")
    return fake


def test_auto_embeds_best_match(client, tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p)
    with patch("lyrics_finder.web.ArtworkFinder", return_value=_auto_finder()):
        r = _post(client, "/api/artwork/auto", {"path": str(p)})
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "success"
    assert data["thumb_url"] == "thumb_200"
    from lyrics_finder.metadata import read_metadata
    assert read_metadata(p)["has_artwork"] is True


def test_auto_skips_when_art_present(client, tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p)
    from lyrics_finder.metadata import write_artwork
    write_artwork(p, _JPEG, "image/jpeg")
    fake = _auto_finder()
    with patch("lyrics_finder.web.ArtworkFinder", return_value=fake):
        r = _post(client, "/api/artwork/auto", {"path": str(p)})
    assert r.get_json()["status"] == "skipped"
    fake.find.assert_not_called()


def test_auto_overwrite_replaces(client, tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p)
    from lyrics_finder.metadata import write_artwork
    write_artwork(p, b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, "image/png")
    with patch("lyrics_finder.web.ArtworkFinder", return_value=_auto_finder()):
        r = _post(client, "/api/artwork/auto", {"path": str(p), "overwrite": True})
    assert r.get_json()["status"] == "success"


def test_auto_not_found(client, tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p)
    fake = MagicMock()
    fake.find.return_value = None
    with patch("lyrics_finder.web.ArtworkFinder", return_value=fake):
        r = _post(client, "/api/artwork/auto", {"path": str(p)})
    assert r.get_json()["status"] == "not_found"


def test_auto_dry_run_does_not_write(client, tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p)
    fake = _auto_finder()
    with patch("lyrics_finder.web.ArtworkFinder", return_value=fake):
        r = _post(client, "/api/artwork/auto", {"path": str(p), "dry_run": True})
    assert r.get_json()["status"] == "success"
    fake.download.assert_not_called()
    from lyrics_finder.metadata import read_metadata
    assert read_metadata(p)["has_artwork"] is False


def test_clear_artwork(client, tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p)
    from lyrics_finder.metadata import write_artwork
    write_artwork(p, _JPEG, "image/jpeg")

    r = _post(client, "/api/artwork/clear", {"path": str(p)})
    assert r.status_code == 200
    assert r.get_json()["had_artwork"] is True
    from lyrics_finder.metadata import read_metadata
    assert read_metadata(p)["has_artwork"] is False


def test_clear_artwork_when_absent(client, tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p)
    r = _post(client, "/api/artwork/clear", {"path": str(p)})
    assert r.status_code == 200
    assert r.get_json()["had_artwork"] is False
