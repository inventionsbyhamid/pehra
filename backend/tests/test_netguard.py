"""Camera addresses the server pulls are refused when they point inside a private network, at this machine or at
link local addresses, names that resolve there included (SSRF, Watchful m3); NVR addresses come from templates."""
import ipaddress
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

from watchful.ingest import nvr
from watchful.ingest.sources import StreamSource
from watchful.netguard import NOT_A_STREAM, PRIVATE, check_url, guard, inside


def resolver(table: dict[str, list[str]]):
    def resolve(host, port):
        if host not in table:
            raise socket.gaierror(socket.EAI_NONAME, "unknown")
        return table[host]
    return resolve


NAMES = resolver({"nvr.example.com": ["93.184.215.14"], "inside.example.com": ["192.168.1.64"],
                  "mixed.example.com": ["93.184.215.14", "10.0.0.5"], "meta.example.com": ["169.254.169.254"],
                  "v6.example.com": ["fd12:3456::1"], "2130706433": ["127.0.0.1"]})


def test_private_hosts_are_refused():
    refused = ["rtsp://10.1.2.3/stream", "rtsp://192.168.1.64:554/Streaming/Channels/102", "rtsp://127.0.0.1/x",
               "rtsp://169.254.169.254/latest/meta-data", "rtsp://[::1]:8000/", "rtsp://[fc00::1]/x",
               "rtsp://[fd12:3456::1]/x", "rtsp://[fe80::1]/x", "rtsp://172.16.0.9/x", "rtsp://100.64.0.1/x",
               "rtsp://0.0.0.0/x", "rtsp://[::ffff:10.0.0.1]/x", "rtsp://admin:pw@inside.example.com/x",
               "rtsp://meta.example.com/x", "rtsp://v6.example.com/x", "rtsp://mixed.example.com/x",
               "rtsp://2130706433/x", "rtsps://inside.example.com/video"]
    for url in refused:
        assert check_url(url, resolve=NAMES) == PRIVATE, url


def test_public_hosts_and_unknown_names_pass():
    for url in ("rtsp://93.184.215.14/x", "rtsp://admin:pw@nvr.example.com:554/cam/realmonitor?channel=1&subtype=1",
                "rtsps://nvr.example.com/live"):
        assert check_url(url, resolve=NAMES) is None, url
    # a name that does not resolve now cannot be reached either; it is checked again before every connection
    assert check_url("rtsp://nowhere.example.com/x", resolve=NAMES) is None


def test_only_stream_addresses_are_accepted():
    for url in ("file:///etc/passwd", "ftp://93.184.215.14/x", "/var/data/x.mp4", "rtsp://", "rtsp://a b/x",
                "rtsp://93.184.215.14\\@127.0.0.1/x", "rtsp://93.184.215.14:99999/x", "", None):
        assert check_url(url, resolve=NAMES) == NOT_A_STREAM, url


def test_a_server_inside_the_society_may_pull_private_addresses():
    assert check_url("rtsp://192.168.1.64/x", resolve=NAMES, allow_private=True) is None
    assert check_url("file:///etc/passwd", allow_private=True) == NOT_A_STREAM


def test_a_name_that_turns_private_is_refused_when_connecting():
    table = {"cam.example.com": ["93.184.215.14"]}
    assert check_url("rtsp://cam.example.com/x", resolve=resolver(table)) is None
    table["cam.example.com"] = ["169.254.169.254"]  # DNS now answers with the metadata address
    src = StreamSource("rtsp://cam.example.com/x", fps=5, guard=guard(resolve=resolver(table)))
    try:
        deadline = time.time() + 3
        while src.status != "refused" and time.time() < deadline:
            time.sleep(0.02)
        assert src.status == "refused" and src.read(timeout=0.1) is None
    finally:
        src.close()


