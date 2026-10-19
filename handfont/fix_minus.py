"""Add the TRUE MINUS SIGN U+2212 to MyHand-Full.ttf, using your handwritten
hyphen U+002D as the outline.

Why: in math mode TeX emits U+2212 (minus sign), not U+002D (hyphen).  On the
template you wrote the hyphen "-", so U+2212 is absent and every minus in a
formula falls back to the printed Latin Modern glyph.  A handwritten minus and
hyphen are both just a horizontal stroke, so reusing the hyphen is faithful.
"""
from __future__ import annotations

from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

TARGET = "handfont/MyHand-Full.ttf"
SRC_CP = 0x2D      # hyphen you wrote
DST_CP = 0x2212    # true minus used by math


def main() -> None:
    f = TTFont(TARGET)
    cmap = f.getBestCmap()

    if DST_CP in cmap:
        print("U+2212 already present, nothing to do")
        return
    if SRC_CP not in cmap:
        raise SystemExit("source hyphen U+002D missing from font")

    glyf = f["glyf"]
    order = f.getGlyphOrder()
    src_name = cmap[SRC_CP]

    name = "minus"
    i = 1
    while name in order:
        name = "minus_%d" % i
        i += 1

    # copy the hyphen outline (flatten composites)
    rec = RecordingPen()
    f.getGlyphSet()[src_name].draw(rec)
    pen = TTGlyphPen(glyf)
    rec.replay(pen)
    order.append(name)
    glyf[name] = pen.glyph()
    f.setGlyphOrder(order)

    # same advance/metrics as the hyphen (both are a plain horizontal stroke)
    adv, lsb = f["hmtx"][src_name]
    f["hmtx"][name] = (adv, lsb)

    for t in f["cmap"].tables:
        t.cmap[DST_CP] = name

    f.save(TARGET)

    f2 = TTFont(TARGET)
    c2 = f2.getBestCmap()
    print("U+2212 minus added from your hyphen U+002D (advance %d)" % adv)
    print("U+2212 present:", DST_CP in c2)
    print("missing ASCII:", [chr(c) for c in range(32, 127) if c not in c2])


if __name__ == "__main__":
    main()
