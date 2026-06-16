"""Tests for the /api/ipod/* endpoints, against a fake Rockbox mount."""
from pathlib import Path

import pytest

from lyrics_finder import web


@pytest.fixture()
def client():
    web.app.config["TESTING"] = True
    return web.app.test_client()


@pytest.fixture()
def rockbox_mount(tmp_path):
    """A throwaway directory that looks like a mounted Rockbox iPod."""
    (tmp_path / ".rockbox").mkdir()
    (tmp_path / "iPod_Control" / "Music").mkdir(parents=True)
    return tmp_path


def _make_mp3(path: Path, title="Song", artist="Artist"):
    from mutagen.id3 import ID3, TIT2, TPE1
    frame = b"\xff\xfb\x90\x00" + b"\x00" * 413
    path.write_bytes(frame * 4)
    tags = ID3()
    tags["TIT2"] = TIT2(encoding=3, text=title)
    tags["TPE1"] = TPE1(encoding=3, text=artist)
    tags.save(str(path))


def _add_track(mount, title="My Song", artist="My Artist"):
    """Add a track to the mount, reading real ID3 tags off the source file."""
    from ipod_manager.detector import probe
    from ipod_manager.manager import IpodManager
    src = mount / "incoming.mp3"
    _make_mp3(src, title=title, artist=artist)
    m = probe(mount)
    mgr = IpodManager(m)
    return mgr.add_track(src)


def test_detect_no_ipod(client, tmp_path):
    r = client.post("/api/ipod/detect", json={"mount": str(tmp_path)})
    assert r.status_code == 404


def test_detect_finds_rockbox(client, rockbox_mount):
    r = client.post("/api/ipod/detect", json={"mount": str(rockbox_mount)})
    assert r.status_code == 200
    data = r.get_json()
    assert data["rockbox"] is True
    assert data["firmware"] == "Rockbox"
    assert data["track_count"] == 0


def test_tracks_empty(client, rockbox_mount):
    r = client.post("/api/ipod/tracks", json={"mount": str(rockbox_mount)})
    assert r.status_code == 200
    assert r.get_json()["tracks"] == []


def test_tracks_lists_added_track(client, rockbox_mount):
    _add_track(rockbox_mount, title="My Song", artist="My Artist")
    r = client.post("/api/ipod/tracks", json={"mount": str(rockbox_mount)})
    tracks = r.get_json()["tracks"]
    assert len(tracks) == 1
    assert tracks[0]["title"] == "My Song"
    assert tracks[0]["artist"] == "My Artist"


def test_add_via_json_path(client, rockbox_mount, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "ipod_manager.manager._read_audio_meta",
        lambda p: {"title": "Fake", "artist": "FA", "album": "", "genre": "",
                   "duration_ms": 1000, "bitrate": 128, "sample_rate": 44100,
                   "track_number": 0, "year": 0},
    )
    src = tmp_path / "upload.mp3"
    _make_mp3(src)
    r = client.post("/api/ipod/add", json={"mount": str(rockbox_mount), "path": str(src)})
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["track"]["title"] == "Fake"


def test_add_missing_file(client, rockbox_mount):
    r = client.post("/api/ipod/add", json={"mount": str(rockbox_mount), "path": "/no/such/file.mp3"})
    assert r.status_code == 400


def test_remove_track(client, rockbox_mount):
    track = _add_track(rockbox_mount)
    r = client.post("/api/ipod/remove", json={"mount": str(rockbox_mount), "track_id": track.track_id})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_remove_unknown_track(client, rockbox_mount):
    r = client.post("/api/ipod/remove", json={"mount": str(rockbox_mount), "track_id": 999})
    assert r.status_code == 404


def test_remove_requires_track_id(client, rockbox_mount):
    r = client.post("/api/ipod/remove", json={"mount": str(rockbox_mount)})
    assert r.status_code == 400


def test_track_lyrics_read_and_write(client, rockbox_mount):
    track = _add_track(rockbox_mount)

    r = client.post("/api/ipod/track-lyrics",
                     json={"mount": str(rockbox_mount), "track_id": track.track_id})
    assert r.get_json()["has_lyrics"] is False

    r = client.post("/api/ipod/track-lyrics",
                     json={"mount": str(rockbox_mount), "track_id": track.track_id,
                           "lyrics": "New lyrics"})
    assert r.get_json()["ok"] is True

    r = client.post("/api/ipod/track-lyrics",
                     json={"mount": str(rockbox_mount), "track_id": track.track_id})
    data = r.get_json()
    assert data["has_lyrics"] is True
    assert data["lyrics"] == "New lyrics"


def test_track_lyrics_unknown_track(client, rockbox_mount):
    r = client.post("/api/ipod/track-lyrics",
                     json={"mount": str(rockbox_mount), "track_id": 999})
    assert r.status_code == 404


def test_update_track_metadata(client, rockbox_mount):
    track = _add_track(rockbox_mount)
    r = client.post("/api/ipod/update-track",
                     json={"mount": str(rockbox_mount), "track_id": track.track_id,
                           "title": "Renamed", "year": 2020})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    tracks = client.post("/api/ipod/tracks", json={"mount": str(rockbox_mount)}).get_json()["tracks"]
    assert tracks[0]["title"] == "Renamed"
    assert tracks[0]["year"] == 2020


def test_update_track_unknown(client, rockbox_mount):
    r = client.post("/api/ipod/update-track",
                     json={"mount": str(rockbox_mount), "track_id": 999, "title": "X"})
    assert r.status_code == 404


def test_update_requires_track_id(client, rockbox_mount):
    r = client.post("/api/ipod/update-track", json={"mount": str(rockbox_mount), "title": "X"})
    assert r.status_code == 400


def test_legacy_update_endpoint(client, rockbox_mount):
    track = _add_track(rockbox_mount)
    r = client.post("/api/ipod/update",
                     json={"mount": str(rockbox_mount), "track_id": track.track_id, "genre": "Jazz"})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
