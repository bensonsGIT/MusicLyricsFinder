"""Tests for the MusicBrainz tag lookup (mocked HTTP — no network)."""
from unittest.mock import MagicMock, patch

import pytest
import requests

from lyrics_finder import musicbrainz
from lyrics_finder.musicbrainz import MusicBrainzError, MusicBrainzTags, _escape


@pytest.fixture(autouse=True)
def no_rate_limit(monkeypatch):
    monkeypatch.setattr(MusicBrainzTags, "MIN_INTERVAL", 0)


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
    f = _finder_with({"releases": []}, {"recordings": [ADELE]}, {"genres": []}, {"genres": []})
    tags = f.lookup_tags("Rolling in the Deep", "Adele", album="Now That's What I Call Music! 79")
    assert tags["album"] == "Now That's What I Call Music! 79"
    assert tags["track_number"] == "5"
    assert tags["genre"] == ""              # neither release group nor artist has genres


def test_genre_falls_back_to_artist():
    f = _finder_with({"recordings": [ADELE]}, {"genres": []}, {"genres": [{"name": "hip hop", "count": 2}]})
    assert f.lookup_tags("Rolling in the Deep", "Adele")["genre"] == "Hip Hop"


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


# ── fewer requests: skip unneeded genre ──────────────────────────────────────

def test_genre_request_skipped_when_not_needed():
    f = _finder_with({"recordings": [ADELE]})
    tags = f.lookup_tags("Rolling in the Deep", "Adele", need={"year", "track_number"})
    assert tags["year"] == "2011"
    assert tags["genre"] == ""
    assert f._session.get.call_count == 1   # only the recording search


# ── fewer requests: one tracklist per album ──────────────────────────────────

ALBUM_SEARCH = {"releases": [
    {"id": "rel-live", "title": "21", "status": "Official", "date": "2011-11-28",
     "artist-credit": [{"name": "Adele", "artist": {"id": "artist-1"}}],
     "release-group": {"id": "rg-live", "primary-type": "Album", "secondary-types": ["Live"]}},
    {"id": "rel-21", "title": "21", "status": "Official", "date": "2011-01-24",
     "artist-credit": [{"name": "Adele", "artist": {"id": "artist-1"}}],
     "release-group": {"id": "rg-21", "primary-type": "Album"}},
]}

ALBUM_RELEASE = {
    "id": "rel-21", "title": "21", "date": "2011-01-24",
    "artist-credit": [{"name": "Adele", "artist": {"id": "artist-1"}}],
    "release-group": {"id": "rg-21", "first-release-date": "2011-01-19"},
    "media": [
        {"position": 1, "track-count": 3, "tracks": [
            {"position": 1, "number": "1", "title": "Rolling in the Deep"},
            {"position": 2, "number": "2", "title": "Rumour Has It"},
            {"position": 3, "number": "3", "title": "Turning Tables"},
        ]},
        {"position": 2, "track-count": 1, "tracks": [
            {"position": 1, "number": "1", "title": "Bonus Song",
             "artist-credit": [{"name": "Adele", "joinphrase": " & ", "artist": {"id": "artist-1"}},
                               {"name": "Guest", "artist": {"id": "artist-2"}}]},
        ]},
    ],
}


def test_album_path_reads_tracklist():
    f = _finder_with(ALBUM_SEARCH, ALBUM_RELEASE, {"genres": [{"name": "soul", "count": 4}]})
    tags = f.lookup_tags("Rumour Has It", "Adele", "21")
    assert tags == {
        "title": "Rumour Has It", "artist": "Adele", "album": "21", "album_artist": "Adele",
        "genre": "Soul", "year": "2011", "track_number": "2", "track_total": "3",
        "disc_number": "1", "disc_total": "2",
    }
    # studio album chosen over the live release with the same title
    assert "release/rel-21" in f._session.get.call_args_list[1].args[0]


def test_album_path_track_artist_and_second_disc():
    f = _finder_with(ALBUM_SEARCH, ALBUM_RELEASE)
    tags = f.lookup_tags("Bonus Song", "Adele", "21", need={"track_number"})
    assert tags["artist"] == "Adele & Guest"
    assert tags["album_artist"] == "Adele"
    assert (tags["track_number"], tags["disc_number"], tags["disc_total"]) == ("1", "2", "2")


