import threading
import urllib.parse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

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

    def find_lyrics_exact(self, title: str, artist: str = "", album: str = "") -> str | None:
        """Single exact-match GET — fast path."""
        try:
            params: dict = {"track_name": title}
            if artist:
                params["artist_name"] = artist
            if album:
                params["album_name"] = album
            r = self._session.get(f"{self._BASE}/get", params=params, timeout=5)
            if r.status_code == 200:
                return (r.json().get("plainLyrics") or "").strip() or None
            return None
        except requests.RequestException as e:
            raise LyricsError(f"{self.NAME} (exact): {e}") from e

    def find_lyrics_fuzzy(self, title: str, artist: str = "", album: str = "") -> str | None:
        """Full-text search fallback."""
        try:
            q = f"{artist} {title}".strip() if artist else title
            r = self._session.get(f"{self._BASE}/search", params={"q": q}, timeout=5)
            r.raise_for_status()
            for hit in r.json()[:5]:
                lyrics = (hit.get("plainLyrics") or "").strip()
                if lyrics:
                    return lyrics
            return None
        except requests.RequestException as e:
            raise LyricsError(f"{self.NAME} (fuzzy): {e}") from e

    def find_lyrics(self, title: str, artist: str = "", album: str = "") -> str | None:
        """Sequential fallback (used by tests and CLI single-source path)."""
        return (
            self.find_lyrics_exact(title, artist, album)
            or self.find_lyrics_fuzzy(title, artist, album)
        )


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
            r = self._session.get(f"https://api.lyrics.ovh/v1/{a}/{t}", timeout=5)
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

    # Shared pool — avoids per-call thread-creation overhead when processing
    # many files in succession.
    _executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="lyrics")

    def __init__(self, musixmatch_key: str = "") -> None:
        self._lrclib = LrcLibSource()
        self._ovh = LyricsOvhSource()
        self._extra: list = []
        if musixmatch_key:
            self._extra.append(MusixmatchAdapter(musixmatch_key))

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
        ex = self._executor

        # Fire lrclib exact, lrclib fuzzy, and lyrics.ovh all at once so they
        # race instead of running sequentially.  lrclib exact typically answers
        # in < 300 ms when it has the track; fuzzy and ovh run in parallel so
        # the first hit from any path wins immediately.
        futures = {
            ex.submit(self._lrclib.find_lyrics_exact, title, artist, album): ("lrclib.net", False),
            ex.submit(self._lrclib.find_lyrics_fuzzy, title, artist, album): ("lrclib.net", True),
            ex.submit(self._ovh.find_lyrics, title, artist, album): ("lyrics.ovh", False),
        }
        for src in self._extra:
            futures[ex.submit(src.find_lyrics, title, artist, album)] = (src.NAME, False)

        pending = set(futures)
        deadline = 8  # seconds overall
        import time
        t0 = time.monotonic()

        while pending:
            elapsed = time.monotonic() - t0
            remaining = deadline - elapsed
            if remaining <= 0:
                break
            done, pending = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
            for f in done:
                try:
                    lyrics = f.result()
                except LyricsError:
                    continue
                if lyrics:
                    source_name = futures[f][0]
                    # Cancel remaining futures — we have our answer.
                    for p in pending:
                        p.cancel()
                    return lyrics, source_name

        return None, None