def test_local_clips_stay_in_their_folders(tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "a.mp4").write_bytes(b"x")
    (tmp_path / "secret.jpg").write_bytes(b"x")
    (uploads / "link.mp4").symlink_to(tmp_path / "secret.jpg")
    assert inside(uploads / "a.mp4", [uploads])
    for path in (tmp_path / "secret.jpg", uploads / ".." / "secret.jpg", uploads / "link.mp4", "/etc/passwd"):
        assert not inside(path, [uploads]), path


def test_nvr_templates_build_substream_addresses():
    assert nvr.build_url("hikvision", "192.168.1.64", 1, "admin", "pw") == \
        "rtsp://admin:pw@192.168.1.64:554/Streaming/Channels/102"
    assert nvr.build_url("hikvision", "192.168.1.64", 17, "admin", "pw").endswith("/Streaming/Channels/1702")
    assert nvr.build_url("hikvision", "192.168.1.64", 2, "admin", "pw", stream="main").endswith("/Channels/201")
    assert nvr.build_url("cp_plus", "192.168.1.108", 3, "admin", "p@ss:w/rd#1") == \
        "rtsp://admin:p%40ss%3Aw%2Frd%231@192.168.1.108:554/cam/realmonitor?channel=3&subtype=1"
    assert nvr.build_url("dahua", "nvr.local", 1, "admin", "pw", port=5543) == \
        "rtsp://admin:pw@nvr.local:5543/cam/realmonitor?channel=1&subtype=1"
    assert nvr.build_url("dahua", "fd00::5", 1).startswith("rtsp://[fd00::5]:554/")
    for bad in ({"brand": "sony"}, {"host": "a/b"}, {"host": "evil.com@127.0.0.1"}, {"channel": 0},
                {"port": 70000}, {"stream": "third"}):
        args = {"brand": "hikvision", "host": "192.168.1.64", "channel": 1, **bad}
        with pytest.raises(ValueError):
            nvr.build_url(**args)
    assert {b["id"] for b in nvr.brands()} == {"hikvision", "cp_plus", "dahua"}


DEPLOY = Path(__file__).resolve().parents[2] / "deploy"


def test_only_rtsp_is_pulled():
    """FFmpeg follows HTTP redirects and loads HLS segments from wherever a playlist points (probed: a 302 and a
    playlist both reached 127.0.0.1), so a pulled camera is RTSP only."""
    for url in ("http://93.184.215.14/live.mjpg", "https://nvr.example.com/live.m3u8", "http://nvr.example.com/x",
                "hls+http://nvr.example.com/x.m3u8", "rtmp://93.184.215.14/live", "srt://93.184.215.14:9000",
                "tcp://93.184.215.14:554", "udp://93.184.215.14:5000", "concat:rtsp://93.184.215.14/x"):
        assert check_url(url, resolve=NAMES) == NOT_A_STREAM, url
        assert check_url(url, resolve=NAMES, allow_private=True) == NOT_A_STREAM, url
    assert check_url("rtsp://93.184.215.14/x", resolve=NAMES) is None
    assert check_url("RTSPS://93.184.215.14/x", resolve=NAMES) is None


def test_ipv6_forms_of_a_private_ipv4_are_refused():
    for host in ("64:ff9b::a9fe:a9fe", "64:ff9b::a00:1", "64:ff9b::7f00:1", "::127.0.0.1", "::a00:1",
                 "2002:a00:1::1", "2002:7f00:1::1", "::ffff:169.254.169.254"):
        assert check_url(f"rtsp://[{host}]/x", resolve=NAMES) == PRIVATE, host
    assert check_url("rtsp://[64:ff9b::808:808]/x", resolve=NAMES) is None  # NAT64 of a public address
    assert check_url("rtsp://nat64.example.com/x", resolve=resolver({"nat64.example.com": ["64:ff9b::a00:5"]})) \
        == PRIVATE


def egress_sets() -> tuple[str, dict[str, list]]:
    text = (DEPLOY / "egress.nft").read_text()
    sets = {}
    for name, body in re.findall(r"set (\w+) \{.*?elements = \{(.*?)\}", text, re.S):
        sets[name] = [ipaddress.ip_network(x.strip()) for x in body.replace("\n", " ").split(",") if x.strip()]
    return text, sets


