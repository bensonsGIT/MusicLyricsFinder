import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from mutagen.id3 import ID3, TIT2, TPE1
from mutagen.mp4 import MP4

from lyrics_finder.cli import main, _collect_files


# ---------------------------------------------------------------------------
# Minimal file helpers (duplicated from test_metadata for independence)
# ---------------------------------------------------------------------------

def _make_mp3(path: Path, title="Song", artist="Artist"):
    header = b"\xff\xfb\x90\x00"
    frame = header + b"\x00" * 413
    path.write_bytes(frame * 4)
    tags = ID3()
    tags["TIT2"] = TIT2(encoding=3, text=title)
    tags["TPE1"] = TPE1(encoding=3, text=artist)
    tags.save(str(path))


def _write_minimal_mp4(path: Path):
    def box(name: bytes, data: bytes = b"") -> bytes:
        return struct.pack(">I", 8 + len(data)) + name + data

    ftyp = box(b"ftyp", b"M4A " + struct.pack(">I", 0) + b"M4A isom")
    mvhd_data = (
        b"\x00" + b"\x00\x00\x00" + b"\x00\x00\x00\x00" + b"\x00\x00\x00\x00"
        + b"\x00\x00\x03\xe8" + b"\x00\x00\x00\x00" + b"\x00\x01\x00\x00"
        + b"\x01\x00" + b"\x00" * 70 + b"\x00\x00\x00\x03"
    )
    moov = box(b"moov", box(b"mvhd", mvhd_data))
    path.write_bytes(ftyp + moov)


def _make_m4a(path: Path, title="Song", artist="Artist"):
    _write_minimal_mp4(path)
    audio = MP4(path)
    audio.add_tags()
    audio.tags["\xa9nam"] = [title]
    audio.tags["\xa9ART"] = [artist]
    audio.save()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_client(lyrics="Test lyrics\nLine 2"):
    client = MagicMock()
    client.search_track.return_value = [
        {"track": {"track_id": 1, "track_name": "Song", "artist_name": "Artist"}}
    ]
    client.get_lyrics.return_value = lyrics
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_collect_files_from_dir(tmp_path):
    (tmp_path / "a.mp3").write_bytes(b"")
    (tmp_path / "b.m4a").write_bytes(b"")
    (tmp_path / "c.txt").write_bytes(b"")
    files = _collect_files([str(tmp_path)])
    names = {f.name for f in files}
    assert "a.mp3" in names
    assert "b.m4a" in names
    assert "c.txt" not in names


def test_collect_files_single_file(tmp_path):
    p = tmp_path / "song.mp3"
    p.write_bytes(b"")
    files = _collect_files([str(p)])
    assert files == [p]


def test_no_api_key_exits(capsys):
    with pytest.raises(SystemExit):
        main(["some_file.mp3"])


def test_no_files_found(tmp_path, capsys):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    rc = main(["--api-key", "key", str(empty_dir)])
    assert rc == 1


def test_mp3_lyrics_written(tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p)
    client = _mock_client()

    with patch("lyrics_finder.cli.MusixmatchClient", return_value=client):
        rc = main(["--api-key", "key", str(p)])

    assert rc == 0
    from lyrics_finder.metadata import read_metadata
    meta = read_metadata(p)
    assert "Test lyrics" in meta["lyrics"]


def test_m4a_lyrics_written(tmp_path):
    p = tmp_path / "song.m4a"
    _make_m4a(p)
    client = _mock_client()

    with patch("lyrics_finder.cli.MusixmatchClient", return_value=client):
        rc = main(["--api-key", "key", str(p)])

    assert rc == 0
    from lyrics_finder.metadata import read_metadata
    meta = read_metadata(p)
    assert "Test lyrics" in meta["lyrics"]


def test_dry_run_does_not_write(tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p)
    client = _mock_client()

    with patch("lyrics_finder.cli.MusixmatchClient", return_value=client):
        rc = main(["--api-key", "key", "--dry-run", str(p)])

    assert rc == 0
    from lyrics_finder.metadata import read_metadata
    meta = read_metadata(p)
    assert meta["lyrics"] == ""


def test_skip_file_with_existing_lyrics(tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p)
    from lyrics_finder.metadata import write_lyrics
    write_lyrics(p, "Existing lyrics")
    client = _mock_client()

    with patch("lyrics_finder.cli.MusixmatchClient", return_value=client):
        rc = main(["--api-key", "key", str(p)])

    assert rc == 0
    client.search_track.assert_not_called()


def test_overwrite_existing_lyrics(tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p)
    from lyrics_finder.metadata import write_lyrics
    write_lyrics(p, "Old lyrics")
    client = _mock_client("New lyrics")

    with patch("lyrics_finder.cli.MusixmatchClient", return_value=client):
        rc = main(["--api-key", "key", "--overwrite", str(p)])

    assert rc == 0
    from lyrics_finder.metadata import read_metadata
    meta = read_metadata(p)
    assert "New lyrics" in meta["lyrics"]


def test_no_search_results(tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p)
    client = _mock_client()
    client.search_track.return_value = []

    with patch("lyrics_finder.cli.MusixmatchClient", return_value=client):
        rc = main(["--api-key", "key", str(p)])

    assert rc == 0  # graceful, not a hard error


def test_title_override(tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p, title="", artist="")
    client = _mock_client()

    with patch("lyrics_finder.cli.MusixmatchClient", return_value=client):
        rc = main(["--api-key", "key", "--title", "Custom Title", str(p)])

    client.search_track.assert_called_once()
    call_args = client.search_track.call_args
    assert call_args[0][0] == "Custom Title"
