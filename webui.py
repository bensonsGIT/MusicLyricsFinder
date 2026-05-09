#!/usr/bin/env python3
"""Launch the Lyrics Finder web UI."""
import argparse
import sys
import threading
import time
import webbrowser


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="lyrics-finder-ui",
        description="Launch the Lyrics Finder web UI",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5000, help="Port (default: 5000)")
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser automatically")
    args = parser.parse_args(argv)

    try:
        from lyrics_finder.web import app
    except ImportError as e:
        print(f"Missing dependency: {e}\nRun: pip install flask", file=sys.stderr)
        return 1

    url = f"http://{args.host}:{args.port}"
    print(f"  Lyrics Finder UI →  {url}")
    print("  Press Ctrl+C to stop.\n")

    if not args.no_browser:
        def _open():
            time.sleep(1.0)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
