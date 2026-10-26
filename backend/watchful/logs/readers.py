"""Readers a camera can have turned on in its settings (data/readers.json): plate reading attaches plate_text and
plate_colour to vehicle tracks (for the gate log and the attribute signal), staff register names enrolled staff by
face. The compiler's "Turn on plate reading" setup task points at the plates reader."""
import json
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data" / "readers.json"
PLATES, FACES = "plates", "faces"


@lru_cache
def readers() -> dict[str, dict]:
    return json.loads(DATA.read_text(encoding="utf-8"))


def known(names) -> list[str]:
    """The names given, checked; raises ValueError naming one that is not a reader."""
    out = []
    for name in names or []:
        if name not in readers():
            raise ValueError(f"no reader called {name}")
        if name not in out:
            out.append(name)
    return sorted(out)


def attributes_of(camera) -> list[str]:
    """What this camera's readers attach to tracks, for context_for."""
    return [a for name in getattr(camera, "readers", None) or [] for a in readers().get(name, {}).get("attributes", [])]


def has(camera, name: str) -> bool:
    return name in (getattr(camera, "readers", None) or [])


def needed(watch, camera) -> list[dict]:
    """The readers a watch's attribute conditions need that are off on this camera, for the card's "Turn on" setup
    task to point at the camera setting: [{"reader": "plates", "label": "Plate reading"}]."""
    if watch is None:
        return []
    wanted = {str(c.params.get("name") or "") for c in watch.conditions if c.signal == "attribute"}
    if any(c.signal == "inside_s" for c in watch.conditions):
        wanted |= set(readers()[PLATES]["attributes"])
    have = set(attributes_of(camera))
    return [{"reader": name, "label": r["label"]} for name, r in readers().items()
            if any(a in r["attributes"] and a not in have for a in wanted)]
