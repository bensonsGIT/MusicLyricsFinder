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


def write_metadata(
    path: Path,
    *,
    title=_SENTINEL,
    artist=_SENTINEL,
    album=_SENTINEL,
    album_artist=_SENTINEL,
    genre=_SENTINEL,
    year=_SENTINEL,
    track_number=_SENTINEL,
    track_total=_SENTINEL,
    disc_number=_SENTINEL,
    disc_total=_SENTINEL,
    composer=_SENTINEL,
    comment=_SENTINEL,
    lyrics=_SENTINEL,
) -> dict:
    """
    Write whichever tag fields are provided to *path*.

    Only supplied keyword arguments are written; omitted ones are left
    unchanged. Returns the full metadata dict after the update.
    """
    def _given(v):
        return v is not _SENTINEL

    suffix = path.suffix.lower()

    if suffix == ".mp3":
        _, tags = _open_mp3(path)
        if _given(title):
            tags["TIT2"] = TIT2(encoding=3, text=str(title))
        if _given(artist):
            tags["TPE1"] = TPE1(encoding=3, text=str(artist))
        if _given(album):
            tags["TALB"] = TALB(encoding=3, text=str(album))
        if _given(album_artist):
            from mutagen.id3 import TPE2
            tags["TPE2"] = TPE2(encoding=3, text=str(album_artist))
        if _given(genre):
            tags["TCON"] = TCON(encoding=3, text=str(genre))
        if _given(year):
            tags["TDRC"] = TDRC(encoding=3, text=str(year))
        if _given(composer):
            from mutagen.id3 import TCOM
            tags["TCOM"] = TCOM(encoding=3, text=str(composer))
        if _given(comment):
            from mutagen.id3 import COMM
            tags.delall("COMM")
            if comment:
                tags.add(COMM(encoding=3, lang="eng", desc="", text=str(comment)))
        if _given(track_number) or _given(track_total):
            cur = str(tags.get("TRCK", ""))
            cur_num, cur_tot = (cur.split("/", 1) + [""])[:2]
            num = str(track_number) if _given(track_number) else cur_num
            tot = str(track_total)  if _given(track_total)  else cur_tot
            tags["TRCK"] = TRCK(encoding=3, text=f"{num}/{tot}" if tot else num)
        if _given(disc_number) or _given(disc_total):
            cur = str(tags.get("TPOS", ""))
            cur_num, cur_tot = (cur.split("/", 1) + [""])[:2]
            num = str(disc_number) if _given(disc_number) else cur_num
            tot = str(disc_total)  if _given(disc_total)  else cur_tot
            tags["TPOS"] = TPOS(encoding=3, text=f"{num}/{tot}" if tot else num)
        if _given(lyrics):
            tags.delall("USLT")
            if lyrics:
                tags.add(USLT(encoding=3, lang="eng", desc="", text=str(lyrics)))
        tags.save(path)

    elif suffix in (".m4a", ".aac"):
        audio = MP4(path)
        if audio.tags is None:
            audio.add_tags()
        t = audio.tags
        if _given(title):        t["\xa9nam"] = [str(title)]
        if _given(artist):       t["\xa9ART"] = [str(artist)]
        if _given(album):        t["\xa9alb"] = [str(album)]
        if _given(album_artist): t["aART"]    = [str(album_artist)]
        if _given(genre):        t["\xa9gen"] = [str(genre)]
        if _given(year):         t["\xa9day"] = [str(year)]
        if _given(composer):     t["\xa9wrt"] = [str(composer)]
        if _given(comment):      t["\xa9cmt"] = [str(comment)]
        if _given(lyrics):
            if lyrics:
                t["\xa9lyr"] = [str(lyrics)]
            elif "\xa9lyr" in t:
                del t["\xa9lyr"]
        if _given(track_number) or _given(track_total):
            cur = t.get("trkn", [(0, 0)])[0]
            num = int(track_number) if _given(track_number) and str(track_number).isdigit() else cur[0]
            tot = int(track_total)  if _given(track_total)  and str(track_total).isdigit()  else cur[1]
            t["trkn"] = [(num, tot)]
        if _given(disc_number) or _given(disc_total):
            cur = t.get("disk", [(0, 0)])[0]
            num = int(disc_number) if _given(disc_number) and str(disc_number).isdigit() else cur[0]
            tot = int(disc_total)  if _given(disc_total)  and str(disc_total).isdigit()  else cur[1]
            t["disk"] = [(num, tot)]
        audio.save()

    else:
        raise ValueError(f"Unsupported file format: {suffix!r} (supported: .mp3, .m4a, .aac)")

    return read_metadata(path)


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
