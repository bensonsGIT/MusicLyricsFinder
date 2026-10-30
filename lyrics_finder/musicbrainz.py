"""Tag lookup via MusicBrainz (https://musicbrainz.org) — free, no API key.

MusicBrainz is a community-curated open music database; its release data is
far more reliable than store catalogues. A recording usually appears on many
releases (the original album, singles, compilations, live albums), so we pick
the release that best matches the file: the file's own album when it has one,
otherwise the earliest official studio album.

MusicBrainz asks clients to send a descriptive User-Agent and stay under one
request per second; both are enforced here. To keep large libraries fast
within that limit we make as few requests as possible:

* every response is cached on disk (SQLite), so repeat lookups are free;
* when a file has album + artist tags we fetch that album's whole tracklist
  once, and every other track on the album is answered from the cache;
* the genre request is only made when the caller actually needs a genre.
"""

import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

from .artwork import _normalize, _similar

_LUCENE_SPECIAL = re.compile(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)')

ALL_FIELDS = frozenset({
    "title", "artist", "album", "album_artist", "genre", "year",
    "track_number", "track_total", "disc_number", "disc_total",
})


def _escape(text: str) -> str:
    return _LUCENE_SPECIAL.sub(r"\\\1", text)


def _credit(artist_credit: list | None) -> str:
    """Render a MusicBrainz artist-credit list as one display string."""
    return "".join(
        (c.get("name") or c.get("artist", {}).get("name", "")) + (c.get("joinphrase") or "")
        for c in (artist_credit or [])
    ).strip()


def _first_artist_id(artist_credit: list | None) -> str:
    credit = artist_credit or []
    return (credit[0].get("artist") or {}).get("id", "") if credit else ""


def default_cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "musiclyricsfinder" / "musicbrainz.sqlite"


class MusicBrainzError(Exception):
    pass


class _ResponseCache:
    """Tiny thread-safe SQLite key/value store for API responses."""

    def __init__(self, path: Path, ttl: float) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._ttl = ttl
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, ts REAL, body TEXT)")
        self._db.commit()

    def get(self, key: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT ts, body FROM responses WHERE key = ?", (key,)).fetchone()
        if not row or time.time() - row[0] > self._ttl:
            return None
        return json.loads(row[1])

    def put(self, key: str, value: dict) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO responses (key, ts, body) VALUES (?, ?, ?)",
                (key, time.time(), json.dumps(value)),
            )
            self._db.commit()


