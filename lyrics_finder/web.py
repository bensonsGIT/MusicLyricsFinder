from __future__ import annotations

from pathlib import Path
from typing import Any, Generator

from flask import Flask, Response, jsonify, render_template, request, stream_with_context
from flask.typing import ResponseReturnValue

from .artwork import ArtworkError, ArtworkFinder
from .itunes_sync import force_refresh_paths, refresh_library
from .metadata import (
    clear_artwork,
    clear_lyrics,
    read_artwork,
    read_metadata,
    write_artwork,
    write_lyrics,
)
from .sources import LyricsFinder

SUPPORTED_EXTENSIONS = {".mp3", ".m4a", ".aac"}

app = Flask(__name__)


@app.route("/")
def index() -> str:
    return render_template("index.html")


@app.route("/api/scan", methods=["POST"])
def scan() -> ResponseReturnValue:
    data = request.get_json(force=True)
    directory = (data or {}).get("directory", "").strip()

    if not directory:
        return jsonify({"error": "No directory provided"}), 400

    path = Path(directory).expanduser()
    if not path.exists():
        return jsonify({"error": f"Path does not exist: {directory}"}), 400
    if not path.is_dir():
        return jsonify({"error": f"Not a directory: {directory}"}), 400

    files = []
    for ext in SUPPORTED_EXTENSIONS:
        for fp in sorted(path.rglob(f"*{ext}")):
            try:
                meta = read_metadata(fp)
            except Exception:
                meta = {"title": "", "artist": "", "album": "", "lyrics": "", "has_artwork": False}
            files.append(
                {
                    "path": str(fp),
                    "name": fp.name,
                    "title": meta["title"],
                    "artist": meta["artist"],
                    "album": meta["album"],
                    "has_lyrics": bool(meta["lyrics"]),
                    "has_artwork": bool(meta.get("has_artwork")),
                    "art_thumb": (
                        f"/api/file/artwork?path={str(fp)}"
                        if meta.get("has_artwork") else None
                    ),
                }
            )

    return jsonify({"files": files, "count": len(files)})


@app.route("/api/clear", methods=["POST"])
def clear() -> ResponseReturnValue:
    data = request.get_json(force=True) or {}
    file_path = data.get("path", "").strip()

    path = Path(file_path)
    if not path.is_file():
        return jsonify({"error": f"File not found: {file_path}"}), 400

    try:
        had = clear_lyrics(path)
    except Exception as e:
        return jsonify({"error": f"Failed to clear lyrics: {e}"}), 500

    return jsonify({"status": "cleared" if had else "no_lyrics", "had_lyrics": had})


@app.route("/api/file/lyrics", methods=["POST"])
def file_lyrics() -> ResponseReturnValue:
    """Return the lyrics currently embedded in a local file."""
    data = request.get_json(force=True) or {}
    file_path = data.get("path", "").strip()

    path = Path(file_path)
    if not path.is_file():
        return jsonify({"error": f"File not found: {file_path}"}), 400

    try:
        meta = read_metadata(path)
    except Exception as e:
        return jsonify({"error": f"Cannot read metadata: {e}"}), 400

    lyrics = meta.get("lyrics") or ""
    return jsonify(
        {
            "has_lyrics": bool(lyrics),
            "lyrics": lyrics,
            "title": meta.get("title", ""),
            "artist": meta.get("artist", ""),
            "album": meta.get("album", ""),
        }
    )


@app.route("/api/file/artwork", methods=["GET"])
def file_artwork() -> ResponseReturnValue:
    """Serve the album art currently embedded in a local file as an image."""
    file_path = (request.args.get("path") or "").strip()

    path = Path(file_path)
    if not path.is_file():
        return jsonify({"error": f"File not found: {file_path}"}), 400

    try:
        image, mime = read_artwork(path)
    except Exception as e:
        return jsonify({"error": f"Cannot read artwork: {e}"}), 400

    if not image:
        return jsonify({"error": "No embedded artwork"}), 404

    return Response(image, mimetype=mime or "image/jpeg")


