"""Album-art search via the public iTunes Search API.

Mirrors the approach of Ben Dodson's iTunes Artwork Finder
(https://bendodson.com/projects/itunes-artwork-finder/): query the iTunes
Search API, then upscale the returned 100×100 thumbnail URL to a high
resolution by rewriting its dimensions in the path.
"""

import re
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher

import requests

# The Search API returns thumbnail URLs like
#   https://is1-ssl.mzstatic.com/image/thumb/.../source/100x100bb.jpg
# Rewriting the "100x100bb" segment yields any resolution Apple has on file.
_SIZE_RE = re.compile(r"/(\d+)x(\d+)(?:bb|cc|sr)?\.(jpg|jpeg|png)", re.IGNORECASE)

# Noise we strip before comparing titles/albums: parentheticals, "feat." credits,
# and edition/remaster qualifiers that differ between the file tag and the store.
_PAREN_RE = re.compile(r"[\(\[\{].*?[\)\]\}]")
_FEAT_RE = re.compile(r"\b(feat|ft|featuring|with)\b.*", re.IGNORECASE)
_NOISE_RE = re.compile(
    r"\b(remaster(ed)?|deluxe|edition|version|explicit|clean|mono|stereo|"
    r"bonus|track|single|ep|original|expanded|anniversary|remix)\b",
    re.IGNORECASE,
)
_NONWORD_RE = re.compile(r"[^a-z0-9]+")


def _normalize(text: str) -> str:
    """Lower-case and strip punctuation/credits so titles compare cleanly."""
    text = (text or "").lower()
    text = _PAREN_RE.sub(" ", text)
    text = _FEAT_RE.sub(" ", text)
    text = _NOISE_RE.sub(" ", text)
    text = _NONWORD_RE.sub(" ", text)
    return " ".join(text.split())


def _similar(a: str, b: str) -> float:
    """Fuzzy 0–1 similarity between two normalized strings."""
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    # Reward containment (e.g. "yesterday" vs "yesterday remastered 2009").
    if na in nb or nb in na:
        return 0.92
    return SequenceMatcher(None, na, nb).ratio()


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

    @staticmethod
    def _score(result: "ArtworkResult", title: str, artist: str, album: str) -> float:
        """Rate how well a candidate matches the track's tags (higher = better)."""
        score = 0.0
        # Artist agreement is the strongest signal — a right cover from the
        # wrong artist is still the wrong cover.
        if artist:
            score += 2.0 * _similar(artist, result.artist)
        # Album/track agreement.
        if album:
            score += 2.5 * _similar(album, result.album)
        if title:
            # A song result carries the track name; an album result doesn't.
            target = result.track or result.album
            score += 1.5 * _similar(title, target)
        return score

    def find(
        self,
        title: str,
        artist: str = "",
        album: str = "",
        size: int = DEFAULT_SIZE,
    ) -> ArtworkResult | None:
        """Best single match for a track's tags, or None.

        Gathers candidates from several queries, then ranks them by how closely
        the artist/album/title agree with the file's tags rather than trusting
        the iTunes Store's own ordering. When an album tag is present, a
        candidate whose album title matches it exactly always wins.
        """
        queries: list[tuple[str, str]] = []
        if album:
            queries.append((f"{artist} {album}".strip(), "album"))
        if title:
            queries.append((f"{artist} {title}".strip(), "song"))
            if not album:
                queries.append((f"{artist} {title}".strip(), "album"))

        candidates: list[ArtworkResult] = []
        seen: set[str] = set()
        for term, entity in queries:
            try:
                results = self.search(term, entity=entity, size=size)
            except ArtworkError:
                continue
            for r in results:
                if r.art_url in seen:
                    continue
                seen.add(r.art_url)
                candidates.append(r)

        if not candidates:
            return None

        # When the file has an album tag, require an exact (normalized) album
        # title match if any candidate offers one — this is the user's hard
        # constraint against grabbing a similarly-named album.
        if album:
            want = _normalize(album)
            exact = [c for c in candidates if _normalize(c.album) == want]
            if exact:
                if artist:
                    exact.sort(
                        key=lambda c: _similar(artist, c.artist), reverse=True
                    )
                return exact[0]

        ranked = sorted(
            candidates,
            key=lambda c: self._score(c, title, artist, album),
            reverse=True,
        )
        return ranked[0]

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
