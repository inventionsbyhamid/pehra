"""Fast scans of recordings: perceive time chunks in parallel, stitch tracks, reason once in order."""
import json
import math
import multiprocessing
import os
import subprocess
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from watchful.config import Settings, available_cpus
from watchful.ingest.sources import Frame, fit_width
from watchful.rules.engine import Candidate, RuleEngine, needs_for
from watchful.rules.watch import Watch
from watchful.components import make_blobs, make_detector, make_faces, make_pose
from watchful.store.blobs import BlobStore
from watchful.store.db import Camera, EventRow
from watchful.tuning import ScanTuning, Tuning
from watchful.vision.faces import Enrollment
from watchful.vision.perceive import Needs, Perceiver, Perception
from watchful.vision.reason import Reasoner
from watchful.vision.track import iou

TRACK_ID_SPAN = 1_000_000  # keeps chunk local track ids apart before stitching
EPS = 1e-6


@dataclass
class ChunkResult:
    index: int
    start: float
    end: float
    perceptions: list[Perception]
    stats: dict = field(default_factory=dict)


@dataclass
class ScanStats:
    duration_s: float
    wall_s: float
    x_realtime: float
    frames_analyzed: int
    detector_runs: int
    workers: int
    stages: dict[str, float]


@dataclass
class ScanResult:
    events: list[EventRow]
    candidates: list[Candidate]
    stats: ScanStats
    paths: dict[int, list] = field(default_factory=dict)  # track to (ts, box), for the verifier's evidence
    poses: dict[int, list] = field(default_factory=dict)


def chunk_plan(duration_s: float, workers: int, overlap_s: float, min_chunk_s: float,
               target_chunk_s: float = 15.0) -> list[tuple[float, float]]:
    """At least one chunk per worker; long videos get chunks of about target_chunk_s so idle workers keep busy."""
    k = max(1, min(workers, math.ceil(duration_s / min_chunk_s)), math.ceil(duration_s / target_chunk_s))
    length = duration_s / k
    return [(max(0.0, i * length - (overlap_s if i else 0.0)), (i + 1) * length) for i in range(k)]


# per worker process state, set up once by the pool initializer
_W: dict = {}


def _init(settings_json: str, tuning_json: str) -> None:
    cv2.setNumThreads(1)
    settings = Settings(**json.loads(settings_json), _env_file=None)
    tuning = Tuning.model_validate_json(tuning_json)
    _W.update(settings=settings, tuning=tuning, pose=None, faces=None,
              detector=make_detector(settings, tuning))


def _ping(_: int) -> int:
    time.sleep(0.2)
    return os.getpid()


@dataclass
class _Job:
    index: int
    path: str
    start: float
    end: float
    camera: Camera
    needs: Needs
    enrollments: list[Enrollment]
    start_epoch: float


def _run_chunk(job: _Job) -> ChunkResult:
    s, t = _W["settings"], _W["tuning"]
    if (job.needs.pose or job.needs.posture) and _W["pose"] is None:
        _W["pose"] = make_pose(s, t)
    faces = None
    if job.needs.faces and job.enrollments:
        if _W["faces"] is None:
            _W["faces"] = make_faces(s, t)
        _W["faces"].load(job.enrollments)
        faces = _W["faces"]
    perceiver = Perceiver(job.camera, s, t, make_blobs(s), faces, job.needs,
                          detector=_W["detector"], pose=_W["pose"])
    perceiver.tracker._next_id = (job.index + 1) * TRACK_ID_SPAN
    cap = cv2.VideoCapture(job.path, cv2.CAP_FFMPEG, [cv2.CAP_PROP_N_THREADS, t.scan.decode_threads])
    cap.set(cv2.CAP_PROP_POS_MSEC, job.start * 1000)
    step = 1.0 / s.analysis_fps
    next_t = math.ceil(job.start / step - EPS) * step
    out: list[Perception] = []
    size, index, grid = (0, 0), 0, job.start
    while cap.grab():
        pos = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
        if pos > job.end + EPS:
            break
        if pos + EPS < next_t:
            continue
        ok, image = cap.retrieve()
        if not ok:
            continue
        grid = round(pos / step) * step
        next_t = grid + step
        index += 1
        image = fit_width(image, s.analysis_width)
        size = (image.shape[1], image.shape[0])
        out.append(perceiver.perceive(Frame(image, job.start_epoch + grid, index, grid)))
    cap.release()
    out.append(perceiver.finish(job.start_epoch + grid, grid, size))
    return ChunkResult(job.index, job.start, job.end, out, perceiver.stats())


