"""Force-refresh tracks in Apple Music (macOS) after embedding tags.

Apple Music caches metadata and ignores the `refresh` AppleScript command
unless it believes the file has changed.  The reliable sequence is:
  1. Touch the file (update mtime) so the OS marks it as modified.
  2. Run a single batch AppleScript that removes the stale library entry
     and re-adds the file, which forces a full re-read of all embedded tags.

macOS only — all functions return a safe no-op result on other platforms.
"""

import os
import subprocess
import sys
from pathlib import Path


def _macos() -> bool:
    return sys.platform == "darwin"


def _run_script(script: str, timeout: int = 120) -> tuple[str, str]:
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=timeout,
    )
    return result.stdout.strip(), result.stderr.strip()


def _touch(paths: list[Path]) -> None:
    """Update mtime on every path so Apple Music sees them as changed."""
    for p in paths:
        try:
            os.utime(p, None)
        except OSError:
            pass


def _build_batch_script(posix_paths: list[str]) -> str:
    """Return an AppleScript that removes each track from the library and
    re-adds the file, forcing a full metadata re-read."""
    # Build an AppleScript list literal from the Python list
    escaped = [p.replace('"', '\\"') for p in posix_paths]
    as_list = "{" + ", ".join(f'"{p}"' for p in escaped) + "}"

    return f"""
tell application "Music"
    set pathList to {as_list}
    set refreshed to 0
    set notFound to 0
    set errors to 0

    repeat with aPath in pathList
        try
            set theLoc to (POSIX file aPath) as alias

            -- Remove existing entry (keeps the file on disk)
            set oldTracks to (every file track of library playlist 1 whose location is theLoc)
            repeat with t in oldTracks
                delete t
            end repeat

            -- Re-add from disk: Music re-reads all embedded tags
            add theLoc to library playlist 1
            set refreshed to refreshed + 1
        on error errMsg
            -- File not in library or other error — just try adding it
            try
                set theLoc to (POSIX file aPath) as alias
                add theLoc to library playlist 1
                set notFound to notFound + 1
            on error
                set errors to errors + 1
            end try
        end try
    end repeat

    return (refreshed as string) & "," & (notFound as string) & "," & (errors as string)
end tell
"""


def force_refresh_paths(paths: list[Path]) -> dict:
    """Force Apple Music to re-read embedded tags for the given files.

    Steps:
      1. Touch each file's mtime so the OS marks it as changed.
      2. Remove each track's library entry and re-add the file from disk.

    Returns a dict with keys:
      available  – False when not macOS
      refreshed  – tracks successfully removed + re-added
      added      – files that weren't in the library but were added
      errors     – count of files that failed entirely
      error_msg  – error string if the whole operation failed
    """
    if not _macos():
        return {"available": False, "refreshed": 0, "added": 0, "errors": 0}

    existing = [p for p in paths if p.exists()]
    if not existing:
        return {"available": True, "refreshed": 0, "added": 0, "errors": len(paths)}

    # Step 1 — touch files
    _touch(existing)

    # Step 2 — batch AppleScript
    posix_paths = [str(p.expanduser().resolve()) for p in existing]
    try:
        out, err = _run_script(_build_batch_script(posix_paths))
    except subprocess.TimeoutExpired:
        return {"available": True, "refreshed": 0, "added": 0, "errors": len(existing),
                "error_msg": "AppleScript timed out — library may be very large"}
    except Exception as exc:
        return {"available": True, "refreshed": 0, "added": 0, "errors": len(existing),
                "error_msg": str(exc)}

    # Parse "refreshed,added,errors" from the script return value
    parts = out.split(",")
    try:
        refreshed = int(parts[0]) if len(parts) > 0 else 0
        added     = int(parts[1]) if len(parts) > 1 else 0
        errs      = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        refreshed = added = errs = 0

    result: dict = {
        "available": True,
        "refreshed": refreshed,
        "added": added,
        "errors": errs,
    }
    if err:
        result["error_msg"] = err
    return result


def refresh_library() -> dict:
    """Touch every file in the library and tell Music to re-add them all.

    This is the nuclear option — use when you want every track refreshed.
    Returns: available, ok, track_count (or None), error_msg.
    """
    if not _macos():
        return {"available": False, "ok": False, "error_msg": "not macOS"}

    # Collect all file paths from the library first
    collect_script = """
tell application "Music"
    set pathList to {}
    repeat with t in every file track of library playlist 1
        try
            set end of pathList to POSIX path of (location of t)
        end try
    end repeat
    set out to ""
    repeat with p in pathList
        set out to out & p & linefeed
    end repeat
    return out
end tell
"""
    try:
        out, _ = _run_script(collect_script, timeout=60)
    except Exception as exc:
        return {"available": True, "ok": False, "error_msg": str(exc)}

    raw_paths = [p.strip() for p in out.splitlines() if p.strip()]
    if not raw_paths:
        return {"available": True, "ok": True, "track_count": 0}

    paths = [Path(p) for p in raw_paths]
    result = force_refresh_paths(paths)
    result["track_count"] = len(paths)
    result["ok"] = result.get("errors", 0) < len(paths)
    return result


def is_available() -> bool:
    if not _macos():
        return False
    try:
        _run_script('tell application "Music" to return name of current playlist')
        return True
    except Exception:
        return False
