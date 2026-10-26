import hashlib
import importlib.util
import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest

from watchful.vision.models import StockClip

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "fetch_assets.py"
pytestmark = pytest.mark.skipif(not (shutil.which("curl") and shutil.which("ffmpeg")), reason="needs curl and ffmpeg")


def fetch_assets():
    spec = importlib.util.spec_from_file_location("fetch_assets", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stock(path: Path) -> Path:
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 120))
    for i in range(20):
        frame = np.full((120, 160, 3), 40, np.uint8)
        cv2.circle(frame, (20 + i * 6, 60), 10, (255, 255, 255), -1)
        out.write(frame)
    out.release()
    return path


def test_a_stock_clip_is_checked_then_cut_to_the_cameras_view(tmp_path):
    source = stock(tmp_path / "stock.mp4")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    clip = StockClip(source.as_uri(), digest, "page", "someone", "licence", "crop=iw/2:ih:iw/2:0,scale=64:48")
    data = tmp_path / "data"
    assert fetch_assets().fetch_clip("demo/pool.mp4", clip, data)
    cap = cv2.VideoCapture(str(data / "samples" / "demo" / "pool.mp4"))
    assert (cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) == (64, 48)
    assert sorted(p.name for p in (data / "samples" / "demo").iterdir()) == ["pool.mp4"]  # the stock file is not kept


def test_a_stock_clip_that_changed_is_refused(tmp_path):
    source = stock(tmp_path / "stock.mp4")
    clip = StockClip(source.as_uri(), "0" * 64, "page", "someone", "licence", "scale=64:48")
    data = tmp_path / "data"
    assert not fetch_assets().fetch_clip("demo/pool.mp4", clip, data)
    assert list((data / "samples" / "demo").iterdir()) == []
