"""Read and write the Grouping tag that carries a song's vibe."""

from pathlib import Path

from mutagen.id3 import GRP1, ID3, ID3NoHeaderError
from mutagen.mp4 import MP4

PREFIX = "vibe:"
SUPPORTED = (".mp3", ".m4a", ".aac")


def label(vibe: int) -> str:
    return f"{PREFIX}{vibe:02d}"


def read_grouping(path: Path) -> str:
    if path.suffix.lower() == ".mp3":
        try:
            return str(ID3(path).get("GRP1", ""))
        except ID3NoHeaderError:
            return ""
    return ((MP4(path).tags or {}).get("\xa9grp") or [""])[0]


def write_grouping(path: Path, value: str) -> None:
    """Set Grouping, or remove it when value is empty.

    MP3 uses GRP1, which iTunes 12.5+ and Apple Music read as Grouping
    (the older TIT1 frame now means "Work").
    """
    if path.suffix.lower() == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall("GRP1")
        if value:
            tags.add(GRP1(encoding=3, text=[value]))
        tags.save(path)
        return
    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()
    if value:
        audio.tags["\xa9grp"] = [value]
    else:
        audio.tags.pop("\xa9grp", None)
    audio.save()


def is_vibe(grouping: str) -> bool:
    return grouping.startswith(PREFIX)
