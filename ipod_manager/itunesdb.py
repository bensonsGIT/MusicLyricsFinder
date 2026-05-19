"""
iTunesDB binary parser and writer for iPod Classic (little-endian format).

Supports reading any generation and writing databases compatible with
iPods 1G–5G (no hash required) and all Rockbox-enabled devices.
iPod Classic 6G/7G stock firmware requires a hash that must be applied
separately (e.g. via libgpod's itdb_hash utility).

Format reference: https://www.ipodlinux.org/ITunesDB/
"""

import os
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Record magic bytes
MHBD = b"mhbd"
MHLT = b"mhlt"
MHIT = b"mhit"
MHOD = b"mhod"
MHLP = b"mhlp"
MHYP = b"mhyp"
MHIP = b"mhip"

# mhod string type codes
MHOD_TITLE = 1
MHOD_FILENAME = 2
MHOD_ALBUM = 3
MHOD_ARTIST = 4
MHOD_GENRE = 5
MHOD_FILETYPE = 6
MHOD_COMMENT = 8
MHOD_COMPOSER = 12
MHOD_GROUPING = 13

# Mac/HFS epoch is 1904-01-01; Unix epoch is 1970-01-01
_MAC_OFFSET = 2082844800


def _to_mac(ts: Optional[float] = None) -> int:
    return int(ts if ts is not None else time.time()) + _MAC_OFFSET


def _to_unix(mac: int) -> int:
    return max(0, mac - _MAC_OFFSET)


@dataclass
class Track:
    track_id: int = 0
    dbid: int = 0
    title: str = ""
    artist: str = ""
    album: str = ""
    genre: str = ""
    composer: str = ""
    comment: str = ""
    # Colon-separated iPod path, e.g. :iPod_Control:Music:F01:0001.mp3
    ipod_path: str = ""
    duration_ms: int = 0
    file_size: int = 0
    bitrate: int = 0
    sample_rate: int = 0
    track_number: int = 0
    track_count: int = 0
    disc_number: int = 0
    disc_count: int = 0
    year: int = 0
    bpm: int = 0
    rating: int = 0        # 0/20/40/60/80/100
    compilation: bool = False
    time_added: int = 0    # unix timestamp
    play_count: int = 0
    last_played: int = 0   # unix timestamp
    file_type: str = ""    # "MP3 ", "M4A ", …

    def local_path(self, mount: Path) -> Path:
        """Resolve the iPod colon-path to an absolute filesystem path."""
        parts = [p for p in self.ipod_path.split(":") if p]
        return mount.joinpath(*parts)

    def colon_path(self) -> str:
        return self.ipod_path


