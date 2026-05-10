"""Command-line interface for the iPod Classic music manager."""

import argparse
import sys
from pathlib import Path

from .detector import find_ipod, probe
from .manager import IpodManager


def _get_manager(args: argparse.Namespace) -> IpodManager:
    hint = getattr(args, "mount", "") or ""
    mount = find_ipod(hint) if not hint else probe(Path(hint))
    if mount is None:
        if hint:
            print(f"[error] No iPod found at {hint!r}", file=sys.stderr)
        else:
            print("[error] No iPod detected. Use --mount to specify the mount point.", file=sys.stderr)
        sys.exit(1)
    mode = "Rockbox" if mount.rockbox else "stock firmware"
    print(f"[info] iPod found: {mount.root}  ({mode})")
    if not mount.rockbox:
        print(
            "[warn] Stock firmware detected. The written iTunesDB does not include\n"
            "       the proprietary hash required by iPod Classic 6G/7G. If the iPod\n"
            "       refuses to load music, enable Rockbox or apply the hash with libgpod."
        )
    return IpodManager(mount)


# ------------------------------------------------------------------ commands

def cmd_list(args: argparse.Namespace) -> int:
    mgr = _get_manager(args)
    tracks = mgr.list_tracks()
    if not tracks:
        print("No tracks found on iPod.")
        return 0
    print(f"\n{'ID':>5}  {'Title':<35} {'Artist':<25} {'Album':<25} {'Duration':>8}")
    print("-" * 104)
    for t in tracks:
        dur = _fmt_duration(t.duration_ms)
        print(
            f"{t.track_id:>5}  {t.title[:35]:<35} {t.artist[:25]:<25}"
            f" {t.album[:25]:<25} {dur:>8}"
        )
    print(f"\n{len(tracks)} track(s) total.")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    mgr = _get_manager(args)
    paths = [Path(p) for p in args.files]
    missing = [p for p in paths if not p.exists()]
    if missing:
        for m in missing:
            print(f"[error] File not found: {m}", file=sys.stderr)
        return 1

    added = 0
    for src in paths:
        print(f"\nAdding: {src.name}")
        try:
            t = mgr.add_track(
                src,
                title=args.title,
                artist=args.artist,
                album=args.album,
                genre=args.genre,
                track_number=args.track_number,
                year=args.year,
            )
            print(f"  -> [{t.track_id}] {t.title or src.stem}")
            print(f"     Artist: {t.artist or '(unknown)'}")
            print(f"     Album : {t.album or '(unknown)'}")
            print(f"     Path  : {t.ipod_path}")
            added += 1
        except Exception as exc:
            print(f"  [error] {exc}", file=sys.stderr)

    print(f"\n{added}/{len(paths)} file(s) added.")
    if mgr.mount.rockbox:
        print("[info] Rockbox: run 'Initialize Now' in Settings > Database to refresh.")
    return 0 if added == len(paths) else 1


def cmd_remove(args: argparse.Namespace) -> int:
    mgr = _get_manager(args)
    removed = 0
    for tid in args.track_ids:
        ok = mgr.remove_track(tid)
        if ok:
            print(f"[ok] Removed track {tid}")
            removed += 1
        else:
            print(f"[warn] Track {tid} not found.", file=sys.stderr)
    if mgr.mount.rockbox and removed:
        print("[info] Rockbox: run 'Initialize Now' in Settings > Database to refresh.")
    return 0 if removed == len(args.track_ids) else 1


def cmd_sync_lyrics(args: argparse.Namespace) -> int:
    from lyrics_finder.sources import LyricsFinder

    mgr = _get_manager(args)
    finder = LyricsFinder(musixmatch_key=getattr(args, "musixmatch_key", ""))
    print("Syncing lyrics …")
    results = mgr.sync_lyrics(finder)
    found = sum(1 for v in results.values() if v not in ("not found",) and not v.startswith("error"))
    for title, source in results.items():
        status = f"[ok] via {source}" if source not in ("not found",) and not source.startswith("error") else f"[--] {source}"
        print(f"  {status:<30} {title}")
    print(f"\n{found}/{len(results)} track(s) updated with lyrics.")
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    mgr = _get_manager(args)
    kwargs = {}
    if args.title:
        kwargs["title"] = args.title
    if args.artist:
        kwargs["artist"] = args.artist
    if args.album:
        kwargs["album"] = args.album
    if args.genre:
        kwargs["genre"] = args.genre
    if args.year:
        kwargs["year"] = args.year
    if args.track_number:
        kwargs["track_number"] = args.track_number

    ok = mgr.update_metadata(args.track_id, **kwargs)
    if ok:
        print(f"[ok] Track {args.track_id} updated.")
        return 0
    print(f"[error] Track {args.track_id} not found.", file=sys.stderr)
    return 1


# ------------------------------------------------------------------ parser

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ipod-manager",
        description=(
            "iPod Classic music manager — list, add, remove tracks and sync lyrics.\n"
            "Works with Rockbox (all operations) and stock firmware iPods 1G–5G\n"
            "(read/write). Stock 6G/7G can be read; write requires a hash update\n"
            "via libgpod if using the original Apple firmware."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mount",
        metavar="PATH",
        default="",
        help="iPod mount point (auto-detected if omitted)",
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    # list
    p_list = sub.add_parser("list", help="List all tracks on the iPod")
    p_list.set_defaults(func=cmd_list)

    # add
    p_add = sub.add_parser("add", help="Add one or more audio files to the iPod")
    p_add.add_argument("files", nargs="+", metavar="FILE", help="MP3 / M4A / AAC files to add")
    p_add.add_argument("--title", default="", help="Override title tag")
    p_add.add_argument("--artist", default="", help="Override artist tag")
    p_add.add_argument("--album", default="", help="Override album tag")
    p_add.add_argument("--genre", default="", help="Override genre tag")
    p_add.add_argument("--track-number", type=int, default=0, metavar="N", help="Track number")
    p_add.add_argument("--year", type=int, default=0, help="Release year")
    p_add.set_defaults(func=cmd_add)

    # remove
    p_rm = sub.add_parser("remove", help="Remove tracks from the iPod by ID")
    p_rm.add_argument("track_ids", nargs="+", type=int, metavar="ID", help="Track IDs to remove")
    p_rm.set_defaults(func=cmd_remove)

    # sync-lyrics
    p_sl = sub.add_parser("sync-lyrics", help="Find and embed lyrics for all iPod tracks")
    p_sl.add_argument(
        "--musixmatch-key",
        default="",
        metavar="KEY",
        dest="musixmatch_key",
        help="Optional Musixmatch API key for extra coverage",
    )
    p_sl.set_defaults(func=cmd_sync_lyrics)

    # update
    p_up = sub.add_parser("update", help="Edit metadata for a single track")
    p_up.add_argument("track_id", type=int, metavar="ID", help="Track ID to update")
    p_up.add_argument("--title", default="")
    p_up.add_argument("--artist", default="")
    p_up.add_argument("--album", default="")
    p_up.add_argument("--genre", default="")
    p_up.add_argument("--year", type=int, default=0)
    p_up.add_argument("--track-number", type=int, default=0, metavar="N", dest="track_number")
    p_up.set_defaults(func=cmd_update)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


# ------------------------------------------------------------------ util

def _fmt_duration(ms: int) -> str:
    if not ms:
        return "-"
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"
