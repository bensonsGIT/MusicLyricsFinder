"""High-level iPod music manager."""

import shutil
from pathlib import Path

from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.id3 import ID3, ID3NoHeaderError

from .detector import IpodMount
from .itunesdb import iTunesDB, Track

_SUPPORTED = {".mp3", ".m4a", ".aac"}
_NUM_SUBDIRS = 50  # F00–F49


class IpodManager:
    def __init__(self, mount: IpodMount):
        self.mount = mount
        self._db: iTunesDB | None = None

    # ---------------------------------------------------------------- public

    def list_tracks(self) -> list[Track]:
        if self.mount.rockbox:
            return self._scan_tracks()
        return self._load_db().tracks

    def add_track(
        self,
        source: Path,
        *,
        title: str = "",
        artist: str = "",
        album: str = "",
        genre: str = "",
        track_number: int = 0,
        year: int = 0,
    ) -> Track:
        """Copy *source* to the iPod and register it in the database."""
        if source.suffix.lower() not in _SUPPORTED:
            raise ValueError(f"Unsupported format: {source.suffix!r}")

        meta = _read_audio_meta(source)
        # CLI overrides win
        if title:
            meta["title"] = title
        if artist:
            meta["artist"] = artist
        if album:
            meta["album"] = album
        if genre:
            meta["genre"] = genre
        if track_number:
            meta["track_number"] = track_number
        if year:
            meta["year"] = year

        db = self._load_db()
        dest_dir, subdir_name = self._pick_subdir(db)
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Use a short unique name so the iPod is happy
        next_id = max((t.track_id for t in db.tracks), default=0) + 1
        filename = f"{next_id:04d}{source.suffix.lower()}"
        dest = dest_dir / filename
        shutil.copy2(source, dest)

        ipod_path = f":iPod_Control:Music:{subdir_name}:{filename}"
        t = Track(
            title=meta.get("title", source.stem),
            artist=meta.get("artist", ""),
            album=meta.get("album", ""),
            genre=meta.get("genre", ""),
            ipod_path=ipod_path,
            duration_ms=meta.get("duration_ms", 0),
            file_size=dest.stat().st_size,
            bitrate=meta.get("bitrate", 0),
            sample_rate=meta.get("sample_rate", 0),
            track_number=meta.get("track_number", 0),
            year=meta.get("year", 0),
            file_type=_file_type(source.suffix),
        )

        db.tracks.append(t)
        db._normalise_ids()
        if not self.mount.rockbox:
            self._save_db(db)
        else:
            self._db = db

        return t

    def remove_track(self, track_id: int) -> bool:
        """Remove a track by its ID. Returns True if found and removed."""
        db = self._load_db()
        target = next((t for t in db.tracks if t.track_id == track_id), None)
        if target is None:
            return False

        local = target.local_path(self.mount.root)
        if local.exists():
            local.unlink()

        if not self.mount.rockbox:
            db.tracks = [t for t in db.tracks if t.track_id != track_id]
            self._save_db(db)

        return True

    def update_lyrics(self, track_id: int, lyrics: str) -> bool:
        """Write *lyrics* to the audio file and the iTunesDB entry.

        Stock firmware iPods read lyrics from the DB; writing to the file
        alone is not enough. This method updates both.
        """
        from lyrics_finder.metadata import write_lyrics as _write_lyrics

        db = self._load_db()
        target = next((t for t in db.tracks if t.track_id == track_id), None)
        if target is None:
            return False

        local = target.local_path(self.mount.root)
        if local.exists():
            _write_lyrics(local, lyrics, target.title, target.artist, target.album)

        target.lyrics = lyrics

        if not self.mount.rockbox:
            self._save_db(db)
        return True

    def sync_lyrics(self, finder, *, workers: int = 20) -> dict[str, str]:
        """
        Find and embed lyrics for every track on the iPod.

        *finder* is a ``LyricsFinder`` instance from lyrics_finder.sources.
        Tracks are processed concurrently using *workers* threads.
        Returns a dict mapping track title → source name (or 'not found').
        """
        from concurrent.futures import ThreadPoolExecutor
        from lyrics_finder.metadata import write_lyrics

        _unhide_ipod_dirs(self.mount.root)
        db = self._load_db()
        db_by_id = {t.track_id: t for t in db.tracks}
        db_dirty = False

        def _process(track):
            local = track.local_path(self.mount.root)
            key = track.title or local.name
            if not _accessible(local):
                return key, "not found (file missing)", None, None
            lyrics, source = finder.find(track.title, track.artist, track.album)
            if lyrics:
                try:
                    write_lyrics(local, lyrics, track.title, track.artist, track.album)
                    return key, source, track.track_id, lyrics
                except Exception as exc:
                    return key, f"error: {exc}", None, None
            return key, "not found", None, None

        results: dict[str, str] = {}
        nonlocal_dirty = [False]
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for key, status, tid, lyrics in ex.map(_process, self.list_tracks()):
                results[key] = status
                if tid is not None and tid in db_by_id:
                    db_by_id[tid].lyrics = lyrics
                    nonlocal_dirty[0] = True

        if nonlocal_dirty[0] and not self.mount.rockbox:
            self._save_db(db)
        return results

    def update_metadata(
        self,
        track_id: int,
        *,
        title: str | None = None,
        artist: str | None = None,
        album: str | None = None,
        album_artist: str | None = None,
        genre: str | None = None,
        year: int | None = None,
        track_number: int | None = None,
        composer: str | None = None,
        comment: str | None = None,
    ) -> bool:
        """Update tag fields on both the audio file and the database entry."""
        db = self._load_db()
        target = next((t for t in db.tracks if t.track_id == track_id), None)
        if target is None:
            return False
        _apply_fields(target, title, artist, album, genre, year, track_number)
        local = target.local_path(self.mount.root)
        if local.exists():
            _write_meta_to_file(local, title=title, artist=artist, album=album,
                                album_artist=album_artist, genre=genre,
                                year=str(year) if year else None,
                                track_number=str(track_number) if track_number else None,
                                composer=composer, comment=comment)
        if not self.mount.rockbox:
            self._save_db(db)
        return True

    def update_all_tracks(
        self,
        *,
        title: str | None = None,
        artist: str | None = None,
        album: str | None = None,
        album_artist: str | None = None,
        genre: str | None = None,
        year: int | None = None,
        track_number: int | None = None,
        composer: str | None = None,
        comment: str | None = None,
    ) -> tuple[int, int]:
        """
        Apply the given fields to every track on the iPod.

        Only supplied (non-None) arguments are written; others are left alone.
        Returns (updated, failed) counts.
        """
        tracks = self.list_tracks()
        updated = failed = 0
        for track in tracks:
            try:
                local = track.local_path(self.mount.root)
                if not local.exists():
                    failed += 1
                    continue
                _write_meta_to_file(local, title=title, artist=artist, album=album,
                                    album_artist=album_artist, genre=genre,
                                    year=str(year) if year else None,
                                    track_number=str(track_number) if track_number else None,
                                    composer=composer, comment=comment)
                updated += 1
            except Exception:
                failed += 1

        if not self.mount.rockbox:
            db = self._load_db()
            for track in db.tracks:
                _apply_fields(track, title, artist, album, genre, year, track_number)
            self._save_db(db)

        return updated, failed

    def push_file_tags_to_db(
        self,
        *,
        fields: set[str] | None = None,
        on_progress=None,
    ) -> tuple[int, int]:
        """Read embedded tags from each iPod audio file and update the iTunesDB.

        This is the right tool after you have already modified files with
        Lyrics Finder or any other tagger — it bridges the gap between
        what is embedded in the file and what the stock firmware actually
        reads from the DB.

        *fields*: which tag fields to sync from file → DB.  Defaults to all:
            {"title", "artist", "album", "genre", "composer", "year",
             "track_number", "lyrics", "file_size"}.

        *on_progress*: optional callable(track, status_str) called after
            each file so callers can stream progress.

        Returns (updated, failed) counts.
        """
        from lyrics_finder.metadata import read_metadata

        ALL_FIELDS = {
            "title", "artist", "album", "genre", "composer",
            "year", "track_number", "lyrics", "file_size",
        }
        sync = fields if fields is not None else ALL_FIELDS

        db = self._load_db()
        updated = failed = 0

        for track in db.tracks:
            local = track.local_path(self.mount.root)
            if not _accessible(local):
                failed += 1
                if on_progress:
                    on_progress(track, "missing")
                continue
            try:
                meta = read_metadata(local)

                if "title" in sync and meta.get("title"):
                    track.title = meta["title"]
                if "artist" in sync and meta.get("artist"):
                    track.artist = meta["artist"]
                if "album" in sync and meta.get("album"):
                    track.album = meta["album"]
                if "genre" in sync and meta.get("genre"):
                    track.genre = meta["genre"]
                if "composer" in sync and meta.get("composer"):
                    track.composer = meta["composer"]
                if "year" in sync and meta.get("year"):
                    raw = str(meta["year"])
                    if raw[:4].isdigit():
                        track.year = int(raw[:4])
                if "track_number" in sync and meta.get("track_number"):
                    try:
                        track.track_number = int(meta["track_number"])
                    except (ValueError, TypeError):
                        pass
                if "lyrics" in sync:
                    track.lyrics = meta.get("lyrics") or ""
                if "file_size" in sync:
                    track.file_size = local.stat().st_size

                updated += 1
                if on_progress:
                    on_progress(track, "updated")
            except Exception as exc:
                failed += 1
                if on_progress:
                    on_progress(track, f"error: {exc}")

        if not self.mount.rockbox:
            self._save_db(db)
        return updated, failed

    def _load_db(self) -> iTunesDB:
        if self._db is not None:
            return self._db
        db_path = self.mount.itunesdb_path
        if db_path.exists():
            self._db = iTunesDB.read(db_path)
        else:
            self._db = iTunesDB()
            if self.mount.rockbox:
                # Populate from filesystem since there is no iTunesDB
                self._db.tracks = self._scan_tracks()
        return self._db

    def _save_db(self, db: iTunesDB) -> None:
        from .hash import apply_hash
        db_path = self.mount.itunesdb_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db.write(db_path)
        self._db = db
        # Apply the firmware hash required by iPod Classic 6G/7G stock firmware.
        # Failures are non-fatal: the DB is still written; warn at the call site.
        try:
            self._last_hash_method = apply_hash(db_path, self.mount.root)
        except RuntimeError as exc:
            self._last_hash_method = f"error: {exc}"
        except Exception as exc:
            self._last_hash_method = f"error: {exc}"

    def _scan_tracks(self) -> list[Track]:
        """Build track list by scanning audio files (used for Rockbox mode)."""
        tracks: list[Track] = []
        tid = 1
        for ext in _SUPPORTED:
            for p in sorted(self.mount.music_dir.rglob(f"*{ext}")):
                meta = _read_audio_meta(p)
                parts = p.relative_to(self.mount.root).parts
                ipod_path = ":" + ":".join(parts)
                tracks.append(
                    Track(
                        track_id=tid,
                        title=meta.get("title", p.stem),
                        artist=meta.get("artist", ""),
                        album=meta.get("album", ""),
                        genre=meta.get("genre", ""),
                        ipod_path=ipod_path,
                        duration_ms=meta.get("duration_ms", 0),
                        file_size=p.stat().st_size,
                        bitrate=meta.get("bitrate", 0),
                        sample_rate=meta.get("sample_rate", 0),
                        track_number=meta.get("track_number", 0),
                        year=meta.get("year", 0),
                        file_type=_file_type(p.suffix),
                    )
                )
                tid += 1
        return tracks

    def _pick_subdir(self, db: iTunesDB) -> tuple[Path, str]:
        """Choose the least-populated F00–F49 subdirectory."""
        counts = [0] * _NUM_SUBDIRS
        for t in db.tracks:
            parts = [p for p in t.ipod_path.split(":") if p]
            if len(parts) >= 3:
                name = parts[2]  # e.g. "F01"
                idx = _subdir_index(name)
                if 0 <= idx < _NUM_SUBDIRS:
                    counts[idx] += 1

        best = counts.index(min(counts))
        name = f"F{best:02d}"
        return self.mount.music_dir / name, name


