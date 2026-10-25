"""Indian number plates from a vehicle's best crops: find the plate (LPD-YuNet, else PP-OCR text boxes), warp it
upright, cut two line plates by horizontal projection, read each line with PP-OCRv5, fix the text with the plate
grammar in data/plates.json, vote across crops, and tell the plate's colour by HSV when the frame is in colour."""
import json
import re
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from itertools import product
from pathlib import Path
from typing import Literal, Protocol

import cv2
import numpy as np

from watchful.config import Settings
from watchful.registry import register
from watchful.tuning import PlateTuning
from watchful.vision.models import load_net
from watchful.vision.quality import flatten, grey, order_quad, widen

DATA = Path(__file__).resolve().parents[1] / "data"
Colour = Literal["yellow", "white", "green", "black", "unknown"]


@dataclass(frozen=True)
class PlateRead:
    text: str | None  # compact, e.g. MH12AB1234; None when unread or when the crops disagree
    confidence: float
    colour: Colour
    reads: tuple[str | None, ...] = ()  # each crop's own read, for the record when they disagree


@dataclass(frozen=True)
class Parsed:
    text: str
    fixes: int  # characters changed to fit the grammar
    dropped: int  # characters left out around the plate text
    rank: int  # 0 for the most common layout


@dataclass(frozen=True)
class CropRead:
    text: str | None
    score: float
    colour: Colour
    plate: np.ndarray | None = None  # the warped plate, or the region around its text rows
    via: str = ""  # "plate box" or "text rows"


# grammar

@dataclass(frozen=True)
class _Part:
    kind: str  # letter or digit
    lengths: tuple[int, ...]  # most common first
    codes: frozenset[str] | None
    fixed: str | None


@dataclass(frozen=True)
class Grammar:
    formats: tuple[tuple[_Part, ...], ...]
    as_letter: dict[str, tuple[str, ...]]
    as_digit: dict[str, str]
    state_letters: dict[str, tuple[str, ...]]  # letters read for one another, tried only on state codes
    ignore: tuple[str, ...]  # words printed on plates that are not the number (the IND strip)
    states: frozenset[str]


@lru_cache
def load_grammar(path: Path = DATA / "plates.json") -> Grammar:
    data = json.loads(path.read_text(encoding="utf-8"))
    states = frozenset(data["states"])
    formats = tuple(
        tuple(_Part(p["kind"], tuple(p["lengths"]), states if p.get("codes") == "states" else None, p.get("fixed"))
              for p in f["parts"])
        for f in data["formats"])
    return Grammar(formats, {k: tuple(v) for k, v in data["as_letter"].items()}, dict(data["as_digit"]),
                   {k: tuple(v) for k, v in data["state_letters"].items()},
                   tuple(sorted(data["ignore"], key=len, reverse=True)), states)


def _coerce(chunk: str, part: _Part, g: Grammar) -> tuple[str, int] | None:
    """The chunk as the part's kind of character, and how many characters that changed."""
    if part.kind == "digit":
        out = [c if c.isdigit() else g.as_digit.get(c) for c in chunk]
        if None in out:
            return None
        return "".join(out), sum(a != b for a, b in zip(chunk, out))
    options = [((c,) + (g.state_letters.get(c, ()) if part.codes else ())) if c.isalpha() else g.as_letter.get(c)
               for c in chunk]
    if None in options:
        return None
    best = None
    for letters in product(*options):  # a digit may stand for several letters; the state code decides
        word = "".join(letters)
        if (part.codes is None or word in part.codes) and (part.fixed is None or word == part.fixed):
            changed = sum(a != b for a, b in zip(chunk, word))
            if best is None or changed < best[1]:
                best = (word, changed)
    return best


