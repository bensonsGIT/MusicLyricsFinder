import pytest

from lyrics_finder.musicbrainz import MusicBrainzTags


@pytest.fixture(autouse=True)
def _no_musicbrainz_disk_cache(monkeypatch):
    """Keep tests from reading or writing the user's real MusicBrainz cache."""
    monkeypatch.setattr(MusicBrainzTags, "CACHE_PATH", None)
