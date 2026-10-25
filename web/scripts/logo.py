"""Writes public/logo.svg and public/favicon.svg: the mark, and the mark beside the name from brand.json set in
Atkinson Hyperlegible Next Bold, drawn as paths so the logo looks the same everywhere. Run it again after a rename.
It needs fontTools and brotli, which the app does not: python -m venv /tmp/logo && /tmp/logo/bin/pip install
fonttools brotli && /tmp/logo/bin/python web/scripts/logo.py"""
import json
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

WEB = Path(__file__).resolve().parents[1]
brand = json.loads((WEB.parent / "brand.json").read_text())
INK = "#15201d"
HEIGHT = 48  # the mark is HEIGHT square; the name's capitals are CAP of it
CAP = 0.54
GAP = 12


def mark(colour: str, scale: float = 1.0) -> str:
    s = f' transform="scale({scale:g})"' if scale != 1 else ""
    return (
        f'<g{s}><rect width="32" height="32" rx="7" fill="{colour}"/>'
        '<path d="M10.5 8v16" stroke="#fff" stroke-width="3" stroke-linecap="round" fill="none"/>'
        '<circle cx="16" cy="13.5" r="5.5" fill="none" stroke="#fff" stroke-width="3"/>'
        '<circle cx="16" cy="13.5" r="1.75" fill="#fff"/></g>'
    )


def name_paths(text: str, x0: float, baseline: float, cap_px: float) -> tuple[str, float]:
    font = TTFont(WEB / "public" / "fonts" / "atkinson-next-latin.woff2")
    if "fvar" in font:
        font = instantiateVariableFont(font, {"wght": 700})
    glyphs = font.getGlyphSet()
    cmap = font.getBestCmap()
    cap = getattr(font["OS/2"], "sCapHeight", 0) or 700
    k = cap_px / cap
    x = 0.0
    paths = []
    for ch in text:
        name = cmap[ord(ch)]
        pen = SVGPathPen(glyphs, ntos=lambda v: f"{v:.2f}".rstrip("0").rstrip("."))
        glyphs[name].draw(TransformPen(pen, (k, 0, 0, -k, x0 + x * k, baseline)))
        if pen.getCommands():
            paths.append(pen.getCommands())
        x += font["hmtx"][name][0]
    return "".join(paths), x * k


def main() -> None:
    cap_px = HEIGHT * CAP
    baseline = (HEIGHT + cap_px) / 2
    d, width = name_paths(brand["name"], HEIGHT + GAP, baseline, cap_px)
    total = round(HEIGHT + GAP + width + 2)
    logo = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total} {HEIGHT}" width="{total}" height="{HEIGHT}" '
        f'role="img" aria-label="{brand["name"]}"><title>{brand["name"]}</title>'
        f'{mark(brand["theme_color"], HEIGHT / 32)}<path d="{d}" fill="{INK}"/></svg>\n'
    )
    (WEB / "public" / "logo.svg").write_text(logo)
    favicon = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">{mark(brand["theme_color"])}</svg>\n'
    (WEB / "public" / "favicon.svg").write_text(favicon)
    print(f"logo.svg {total}x{HEIGHT}, favicon.svg")


if __name__ == "__main__":
    main()
