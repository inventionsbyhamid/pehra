#!/usr/bin/env python3
"""Fills the submission documents with measured numbers, so no number is ever copied by hand.

  python3 docs/fill.py            writes docs/<name>.md from each template docs/src/<name>.md, and the marked sections
                                  of README.md from docs/src/readme-<section>.md
  python3 docs/fill.py --check    writes nothing; lists the placeholders still waiting for a number, and any number in
                                  a template that comes from no file
  python3 docs/fill.py --check --strict   the same, and exits 1 while any is waiting or any number has no source

A placeholder is {{key}}, or {{key|bare}} for the number without its unit word (a table whose row names the unit).
docs/keys.json says where each key's value is: "from" names a results file and "path" the JSON path inside it.
  eval-<clip>     eval/results/eval-<clip>.json (eval/evaluate.py)
  plates-<name>   eval/results/plates-<name>.json (eval/plates.py)
  bench:<setup>   the eval/results/bench-*.json whose "setup" is that (eval/bench.py, eval/bench_aws.sh)
  aws, build      eval/results/aws.json, eval/results/build_measurements.json (each value there carries its unit)
  plans           backend/watchful/data/plans.json (prices in rupees, the dated dollar rate); a list there is indexed
                  by its items' id, as in plans.society.price_month_inr
  link.<name>     docs/links.json (the site, demo, repository and video addresses)
A key may hold * segments: it then serves every key with any word there, and {1}, {2} ... in its "from" and "path" (and
in its ratio parts) take the words in order: "eval.*.fps_*.*.recall" with path "runs.fps_{2}.watches.{3}.recall.value"
and from "eval-{1}". A key may instead be a ratio of two other keys, their product ("times", for a dollar amount in
rupees), or count a list ("op": "len").

Each number links to the file it came from, with its JSON path as the link title; so does "n/a" (a null in the file:
measured, nothing to count). A value not in its file yet reads "not measured yet", an address not set "not set yet".
A key that no table knows stops the run, so a typo never reaches a judge; so do two bench files of one setup.

A number with a unit (%, ms, s, MB, GB, fps, frames a second, x, times) or a currency sign written into a template
by hand has no source, and --check lists it. A setting or a design figure, which nobody measures, is written
{{say:2 s}}: it reads "2 s", and says plainly that it is stated, not measured. Standard library only."""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from datetime import date
from pathlib import Path

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.*:-]+)(\|bare)?\s*\}\}")
SAY = re.compile(r"\{\{\s*say:([^}]*)\}\}")
MEASURE = re.compile(r"(?:[$₹]\s?\d[\d,]*(?:\.\d+)?)|(?:(?<![\w.])\d[\d,]*(?:\.\d+)?\s?"
                     r"(?:%|ms\b|s\b|MB\b|GB\b|fps\b|frames a second\b|x\b|times\b))")
CODE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
SUFFIXES = (" frames a second", " per 100", " times", " ms", " MB", " s")  # what |bare drops
RESULTS = Path("eval") / "results"
PLANS = Path("backend") / "watchful" / "data" / "plans.json"
NOT_MEASURED, NOT_APPLICABLE, NOT_SET = "not measured yet", "n/a", "not set yet"
README_PREFIX = "readme-"  # docs/src/readme-<name>.md fills README.md between <!-- <name> start --> and end
ABSOLUTE = {"devpost.md"}  # pasted into Devpost, so its links need the repository's address
HEADER = "<!-- Made by docs/fill.py from docs/src/{name}. Edit that file, then run python3 docs/fill.py. -->\n\n"


def _trim(text: str) -> str:
    return text.rstrip("0").rstrip(".") if "." in text else text


def _plain(v: float) -> str:
    """Enough digits to read, no more: 1,653 and 88 and 5.6 and 0.25."""
    if abs(v) >= 10:
        return f"{round(v):,}"
    return _trim(f"{v:.1f}") if abs(v) >= 1 else _trim(f"{v:.2f}")


def _money(v: float) -> str:
    return f"{v:,.2f}" if abs(v) >= 1 else f"{v:.3g}"


def _about(v: float) -> int:
    """As the site's aboutDollars: whole units, half up."""
    return math.floor(v + 0.5)