# ------------------------------------------------------------------ helpers

def _read_audio_meta(path: Path) -> dict:
    meta: dict = {}
    try:
        suffix = path.suffix.lower()
        if suffix == ".mp3":
            audio = MP3(path)
            meta["duration_ms"] = int((audio.info.length or 0) * 1000)
            meta["bitrate"] = getattr(audio.info, "bitrate", 0)
            meta["sample_rate"] = getattr(audio.info, "sample_rate", 0)
            try:
                tags = ID3(path)
            except ID3NoHeaderError:
                tags = {}
            meta["title"] = str(tags.get("TIT2", ""))
            meta["artist"] = str(tags.get("TPE1", ""))
            meta["album"] = str(tags.get("TALB", ""))
            meta["genre"] = str(tags.get("TCON", ""))
            trck = str(tags.get("TRCK", ""))
            meta["track_number"] = int(trck.split("/")[0]) if trck.isdigit() or "/" in trck else 0
            tdrc = str(tags.get("TDRC", ""))
            meta["year"] = int(str(tdrc)[:4]) if tdrc and str(tdrc)[:4].isdigit() else 0
        elif suffix in (".m4a", ".aac"):
            audio = MP4(path)
            meta["duration_ms"] = int((audio.info.length or 0) * 1000)
            meta["bitrate"] = getattr(audio.info, "bitrate", 0)
            meta["sample_rate"] = getattr(audio.info, "sample_rate", 0)
            tags = audio.tags or {}
            meta["title"] = (tags.get("\xa9nam") or [""])[0]
            meta["artist"] = (tags.get("\xa9ART") or [""])[0]
            meta["album"] = (tags.get("\xa9alb") or [""])[0]
            meta["genre"] = (tags.get("\xa9gen") or [""])[0]
            trkn = tags.get("trkn")
            meta["track_number"] = trkn[0][0] if trkn else 0
            year_raw = (tags.get("\xa9day") or [""])[0]
            meta["year"] = int(str(year_raw)[:4]) if year_raw and str(year_raw)[:4].isdigit() else 0
    except Exception:
        pass
    return meta


