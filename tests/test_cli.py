import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from mutagen.id3 import ID3, TIT2, TPE1
from mutagen.mp4 import MP4

from lyrics_finder.cli import main, _collect_files


# ---------------------------------------------------------------------------
# Minimal file helpers
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


def _mock_finder(lyrics="Test lyrics\nLine 2"):
    finder = MagicMock()
    finder.find.return_value = (lyrics, "lrclib.net")
    return finder


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
    assert _collect_files([str(p)]) == [p]


def test_no_files_found(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    rc = main([str(empty)])
    assert rc == 1


def test_mp3_lyrics_written(tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p)
    finder = _mock_finder()

    with patch("lyrics_finder.cli.LyricsFinder", return_value=finder):
        rc = main([str(p)])

    assert rc == 0
    from lyrics_finder.metadata import read_metadata
    assert "Test lyrics" in read_metadata(p)["lyrics"]


def test_m4a_lyrics_written(tmp_path):
    p = tmp_path / "song.m4a"
    _make_m4a(p)
    finder = _mock_finder()

    with patch("lyrics_finder.cli.LyricsFinder", return_value=finder):
        rc = main([str(p)])

    assert rc == 0
    from lyrics_finder.metadata import read_metadata
    assert "Test lyrics" in read_metadata(p)["lyrics"]


def test_dry_run_does_not_write(tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p)
    finder = _mock_finder()

    with patch("lyrics_finder.cli.LyricsFinder", return_value=finder):
        rc = main(["--dry-run", str(p)])

    assert rc == 0
    from lyrics_finder.metadata import read_metadata
    assert read_metadata(p)["lyrics"] == ""


def test_skip_file_with_existing_lyrics(tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p)
    from lyrics_finder.metadata import write_lyrics
    write_lyrics(p, "Existing lyrics")
    finder = _mock_finder()

    with patch("lyrics_finder.cli.LyricsFinder", return_value=finder):
        rc = main([str(p)])

    assert rc == 0
    finder.find.assert_not_called()


def test_overwrite_existing_lyrics(tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p)
    from lyrics_finder.metadata import write_lyrics
    write_lyrics(p, "Old lyrics")
    finder = _mock_finder("New lyrics")

    with patch("lyrics_finder.cli.LyricsFinder", return_value=finder):
        rc = main(["--overwrite", str(p)])

    assert rc == 0
    from lyrics_finder.metadata import read_metadata
    assert "New lyrics" in read_metadata(p)["lyrics"]


def test_no_search_results(tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p)
    finder = _mock_finder()
    finder.find.return_value = (None, None)

    with patch("lyrics_finder.cli.LyricsFinder", return_value=finder):
        rc = main([str(p)])

    assert rc == 0


def test_title_override(tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p, title="", artist="")
    finder = _mock_finder()

    with patch("lyrics_finder.cli.LyricsFinder", return_value=finder):
        main(["--title", "Custom Title", str(p)])

    finder.find.assert_called_once()
    assert finder.find.call_args[0][0] == "Custom Title"


# ---------------------------------------------------------------------------
# Album art
# ---------------------------------------------------------------------------

_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 32 + b"\xff\xd9"


def _mock_art_finder():
    from lyrics_finder.artwork import ArtworkResult
    finder = MagicMock()
    finder.DEFAULT_SIZE = 600
    finder.find.return_value = ArtworkResult(
        "Artist", "Album", "", "thumb", "https://x/600x600bb.jpg", "album")
    finder.download.return_value = (_JPEG, "image/jpeg")
    return finder


def test_artwork_only_embeds_art(tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p)
    art = _mock_art_finder()

    with patch("lyrics_finder.cli.ArtworkFinder", return_value=art):
        rc = main(["--artwork-only", str(p)])

    assert rc == 0
    art.find.assert_called_once()
    from lyrics_finder.metadata import read_metadata
    assert read_metadata(p)["has_artwork"] is True


def test_artwork_alongside_lyrics(tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p)
    finder = _mock_finder()
    art = _mock_art_finder()

    with patch("lyrics_finder.cli.LyricsFinder", return_value=finder), \
         patch("lyrics_finder.cli.ArtworkFinder", return_value=art):
        rc = main(["--artwork", str(p)])

    assert rc == 0
    from lyrics_finder.metadata import read_metadata
    meta = read_metadata(p)
    assert "Test lyrics" in meta["lyrics"]
    assert meta["has_artwork"] is True


def test_artwork_dry_run_does_not_write(tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p)
    art = _mock_art_finder()

    with patch("lyrics_finder.cli.ArtworkFinder", return_value=art):
        rc = main(["--artwork-only", "--dry-run", str(p)])

    assert rc == 0
    art.download.assert_not_called()
    from lyrics_finder.metadata import read_metadata
    assert read_metadata(p)["has_artwork"] is False


def test_clear_artwork_flag(tmp_path):
    p = tmp_path / "song.mp3"
    _make_mp3(p)
    from lyrics_finder.metadata import write_artwork, read_metadata
    write_artwork(p, _JPEG, "image/jpeg")

    rc = main(["--clear-artwork", str(p)])
    assert rc == 0
    assert read_metadata(p)["has_artwork"] is False
