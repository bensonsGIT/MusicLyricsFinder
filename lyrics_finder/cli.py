import argparse
import sys
from pathlib import Path

from .artwork import ArtworkError, ArtworkFinder
from .itunes_sync import refresh_paths
from .metadata import clear_artwork, clear_lyrics, read_metadata, write_artwork, write_lyrics
from .sources import LyricsFinder

SUPPORTED_EXTENSIONS = {".mp3", ".m4a", ".aac"}


def _collect_files(paths: list[str]) -> list[Path]:
    result = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            for ext in SUPPORTED_EXTENSIONS:
                result.extend(sorted(path.rglob(f"*{ext}")))
        elif path.is_file():
            if path.suffix.lower() in SUPPORTED_EXTENSIONS:
                result.append(path)
            else:
                print(f"[skip] {path.name}: unsupported format", file=sys.stderr)
        else:
            print(f"[skip] {p}: not found", file=sys.stderr)
    return result


def _process_file(
    finder: LyricsFinder,
    path: Path,
    title_override: str = "",
    artist_override: str = "",
    dry_run: bool = False,
    interactive: bool = False,
    overwrite: bool = False,
) -> bool:
    print(f"\n{'='*60}")
    print(f"File   : {path}")

    try:
        meta = read_metadata(path)
    except Exception as e:
        print(f"[error] Cannot read metadata: {e}")
        return False

    title = title_override or meta["title"]
    artist = artist_override or meta["artist"]

    if not title:
        print("[skip] No title found in metadata. Use --title to specify one.")
        return False

    print(f"Title  : {title}")
    print(f"Artist : {artist or '(unknown)'}")

    if meta["lyrics"] and not overwrite:
        print("[info] Lyrics already present in file.")
        if not interactive:
            print("[skip] Use --overwrite to replace existing lyrics.")
            return False
        answer = input("Lyrics already exist. Overwrite? [y/N] ").strip().lower()
        if answer != "y":
            print("[skip] Keeping existing lyrics.")
            return False

    print(f"[info] Searching for: {title!r} / {artist!r}")
    lyrics, source = finder.find(title, artist, meta.get("album", ""))

    if not lyrics:
        print("[info] Lyrics not found in any source.")
        return False

    print(f"[info] Found via {source}")
    preview = lyrics[:200].replace("\n", " ")
    print(f"[info] Preview: {preview}...")

    if dry_run:
        print("[dry-run] Skipping write.")
        return True

    try:
        write_lyrics(path, lyrics)
    except Exception as e:
        print(f"[error] Failed to write lyrics: {e}")
        return False

    print(f"[ok] Lyrics written to {path.name}")
    return True


