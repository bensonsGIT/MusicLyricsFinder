#!/usr/bin/env python3
"""Diagnostic script — run with: python debug_ipod.py /Volumes/IPOD_NAME"""

import struct
import sys
from pathlib import Path


def peek(data: bytes, pos: int, n: int = 4) -> str:
    chunk = data[pos : pos + n]
    try:
        text = chunk.decode("ascii")
        printable = all(32 <= b < 127 for b in chunk)
    except Exception:
        printable = False
    hex_str = chunk.hex()
    return f"{hex_str}  ({text!r})" if printable else hex_str


def u32le(data, pos):
    return struct.unpack_from("<I", data, pos)[0]


def u32be(data, pos):
    return struct.unpack_from(">I", data, pos)[0]


def main():
    if len(sys.argv) < 2:
        print("Usage: python debug_ipod.py <ipod-mount-point>")
        sys.exit(1)

    root = Path(sys.argv[1])
    db_path = root / "iPod_Control" / "iTunes" / "iTunesDB"

    if not db_path.exists():
        print(f"[error] No iTunesDB at {db_path}")
        sys.exit(1)

    data = db_path.read_bytes()
    print(f"iTunesDB size: {len(data):,} bytes")
    print(f"First 4 bytes: {peek(data, 0)}")
    print()

    # Check endianness
    magic_le = data[0:4]
    if magic_le == b"mhbd":
        endian = "little"
        u32 = u32le
        print("[ok] Magic: 'mhbd' (little-endian — correct for iPod 4G+)")
    elif magic_le == b"dbhm":
        endian = "big"
        u32 = u32be
        print("[ok] Magic: 'dbhm' (big-endian — iPod 1G-3G)")
    else:
        print(f"[error] Unrecognised magic: {magic_le!r}")
        sys.exit(1)

    hdr_size = u32(data, 4)
    total_size = u32(data, 8)
    version = struct.unpack_from("<H", data, 20)[0] if endian == "little" else struct.unpack_from(">H", data, 20)[0]
    num_tracks_field = u32(data, 24)
    print(f"mhbd header size : 0x{hdr_size:X} ({hdr_size})")
    print(f"mhbd total size  : 0x{total_size:X} ({total_size:,})")
    print(f"DB version       : 0x{version:X} ({version})")
    print(f"Track count hint : {num_tracks_field}")
    print()

    # Walk immediate children of mhbd
    print(f"Records starting at offset 0x{hdr_size:X}:")
    pos = hdr_size
    mhlt_pos = None
    for i in range(20):
        if pos + 12 > len(data):
            break
        magic = data[pos : pos + 4]
        try:
            magic_str = magic.decode("ascii")
        except Exception:
            magic_str = magic.hex()
        rec_hdr = u32(data, pos + 4)
        field8  = u32(data, pos + 8)
        print(f"  [{i}] offset=0x{pos:X}  magic={magic_str!r}  hdr_size={rec_hdr}  field@+8={field8}")

        if magic == b"mhlt" or magic == b"tlhm":
            mhlt_pos = pos
            print(f"       ^ mhlt found! num_tracks={field8}")
            break

        # advance: for most records field@+8 is NOT the total size
        # so just advance by hdr_size and hope for the best
        advance = field8 if field8 > rec_hdr else rec_hdr
        if advance == 0:
            print("  [stop] zero advance")
            break
        pos += advance

    if mhlt_pos is None:
        print("[warn] mhlt not found in first 20 records")
        return

    # Look at first few mhit records
    mhlt_hdr = u32(data, mhlt_pos + 4)
    num_tracks = u32(data, mhlt_pos + 8)
    print()
    print(f"mhlt header size: {mhlt_hdr}, num_tracks: {num_tracks}")

    pos = mhlt_pos + mhlt_hdr
    for i in range(min(3, num_tracks)):
        if pos + 4 > len(data):
            break
        magic = data[pos : pos + 4]
        try:
            magic_str = magic.decode("ascii")
        except Exception:
            magic_str = magic.hex()
        rec_hdr2 = u32(data, pos + 4)
        rec_total = u32(data, pos + 8)
        num_mhod = u32(data, pos + 12)
        track_id = u32(data, pos + 16)
        print(f"  track[{i}] offset=0x{pos:X}  magic={magic_str!r}  hdr={rec_hdr2}  total={rec_total}  mhods={num_mhod}  id={track_id}")
        if rec_total == 0:
            break
        pos += rec_total


if __name__ == "__main__":
    main()
