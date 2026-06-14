"""Refresh tracks in Apple Music (macOS) after embedding tags with Lyrics Finder.

Uses osascript to invoke AppleScript — macOS only.  On any other platform
every function returns immediately with a descriptive error string.
"""

import subprocess
import sys
from pathlib import Path


def _macos() -> bool:
    return sys.platform == "darwin"


def _run_script(script: str) -> tuple[str, str]:
    """Run *script* via osascript and return (stdout, stderr)."""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=60,
    )
    return result.stdout.strip(), result.stderr.strip()


def refresh_paths(paths: list[Path]) -> dict:
    """Tell Apple Music to refresh specific file paths.

    Returns a dict with keys:
      refreshed  – number of tracks successfully refreshed
      not_found  – number of paths not found in the library
      errors     – list of error strings
      available  – False when not running on macOS
    """
    if not _macos():
        return {"available": False, "refreshed": 0, "not_found": 0, "errors": []}

    refreshed = not_found = 0
    errors: list[str] = []

    for path in paths:
        posix = str(path.expanduser().resolve())
        # Ask Apple Music to locate the track by its on-disk path then refresh it.
        script = f'''
tell application "Music"
    set theLoc to (POSIX file "{posix}") as alias
    set theTracks to (every file track of library playlist 1 whose location is theLoc)
    if length of theTracks > 0 then
        refresh (item 1 of theTracks)
        return "ok"
    else
        return "not found"
    end if
end tell
'''
        try:
            out, err = _run_script(script)
            if out == "ok":
                refreshed += 1
            else:
                not_found += 1
            if err:
                errors.append(f"{path.name}: {err}")
        except subprocess.TimeoutExpired:
            errors.append(f"{path.name}: timed out")
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")

    return {
        "available": True,
        "refreshed": refreshed,
        "not_found": not_found,
        "errors": errors,
    }


def refresh_library() -> dict:
    """Tell Apple Music to refresh every track in the library.

    This is slower than refresh_paths() but catches everything.
    Returns a dict with keys: available, ok (bool), error (str or None).
    """
    if not _macos():
        return {"available": False, "ok": False, "error": "not macOS"}

    script = '''
tell application "Music"
    set theLib to library playlist 1
    repeat with t in (get every file track of theLib)
        try
            refresh t
        end try
    end repeat
    return count of every file track of theLib
end tell
'''
    try:
        out, err = _run_script(script)
        return {
            "available": True,
            "ok": True,
            "track_count": int(out) if out.isdigit() else None,
            "error": err or None,
        }
    except subprocess.TimeoutExpired:
        return {"available": True, "ok": False, "error": "timed out after 60 s"}
    except Exception as exc:
        return {"available": True, "ok": False, "error": str(exc)}


def is_available() -> bool:
    """Return True if Apple Music refresh is available (macOS with Music.app)."""
    if not _macos():
        return False
    try:
        out, _ = _run_script('tell application "Music" to return name of current playlist')
        return True
    except Exception:
        return False
