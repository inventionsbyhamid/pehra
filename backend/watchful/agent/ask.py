import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

import numpy as np

from watchful.agent.llm import LLM, AIError, AILimitReached, Msg
from watchful.agent.prompting import parse_json, plain, render
from watchful.agent.verifier import TraceStep
from watchful.tuning import AppearanceTuning
from watchful.vision.appearance import TrackSighting, link_tracks

PERSON_KINDS = ["track_start", "track_end", "enter", "exit"]
STEP_SCHEMA = {"type": "object", "properties": {"tool": {"type": "string"}, "args": {"type": "object"},
                                                "why": {"type": "string"}}, "required": ["tool", "args"]}
MAX_ITEMS = 8
CONFIDENCE = ["low", "medium", "high"]


@dataclass
class AskContext:
    repo: object
    blobs: object
    camera_id: str | None
    tz: str
    appearance: AppearanceTuning = field(default_factory=AppearanceTuning)
    images: dict[str, bytes] = field(default_factory=dict)
    track_groups: dict[int, tuple[str, bool]] = field(default_factory=dict)  # track to (group, ambiguous)


@dataclass
class AnswerItem:
    label: str
    start_ts: float | None
    end_ts: float | None
    thumbnail_url: str | None
    clip_url: str | None = None
    event_id: str | None = None


@dataclass
class Answer:
    text: str
    items: list[AnswerItem]
    confidence: Literal["high", "medium", "low"]
    trace: list[TraceStep] = field(default_factory=list)


def _clock(ts: float, tz: str) -> str:
    return datetime.fromtimestamp(ts, ZoneInfo(tz)).strftime("%a %H:%M:%S")


def events_tool(ctx: AskContext, zone=None, cls=None, kinds=None, since=None, until=None) -> tuple[str, list]:
    rows = ctx.repo.query_events(camera_id=ctx.camera_id, kinds=kinds, zone=zone, cls=cls, since=since,
                                 until=until, limit=200)
    if not rows:
        return "no events", []
    return "\n".join(f"{e.id} | {_clock(e.ts, ctx.tz)} (ts {e.ts:.1f}) | {e.kind} | {e.zone or '-'} | {e.cls} "
                     f"{e.track_id} | {e.person_label or ''}" for e in rows), []


def people_groups(ctx: AskContext, since=None, until=None) -> list[dict]:
    """People grouped by face and clothing, each with their zone visits; marks look alikes."""
    rows = ctx.repo.query_events(camera_id=ctx.camera_id, kinds=PERSON_KINDS, cls="person", since=since,
                                 until=until, limit=5000)
    tracks: dict[int, dict] = {}
    for e in rows:
        t = tracks.setdefault(e.track_id, {"first": e.ts, "last": e.ts, "sig": None, "label": None, "key": None,
                                           "visits": []})
        t["first"], t["last"] = min(t["first"], e.ts), max(t["last"], e.ts)
        t["sig"] = t["sig"] or e.signature
        t["label"] = t["label"] or e.person_label
        t["key"] = t["key"] or e.keyframe_key
        if e.kind in ("enter", "exit"):
            t["visits"].append({"kind": e.kind, "zone": e.zone, "ts": e.ts, "event_id": e.id})
    sightings = [TrackSighting(tid, t["first"], t["last"],
                               np.asarray(t["sig"], np.float32).reshape(30, 32) if t["sig"] else
                               np.zeros((30, 32), np.float32), t["key"], t["label"], t["label"])
                 for tid, t in tracks.items()]
    labels = ctx.repo.person_labels(ctx.camera_id) if ctx.camera_id else {}
    out = []
    for g in link_tracks(sightings, ctx.appearance):
        for tid in g.track_ids:
            ctx.track_groups[tid] = (g.group_id, g.ambiguous)
        out.append({"group_id": g.group_id, "label": labels.get(g.group_id) or g.label, "first_ts": g.first_ts,
                    "last_ts": g.last_ts, "track_ids": g.track_ids, "ambiguous": g.ambiguous,
                    "tracks": [{"track_id": m.track_id, "first_ts": m.first_ts, "last_ts": m.last_ts}
                               for m in g.members],
                    "thumbnail_key": g.thumbnail_key,
                    "visits": sorted((v for tid in g.track_ids for v in tracks[tid]["visits"]), key=lambda v: v["ts"])})
    return out


def people_tool(ctx: AskContext, since=None, until=None) -> tuple[str, list]:
    groups = people_groups(ctx, since, until)
    if not groups:
        return "nobody was seen", []
    lines = []
    for g in groups:
        flag = " LOOK ALIKE: may be confused with another person" if g["ambiguous"] else ""
        lines.append(f"{g['group_id']} ({g['label'] or 'unknown person'}) seen {_clock(g['first_ts'], ctx.tz)} to "
                     f"{_clock(g['last_ts'], ctx.tz)}, tracks {g['track_ids']}{flag}")
        lines += [f"    {v['kind']} {v['zone']} at {_clock(v['ts'], ctx.tz)} (ts {v['ts']:.1f}) [{v['event_id']}]"
                  for v in g["visits"]]
    return "\n".join(lines), []


