import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Literal

from watchful.agent.compiler import mentions_age, neutral_text
from watchful.agent.llm import LLM, AIError, AILimitReached, Msg
from watchful.agent.prompting import parse_json, plain, render
from watchful.agent.tools import TERMINAL, TOOLS, ToolContext, describe_tools, run_tool
from watchful.rules.engine import Candidate

MAX_IMAGES = 6
GRADES = ("critical", "warning", "info")
CANNOT_JUDGE = "cannot judge"
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
    severity: str | None = None  # the agent's grade of a real moment: critical, warning or info
    reason: str = ""  # why that grade


def _fallback(c: Candidate, trace: list[TraceStep], why: str) -> Verdict:
    seen = "; ".join(c.evidence.get("reasons", [])) or "OpenCV flagged it"
    if c.severity == "critical":
        return Verdict("unverified", plain(f"{c.watch_name}: {seen}. Not checked by the AI ({why})."), trace)
    return Verdict("dismissed", f"Not verified ({why}).", trace)


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


def verify(c: Candidate, ctx: ToolContext, llm: LLM, max_steps: int = 6, deadline_s: float = 45) -> Verdict:
    if c.evidence.get("method") == "face":
        return Verdict("alert", f"{c.watch_name}: recognised by face.")
    if c.evidence.get("method") == "camera":
        seen = "; ".join(c.evidence.get("reasons", [])) or "the camera is not seeing properly"
        return Verdict("alert", plain(f"{c.watch_name}: {seen}."))
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
            return _fallback(c, trace, "out of time")
        images = [ctx.images[r] for r in list(ctx.images)[-MAX_IMAGES:]]
        t0 = time.monotonic()
        try:
            raw = llm.complete(system, messages, images=images, json_schema=SCHEMA, timeout_s=min(20.0, left))
        except AILimitReached:
            return _fallback(c, trace, "daily AI limit reached")
        except Exception:  # timeouts, refused connections, provider errors: the alert must not be lost
            return _fallback(c, trace, "the AI did not answer")
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
            if tool == "cannot_judge" or CANNOT_JUDGE in f"{summary} {why}".lower():
                # it needs an age, which is never judged: a person sees the moment, unchecked, and no age is stated
                return Verdict("unverified", plain(f"{c.watch_name}: the AI can't judge this, it would need an age."),
                               trace)
            if mentions_age(summary):
                summary = neutral_text(summary)  # the agent never states anyone's age or calls anyone a child
            status = {"raise_alert": "alert", "dismiss": "dismissed", "ask_human": "awaiting_human"}[tool]
            grade = str(args.get("severity") or "").strip().lower()
            if tool == "raise_alert" and grade in GRADES:
                return Verdict(status, summary, trace, grade, plain(str(args.get("reason") or "")))
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