def _fit(window: str, parts: tuple[_Part, ...], g: Grammar, cfg: PlateTuning) -> tuple[str, int, int] | None:
    best, best_key = None, None
    for choice in product(*(enumerate(p.lengths) for p in parts)):
        if sum(n for _, n in choice) != len(window):
            continue
        out, fixes, at = [], 0, 0
        for part, (_, n) in zip(parts, choice):
            got = _coerce(window[at:at + n], part, g)
            if got is None:
                break
            out.append(got[0])
            fixes += got[1]
            at += n
        else:
            rank = sum(i for i, _ in choice)
            key = (fixes + cfg.rank_cost * rank, fixes)
            if fixes <= cfg.max_fixes and (best_key is None or key < best_key):
                best, best_key = ("".join(out), fixes, rank), key
    return best


def parse(raw: str, grammar: Grammar | None = None, cfg: PlateTuning = PlateTuning()) -> Parsed | None:
    """The plate text in a raw read: the window and layout costing least (characters changed, characters left out
    around it, rarer layouts), `O`/`0`, `I`/`1`, `B`/`8` and the like fixed by position, the state code checked."""
    g = grammar or load_grammar()
    s = re.sub(r"[^A-Z0-9]", "", raw.upper())
    for word in g.ignore:
        s = s.replace(word, "")
    best, best_key = None, None
    for parts in g.formats:
        shortest = sum(min(p.lengths) for p in parts)
        longest = sum(max(p.lengths) for p in parts)
        for n in range(shortest, min(longest, len(s)) + 1):
            for start in range(len(s) - n + 1):
                got = _fit(s[start:start + n], parts, g, cfg)
                if got is None:
                    continue
                text, fixes, rank = got
                dropped = len(s) - n
                key = (fixes + cfg.drop_cost * dropped + cfg.rank_cost * rank, fixes)
                if key[0] <= cfg.max_cost and (best_key is None or key < best_key):
                    best, best_key = Parsed(text, fixes, dropped, rank), key
    return best


# voting

def vote(reads: list[CropRead], cfg: PlateTuning = PlateTuning(), grammar: Grammar | None = None) -> PlateRead:
    """One plate from several crops: the text holding at least `min_share` of the reads' confidence, else a
    character by character vote over reads of the same length that still fits the grammar, else unread (None)."""
    colour = _vote_colour(reads)
    texts = tuple(r.text for r in reads)
    valid = [r for r in reads if r.text]
    if not valid:
        return PlateRead(None, 0.0, colour, texts)
    weight: dict[str, float] = defaultdict(float)
    for r in valid:
        weight[r.text] += max(r.score, 1e-6)
    total = sum(weight.values())
    best = max(weight, key=weight.get)
    if weight[best] / total >= cfg.min_share:
        mean = np.mean([r.score for r in valid if r.text == best])
        return PlateRead(best, round(float(weight[best] / total * mean), 3), colour, texts)
    length = Counter(len(r.text) for r in valid).most_common(1)[0][0]
    same = [r for r in valid if len(r.text) == length]
    chars, shares = [], []
    for i in range(length):
        at: dict[str, float] = defaultdict(float)
        for r in same:
            at[r.text[i]] += max(r.score, 1e-6)
        c = max(at, key=at.get)
        chars.append(c)
        shares.append(at[c] / sum(at.values()))
    text = "".join(chars)
    fit = parse(text, grammar, cfg)
    if len(same) >= cfg.min_char_votes and min(shares) >= cfg.min_share and fit and fit.text == text:
        conf = float(np.mean(shares) * np.mean([r.score for r in same]) * len(same) / len(valid))
        return PlateRead(text, round(conf, 3), colour, texts)
    return PlateRead(None, 0.0, colour, texts)


def _vote_colour(reads: list[CropRead]) -> Colour:
    known = Counter(r.colour for r in reads if r.colour != "unknown")
    if not known:
        return "unknown"
    top = known.most_common()
    if len(top) > 1 and top[0][1] == top[1][1]:
        tied = {c for c, n in top if n == top[0][1]}
        return max((r for r in reads if r.colour in tied), key=lambda r: r.score).colour
    return top[0][0]


