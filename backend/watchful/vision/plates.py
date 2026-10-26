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
    text: str | None  # compact, e.g. MH12AB1234; None when unread, when the crops disagree or under min_conf
    confidence: float  # of the best guess, also when it stays under min_conf; 0 with no guess
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
    via: str = ""  # "plate box", "text rows around the plate box" or "text rows"


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


def grammar_cost(p: Parsed, cfg: PlateTuning = PlateTuning()) -> float:
    """How far a read had to be bent to fit: a changed character is 1."""
    return p.fixes + cfg.drop_cost * p.dropped + cfg.rank_cost * p.rank


def _clean(line: str, g: Grammar) -> str:
    """Letters and digits only, words that are not the number (the IND strip) taken off either end."""
    s = re.sub(r"[^A-Z0-9]", "", line.upper())
    trimmed = None
    while trimmed != s:
        trimmed = s
        for word in g.ignore:
            s = s.removeprefix(word).removesuffix(word)
    return s


def parse(raw: str | list[str], grammar: Grammar | None = None, cfg: PlateTuning = PlateTuning()) -> Parsed | None:
    """The plate text in a raw read (one string, or the lines of a plate top to bottom): the window and layout
    costing least (characters changed, characters left out around it, rarer layouts), `O`/`0`, `I`/`1`, `B`/`8` and
    the like fixed by position, the state code checked."""
    g = grammar or load_grammar()
    s = "".join(_clean(line, g) for line in ([raw] if isinstance(raw, str) else raw))
    best, best_key = None, None
    for parts in g.formats:
        shortest = sum(min(p.lengths) for p in parts)
        longest = sum(max(p.lengths) for p in parts)
        for n in range(shortest, min(longest, len(s)) + 1):
            for start in range(len(s) - n + 1):
                got = _fit(s[start:start + n], parts, g, cfg)
                if got is None:
                    continue
                parsed = Parsed(got[0], got[1], len(s) - n, got[2])
                key = (grammar_cost(parsed, cfg), parsed.fixes)
                if key[0] <= cfg.max_cost and (best_key is None or key < best_key):
                    best, best_key = parsed, key
    return best


def crop_confidence(parsed: Parsed, read_score: float, box_score: float, cfg: PlateTuning = PlateTuning()) -> float:
    """One crop's read: the recogniser's score, less `cost_conf` per unit of grammar cost, scaled by how sure the
    plate box was (`rows_score` for text rows found without one)."""
    box = cfg.box_floor + (1 - cfg.box_floor) * box_score
    return max(0.0, read_score * (1 - cfg.cost_conf * grammar_cost(parsed, cfg)) * box)


# voting

def vote(reads: list[CropRead], cfg: PlateTuning = PlateTuning(), grammar: Grammar | None = None) -> PlateRead:
    """One plate from several crops: the text holding at least `min_share` of the reads' confidence, else a
    character by character vote over reads of the same length that still fits the grammar, else unread (None).
    Its confidence is the agreeing reads' mean, times their share of all the crops (an unread crop counting as a
    read of `unread_weight`), times `single_read` when one crop alone read it; under `min_conf` it stays unread."""
    colour = _vote_colour(reads)
    texts = tuple(r.text for r in reads)
    reads = distinct(reads)
    valid = [r for r in reads if r.text]
    if not valid:
        return PlateRead(None, 0.0, colour, texts)
    weight: dict[str, float] = defaultdict(float)
    for r in valid:
        weight[r.text] += max(r.score, 1e-6)
    total = sum(weight.values())
    text = max(weight, key=weight.get)
    if weight[text] / total >= cfg.min_share:
        agreeing, agreed = [r for r in valid if r.text == text], weight[text]
    else:
        length = Counter(len(r.text) for r in valid).most_common(1)[0][0]
        agreeing = [r for r in valid if len(r.text) == length]
        chars, shares = [], []
        for i in range(length):
            at: dict[str, float] = defaultdict(float)
            for r in agreeing:
                at[r.text[i]] += max(r.score, 1e-6)
            c = max(at, key=at.get)
            chars.append(c)
            shares.append(at[c] / sum(at.values()))
        text = "".join(chars)
        fit = parse(text, grammar, cfg)
        if len(agreeing) < cfg.min_char_votes or min(shares) < cfg.min_share or not fit or fit.text != text:
            return PlateRead(None, 0.0, colour, texts)
        agreed = sum(max(r.score, 1e-6) for r in agreeing) * float(np.mean(shares))
    share = agreed / (total + cfg.unread_weight * (len(reads) - len(valid)))
    conf = float(np.mean([r.score for r in agreeing])) * share * (cfg.single_read if len(agreeing) == 1 else 1.0)
    conf = round(conf, 3)
    return PlateRead(text if conf >= cfg.min_conf else None, conf, colour, texts)


def _same_read(a: CropRead, b: CropRead) -> bool:
    if a.text != b.text or a.score != b.score or a.via != b.via:
        return False
    if a.plate is None or b.plate is None:
        return a.plate is None and b.plate is None
    return a.plate.shape == b.plate.shape and np.array_equal(a.plate, b.plate)


def distinct(reads: list[CropRead]) -> list[CropRead]:
    """The reads with identical repeats of a text (the same crop read again) counted once: a repeat is not a second
    opinion, so it never lifts a costly read over min_conf. Unread crops all count against the text."""
    out: list[CropRead] = []
    for r in reads:
        if r.text is None or not any(_same_read(r, o) for o in out):
            out.append(r)
    return out


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
    return float(np.percentile(chroma, cfg.grey_percentile)) < cfg.grey_chroma


