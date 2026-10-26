import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Literal

from watchful.agent.compiler import Names, about_age, age_left_out, age_safe_summary, mentions_age
from watchful.agent.llm import LLM, AIError, AILimitReached, Msg
from watchful.agent.prompting import parse_json, plain, render
from watchful.agent.tools import TERMINAL, TOOLS, ToolContext, describe_tools, run_tool
from watchful.rules.engine import Candidate

MAX_IMAGES = 6
GRADES = ("critical", "warning", "info")
SCHEMA = {"type": "object", "properties": {"tool": {"type": "string", "enum": list(TOOLS) + list(TERMINAL)},
                                           "args": {"type": "object"}, "why": {"type": "string"}},
          "required": ["tool", "args", "why"]}


@dataclass
class TraceStep:
    step: int
    tool: str
    args: dict
    why: str
    result: str
    ms: int


@dataclass
class Verdict:
    # measured: the camera's own state or an enrolled face, measured by OpenCV; no AI looks at those
    status: Literal["alert", "dismissed", "awaiting_human", "unverified", "measured"]
    summary: str
    trace: list[TraceStep] = field(default_factory=list)
    severity: str | None = None  # the agent's grade of a real moment: critical, warning or info
    reason: str = ""  # why that grade


LIMIT, UNREACHED = "daily limit reached", "the AI could not be reached"
MEASURED = ("camera", "face")  # Candidate evidence methods OpenCV measures itself; no AI checks them


def _fallback(c: Candidate, trace: list[TraceStep], why: str) -> Verdict:
    """A moment the AI could not check is still filed, whatever its severity, saying why it was not checked: past
    the daily cap or in an outage a warning or a note must not vanish."""
    seen = "; ".join(c.evidence.get("reasons", [])) or "OpenCV flagged it"
    return Verdict("unverified", plain(f"{c.watch_name}: {seen}. Not checked by the AI: {why}."), trace)


def _opening(c: Candidate, ctx: ToolContext) -> tuple[str, list[str]]:
    refs = []
    for key in c.keyframe_keys:
        try:
            refs.append(ctx.add_image(ctx.blobs.get(key)))
        except (OSError, KeyError):
            pass
    refs += [ctx.add_image(f) for f in ctx.frames(c.ts - 1.0, c.ts + 1.0, 2)]
    crops = len([k for k in c.keyframe_keys])
    lines = [f"Watch: {c.watch_name} (severity {c.severity})", f"Question: {c.verify or 'Is this really happening?'}",
             "OpenCV saw: " + ("; ".join(c.evidence.get("reasons", [])) or "a match"),
             f"Signals: {c.evidence.get('signals', {})}",
             "The flagged moment is time 0; tool times are seconds before (negative) or after it.",
             f"Images: {', '.join(refs) or 'none'}. The first {crops} are close crops of the flagged object; the "
             "rest are frames around the moment with the zones outlined in yellow and the object boxed in red."]
    return "\n".join(lines), refs


def _places(ctx: ToolContext) -> Names:
    """The society's zone and line names (this camera's among them) and its staff names: places and names, never
    read as an age in a summary."""
    try:
        places = sorted({z.name for cam in ctx.repo.list_cameras() for z in cam.zones})
    except Exception:  # a summary is still checked without them; it may then be replaced when it names a place
        places = []
    try:
        people = [e.name for e in ctx.repo.list_enrollments()]
    except Exception:
        people = []
    return Names(places, people)


def scrub(trace: list[TraceStep], about) -> None:
    """The stored trace says nothing about anyone's age either: each text in it that is about age is left out."""
    def clean(value):
        if isinstance(value, str):
            return age_left_out() if about(value) else value
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value
    for t in trace:
        t.why, t.args = clean(t.why), clean(t.args)