class ScanPool:
    """Worker processes with models loaded once; create at app start and reuse."""

    def __init__(self, settings: Settings, tuning: Tuning, workers: int | None = None):
        self.workers = workers or settings.scan_workers or available_cpus()
        self.settings, self.tuning = settings, tuning
        self.ex = ProcessPoolExecutor(self.workers, mp_context=multiprocessing.get_context("spawn"),
                                      initializer=_init,
                                      initargs=(settings.model_dump_json(), tuning.model_dump_json()))

    def warm(self) -> None:
        list(self.ex.map(_ping, range(self.workers * 2)))

    def close(self) -> None:
        self.ex.shutdown(cancel_futures=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _boxes_in(perceptions: list[Perception], lo: float, hi: float) -> dict[int, dict[float, tuple]]:
    out: dict[int, dict[float, tuple]] = {}
    for p in perceptions:
        if p.pos is not None and lo - EPS <= p.pos <= hi + EPS:
            for o in p.observations:
                if not o.ended:
                    out.setdefault(o.track_id, {})[round(p.pos, 3)] = o.box
    return out


def _close(a: tuple, b: tuple, max_distance: float) -> bool:
    ah, bh = max(a[3], 1.0), max(b[3], 1.0)
    dx = (a[0] + a[2] / 2) - (b[0] + b[2] / 2)
    dy = (a[1] + a[3] / 2) - (b[1] + b[3] / 2)
    return (dx * dx + dy * dy) ** 0.5 / ah <= max_distance and 0.67 <= bh / ah <= 1.5


def _join(prev: ChunkResult, cur: ChunkResult, cfg: ScanTuning) -> dict[int, int]:
    """Tracks of cur that continue tracks of prev, by box overlap in the shared seconds."""
    lo, hi = cur.start, prev.end
    a_boxes, b_boxes = _boxes_in(prev.perceptions, lo, hi), _boxes_in(cur.perceptions, lo, hi)
    pairs = []
    for a, ab in a_boxes.items():
        for b, bb in b_boxes.items():
            shared = ab.keys() & bb.keys()
            if shared:
                v = sum(iou(ab[k], bb[k]) for k in shared) / len(shared)
                if v >= cfg.join_iou:
                    pairs.append((v, a, b))
    joined: dict[int, int] = {}
    for _, a, b in sorted(pairs, reverse=True):
        if a not in joined.values() and b not in joined:
            joined[b] = a
    # fallback for tracks seen apart in time: where prev last saw it, cur first sees it
    for a, ab in a_boxes.items():
        if a in joined.values():
            continue
        a_ts = max(ab)
        best = None
        for b, bb in b_boxes.items():
            if b in joined:
                continue
            b_ts = min(bb)
            if abs(b_ts - a_ts) <= cfg.join_gap_s and _close(ab[a_ts], bb[b_ts], cfg.join_distance):
                d = abs(b_ts - a_ts)
                if best is None or d < best[0]:
                    best = (d, b)
        if best:
            joined[best[1]] = a
    return joined


def stitch(chunks: list[ChunkResult], cfg: ScanTuning = ScanTuning()) -> list[Perception]:
    """Join tracks that cross chunk borders, then merge chunks into one ordered stream."""
    chunks = sorted(chunks, key=lambda c: c.index)
    merged: list[Perception] = list(chunks[0].perceptions) if chunks else []
    alias: dict[int, int] = {}
    for prev, cur in zip(chunks, chunks[1:]):
        hi = prev.end
        joined = {b: alias.get(a, a) for b, a in _join(prev, cur, cfg).items()}
        continued = set(joined.values())
        last = prev.perceptions[-1] if prev.perceptions else None
        if last is not None:
            last.observations = [o for o in last.observations if not (o.ended and o.track_id in continued)]
        seen: set[int] = set()
        for p in cur.perceptions:
            if p.pos is not None and p.pos <= hi + EPS:
                continue
            for o in p.observations:
                if o.track_id in joined:
                    alias[o.track_id] = joined[o.track_id]
                    o.track_id = joined[o.track_id]
                    o.started = False
                elif o.track_id not in seen and not o.ended:
                    o.started = True
                seen.add(o.track_id)
            merged.append(p)
    merged.sort(key=lambda p: (p.pos if p.pos is not None else p.ts))
    return merged


def probe_duration(path: str | Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def cut_clip(path: str, pos: float, settings: Settings, tuning: Tuning) -> bytes:
    """A short clip around a moment of a stored recording; cut when someone opens the alert."""
    c = tuning.clips
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "clip.mp4"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{max(0.0, pos - c.before_s):.2f}", "-i", path,
                        "-t", f"{c.before_s + c.after_s:.2f}", "-vf", f"scale='min({settings.analysis_width},iw)':-2",
                        "-c:v", "libx264", "-preset", c.preset, "-crf", str(c.crf), "-pix_fmt", "yuv420p", "-an",
                        "-movflags", "+faststart", str(out)], check=True)
        return out.read_bytes()


