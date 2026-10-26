"""In a camera process with plate reading on: the plate reader runs on vehicle tracks, at most Tuning
ledger.reads_per_track times a track and, on a camera with lines, only while the vehicle is near one, so its cost
stays bounded (about 150 ms a read on the dev box). Reads attach to the track as attributes (plate_text, plate_colour)
for the attribute signal, and each vehicle crossing of a line becomes a passage message for the app's gate log once
the track's reads are in."""
import time
from collections import OrderedDict
from dataclasses import dataclass, field

import cv2
import numpy as np

from watchful.store.blobs import society_key
from watchful.tuning import Tuning
from watchful.vision.plates import CropRead, PlateRead
from watchful.vision.zones import foot_point, line_position

ENDED_KEPT = 64  # ended tracks whose plates are kept until their passage goes


@dataclass
class _Plate:
    reads: list[CropRead] = field(default_factory=list)
    last: float = float("-inf")
    plate: PlateRead = PlateRead(None, 0.0, "unknown")
    picture: np.ndarray | None = None  # the vehicle crop of the best read, for the passage
    picture_score: float = -1.0

    def attributes(self) -> dict[str, str]:
        out = {}
        if self.plate.text is not None:  # a guess under min_conf is never a plate
            out["plate_text"] = self.plate.text
        if self.plate.colour != "unknown":
            out["plate_colour"] = self.plate.colour
        return out


class PlateSampler:
    def __init__(self, reader, zones, tuning: Tuning):
        self.reader, self.cfg, self.lines_cfg = reader, tuning.ledger, tuning.zones
        self.lines = [z for z in zones if z.is_line and len(z.polygon) >= 2]
        self.tracks: dict[int, _Plate] = {}
        self.ended: OrderedDict[int, _Plate] = OrderedDict()
        self.size = (0, 0)
        self.reads = 0
        self.ms = 0.0

    def near_line(self, box, size) -> bool:
        if not self.lines:
            return True
        foot = foot_point(box)
        reach = self.cfg.line_near * max(box[3], 1.0)
        for line in self.lines:
            dist, along = line_position(line, foot, size)
            if abs(dist) <= reach and -self.lines_cfg.line_extend <= along <= 1 + self.lines_cfg.line_extend:
                return True
        return False

    def observe(self, image: np.ndarray, track, ts: float) -> dict[str, str] | None:
        if track.cls not in self.cfg.classes:
            return None
        h, w = image.shape[:2]
        self.size = (w, h)
        st = self.tracks.setdefault(track.id, _Plate())
        x, y, bw, bh = track.box
        if len(st.reads) < self.cfg.reads_per_track and ts - st.last >= self.cfg.read_gap_s \
                and min(bw, bh) >= self.cfg.min_box_px and self.near_line(track.box, (w, h)):
            st.last = ts
            mx, my = self.cfg.crop_margin * bw, self.cfg.crop_margin * bh
            x0, y0 = int(max(0, x - mx)), int(max(0, y - my))
            x1, y1 = int(min(w, x + bw + mx)), int(min(h, y + bh + my))
            crop = image[y0:y1, x0:x1]
            if crop.size:
                start = time.perf_counter()
                got = self.reader.read_crop(crop)
                self.ms += (time.perf_counter() - start) * 1000
                self.reads += 1
                st.reads.append(got)
                st.plate = self.reader.vote(st.reads)
                if got.score > st.picture_score or st.picture is None:
                    st.picture, st.picture_score = crop.copy(), got.score
        return st.attributes() or None

    def forget(self, track_id: int, ts: float | None = None) -> dict[str, str] | None:
        st = self.tracks.pop(track_id, None)
        if st is None:
            return None
        self.ended[track_id] = st
        while len(self.ended) > ENDED_KEPT:
            self.ended.popitem(last=False)
        return st.attributes() or None

    def state(self, track_id: int) -> _Plate | None:
        return self.tracks.get(track_id) or self.ended.get(track_id)

    def stats(self) -> dict:
        return {"plate_reads": self.reads, "plate_ms": round(self.ms / max(self.reads, 1), 1)}


class Passages:
    """Crossings of a line by a vehicle, held until the track's reads are in (Tuning ledger.reads_per_track), the
    track ends, or ledger.settle_s passed, then sent to the app as {"type": "passage"} for its gate log."""

    def __init__(self, sampler: PlateSampler, blobs, camera, tuning: Tuning):
        self.sampler, self.blobs, self.camera, self.cfg = sampler, blobs, camera, tuning.ledger
        self.pending: list[tuple[object, float]] = []

    def on_tick(self, tick) -> list[dict]:
        for e in tick.events:
            if e.kind == "cross" and e.cls in self.cfg.classes:
                self.pending.append((e, tick.ts + self.cfg.settle_s))
        ended = {s.track_id for s in tick.ended}
        out = []
        for item in list(self.pending):
            e, due = item
            st = self.sampler.state(e.track_id)
            done = st is None or len(st.reads) >= self.cfg.reads_per_track or e.track_id in ended
            if done or tick.ts >= due:
                self.pending.remove(item)
                out.append(self.message(e, st))
        return out

    def flush(self) -> list[dict]:
        out = [self.message(e, self.sampler.state(e.track_id)) for e, _ in self.pending]
        self.pending.clear()
        return out

    def message(self, e, st: _Plate | None) -> dict:
        plate = st.plate if st is not None else PlateRead(None, 0.0, "unknown")
        key = None
        if st is not None and st.picture is not None:
            ok, jpg = cv2.imencode(".jpg", st.picture, [cv2.IMWRITE_JPEG_QUALITY, self.cfg.jpeg_quality])
            if ok:
                key = society_key(self.camera.workspace_id, self.camera.id, "passages",
                                  f"{e.track_id}_{int(e.ts * 1000)}.jpg")
                self.blobs.put(key, jpg.tobytes(), "image/jpeg")
        return {"type": "passage", "workspace_id": self.camera.workspace_id, "camera_id": self.camera.id,
                "event": e, "plate": {"text": plate.text, "confidence": plate.confidence, "colour": plate.colour,
                                      "reads": list(plate.reads)}, "picture_key": key}
