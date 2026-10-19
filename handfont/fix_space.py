"""Add a proper SPACE (U+0020) glyph to MyHand-Full.ttf.

Why: a space cannot be handwritten on the template (empty cell -> skipped in step 03),
is not a valid AI target, and the simsun fallback also failed for it.  Without it,
XeLaTeX emits `Missing character: There is no (U+0020)` and every word collapses.

A space glyph has NO outline - only an advance width.  We use fontTools to add an
empty glyph and register it in glyf/hmtx/cmap.
"""
from __future__ import annotations

from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

TARGET = "handfont/MyHand-Full.ttf"


def main() -> None:
    f = TTFont(TARGET)
    cmap = f.getBestCmap()

    if 0x20 in cmap:
        print("space already present, nothing to do")
        return

    glyf = f["glyf"]
    glyf_order = f.getGlyphOrder()

    # pick a free glyph name and register an EMPTY glyph (no contours)
    name = "space"
    i = 1
    while name in glyf_order:
        name = "space_%d" % i
        i += 1

    pen = TTGlyphPen(glyf)
    glyph = pen.glyph()  # empty glyph -> space has no outline
    glyf_order.append(name)
    glyf[name] = glyph
    f.setGlyphOrder(glyf_order)

    # advance width: ~0.30 em. At unitsPerEm=1000 that is 300.
    upm = f["head"].unitsPerEm
    adv = int(round(upm * 0.30))
    f["hmtx"][name] = (adv, 0)

    # map U+0020 in every cmap subtable
    for t in f["cmap"].tables:
        t.cmap[0x20] = name

    f.save(TARGET)

    # verify
    f2 = TTFont(TARGET)
    c2 = f2.getBestCmap()
    print("space U+0020 -> %s (advance %d @upm %d)" % (c2[0x20], adv, upm))
    print("missing ASCII 32-126:", [chr(c) for c in range(32, 127) if c not in c2])
    print("total glyphs:", f2["maxp"].numGlyphs)


if __name__ == "__main__":
    main()
