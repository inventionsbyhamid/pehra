import json
import re
from functools import lru_cache
from pathlib import Path

PROMPTS = Path(__file__).resolve().parent / "prompts"


@lru_cache
def _template(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text()


def render(name: str, **values) -> str:
    text = _template(name)
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value if isinstance(value, str) else json.dumps(value))
    return text


def parse_json(raw: str) -> dict:
    """The first JSON object in a reply, tolerating code fences and stray text."""
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    start = raw.find("{")
    if start < 0:
        raise ValueError("no JSON object in the reply")
    data, _ = json.JSONDecoder().raw_decode(raw[start:])
    if not isinstance(data, dict):
        raise ValueError("the reply is not a JSON object")
    return data


def plain(text: str) -> str:
    """User facing AI text without dashes: ranges read 'to', joined words are spaced, breaks become commas."""
    text = re.sub(r"(\d)\s*[-\u2013]\s*(\d)", r"\1 to \2", text)
    text = re.sub(r"\s*[\u2014\u2013]\s*", ", ", text)
    text = re.sub(r"(\w)-(\w)", r"\1 \2", text)
    return re.sub(r"\s{2,}", " ", text).strip()