def _rough(v: float) -> int:
    """An estimate: whole units, two significant figures from 100 up (2,800, 1,60,000)."""
    if v < 100:
        return math.floor(v + 0.5)
    step = 10 ** (math.floor(math.log10(v)) - 1)
    return math.floor(v / step + 0.5) * step


def _lakh(n: int) -> str:
    """Indian digit grouping: 7,990 and 1,11,793."""
    head, tail = str(n)[:-3], str(n)[-3:]
    groups = []
    while head:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return ",".join([*groups, tail])


def _day(v: str) -> str:
    d = date.fromisoformat(v[:10])
    return f"{d.day} {d:%B %Y}"


def _interval(v) -> str:
    lo, hi = v
    return f"{round(100 * lo)}% to {round(100 * hi)}%"


UNITS = {
    "int": lambda v: f"{round(v):,}",
    "num": lambda v: _trim(f"{v:,.2f}"),
    "pct": lambda v: f"{round(100 * v)}%",
    "interval": _interval,
    "ms": lambda v: f"{_plain(v)} ms",
    "s": lambda v: f"{_plain(v)} s",
    "mb": lambda v: f"{round(v):,} MB",
    "fps": lambda v: f"{_plain(v)} frames a second",
    "per100": lambda v: f"{_trim(f'{v:.1f}')} per 100",
    "times": lambda v: f"{v:.2f} times",
    "usd": lambda v: f"${_money(v)}",
    "inr": lambda v: f"₹{_money(v)}",
    "inr_whole": lambda v: f"₹{_lakh(round(v))}",
    "inr_about": lambda v: f"₹{_lakh(_rough(v))}",
    "usd_about": lambda v: f"${_about(v):,}",
    "day": _day,
    "bool": lambda v: "yes" if v else "no",
    "text": str,
}


class Value:
    """A resolved placeholder: its words, and for a number the file and JSON path it came from."""

    def __init__(self, status: str, text: str, raw=None, file: Path | None = None, title: str | None = None,
                 unknown: tuple[str, ...] = ()):
        self.status, self.text, self.raw, self.file, self.title = status, text, raw, file, title
        self.unknown = unknown  # for a ratio: the parts no table knows


def leaves(tree, prefix: str = ""):
    """(path, leaf) for every measured value in build_measurements.json: a dict that holds "value"."""
    if isinstance(tree, dict):
        if "value" in tree:
            yield prefix, tree
            return
        for name, sub in tree.items():
            yield from leaves(sub, f"{prefix}.{name}" if prefix else name)


def _walk(data, path: str):
    for part in path.split("."):
        if data is None:  # measured, and nothing there (no false alarm, so no first false alarm)
            return None
        if isinstance(data, list) and part.isdigit():
            data = data[int(part)] if int(part) < len(data) else {}
            continue
        if isinstance(data, list):  # a list of items with ids, as plans.json's plans
            data = {item.get("id"): item for item in data if isinstance(item, dict)}
        if not isinstance(data, dict) or part not in data:
            raise KeyError(path)
        data = data[part]
    return data


def _format(raw, unit: str) -> str:
    if isinstance(raw, bool) and unit != "bool":
        return UNITS["bool"](raw)
    if unit == "interval":
        return _interval(raw) if isinstance(raw, (list, tuple)) and len(raw) == 2 else str(raw)
    if unit not in ("text", "bool", "day") and not isinstance(raw, (int, float)):
        return str(raw)
    return UNITS[unit](raw)


def _json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise SystemExit(f"cannot read {path}: {e}") from None


def _fill_in(text, words: list[str]):
    if isinstance(text, str):
        return re.sub(r"\{(\d+)\}", lambda m: words[int(m.group(1)) - 1], text)
    if isinstance(text, list):
        return [_fill_in(t, words) for t in text]
    return text