def _process_artwork(
    finder: ArtworkFinder,
    path: Path,
    meta: dict | None = None,
    size: int = ArtworkFinder.DEFAULT_SIZE,
    overwrite: bool = False,
    dry_run: bool = False,
) -> bool:
    if meta is None:
        try:
            meta = read_metadata(path)
        except Exception as e:
            print(f"[art] Cannot read metadata: {e}")
            return False

    if meta.get("has_artwork") and not overwrite:
        print(f"[art] {path.name}: artwork already present (use --overwrite to replace).")
        return False

    title = meta.get("title", "")
    artist = meta.get("artist", "")
    album = meta.get("album", "")
    if not title and not album:
        print(f"[art] {path.name}: no title or album tag to search artwork with.")
        return False

    print(f"[art] Searching artwork for: {(album or title)!r} / {artist or '(unknown)'!r}")
    try:
        result = finder.find(title, artist, album, size=size)
    except ArtworkError as e:
        print(f"[art] Search failed: {e}")
        return False

    if not result:
        print("[art] No artwork found.")
        return False

    print(f"[art] Match: {result.album or result.track or '(untitled)'} — {result.artist or 'unknown'}")

    if dry_run:
        print(f"[dry-run] Would embed: {result.art_url}")
        return True

    try:
        image, mime = finder.download(result.art_url)
        write_artwork(path, image, mime)
    except Exception as e:
        print(f"[art] Failed to embed artwork: {e}")
        return False

    print(f"[ok] Artwork embedded in {path.name} ({len(image)} bytes)")
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lyrics-finder",
        description=(
            "Search for lyrics and embed them into MP3/M4A files. "
            "Uses lrclib.net and lyrics.ovh — no API key required."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="+",
        metavar="FILE_OR_DIR",
        help="Audio files or directories to process",
    )
    parser.add_argument(
        "--title",
        default="",
        metavar="TITLE",
        help="Override track title for search (single file)",
    )
    parser.add_argument(
        "--artist",
        default="",
        metavar="ARTIST",
        help="Override artist name for search",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite lyrics even if the file already has them",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Search and preview lyrics without writing to files",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt before overwriting existing lyrics",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Remove embedded lyrics from the given files instead of searching",
    )
    parser.add_argument(
        "--artwork",
        action="store_true",
        help="Also search for and embed album art (iTunes) alongside lyrics",
    )
    parser.add_argument(
        "--artwork-only",
        action="store_true",
        help="Embed album art only — skip lyrics search",
    )
    parser.add_argument(
        "--artwork-size",
        type=int,
        default=ArtworkFinder.DEFAULT_SIZE,
        metavar="PX",
        help=f"Album-art resolution in pixels (default: {ArtworkFinder.DEFAULT_SIZE})",
    )
    parser.add_argument(
        "--clear-artwork",
        action="store_true",
        help="Remove embedded album art from the given files instead of searching",
    )
    parser.add_argument(
        "--refresh-itunes",
        action="store_true",
        help="After writing lyrics/art, tell Apple Music to refresh those tracks (macOS only)",
    )
    parser.add_argument(
        "--musixmatch-key",
        default="",
        metavar="KEY",
        help="Optional Musixmatch API key for additional coverage",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    files = _collect_files(args.paths)
    if not files:
        print("No supported audio files found.", file=sys.stderr)
        return 1

    print(f"Found {len(files)} file(s) to process.")

    if args.clear_artwork:
        cleared = 0
        skipped = 0
        for path in files:
            try:
                if args.dry_run:
                    had = bool(read_metadata(path).get("has_artwork"))
                    if had:
                        print(f"[dry-run] Would clear artwork: {path}")
                else:
                    had = clear_artwork(path)
                if had:
                    if not args.dry_run:
                        print(f"[ok] Cleared artwork: {path}")
                    cleared += 1
                else:
                    skipped += 1
            except Exception as e:
                print(f"[error] {path}: {e}")
                skipped += 1
        print(f"\nDone. {cleared} cleared, {skipped} had no artwork or failed.")
        return 0

    if args.clear:
        cleared = 0
        skipped = 0
        for path in files:
            try:
                if args.dry_run:
                    meta = read_metadata(path)
                    had = bool(meta["lyrics"])
                    if had:
                        print(f"[dry-run] Would clear lyrics: {path}")
                else:
                    had = clear_lyrics(path)
                if had:
                    if not args.dry_run:
                        print(f"[ok] Cleared lyrics: {path}")
                    cleared += 1
                else:
                    skipped += 1
            except Exception as e:
                print(f"[error] {path}: {e}")
                skipped += 1
        print(f"\nDone. {cleared} cleared, {skipped} had no lyrics or failed.")
        return 0

    do_lyrics = not args.artwork_only
    do_artwork = args.artwork or args.artwork_only

    finder = LyricsFinder(musixmatch_key=args.musixmatch_key) if do_lyrics else None
    art_finder = ArtworkFinder() if do_artwork else None

    ok = 0
    skipped = 0

    for path in files:
        meta = None
        try:
            meta = read_metadata(path)
        except Exception:
            pass

        file_ok = False

        if do_lyrics:
            has_lyrics = bool(meta and meta.get("lyrics"))
            if has_lyrics and not args.overwrite and not args.interactive:
                print(f"\n{'='*60}")
                print(f"File   : {path}")
                print("[skip] Lyrics already present. Use --overwrite to replace.")
            elif _process_file(
                finder=finder,
                path=path,
                title_override=args.title,
                artist_override=args.artist,
                dry_run=args.dry_run,
                interactive=args.interactive,
                overwrite=args.overwrite,
            ):
                file_ok = True

        if do_artwork:
            art_meta = dict(meta) if meta else None
            if art_meta is not None:
                if args.title:
                    art_meta["title"] = args.title
                if args.artist:
                    art_meta["artist"] = args.artist
            if _process_artwork(
                finder=art_finder,
                path=path,
                meta=art_meta,
                size=args.artwork_size,
                overwrite=args.overwrite,
                dry_run=args.dry_run,
            ):
                file_ok = True

        if file_ok:
            ok += 1
        else:
            skipped += 1

    print(f"\n{'='*60}")
    print(f"Done. {ok} updated, {skipped} skipped.")

    if args.refresh_itunes and ok > 0:
        print("\n[info] Refreshing processed tracks in Apple Music…")
        result = refresh_paths([p for p in files])
        if not result["available"]:
            print("[info] Apple Music refresh is only available on macOS — skipping.")
        else:
            print(f"[ok] Apple Music: {result['refreshed']} refreshed, "
                  f"{result['not_found']} not in library.")
            for err in result.get("errors") or []:
                print(f"[warn] {err}")

    return 0
