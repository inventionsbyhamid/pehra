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
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in the reply")
    data = json.loads(raw[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("the reply is not a JSON object")
    return data