class Sources:
    def __init__(self, root: Path):
        self.root = root
        results = root / RESULTS
        self.files: dict[str, tuple[Path, object]] = {}
        for path in sorted(results.glob("*.json")) if results.is_dir() else []:
            data = _json(path)
            if path.name.startswith("bench-"):
                setup = data.get("setup") if isinstance(data, dict) else None
                name = f"bench:{setup or re.sub(r'[^A-Za-z0-9]+', '_', path.stem[6:])}"
                if name in self.files:
                    raise SystemExit(f"{self.files[name][0].name} and {path.name} are both setup {name[6:]}: keep one")
            else:
                name = {"build_measurements": "build"}.get(path.stem, path.stem)
            self.files[name] = (path, data)
        if (root / PLANS).exists():
            self.files["plans"] = (root / PLANS, _json(root / PLANS))
        docs = root / "docs"
        self.keys = _json(docs / "keys.json") if (docs / "keys.json").exists() else {}
        self.links = _json(docs / "links.json") if (docs / "links.json").exists() else {}

    def spec(self, key: str) -> dict | None:
        """The key's own entry, else the most specific * entry that serves it, with its words filled in."""
        if key in self.keys:
            return self.keys[key]
        parts, best = key.split("."), None
        for pattern, spec in self.keys.items():
            pat = pattern.split(".")
            if "*" not in pat or len(pat) != len(parts):
                continue
            words = []
            for p, k in zip(pat, parts):
                if p == "*":
                    words.append(k)
                elif "*" in p and re.fullmatch(re.escape(p).replace(r"\*", "(.+)"), k):
                    words.append(re.fullmatch(re.escape(p).replace(r"\*", "(.+)"), k).group(1))
                elif p != k:
                    break
            else:
                if best is None or len(words) < best[0]:
                    best = (len(words), {k: _fill_in(v, words) for k, v in spec.items()})
        return best[1] if best else None

    def value(self, key: str) -> Value:
        head, _, rest = key.partition(".")
        if head == "link":
            if rest not in self.links:
                return Value("unknown", key)
            address = self.links[rest]
            return Value("ok", address) if address else Value("missing", NOT_SET)
        if head == "build":
            return self._build(key, rest)
        spec = self.spec(key)
        if spec is None:
            return Value("unknown", key)
        unit = spec.get("unit", "text")
        if "ratio" in spec:
            return self._pair(spec["ratio"], unit, "over")
        if "times" in spec:
            return self._pair(spec["times"], unit, "times")
        source = spec.get("from", head)
        if source not in self.files:
            return Value("missing", NOT_MEASURED)
        path, data = self.files[source]
        try:
            raw = _walk(data, spec["path"])
        except KeyError:
            return Value("missing", NOT_MEASURED)
        if spec.get("op") == "len":
            raw = len(raw) if isinstance(raw, (list, dict)) else None
        if raw is None:
            return Value("na", NOT_APPLICABLE, None, path, spec["path"])
        return Value("ok", _format(raw, unit), raw, path, spec["path"])

    def _build(self, key: str, path: str) -> Value:
        if "build" not in self.files:
            return Value("unknown", key)
        file, data = self.files["build"]
        try:
            leaf = _walk(data, path)
        except KeyError:
            return Value("unknown", key)
        if not isinstance(leaf, dict) or "value" not in leaf:
            return Value("unknown", key)
        if leaf["value"] is None:
            return Value("na", NOT_APPLICABLE, None, file, path)
        return Value("ok", _format(leaf["value"], leaf.get("unit", "text")), leaf["value"], file, path)

    def _pair(self, pair: list[str], unit: str, op: str) -> Value:
        top, bottom = (self.value(k) for k in pair)
        unknown = tuple(k for k, part in zip(pair, (top, bottom)) if part.status == "unknown")
        if unknown:
            return Value("unknown", ", ".join(unknown), unknown=unknown)
        if "missing" in (top.status, bottom.status):
            return Value("missing", NOT_MEASURED)
        if top.status == "na" or bottom.status == "na" or not isinstance(top.raw, (int, float)) \
                or not isinstance(bottom.raw, (int, float)) or (op == "over" and not bottom.raw):
            return Value("na", NOT_APPLICABLE)
        raw = top.raw / bottom.raw if op == "over" else top.raw * bottom.raw
        title = f"{top.title} in {top.file.name} {op} {bottom.title} in {bottom.file.name}"
        return Value("ok", _format(raw, unit), raw, top.file, title)


