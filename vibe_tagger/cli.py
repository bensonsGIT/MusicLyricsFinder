"""`vibe-tagger`: label each song's vibe in its Grouping tag for Play Similar."""

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from .tags import SUPPORTED, is_vibe, label, read_grouping, write_grouping

# About 400 songs per vibe on a 6,000-8,000 song library; 40 vibes shifted far more
# between runs, and Play Similar needs enough same-vibe songs to choose from.
DEFAULT_CLUSTERS = 20


def default_cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "musiclyricsfinder" / "vibe-features.json"


def find_audio(paths: list[str]) -> list[Path]:
    found = []
    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_dir():
            found += [p for p in sorted(path.rglob("*")) if p.suffix.lower() in SUPPORTED]
        elif path.suffix.lower() in SUPPORTED:
            found.append(path)
    return found


def _load_cache(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _save_cache(path: Path | None, cache: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(cache))
    temporary.replace(path)


def prune(cache: dict, roots: list[str]) -> int:
    """Drop cached songs under the scanned folders that no longer exist.

    Only scanned folders are pruned, so an unmounted iPod or another library
    keeps its cached analyses."""
    prefixes = [str(Path(root).expanduser().resolve()) + os.sep
                for root in roots if Path(root).expanduser().is_dir()]
    stale = [key for key in cache
             if any(key.startswith(prefix) for prefix in prefixes) and not Path(key).exists()]
    for key in stale:
        del cache[key]
    return len(stale)


def _analyze(path: str) -> list[float]:
    from .features import analyze
    return analyze(Path(path))


def measure(files: list[Path], cache: dict, cache_path: Path | None,
            jobs: int) -> dict[Path, list[float]]:
    """Feature vectors per file, analysing only files that changed since the last run.

    The cache is keyed by path and file size; main() refreshes the size after its
    own tag writes so retagging does not force re-analysis."""
    from .features import FEATURES

    vectors, pending = {}, []
    for path in files:
        key = str(path.resolve())
        entry = cache.get(key)
        # Re-analyse entries from older feature sets or ones holding NaN, which
        # earlier versions could store and which would break clustering.
        if (entry and entry.get("size") == path.stat().st_size
                and len(entry["features"]) == len(FEATURES)
                and all(math.isfinite(value) for value in entry["features"])):
            vectors[path] = entry["features"]
        else:
            pending.append(path)

    if pending:
        print(f"Analysing {len(pending)} of {len(files)} songs "
              f"({len(files) - len(pending)} cached)…", flush=True)
    failed = 0
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(_analyze, str(path)): path for path in pending}
        for done, future in enumerate(as_completed(futures), 1):
            path = futures[future]
            try:
                vectors[path] = future.result()
                cache[str(path.resolve())] = {"size": path.stat().st_size,
                                              "features": vectors[path]}
            except Exception as error:  # unreadable, DRM-protected or tiny files
                failed += 1
                print(f"  skipped {path.name}: {error}", file=sys.stderr)
            if done % 50 == 0 or done == len(pending):
                print(f"  {done}/{len(pending)}", flush=True)
                _save_cache(cache_path, cache)
    if failed:
        print(f"{failed} songs could not be analysed and were left untagged.")
    return vectors


def _song_name(path: Path) -> str:
    from lyrics_finder.metadata import read_metadata
    try:
        meta = read_metadata(path)
    except Exception:
        return path.stem
    artist, title = meta.get("artist"), meta.get("title")
    return f"{artist} – {title}" if artist and title else path.stem


def summarize(vibes: dict[Path, int], vectors: dict[Path, list[float]], samples: int = 4) -> None:
    from .cluster import describe

    members = defaultdict(list)
    for path, vibe in vibes.items():
        members[vibe].append(path)
    groups = {vibe: [vectors[p] for p in paths] for vibe, paths in members.items()}
    for vibe in sorted(members):
        paths = members[vibe]
        summary = describe(groups[vibe], list(groups.values()))
        print(f"\n{label(vibe)}  {len(paths)} songs  ({summary})")
        step = max(1, len(paths) // samples)
        for path in paths[::step][:samples]:
            print(f"    {_song_name(path)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vibe-tagger",
        description=(
            "Analyse how songs sound and write a vibe label (e.g. vibe:07) into "
            "their Grouping tag. RepriseOS Play Similar prefers songs with the "
            "same vibe. Requires: pip install -e '.[vibes]'"
        ),
    )
    parser.add_argument("paths", nargs="+", metavar="FILE_OR_DIR",
                        help="Audio files or directories (your whole library works best)")
    parser.add_argument("--clusters", type=int, default=DEFAULT_CLUSTERS,
                        help=f"Number of vibes (default {DEFAULT_CLUSTERS})")
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1),
                        help="Songs analysed in parallel (default: CPU cores - 1)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Analyse and print the vibes without writing tags")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite Grouping even when it holds something other than a vibe")
    parser.add_argument("--clear", action="store_true",
                        help="Remove vibe labels from Grouping instead of analysing")
    parser.add_argument("--no-cache", action="store_true",
                        help="Re-analyse every song instead of reusing earlier results")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    files = find_audio(args.paths)
    if not files:
        print("No .mp3/.m4a/.aac files found.", file=sys.stderr)
        return 1

    if args.clear:
        cleared = 0
        for path in files:
            if is_vibe(read_grouping(path)):
                write_grouping(path, "")
                cleared += 1
        print(f"Removed vibe labels from {cleared} songs.")
        return 0

    try:
        from .cluster import assign
    except ImportError:
        print("vibe-tagger needs librosa and scikit-learn: pip install -e '.[vibes]'",
              file=sys.stderr)
        return 1

    cache_path = None if args.no_cache else default_cache_path()
    cache = _load_cache(cache_path)
    pruned = prune(cache, args.paths)
    if pruned:
        print(f"Forgot {pruned} cached songs that are no longer in the library.")
        _save_cache(cache_path, cache)
    vectors = measure(files, cache, cache_path, args.jobs)
    if not vectors:
        print("No songs could be analysed.", file=sys.stderr)
        return 1
    paths = list(vectors)
    vibes = dict(zip(paths, assign([vectors[p] for p in paths], args.clusters)))
    summarize(vibes, vectors)

    if args.dry_run:
        print("\nDry run: no tags written.")
        return 0
    written = kept = 0
    for path, vibe in vibes.items():
        current = read_grouping(path)
        if current and not is_vibe(current) and not args.force:
            kept += 1
            continue
        if current != label(vibe):
            write_grouping(path, label(vibe))
            written += 1
            entry = cache.get(str(path.resolve()))
            if entry:
                entry["size"] = path.stat().st_size
    _save_cache(cache_path, cache)
    print(f"\nWrote vibes to {written} songs.")
    if kept:
        print(f"Left {kept} songs alone because their Grouping holds something else "
              "(use --force to overwrite).")
    print("Sync the changed songs to your iPod so Play Similar can use them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
