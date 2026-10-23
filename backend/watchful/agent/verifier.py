import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Literal

from watchful.agent.llm import LLM, AIError, AILimitReached, Msg
from watchful.agent.prompting import parse_json, render
from watchful.agent.tools import TERMINAL, TOOLS, ToolContext, describe_tools, run_tool
from watchful.rules.engine import Candidate

MAX_IMAGES = 6
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
    status: Literal["alert", "dismissed", "awaiting_human", "unverified"]
    summary: str
    trace: list[TraceStep] = field(default_factory=list)


def _fallback(c: Candidate, trace: list[TraceStep], why: str) -> Verdict:
    seen = "; ".join(c.evidence.get("reasons", [])) or "OpenCV flagged it"
    if c.severity == "critical":
        return Verdict("unverified", f"{c.watch_name}: {seen}. Not checked by the AI ({why}).", trace)
    return Verdict("dismissed", f"Not verified ({why}).", trace)


def _opening(c: Candidate, ctx: ToolContext) -> tuple[str, list[str]]:
    refs = []
    for key in c.keyframe_keys:
        try:
            refs.append(ctx.add_image(ctx.blobs.get(key)))
        except (OSError, KeyError):
            pass
    refs += [ctx.add_image(f) for f in ctx.frames(c.ts - 1.0, c.ts + 1.0, 2)]
    lines = [f"Watch: {c.watch_name}", f"Question: {c.verify or 'Is this really happening?'}",
             f"Time: {c.ts:.1f}", f"Track: {c.track_id}",
             "OpenCV saw: " + ("; ".join(c.evidence.get("reasons", [])) or "a match"),
             f"Signals: {c.evidence.get('signals', {})}",
             f"Images: {', '.join(refs) or 'none'} (the first is the object's best crop when there is one)"]
    return "\n".join(lines), refs


def verify(c: Candidate, ctx: ToolContext, llm: LLM, max_steps: int = 6, deadline_s: float = 45) -> Verdict:
    if c.evidence.get("method") == "face":
        return Verdict("alert", f"{c.watch_name}: recognised by face.")
    start = time.monotonic()
    system = render("verify", tools=describe_tools())
    text, _ = _opening(c, ctx)
    messages = [Msg("user", text)]
    trace: list[TraceStep] = []
    bad = 0
    for n in range(1, max_steps + 1):
        left = deadline_s - (time.monotonic() - start)
        if left <= 0:
            return _fallback(c, trace, "out of time")
        images = [ctx.images[r] for r in list(ctx.images)[-MAX_IMAGES:]]
        t0 = time.monotonic()
        try:
            raw = llm.complete(system, messages, images=images, json_schema=SCHEMA, timeout_s=min(20.0, left))
        except (TimeoutError, AIError, AILimitReached) as e:
            return _fallback(c, trace, "the AI did not answer" if not isinstance(e, AILimitReached) else
                             "daily AI limit reached")
        try:
            step = parse_json(raw)
            tool, args, why = step["tool"], step.get("args") or {}, step.get("why", "")
        except (ValueError, KeyError):
            bad += 1
            if bad >= 2:
                return _fallback(c, trace, "the AI reply was not readable")
            messages += [Msg("assistant", raw), Msg("user", "Reply with one JSON object for one step.")]
            continue
        bad = 0
        if tool in TERMINAL:
            summary = args.get("summary") or args.get("reason") or args.get("question") or why
            trace.append(TraceStep(n, tool, args, why, "", int((time.monotonic() - t0) * 1000)))
            status = {"raise_alert": "alert", "dismiss": "dismissed", "ask_human": "awaiting_human"}[tool]
            return Verdict(status, summary, trace)
        result = run_tool(ctx, tool, args)
        trace.append(TraceStep(n, tool, args, why, result.text, int((time.monotonic() - t0) * 1000)))
        note = f"Result: {result.text}" + (f" New images: {', '.join(result.images)}." if result.images else "")
        messages += [Msg("assistant", raw), Msg("user", note)]
    return _fallback(c, trace, "step limit reached")


def verify_many(candidates: list[Candidate], ctx_for: Callable[[Candidate], ToolContext], llm: LLM,
                parallel: int = 6) -> list[Verdict]:
    with ThreadPoolExecutor(max(1, parallel)) as ex:
        return list(ex.map(lambda c: verify(c, ctx_for(c), llm), candidates))
