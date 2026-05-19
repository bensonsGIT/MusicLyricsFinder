import urllib.parse
import requests


class LyricsError(Exception):
    pass


class LrcLibSource:
    """https://lrclib.net — free, no key, open lyrics database."""

    NAME = "lrclib.net"
    _BASE = "https://lrclib.net/api"
    _HEADERS = {"Lrclib-Client": "MusicLyricsFinder/1.0"}

    def find_lyrics(self, title: str, artist: str = "", album: str = "") -> str | None:
        try:
            # Exact lookup first
            params: dict = {"track_name": title}
            if artist:
                params["artist_name"] = artist
            if album:
                params["album_name"] = album
            r = requests.get(f"{self._BASE}/get", params=params, headers=self._HEADERS, timeout=10)
            if r.status_code == 200:
                lyrics = (r.json().get("plainLyrics") or "").strip()
                if lyrics:
                    return lyrics

            # Fuzzy search fallback
            q = f"{artist} {title}".strip() if artist else title
            r = requests.get(f"{self._BASE}/search", params={"q": q}, headers=self._HEADERS, timeout=10)
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

    def find_lyrics(self, title: str, artist: str = "", album: str = "") -> str | None:
        if not artist:
            return None  # API requires both fields
        a = urllib.parse.quote(artist, safe="")
        t = urllib.parse.quote(title, safe="")
        try:
            r = requests.get(f"https://api.lyrics.ovh/v1/{a}/{t}", timeout=10)
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

    def __init__(self, api_key: str):
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
    """Query all sources in parallel; return the first hit."""

    def __init__(self, musixmatch_key: str = ""):
        self._sources: list = [LrcLibSource(), LyricsOvhSource()]
        if musixmatch_key:
            self._sources.append(MusixmatchAdapter(musixmatch_key))

    def find(self, title: str, artist: str = "", album: str = "") -> tuple[str | None, str | None]:
        """Return (lyrics, source_name) or (None, None).

        All sources are queried concurrently; the first non-empty result wins.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _query(source):
            try:
                lyrics = source.find_lyrics(title, artist, album)
                return (lyrics, source.NAME) if lyrics else (None, None)
            except LyricsError:
                return (None, None)

        with ThreadPoolExecutor(max_workers=len(self._sources)) as ex:
            futures = {ex.submit(_query, src): src for src in self._sources}
            for fut in as_completed(futures):
                lyrics, name = fut.result()
                if lyrics:
                    return lyrics, name
        return None, None