def unsourced(template: str) -> list[str]:
    """Numbers with a unit or a currency sign written into a template by hand: not from a file, not marked stated.
    Placeholders, {{say:...}} and code are left out."""
    text = CODE.sub(" ", SAY.sub(" ", PLACEHOLDER.sub(" ", template)))
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    return [m.group(0).strip() for m in MEASURE.finditer(text)]


def _href(file: Path, out: Path, root: Path, repo: str | None) -> str:
    if repo:
        return f"{repo.rstrip('/')}/blob/main/{file.relative_to(root).as_posix()}"
    return Path(os.path.relpath(file, out.parent)).as_posix()


def render(text: str, sources: Sources, out: Path, root: Path, seen: dict) -> str:
    repo = sources.links.get("repo") if out.name in ABSOLUTE else None

    def one(match):
        key, bare = match.group(1), bool(match.group(2))
        v = sources.value(key)
        seen.setdefault(v.status, set()).update(v.unknown or (key,))
        words = v.text
        if bare and v.status == "ok":
            words = next((words[:-len(s)] for s in SUFFIXES if words.endswith(s)), words)
        if v.status in ("ok", "na") and v.file is not None:
            return f'[{words}]({_href(v.file, out, root, repo)} "{v.title}")'
        return words

    return PLACEHOLDER.sub(one, SAY.sub(lambda m: m.group(1).strip(), text))


def _section(readme: str, name: str, body: str) -> str:
    start, end = f"<!-- {name} start -->\n", f"<!-- {name} end -->"
    if start not in readme or end not in readme:
        raise SystemExit(f"README.md has no '{start.strip()}' ... '{end}' markers")
    head, rest = readme.split(start, 1)
    return head + start + body + end + rest.split(end, 1)[1]


def fill(root: Path, check: bool = False) -> dict:
    """Fills every template under docs/src; returns what was written, which keys were missing or unknown, and the
    numbers with no source. With check, writes nothing."""
    root = Path(root)
    sources = Sources(root)
    seen: dict[str, set] = {}
    outputs: dict[Path, str] = {}
    bare: dict[str, list[str]] = {}
    readme_path = root / "README.md"
    readme = readme_path.read_text() if readme_path.exists() else None
    for src in sorted((root / "docs" / "src").glob("*.md")):
        text = src.read_text()
        if found := unsourced(text):
            bare[src.name] = found
        if src.name.startswith(README_PREFIX):
            if readme is None:
                raise SystemExit("README.md is missing")
            readme = _section(readme, src.stem[len(README_PREFIX):], render(text, sources, readme_path, root, seen))
            outputs[readme_path] = readme
        else:
            out = root / "docs" / src.name
            outputs[out] = HEADER.format(name=src.name) + render(text, sources, out, root, seen)
    unknown = sorted(seen.get("unknown", ()))
    summary = {"written": [], "filled": sorted(seen.get("ok", ())), "missing": sorted(seen.get("missing", ())),
               "na": sorted(seen.get("na", ())), "unknown": unknown, "unsourced": bare}
    if check:
        return summary
    if unknown:
        raise SystemExit("unknown placeholders, nothing written: " + ", ".join(unknown))
    for path, text in outputs.items():
        path.write_text(text)
        summary["written"].append(str(path.relative_to(root)))
    return summary


def main(argv: list[str] | None = None) -> None:
    args = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    args.add_argument("--root", default=str(Path(__file__).resolve().parents[1]), help="the repository")
    args.add_argument("--check", action="store_true", help="write nothing; list what is still waiting")
    args.add_argument("--strict", action="store_true", help="exit 1 while anything waits or has no source")
    opts = args.parse_args(argv)
    summary = fill(Path(opts.root), check=opts.check)
    for path in summary["written"]:
        print(f"wrote {path}")
    print(f"{len(summary['filled'])} values filled, {len(summary['missing'])} not measured or not set yet, "
          f"{len(summary['na'])} n/a")
    for key in summary["missing"]:
        print(f"  waiting: {key}")
    for name, found in summary["unsourced"].items():
        print(f"  no source in docs/src/{name}: " + ", ".join(found))
    if summary["unknown"]:
        print("unknown placeholders: " + ", ".join(summary["unknown"]), file=sys.stderr)
        sys.exit(1)
    if opts.strict and (summary["missing"] or summary["unsourced"]):
        sys.exit(1)


if __name__ == "__main__":
    main()
