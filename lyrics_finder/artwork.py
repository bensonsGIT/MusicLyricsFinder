"""Album-art search via the public iTunes Search API.

Mirrors the approach of Ben Dodson's iTunes Artwork Finder
(https://bendodson.com/projects/itunes-artwork-finder/): query the iTunes
Search API, then upscale the returned 100×100 thumbnail URL to a high
resolution by rewriting its dimensions in the path.
"""

import re
from dataclasses import asdict, dataclass

import requests

# The Search API returns thumbnail URLs like
#   https://is1-ssl.mzstatic.com/image/thumb/.../source/100x100bb.jpg
# Rewriting the "100x100bb" segment yields any resolution Apple has on file.
_SIZE_RE = re.compile(r"/(\d+)x(\d+)(?:bb|cc|sr)?\.(jpg|jpeg|png)", re.IGNORECASE)


class ArtworkError(Exception):
    pass


@dataclass
class ArtworkResult:
    artist: str
    album: str
    track: str
    thumb_url: str   # ~200px preview for the UI
    art_url: str     # full-resolution image to embed
    kind: str        # "album" or "song"

    def as_dict(self) -> dict:
        return asdict(self)


class ArtworkFinder:
    """Search the iTunes Store for album art — free, no API key."""

    SEARCH_URL = "https://itunes.apple.com/search"
    DEFAULT_SIZE = 600

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "MusicLyricsFinder/1.0"

    @staticmethod
    def _scale(url: str, size: int) -> str:
        """Rewrite an iTunes artwork URL to request a square ``size`` image."""
        if not url:
            return url
        return _SIZE_RE.sub(lambda m: f"/{size}x{size}bb.{m.group(3)}", url)

    def search(
        self,
        term: str,
        entity: str = "album",
        country: str = "US",
        limit: int = 12,
        size: int = DEFAULT_SIZE,
    ) -> list[ArtworkResult]:
        """Return artwork candidates for *term* (most relevant first)."""
        term = (term or "").strip()
        if not term:
            return []

        params = {"term": term, "entity": entity, "limit": limit, "country": country}
        try:
            r = self._session.get(self.SEARCH_URL, params=params, timeout=10)
            r.raise_for_status()
            payload = r.json()
        except requests.RequestException as e:
            raise ArtworkError(f"iTunes search failed: {e}") from e
        except ValueError as e:
            raise ArtworkError(f"iTunes returned invalid JSON: {e}") from e

        results: list[ArtworkResult] = []
        seen: set[str] = set()
        for item in payload.get("results", []):
            base = item.get("artworkUrl100") or item.get("artworkUrl60") or ""
            if not base:
                continue
            art_url = self._scale(base, size)
            if art_url in seen:
                continue
            seen.add(art_url)
            results.append(
                ArtworkResult(
                    artist=item.get("artistName", ""),
                    album=item.get("collectionName", ""),
                    track=item.get("trackName", ""),
                    thumb_url=self._scale(base, 200),
                    art_url=art_url,
                    kind=entity,
                )
            )
        return results

    def find(
        self,
        title: str,
        artist: str = "",
        album: str = "",
        size: int = DEFAULT_SIZE,
    ) -> ArtworkResult | None:
        """Best-effort single match for a track's tags, or None."""
        queries: list[tuple[str, str]] = []
        if album:
            queries.append((f"{artist} {album}".strip(), "album"))
        if title:
            queries.append((f"{artist} {title}".strip(), "song"))
            if not album:
                queries.append((f"{artist} {title}".strip(), "album"))

        for term, entity in queries:
            try:
                results = self.search(term, entity=entity, size=size)
            except ArtworkError:
                continue
            if results:
                return results[0]
        return None

    def download(self, url: str) -> tuple[bytes, str]:
        """Fetch an artwork image, returning (bytes, mime-type)."""
        try:
            r = self._session.get(url, timeout=15)
            r.raise_for_status()
        except requests.RequestException as e:
            raise ArtworkError(f"Failed to download artwork: {e}") from e

        mime = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if mime not in ("image/jpeg", "image/png"):
            mime = "image/png" if url.lower().endswith(".png") else "image/jpeg"
        return r.content, mime
