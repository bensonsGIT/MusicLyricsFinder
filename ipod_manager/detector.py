"""Locate a mounted iPod by scanning common mount points."""

import os
import platform
from pathlib import Path

_STOCK_MARKER = Path("iPod_Control") / "iTunes" / "iTunesDB"
_ROCKBOX_MARKER = Path(".rockbox")
_MUSIC_DIR = Path("iPod_Control") / "Music"


def _candidate_roots() -> list[Path]:
    system = platform.system()
    candidates: list[Path] = []

    if system == "Darwin":
        volumes = Path("/Volumes")
        if volumes.exists():
            candidates.extend(volumes.iterdir())
    elif system == "Linux":
        uid = os.getuid()
        for base in [
            Path(f"/run/media/{os.environ.get('USER', '')}"),
            Path(f"/media/{os.environ.get('USER', '')}"),
            Path("/media"),
            Path("/mnt"),
        ]:
            if base.exists():
                try:
                    candidates.extend(base.iterdir())
                except PermissionError:
                    pass
    elif system == "Windows":
        import string
        for letter in string.ascii_uppercase:
            candidates.append(Path(f"{letter}:\\"))

    return candidates


class IpodMount:
    def __init__(self, root: Path, rockbox: bool):
        self.root = root
        self.rockbox = rockbox

    @property
    def itunesdb_path(self) -> Path:
        return self.root / _STOCK_MARKER

    @property
    def music_dir(self) -> Path:
        return self.root / _MUSIC_DIR

    def __repr__(self) -> str:
        mode = "Rockbox" if self.rockbox else "stock"
        return f"IpodMount({self.root}, mode={mode})"


def find_ipod(hint: str = "") -> IpodMount | None:
    """Return the first iPod mount found, or None."""
    if hint:
        return probe(Path(hint))
    for root in _candidate_roots():
        result = probe(root)
        if result:
            return result
    return None


def probe(root: Path) -> IpodMount | None:
    """Return an IpodMount if *root* looks like an iPod, else None."""
    if not root.is_dir():
        return None
    rockbox = (root / _ROCKBOX_MARKER).is_dir()
    stock = (root / _STOCK_MARKER).is_file()
    if rockbox or stock:
        return IpodMount(root, rockbox=rockbox)
    return None
