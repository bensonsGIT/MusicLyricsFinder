from unittest.mock import MagicMock, patch

import pytest
import requests

import lyrics_finder.sources as src_module
from lyrics_finder.sources import LrcLibSource, LyricsOvhSource, LyricsFinder, LyricsError


def _mock_response(status=200, json_data=None):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = json_data if json_data is not None else {}
    m.raise_for_status.return_value = None
    return m


# Clear the module-level cache before every test to avoid cross-test pollution
@pytest.fixture(autouse=True)
def clear_lyrics_cache():
    src_module.clear_cache()
    yield
    src_module.clear_cache()


# ── LrcLibSource ──────────────────────────────────────────────────────────

class TestLrcLibSource:
    def setup_method(self):
        self.src = LrcLibSource()

    def test_returns_plain_lyrics(self):
        r = _mock_response(200, {"plainLyrics": "Line 1\nLine 2"})
        with patch.object(self.src._session, "get", return_value=r):
            result = self.src.find_lyrics("Song", "Artist")
        assert result == "Line 1\nLine 2"

    def test_404_falls_through_to_search(self):
        r404 = _mock_response(404)
        r_search = _mock_response(200, [{"plainLyrics": "Found via search"}])
        with patch.object(self.src._session, "get", side_effect=[r404, r_search]):
            result = self.src.find_lyrics("Song", "Artist")
        assert result == "Found via search"

    def test_empty_lyrics_returns_none(self):
        r = _mock_response(200, {"plainLyrics": ""})
        r_empty_search = _mock_response(200, [])
        with patch.object(self.src._session, "get", side_effect=[r, r_empty_search]):
            result = self.src.find_lyrics("Song", "Artist")
        assert result is None

    def test_network_error_raises(self):
        with patch.object(self.src._session, "get", side_effect=requests.ConnectionError()):
            with pytest.raises(LyricsError):
                self.src.find_lyrics("Song", "Artist")


# ── LyricsOvhSource ───────────────────────────────────────────────────────

class TestLyricsOvhSource:
    def setup_method(self):
        self.src = LyricsOvhSource()

    def test_returns_lyrics(self):
        r = _mock_response(200, {"lyrics": "Verse 1\nVerse 2"})
        with patch.object(self.src._session, "get", return_value=r):
            result = self.src.find_lyrics("Song", "Artist")
        assert result == "Verse 1\nVerse 2"

    def test_no_artist_returns_none(self):
        result = self.src.find_lyrics("Song", "")
        assert result is None

    def test_404_returns_none(self):
        r = _mock_response(404)
        with patch.object(self.src._session, "get", return_value=r):
            result = self.src.find_lyrics("Song", "Artist")
        assert result is None

    def test_error_key_returns_none(self):
        r = _mock_response(200, {"error": "No lyrics found"})
        with patch.object(self.src._session, "get", return_value=r):
            result = self.src.find_lyrics("Song", "Artist")
        assert result is None

    def test_network_error_raises(self):
        with patch.object(self.src._session, "get", side_effect=requests.ConnectionError()):
            with pytest.raises(LyricsError):
                self.src.find_lyrics("Song", "Artist")


# ── LyricsFinder ─────────────────────────────────────────────────────────

class TestLyricsFinder:
    def test_returns_a_successful_source(self):
        finder = LyricsFinder()
        finder._lrclib.find_lyrics_exact = lambda *a, **kw: "Lyrics from lrclib"
        finder._lrclib.find_lyrics_fuzzy = lambda *a, **kw: None
        finder._ovh.find_lyrics = lambda *a, **kw: None
        lyrics, source = finder.find("Song A", "Artist")
        assert lyrics == "Lyrics from lrclib"
        assert source == "lrclib.net"

    def test_returns_none_none_when_all_fail(self):
        finder = LyricsFinder()
        finder._lrclib.find_lyrics_exact = lambda *a, **kw: None
        finder._lrclib.find_lyrics_fuzzy = lambda *a, **kw: None
        finder._ovh.find_lyrics = lambda *a, **kw: None
        lyrics, source = finder.find("Song B", "Artist")
        assert lyrics is None
        assert source is None

    def test_skips_erroring_source(self):
        def boom(*a, **kw):
            raise LyricsError("network down")

        finder = LyricsFinder()
        finder._lrclib.find_lyrics_exact = boom
        finder._lrclib.find_lyrics_fuzzy = boom
        finder._ovh.find_lyrics = lambda *a, **kw: "Fallback lyrics"
        lyrics, source = finder.find("Song C", "Artist")
        assert lyrics == "Fallback lyrics"
        assert source == "lyrics.ovh"

    def test_cache_hit_skips_network(self):
        finder = LyricsFinder()
        call_count = 0

        def counting(*a, **kw):
            nonlocal call_count
            call_count += 1
            return "Cached lyrics"

        finder._lrclib.find_lyrics_exact = counting
        finder._lrclib.find_lyrics_fuzzy = lambda *a, **kw: None
        finder._ovh.find_lyrics = lambda *a, **kw: None

        r1 = finder.find("Song D", "Artist")
        r2 = finder.find("Song D", "Artist")   # should hit cache
        r3 = finder.find("song d", "ARTIST")   # normalised key — also cache

        assert r1 == r2 == r3
        assert call_count == 1   # network called only once

    def test_cache_is_case_insensitive(self):
        finder = LyricsFinder()
        finder._lrclib.find_lyrics_exact = lambda *a, **kw: "Lyrics"
        finder._lrclib.find_lyrics_fuzzy = lambda *a, **kw: None
        finder._ovh.find_lyrics = lambda *a, **kw: None

        lyrics1, _ = finder.find("Bohemian Rhapsody", "Queen")
        lyrics2, _ = finder.find("BOHEMIAN RHAPSODY", "queen")
        assert lyrics1 == lyrics2 == "Lyrics"