def verify(c: Candidate, ctx: ToolContext, llm: LLM, max_steps: int = 6, deadline_s: float = 45,
           meaning=None) -> Verdict:
    """The AI checks one flagged moment. What it says about anyone's age never reaches a person: a summary or reason
    about age (by the words, or by the meaning check with place names taken out) becomes the plain verdict, and the
    trace's texts about age are left out (by the words; its last step's words with the verdict's). `meaning` is the
    meaning check; None is the process's own."""
    places: Names | None = None

    def about(words: str) -> bool:
        nonlocal places
        if places is None:
            places = _places(ctx)
        return about_age(words, places, meaning)

    v = _verify(c, ctx, llm, max_steps, deadline_s, about)
    # the trace by the words (the meaning check costs a text tower run each); its final words follow the verdict's
    names = places if places is not None else _places(ctx)
    scrub(v.trace, lambda words: mentions_age(words, names))
    if v.trace and v.trace[-1].tool in TERMINAL and v.summary == age_safe_summary(v.status, c.watch_name):
        last = v.trace[-1]
        last.args = {k: age_left_out() if k in ("summary", "reason", "question") else a for k, a in last.args.items()}
        last.why = age_left_out() if about(last.why) else last.why
    return v


def _verify(c: Candidate, ctx: ToolContext, llm: LLM, max_steps: int, deadline_s: float, about) -> Verdict:
    if c.evidence.get("method") == "face":
        return Verdict("measured", f"{c.watch_name}: recognised by face.")
    if c.evidence.get("method") == "camera":
        seen = "; ".join(c.evidence.get("reasons", [])) or "the camera is not seeing properly"
        return Verdict("measured", plain(f"{c.watch_name}: {seen}."))
    llm = llm.check() if hasattr(llm, "check") else llm  # one check counts once against the plan, whatever its steps
    start = time.monotonic()
    system = render("verify", tools=describe_tools())
    text, _ = _opening(c, ctx)
    messages = [Msg("user", text)]
    trace: list[TraceStep] = []
    bad = 0
    for n in range(1, max_steps + 1):
        left = deadline_s - (time.monotonic() - start)
        if left <= 0:
            return _fallback(c, trace, "the AI took too long")
        images = [ctx.images[r] for r in list(ctx.images)[-MAX_IMAGES:]]
        t0 = time.monotonic()
        try:
            raw = llm.complete(system, messages, images=images, json_schema=SCHEMA, timeout_s=min(20.0, left))
        except AILimitReached:
            return _fallback(c, trace, LIMIT)
        except Exception:  # timeouts, refused connections, provider errors: the alert must not be lost
            return _fallback(c, trace, UNREACHED)
        try:
            step = parse_json(raw)
            tool, args, why = str(step["tool"]), step.get("args"), str(step.get("why", ""))
            args = args if isinstance(args, dict) else {}
        except (ValueError, KeyError):
            bad += 1
            if bad >= 2:
                return _fallback(c, trace, "the AI reply was not readable")
            messages += [Msg("assistant", raw), Msg("user", "Reply with one JSON object for one step.")]
            continue
        bad = 0
        if tool in TERMINAL:
            summary = plain(str(args.get("summary") or args.get("reason") or args.get("question") or why))
            trace.append(TraceStep(n, tool, args, why, "", int((time.monotonic() - t0) * 1000)))
            if tool == "cannot_judge":
                # it needs an age, which is never judged: a person sees the moment, unchecked, and no age is stated
                return Verdict("unverified", plain(f"{c.watch_name}: the AI can't judge this, it would need an age."),
                               trace)
            status = {"raise_alert": "alert", "dismiss": "dismissed", "ask_human": "awaiting_human"}[tool]
            grade = str(args.get("severity") or "").strip().lower()
            reason = plain(str(args.get("reason") or "")) if tool == "raise_alert" and grade in GRADES else ""
            if about(summary) or about(reason):  # fail closed: the verdict stands, the words about anyone go
                summary, reason = age_safe_summary(status, c.watch_name), ""
            if reason or (tool == "raise_alert" and grade in GRADES):
                return Verdict(status, summary, trace, grade, reason)
            return Verdict(status, summary, trace)
        result = run_tool(ctx, tool, args)
        trace.append(TraceStep(n, tool, args, why, result.text, int((time.monotonic() - t0) * 1000)))
        note = f"Result: {result.text}" + (f" New images: {', '.join(result.images)}." if result.images else "")
        messages += [Msg("assistant", raw), Msg("user", note)]
    return _fallback(c, trace, "the AI took too long")


def verify_many(candidates: list[Candidate], ctx_for: Callable[[Candidate], ToolContext], llm: LLM,
                parallel: int = 6) -> list[Verdict]:
    with ThreadPoolExecutor(max(1, parallel)) as ex:
        return list(ex.map(lambda c: verify(c, ctx_for(c), llm), candidates))
