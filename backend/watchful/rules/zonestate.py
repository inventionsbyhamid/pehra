"""Zone state by example: a zone's state is the state of the saved example picture it looks most like.
Examples are kept apart by day and night (colour and IR pictures look nothing alike). Each comparison is a CLAHE
edge picture correlation, plus the image model's similarity when one is loaded."""
from dataclasses import dataclass, field

import cv2
import numpy as np

from watchful.tuning import ZoneTuning
from watchful.vision.zones import Zone


def is_night(image: np.ndarray, cfg: ZoneTuning = ZoneTuning()) -> bool:
    """An IR night picture has no colour; a small copy averages sensor noise away first."""
    if image.ndim < 3:
        return True
    h, w = image.shape[:2]
    small = cv2.resize(image, (cfg.night_probe_width, max(1, round(h * cfg.night_probe_width / w))),
                       interpolation=cv2.INTER_AREA)
    return float(cv2.cvtColor(small, cv2.COLOR_BGR2HSV)[..., 1].mean()) < cfg.night_saturation


def zone_crop(zone: Zone, image: np.ndarray) -> np.ndarray:
    """The zone's bounding box with everything outside the polygon blacked out."""
    h, w = image.shape[:2]
    pts = zone.contour((w, h)).astype(np.int32)
    x0, y0 = np.maximum(pts.min(0), 0)
    x1, y1 = np.minimum(pts.max(0), [w, h])
    mask = np.zeros((h, w), np.uint8)
    cv2.fillPoly(mask, [pts], 255)
    out = cv2.bitwise_and(image, image, mask=mask)
    return out[y0:max(y1, y0 + 1), x0:max(x1, x0 + 1)]


def edge_picture(crop: np.ndarray, cfg: ZoneTuning) -> np.ndarray:
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    gray = cv2.resize(gray, (cfg.state_size, cfg.state_size), interpolation=cv2.INTER_AREA)
    gray = cv2.GaussianBlur(gray, (0, 0), cfg.state_pre_blur)
    tiles = (cfg.state_clahe_tiles, cfg.state_clahe_tiles)
    gray = cv2.createCLAHE(clipLimit=cfg.state_clahe_clip, tileGridSize=tiles).apply(gray)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1)
    return cv2.GaussianBlur(cv2.magnitude(gx, gy), (0, 0), cfg.state_blur)


def correlation(a: np.ndarray, b: np.ndarray) -> float:
    """Normalised correlation of two same size pictures; 0 when either is flat."""
    if cv2.meanStdDev(a)[1].item() < 1e-6 or cv2.meanStdDev(b)[1].item() < 1e-6:
        return 0.0
    return float(cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)[0, 0])


@dataclass
class Example:
    state: str
    period: str
    edges: np.ndarray
    embedding: np.ndarray | None = None


@dataclass
class StateGuess:
    state: str  # a saved state name, or "unsure"
    period: str
    score: float
    margin: float


@dataclass
class ZoneStates:
    cfg: ZoneTuning = field(default_factory=ZoneTuning)
    embedder: object | None = None
    examples: dict[str, list[Example]] = field(default_factory=dict)

    def has(self, zone: str) -> bool:
        return bool(self.examples.get(zone))

    def states(self, zone: str) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for e in self.examples.get(zone, []):
            out.setdefault(e.state, set()).add(e.period)
        return out

    def _features(self, zone: Zone, image: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
        crop = zone_crop(zone, image)
        emb = self.embedder.image(crop) if self.embedder is not None else None
        return edge_picture(crop, self.cfg), emb

    def add(self, zone: Zone, state: str, image: np.ndarray, period: str | None = None) -> Example:
        """One saved picture of the whole frame with the zone in a named state."""
        edges, emb = self._features(zone, image)
        ex = Example(state.strip().lower(), period or ("night" if is_night(image, self.cfg) else "day"), edges, emb)
        self.examples.setdefault(zone.name, []).append(ex)
        return ex

    def _score(self, ex: Example, edges: np.ndarray, emb: np.ndarray | None) -> float:
        s = correlation(ex.edges, edges)
        if emb is not None and ex.embedding is not None:
            w = self.cfg.state_edge_weight
            s = w * s + (1 - w) * float(np.dot(emb, ex.embedding))
        return s

    def classify(self, zone: Zone, image: np.ndarray) -> StateGuess:
        period = "night" if is_night(image, self.cfg) else "day"
        saved = self.examples.get(zone.name, [])
        pool = [e for e in saved if e.period == period] or saved
        if not pool:
            return StateGuess("unsure", period, 0.0, 0.0)
        edges, emb = self._features(zone, image)
        best: dict[str, float] = {}
        for e in pool:
            best[e.state] = max(best.get(e.state, -1.0), self._score(e, edges, emb))
        ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
        state, score = ranked[0]
        margin = score - ranked[1][1] if len(ranked) > 1 else score
        sure = score >= self.cfg.state_min_score and (len(ranked) == 1 or margin >= self.cfg.state_margin)
        return StateGuess(state if sure else "unsure", period, score, margin)


def load_states(rows, read_picture, zones: list[Zone], cfg: ZoneTuning, embedder=None) -> ZoneStates:
    """Saved examples as the repository lists them, (example id, zone id, state, picture key); read_picture(key)
    gives JPEG bytes. Rows for zones the camera no longer has, or pictures that are gone, are skipped."""
    by_id = {z.id: z for z in zones}
    out = ZoneStates(cfg, embedder)
    for _, zone_id, state, key in rows:
        if zone_id not in by_id:
            continue
        try:
            image = cv2.imdecode(np.frombuffer(read_picture(key), np.uint8), cv2.IMREAD_COLOR)
        except (OSError, KeyError, ValueError):
            continue
        if image is not None:
            out.add(by_id[zone_id], state, image, period_of(key))
    return out


def picture_loads(read_picture, key: str) -> bool:
    """Whether a saved example's picture is there and reads, as load_states needs it: a row whose picture is gone
    teaches nothing."""
    try:
        data = read_picture(key)
    except (OSError, KeyError, ValueError):
        return False
    return bool(data) and cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_REDUCED_GRAYSCALE_8) is not None


def example_key(workspace_id: str, camera_id: str, zone_id: str, period: str, ts: float) -> str:
    """Where a saved example picture lives, under its society; the period rides in the name so lists need no
    decoding."""
    return f"{workspace_id}/{camera_id}/states/{zone_id}/{int(ts * 1000)}_{period}.jpg"


def period_of(key: str) -> str | None:
    tail = key.rsplit("_", 1)[-1].split(".")[0]
    return tail if tail in ("day", "night") else None
