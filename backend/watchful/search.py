"""Find a person in recorded footage: by an enrolled person, a photo (with consent) or a description."""
import json
from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np

from watchful.agent.llm import LLM, AIError, AILimitReached, Msg
from watchful.agent.prompting import parse_json
from watchful.tuning import SignalTuning
from watchful.vision.faces import ConsentRequired, FaceIndex


@dataclass
class PersonQuery:
    person_id: str | None = None
    photo: bytes | None = None
    description: str | None = None
    camera_id: str | None = None
    since: float | None = None
    until: float | None = None
    consent: bool = False


@dataclass
class Sighting:
    camera_id: str
    track_id: int
    start_ts: float
    end_ts: float
    thumbnail_url: str | None
    clip_url: str | None
    score: float
    method: Literal["face", "description"]


def _decode(data: bytes) -> np.ndarray | None:
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def _tracks(repo, q: PersonQuery) -> dict[int, dict]:
    rows = repo.query_events(camera_id=q.camera_id, kinds=["track_start", "track_end"], cls="person",
                             since=q.since, until=q.until, limit=5000)
    tracks: dict[int, dict] = {}
    for e in rows:
        t = tracks.setdefault(e.track_id, {"camera_id": e.camera_id, "start": e.ts, "end": e.ts, "key": None,
                                           "embedding": None})
        t["start"], t["end"] = min(t["start"], e.ts), max(t["end"], e.ts)
        t["key"] = e.keyframe_key or t["key"]
        t["embedding"] = e.embedding or t["embedding"]
    return tracks


def _by_face(q: PersonQuery, tracks: dict, blobs, faces: FaceIndex) -> list[tuple[int, float]]:
    probe = type(faces)(cfg=faces.cfg, settings=faces.settings)
    if q.photo is not None:
        if not q.consent:
            raise ConsentRequired("consent is required to search with a photo")
        probe.enroll("query", "photo", _decode(q.photo), consent=True)
    else:
        probe.load([e for e in faces.people() if e.person_id == q.person_id])
    found = []
    for tid, t in tracks.items():
        img = _decode(blobs.get(t["key"])) if t["key"] else None
        if img is None:
            continue
        m = probe.identify(img, (0, 0, img.shape[1], img.shape[0]))
        if m:
            found.append((tid, m.score))
    return found


def _by_description(q: PersonQuery, tracks: dict, blobs, embedder, llm: LLM | None,
                    cfg: SignalTuning) -> list[tuple[int, float]]:
    target = embedder.text(q.description)
    scored = []
    for tid, t in tracks.items():
        vec = np.asarray(t["embedding"], np.float32) if t["embedding"] else None
        if vec is None and t["key"]:
            img = _decode(blobs.get(t["key"]))
            vec = embedder.image(img) if img is not None else None
        if vec is not None:
            score = float(np.dot(vec, target))
            if score >= cfg.looks_like_threshold:
                scored.append((score, tid))
    best = sorted(scored, reverse=True)[:cfg.search_top_n]
    if not best or llm is None:
        return [(tid, s) for s, tid in best]
    images = [blobs.get(tracks[tid]["key"]) for _, tid in best]
    prompt = (f"These {len(images)} pictures are numbered from 1 in order. Which show {q.description}? "
              'Reply with JSON {"matches": [numbers]}.')
    try:
        reply = parse_json(llm.complete("You check people in CCTV pictures.", [Msg("user", prompt)], images=images,
                                        json_schema={"type": "object", "properties": {"matches": {
                                            "type": "array", "items": {"type": "integer"}}}}))
        keep = {int(i) for i in reply.get("matches", [])}
    except (TimeoutError, AIError, AILimitReached, ValueError, json.JSONDecodeError):
        return [(tid, s) for s, tid in best]
    return [(tid, s) for i, (s, tid) in enumerate(best, start=1) if i in keep]


def find_person(q: PersonQuery, repo, blobs, faces: FaceIndex | None = None, embedder=None, llm: LLM | None = None,
                cfg: SignalTuning = SignalTuning()) -> list[Sighting]:
    if sum(x is not None for x in (q.person_id, q.photo, q.description)) != 1:
        raise ValueError("search with exactly one of a person, a photo or a description")
    tracks = _tracks(repo, q)
    if q.description is not None:
        if embedder is None:
            raise ValueError("description search needs the embedding model")
        found, method = _by_description(q, tracks, blobs, embedder, llm, cfg), "description"
    else:
        if faces is None:
            raise ValueError("face search needs the face models")
        found, method = _by_face(q, tracks, blobs, faces), "face"
    out = [Sighting(tracks[tid]["camera_id"], tid, tracks[tid]["start"], tracks[tid]["end"],
                    blobs.url(tracks[tid]["key"]) if tracks[tid]["key"] else None, None, score, method)
           for tid, score in found]
    return sorted(out, key=lambda s: s.start_ts)
