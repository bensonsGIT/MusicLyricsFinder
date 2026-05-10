from unittest.mock import MagicMock, patch

import pytest
import requests

from lyrics_finder.sources import LrcLibSource, LyricsOvhSource, LyricsFinder, LyricsError


def _mock_response(status=200, json_data=None):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = json_data if json_data is not None else {}
    m.raise_for_status.return_value = None
    return m


# ── LrcLibSource ──────────────────────────────────────────────────────────

class TestLrcLibSource:
    def setup_method(self):
        self.src = LrcLibSource()

    def test_returns_plain_lyrics(self):
        r = _mock_response(200, {"plainLyrics": "Line 1\nLine 2"})
        with patch("lyrics_finder.sources.requests.get", return_value=r):
            result = self.src.find_lyrics("Song", "Artist")
        assert result == "Line 1\nLine 2"

    def test_404_falls_through_to_search(self):
        r404 = _mock_response(404)
        r_search = _mock_response(200, [{"plainLyrics": "Found via search"}])
        with patch("lyrics_finder.sources.requests.get", side_effect=[r404, r_search]):
            result = self.src.find_lyrics("Song", "Artist")
        assert result == "Found via search"

    def test_empty_lyrics_returns_none(self):
        r = _mock_response(200, {"plainLyrics": ""})
        r_empty_search = _mock_response(200, [])
        with patch("lyrics_finder.sources.requests.get", side_effect=[r, r_empty_search]):
            result = self.src.find_lyrics("Song", "Artist")
        assert result is None

    def test_network_error_raises(self):
        with patch("lyrics_finder.sources.requests.get", side_effect=requests.ConnectionError()):
            with pytest.raises(LyricsError):
                self.src.find_lyrics("Song", "Artist")


# ── LyricsOvhSource ───────────────────────────────────────────────────────

class TestLyricsOvhSource:
    def setup_method(self):
        self.src = LyricsOvhSource()

    def test_returns_lyrics(self):
        r = _mock_response(200, {"lyrics": "Verse 1\nVerse 2"})
        with patch("lyrics_finder.sources.requests.get", return_value=r):
            result = self.src.find_lyrics("Song", "Artist")
        assert result == "Verse 1\nVerse 2"

    def test_no_artist_returns_none(self):
        result = self.src.find_lyrics("Song", "")
        assert result is None

    def test_404_returns_none(self):
        r = _mock_response(404)
        with patch("lyrics_finder.sources.requests.get", return_value=r):
            result = self.src.find_lyrics("Song", "Artist")
        assert result is None

    def test_error_key_returns_none(self):
        r = _mock_response(200, {"error": "No lyrics found"})
        with patch("lyrics_finder.sources.requests.get", return_value=r):
            result = self.src.find_lyrics("Song", "Artist")
        assert result is None

    def test_network_error_raises(self):
        with patch("lyrics_finder.sources.requests.get", side_effect=requests.ConnectionError()):
            with pytest.raises(LyricsError):
                self.src.find_lyrics("Song", "Artist")


# ── LyricsFinder ─────────────────────────────────────────────────────────

class TestLyricsFinder:
    def test_returns_first_successful_source(self):
        finder = LyricsFinder()
        finder._sources[0] = MagicMock(find_lyrics=lambda *a, **kw: "Lyrics from lrclib", NAME="lrclib.net")
        lyrics, source = finder.find("Song", "Artist")
        assert lyrics == "Lyrics from lrclib"
        assert source == "lrclib.net"

    def test_falls_back_to_second_source(self):
        finder = LyricsFinder()
        finder._sources[0] = MagicMock(find_lyrics=lambda *a, **kw: None, NAME="lrclib.net")
        finder._sources[1] = MagicMock(find_lyrics=lambda *a, **kw: "Lyrics from ovh", NAME="lyrics.ovh")
        lyrics, source = finder.find("Song", "Artist")
        assert lyrics == "Lyrics from ovh"
        assert source == "lyrics.ovh"

    def test_returns_none_none_when_all_fail(self):
        finder = LyricsFinder()
        for s in finder._sources:
            s.find_lyrics = lambda *a, **kw: None
        lyrics, source = finder.find("Song", "Artist")
        assert lyrics is None
        assert source is None

    def test_skips_erroring_source(self):
        def boom(*a, **kw):
            raise LyricsError("network down")

        finder = LyricsFinder()
        finder._sources[0] = MagicMock(find_lyrics=boom, NAME="lrclib.net")
        finder._sources[1] = MagicMock(find_lyrics=lambda *a, **kw: "Fallback lyrics", NAME="lyrics.ovh")
        lyrics, source = finder.find("Song", "Artist")
        assert lyrics == "Fallback lyrics"
