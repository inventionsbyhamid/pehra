"""Find anything in recorded footage, by words or a photo. One semantic index covers whole scenes (a frame every
few seconds while something moves) and every tracked object's best crop; words become a caption plus filters,
the index ranks by image and text embeddings, and the AI confirms the best matches."""
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

import cv2
import numpy as np

from watchful.agent.llm import LLM, AIError, AILimitReached, Msg
from watchful.agent.prompting import parse_json, render
from watchful.rules.vocab import resolve_classes
from watchful.tuning import SignalTuning
from watchful.vision.detect import COCO
from watchful.vision.faces import ConsentRequired, FaceIndex

SEARCH_KINDS = ["scene", "track_end"]
INTERPRET_SCHEMA = {"type": "object", "properties": {
    "looks_like": {"type": "string"}, "not_like": {"type": ["string", "null"]},
    "camera": {"type": ["string", "null"]}, "since": {"type": ["number", "null"]},
    "until": {"type": ["number", "null"]}, "classes": {"type": "array", "items": {"type": "string"}}},
    "required": ["looks_like"]}
CONFIRM_SCHEMA = {"type": "object", "properties": {"matches": {"type": "array", "items": {"type": "integer"}}}}


@dataclass
class SearchQuery:
    text: str | None = None
    photo: bytes | None = None
    person_id: str | None = None  # an enrolled person, who consented when enrolled
    consent: bool = False
    camera_id: str | None = None
    since: float | None = None
    until: float | None = None
    classes: list[str] = field(default_factory=list)


@dataclass
class SearchHit:
    kind: Literal["scene", "object"]
    camera_id: str
    ts: float
    start_ts: float
    end_ts: float
    thumbnail_url: str | None
    score: float
    confirmed: bool
    method: Literal["embedding", "face"]
    event_id: str | None
    track_id: int | None
    cls: str | None


@dataclass
class SearchResult:
    query: dict
    results: list[SearchHit]
    problem: str | None = None


