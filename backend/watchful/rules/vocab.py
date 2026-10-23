import json
from functools import lru_cache
from pathlib import Path

from watchful.vision.detect import COCO

DATA = Path(__file__).resolve().parents[1] / "data"


@lru_cache
def vocabulary() -> dict[str, list[str]]:
    return json.loads((DATA / "vocabulary.json").read_text())


def resolve_classes(words: list[str]) -> list[str]:
    """Everyday words to detector classes; raises ValueError for words it can't place."""
    out: list[str] = []
    for word in words:
        w = word.strip().lower()
        found = [w] if w in COCO else vocabulary().get(w) or vocabulary().get(w.rstrip("s"))
        if not found:
            raise ValueError(f"unknown thing to watch: {word}")
        out += [c for c in found if c not in out]
    return out