def scan_file(path: str | Path, camera: Camera, watches: list[tuple[str, Watch]], pool: ScanPool, blobs: BlobStore,
              settings: Settings, tuning: Tuning, start_epoch: float | None = None,
              enrollments: list[Enrollment] | None = None, embedder=None,
              on_progress: Callable[[float], None] | None = None) -> ScanResult:
    t0 = time.perf_counter()
    stages: dict[str, float] = {}
    path = str(path)
    start_epoch = time.time() if start_epoch is None else start_epoch
    duration = probe_duration(path)
    needs = needs_for([w for _, w in watches]) if watches else Needs(classes={"person"})
    plan = chunk_plan(duration, pool.workers, settings.scan_overlap_s, tuning.scan.min_chunk_s,
                      tuning.scan.target_chunk_s)
    jobs = [_Job(i, path, a, b, camera, needs, enrollments or [], start_epoch) for i, (a, b) in enumerate(plan)]

    chunks = []
    futures = [pool.ex.submit(_run_chunk, j) for j in jobs]
    for f in as_completed(futures):
        chunks.append(f.result())
        if on_progress:
            on_progress(0.8 * len(chunks) / len(jobs))
    stages["perceive"] = time.perf_counter() - t0

    t1 = time.perf_counter()
    merged = stitch(chunks, tuning.scan)
    if embedder is not None and needs.embed:
        _embed_tracks(merged, blobs, embedder)
    reasoner = Reasoner(camera, tuning, postures=needs.pose or needs.posture)
    engine = RuleEngine(watches, camera.zones, tuning, settings.timezone, camera.kind, embedder)
    events: list[EventRow] = []
    candidates: list[Candidate] = []
    paths: dict[int, list] = {}
    poses: dict[int, list] = {}
    for p in merged:
        tick = reasoner.step(p)
        events += tick.events
        candidates += engine.on_tick(camera.id, tick, now=tick.ts)
        for t in tick.tracks:
            paths.setdefault(t.track_id, []).append((tick.ts, t.box))
            if t.fresh_pose and t.pose is not None:
                poses.setdefault(t.track_id, []).append({"ts": tick.ts, "torso_angle": round(t.pose.torso_angle_deg, 1)})
    stages["reason"] = time.perf_counter() - t1
    if on_progress:
        on_progress(1.0)

    wall = time.perf_counter() - t0
    stats = ScanStats(duration, wall, duration / wall if wall else 0.0,
                      sum(c.stats.get("frames", 0) for c in chunks),
                      sum(c.stats.get("detector_runs", 0) for c in chunks), pool.workers,
                      {k: round(v, 2) for k, v in stages.items()})
    wanted = {c.track_id for c in candidates}
    return ScanResult(events, candidates, stats, {k: v for k, v in paths.items() if k in wanted},
                      {k: v for k, v in poses.items() if k in wanted})


def _embed_tracks(merged: list[Perception], blobs: BlobStore, embedder) -> None:
    best: dict[int, str] = {}
    for p in merged:
        for o in p.observations:
            if o.cls == "person" and o.best_key:
                best[o.track_id] = o.best_key
    vectors = {}
    for tid, key in best.items():
        img = cv2.imdecode(np.frombuffer(blobs.get(key), np.uint8), cv2.IMREAD_COLOR)
        if img is not None:
            vectors[tid] = embedder.image(img)
    for p in merged:
        for o in p.observations:
            if o.track_id in vectors:
                o.embedding = vectors[o.track_id]
