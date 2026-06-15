from unittest.mock import MagicMock, patch

import pytest
import requests

from lyrics_finder.artwork import ArtworkError, ArtworkFinder, ArtworkResult


_ART_100 = "https://is1-ssl.mzstatic.com/image/thumb/Music/v4/ab/source/100x100bb.jpg"


def _mock_response(status=200, json_data=None, content=b"", headers=None):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = json_data if json_data is not None else {}
    m.content = content
    m.headers = headers or {}
    m.raise_for_status.return_value = None
    return m


def _itunes_payload(*albums):
    return {
        "resultCount": len(albums),
        "results": [
            {
                "artistName": a.get("artist", "Artist"),
                "collectionName": a.get("album", "Album"),
                "trackName": a.get("track", ""),
                "artworkUrl100": a.get("art", _ART_100),
            }
            for a in albums
        ],
    }


class TestScale:
    def test_upscales_dimensions(self):
        out = ArtworkFinder._scale(_ART_100, 600)
        assert "600x600bb.jpg" in out
        assert "100x100" not in out

    def test_preserves_png_extension(self):
        url = "https://x.mzstatic.com/image/thumb/a/source/100x100bb.png"
        assert ArtworkFinder._scale(url, 1200).endswith("1200x1200bb.png")

    def test_handles_empty(self):
        assert ArtworkFinder._scale("", 600) == ""


class TestSearch:
    def setup_method(self):
        self.finder = ArtworkFinder()

    def test_parses_results(self):
        r = _mock_response(200, _itunes_payload(
            {"artist": "Queen", "album": "A Night at the Opera"},
        ))
        with patch.object(self.finder._session, "get", return_value=r):
            results = self.finder.search("Queen", size=600)
        assert len(results) == 1
        assert results[0].artist == "Queen"
        assert results[0].album == "A Night at the Opera"
        assert "600x600bb.jpg" in results[0].art_url
        assert "200x200bb.jpg" in results[0].thumb_url

    def test_empty_term_returns_empty(self):
        results = self.finder.search("   ")
        assert results == []

    def test_dedupes_identical_artwork(self):
        payload = _itunes_payload(
            {"album": "X", "art": _ART_100},
            {"album": "X (Deluxe)", "art": _ART_100},
        )
        r = _mock_response(200, payload)
        with patch.object(self.finder._session, "get", return_value=r):
            results = self.finder.search("X")
        assert len(results) == 1

    def test_skips_items_without_artwork(self):
        payload = {"results": [{"artistName": "A", "collectionName": "B"}]}
        r = _mock_response(200, payload)
        with patch.object(self.finder._session, "get", return_value=r):
            results = self.finder.search("A")
        assert results == []

    def test_network_error_raises(self):
        with patch.object(self.finder._session, "get", side_effect=requests.ConnectionError()):
            with pytest.raises(ArtworkError):
                self.finder.search("anything")


class TestFind:
    def setup_method(self):
        self.finder = ArtworkFinder()

    def test_returns_first_match(self):
        r = _mock_response(200, _itunes_payload({"album": "Greatest Hits"}))
        with patch.object(self.finder._session, "get", return_value=r):
            result = self.finder.find("Song", "Artist", "Greatest Hits")
        assert isinstance(result, ArtworkResult)
        assert result.album == "Greatest Hits"

    def test_returns_none_when_no_results(self):
        r = _mock_response(200, {"results": []})
        with patch.object(self.finder._session, "get", return_value=r):
            result = self.finder.find("Nope", "Nobody")
        assert result is None

    def test_picks_exact_album_over_itunes_first(self):
        # iTunes lists a wrong album first; the exact-titled album must win.
        payload = _itunes_payload(
            {"artist": "Adele", "album": "Greatest Hits Live",
             "art": "https://x/a/100x100bb.jpg"},
            {"artist": "Adele", "album": "21",
             "art": "https://x/b/100x100bb.jpg"},
        )
        r = _mock_response(200, payload)
        with patch.object(self.finder._session, "get", return_value=r):
            result = self.finder.find("Rolling in the Deep", "Adele", "21")
        assert result.album == "21"

    def test_exact_album_match_prefers_correct_artist(self):
        # Two different artists both have an album literally named "Hits".
        payload = _itunes_payload(
            {"artist": "Wrong Band", "album": "Hits",
             "art": "https://x/a/100x100bb.jpg"},
            {"artist": "The Beatles", "album": "Hits",
             "art": "https://x/b/100x100bb.jpg"},
        )
        r = _mock_response(200, payload)
        with patch.object(self.finder._session, "get", return_value=r):
            result = self.finder.find("Help", "The Beatles", "Hits")
        assert result.artist == "The Beatles"

    def test_album_match_ignores_remaster_suffix(self):
        payload = _itunes_payload(
            {"artist": "Pink Floyd", "album": "The Wall (2011 Remastered)",
             "art": "https://x/a/100x100bb.jpg"},
        )
        r = _mock_response(200, payload)
        with patch.object(self.finder._session, "get", return_value=r):
            result = self.finder.find("Hey You", "Pink Floyd", "The Wall")
        assert result.album.startswith("The Wall")

    def test_ranks_by_artist_when_no_album(self):
        # Without an album tag, the candidate from the right artist wins even
        # if iTunes returned a different artist first.
        payload = _itunes_payload(
            {"artist": "Cover Band", "album": "Tribute", "track": "Imagine",
             "art": "https://x/a/100x100bb.jpg"},
            {"artist": "John Lennon", "album": "Imagine", "track": "Imagine",
             "art": "https://x/b/100x100bb.jpg"},
        )
        r = _mock_response(200, payload)
        with patch.object(self.finder._session, "get", return_value=r):
            result = self.finder.find("Imagine", "John Lennon")
        assert result.artist == "John Lennon"


class TestDownload:
    def setup_method(self):
        self.finder = ArtworkFinder()

    def test_returns_bytes_and_mime(self):
        r = _mock_response(200, content=b"\xff\xd8\xff\xe0imagedata",
                           headers={"Content-Type": "image/jpeg"})
        with patch.object(self.finder._session, "get", return_value=r):
            data, mime = self.finder.download("https://x/y/600x600bb.jpg")
        assert data == b"\xff\xd8\xff\xe0imagedata"
        assert mime == "image/jpeg"

    def test_infers_png_from_url(self):
        r = _mock_response(200, content=b"\x89PNG", headers={})
        with patch.object(self.finder._session, "get", return_value=r):
            _, mime = self.finder.download("https://x/y/600x600bb.png")
        assert mime == "image/png"

    def test_network_error_raises(self):
        with patch.object(self.finder._session, "get", side_effect=requests.Timeout()):
            with pytest.raises(ArtworkError):
                self.finder.download("https://x/y/600x600bb.jpg")