@app.route("/api/process", methods=["POST"])
def process() -> ResponseReturnValue:
    data = request.get_json(force=True) or {}
    file_path = data.get("path", "").strip()
    overwrite = bool(data.get("overwrite", False))
    dry_run = bool(data.get("dry_run", False))
    title_override = data.get("title", "").strip()
    artist_override = data.get("artist", "").strip()
    musixmatch_key = data.get("musixmatch_key", "").strip()

    path = Path(file_path)
    if not path.is_file():
        return jsonify({"error": f"File not found: {file_path}"}), 400

    try:
        meta = read_metadata(path)
    except Exception as e:
        return jsonify({"error": f"Cannot read metadata: {e}"}), 400

    if meta["lyrics"] and not overwrite:
        return jsonify({"status": "skipped", "reason": "already_has_lyrics", "has_lyrics": True})

    title = title_override or meta["title"]
    artist = artist_override or meta["artist"]

    if not title:
        return jsonify({"status": "skipped", "reason": "no_title", "has_lyrics": False})

    finder = LyricsFinder(musixmatch_key=musixmatch_key)
    lyrics, source = finder.find(title, artist, meta.get("album", ""))

    if not lyrics:
        return jsonify({"status": "not_found", "reason": "no_results"})

    if not dry_run:
        try:
            write_lyrics(path, lyrics)
        except Exception as e:
            return jsonify({"error": f"Failed to write lyrics: {e}"}), 500

    return jsonify(
        {
            "status": "success",
            "matched_title": title,
            "matched_artist": artist,
            "source": source,
            "lyrics": lyrics,
            "dry_run": dry_run,
        }
    )


# ── Apple Music refresh ────────────────────────────────────────────────────

@app.route("/api/itunes/refresh", methods=["POST"])
def itunes_refresh() -> ResponseReturnValue:
    """Refresh specific file paths in Apple Music (macOS only).

    Body: {"paths": ["/abs/path/to/song.mp3", ...]}
    Returns counts of refreshed / not_found tracks plus any errors.
    """
    data = request.get_json(force=True) or {}
    raw_paths = data.get("paths") or []
    if not isinstance(raw_paths, list):
        return jsonify({"error": "'paths' must be a list"}), 400

    paths = [Path(p) for p in raw_paths if isinstance(p, str) and p.strip()]
    if not paths:
        return jsonify({"error": "No paths provided"}), 400

    result = force_refresh_paths(paths)
    return jsonify(result)


@app.route("/api/itunes/refresh-library", methods=["POST"])
def itunes_refresh_library() -> ResponseReturnValue:
    """Tell Apple Music to refresh its entire library (macOS only)."""
    result = refresh_library()
    return jsonify(result)


@app.route("/api/itunes/available", methods=["GET"])
def itunes_available() -> ResponseReturnValue:
    """Return whether Apple Music refresh is available on this machine."""
    import sys
    return jsonify({"available": sys.platform == "darwin"})


# ── Album Art API ──────────────────────────────────────────────────────────

@app.route("/api/artwork/search", methods=["POST"])
def artwork_search() -> ResponseReturnValue:
    data = request.get_json(force=True) or {}
    term = data.get("term", "").strip()
    title = data.get("title", "").strip()
    artist = data.get("artist", "").strip()
    album = data.get("album", "").strip()
    entity = (data.get("entity", "album") or "album").strip()
    country = (data.get("country", "US") or "US").strip()
    try:
        size = int(data.get("size", 600) or 600)
    except (TypeError, ValueError):
        size = 600

    if not term:
        term = " ".join(x for x in [artist, album or title] if x).strip()
    if not term:
        return jsonify({"error": "Nothing to search for — provide a title/artist or a search term"}), 400

    finder = ArtworkFinder()
    try:
        results = finder.search(term, entity=entity, country=country, size=size)
    except ArtworkError as e:
        return jsonify({"error": str(e)}), 502

    return jsonify({"term": term, "results": [r.as_dict() for r in results]})


@app.route("/api/artwork/apply", methods=["POST"])
def artwork_apply() -> ResponseReturnValue:
    data = request.get_json(force=True) or {}
    file_path = data.get("path", "").strip()
    art_url = data.get("art_url", "").strip()

    path = Path(file_path)
    if not path.is_file():
        return jsonify({"error": f"File not found: {file_path}"}), 400
    if not art_url:
        return jsonify({"error": "No artwork URL provided"}), 400

    finder = ArtworkFinder()
    try:
        image, mime = finder.download(art_url)
    except ArtworkError as e:
        return jsonify({"error": str(e)}), 502

    try:
        write_artwork(path, image, mime)
    except Exception as e:
        return jsonify({"error": f"Failed to embed artwork: {e}"}), 500

    return jsonify({"status": "applied", "bytes": len(image), "mime": mime})


