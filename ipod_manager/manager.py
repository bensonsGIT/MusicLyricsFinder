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

    def sync_lyrics(self, finder) -> dict[str, str]:
        """
        Find and embed lyrics for every track on the iPod.

        *finder* is a ``LyricsFinder`` instance from lyrics_finder.sources.
        Returns a dict mapping track title → source name (or 'not found').
        """
        from lyrics_finder.metadata import write_lyrics

        results: dict[str, str] = {}
        for track in self.list_tracks():
            local = track.local_path(self.mount.root)
            if not local.exists():
                continue
            key = track.title or local.name
            lyrics, source = finder.find(track.title, track.artist, track.album)
            if lyrics:
                try:
                    write_lyrics(local, lyrics, track.title, track.artist, track.album)
                    results[key] = source
                except Exception as exc:
                    results[key] = f"error: {exc}"
            else:
                results[key] = "not found"
        return results

    def update_metadata(
        self,
        track_id: int,
        *,
        title: str | None = None,
        artist: str | None = None,
        album: str | None = None,
        genre: str | None = None,
        year: int | None = None,
        track_number: int | None = None,
    ) -> bool:
        """Update tag fields on both the audio file and the database entry."""
        from lyrics_finder.metadata import write_lyrics

        db = self._load_db()
        target = next((t for t in db.tracks if t.track_id == track_id), None)
        if target is None:
            return False

        if title is not None:
            target.title = title
        if artist is not None:
            target.artist = artist
        if album is not None:
            target.album = album
        if genre is not None:
            target.genre = genre
        if year is not None:
            target.year = year
        if track_number is not None:
            target.track_number = track_number

        local = target.local_path(self.mount.root)
        if local.exists():
            _write_audio_meta(local, target)

        if not self.mount.rockbox:
            self._save_db(db)
        return True

    # --------------------------------------------------------------- private

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
        db_path = self.mount.itunesdb_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db.write(db_path)
        self._db = db

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


def _write_audio_meta(path: Path, t: Track) -> None:
    from mutagen.id3 import TIT2, TPE1, TALB, TCON, TRCK, TDRC
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        if t.title:
            tags["TIT2"] = TIT2(encoding=3, text=t.title)
        if t.artist:
            tags["TPE1"] = TPE1(encoding=3, text=t.artist)
        if t.album:
            tags["TALB"] = TALB(encoding=3, text=t.album)
        if t.genre:
            tags["TCON"] = TCON(encoding=3, text=t.genre)
        if t.track_number:
            tags["TRCK"] = TRCK(encoding=3, text=str(t.track_number))
        if t.year:
            tags["TDRC"] = TDRC(encoding=3, text=str(t.year))
        tags.save(path)
    elif suffix in (".m4a", ".aac"):
        audio = MP4(path)
        if audio.tags is None:
            audio.add_tags()
        if t.title:
            audio.tags["\xa9nam"] = [t.title]
        if t.artist:
            audio.tags["\xa9ART"] = [t.artist]
        if t.album:
            audio.tags["\xa9alb"] = [t.album]
        if t.genre:
            audio.tags["\xa9gen"] = [t.genre]
        if t.track_number:
            audio.tags["trkn"] = [(t.track_number, 0)]
        if t.year:
            audio.tags["\xa9day"] = [str(t.year)]
        audio.save()


def _file_type(suffix: str) -> str:
    return {"mp3": "MP3 ", "m4a": "M4A ", "aac": "AAC "}.get(suffix.lstrip(".").lower(), "    ")


def _subdir_index(name: str) -> int:
    try:
        return int(name[1:])
    except (ValueError, IndexError):
        return -1
