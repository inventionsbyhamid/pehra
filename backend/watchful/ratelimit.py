"""Rate limits, and the client address they key on.

RateLimiter counts hits per key in a sliding window, in one process: sign in attempts and link pages per address
and API requests per society (`Limiters`, one set per app). The values are Tuning limits.*.

`client_ip` is the one answer to who is asking: Cloudflare's CF-Connecting-IP only when TRUST_CLOUDFLARE is on (the
ioflood tunnel, where every request comes through Cloudflare), else the peer address, or the last X-Forwarded-For hop
when the peer is the local Caddy (a loopback address). `address_key` keys an IPv6 address by its /64, which one
visitor's devices usually share."""
import ipaddress
import logging
import math
import threading
import time
from collections import deque
from collections.abc import Callable

from starlette.requests import HTTPConnection

log = logging.getLogger(__name__)


class TooManyTries(Exception):
    def __init__(self, retry_after_s: int):
        super().__init__(f"too many tries; wait {retry_after_s} s")
        self.retry_after_s = retry_after_s


class _Hit:
    __slots__ = ("at",)

    def __init__(self, at: float):
        self.at = at


class RateLimiter:
    """At most `limit` hits per key in any `window_s`. `reserve` counts a hit under the lock before the caller acts,
    so a burst of parallel requests is counted in full, and raises TooManyTries once the key has used them all;
    `release` gives one back (a right PIN, a refused request). Keys with no hit in the last window are dropped, at
    most once a window, so memory follows the keys seen lately."""

    def __init__(self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic):
        self.limit, self.window_s, self.clock = limit, window_s, clock
        self._hits: dict[str, deque[_Hit]] = {}
        self._lock = threading.Lock()
        self._swept = clock()

    def _recent(self, key: str, now: float) -> deque[_Hit]:
        held = self._hits.setdefault(key, deque())
        while held and now - held[0].at >= self.window_s:
            held.popleft()
        return held

    def _sweep(self, now: float) -> None:
        if now - self._swept < self.window_s:
            return
        self._swept = now
        for key in [k for k, held in self._hits.items() if not held or now - held[-1].at >= self.window_s]:
            del self._hits[key]

    def reserve(self, key: str) -> tuple[str, _Hit]:
        with self._lock:
            now = self.clock()
            self._sweep(now)
            held = self._recent(key, now)
            if len(held) >= self.limit:
                raise TooManyTries(max(1, math.ceil(self.window_s - (now - held[0].at))))
            hit = _Hit(now)
            held.append(hit)
            return key, hit

    def release(self, handle: tuple[str, _Hit]) -> None:
        key, hit = handle
        with self._lock:
            held = self._hits.get(key)
            if held is not None and hit in held:
                held.remove(hit)
            if held is not None and not held:
                del self._hits[key]

    def allow(self, key: str) -> bool:
        """Counts a hit when one is left."""
        try:
            self.reserve(key)
        except TooManyTries:
            return False
        return True

    def keys(self) -> int:
        return len(self._hits)


class Limiters:
    """The per app limits on HTTP requests (api/account.py, auth/deps.py, api/demo.py)."""

    def __init__(self, limits):
        self.sign_in = RateLimiter(limits.login_per_min_ip, 60.0)  # Google and operator links, per address
        self.api = RateLimiter(limits.api_per_min_workspace, 60.0)  # signed in API requests, per society
        self.public = RateLimiter(limits.public_per_min_ip, 60.0)  # pages behind a link that exists, per address
        self.misses = RateLimiter(limits.misses_per_min_ip, 60.0)  # unknown link tokens: guessing
        self.demo = RateLimiter(limits.demo_logins_per_ip, limits.demo_login_window_s)  # new sandboxes, per address
        self.demo_48 = RateLimiter(limits.demo_logins_per_48, limits.demo_login_window_s)  # and per IPv6 /48


def address_key(ip: str) -> str:
    """An IPv4 address as it is; an IPv6 one by its /64."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.version == 6:
        if addr.ipv4_mapped:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


def wide_key(ip: str | None) -> str | None:
    """An IPv6 address's /48, which a provider may give one home or office; None for IPv4."""
    try:
        addr = ipaddress.ip_address(ip or "")
    except ValueError:
        return None
    if addr.version != 6 or addr.ipv4_mapped:
        return None
    return str(ipaddress.ip_network(f"{addr}/48", strict=False))


def _loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


def _tunnel_like(ip: str) -> bool:
    """A private address that is not this host: what a Cloudflare tunnel container is to the app."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_private and not addr.is_loopback


_untrusted_warning = RateLimiter(1, 3600.0)  # the warning below at most once an hour


def client_ip(conn: HTTPConnection, trust_cloudflare: bool) -> str | None:
    """Who is asking (see the module notes); None when the address is unknown."""
    cf = conn.headers.get("cf-connecting-ip", "").strip()
    if trust_cloudflare and cf:
        return cf
    peer = conn.client.host if conn.client else None
    if cf and peer and _tunnel_like(peer) and _untrusted_warning.allow("cf"):
        log.warning("CF-Connecting-IP came from %s, a private address, while TRUST_CLOUDFLARE is off: behind the "
                    "Cloudflare tunnel every visitor now shares that address for rate limits", peer)
    if peer and _loopback(peer) and (hops := conn.headers.get("x-forwarded-for")):
        return hops.split(",")[-1].strip() or peer
    return peer


def client_key(conn: HTTPConnection, trust_cloudflare: bool) -> str:
    """The address a limit counts against."""
    ip = client_ip(conn, trust_cloudflare)
    return address_key(ip) if ip else "unknown"