def _apply_fields(track: Track, title, artist, album, genre, year, track_number) -> None:
    """Copy non-None values into a Track dataclass."""
    if title is not None:        track.title = title
    if artist is not None:       track.artist = artist
    if album is not None:        track.album = album
    if genre is not None:        track.genre = genre
    if year is not None:         track.year = year
    if track_number is not None: track.track_number = track_number


def _write_meta_to_file(path: Path, **kwargs) -> None:
    """Write only the supplied (non-None) kwargs to the audio file."""
    from lyrics_finder.metadata import write_metadata
    filtered = {k: v for k, v in kwargs.items() if v is not None}
    if filtered:
        write_metadata(path, **filtered)


def _file_type(suffix: str) -> str:
    return {"mp3": "MP3 ", "m4a": "M4A ", "aac": "AAC "}.get(suffix.lstrip(".").lower(), "    ")


def _subdir_index(name: str) -> int:
    try:
        return int(name[1:])
    except (ValueError, IndexError):
        return -1


def _accessible(path: Path) -> bool:
    """Return True if *path* exists and is accessible (distinguishes PermissionError)."""
    try:
        path.stat()
        return True
    except (FileNotFoundError, OSError, ValueError):
        return False


def _unhide_ipod_dirs(root: Path) -> None:
    """Remove the macOS HFS+ 'hidden' flag from iPod_Control and its subdirs.

    iTunes sets UF_HIDDEN on these folders so Finder won't show them.
    Python's Path.stat() can still be blocked by this on some macOS versions,
    so we clear the flag with chflags before accessing the files.
    """
    import subprocess
    import sys
    if sys.platform != "darwin":
        return
    targets = [
        root / "iPod_Control",
        root / "iPod_Control" / "Music",
        root / "iPod_Control" / "iTunes",
    ]
    existing = [str(p) for p in targets if p.parent.exists()]
    if existing:
        try:
            subprocess.run(["chflags", "nohidden"] + existing,
                           capture_output=True, timeout=10)
        except Exception:
            pass
