"""Which camera addresses the server may pull from (SSRF). Only RTSP (rtsp, rtsps): FFmpeg follows HTTP redirects
and loads HLS segments from wherever a playlist points, so HTTP, HLS and MJPEG over HTTP are refused (a phone's
MJPEG address is on its own network anyway; phones use the device link). Hosts in a private network (10.x,
172.16.x, 192.168.x and other non public ranges), this machine, link local addresses (the cloud's metadata service
among them), IPv6 unique local addresses and IPv6 forms that carry such an IPv4 address (NAT64 64:ff9b::/96, IPv4
compatible ::/96, 6to4, Teredo) are refused, and so are names that resolve to any of them. Cameras behind a society's
router reach the server through the bridge or a device link, which push frames, so they never need an exception.
ALLOW_PRIVATE_STREAMS turns the check off for a server that itself runs inside the society's network.

A name is checked when the camera is added and again before each connection (ingest/sources.py), so a name that
later resolves to a private address is refused too.

What remains: an RTSP server may answer 3xx, and FFmpeg follows RTSP redirects with no option to refuse them, so a
public camera address can still send FFmpeg's RTSP requests (OPTIONS, DESCRIBE) to a private or loopback address.
They are blind (the server never shows what came back) and RTSP cannot send an HTTP PUT, so IMDSv2 tokens cannot be
fetched. On AWS the instance drops the app user's new connections to private, loopback, link local, CGNAT and ULA
addresses (deploy/egress.nft), apart from DNS and the metadata service; on ioflood there is no such rule, so a
camera address there can still make the dev container send blind RTSP requests to its own network.

Local files are a separate question: `inside` keeps the server's own clips (uploads, the demo samples) to their
folders."""
import ipaddress
import socket
from collections.abc import Callable
from functools import partial
from pathlib import Path
from urllib.parse import urlsplit

SCHEMES = ("rtsp", "rtsps")
DEFAULT_PORTS = {"rtsp": 554, "rtsps": 322}
ODD = set(" \\\t\r\n\x00")  # characters other URL parsers read differently

Resolver = Callable[[str, int], list[str]]

NOT_A_STREAM = "Paste the camera's RTSP address. It starts with rtsp://"
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
    if ip.version == 6:
        inner = embedded_ipv4(ip)
        if inner is not None and not public(str(inner)):
            return False
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


NAT64 = ipaddress.ip_network("64:ff9b::/96")
COMPAT = ipaddress.ip_network("::/96")


def embedded_ipv4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """The IPv4 address an IPv6 address leads to, where it carries one."""
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip in NAT64 or (ip in COMPAT and int(ip) > 1):
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if ip.sixtofour is not None:
        return ip.sixtofour
    if ip.teredo is not None:
        return ip.teredo[1]
    return None


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