def test_album_path_falls_back_when_song_not_on_album():
    other = _recording("Someone Like You", "Adele", [_release("21", "2011-01-24", "rg-21", offset=10)])
    f = _finder_with(ALBUM_SEARCH, ALBUM_RELEASE, {"recordings": [other]})
    tags = f.lookup_tags("Someone Like You", "Adele", "21", need={"year"})
    assert (tags["album"], tags["track_number"]) == ("21", "11")
    assert f._session.get.call_count == 3   # album search, tracklist, recording search


def test_whole_album_costs_two_requests_with_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(MusicBrainzTags, "CACHE_PATH", tmp_path / "mb.sqlite")
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        return _resp(ALBUM_RELEASE if "/release/" in url else ALBUM_SEARCH)

    for song in ("Rolling in the Deep", "Rumour Has It", "Turning Tables"):
        f = MusicBrainzTags()
        f._session.get = fake_get
        assert f.lookup_tags(song, "Adele", "21", need={"track_number"})["album"] == "21"
    assert len(calls) == 2


# ── disk cache ───────────────────────────────────────────────────────────────

def test_disk_cache_survives_new_instances(tmp_path, monkeypatch):
    monkeypatch.setattr(MusicBrainzTags, "CACHE_PATH", tmp_path / "mb.sqlite")
    first = _finder_with({"recordings": [ADELE]})
    first.lookup_tags("Rolling in the Deep", "Adele", need={"year"})

    MusicBrainzTags._caches.clear()          # simulate an app restart
    second = MusicBrainzTags()
    second._session.get = MagicMock(side_effect=AssertionError("should not hit the network"))
    assert second.lookup_tags("Rolling in the Deep", "Adele", need={"year"})["year"] == "2011"
    assert (tmp_path / "mb.sqlite").exists()


def test_cache_entries_expire(tmp_path, monkeypatch):
    monkeypatch.setattr(MusicBrainzTags, "CACHE_PATH", tmp_path / "mb.sqlite")
    monkeypatch.setattr(MusicBrainzTags, "CACHE_TTL", -1)   # everything already stale
    MusicBrainzTags._caches.clear()
    f = _finder_with({"recordings": [ADELE]}, {"recordings": [ADELE]})
    f.lookup_tags("Rolling in the Deep", "Adele", need={"year"})
    f.lookup_tags("Rolling in the Deep", "Adele", need={"year"})
    assert f._session.get.call_count == 2


def test_errors_are_not_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(MusicBrainzTags, "CACHE_PATH", tmp_path / "mb.sqlite")
    MusicBrainzTags._caches.clear()
    f = MusicBrainzTags()
    f._session.get = MagicMock(side_effect=[_resp({}, status=503), _resp({"recordings": [ADELE]})])
    with pytest.raises(MusicBrainzError):
        f.lookup_tags("Rolling in the Deep", "Adele", need={"year"})
    assert f.lookup_tags("Rolling in the Deep", "Adele", need={"year"})["year"] == "2011"


def test_unwritable_cache_dir_still_works(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(MusicBrainzTags, "CACHE_PATH", blocker / "sub" / "mb.sqlite")
    MusicBrainzTags._caches.clear()
    f = _finder_with({"recordings": [ADELE]})
    assert f.lookup_tags("Rolling in the Deep", "Adele", need={"year"})["year"] == "2011"


def test_concurrent_tracks_from_one_album_share_requests(tmp_path, monkeypatch):
    """Process All runs lookups in parallel; the album must still be fetched once."""
    import threading
    monkeypatch.setattr(MusicBrainzTags, "CACHE_PATH", tmp_path / "mb.sqlite")
    monkeypatch.setattr(MusicBrainzTags, "MIN_INTERVAL", 0.05)
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        return _resp(ALBUM_RELEASE if "/release/" in url else ALBUM_SEARCH)

    results = []

    def worker(song):
        f = MusicBrainzTags()
        f._session.get = fake_get
        results.append(f.lookup_tags(song, "Adele", "21", need={"track_number"})["track_number"])

    songs = ["Rolling in the Deep", "Rumour Has It", "Turning Tables", "Bonus Song"]
    threads = [threading.Thread(target=worker, args=(s,)) for s in songs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["1", "1", "2", "3"]
    assert len(calls) == 2
