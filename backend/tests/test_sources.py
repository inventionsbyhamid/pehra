import time

import cv2
import numpy as np

from watchful.ingest.sources import FileSource, PushSource, StreamSource
from watchful.vision.models import sample_path

VTEST = sample_path("vtest.avi")


def test_file_source_samples_rate():
    src = FileSource(VTEST, fps=5, start_epoch=1000.0)
    frames = [src.read() for _ in range(10)]
    steps = np.diff([f.ts for f in frames])
    assert all(abs(s - 0.2) <= 0.05 for s in steps)
    assert frames[0].ts >= 1000.0


def test_file_source_never_upscales():
    src = FileSource(VTEST, fps=5, width=960)
    assert src.read().image.shape[1] == 768
    small = FileSource(VTEST, fps=5, width=320)
    assert small.read().image.shape[1] == 320


def test_file_source_ends():
    src = FileSource(VTEST, fps=1)
    count = 0
    while src.read() is not None:
        count += 1
    assert count > 10 and src.status == "ended"


def test_file_source_loop():
    src = FileSource(VTEST, fps=2, loop=True)
    ts = [src.read().ts for _ in range(250)]
    assert all(b > a for a, b in zip(ts, ts[1:]))
    assert src.status == "live"


def test_stream_source_reconnects():
    src = StreamSource("rtsp://127.0.0.1:1/none", fps=5)
    try:
        deadline = time.time() + 5
        while src.status != "reconnecting" and time.time() < deadline:
            time.sleep(0.05)
        assert src.status == "reconnecting"
        assert src.read(timeout=0.3) is None
    finally:
        src.close()


def test_push_source_keeps_latest():
    src = PushSource(fps=5)
    for value in (10, 120, 250):
        img = np.full((48, 64, 3), value, np.uint8)
        src.push(cv2.imencode(".jpg", img)[1].tobytes(), ts=float(value))
    frame = src.read(timeout=0.1)
    assert frame.ts == 250.0
    assert abs(int(frame.image.mean()) - 250) <= 3
    assert src.read(timeout=0.05) is None
