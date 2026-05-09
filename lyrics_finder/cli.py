import argparse
import os
import sys
from pathlib import Path

from .metadata import read_metadata, write_lyrics
from .musixmatch import MusixmatchClient, MusixmatchError

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
    client: MusixmatchClient,
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

    print(f"[info] Searching Musixmatch for: {title!r} / {artist!r}")
    try:
        results = client.search_track(title, artist)
    except MusixmatchError as e:
        print(f"[error] {e}")
        return False

    if not results:
        print("[info] No results found.")
        return False

    track = results[0]["track"]
    track_id = track["track_id"]
    matched_title = track["track_name"]
    matched_artist = track["artist_name"]
    print(f"[info] Matched: {matched_title!r} by {matched_artist!r} (id={track_id})")

    try:
        lyrics = client.get_lyrics(track_id)
    except MusixmatchError as e:
        print(f"[error] {e}")
        return False

    if not lyrics:
        print("[info] Lyrics not available for this track.")
        return False

    preview = lyrics[:200].replace("\n", " ")
    print(f"[info] Lyrics preview: {preview}...")

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lyrics-finder",
        description="Search Musixmatch for lyrics and embed them into MP3/M4A files.",
    )
    parser.add_argument(
        "paths",
        nargs="+",
        metavar="FILE_OR_DIR",
        help="Audio files or directories to process",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("MUSIXMATCH_API_KEY", ""),
        metavar="KEY",
        help="Musixmatch API key (or set MUSIXMATCH_API_KEY env var)",
    )
    parser.add_argument(
        "--title",
        default="",
        metavar="TITLE",
        help="Override track title for search (only useful with a single file)",
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.api_key:
        parser.error(
            "Musixmatch API key required. Pass --api-key or set the "
            "MUSIXMATCH_API_KEY environment variable.\n"
            "Get a free key at https://developer.musixmatch.com/"
        )

    files = _collect_files(args.paths)
    if not files:
        print("No supported audio files found.", file=sys.stderr)
        return 1

    print(f"Found {len(files)} file(s) to process.")

    client = MusixmatchClient(args.api_key)
    ok = 0
    skipped = 0

    for path in files:
        meta = None
        try:
            meta = read_metadata(path)
        except Exception:
            pass

        has_lyrics = bool(meta and meta.get("lyrics"))
        if has_lyrics and not args.overwrite and not args.interactive:
            print(f"\n{'='*60}")
            print(f"File   : {path}")
            print("[skip] Lyrics already present. Use --overwrite to replace.")
            skipped += 1
            continue

        success = _process_file(
            client=client,
            path=path,
            title_override=args.title,
            artist_override=args.artist,
            dry_run=args.dry_run,
            interactive=args.interactive,
            overwrite=args.overwrite,
        )
        if success:
            ok += 1
        else:
            skipped += 1

    print(f"\n{'='*60}")
    print(f"Done. {ok} updated, {skipped} skipped.")
    return 0
