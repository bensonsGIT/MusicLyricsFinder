"""Smoke-tests for the iPod manager (no real device required)."""

import struct
import tempfile
from pathlib import Path

import pytest
from mutagen.id3 import ID3, TIT2, TPE1, TALB

from ipod_manager.itunesdb import iTunesDB, Track, MHBD, MHLT, MHIT, MHOD
from ipod_manager.detector import probe
from ipod_manager.manager import IpodManager, _file_type


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


# ------------------------------------------------------------------ push_file_tags_to_db

def _make_mp3(path: Path, title="", artist="", album="", lyrics=""):
    frame = b"\xff\xfb\x90\x00" + b"\x00" * 413
    path.write_bytes(frame * 4)
    tags = ID3()
    if title:
        tags["TIT2"] = TIT2(encoding=3, text=title)
    if artist:
        tags["TPE1"] = TPE1(encoding=3, text=artist)
    if album:
        tags["TALB"] = TALB(encoding=3, text=album)
    if lyrics:
        from mutagen.id3 import USLT
        tags["USLT::eng"] = USLT(encoding=3, lang="eng", desc="", text=lyrics)
    tags.save(str(path))


def _stock_mount(tmp_path: Path):
    db_dir = tmp_path / "iPod_Control" / "iTunes"
    db_dir.mkdir(parents=True)
    music_dir = tmp_path / "iPod_Control" / "Music" / "F00"
    music_dir.mkdir(parents=True)
    # probe() requires a non-empty iTunesDB file to recognise the device
    empty_db = iTunesDB()
    empty_db.write(db_dir / "iTunesDB")
    from ipod_manager.detector import probe
    return probe(tmp_path)


def test_push_tags_updates_lyrics_in_db(tmp_path):
    """Lyrics embedded in a file should be written to the DB entry."""
    mount = _stock_mount(tmp_path)

    # Create a real MP3 on the iPod path with lyrics embedded
    mp3_path = tmp_path / "iPod_Control" / "Music" / "F00" / "0001.mp3"
    _make_mp3(mp3_path, title="My Song", artist="My Artist", lyrics="Verse 1\nVerse 2")

    # Build a DB whose entry has no lyrics yet
    db = iTunesDB()
    db.tracks.append(Track(
        title="My Song", artist="My Artist",
        ipod_path=":iPod_Control:Music:F00:0001.mp3",
        lyrics="",
    ))
    db.write(mount.itunesdb_path)

    mgr = IpodManager(mount)
    updated, failed = mgr.push_file_tags_to_db()

    assert updated == 1
    assert failed == 0

    # Re-read the DB and confirm lyrics were written
    db2 = iTunesDB.read(mount.itunesdb_path)
    assert "Verse 1" in db2.tracks[0].lyrics


def test_push_tags_clears_lyrics_when_file_has_none(tmp_path):
    """If a file has no embedded lyrics, the DB entry's lyrics should be cleared."""
    mount = _stock_mount(tmp_path)

    mp3_path = tmp_path / "iPod_Control" / "Music" / "F00" / "0001.mp3"
    _make_mp3(mp3_path, title="T", artist="A")  # no lyrics

    db = iTunesDB()
    db.tracks.append(Track(
        title="T", artist="A",
        ipod_path=":iPod_Control:Music:F00:0001.mp3",
        lyrics="Old stale lyrics",
    ))
    db.write(mount.itunesdb_path)

    mgr = IpodManager(mount)
    mgr.push_file_tags_to_db()

    db2 = iTunesDB.read(mount.itunesdb_path)
    assert db2.tracks[0].lyrics == ""


def test_push_tags_counts_missing_files(tmp_path):
    """Tracks whose files don't exist on disk are counted as failed."""
    mount = _stock_mount(tmp_path)

    db = iTunesDB()
    db.tracks.append(Track(
        title="Ghost", ipod_path=":iPod_Control:Music:F00:ghost.mp3",
    ))
    db.write(mount.itunesdb_path)

    mgr = IpodManager(mount)
    updated, failed = mgr.push_file_tags_to_db()

    assert updated == 0
    assert failed == 1


def test_push_tags_selective_fields(tmp_path):
    """When fields={'lyrics'} only lyrics are synced; other tags stay."""
    mount = _stock_mount(tmp_path)

    mp3_path = tmp_path / "iPod_Control" / "Music" / "F00" / "0001.mp3"
    _make_mp3(mp3_path, title="File Title", artist="File Artist", lyrics="New lyrics")

    db = iTunesDB()
    db.tracks.append(Track(
        title="DB Title", artist="DB Artist",
        ipod_path=":iPod_Control:Music:F00:0001.mp3",
        lyrics="",
    ))
    db.write(mount.itunesdb_path)

    mgr = IpodManager(mount)
    mgr.push_file_tags_to_db(fields={"lyrics"})

    db2 = iTunesDB.read(mount.itunesdb_path)
    t = db2.tracks[0]
    assert "New lyrics" in t.lyrics
    # Title should NOT have been synced from the file
    assert t.title == "DB Title"
