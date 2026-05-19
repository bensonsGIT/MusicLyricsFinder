from pathlib import Path

from mutagen.mp3 import MP3
from mutagen.id3 import ID3, ID3NoHeaderError, TIT2, TPE1, TALB, USLT, TCON, TRCK, TDRC, TPOS
from mutagen.mp4 import MP4

_SENTINEL = object()  # distinguishes "not provided" from ""


def _open_mp3(path: Path) -> tuple[MP3, ID3]:
    audio = MP3(path)
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
        tags.save(path)
        tags = ID3(path)
    return audio, tags


def read_metadata(path: Path) -> dict:
    """Return all readable tag fields for *path* as a flat dict."""
    suffix = path.suffix.lower()
    info = {
        "title": "", "artist": "", "album": "", "album_artist": "",
        "genre": "", "year": "", "track_number": "", "track_total": "",
        "disc_number": "", "disc_total": "", "composer": "", "comment": "",
        "lyrics": "",
    }

    if suffix == ".mp3":
        audio, tags = _open_mp3(path)
        info["title"]        = str(tags.get("TIT2", ""))
        info["artist"]       = str(tags.get("TPE1", ""))
        info["album"]        = str(tags.get("TALB", ""))
        info["album_artist"] = str(tags.get("TPE2", ""))
        info["genre"]        = str(tags.get("TCON", ""))
        info["year"]         = str(tags.get("TDRC", ""))
        info["composer"]     = str(tags.get("TCOM", ""))
        trck = str(tags.get("TRCK", ""))
        if "/" in trck:
            info["track_number"], info["track_total"] = trck.split("/", 1)
        else:
            info["track_number"] = trck
        tpos = str(tags.get("TPOS", ""))
        if "/" in tpos:
            info["disc_number"], info["disc_total"] = tpos.split("/", 1)
        else:
            info["disc_number"] = tpos
        comm = tags.getall("COMM")
        info["comment"] = comm[0].text[0] if comm else ""
        uslt = tags.getall("USLT")
        info["lyrics"] = uslt[0].text if uslt else ""
    elif suffix in (".m4a", ".aac"):
        tags = MP4(path).tags or {}
        info["title"]        = (tags.get("\xa9nam") or [""])[0]
        info["artist"]       = (tags.get("\xa9ART") or [""])[0]
        info["album"]        = (tags.get("\xa9alb") or [""])[0]
        info["album_artist"] = (tags.get("aART")   or [""])[0]
        info["genre"]        = (tags.get("\xa9gen") or [""])[0]
        info["year"]         = (tags.get("\xa9day") or [""])[0]
        info["composer"]     = (tags.get("\xa9wrt") or [""])[0]
        info["comment"]      = (tags.get("\xa9cmt") or [""])[0]
        info["lyrics"]       = (tags.get("\xa9lyr") or [""])[0]
        trkn = tags.get("trkn")
        if trkn:
            info["track_number"] = str(trkn[0][0])
            info["track_total"]  = str(trkn[0][1]) if trkn[0][1] else ""
        disk = tags.get("disk")
        if disk:
            info["disc_number"] = str(disk[0][0])
            info["disc_total"]  = str(disk[0][1]) if disk[0][1] else ""
    else:
        raise ValueError(f"Unsupported file format: {suffix!r} (supported: .mp3, .m4a, .aac)")

    return info


def clear_lyrics(path: Path) -> bool:
    """Remove embedded lyrics from a file. Returns True if lyrics were present."""
    suffix = path.suffix.lower()

    if suffix == ".mp3":
        _, tags = _open_mp3(path)
        had = bool(tags.getall("USLT"))
        if had:
            tags.delall("USLT")
            tags.save(path)
        return had
    elif suffix in (".m4a", ".aac"):
        audio = MP4(path)
        had = bool(audio.tags and audio.tags.get("\xa9lyr"))
        if had:
            del audio.tags["\xa9lyr"]
            audio.save()
        return had
    else:
        raise ValueError(f"Unsupported file format: {suffix!r} (supported: .mp3, .m4a, .aac)")


def write_lyrics(path: Path, lyrics: str, title: str = "", artist: str = "", album: str = "") -> None:
    suffix = path.suffix.lower()

    if suffix == ".mp3":
        _, tags = _open_mp3(path)
        tags.delall("USLT")
        tags.add(USLT(encoding=3, lang="eng", desc="", text=lyrics))
        if title:
            tags["TIT2"] = TIT2(encoding=3, text=title)
        if artist:
            tags["TPE1"] = TPE1(encoding=3, text=artist)
        if album:
            tags["TALB"] = TALB(encoding=3, text=album)
        tags.save(path)
    elif suffix in (".m4a", ".aac"):
        audio = MP4(path)
        if audio.tags is None:
            audio.add_tags()
        audio.tags["\xa9lyr"] = [lyrics]
        if title:
            audio.tags["\xa9nam"] = [title]
        if artist:
            audio.tags["\xa9ART"] = [artist]
        if album:
            audio.tags["\xa9alb"] = [album]
        audio.save()
    else:
        raise ValueError(f"Unsupported file format: {suffix!r} (supported: .mp3, .m4a, .aac)")
