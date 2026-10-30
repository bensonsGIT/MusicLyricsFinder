"""Tag lookup via MusicBrainz (https://musicbrainz.org) — free, no API key.

MusicBrainz is a community-curated open music database; its release data is
far more reliable than store catalogues. A recording usually appears on many
releases (the original album, singles, compilations, live albums), so we pick
the release that best matches the file: the file's own album when it has one,
otherwise the earliest official studio album.

MusicBrainz asks clients to send a descriptive User-Agent and stay under one
request per second; both are enforced here.
"""

import re
import threading
import time

import requests

from .artwork import _normalize, _similar

_LUCENE_SPECIAL = re.compile(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)')


def _escape(text: str) -> str:
    return _LUCENE_SPECIAL.sub(r"\\\1", text)


def _credit(artist_credit: list | None) -> str:
    """Render a MusicBrainz artist-credit list as one display string."""
    return "".join(
        (c.get("name") or c.get("artist", {}).get("name", "")) + (c.get("joinphrase") or "")
        for c in (artist_credit or [])
    ).strip()


class MusicBrainzError(Exception):
    pass


class MusicBrainzTags:
    BASE = "https://musicbrainz.org/ws/2"
    USER_AGENT = "MusicLyricsFinder/1.0 (https://github.com/bensonsGIT/MusicLyricsFinder)"
    MIN_INTERVAL = 1.1  # seconds between requests (MusicBrainz limit: 1/s)

    # Shared across instances: the rate limit is per client IP, not per object.
    _lock = threading.Lock()
    _last_request = 0.0
    _genre_cache: dict[str, str] = {}

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers["User-Agent"] = self.USER_AGENT
        self._session.headers["Accept"] = "application/json"

    def _get(self, path: str, params: dict) -> dict:
        with MusicBrainzTags._lock:
            wait = MusicBrainzTags._last_request + self.MIN_INTERVAL - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                r = self._session.get(f"{self.BASE}/{path}", params={**params, "fmt": "json"}, timeout=15)
            except requests.RequestException as e:
                raise MusicBrainzError(f"MusicBrainz request failed: {e}") from e
            finally:
                MusicBrainzTags._last_request = time.monotonic()
        try:
            if r.status_code == 503:
                raise MusicBrainzError("MusicBrainz is rate-limiting requests; try again shortly")
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            raise MusicBrainzError(f"MusicBrainz request failed: {e}") from e
        except ValueError as e:
            raise MusicBrainzError(f"MusicBrainz returned invalid JSON: {e}") from e

    # ── release choice ────────────────────────────────────────────────────

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
            key = f"{entity}:{mbid}"
            if key not in self._genre_cache:
                try:
                    data = self._get(f"{entity}/{mbid}", {"inc": "genres"})
                except MusicBrainzError:
                    continue
                genres = sorted(data.get("genres") or [], key=lambda g: -g.get("count", 0))
                self._genre_cache[key] = genres[0]["name"].title() if genres else ""
            if self._genre_cache[key]:
                return self._genre_cache[key]
        return ""

    # ── public API ────────────────────────────────────────────────────────

    def lookup_tags(self, title: str, artist: str = "", album: str = "") -> dict | None:
        """Return tag fields for the best-matching recording, or None.

        Keys: title, artist, album, album_artist, genre, year, track_number,
        track_total, disc_number, disc_total.
        """
        title = (title or "").strip()
        if not title:
            return None

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
        artist_id = (credit[0].get("artist") or {}).get("id", "") if credit else ""

        return {
            "title": rec.get("title", ""),
            "artist": _credit(credit),
            "album": release.get("title", ""),
            "album_artist": _credit(release.get("artist-credit")) or _credit(credit),
            "genre": self._genre(rg.get("id", ""), artist_id),
            "year": year,
            "track_number": track_number,
            "track_total": str(media.get("track-count") or ""),
            "disc_number": str(media.get("position") or ""),
            "disc_total": disc_total if disc_total not in ("", "0") else "",
        }