def test_the_app_user_cannot_reach_private_addresses_on_aws():
    """FFmpeg follows RTSP redirects and has no option to refuse them, so on AWS the app user's new connections to
    this machine and private, link local, CGNAT and ULA addresses are dropped, apart from DNS and the metadata
    service (instance role credentials)."""
    text, sets = egress_sets()
    chain = text[text.index("chain output"):]
    rules = [line.strip() for line in chain.splitlines() if line.strip()]
    assert 'meta skuid != "watchful" accept' in rules and "ct state established,related accept" in rules
    drops = [i for i, r in enumerate(rules) if r.endswith("drop")]
    for allowed in ("meta l4proto { tcp, udp } th dport 53 accept", "ip daddr 169.254.169.254 tcp dport 80 accept"):
        assert allowed in rules and rules.index(allowed) < min(drops), allowed
    assert {r for r in rules if r.endswith("drop")} == {"ip daddr @private4 counter drop",
                                                         "ip6 daddr @private6 counter drop"}
    for addr in ("127.0.0.1", "127.0.0.53", "10.1.2.3", "172.16.0.9", "172.31.0.2", "192.168.1.64", "169.254.169.254",
                 "169.254.170.2", "100.64.0.1", "0.0.0.0"):
        assert any(ipaddress.ip_address(addr) in n for n in sets["private4"]), addr
    for addr in ("::1", "fd00:ec2::254", "fc00::1", "fe80::1", "::ffff:10.0.0.1", "64:ff9b::a9fe:a9fe"):
        assert any(ipaddress.ip_address(addr) in n for n in sets["private6"]), addr
    for addr in ("93.184.215.14", "8.8.8.8", "52.95.110.1"):
        assert not any(ipaddress.ip_address(addr) in n for n in sets["private4"]), addr
    # every address the guard refuses at add time is also dropped at connect time
    for addr in ("10.0.0.1", "127.0.0.1", "169.254.169.254", "100.64.0.1", "fd12:3456::1", "fe80::1", "::1"):
        assert not check_url(f"rtsp://{'[' + addr + ']' if ':' in addr else addr}/x") is None

    setup = (DEPLOY / "remote_setup.sh").read_text()
    unit = (DEPLOY / "egress.service").read_text()
    assert "ExecStart=/usr/sbin/nft -f @APP@/current/deploy/egress.nft" in unit
    assert "Before=watchful-app.service" in unit and "User=" not in unit
    for name in ("watchful-app", "watchful-worker", "watchful-litestream"):  # never started without the rules
        lines = (DEPLOY / "systemd" / f"{name}.service").read_text().splitlines()
        after = next(line for line in lines if line.startswith("After="))
        assert "Requires=watchful-egress.service" in lines and "watchful-egress.service" in after.split(), name
    assert re.search(r"apt-get install .*nftables", setup)
    assert "deploy/egress.service\" > /etc/systemd/system/watchful-egress.service" in setup
    order = [setup.index(x) for x in ("systemctl enable caddy watchful-egress", "systemctl restart watchful-egress",
                                      "nft list table inet watchful_egress",
                                      "systemctl restart watchful-app watchful-worker watchful-litestream")]
    assert order == sorted(order)
    user = re.search(r"useradd --system .* (\w+)$", setup, re.M).group(1)
    assert user == "watchful" and "User=watchful" in (DEPLOY / "systemd" / "watchful-app.service").read_text()
    stack = (DEPLOY / "cloudformation.yaml").read_text()
    assert "VPCEndpoint" not in stack  # AWS APIs are reached on public endpoints, which the rules leave open


@pytest.mark.skipif(shutil.which("nft") is None, reason="nft is not installed here (the instance checks the rules)")
def test_the_egress_rules_parse():
    result = subprocess.run(["nft", "-c", "-f", str(DEPLOY / "egress.nft")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
