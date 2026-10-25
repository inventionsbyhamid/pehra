"""Camera addresses the server pulls are refused when they point inside a private network, at this machine or at
link local addresses, names that resolve there included (SSRF, Watchful m3); NVR addresses come from templates."""
import socket
import time

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
               "rtsp://169.254.169.254/latest/meta-data", "http://[::1]:8000/", "rtsp://[fc00::1]/x",
               "rtsp://[fd12:3456::1]/x", "rtsp://[fe80::1]/x", "rtsp://172.16.0.9/x", "rtsp://100.64.0.1/x",
               "rtsp://0.0.0.0/x", "rtsp://[::ffff:10.0.0.1]/x", "rtsp://admin:pw@inside.example.com/x",
               "rtsp://meta.example.com/x", "rtsp://v6.example.com/x", "rtsp://mixed.example.com/x",
               "rtsp://2130706433/x", "https://inside.example.com/video.mjpg"]
    for url in refused:
        assert check_url(url, resolve=NAMES) == PRIVATE, url


def test_public_hosts_and_unknown_names_pass():
    for url in ("rtsp://93.184.215.14/x", "rtsp://admin:pw@nvr.example.com:554/cam/realmonitor?channel=1&subtype=1",
                "https://nvr.example.com/live.mjpg"):
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