@app.route("/api/artwork/auto", methods=["POST"])
def artwork_auto() -> ResponseReturnValue:
    """Search for and embed the best-matching cover in one step (no picker)."""
    data = request.get_json(force=True) or {}
    file_path = data.get("path", "").strip()
    overwrite = bool(data.get("overwrite", False))
    dry_run = bool(data.get("dry_run", False))
    title_override = data.get("title", "").strip()
    artist_override = data.get("artist", "").strip()
    try:
        size = int(data.get("size", 600) or 600)
    except (TypeError, ValueError):
        size = 600

    path = Path(file_path)
    if not path.is_file():
        return jsonify({"error": f"File not found: {file_path}"}), 400

    try:
        meta = read_metadata(path)
    except Exception as e:
        return jsonify({"error": f"Cannot read metadata: {e}"}), 400

    if meta.get("has_artwork") and not overwrite:
        return jsonify({"status": "skipped", "reason": "already_has_artwork", "has_artwork": True})

    title = title_override or meta["title"]
    artist = artist_override or meta["artist"]
    album = meta.get("album", "")
    if not title and not album:
        return jsonify({"status": "skipped", "reason": "no_title", "has_artwork": False})

    finder = ArtworkFinder()
    try:
        result = finder.find(title, artist, album, size=size)
    except ArtworkError as e:
        return jsonify({"error": str(e)}), 502

    if not result:
        return jsonify({"status": "not_found", "reason": "no_results"})

    if not dry_run:
        try:
            image, mime = finder.download(result.art_url)
            write_artwork(path, image, mime)
        except ArtworkError as e:
            return jsonify({"error": str(e)}), 502
        except Exception as e:
            return jsonify({"error": f"Failed to embed artwork: {e}"}), 500

    return jsonify(
        {
            "status": "success",
            "matched_album": result.album or result.track,
            "matched_artist": result.artist,
            "thumb_url": result.thumb_url,
            "art_url": result.art_url,
            "dry_run": dry_run,
        }
    )


@app.route("/api/artwork/clear", methods=["POST"])
def artwork_clear() -> ResponseReturnValue:
    data = request.get_json(force=True) or {}
    file_path = data.get("path", "").strip()

    path = Path(file_path)
    if not path.is_file():
        return jsonify({"error": f"File not found: {file_path}"}), 400

    try:
        had = clear_artwork(path)
    except Exception as e:
        return jsonify({"error": f"Failed to clear artwork: {e}"}), 500

    return jsonify({"status": "cleared" if had else "no_artwork", "had_artwork": had})


# ── iPod Manager API ───────────────────────────────────────────────────────

def _get_mount(mount_path: str) -> tuple[Any | None, str | None]:
    from ipod_manager.detector import find_ipod, probe
    if mount_path:
        m = probe(Path(mount_path))
        if m is None:
            return None, f"No iPod found at {mount_path!r}"
        return m, None
    m = find_ipod("")
    if m is None:
        return None, "No iPod detected. Plug in your iPod or enter the mount path."
    return m, None


