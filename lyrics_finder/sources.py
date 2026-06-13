import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# Module-level cache: (title_lower, artist_lower) → (lyrics, source_name)
# Shared across all LyricsFinder instances in a process, so "Process All"
# never re-fetches the same track twice.
_cache: dict[tuple[str, str], tuple[str | None, str | None]] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


class LyricsError(Exception):
    pass


class LrcLibSource:
    """https://lrclib.net — free, no key, open lyrics database."""

    NAME = "lrclib.net"
    _BASE = "https://lrclib.net/api"

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers["Lrclib-Client"] = "MusicLyricsFinder/1.0"

    def find_lyrics(self, title: str, artist: str = "", album: str = "") -> str | None:
        try:
            # Exact lookup
            params: dict = {"track_name": title}
            if artist:
                params["artist_name"] = artist
            if album:
                params["album_name"] = album
            r = self._session.get(f"{self._BASE}/get", params=params, timeout=8)
            if r.status_code == 200:
                lyrics = (r.json().get("plainLyrics") or "").strip()
                if lyrics:
                    return lyrics

            # Fuzzy search fallback
            q = f"{artist} {title}".strip() if artist else title
            r = self._session.get(f"{self._BASE}/search", params={"q": q}, timeout=8)
            r.raise_for_status()
            for hit in r.json()[:5]:
                lyrics = (hit.get("plainLyrics") or "").strip()
                if lyrics:
                    return lyrics
            return None
        except requests.RequestException as e:
            raise LyricsError(f"{self.NAME}: {e}") from e


class LyricsOvhSource:
    """https://api.lyrics.ovh — free, no key."""

    NAME = "lyrics.ovh"

    def __init__(self) -> None:
        self._session = requests.Session()

    def find_lyrics(self, title: str, artist: str = "", album: str = "") -> str | None:
        if not artist:
            return None
        a = urllib.parse.quote(artist, safe="")
        t = urllib.parse.quote(title, safe="")
        try:
            r = self._session.get(f"https://api.lyrics.ovh/v1/{a}/{t}", timeout=8)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                return None
            return (data.get("lyrics") or "").strip() or None
        except requests.RequestException as e:
            raise LyricsError(f"{self.NAME}: {e}") from e


class MusixmatchAdapter:
    """Optional Musixmatch source — only used when an API key is supplied."""

    NAME = "musixmatch"

    def __init__(self, api_key: str) -> None:
        from .musixmatch import MusixmatchClient, MusixmatchError
        self._client = MusixmatchClient(api_key)
        self._MusixmatchError = MusixmatchError

    def find_lyrics(self, title: str, artist: str = "", album: str = "") -> str | None:
        try:
            results = self._client.search_track(title, artist)
            if not results:
                return None
            track_id = results[0]["track"]["track_id"]
            return self._client.get_lyrics(track_id)
        except self._MusixmatchError as e:
            raise LyricsError(str(e)) from e


class LyricsFinder:
    """Query all sources in parallel; return the first successful result."""

    def __init__(self, musixmatch_key: str = "") -> None:
        self._sources: list = [LrcLibSource(), LyricsOvhSource()]
        if musixmatch_key:
            self._sources.append(MusixmatchAdapter(musixmatch_key))

    def find(self, title: str, artist: str = "", album: str = "") -> tuple[str | None, str | None]:
        """Return (lyrics, source_name) or (None, None). Results are cached."""
        key = (title.lower().strip(), artist.lower().strip())
        with _cache_lock:
            if key in _cache:
                return _cache[key]

        result = self._find_parallel(title, artist, album)

        with _cache_lock:
            _cache[key] = result
        return result

    def _find_parallel(self, title: str, artist: str, album: str) -> tuple[str | None, str | None]:
        if not self._sources:
            return None, None

        executor = ThreadPoolExecutor(max_workers=len(self._sources))
        futures = {
            executor.submit(src.find_lyrics, title, artist, album): src
            for src in self._sources
        }

        result: tuple[str | None, str | None] = (None, None)
        try:
            for future in as_completed(futures, timeout=12):
                src = futures[future]
                try:
                    lyrics = future.result()
                    if lyrics:
                        result = (lyrics, src.NAME)
                        break
                except LyricsError:
                    continue
        except TimeoutError:
            pass
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        return result
