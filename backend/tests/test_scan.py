import subprocess
import time

import pytest

from watchful.config import Settings
from watchful.ingest.sources import FileSource
from watchful.rules.presets import preset
from watchful.store.blobs import LocalBlobStore
from watchful.store.db import Camera, SqliteRepository
from watchful.tuning import Tuning
from watchful.vision.faces import FaceIndex
from watchful.vision.perceive import Needs, Observation, Perception
from watchful.vision.pipeline import CameraPipeline
from watchful.vision.models import sample_path
from watchful.vision.scan import ChunkResult, ScanPool, chunk_plan, scan_file, stitch
from watchful.vision.zones import Zone

LEFT = Zone("z1", "left", [(0, 0), (0.5, 0), (0.5, 1), (0, 1)])


@pytest.fixture(scope="module")
def settings():
    return Settings(demo_password="x", session_secret="y", _env_file=None)


@pytest.fixture(scope="module")
def clip40():
    out = sample_path("vtest40.mp4")
    if not out.exists():
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(sample_path("vtest.avi")), "-t", "40",
                        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


def test_long_videos_get_more_chunks_than_workers():
    plan = chunk_plan(600.0, workers=6, overlap_s=2.0, min_chunk_s=4.0, target_chunk_s=15.0)
    assert len(plan) == 40 and plan[-1][1] == 600.0


def test_chunk_plan_overlap():
    plan = chunk_plan(60.0, workers=6, overlap_s=1.0, min_chunk_s=4.0)
    assert len(plan) == 6
    assert plan[0] == (0.0, 10.0)
    assert all(abs(s - (10 * i - 1)) < 1e-6 and abs(e - 10 * (i + 1)) < 1e-6 for i, (s, e) in enumerate(plan) if i)


def obs(ts, tid, x, started=False, ended=False):
    return Observation(ts, tid, "person", (float(x), 100.0, 40.0, 90.0), 0.9, started=started, ended=ended)


def frame(t, observations):
    return Perception(t, (640, 480), True, observations, pos=t)


def test_stitch_joins_track_across_boundary():
    # one person walking right; chunk 0 covers 0..10 s, chunk 1 covers 9..20 s; each chunk ends its open tracks
    a = [frame(t, [obs(t, 1, 10 * t, started=(t == 0))]) for t in [i * 0.5 for i in range(21)]]
    a.append(frame(10.0, [obs(10.0, 1, 100, ended=True)]))
    b = [frame(t, [obs(t, 5, 10 * t, started=(t == 9.0))]) for t in [9 + i * 0.5 for i in range(23)]]
    b.append(frame(20.0, [obs(20.0, 5, 200, ended=True)]))
    merged = stitch([ChunkResult(0, 0.0, 10.0, a), ChunkResult(1, 9.0, 20.0, b)])
    ids = {o.track_id for p in merged for o in p.observations}
    assert len(ids) == 1
    flags = [(o.started, o.ended) for p in merged for o in p.observations]
    assert sum(s for s, _ in flags) == 1 and sum(e for _, e in flags) == 1
    assert [p.ts for p in merged] == sorted(p.ts for p in merged)


def test_scan_matches_single_pass(settings, clip40, tmp_path):
    cam = Camera("cam1", "Plaza", "file", None, [LEFT])
    blobs = LocalBlobStore(tmp_path)
    repo = SqliteRepository(tmp_path / "w.db")
    pipe = CameraPipeline(cam, repo, blobs, FaceIndex(), settings, Tuning(), Needs(classes={"person"}))
    src = FileSource(clip40, fps=settings.analysis_fps, start_epoch=0)
    last = None
    while (f := src.read()) is not None:
        pipe.process(f)
        last = f
    pipe.finish(last.ts, last.pos)
    single = [e for e in repo.query_events(camera_id="cam1", kinds=["enter", "exit"], limit=5000)]

    with ScanPool(settings, Tuning(), workers=4) as pool:
        result = scan_file(clip40, cam, [], pool, blobs, settings, Tuning(), start_epoch=0)
    scanned = [e for e in result.events if e.kind in ("enter", "exit")]
    assert abs(len(scanned) - len(single)) <= max(2, 0.1 * len(single))
    matched = sum(any(s.kind == e.kind and abs(s.ts - e.ts) <= 0.4 for s in scanned) for e in single)
    assert matched >= 0.9 * len(single)
    assert result.stats.workers == 4 and result.stats.x_realtime > 0


@pytest.mark.slow
def test_scan_speed_1080p(settings, tmp_path):
    clip = sample_path("speed_1080p.mp4")
    if not clip.exists():
        subprocess.run(["bash", "eval/make_speed_clip.sh"], check=True)
    cam = Camera("cam1", "Plaza", "file", None, [LEFT])
    watches = [("w1", preset("enters", zone="left")), ("w2", preset("fall"))]
    with ScanPool(settings, Tuning()) as pool:
        pool.warm()
        result = scan_file(clip, cam, watches, pool, LocalBlobStore(tmp_path), settings, Tuning(), start_epoch=0)
    print(f"\n1080p minute scanned in {result.stats.wall_s:.1f} s ({result.stats.x_realtime:.1f}x real time), "
          f"stages {result.stats.stages}")
    assert result.stats.wall_s <= 10