def _decode(data: bytes):
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def _num(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def interpret(q: SearchQuery, repo, llm: LLM | None, now: float, tz: str) -> dict:
    """Words to a caption plus filters; explicit fields on the query always win."""
    base = {"looks_like": q.text or "", "not_like": None, "camera_id": q.camera_id, "since": q.since,
            "until": q.until, "classes": list(q.classes)}
    if llm is None or not q.text:
        return base
    cams = {c.name: c.id for c in repo.list_cameras()}
    stamp = datetime.fromtimestamp(now, ZoneInfo(tz)).strftime("%A %Y-%m-%d %H:%M:%S")
    system = render("search", now=f"{stamp} (ts {now:.0f})", tz=tz, cameras=list(cams), classes=COCO)
    try:
        data = parse_json(llm.complete(system, [Msg("user", q.text)], json_schema=INTERPRET_SCHEMA))
    except (TimeoutError, AIError, AILimitReached, ValueError):
        return base
    try:
        classes = resolve_classes([c for c in data.get("classes") or [] if isinstance(c, str)])
    except ValueError:
        classes = []
    return {"looks_like": str(data.get("looks_like") or q.text), "not_like": data.get("not_like") or None,
            "camera_id": q.camera_id or cams.get(data.get("camera")),
            "since": q.since if q.since is not None else _num(data.get("since")),
            "until": q.until if q.until is not None else _num(data.get("until")),
            "classes": q.classes or classes}


def _span(e) -> tuple[float, float]:
    if e.kind == "track_end":
        return e.ts - float(e.data.get("duration_s") or 0.0), e.ts
    return e.ts, e.ts


def _hit(e, blobs, score: float, method: str, confirmed: bool = False) -> SearchHit:
    start, end = _span(e)
    return SearchHit("object" if e.kind == "track_end" else "scene", e.camera_id, e.ts, start, end,
                     blobs.url(e.keyframe_key) if e.keyframe_key else None, round(score, 4), confirmed, method,
                     e.id, e.track_id, e.cls)


def _faces(rows, blobs, faces: FaceIndex, image=None, person_id: str | None = None) -> list[SearchHit]:
    probe = type(faces)(cfg=faces.cfg, settings=faces.settings)
    if person_id:
        probe.load([e for e in faces.people() if e.person_id == person_id])
    else:
        probe.enroll("query", "photo", image, consent=True)
    hits = []
    for e in rows:
        if e.kind != "track_end" or e.cls != "person" or not e.keyframe_key:
            continue
        img = _decode(blobs.get(e.keyframe_key))
        m = probe.identify(img, (0, 0, img.shape[1], img.shape[0])) if img is not None else None
        if m:
            hits.append(_hit(e, blobs, m.score, "face", True))
    return sorted(hits, key=lambda h: -h.score)


def _vector(e, repo, blobs, embedder, budget: list[int]):
    if e.embedding is not None:
        return np.asarray(e.embedding, np.float32)
    if budget[0] <= 0 or not e.keyframe_key:
        return None
    img = _decode(blobs.get(e.keyframe_key))
    if img is None:
        return None
    budget[0] -= 1
    v = embedder.image(img)
    repo.set_event_embedding(e.id, v.tolist())
    return v


def _confirm(hits: list[SearchHit], caption: str, blobs, llm: LLM, keys: dict[str, str]) -> list[SearchHit]:
    images = [blobs.get(keys[h.event_id]) for h in hits]
    prompt = (f"These {len(images)} pictures from home cameras are numbered from 1 in order. Which of them show "
              f"{caption}? Reply with JSON {{\"matches\": [numbers]}}.")
    try:
        reply = parse_json(llm.complete("You check pictures from security cameras.", [Msg("user", prompt)],
                                        images=images, json_schema=CONFIRM_SCHEMA))
        keep = {int(i) for i in reply.get("matches", []) if str(i).lstrip("-").isdigit()}
    except (TimeoutError, AIError, AILimitReached, ValueError, json.JSONDecodeError):
        return hits
    for i, h in enumerate(hits, start=1):
        h.confirmed = i in keep
    return [h for h in hits if h.confirmed] + [h for h in hits if not h.confirmed]


def search(q: SearchQuery, repo, blobs, embedder, llm: LLM | None = None, faces: FaceIndex | None = None,
           cfg: SignalTuning = SignalTuning(), now: float | None = None, tz: str = "Asia/Kolkata") -> SearchResult:
    if q.text is None and q.photo is None and q.person_id is None:
        raise ValueError("search with words, a photo or an enrolled person")
    if q.person_id is not None:
        if faces is None or not any(e.person_id == q.person_id for e in faces.people()):
            raise ValueError("that person is not enrolled")
        rows = repo.query_events(camera_id=q.camera_id, kinds=["track_end"], cls="person", since=q.since,
                                 until=q.until, limit=20000)
        query = {"person_id": q.person_id, "camera_id": q.camera_id, "since": q.since, "until": q.until}
        return SearchResult(query, _faces(rows, blobs, faces, person_id=q.person_id)[:cfg.search_top_n * 2])
    now = time.time() if now is None else now
    image = _decode(q.photo) if q.photo is not None else None
    if q.photo is not None and image is None:
        raise ValueError("that photo can't be read")

    if image is not None and faces is not None and faces.has_face(image):
        if not q.consent:
            raise ConsentRequired("searching with a photo of a person needs their consent")
        query = {"photo": "face", "camera_id": q.camera_id, "since": q.since, "until": q.until, "classes": ["person"]}
        rows = repo.query_events(camera_id=q.camera_id, kinds=["track_end"], cls="person", since=q.since,
                                 until=q.until, limit=20000)
        return SearchResult(query, _faces(rows, blobs, faces, image)[:cfg.search_top_n * 2])

    if image is not None:
        query = {"photo": "appearance", "looks_like": q.text, "not_like": None, "camera_id": q.camera_id,
                 "since": q.since, "until": q.until, "classes": list(q.classes)}
        target, against = embedder.image(image), None
    else:
        query = interpret(q, repo, llm, now, tz)
        target = embedder.text(query["looks_like"])
        against = embedder.text(query["not_like"]) if query["not_like"] else None

    rows = repo.query_events(camera_id=query["camera_id"], kinds=SEARCH_KINDS, since=query["since"],
                             until=query["until"], limit=20000)
    if query["classes"]:
        rows = [e for e in rows if e.kind == "track_end" and e.cls in query["classes"]]
    calibrated = hasattr(embedder, "probability")

    def score(v: np.ndarray) -> float | None:
        cos = float(np.dot(v, target))
        if image is not None:
            return cos
        if against is not None:  # "without a helmet" against "with a helmet": the closer caption wins
            diff = cos - float(np.dot(v, against))
            return diff if diff >= cfg.contrast_margin else None
        p = embedder.probability(cos) if calibrated else cos
        return p if p >= (cfg.search_min_probability if calibrated else cfg.looks_like_threshold) else None

    budget, scored, closest = [cfg.search_embed_on_demand], [], []
    for e in rows:
        v = _vector(e, repo, blobs, embedder, budget)
        if v is None:
            continue
        s = score(v)
        if s is None:
            closest.append((float(np.dot(v, target)), e))
        else:
            scored.append((s, e))
    scored.sort(key=lambda x: -x[0])
    problem = None
    unindexed = sum(1 for e in rows if e.embedding is None) - (cfg.search_embed_on_demand - budget[0])
    if unindexed > 0:
        problem = f"Still indexing {unindexed} recent moments, so some may be missing. Try again in a minute."
    if not scored and closest:
        problem = " ".join(x for x in ["No sure matches. These are the closest moments.", problem] if x)
        scored = sorted(closest, key=lambda x: -x[0])[:6]

    hits, seen_tracks, kept_scenes = [], set(), []
    for s, e in scored:
        if e.kind == "track_end":
            if (e.camera_id, e.track_id) in seen_tracks:
                continue
            seen_tracks.add((e.camera_id, e.track_id))
        elif any(c == e.camera_id and abs(t - e.ts) < cfg.search_dedupe_s for c, t in kept_scenes):
            continue
        else:
            kept_scenes.append((e.camera_id, e.ts))
        hits.append(_hit(e, blobs, s, "embedding"))
        if len(hits) >= cfg.search_top_n * 2:
            break
    if llm is not None and hits and query.get("looks_like"):
        keys = {e.id: e.keyframe_key for _, e in scored if e.keyframe_key}
        top, rest = hits[:cfg.search_top_n], hits[cfg.search_top_n:]
        hits = _confirm(top, query["looks_like"], blobs, llm, keys) + rest
    return SearchResult(query, hits, problem)
