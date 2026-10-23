import json
from functools import lru_cache

from watchful.rules.vocab import DATA
from watchful.rules.watch import Watch


@lru_cache
def _presets() -> dict:
    return json.loads((DATA / "presets.json").read_text())


def preset_names() -> list[str]:
    return list(_presets())


def describe() -> list[dict]:
    return [{"preset": k, "description": v["description"], "watch": v["watch"]} for k, v in _presets().items()]


def _fill(node, values: dict):
    if isinstance(node, dict):
        return {k: _fill(v, values) for k, v in node.items()}
    if isinstance(node, list):
        out = []
        for item in node:
            if isinstance(item, str) and item.startswith("$") and item[1:] not in values:
                continue
            out.append(_fill(item, values))
        return out
    if isinstance(node, str):
        if node.startswith("$") and node[1:] in values:
            return values[node[1:]]
        for k, v in values.items():
            node = node.replace(f"${k}", str(v))
        return node
    return node


def preset(name: str, **fill) -> Watch:
    p = _presets()[name]
    values = {**p.get("defaults", {}), **{k: v for k, v in fill.items() if v is not None}}
    return Watch.model_validate(_fill(p["watch"], values))
