"""iPod Classic music manager."""

from .detector import IpodMount, find_ipod, probe
from .itunesdb import Track, iTunesDB
from .manager import IpodManager

__all__ = ["IpodMount", "IpodManager", "Track", "iTunesDB", "find_ipod", "probe"]