@app.route("/api/ipod/detect", methods=["POST"])
def ipod_detect() -> ResponseReturnValue:
    data = request.get_json(force=True) or {}
    mount, err = _get_mount(data.get("mount", "").strip())
    if err:
        return jsonify({"error": err}), 404
    try:
        from ipod_manager.manager import IpodManager
        mgr = IpodManager(mount)
        tracks = mgr.list_tracks()
        return jsonify({
            "root": str(mount.root),
            "rockbox": mount.rockbox,
            "firmware": "Rockbox" if mount.rockbox else "Stock",
            "track_count": len(tracks),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ipod/tracks", methods=["POST"])
def ipod_tracks() -> ResponseReturnValue:
    data = request.get_json(force=True) or {}
    mount, err = _get_mount(data.get("mount", "").strip())
    if err:
        return jsonify({"error": err}), 404
    try:
        from ipod_manager.manager import IpodManager
        mgr = IpodManager(mount)
        tracks = mgr.list_tracks()
        return jsonify({"tracks": [
            {"id": t.track_id, "title": t.title, "artist": t.artist,
             "album": t.album, "genre": t.genre, "duration": t.duration_ms,
             "track_number": t.track_number, "year": t.year}
            for t in tracks
        ]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


_SYNC_WORKERS = 20


@app.route("/api/ipod/lyrics-status", methods=["POST"])
def ipod_lyrics_status() -> ResponseReturnValue:
    """Diagnostic: report how many tracks have lyrics in the DB."""
    data = request.get_json(force=True) or {}
    mount, err = _get_mount(data.get("mount", "").strip())
    if err:
        return jsonify({"error": err}), 404
    try:
        from ipod_manager.manager import IpodManager
        mgr = IpodManager(mount)
        db = mgr._load_db()
        with_lyrics = [t for t in db.tracks if t.lyrics]
        sample = [{"id": t.track_id, "title": t.title,
                   "lyrics_preview": t.lyrics[:80]}
                  for t in with_lyrics[:5]]
        return jsonify({
            "total": len(db.tracks),
            "lyrics_in_db": len(with_lyrics),
            "sample": sample,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500





@app.route("/api/ipod/push-tags")
def ipod_push_tags() -> ResponseReturnValue:
    """Read embedded tags from each iPod audio file and push them to the iTunesDB.

    Streams SSE events so the UI can show per-track progress.
    """
    import json as _json

    mount_path = request.args.get("mount", "").strip()
    raw_fields = request.args.get("fields", "").strip()
    fields: set[str] | None = (
        {f.strip() for f in raw_fields.split(",") if f.strip()} if raw_fields else None
    )

    ALL_FIELDS = {
        "title", "artist", "album", "genre", "composer",
        "year", "track_number", "lyrics", "file_size",
    }
    sync = fields if fields is not None else ALL_FIELDS

    def generate() -> Generator[str, None, None]:
        try:
            mount, err = _get_mount(mount_path)
            if err:
                yield f"data: {_json.dumps({'error': err})}\n\n"
                return

            from ipod_manager.manager import IpodManager, _accessible, _unhide_ipod_dirs
            _unhide_ipod_dirs(mount.root)
            mgr = IpodManager(mount)
            db = mgr._load_db()
            total = len(db.tracks)

            updated = failed = 0
            for i, track in enumerate(db.tracks, 1):
                local = track.local_path(mount.root)
                if not _accessible(local):
                    failed += 1
                    yield f"data: {_json.dumps({'i': i, 'total': total, 'track': track.title or local.name, 'status': 'missing'})}\n\n"
                    continue
                try:
                    meta = read_metadata(local)
                    if "title" in sync and meta.get("title"):
                        track.title = meta["title"]
                    if "artist" in sync and meta.get("artist"):
                        track.artist = meta["artist"]
                    if "album" in sync and meta.get("album"):
                        track.album = meta["album"]
                    if "genre" in sync and meta.get("genre"):
                        track.genre = meta["genre"]
                    if "composer" in sync and meta.get("composer"):
                        track.composer = meta["composer"]
                    if "year" in sync and meta.get("year"):
                        raw = str(meta["year"])
                        if raw[:4].isdigit():
                            track.year = int(raw[:4])
                    if "track_number" in sync and meta.get("track_number"):
                        try:
                            track.track_number = int(meta["track_number"])
                        except (ValueError, TypeError):
                            pass
                    if "lyrics" in sync:
                        track.lyrics = meta.get("lyrics") or ""
                    if "file_size" in sync:
                        track.file_size = local.stat().st_size
                    updated += 1
                    yield f"data: {_json.dumps({'i': i, 'total': total, 'track': track.title or local.name, 'status': 'updated', 'has_lyrics': bool(track.lyrics)})}\n\n"
                except Exception as exc:
                    failed += 1
                    yield f"data: {_json.dumps({'i': i, 'total': total, 'track': track.title or local.name, 'status': 'error', 'error': str(exc)})}\n\n"

            if not mount.rockbox:
                mgr._save_db(db)

            yield f"data: {_json.dumps({'done': True, 'updated': updated, 'failed': failed, 'total': total})}\n\n"
        except Exception as e:
            yield f"data: {_json.dumps({'error': str(e)})}\n\n"

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/ipod/sync-lyrics")
def ipod_sync_lyrics() -> ResponseReturnValue:
    import json as _json
    from concurrent.futures import ThreadPoolExecutor

    mount_path = request.args.get("mount", "").strip()
    musixmatch_key = request.args.get("musixmatch_key", "").strip()

    def generate() -> Generator[str, None, None]:
        try:
            mount, err = _get_mount(mount_path)
            if err:
                yield f"data: {_json.dumps({'error': err})}\n\n"
                return

            from ipod_manager.manager import IpodManager, _accessible, _unhide_ipod_dirs
            _unhide_ipod_dirs(mount.root)
            mgr = IpodManager(mount)
            tracks = mgr.list_tracks()
            finder = LyricsFinder(musixmatch_key=musixmatch_key)
            total = len(tracks)
            found = 0

            db = mgr._load_db()
            db_by_id = {t.track_id: t for t in db.tracks}
            db_dirty = False

            def _process(indexed_track: tuple[int, Any]) -> tuple[dict[str, Any], int | None, str | None]:
                i, track = indexed_track
                local = track.local_path(mount.root)
                key = track.title or local.name
                if not _accessible(local):
                    return {'i': i, 'total': total, 'track': key, 'status': 'missing'}, None, None
                lyrics, source = finder.find(track.title, track.artist, track.album)
                if lyrics:
                    try:
                        write_lyrics(local, lyrics, track.title, track.artist, track.album)
                        return {'i': i, 'total': total, 'track': key, 'status': 'found', 'source': source}, track.track_id, lyrics
                    except Exception as exc:
                        return {'i': i, 'total': total, 'track': key, 'status': 'error', 'error': str(exc)}, None, None
                return {'i': i, 'total': total, 'track': key, 'status': 'not_found'}, None, None

            try:
                with ThreadPoolExecutor(max_workers=_SYNC_WORKERS) as ex:
                    for event, tid, lyrics in ex.map(_process, enumerate(tracks, 1)):
                        if event['status'] == 'found':
                            found += 1
                            if tid is not None and tid in db_by_id:
                                db_by_id[tid].lyrics = lyrics
                                db_dirty = True
                        yield f"data: {_json.dumps(event)}\n\n"
            finally:
                if db_dirty and not mount.rockbox:
                    mgr._save_db(db)

            yield f"data: {_json.dumps({'done': True, 'found': found, 'total': total})}\n\n"
        except Exception as e:
            yield f"data: {_json.dumps({'error': str(e)})}\n\n"

    return Response(stream_with_context(generate()),
                    content_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/ipod/add", methods=["POST"])
def ipod_add() -> ResponseReturnValue:
    import tempfile
    if request.files.get("file"):
        mount_path = request.form.get("mount", "").strip()
        title = request.form.get("title", "")
        artist = request.form.get("artist", "")
        album = request.form.get("album", "")
        genre = request.form.get("genre", "")
        year = int(request.form.get("year", 0) or 0)
        track_number = int(request.form.get("track_number", 0) or 0)
        uploaded = request.files["file"]
        suffix = Path(uploaded.filename or "").suffix.lower() or ".mp3"
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        uploaded.save(tmp.name)
        src = Path(tmp.name)
        cleanup = True
    else:
        data = request.get_json(force=True) or {}
        mount_path = data.get("mount", "").strip()
        src = Path(data.get("path", "").strip())
        if not src.is_file():
            return jsonify({"error": f"File not found: {src}"}), 400
        title = data.get("title", "")
        artist = data.get("artist", "")
        album = data.get("album", "")
        genre = data.get("genre", "")
        year = int(data.get("year", 0) or 0)
        track_number = int(data.get("track_number", 0) or 0)
        cleanup = False

    mount, err = _get_mount(mount_path)
    if err:
        if cleanup:
            src.unlink(missing_ok=True)
        return jsonify({"error": err}), 404

    try:
        from ipod_manager.manager import IpodManager
        mgr = IpodManager(mount)
        t = mgr.add_track(src, title=title, artist=artist, album=album,
                          genre=genre, year=year, track_number=track_number)
        return jsonify({"ok": True, "track": {
            "id": t.track_id, "title": t.title, "artist": t.artist,
            "album": t.album, "duration": t.duration_ms,
        }})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if cleanup:
            src.unlink(missing_ok=True)


@app.route("/api/ipod/remove", methods=["POST"])
def ipod_remove() -> ResponseReturnValue:
    data = request.get_json(force=True) or {}
    mount, err = _get_mount(data.get("mount", "").strip())
    if err:
        return jsonify({"error": err}), 404
    track_id = data.get("track_id")
    if track_id is None:
        return jsonify({"error": "track_id required"}), 400
    try:
        from ipod_manager.manager import IpodManager
        mgr = IpodManager(mount)
        ok = mgr.remove_track(int(track_id))
        return jsonify({"ok": ok}) if ok else (jsonify({"error": f"Track {track_id} not found"}), 404)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ipod/track-lyrics", methods=["POST"])
def ipod_track_lyrics() -> ResponseReturnValue:
    """Read lyrics from a track file, or write them (file + DB) when 'lyrics' key is present."""
    data = request.get_json(force=True) or {}
    mount, err = _get_mount(data.get("mount", "").strip())
    if err:
        return jsonify({"error": err}), 404
    track_id = data.get("track_id")
    if track_id is None:
        return jsonify({"error": "track_id required"}), 400
    lyrics_text = data.get("lyrics")  # None = read, str = write
    try:
        from ipod_manager.manager import IpodManager
        mgr = IpodManager(mount)
        db = mgr._load_db()
        target = next((t for t in db.tracks if t.track_id == int(track_id)), None)
        if target is None:
            return jsonify({"error": f"Track {track_id} not found"}), 404
        local = target.local_path(mount.root)
        if not local.exists():
            return jsonify({"error": f"File not found on iPod: {local.name}"}), 404
        if lyrics_text is not None:
            mgr.update_lyrics(int(track_id), lyrics_text)
            return jsonify({"ok": True, "lyrics": lyrics_text})
        # Read — prefer DB lyrics (already parsed), fall back to file tag
        lyrics = target.lyrics or read_metadata(local).get("lyrics", "")
        return jsonify({"lyrics": lyrics, "has_lyrics": bool(lyrics)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ipod/update-track", methods=["POST"])
def ipod_update_track() -> ResponseReturnValue:
    """Update metadata and optionally lyrics in one request."""
    data = request.get_json(force=True) or {}
    mount, err = _get_mount(data.get("mount", "").strip())
    if err:
        return jsonify({"error": err}), 404
    track_id = data.get("track_id")
    if track_id is None:
        return jsonify({"error": "track_id required"}), 400
    try:
        from ipod_manager.manager import IpodManager
        mgr = IpodManager(mount)
        kwargs = {k: data[k] for k in
                  ("title", "artist", "album", "album_artist", "genre", "composer", "comment")
                  if k in data}
        if "year" in data:
            kwargs["year"] = int(data["year"]) if data["year"] else None
        if "track_number" in data:
            kwargs["track_number"] = int(data["track_number"]) if data["track_number"] else None
        if kwargs:
            ok = mgr.update_metadata(int(track_id), **kwargs)
            if not ok:
                return jsonify({"error": f"Track {track_id} not found"}), 404
        if "lyrics" in data:
            mgr.update_lyrics(int(track_id), data["lyrics"])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ipod/update", methods=["POST"])
def ipod_update() -> ResponseReturnValue:
    data = request.get_json(force=True) or {}
    mount, err = _get_mount(data.get("mount", "").strip())
    if err:
        return jsonify({"error": err}), 404
    track_id = data.get("track_id")
    if track_id is None:
        return jsonify({"error": "track_id required"}), 400
    kwargs = {k: data[k] for k in
              ("title", "artist", "album", "album_artist", "genre", "composer", "comment")
              if k in data}
    if "year" in data:
        kwargs["year"] = int(data["year"]) if data["year"] else None
    if "track_number" in data:
        kwargs["track_number"] = int(data["track_number"]) if data["track_number"] else None
    try:
        from ipod_manager.manager import IpodManager
        mgr = IpodManager(mount)
        ok = mgr.update_metadata(int(track_id), **kwargs)
        return jsonify({"ok": ok}) if ok else (jsonify({"error": f"Track {track_id} not found"}), 404)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