class iTunesDB:
    """Read and write an iTunes database."""

    # Header sizes used when building a fresh database
    _MHBD_HDR = 0x68
    _MHLT_HDR = 0x5C
    _MHIT_HDR = 0x148   # iPod Classic 6G size (backward-compatible)
    _MHOD_HDR = 0x18
    _MHLP_HDR = 0x5C
    _MHYP_HDR = 0xDC
    _MHIP_HDR = 0x4C

    def __init__(self):
        self.tracks: list[Track] = []
        self._db_id: int = int.from_bytes(os.urandom(8), "little")
        self._version: int = 0x1A  # iPod Classic 6G

    # ------------------------------------------------------------------ read

    @classmethod
    def read(cls, path: Path) -> "iTunesDB":
        db = cls()
        db._parse(path.read_bytes())
        return db

    def _parse(self, data: bytes) -> None:
        if data[:4] != MHBD:
            raise ValueError("Not a valid iTunesDB (missing mhbd magic)")
        hdr_size = struct.unpack_from("<I", data, 4)[0]
        pos = hdr_size
        while pos + 8 < len(data):
            magic = data[pos : pos + 4]
            rec_hdr = struct.unpack_from("<I", data, pos + 4)[0]
            rec_total = struct.unpack_from("<I", data, pos + 8)[0]
            if magic == MHLT:
                self._parse_mhlt(data, pos)
                return
            # iTunes-synced iPods wrap mhlt inside mhsd section records.
            # Walk inside each mhsd to find mhlt (handles all layout variants).
            if magic == b"mhsd" and rec_hdr > 0 and rec_total > rec_hdr:
                mhsd_end = pos + rec_total
                inner = pos + rec_hdr
                while inner + 8 < mhsd_end and inner + 8 < len(data):
                    inner_magic = data[inner : inner + 4]
                    inner_hdr = struct.unpack_from("<I", data, inner + 4)[0]
                    inner_total = struct.unpack_from("<I", data, inner + 8)[0]
                    if inner_magic == MHLT:
                        self._parse_mhlt(data, inner)
                        return
                    advance = inner_total or inner_hdr
                    if advance == 0:
                        break
                    inner += advance
            advance = rec_total or rec_hdr
            if advance == 0:
                break
            pos += advance

    def _parse_mhlt(self, data: bytes, pos: int) -> None:
        stored_hdr = struct.unpack_from("<I", data, pos + 4)[0]
        num_tracks  = struct.unpack_from("<I", data, pos + 8)[0]

        # Locate the first mhit record. Standard layout puts it at
        # pos + header_size, but some DB versions report a different value
        # (e.g. total record size instead of header size). Try a set of
        # known header sizes before falling back to a forward scan.
        track_start: int | None = None
        for candidate in (stored_hdr, 0x5C, 0x68, 0x9C, 0x148):
            off = pos + candidate
            if off + 4 <= len(data) and data[off : off + 4] == MHIT:
                track_start = off
                break

        if track_start is None:
            # Last resort: scan byte-by-byte up to 4 KB ahead
            for off in range(pos + 4, min(pos + 4096, len(data) - 3)):
                if data[off : off + 4] == MHIT:
                    track_start = off
                    break

        if track_start is None:
            return

        limit = num_tracks if 0 < num_tracks < 1_000_000 else 1_000_000
        cur = track_start
        for _ in range(limit):
            if cur + 4 > len(data) or data[cur : cur + 4] != MHIT:
                break
            track, cur = self._parse_mhit(data, cur)
            self.tracks.append(track)

    def _parse_mhit(self, data: bytes, pos: int) -> tuple["Track", int]:
        hdr_size = struct.unpack_from("<I", data, pos + 4)[0]
        total_size = struct.unpack_from("<I", data, pos + 8)[0]
        num_mhod = struct.unpack_from("<I", data, pos + 12)[0]

        t = Track()
        t.track_id = struct.unpack_from("<I", data, pos + 16)[0]

        def _u8(off: int) -> int:
            return struct.unpack_from("<B", data, pos + off)[0] if hdr_size > off else 0

        def _u16(off: int) -> int:
            return struct.unpack_from("<H", data, pos + off)[0] if hdr_size > off + 1 else 0

        def _u32(off: int) -> int:
            return struct.unpack_from("<I", data, pos + off)[0] if hdr_size > off + 3 else 0

        def _u64(off: int) -> int:
            return struct.unpack_from("<Q", data, pos + off)[0] if hdr_size > off + 7 else 0

        ft_raw = data[pos + 24 : pos + 28] if hdr_size > 27 else b"    "
        t.file_type = ft_raw.decode("ascii", errors="replace").strip()
        t.rating = _u8(28)
        t.compilation = bool(_u8(29))
        t.bitrate = _u16(30)
        t.sample_rate = _u32(32)
        t.duration_ms = _u32(36)
        t.file_size = _u32(40)
        t.time_added = _to_unix(_u32(44))
        t.play_count = _u32(48)
        t.last_played = _to_unix(_u32(56))
        t.disc_number = _u32(60)
        t.disc_count = _u32(64)
        t.track_number = _u32(68)
        t.track_count = _u32(72)
        t.year = _u32(76)
        t.bpm = _u32(80)
        t.dbid = _u64(88)

        mhod_pos = pos + hdr_size
        for _ in range(num_mhod):
            if mhod_pos + 12 > pos + total_size:
                break
            if data[mhod_pos : mhod_pos + 4] != MHOD:
                break
            mhod_total = struct.unpack_from("<I", data, mhod_pos + 8)[0]
            mhod_type = struct.unpack_from("<I", data, mhod_pos + 12)[0]
            if mhod_type in (
                MHOD_TITLE, MHOD_FILENAME, MHOD_ALBUM, MHOD_ARTIST,
                MHOD_GENRE, MHOD_COMPOSER, MHOD_COMMENT,
            ):
                value = self._parse_mhod_string(data, mhod_pos)
                if mhod_type == MHOD_TITLE:
                    t.title = value
                elif mhod_type == MHOD_FILENAME:
                    t.ipod_path = value
                elif mhod_type == MHOD_ALBUM:
                    t.album = value
                elif mhod_type == MHOD_ARTIST:
                    t.artist = value
                elif mhod_type == MHOD_GENRE:
                    t.genre = value
                elif mhod_type == MHOD_COMPOSER:
                    t.composer = value
                elif mhod_type == MHOD_COMMENT:
                    t.comment = value
            mhod_pos += mhod_total

        return t, pos + total_size

    def _parse_mhod_string(self, data: bytes, pos: int) -> str:
        # String section starts at header end (offset 0x18 = 24)
        base = pos + self._MHOD_HDR
        if base + 16 > len(data):
            return ""
        encoding = struct.unpack_from("<I", data, base)[0]
        str_len = struct.unpack_from("<I", data, base + 4)[0]
        str_start = base + 16
        if str_start + str_len > len(data):
            return ""
        raw = data[str_start : str_start + str_len]
        return raw.decode("utf-8" if encoding == 1 else "utf-16-le", errors="replace")

    # ----------------------------------------------------------------- write

    def write(self, path: Path) -> None:
        self._normalise_ids()
        path.write_bytes(self._build())

    def _normalise_ids(self) -> None:
        used_ids = {t.track_id for t in self.tracks if t.track_id}
        used_dbids = {t.dbid for t in self.tracks if t.dbid}
        next_id = max(used_ids, default=0) + 1
        next_dbid = max(used_dbids, default=0) + 1
        for t in self.tracks:
            if not t.track_id:
                t.track_id = next_id
                next_id += 1
            if not t.dbid:
                t.dbid = next_dbid
                next_dbid += 1

    def _build(self) -> bytes:
        mhlt = self._build_mhlt()
        mhlp = self._build_mhlp()
        total = self._MHBD_HDR + len(mhlt) + len(mhlp)

        hdr = bytearray(self._MHBD_HDR)
        hdr[0:4] = MHBD
        struct.pack_into("<I", hdr, 4, self._MHBD_HDR)
        struct.pack_into("<I", hdr, 8, total)
        struct.pack_into("<I", hdr, 12, 1)
        struct.pack_into("<I", hdr, 16, 2)
        struct.pack_into("<H", hdr, 20, self._version)
        struct.pack_into("<I", hdr, 24, len(self.tracks))
        struct.pack_into("<I", hdr, 28, 1)
        struct.pack_into("<Q", hdr, 32, self._db_id)
        struct.pack_into("<H", hdr, 40, 2)
        return bytes(hdr) + mhlt + mhlp

    def _build_mhlt(self) -> bytes:
        tracks_data = b"".join(self._build_mhit(t) for t in self.tracks)
        hdr = bytearray(self._MHLT_HDR)
        hdr[0:4] = MHLT
        struct.pack_into("<I", hdr, 4, self._MHLT_HDR)
        struct.pack_into("<I", hdr, 8, len(self.tracks))
        return bytes(hdr) + tracks_data

    def _build_mhit(self, t: Track) -> bytes:
        mhods = []
        for mtype, value in [
            (MHOD_TITLE, t.title),
            (MHOD_FILENAME, t.ipod_path),
            (MHOD_ALBUM, t.album),
            (MHOD_ARTIST, t.artist),
            (MHOD_GENRE, t.genre),
            (MHOD_COMPOSER, t.composer),
            (MHOD_COMMENT, t.comment),
        ]:
            if value:
                mhods.append(self._build_mhod_string(mtype, value))

        mhod_data = b"".join(mhods)
        total = self._MHIT_HDR + len(mhod_data)

        hdr = bytearray(self._MHIT_HDR)
        hdr[0:4] = MHIT
        struct.pack_into("<I", hdr, 4, self._MHIT_HDR)
        struct.pack_into("<I", hdr, 8, total)
        struct.pack_into("<I", hdr, 12, len(mhods))
        struct.pack_into("<I", hdr, 16, t.track_id)
        struct.pack_into("<I", hdr, 20, 1)  # visible

        ft = (t.file_type + "    ")[:4].encode("ascii", errors="replace")
        hdr[24:28] = ft
        struct.pack_into("<B", hdr, 28, t.rating)
        struct.pack_into("<B", hdr, 29, 1 if t.compilation else 0)
        struct.pack_into("<H", hdr, 30, t.bitrate)
        struct.pack_into("<I", hdr, 32, t.sample_rate)
        struct.pack_into("<I", hdr, 36, t.duration_ms)
        struct.pack_into("<I", hdr, 40, t.file_size)
        struct.pack_into("<I", hdr, 44, _to_mac(t.time_added) if t.time_added else _to_mac())
        struct.pack_into("<I", hdr, 48, t.play_count)
        struct.pack_into("<I", hdr, 52, t.play_count)
        struct.pack_into("<I", hdr, 56, _to_mac(t.last_played) if t.last_played else 0)
        struct.pack_into("<I", hdr, 60, t.disc_number)
        struct.pack_into("<I", hdr, 64, t.disc_count)
        struct.pack_into("<I", hdr, 68, t.track_number)
        struct.pack_into("<I", hdr, 72, t.track_count)
        struct.pack_into("<I", hdr, 76, t.year)
        struct.pack_into("<I", hdr, 80, t.bpm)
        struct.pack_into("<Q", hdr, 88, t.dbid)

        return bytes(hdr) + mhod_data

    def _build_mhod_string(self, mtype: int, value: str) -> bytes:
        encoded = value.encode("utf-16-le")
        # Data section: encoding(4) + length(4) + pad(4) + pad(4) + string
        section = bytearray(16 + len(encoded))
        struct.pack_into("<I", section, 0, 0)             # UTF-16 LE
        struct.pack_into("<I", section, 4, len(encoded))
        section[16:] = encoded

        total = self._MHOD_HDR + len(section)
        hdr = bytearray(self._MHOD_HDR)
        hdr[0:4] = MHOD
        struct.pack_into("<I", hdr, 4, self._MHOD_HDR)
        struct.pack_into("<I", hdr, 8, total)
        struct.pack_into("<I", hdr, 12, mtype)
        return bytes(hdr) + bytes(section)

    def _build_mhlp(self) -> bytes:
        master = self._build_master_playlist()
        hdr = bytearray(self._MHLP_HDR)
        hdr[0:4] = MHLP
        struct.pack_into("<I", hdr, 4, self._MHLP_HDR)
        struct.pack_into("<I", hdr, 8, 1)
        return bytes(hdr) + master

    def _build_master_playlist(self) -> bytes:
        name_mhod = self._build_mhod_string(MHOD_TITLE, "Library")
        mhips = b"".join(self._build_mhip(t) for t in self.tracks)
        total = self._MHYP_HDR + len(name_mhod) + len(mhips)

        hdr = bytearray(self._MHYP_HDR)
        hdr[0:4] = MHYP
        struct.pack_into("<I", hdr, 4, self._MHYP_HDR)
        struct.pack_into("<I", hdr, 8, total)
        struct.pack_into("<I", hdr, 12, 1)              # 1 mhod (name)
        struct.pack_into("<I", hdr, 16, len(self.tracks))
        struct.pack_into("<I", hdr, 20, 1)              # is_master
        return bytes(hdr) + name_mhod + mhips

    def _build_mhip(self, t: Track) -> bytes:
        hdr = bytearray(self._MHIP_HDR)
        hdr[0:4] = MHIP
        struct.pack_into("<I", hdr, 4, self._MHIP_HDR)
        struct.pack_into("<I", hdr, 8, self._MHIP_HDR)
        struct.pack_into("<I", hdr, 24, t.track_id)
        struct.pack_into("<I", hdr, 28, _to_mac())
        return bytes(hdr)
