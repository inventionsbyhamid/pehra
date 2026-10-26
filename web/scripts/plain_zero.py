"""Atkinson Hyperlegible Next draws a slashed zero and has no OpenType feature to turn it off, so times read "1Ø PM".
This redraws the zero once in the font file: the outer oval stays, and the two half counters either side of the slash
become one counter, the outer oval scaled into their bounds, at every weight of the variable font.

    python3 web/scripts/plain_zero.py web/public/fonts/atkinson-next-latin.woff2   (needs fonttools and brotli)

Running it again changes nothing. The font is under the SIL Open Font License (OFL.txt), which allows modified
versions under the same licence."""
import sys

from fontTools.ttLib import TTFont
from fontTools.ttLib.tables import ttProgram
from fontTools.ttLib.tables._g_l_y_f import GlyphCoordinates


def contours(coords, ends):
    start, out = 0, []
    for end in ends:
        out.append(list(range(start, end + 1)))
        start = end + 1
    return out


def bounds(points):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def counter(outer, inner):
    """The outer contour's points scaled into the inner points' bounds, in the opposite direction (a hole)."""
    ox0, oy0, ox1, oy1 = bounds(outer)
    ix0, iy0, ix1, iy1 = bounds(inner)
    sx, sy = (ix1 - ix0) / (ox1 - ox0), (iy1 - iy0) / (oy1 - oy0)
    scaled = [(ix0 + (x - ox0) * sx, iy0 + (y - oy0) * sy) for x, y in outer]
    return [scaled[0], *reversed(scaled[1:])]


def main(path: str) -> None:
    font = TTFont(path)
    glyf, gvar = font["glyf"], font["gvar"]
    zero = glyf["zero"]
    if zero.numberOfContours == 2:
        print("already plain")
        return
    assert zero.numberOfContours == 3, "expected an outer oval and two half counters"
    coords, ends, flags = zero.getCoordinates(glyf)
    parts = contours(coords, ends)
    outer, halves = parts[0], parts[1] + parts[2]
    n = len(coords)

    def drawn(c):
        o = [c[i] for i in outer]
        return o + counter(o, [c[i] for i in halves])

    new_default = drawn(list(coords))
    on = [flags[i] & 1 for i in outer]
    new_flags = on + [on[0], *reversed(on[1:])]
    for var in gvar.variations["zero"]:
        deltas = list(var.coordinates)
        assert None not in deltas, "every point has its delta"
        master = [(x + dx, y + dy) for (x, y), (dx, dy) in zip(coords, deltas[:n])]
        moved = drawn(master)
        var.coordinates = [(round(a[0] - b[0]), round(a[1] - b[1])) for a, b in zip(moved, new_default)] + deltas[n:]

    zero.coordinates = GlyphCoordinates([(round(x), round(y)) for x, y in new_default])
    zero.endPtsOfContours = [len(outer) - 1, 2 * len(outer) - 1]
    zero.numberOfContours = 2
    zero.flags = bytearray(new_flags)
    if getattr(zero, "program", None) is not None:
        zero.program = ttProgram.Program()
        zero.program.fromBytecode(b"")  # hints were for the old points
    zero.recalcBounds(glyf)
    font.save(path)
    print("zero redrawn without its slash")


if __name__ == "__main__":
    main(sys.argv[1])
