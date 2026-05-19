from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from .metadata import read_metadata, write_lyrics
from .sources import LyricsFinder

SUPPORTED_EXTENSIONS = {".mp3", ".m4a", ".aac"}

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/scan", methods=["POST"])
def scan():
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
                meta = {"title": "", "artist": "", "album": "", "lyrics": ""}
            files.append(
                {
                    "path": str(fp),
                    "name": fp.name,
                    "title": meta["title"],
                    "artist": meta["artist"],
                    "album": meta["album"],
                    "has_lyrics": bool(meta["lyrics"]),
                }
            )

    return jsonify({"files": files, "count": len(files)})


@app.route("/api/process", methods=["POST"])
def process():
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


# ── iPod Manager API ───────────────────────────────────────────────────────

def _get_mount(mount_path: str):
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
def ipod_detect():
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
def ipod_tracks():
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


@app.route("/api/ipod/sync-lyrics")
def ipod_sync_lyrics():
    import json as _json
    from concurrent.futures import ThreadPoolExecutor

    mount_path = request.args.get("mount", "").strip()
    musixmatch_key = request.args.get("musixmatch_key", "").strip()

    def generate():
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

            def _process(indexed_track):
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

            with ThreadPoolExecutor(max_workers=_SYNC_WORKERS) as ex:
                for event, tid, lyrics in ex.map(_process, enumerate(tracks, 1)):
                    if event['status'] == 'found':
                        found += 1
                        if tid is not None and tid in db_by_id:
                            db_by_id[tid].lyrics = lyrics
                            db_dirty = True
                    yield f"data: {_json.dumps(event)}\n\n"

            if db_dirty and not mount.rockbox:
                mgr._save_db(db)

            yield f"data: {_json.dumps({'done': True, 'found': found, 'total': total})}\n\n"
        except Exception as e:
            yield f"data: {_json.dumps({'error': str(e)})}\n\n"

    return Response(stream_with_context(generate()),
                    content_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/ipod/add", methods=["POST"])
def ipod_add():
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
def ipod_remove():
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
def ipod_track_lyrics():
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
def ipod_update_track():
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
def ipod_update():
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
