"""Smoke-tests for the iPod manager (no real device required)."""

import struct
import tempfile
from pathlib import Path

import pytest

from ipod_manager.itunesdb import iTunesDB, Track, MHBD, MHLT, MHIT, MHOD
from ipod_manager.detector import probe
from ipod_manager.manager import _file_type


# ------------------------------------------------------------------ itunesdb

def _make_db_with_track() -> iTunesDB:
    db = iTunesDB()
    db.tracks.append(
        Track(
            title="Test Song",
            artist="Test Artist",
            album="Test Album",
            genre="Rock",
            ipod_path=":iPod_Control:Music:F00:0001.mp3",
            duration_ms=210_000,
            file_size=4_096_000,
            bitrate=320,
            sample_rate=44100,
            track_number=3,
            year=2024,
            file_type="MP3 ",
        )
    )
    return db


def test_build_and_parse_roundtrip(tmp_path):
    db = _make_db_with_track()
    db_file = tmp_path / "iTunesDB"
    db.write(db_file)

    assert db_file.exists()
    data = db_file.read_bytes()
    assert data[:4] == MHBD

    db2 = iTunesDB.read(db_file)
    assert len(db2.tracks) == 1
    t = db2.tracks[0]
    assert t.title == "Test Song"
    assert t.artist == "Test Artist"
    assert t.album == "Test Album"
    assert t.genre == "Rock"
    assert t.duration_ms == 210_000
    assert t.bitrate == 320
    assert t.sample_rate == 44100
    assert t.track_number == 3
    assert t.year == 2024
    assert t.ipod_path == ":iPod_Control:Music:F00:0001.mp3"


def test_track_ids_assigned(tmp_path):
    db = _make_db_with_track()
    db_file = tmp_path / "iTunesDB"
    db.write(db_file)

    db2 = iTunesDB.read(db_file)
    assert db2.tracks[0].track_id == 1


def test_multiple_tracks_roundtrip(tmp_path):
    db = iTunesDB()
    for i in range(5):
        db.tracks.append(
            Track(
                title=f"Song {i}",
                artist=f"Artist {i}",
                ipod_path=f":iPod_Control:Music:F0{i}:000{i}.mp3",
            )
        )
    db_file = tmp_path / "iTunesDB"
    db.write(db_file)

    db2 = iTunesDB.read(db_file)
    assert len(db2.tracks) == 5
    titles = [t.title for t in db2.tracks]
    for i in range(5):
        assert f"Song {i}" in titles


def test_empty_db(tmp_path):
    db = iTunesDB()
    db_file = tmp_path / "iTunesDB"
    db.write(db_file)

    db2 = iTunesDB.read(db_file)
    assert db2.tracks == []


def test_invalid_file_raises(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"THIS IS NOT AN ITUNESDB FILE")
    with pytest.raises(ValueError, match="mhbd"):
        iTunesDB.read(bad)


# ------------------------------------------------------------------ detector

def test_probe_returns_none_for_random_dir(tmp_path):
    assert probe(tmp_path) is None


def test_probe_detects_stock_firmware(tmp_path):
    db_path = tmp_path / "iPod_Control" / "iTunes"
    db_path.mkdir(parents=True)
    (db_path / "iTunesDB").write_bytes(b"\x00" * 4)  # won't be parsed by probe
    mount = probe(tmp_path)
    assert mount is not None
    assert not mount.rockbox
    assert mount.root == tmp_path


def test_probe_detects_rockbox(tmp_path):
    (tmp_path / ".rockbox").mkdir()
    mount = probe(tmp_path)
    assert mount is not None
    assert mount.rockbox


# ------------------------------------------------------------------ helpers

def test_file_type():
    assert _file_type(".mp3") == "MP3 "
    assert _file_type(".m4a") == "M4A "
    assert _file_type(".aac") == "AAC "
    assert _file_type(".flac") == "    "


def test_fmt_duration():
    from ipod_manager.cli import _fmt_duration as fmt
    assert fmt(0) == "-"
    assert fmt(60_000) == "1:00"
    assert fmt(3_661_000) == "61:01"


# ------------------------------------------------------------------ manager add/remove (no real audio)

def test_add_remove_rockbox(tmp_path, monkeypatch):
    """Add and remove a dummy file in Rockbox mode without real audio processing."""
    # Set up a fake Rockbox iPod structure
    (tmp_path / ".rockbox").mkdir()
    music_dir = tmp_path / "iPod_Control" / "Music"
    music_dir.mkdir(parents=True)

    from ipod_manager.detector import probe
    from ipod_manager.manager import IpodManager, _read_audio_meta

    # Patch _read_audio_meta so we don't need real audio files
    monkeypatch.setattr(
        "ipod_manager.manager._read_audio_meta",
        lambda p: {"title": "Fake", "artist": "FA", "album": "FB",
                   "genre": "", "duration_ms": 1000, "bitrate": 128,
                   "sample_rate": 44100, "track_number": 1, "year": 2024},
    )

    # Create a dummy .mp3 file (content doesn't matter since we patched meta reading)
    fake_mp3 = tmp_path / "song.mp3"
    fake_mp3.write_bytes(b"\xff\xfb" + b"\x00" * 100)

    mount = probe(tmp_path)
    assert mount is not None

    mgr = IpodManager(mount)
    track = mgr.add_track(fake_mp3)

    assert track.title == "Fake"
    assert track.artist == "FA"
    # File should exist on the iPod
    local = track.local_path(tmp_path)
    assert local.exists()

    # Remove it
    ok = mgr.remove_track(track.track_id)
    assert ok
    assert not local.exists()
