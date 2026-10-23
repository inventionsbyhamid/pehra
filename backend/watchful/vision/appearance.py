from dataclasses import dataclass, field

import cv2
import numpy as np

SAME_THRESHOLD = 0.35
CONTINUITY_S = 600.0


def signature(image: np.ndarray, box) -> np.ndarray:
    """Hue and saturation histogram of the torso: rows 20% to 60% of the box, middle 60% of its width."""
    x, y, w, h = box
    H, W = image.shape[:2]
    x0, x1 = int(max(0, x + 0.2 * w)), int(min(W, x + 0.8 * w))
    y0, y1 = int(max(0, y + 0.2 * h)), int(min(H, y + 0.6 * h))
    if x1 <= x0 or y1 <= y0:
        return np.zeros((30, 32), np.float32)
    hsv = cv2.cvtColor(image[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [30, 32], [0, 180, 0, 256])
    cv2.normalize(hist, hist, 1.0, 0.0, cv2.NORM_L1)
    return hist


def same_person(a: np.ndarray, b: np.ndarray) -> float:
    """Bhattacharyya distance between two signatures: 0 is identical, 1 is nothing in common."""
    a = np.asarray(a, np.float32).reshape(30, 32)
    b = np.asarray(b, np.float32).reshape(30, 32)
    return float(cv2.compareHist(a, b, cv2.HISTCMP_BHATTACHARYYA))


@dataclass
class TrackSighting:
    track_id: int
    first_ts: float
    last_ts: float
    signature: np.ndarray
    thumbnail_key: str | None = None
    person_id: str | None = None
    name: str | None = None


@dataclass
class PersonGroup:
    group_id: str
    track_ids: list[int]
    label: str | None
    first_ts: float
    last_ts: float
    thumbnail_key: str | None
    ambiguous: bool = False
    person_id: str | None = None
    members: list[TrackSighting] = field(default_factory=list, repr=False)


def _overlaps(s: TrackSighting, g: PersonGroup) -> bool:
    return any(s.first_ts < m.last_ts and m.first_ts < s.last_ts for m in g.members)


def _distance(s: TrackSighting, g: PersonGroup) -> float:
    return min(same_person(s.signature, m.signature) for m in g.members)


def _gap(s: TrackSighting, g: PersonGroup) -> float:
    return min(max(s.first_ts - m.last_ts, m.first_ts - s.last_ts, 0.0) for m in g.members)


def _add(g: PersonGroup, s: TrackSighting) -> None:
    g.members.append(s)
    g.track_ids.append(s.track_id)
    g.first_ts, g.last_ts = min(g.first_ts, s.first_ts), max(g.last_ts, s.last_ts)
    g.thumbnail_key = g.thumbnail_key or s.thumbnail_key


def _new(s: TrackSighting) -> PersonGroup:
    return PersonGroup(f"g{s.track_id}", [s.track_id], s.name, s.first_ts, s.last_ts, s.thumbnail_key,
                       person_id=s.person_id, members=[s])


def link_tracks(items: list[TrackSighting]) -> list[PersonGroup]:
    """Group a day's person tracks: face matches first, then clothing and time."""
    items = sorted(items, key=lambda s: s.first_ts)
    groups: list[PersonGroup] = []
    by_person: dict[str, PersonGroup] = {}
    for s in items:
        if s.person_id:
            if s.person_id in by_person:
                _add(by_person[s.person_id], s)
            else:
                by_person[s.person_id] = _new(s)
                groups.append(by_person[s.person_id])
    for s in items:
        if s.person_id:
            continue
        best, best_d = None, SAME_THRESHOLD
        for g in groups:
            if _overlaps(s, g) or (g.person_id and _gap(s, g) > CONTINUITY_S):
                continue
            d = _distance(s, g)
            if d < best_d:
                best, best_d = g, d
        if best:
            _add(best, s)
        else:
            groups.append(_new(s))
    for i, a in enumerate(groups):
        for b in groups[i + 1:]:
            if a.person_id and b.person_id:
                continue
            if min(_distance(m, b) for m in a.members) < SAME_THRESHOLD:
                a.ambiguous = b.ambiguous = True
    for g in groups:
        order = sorted(range(len(g.members)), key=lambda i: g.members[i].first_ts)
        g.members = [g.members[i] for i in order]
        g.track_ids = [m.track_id for m in g.members]
    return sorted(groups, key=lambda g: g.first_ts)
