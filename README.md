# MusicLyricsFinder

Find and embed lyrics and album art into your MP3/M4A library, and sync metadata
directly to a connected iPod Classic (stock firmware or Rockbox).

The project has two independent packages that share the same audio files:

- **`lyrics_finder`** — searches lyrics (lrclib.net, lyrics.ovh, optional Musixmatch)
  and album art (iTunes Search API), then embeds them into the file's tags.
  Available as a CLI and a local Flask web UI.
- **`ipod_manager`** — reads and writes the iPod's `iTunesDB` directly, so you can
  list/add/remove tracks and push metadata changes without iTunes/Apple Music.

## Installation

Requires Python 3.11+.

```bash
pip install -r requirements.txt
pip install -e .
```

This registers three console scripts: `lyrics-finder`, `lyrics-finder-ui`, `ipod-manager`.

## Usage

### CLI — search and embed lyrics/art

```bash
# Lyrics only, recurse into a directory
lyrics-finder ~/Music/SomeAlbum

# Lyrics + album art, overwrite existing tags
lyrics-finder ~/Music/SomeAlbum --artwork --overwrite

# Preview without writing
lyrics-finder track.mp3 --dry-run

# Remove embedded lyrics/art instead of searching
lyrics-finder ~/Music/SomeAlbum --clear
lyrics-finder ~/Music/SomeAlbum --clear-artwork
```

Run `lyrics-finder --help` for the full flag list (title/artist overrides,
`--artwork-only`, `--artwork-size`, `--musixmatch-key`, `--refresh-itunes` to
force Apple Music to pick up the changes on macOS).

### Web UI

```bash
lyrics-finder-ui                 # opens http://127.0.0.1:5000 in your browser
lyrics-finder-ui --port 8080 --no-browser
```

Scan a directory, see which files are missing lyrics/art, process them in
bulk with live progress, preview embedded art/lyrics per file, and push
changes to a connected iPod.

### iPod Classic manager

```bash
ipod-manager list                          # auto-detects the mounted iPod
ipod-manager add song.mp3 --album "Title"
ipod-manager remove 1234 5678
ipod-manager sync-lyrics                   # fetch + embed lyrics for every track on the iPod
ipod-manager update 1234 --title "New Title"
ipod-manager update-all --genre "Rock"
```

Supports iPod Classic generations 1G–7G. Stock firmware 6G/7G requires a
correct `iTunesDB` hash to boot after writes — install `libgpod`
(`brew install libgpod`) for that; Rockbox needs no hash and just requires
re-running "Initialize Now" under Settings > Database after a change.

## Architecture

```
lyrics_finder/
  sources.py      lyrics backends (LrcLib exact+fuzzy, lyrics.ovh, Musixmatch),
                   raced in parallel via a thread pool; first hit wins
  artwork.py       iTunes Search API lookup + match scoring/dedup
  metadata.py      MP3 (ID3) / M4A (MP4) tag read/write via mutagen
  itunes_sync.py   macOS-only: force Apple Music to re-scan changed files
  cli.py           `lyrics-finder` entry point
  web.py           Flask app behind `lyrics-finder-ui`

ipod_manager/
  detector.py      finds the iPod mount point on macOS/Linux/Windows
  itunesdb.py      binary parser/writer for the iTunesDB format
  hash.py          iPod 6G/7G hash58 calculation (libgpod fallback)
  manager.py       high-level add/remove/update/sync API used by the CLI and web UI
  cli.py           `ipod-manager` entry point
```

## Tests

```bash
pytest
```

## Tools

`tools/debug_ipod.py` is a standalone diagnostic script for inspecting the raw
binary structure of an iPod's `iTunesDB` (endianness, header sizes, record
walk). Not part of the installed package; run directly with
`python tools/debug_ipod.py /Volumes/IPOD_NAME`.
