"""Which camera addresses the server may pull from (SSRF). Hosts in a private network (10.x, 172.16.x, 192.168.x and
other non public ranges), this machine, link local addresses (the cloud's metadata service among them) and IPv6
unique local addresses are refused, and so are names that resolve to any of them. Cameras behind a society's router
reach the server through the bridge or a device link, which push frames, so they never need an exception.
ALLOW_PRIVATE_STREAMS turns the check off for a server that itself runs inside the society's network.

A name is checked when the camera is added and again before each connection (ingest/sources.py), so a name that
later resolves to a private address is refused too. Local files are a separate question: `inside` keeps the server's
own clips (uploads, the demo samples) to their folders."""
import ipaddress
import socket
from collections.abc import Callable
from functools import partial
from pathlib import Path
from urllib.parse import urlsplit

SCHEMES = ("rtsp", "rtsps", "http", "https")
DEFAULT_PORTS = {"rtsp": 554, "rtsps": 322, "http": 80, "https": 443}
ODD = set(" \\\t\r\n\x00")  # characters other URL parsers read differently

Resolver = Callable[[str, int], list[str]]

NOT_A_STREAM = "Paste an RTSP or HTTP stream address."
PRIVATE = ("That address is inside a private network, so the server can't reach it. Cameras behind the society's "
           "router connect through the bridge instead.")


def _resolve(host: str, port: int) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


def public(addr: str) -> bool:
    """True only for an address on the public internet."""
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def check_url(url: str, resolve: Resolver | None = None, allow_private: bool = False) -> str | None:
    """None when the server may pull this address, else the reason in plain words. A name that does not resolve
    passes here (the camera simply stays unreachable); it is checked again before every connection."""
    if not isinstance(url, str) or ODD & set(url):
        return NOT_A_STREAM
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return NOT_A_STREAM
    if parts.scheme.lower() not in SCHEMES or not parts.hostname:
        return NOT_A_STREAM
    if allow_private:
        return None
    host = parts.hostname
    try:
        addrs = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            addrs = (resolve or _resolve)(host, port or DEFAULT_PORTS[parts.scheme.lower()])
        except (OSError, UnicodeError):
            return None
    if not addrs or not all(public(a) for a in addrs):
        return PRIVATE
    return None


def guard(allow_private: bool = False, resolve: Resolver | None = None) -> Callable[[str], str | None]:
    """check_url with the server's setting, for a stream source to call before each connection."""
    return partial(check_url, resolve=resolve, allow_private=allow_private)


def inside(path: str | Path, roots: list[Path]) -> bool:
    """True when a local file is one of the server's own clips: inside one of these folders, links resolved."""
    try:
        real = Path(path).resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    return any(real.is_relative_to(Path(r).resolve()) for r in roots)


def sample_roots(data_dir: str | Path) -> list[Path]:
    """Where the clips demo cameras replay live (scripts/seed_demo.py refuses any other)."""
    return [Path(data_dir) / "samples", Path("data") / "samples"]


def clip_roots(data_dir: str | Path) -> list[Path]:
    """Where the server's own clips live: uploads, and the demo samples."""
    return [Path(data_dir) / "uploads", *sample_roots(data_dir)]