class MusicBrainzTags:
    BASE = "https://musicbrainz.org/ws/2"
    USER_AGENT = "MusicLyricsFinder/1.0 (https://github.com/bensonsGIT/MusicLyricsFinder)"
    MIN_INTERVAL = 1.1          # seconds between requests (MusicBrainz limit: 1/s)
    CACHE_TTL = 30 * 24 * 3600  # re-check cached answers after 30 days
    CACHE_PATH: Path | None = default_cache_path()  # None disables the disk cache

    # Shared across instances: the rate limit is per client IP, not per object.
    _lock = threading.Lock()
    _last_request = 0.0
    _caches: dict[str, _ResponseCache] = {}
    _caches_lock = threading.Lock()

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers["User-Agent"] = self.USER_AGENT
        self._session.headers["Accept"] = "application/json"
        self._cache = self._open_cache()

    @classmethod
    def _open_cache(cls) -> _ResponseCache | None:
        if cls.CACHE_PATH is None:
            return None
        key = str(cls.CACHE_PATH)
        with cls._caches_lock:
            if key not in cls._caches:
                try:
                    cls._caches[key] = _ResponseCache(Path(cls.CACHE_PATH), cls.CACHE_TTL)
                except (OSError, sqlite3.Error):
                    return None  # unwritable cache dir: work uncached
            return cls._caches[key]

    def _get(self, path: str, params: dict) -> dict:
        params = {**params, "fmt": "json"}
        key = f"{path}?{urlencode(sorted(params.items()))}"
        if self._cache and (hit := self._cache.get(key)) is not None:
            return hit

        with MusicBrainzTags._lock:
            # Another thread may have fetched this while we waited for the lock
            # (e.g. two tracks from the same album processed concurrently).
            if self._cache and (hit := self._cache.get(key)) is not None:
                return hit
            wait = MusicBrainzTags._last_request + self.MIN_INTERVAL - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                r = self._session.get(f"{self.BASE}/{path}", params=params, timeout=15)
            except requests.RequestException as e:
                raise MusicBrainzError(f"MusicBrainz request failed: {e}") from e
            finally:
                MusicBrainzTags._last_request = time.monotonic()

            try:
                if r.status_code == 503:
                    raise MusicBrainzError("MusicBrainz is rate-limiting requests; try again shortly")
                if r.status_code == 404:
                    data: dict = {}
                else:
                    r.raise_for_status()
                    data = r.json()
            except requests.RequestException as e:
                raise MusicBrainzError(f"MusicBrainz request failed: {e}") from e
            except ValueError as e:
                raise MusicBrainzError(f"MusicBrainz returned invalid JSON: {e}") from e

            if self._cache:
                self._cache.put(key, data)
            return data

    # ── shared helpers ────────────────────────────────────────────────────

    @staticmethod
    def _release_rank(release: dict, album: str) -> tuple:
        """Sort key — lower is better."""
        rg = release.get("release-group") or {}
        album_match = _normalize(album) == _normalize(release.get("title", "")) if album else False
        official = release.get("status") == "Official"
        studio_album = rg.get("primary-type") == "Album" and not rg.get("secondary-types")
        date = release.get("date") or "9999"
        return (not album_match, not official, not studio_album, date)

    def _genre(self, release_group_id: str, artist_id: str) -> str:
        """Most-voted genre of the release group, falling back to the artist's."""
        for entity, mbid in (("release-group", release_group_id), ("artist", artist_id)):
            if not mbid:
                continue
            try:
                data = self._get(f"{entity}/{mbid}", {"inc": "genres"})
            except MusicBrainzError:
                continue
            genres = sorted(data.get("genres") or [], key=lambda g: -g.get("count", 0))
            if genres:
                return genres[0]["name"].title()
        return ""

    # ── album path: one tracklist answers every song on the album ─────────

    def _album_release(self, album: str, artist: str) -> dict | None:
        """Full release (with tracklist) best matching an album + artist."""
        query = f'release:"{_escape(album)}" AND artist:"{_escape(artist)}"'
        data = self._get("release", {"query": query, "limit": 10})
        matches = [
            rel for rel in data.get("releases", [])
            if _similar(album, rel.get("title", "")) >= 0.9
            and _similar(artist, _credit(rel.get("artist-credit"))) >= 0.5
        ]
        if not matches:
            return None
        best = min(matches, key=lambda rel: self._release_rank(rel, album))
        release = self._get(f"release/{best['id']}", {"inc": "recordings artist-credits release-groups"})
        return release or None

    def _from_album(self, title: str, artist: str, album: str, need: frozenset) -> dict | None:
        release = self._album_release(album, artist)
        if not release:
            return None

        best, best_sim = None, 0.0
        media = release.get("media") or []
        for medium in media:
            for track in medium.get("tracks") or []:
                sim = _similar(title, track.get("title", ""))
                if sim > best_sim:
                    best, best_sim = (medium, track), sim
        if not best or best_sim < 0.8:
            return None  # song isn't on this album — let the caller search by recording

        medium, track = best
        rg = release.get("release-group") or {}
        track_credit = track.get("artist-credit") or (track.get("recording") or {}).get("artist-credit")
        credit = track_credit or release.get("artist-credit")
        year = (rg.get("first-release-date") or release.get("date") or "")[:4]
        genre = ""
        if "genre" in need:
            genre = self._genre(rg.get("id", ""), _first_artist_id(credit))

        return {
            "title": track.get("title", ""),
            "artist": _credit(credit),
            "album": release.get("title", ""),
            "album_artist": _credit(release.get("artist-credit")) or _credit(credit),
            "genre": genre,
            "year": year,
            "track_number": str(track.get("position") or ""),
            "track_total": str(medium.get("track-count") or len(medium.get("tracks") or []) or ""),
            "disc_number": str(medium.get("position") or ""),
            "disc_total": str(len(media)) if media else "",
        }

    # ── recording path: search the song itself ────────────────────────────

    def _from_recording(self, title: str, artist: str, album: str, need: frozenset) -> dict | None:
        query = f'recording:"{_escape(title)}"'
        if artist:
            query += f' AND artist:"{_escape(artist)}"'
        data = self._get("recording", {"query": query, "limit": 15})

        def rec_score(rec: dict) -> float:
            s = 2.0 * _similar(title, rec.get("title", ""))
            if artist:
                s += 2.0 * _similar(artist, _credit(rec.get("artist-credit")))
            return s + rec.get("score", 0) / 100

        candidates = [
            r for r in data.get("recordings", [])
            if r.get("releases")
            and _similar(title, r.get("title", "")) >= 0.6
            and (not artist or _similar(artist, _credit(r.get("artist-credit"))) >= 0.5)
        ]
        if not candidates:
            return None

        # Among well-matching recordings, choose the release across all of them,
        # so an album version wins over a same-titled single or live take.
        best_score = max(rec_score(r) for r in candidates)
        pool = [
            (rec, rel)
            for rec in candidates if rec_score(rec) >= best_score - 0.5
            for rel in rec["releases"]
        ]
        rec, release = min(pool, key=lambda pr: self._release_rank(pr[1], album))

        rg = release.get("release-group") or {}
        # Year of the first release in this release group, not of a reissue.
        same_group = [
            rel.get("date", "") for r in candidates for rel in r["releases"]
            if (rel.get("release-group") or {}).get("id") == rg.get("id") and rel.get("date")
        ]
        year = (min(same_group) if same_group else rec.get("first-release-date", ""))[:4]

        media = (release.get("media") or [{}])[0]
        tracks = media.get("track") or [{}]
        # "number" can be vinyl-style ("A1"); track-offset is always numeric.
        if isinstance(media.get("track-offset"), int):
            track_number = str(media["track-offset"] + 1)
        else:
            number = str(tracks[0].get("number") or "")
            track_number = number if number.isdigit() else ""
        disc_total = str(release.get("medium-count") or "")
        credit = rec.get("artist-credit") or []
        genre = ""
        if "genre" in need:
            genre = self._genre(rg.get("id", ""), _first_artist_id(credit))

        return {
            "title": rec.get("title", ""),
            "artist": _credit(credit),
            "album": release.get("title", ""),
            "album_artist": _credit(release.get("artist-credit")) or _credit(credit),
            "genre": genre,
            "year": year,
            "track_number": track_number,
            "track_total": str(media.get("track-count") or ""),
            "disc_number": str(media.get("position") or ""),
            "disc_total": disc_total if disc_total not in ("", "0") else "",
        }

    # ── public API ────────────────────────────────────────────────────────

    def lookup_tags(
        self,
        title: str,
        artist: str = "",
        album: str = "",
        need: set[str] | frozenset[str] | None = None,
    ) -> dict | None:
        """Return tag fields for the best-matching recording, or None.

        Keys: title, artist, album, album_artist, genre, year, track_number,
        track_total, disc_number, disc_total. *need* names the fields the
        caller actually wants (default: all); the extra genre request is
        skipped when "genre" isn't among them.
        """
        title = (title or "").strip()
        artist = (artist or "").strip()
        album = (album or "").strip()
        if not title:
            return None
        need = frozenset(need) if need is not None else ALL_FIELDS

        if album and artist:
            tags = self._from_album(title, artist, album, need)
            if tags:
                return tags
        return self._from_recording(title, artist, album, need)