def plate_colour(plate: np.ndarray, frame: np.ndarray, cfg: PlateTuning = PlateTuning()) -> Colour:
    """The plate's background by HSV, its text removed by Otsu; unknown on a grey (IR) frame or when the plate itself
    is too dark or too flat to tell (a dark car body around it does not matter)."""
    if is_grey(frame, cfg):
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
    if max(val, text_val) < cfg.dark_v or abs(val - text_val) < cfg.min_contrast:
        return "unknown"
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

def quad_fill(corners: np.ndarray) -> np.ndarray:
    """For quads (n, 4, 2) with corners in order around them: their area over the product of their mean opposite
    edges. 1 for any rectangle however it is turned, about the cosine of the slant for a trapezoid, near 0 when the
    corners collapse onto a line or cross."""
    corners = np.asarray(corners, np.float32).reshape(-1, 4, 2)
    x, y = corners[..., 0], corners[..., 1]
    area = 0.5 * np.abs((x * np.roll(y, -1, 1) - np.roll(x, -1, 1) * y).sum(1))
    edges = np.linalg.norm(np.roll(corners, -1, 1) - corners, axis=2)
    return area / np.maximum((edges[:, 0] + edges[:, 2]) * (edges[:, 1] + edges[:, 3]) / 4, 1e-6)


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
        sliver = quad_fill(corners) < self.cfg.min_fill
        if sliver.any():  # corners that crossed or collapsed: the box around them still holds the plate
            corners[sliver] = np.stack([lo[sliver], np.c_[hi[sliver, 0], lo[sliver, 1]], hi[sliver],
                                        np.c_[lo[sliver, 0], hi[sliver, 1]]], 1)
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
    if profile[row] > cfg.split_gap * float(np.percentile(profile, cfg.busy_percentile)):
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


@dataclass(frozen=True)
class _Candidate:
    parsed: Parsed
    read_score: float
    box_score: float
    plate: np.ndarray
    via: str


class PlateReader:
    def __init__(self, detector: PlateDetector, ocr, cfg: PlateTuning = PlateTuning(),
                 grammar: Grammar | None = None):
        self.detector, self.ocr, self.cfg = detector, ocr, cfg
        self.grammar = grammar or load_grammar()

    def read(self, crops: list[np.ndarray]) -> PlateRead:
        return vote([self.read_crop(c) for c in crops[:self.cfg.max_crops]], self.cfg, self.grammar)

    def vote(self, reads: list[CropRead]) -> PlateRead:
        return vote(reads, self.cfg, self.grammar)

    def read_crop(self, crop: np.ndarray) -> CropRead:
        best: _Candidate | None = None
        first_plate = None
        for quad, box_score in self.detector.find(crop)[:self.cfg.max_plates]:
            plate = self._warp(crop, quad)
            first_plate = plate if first_plate is None else first_plate
            got = self._read_plate(plate, box_score)
            if got is None or grammar_cost(got.parsed, self.cfg):  # the box may cut characters off: read around it
                region, _ = flatten(crop, widen(quad, self.cfg.reread_margin))
                got = self._pick(got, self._read_text_rows(region, box_score, "text rows around the plate box"))
            best = self._pick(best, got)
            if best and not grammar_cost(best.parsed, self.cfg):
                break
        if best is None:
            best = self._read_text_rows(crop, self.cfg.rows_score)
        if best is None:
            colour = plate_colour(first_plate, crop, self.cfg) if first_plate is not None else "unknown"
            return CropRead(None, 0.0, colour, first_plate)
        conf = crop_confidence(best.parsed, best.read_score, best.box_score, self.cfg)
        return CropRead(best.parsed.text, conf, plate_colour(best.plate, crop, self.cfg), best.plate, best.via)

    def _pick(self, a: _Candidate | None, b: _Candidate | None) -> _Candidate | None:
        """The better of two reads: the lower grammar cost, then the higher score."""
        if a is None or b is None:
            return a or b
        def key(c):
            return grammar_cost(c.parsed, self.cfg), -c.read_score
        return a if key(a) <= key(b) else b

    def _warp(self, image: np.ndarray, quad: np.ndarray) -> np.ndarray:
        tl, tr, br, bl = quad
        height = max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr), 1.0)
        plate, _ = flatten(image, widen(quad, self.cfg.margin), self.cfg.plate_height / height, cv2.INTER_CUBIC)
        return plate

    def _score(self, score: float | None) -> float:
        return self.cfg.unscored_read if score is None else score

    def _read_plate(self, plate: np.ndarray, box_score: float) -> _Candidate | None:
        lines = split_lines(plate, self.cfg)
        for parts in [lines] if len(lines) == 1 else [lines, [plate]]:  # a cut that reads nothing: one line
            reads = [self.ocr.read_line(line) for line in parts]
            parsed = parse([t for t, _ in reads], self.grammar, self.cfg)
            if parsed:
                score = float(np.mean([self._score(s) for _, s in reads]))
                return _Candidate(parsed, score, box_score, plate, "plate box")
        return None

    def _read_text_rows(self, crop: np.ndarray, box_score: float, via: str = "text rows") -> _Candidate | None:
        """PP-OCR's own text lines, a row at a time and two stacked rows together."""
        rows = _rows(self.ocr.read(crop), self.cfg.group_overlap)
        groups = [[row] for row in rows] + [[a, b] for a, b in zip(rows, rows[1:])]
        best = None
        for group in groups:
            boxes = [box for row in group for box in row]
            parsed = parse([box.text for box in boxes], self.grammar, self.cfg)
            if not parsed:
                continue
            points = np.concatenate([box.quad for box in boxes])
            region = order_quad(cv2.boxPoints(cv2.minAreaRect(points)))
            plate, _ = flatten(crop, widen(region, self.cfg.region_margin))
            score = float(np.mean([box.score for box in boxes]))
            best = self._pick(best, _Candidate(parsed, score, box_score, plate, via))
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