# colour

def is_grey(image: np.ndarray, cfg: PlateTuning = PlateTuning()) -> bool:
    """An IR night frame: no pixel has much more of one channel than another."""
    if image.ndim == 2:
        return True
    chroma = image.max(2).astype(np.int16) - image.min(2)
    return float(np.percentile(chroma, 99)) < cfg.grey_chroma


def plate_colour(plate: np.ndarray, frame: np.ndarray, cfg: PlateTuning = PlateTuning()) -> Colour:
    """The plate's background by HSV, its text removed by Otsu; unknown on grey (IR) or dark frames."""
    if is_grey(frame, cfg) or float(cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)[..., 2].mean()) < cfg.dark_v:
        return "unknown"
    h, w = plate.shape[:2]
    y, x = int(h * cfg.colour_border), int(w * cfg.colour_border)
    inner = plate[y:max(y + 1, h - y), x:max(x + 1, w - x)]
    _, bright = cv2.threshold(grey(inner), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bright = bright > 0
    background = bright if bright.mean() >= 0.5 else ~bright
    if background.all() or not background.any():
        return "unknown"
    hsv = cv2.cvtColor(inner, cv2.COLOR_BGR2HSV)
    hue, sat, val = (float(np.median(hsv[..., i][background])) for i in range(3))
    text_val = float(np.median(hsv[..., 2][~background]))
    if val < cfg.black_v:
        return "black" if text_val >= cfg.white_v else "unknown"
    if sat >= cfg.colour_s:
        if cfg.yellow_hue[0] <= hue <= cfg.yellow_hue[1]:
            return "yellow"
        if cfg.green_hue[0] <= hue <= cfg.green_hue[1]:
            return "green"
        return "unknown"
    return "white" if val >= cfg.white_v else "unknown"


# finding plates

class PlateDetector(Protocol):
    def find(self, image: np.ndarray) -> list[tuple[np.ndarray, float]]: ...


@register("plate_detector", "lpd_yunet")
class LpdYunet:
    """The Model Zoo's LPD-YuNet: four corners per plate, decoded from its priors as the Zoo's demo does."""
    MIN_SIZES = ((10, 16, 24), (32, 48), (64, 96), (128, 192, 256))
    STEPS = (8, 16, 32, 64)
    VARIANCE = 0.1
    STRIDE = 64

    def __init__(self, name: str = "lpd_yunet", cfg: PlateTuning = PlateTuning(), settings: Settings | None = None,
                 **_):
        self.cfg = cfg
        self.net = load_net("lpd_yunet", settings)
        self._priors: dict[tuple[int, int], np.ndarray] = {}
        self._lock = threading.Lock()

    def _priors_for(self, w: int, h: int) -> np.ndarray:
        if (w, h) not in self._priors:
            rows = []
            for k, step in enumerate(self.STEPS):
                fh, fw = h // step, w // step
                i, j, s = np.meshgrid(np.arange(fh), np.arange(fw), np.array(self.MIN_SIZES[k]), indexing="ij")
                rows.append(np.stack([(j + 0.5) * step / w, (i + 0.5) * step / h, s / w, s / h], -1).reshape(-1, 4))
            self._priors[(w, h)] = np.concatenate(rows).astype(np.float32)
        return self._priors[(w, h)]

    def find(self, image: np.ndarray) -> list[tuple[np.ndarray, float]]:
        h, w = image.shape[:2]
        s = self.cfg.detect_side / max(h, w)
        nw = max(self.STRIDE, round(w * s / self.STRIDE) * self.STRIDE)
        nh = max(self.STRIDE, round(h * s / self.STRIDE) * self.STRIDE)
        with self._lock:
            self.net.setInput(cv2.dnn.blobFromImage(cv2.resize(image, (nw, nh))))
            loc, conf, iou = self.net.forward(["loc", "conf", "iou"])
        priors = self._priors_for(nw, nh)
        loc, conf, iou = loc.reshape(-1, 14), conf.reshape(-1, 2), iou.reshape(-1)
        scores = np.sqrt(conf[:, 1] * np.clip(iou, 0, 1))
        keep = np.flatnonzero(scores >= self.cfg.detect_score)
        if not len(keep):
            return []
        p = priors[keep]
        corners = np.stack([p[:, :2] + loc[keep, a:a + 2] * self.VARIANCE * p[:, 2:] for a in (4, 6, 10, 12)], 1)
        corners *= np.float32([w, h])  # back to the crop's own pixels
        lo, hi = corners.min(1), corners.max(1)
        size = hi - lo
        aspect = size[:, 0] / np.maximum(size[:, 1], 1e-3)
        tol = self.cfg.outside * np.float32([w, h])
        inside = (lo >= -tol).all(1) & (hi <= np.float32([w, h]) + tol).all(1)
        plausible = inside & (aspect >= self.cfg.plate_aspect[0]) & (aspect <= self.cfg.plate_aspect[1])
        corners, kept = corners[plausible], scores[keep][plausible]
        boxes = np.hstack([lo[plausible], size[plausible]]).tolist()  # x, y, width, height
        picked = cv2.dnn.NMSBoxes(boxes, kept.tolist(), self.cfg.detect_score, self.cfg.detect_nms)
        out = [(order_quad(corners[i]), float(kept[i])) for i in np.asarray(picked, int).reshape(-1)]
        return sorted(out, key=lambda qs: -qs[1])


@register("plate_detector", "none")
class NoPlateDetector:
    def __init__(self, **_):
        pass

    def find(self, image: np.ndarray) -> list[tuple[np.ndarray, float]]:
        return []


def split_lines(plate: np.ndarray, cfg: PlateTuning = PlateTuning()) -> list[np.ndarray]:
    """A plate narrower than `two_line_aspect` cut at the emptiest row near its middle, when that row is a gap."""
    h, w = plate.shape[:2]
    if w / h >= cfg.two_line_aspect:
        return [plate]
    _, bright = cv2.threshold(grey(plate), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    text = bright == 0 if (bright > 0).mean() >= 0.5 else bright > 0  # text is the smaller class
    side = int(w * cfg.split_side)  # the IND strip and bolts stay out of the row counts
    profile = cv2.blur(text[:, side:w - side].mean(1, dtype=np.float32).reshape(-1, 1), (1, 3)).ravel()
    lo, hi = int(h * cfg.split_band[0]), int(h * cfg.split_band[1])
    if hi <= lo:
        return [plate]
    row = lo + int(np.argmin(profile[lo:hi]))
    if profile[row] > cfg.split_gap * float(np.percentile(profile, 90)):
        return [plate]
    return [plate[:row], plate[row:]]


def _rows(boxes: list, overlap: float) -> list[list]:
    """Text boxes grouped into rows by vertical overlap, top to bottom, each row left to right."""
    rows: list[list] = []
    for box in sorted(boxes, key=lambda b: b.quad[:, 1].mean()):
        y0, y1 = box.quad[:, 1].min(), box.quad[:, 1].max()
        for row in rows:
            r0, r1 = row[0].quad[:, 1].min(), row[0].quad[:, 1].max()
            if min(y1, r1) - max(y0, r0) >= overlap * min(y1 - y0, r1 - r0):
                row.append(box)
                break
        else:
            rows.append([box])
    return [sorted(row, key=lambda b: b.quad[:, 0].min()) for row in rows]


class PlateReader:
    def __init__(self, detector: PlateDetector, ocr, cfg: PlateTuning = PlateTuning(),
                 grammar: Grammar | None = None):
        self.detector, self.ocr, self.cfg = detector, ocr, cfg
        self.grammar = grammar or load_grammar()

    def read(self, crops: list[np.ndarray]) -> PlateRead:
        return vote([self.read_crop(c) for c in crops[:self.cfg.max_crops]], self.cfg, self.grammar)

    def read_crop(self, crop: np.ndarray) -> CropRead:
        best: tuple[Parsed, float, np.ndarray, str] | None = None
        first_plate = None
        for quad, _ in self.detector.find(crop)[:self.cfg.max_plates]:
            plate = self._warp(crop, quad)
            first_plate = plate if first_plate is None else first_plate
            got = self._read_plate(plate)
            if got is None or self._cost(got[0]):  # the box may cut characters off: read the text around it too
                region, _ = flatten(crop, widen(quad, self.cfg.reread_margin))
                got = self._pick(got, self._read_text_rows(region))
            best = self._pick(best, got)
            if best and not self._cost(best[0]):
                break
        if best is None:
            best = self._read_text_rows(crop)
        if best is None:
            colour = plate_colour(first_plate, crop, self.cfg) if first_plate is not None else "unknown"
            return CropRead(None, 0.0, colour, first_plate)
        parsed, score, plate, via = best
        conf = max(0.0, score * (1 - self.cfg.fix_penalty * parsed.fixes))
        return CropRead(parsed.text, conf, plate_colour(plate, crop, self.cfg), plate, via)

    def _cost(self, p: Parsed) -> float:
        return p.fixes + self.cfg.drop_cost * p.dropped + self.cfg.rank_cost * p.rank

    def _pick(self, a, b):
        """The better of two reads: the lower grammar cost, then the higher score."""
        if a is None or b is None:
            return a or b
        return a if (self._cost(a[0]), -a[1]) <= (self._cost(b[0]), -b[1]) else b

    def _warp(self, image: np.ndarray, quad: np.ndarray) -> np.ndarray:
        tl, tr, br, bl = quad
        height = max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr), 1.0)
        plate, _ = flatten(image, widen(quad, self.cfg.margin), self.cfg.plate_height / height, cv2.INTER_CUBIC)
        return plate

    def _read_plate(self, plate: np.ndarray) -> tuple[Parsed, float, np.ndarray, str] | None:
        lines = split_lines(plate, self.cfg)
        for parts in [lines] if len(lines) == 1 else [lines, [plate]]:  # a cut that reads nothing: one line
            reads = [self.ocr.read_line(line) for line in parts]
            parsed = parse("".join(t for t, _ in reads), self.grammar, self.cfg)
            if parsed:
                return parsed, float(np.mean([s for _, s in reads])), plate, "plate box"
        return None

    def _read_text_rows(self, crop: np.ndarray) -> tuple[Parsed, float, np.ndarray, str] | None:
        """PP-OCR's own text lines, a row at a time and two stacked rows together."""
        rows = _rows(self.ocr.read(crop), self.cfg.group_overlap)
        groups = [[row] for row in rows] + [[a, b] for a, b in zip(rows, rows[1:])]
        best = None
        for group in groups:
            boxes = [box for row in group for box in row]
            parsed = parse("".join(box.text for box in boxes), self.grammar, self.cfg)
            if not parsed:
                continue
            points = np.concatenate([box.quad for box in boxes])
            region = order_quad(cv2.boxPoints(cv2.minAreaRect(points)))
            plate, _ = flatten(crop, widen(region, self.cfg.region_margin))
            best = self._pick(best, (parsed, float(np.mean([box.score for box in boxes])), plate, "text rows"))
        return best


_default: PlateReader | None = None


def read_plate(crops: list[np.ndarray], reader: PlateReader | None = None) -> PlateRead:
    """Plate text, confidence and colour from a vehicle's best crops (sharpest and largest first)."""
    global _default
    if reader is None:
        if _default is None:
            from watchful.components import make_plate_reader
            from watchful.config import get_settings
            from watchful.tuning import load_tuning
            settings = get_settings()
            _default = make_plate_reader(settings, load_tuning(settings.tuning_file))
        reader = _default
    return reader.read(crops)
