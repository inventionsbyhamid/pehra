from dataclasses import dataclass

from pydantic import ValidationError

from watchful.agent.llm import LLM, AIError, AILimitReached, Msg
from watchful.agent.prompting import parse_json, render
from watchful.rules.engine import validate
from watchful.rules.presets import describe
from watchful.rules.signals import catalog
from watchful.rules.vocab import resolve_classes, vocabulary
from watchful.rules.watch import Watch
from watchful.vision.detect import COCO

ATTEMPTS = 3
SCHEMA = {"type": "object", "properties": {"watch": {"type": ["object", "null"]},
                                           "problem": {"type": ["string", "null"]}},
          "required": ["watch", "problem"]}


@dataclass
class CompileResult:
    watch: Watch | None
    problem: str | None


def _explain(e: Exception) -> str:
    if isinstance(e, ValidationError):
        return "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()[:5])
    return str(e).split("\n")[0]


def compile_watch(text: str, zones: list[str], llm: LLM, people: dict[str, str] | None = None) -> CompileResult:
    system = render("compile", catalog=catalog(), classes=COCO, vocabulary=sorted(vocabulary()), zones=zones,
                    presets=describe(), people=people or {})
    messages = [Msg("user", text)]
    last = ""
    for _ in range(ATTEMPTS):
        try:
            raw = llm.complete(system, messages, json_schema=SCHEMA)
        except (TimeoutError, AIError, AILimitReached):
            if last:
                break
            return CompileResult(None, "The AI could not be reached. Try again in a moment.")
        try:
            data = parse_json(raw)
        except ValueError as e:
            last = _explain(e)
            continue  # a cut off or garbled reply: ask again from the start rather than feeding it back
        try:
            if not data.get("watch"):
                return CompileResult(None, data.get("problem") or "That can't be watched yet.")
            w = data["watch"]
            subject = w.setdefault("subject", {})
            subject["classes"] = resolve_classes(subject.get("classes") or ["person"])
            if any(c.get("signal") == "looks_like" for c in w.get("conditions", [])):
                subject.pop("looks_like", None)  # the condition already checks appearance
            watch = Watch.model_validate(w)
            problems = validate(watch, set(zones))
            if problems:
                raise ValueError("; ".join(problems))
            return CompileResult(watch, None)
        except (ValueError, KeyError, TypeError, ValidationError) as e:
            last = _explain(e)
            messages = [Msg("user", text), Msg("assistant", raw),
                        Msg("user", f"That watch is not valid: {last}. Reply with fixed JSON.")]
    return CompileResult(None, f"I couldn't turn that into a watch: {last}")
