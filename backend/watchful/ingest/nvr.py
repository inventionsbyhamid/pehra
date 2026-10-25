"""Stream addresses built from an NVR brand's template (data/nvr.json): brand, host, channel, user and password in,
an RTSP address out, the substream unless asked otherwise. The address carries the password, so it is stored sealed
(store/sql.py UrlBox) like any camera address."""
import ipaddress
import json
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

DATA = Path(__file__).resolve().parents[1] / "data" / "nvr.json"
HOSTNAME = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}"
                      r"[A-Za-z0-9])?)*$")
MAX_CHANNEL = 256


@lru_cache
def templates() -> dict:
    return json.loads(DATA.read_text())


def brands() -> list[dict]:
    """The brands a form offers: id, label, default port and a hint."""
    return [{"id": key, "label": b["label"], "port": b["port"], "hint": b.get("hint", "")}
            for key, b in templates()["brands"].items()]


def _host(host: str) -> str:
    host = (host or "").strip()
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
        return f"[{ip}]" if ip.version == 6 else str(ip)
    except ValueError:
        pass
    if not HOSTNAME.match(host):
        raise ValueError("Type the recorder's address, for example 192.168.1.64.")
    return host


def build_url(brand: str, host: str, channel: int = 1, user: str = "", password: str = "", port: int | None = None,
              stream: str | None = None) -> str:
    """The RTSP address of one channel; ValueError with a plain reason when a part is not usable."""
    spec = templates()["brands"].get(brand)
    if spec is None:
        raise ValueError("Pick the recorder's brand.")
    stream = stream or templates()["default_stream"]
    if stream not in spec["streams"]:
        raise ValueError("Pick the main stream or the substream.")
    if not 1 <= int(channel) <= MAX_CHANNEL:
        raise ValueError(f"Channels go from 1 to {MAX_CHANNEL}.")
    port = spec["port"] if port is None else int(port)
    if not 1 <= port <= 65535:
        raise ValueError("Ports go from 1 to 65535.")
    auth = f"{quote(user, safe='')}:{quote(password, safe='')}@" if user else ""
    return spec["url"].format(auth=auth, host=_host(host), port=port, channel=int(channel),
                              stream=spec["streams"][stream])
