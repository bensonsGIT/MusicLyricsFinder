import shutil
import struct
import tempfile
from pathlib import Path

import pytest
from mutagen.id3 import ID3, TIT2, TPE1, TALB
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4

from lyrics_finder.metadata import clear_lyrics, read_metadata, write_lyrics


# ---------------------------------------------------------------------------
# Helpers to create minimal valid audio files
# ---------------------------------------------------------------------------

def _make_mp3(path: Path, title="", artist="", album="", lyrics=""):
    """Create a minimal valid MP3 with ID3 tags using a real silent frame."""
    # A valid 128kbps silent MPEG1 Layer3 frame (417 bytes of silence)
    header = b"\xff\xfb\x90\x00"  # sync + MPEG1, L3, 128k, 44100, stereo
    frame = header + b"\x00" * 413
    path.write_bytes(frame * 4)  # a few frames so mutagen is happy

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


def _make_m4a(path: Path, title="", artist="", album="", lyrics=""):
    """Create a minimal valid M4A container using mutagen."""
    # Write a minimal ftyp + moov box so mutagen can open it
    # Use mutagen's MP4 writer by saving an empty file first
    _write_minimal_mp4(path)
    audio = MP4(path)
    audio.add_tags()
    if title:
        audio.tags["\xa9nam"] = [title]
    if artist:
        audio.tags["\xa9ART"] = [artist]
    if album:
        audio.tags["\xa9alb"] = [album]
    if lyrics:
        audio.tags["\xa9lyr"] = [lyrics]
    audio.save()


def _write_minimal_mp4(path: Path):
    """Write a bare-minimum MP4/M4A binary that mutagen can parse."""
    def box(name: bytes, data: bytes = b"") -> bytes:
        size = 8 + len(data)
        return struct.pack(">I", size) + name + data

    ftyp = box(b"ftyp", b"M4A " + struct.pack(">I", 0) + b"M4A isom")
    # minimal moov with mvhd
    mvhd_data = (
        b"\x00"  # version
        + b"\x00\x00\x00"  # flags
        + b"\x00\x00\x00\x00"  # creation time
        + b"\x00\x00\x00\x00"  # modification time
        + b"\x00\x00\x03\xe8"  # time scale (1000)
        + b"\x00\x00\x00\x00"  # duration
        + b"\x00\x01\x00\x00"  # rate
        + b"\x01\x00"  # volume
        + b"\x00" * 70  # reserved + matrix + pre-defined
        + b"\x00\x00\x00\x03"  # next track id
    )
    mvhd = box(b"mvhd", mvhd_data)
    moov = box(b"moov", mvhd)
    path.write_bytes(ftyp + moov)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp(tmp_path):
    return tmp_path


# ---------------------------------------------------------------------------
# MP3 tests
# ---------------------------------------------------------------------------

class TestMP3Metadata:
    def test_read_title_artist(self, tmp):
        p = tmp / "song.mp3"
        _make_mp3(p, title="Hello", artist="World", album="Album1")
        meta = read_metadata(p)
        assert meta["title"] == "Hello"
        assert meta["artist"] == "World"
        assert meta["album"] == "Album1"
        assert meta["lyrics"] == ""

    def test_read_lyrics(self, tmp):
        p = tmp / "song.mp3"
        _make_mp3(p, title="T", artist="A", lyrics="Line 1\nLine 2")
        meta = read_metadata(p)
        assert "Line 1" in meta["lyrics"]

    def test_write_and_read_lyrics(self, tmp):
        p = tmp / "song.mp3"
        _make_mp3(p, title="T", artist="A")
        write_lyrics(p, "Verse 1\nVerse 2")
        meta = read_metadata(p)
        assert "Verse 1" in meta["lyrics"]

    def test_overwrite_lyrics(self, tmp):
        p = tmp / "song.mp3"
        _make_mp3(p, title="T", artist="A", lyrics="Old lyrics")
        write_lyrics(p, "New lyrics")
        meta = read_metadata(p)
        assert "New lyrics" in meta["lyrics"]
        assert "Old" not in meta["lyrics"]

    def test_write_does_not_corrupt_tags(self, tmp):
        p = tmp / "song.mp3"
        _make_mp3(p, title="My Song", artist="My Artist", album="My Album")
        write_lyrics(p, "Some lyrics")
        meta = read_metadata(p)
        assert meta["title"] == "My Song"
        assert meta["artist"] == "My Artist"


# ---------------------------------------------------------------------------
# M4A tests
# ---------------------------------------------------------------------------

class TestM4AMetadata:
    def test_read_title_artist(self, tmp):
        p = tmp / "song.m4a"
        _make_m4a(p, title="Hello", artist="World", album="Album1")
        meta = read_metadata(p)
        assert meta["title"] == "Hello"
        assert meta["artist"] == "World"

    def test_write_and_read_lyrics(self, tmp):
        p = tmp / "song.m4a"
        _make_m4a(p, title="T", artist="A")
        write_lyrics(p, "M4A Verse\nLine 2")
        meta = read_metadata(p)
        assert "M4A Verse" in meta["lyrics"]

    def test_overwrite_lyrics(self, tmp):
        p = tmp / "song.m4a"
        _make_m4a(p, title="T", artist="A", lyrics="Old lyrics")
        write_lyrics(p, "New lyrics")
        meta = read_metadata(p)
        assert "New lyrics" in meta["lyrics"]

    def test_aac_extension(self, tmp):
        p = tmp / "song.aac"
        _make_m4a(p, title="ACC Song", artist="Artist")
        write_lyrics(p, "AAC lyrics")
        meta = read_metadata(p)
        assert "AAC lyrics" in meta["lyrics"]


# ---------------------------------------------------------------------------
# Clear lyrics
# ---------------------------------------------------------------------------

class TestClearLyrics:
    def test_clear_mp3(self, tmp_path):
        p = tmp_path / "song.mp3"
        _make_mp3(p, title="T", artist="A", lyrics="Some lyrics")
        assert clear_lyrics(p) is True
        meta = read_metadata(p)
        assert meta["lyrics"] == ""
        assert meta["title"] == "T"  # other tags untouched

    def test_clear_mp3_without_lyrics(self, tmp_path):
        p = tmp_path / "song.mp3"
        _make_mp3(p, title="T", artist="A")
        assert clear_lyrics(p) is False

    def test_clear_m4a(self, tmp_path):
        p = tmp_path / "song.m4a"
        _make_m4a(p, title="T", artist="A", lyrics="Some lyrics")
        assert clear_lyrics(p) is True
        meta = read_metadata(p)
        assert meta["lyrics"] == ""
        assert meta["title"] == "T"

    def test_clear_m4a_without_lyrics(self, tmp_path):
        p = tmp_path / "song.m4a"
        _make_m4a(p, title="T", artist="A")
        assert clear_lyrics(p) is False

    def test_clear_unsupported_raises(self, tmp_path):
        p = tmp_path / "song.flac"
        p.write_bytes(b"\x00" * 100)
        with pytest.raises(ValueError, match="Unsupported"):
            clear_lyrics(p)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_unsupported_format_raises(tmp_path):
    p = tmp_path / "song.flac"
    p.write_bytes(b"\x00" * 100)
    with pytest.raises(ValueError, match="Unsupported"):
        read_metadata(p)