def keyframes_tool(ctx: AskContext, event_ids: list[str]) -> tuple[str, list]:
    refs = []
    for eid in event_ids[:4]:
        e = ctx.repo.get_event(eid)
        if e and e.keyframe_key:
            ref = f"img{len(ctx.images) + 1}"
            try:
                ctx.images[ref] = ctx.blobs.get(e.keyframe_key)
            except (OSError, KeyError):
                continue
            refs.append(ref)
    return (f"images {', '.join(refs)}" if refs else "no images for those events"), refs


TOOLS = {
    "events": ("events(zone, cls, kinds, since, until): recorded events; kinds like enter, exit, track_start, "
               "fallen; times are ts numbers", events_tool),
    "people": ("people(since, until): people grouped by face and clothing, with their zone visits", people_tool),
    "keyframes": ("keyframes(event_ids): pictures of up to 4 events", keyframes_tool),
}


def _until(ctx: AskContext, e) -> float | None:
    """When the person left the zone they entered at e, from the matching exit event."""
    if e.kind != "enter":
        return None
    exits = [x for x in ctx.repo.query_events(camera_id=e.camera_id, kinds=["exit"], zone=e.zone, since=e.ts,
                                              until=e.ts + 86400, limit=500) if x.track_id == e.track_id]
    return exits[0].ts if exits else None


def _item(ctx: AskContext, e, label: str) -> AnswerItem:
    return AnswerItem(label, e.ts, _until(ctx, e), ctx.blobs.url(e.keyframe_key) if e.keyframe_key else None,
                      None, e.id)


def _ground(a: dict, ctx: AskContext) -> Answer:
    """Keeps only items that cite recorded events, takes their times from those events, and when the person may
    be confused with a look alike, lists the look alike's matching moment too and says so."""
    cited = []
    for it in (a.get("items") or [])[:MAX_ITEMS]:
        e = ctx.repo.get_event(str(it.get("event_id", "")))
        if e is not None:
            cited.append((str(it.get("label") or e.kind), e))
    if cited and any(e.track_id not in ctx.track_groups for _, e in cited if e.track_id is not None):
        lo, hi = min(e.ts for _, e in cited), max(e.ts for _, e in cited)
        people_groups(ctx, lo - 6 * 3600, hi + 6 * 3600)
    items = [_item(ctx, e, label) for label, e in cited]
    ambiguous = False
    for label, e in cited:
        group, unsure = ctx.track_groups.get(e.track_id, ("", False))
        if not unsure:
            continue
        ambiguous = True
        others = {t for t, (g, u) in ctx.track_groups.items() if u and g != group}
        near = [x for x in ctx.repo.query_events(camera_id=e.camera_id, kinds=[e.kind], zone=e.zone,
                                                 since=e.ts - 6 * 3600, until=e.ts + 6 * 3600, limit=500)
                if x.track_id in others and x.id not in {i.event_id for i in items}]
        if near:
            best = min(near, key=lambda x: abs(x.ts - e.ts))
            items.append(_item(ctx, best, "Someone who looks alike"))
    text = plain(str(a.get("text") or ""))
    confidence = a.get("confidence") if a.get("confidence") in CONFIDENCE else "medium"
    if ambiguous:
        confidence = "low"
        text += " Two people look alike here, so I am not sure which one it was."
    return Answer(text, items[:MAX_ITEMS], confidence)


def ask(question: str, camera_id: str | None, ctx: AskContext, llm: LLM, now: float, max_steps: int = 6,
        deadline_s: float = 45) -> Answer:
    start = time.monotonic()
    tools = "\n".join(f"* {d}" for d, _ in TOOLS.values()) + "\n* answer(text, items, confidence): finish"
    stamp = datetime.fromtimestamp(now, ZoneInfo(ctx.tz)).strftime("%A %Y-%m-%d %H:%M:%S")
    system = render("ask", tools=tools, now=f"{stamp} (ts {now:.0f})", tz=ctx.tz)
    messages = [Msg("user", question)]
    trace: list[TraceStep] = []
    for n in range(1, max_steps + 1):
        left = deadline_s - (time.monotonic() - start)
        if left <= 0:
            break
        t0 = time.monotonic()
        try:
            raw = llm.complete(system, messages, images=list(ctx.images.values())[-6:], json_schema=STEP_SCHEMA,
                               timeout_s=min(20.0, left))
            step = parse_json(raw)
            tool, args = step["tool"], step.get("args") or {}
        except (TimeoutError, AIError, AILimitReached, ValueError, KeyError):
            break
        if tool == "answer":
            trace.append(TraceStep(n, tool, args, step.get("why", ""), "", int((time.monotonic() - t0) * 1000)))
            answer = _ground(args, ctx)
            answer.trace = trace
            return answer
        if tool not in TOOLS:
            result = f"unknown tool {tool}"
        else:
            try:
                result, _ = TOOLS[tool][1](ctx, **args)
            except TypeError as e:
                result = f"tool failed: {e}"
        trace.append(TraceStep(n, tool, args, step.get("why", ""), result[:2000], int((time.monotonic() - t0) * 1000)))
        messages += [Msg("assistant", raw), Msg("user", f"Result:\n{result[:6000]}")]
    return Answer("I couldn't answer that right now. Try again in a moment.", [], "low", trace)
