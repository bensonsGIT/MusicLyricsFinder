from pathlib import Path

from flask import Flask, jsonify, render_template, request

from .metadata import read_metadata, write_lyrics
from .musixmatch import MusixmatchClient, MusixmatchError

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
    api_key = data.get("api_key", "").strip()
    overwrite = bool(data.get("overwrite", False))
    dry_run = bool(data.get("dry_run", False))
    title_override = data.get("title", "").strip()
    artist_override = data.get("artist", "").strip()

    if not api_key:
        return jsonify({"error": "API key required"}), 400

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

    client = MusixmatchClient(api_key)

    try:
        results = client.search_track(title, artist)
    except MusixmatchError as e:
        return jsonify({"error": str(e)}), 502

    if not results:
        return jsonify({"status": "not_found", "reason": "no_results"})

    track = results[0]["track"]
    track_id = track["track_id"]
    matched_title = track["track_name"]
    matched_artist = track["artist_name"]

    try:
        lyrics = client.get_lyrics(track_id)
    except MusixmatchError as e:
        return jsonify({"error": str(e)}), 502

    if not lyrics:
        return jsonify({"status": "not_found", "reason": "no_lyrics"})

    if not dry_run:
        try:
            write_lyrics(path, lyrics)
        except Exception as e:
            return jsonify({"error": f"Failed to write lyrics: {e}"}), 500

    return jsonify(
        {
            "status": "success",
            "matched_title": matched_title,
            "matched_artist": matched_artist,
            "lyrics": lyrics,
            "dry_run": dry_run,
        }
    )
