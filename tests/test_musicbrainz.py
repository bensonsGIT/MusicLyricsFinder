"""Tests for the MusicBrainz tag lookup (mocked HTTP — no network)."""
from unittest.mock import MagicMock, patch

import pytest
import requests

from lyrics_finder import musicbrainz
from lyrics_finder.musicbrainz import MusicBrainzError, MusicBrainzTags, _escape


@pytest.fixture(autouse=True)
def no_rate_limit_or_cache(monkeypatch):
    monkeypatch.setattr(MusicBrainzTags, "MIN_INTERVAL", 0)
    MusicBrainzTags._genre_cache.clear()


def _resp(payload, status=200):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = payload
    m.raise_for_status.return_value = None
    return m


def _release(title, date, rg_id, *, status="Official", ptype="Album", secondary=None,
             offset=0, count=11, position=1, mediums=1, artist=None):
    rel = {
        "title": title, "date": date, "status": status, "medium-count": mediums,
        "release-group": {"id": rg_id, "primary-type": ptype, "secondary-types": secondary or []},
        "media": [{"position": position, "track-count": count, "track-offset": offset,
                   "track": [{"number": str(offset + 1)}]}],
    }
    if artist:
        rel["artist-credit"] = [{"name": artist, "artist": {"id": "x"}}]
    return rel


def _recording(title, artist, releases, score=100):
    return {"title": title, "score": score, "first-release-date": "2011-01-24",
            "artist-credit": [{"name": artist, "artist": {"id": "artist-1"}}],
            "releases": releases}


ADELE = _recording("Rolling in the Deep", "Adele", [
    _release("Now That's What I Call Music! 79", "2011-07-25", "rg-comp", secondary=["Compilation"], offset=4),
    _release("Rolling in the Deep", "2010-11-29", "rg-single", ptype="Single", count=2),
    _release("21", "2011-01-24", "rg-21", offset=0, count=11),
    _release("21", "2012-03-01", "rg-21", offset=0, count=11),  # reissue, same group
])


def _finder_with(*payloads):
    f = MusicBrainzTags()
    f._session.get = MagicMock(side_effect=[_resp(p) for p in payloads])
    return f


def test_prefers_earliest_official_studio_album():
    f = _finder_with({"recordings": [ADELE]}, {"genres": [{"name": "soul", "count": 9}, {"name": "pop", "count": 3}]})
    tags = f.lookup_tags("Rolling in the Deep", "Adele")
    assert tags["album"] == "21"
    assert tags["year"] == "2011"
    assert (tags["track_number"], tags["track_total"]) == ("1", "11")
    assert (tags["disc_number"], tags["disc_total"]) == ("1", "1")
    assert tags["genre"] == "Soul"          # most-voted, title-cased
    assert tags["artist"] == "Adele"
    assert tags["album_artist"] == "Adele"


def test_file_album_tag_wins():
    f = _finder_with({"recordings": [ADELE]}, {"genres": []}, {"genres": []})
    tags = f.lookup_tags("Rolling in the Deep", "Adele", album="Now That's What I Call Music! 79")
    assert tags["album"] == "Now That's What I Call Music! 79"
    assert tags["track_number"] == "5"
    assert tags["genre"] == ""              # neither release group nor artist has genres


def test_genre_falls_back_to_artist():
    f = _finder_with({"recordings": [ADELE]}, {"genres": []}, {"genres": [{"name": "hip hop", "count": 2}]})
    assert f.lookup_tags("Rolling in the Deep", "Adele")["genre"] == "Hip Hop"


def test_genre_is_cached_per_release_group():
    f = _finder_with({"recordings": [ADELE]}, {"genres": [{"name": "soul", "count": 1}]}, {"recordings": [ADELE]})
    f.lookup_tags("Rolling in the Deep", "Adele")
    f.lookup_tags("Rolling in the Deep", "Adele")
    assert f._session.get.call_count == 3   # second lookup skips the genre request


def test_rejects_wrong_artist():
    other = _recording("Rolling in the Deep", "Some Cover Band", ADELE["releases"])
    f = _finder_with({"recordings": [other]})
    assert f.lookup_tags("Rolling in the Deep", "Adele") is None


def test_rejects_unrelated_title():
    f = _finder_with({"recordings": [_recording("Hello", "Adele", ADELE["releases"])]})
    assert f.lookup_tags("Rolling in the Deep", "Adele") is None


def test_vinyl_track_number_without_offset():
    rel = _release("LP", "1970", "rg", count=10)
    del rel["media"][0]["track-offset"]
    rel["media"][0]["track"] = [{"number": "A3"}]
    f = _finder_with({"recordings": [_recording("Song", "Band", [rel])]}, {"genres": []}, {"genres": []})
    assert f.lookup_tags("Song", "Band")["track_number"] == ""


def test_query_escapes_lucene_syntax():
    assert _escape('AC/DC: "Hits"') == 'AC\\/DC\\: \\"Hits\\"'
    f = _finder_with({"recordings": []})
    f.lookup_tags("What?", "AC/DC")
    query = f._session.get.call_args.kwargs["params"]["query"]
    assert query == 'recording:"What\\?" AND artist:"AC\\/DC"'


def test_sends_user_agent():
    assert "MusicLyricsFinder" in MusicBrainzTags()._session.headers["User-Agent"]


def test_network_error_raises():
    f = MusicBrainzTags()
    f._session.get = MagicMock(side_effect=requests.ConnectionError("down"))
    with pytest.raises(MusicBrainzError):
        f.lookup_tags("Song", "Band")


def test_rate_limited_raises():
    f = MusicBrainzTags()
    f._session.get = MagicMock(return_value=_resp({}, status=503))
    with pytest.raises(MusicBrainzError, match="rate-limiting"):
        f.lookup_tags("Song", "Band")


def test_requests_are_spaced(monkeypatch):
    monkeypatch.setattr(MusicBrainzTags, "MIN_INTERVAL", 1.1)
    sleeps = []
    monkeypatch.setattr(musicbrainz.time, "sleep", sleeps.append)
    MusicBrainzTags._last_request = musicbrainz.time.monotonic()
    f = _finder_with({"recordings": []})
    f.lookup_tags("Song", "Band")
    assert sleeps and sleeps[0] > 1.0
