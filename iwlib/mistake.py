"""Mistake simulation at the TeX level.

Doing corrections in LaTeX (rather than on the raster) has three advantages:
the wrong glyph is a *real* character, the replacement glyph is written next
to it, and \xcancel places the strike exactly over the wrong glyph.
"""

from __future__ import annotations

# visually confusable pairs (形近字), used as the "wrong" character
CONFUSABLE: dict[str, list[str]] = {
    "的": ["约", "旳"],
    "是": ["足", "昰"],
    "在": ["茬", "侢"],
    "有": ["冇", "侑"],
    "不": ["坏", "抔"],
    "人": ["入", "亽"],
    "大": ["太", "夶"],
    "小": ["少", "尐"],
    "上": ["卜", "尙"],
    "下": ["丅", "芐"],
    "天": ["夭", "兲"],
    "无": ["元", "旡"],
    "一": ["二", "壹"],
    "二": ["一", "弍"],
    "三": ["彡", "叁"],
    "日": ["曰", "囸"],
    "月": ["用", "円"],
    "水": ["氺", "沝"],
    "火": ["灭", "灬"],
    "木": ["术", "朩"],
    "金": ["全", "釒"],
    "土": ["士", "圡"],
    "山": ["屲", "舢"],
    "石": "右",
    "白": ["自", "臼"],
    "目": ["且", "囬"],
    "手": ["毛", "扌"],
    "心": ["必", "忄"],
    "中": ["申", "盅"],
    "内": ["肉", "內"],
    "外": ["处", "外"],
    "多": ["夕", "夛"],
    "少": ["小", "尐"],
    "高": ["髙", "膏"],
    "低": ["氐", "胝"],
    "长": ["苌", "長"],
    "短": ["矮", "疒"],
    "新": ["薪", "親"],
    "旧": ["汩", "舊"],
    "对": ["双", "対"],
    "错": ["错", "錯"],
    "好": ["女子", "孬"],
    "学": ["学", "學"],
    "生": ["牛", "龶"],
    "同": ["向", "侗"],
    "不": "杯",
    "以": ["似", "苡"],
    "及": ["乃", "彶"],
    "于": ["干", "亍"],
    "之": ["乏", "辶"],
    "与": ["写", "與"],
    "也": ["乜", "也"],
    "了": ["子", "孒"],
    "和": ["咊", "盉"],
    "或": ["域", "戓"],
    "如": ["妇", "侞"],
    "其": ["甚", "其"],
    "此": ["柴", "泀"],
    "因": ["烟", "囙"],
    "而": ["面", "洏"],
    "且": ["旦", "且"],
    "若": ["苦", "婼"],
    "则": ["厕", "則"],
    "所": ["沂", "所"],
    "为": ["办", "為"],
    "然": ["燃", "然"],
    "所": ["所", "所"],
}

# a small pool of alternate characters for cases with no obvious pair
FALLBACK = ["某", "该", "此", "其", "某", "该"]


def _variants(char: str) -> list[str]:
    """Candidate wrong characters for a given correct character."""
    v = CONFUSABLE.get(char)
    if not v:
        return []
    if isinstance(v, str):
        v = [v]
    # keep only real single characters
    return [c for c in v if len(c) == 1 and c != char]


def inject_mistakes(tex: str, rate: float, rng) -> tuple[str, int]:
    """Replace a fraction of CJK characters with a confusable wrong char,
    struck through with \\cancel (slash) or \\xcancel (cross), plus the right
    character written next to it.

    Corrections only ever land in body prose. Any line that is a LaTeX command,
    a heading, or that contains math (``$...$`` / ``\\begin{...}``) is left
    untouched — injecting into a formula would corrupt the math source.

    Returns the new tex source and the number of injected mistakes.
    """
    if rate <= 0:
        return tex, 0

    import re

    # Lines we must never touch: commands, headings, and anything with math.
    skip_re = re.compile(r"^\s*\\|[\$]|\\begin\{|\\end\{")

    injected = 0
    out_lines: list[str] = []

    for ln in tex.split("\n"):
        if skip_re.search(ln):
            out_lines.append(ln)
            continue
        chars = list(ln)
        for i, ch in enumerate(chars):
            if "\u4e00" <= ch <= "\u9fff" and rng.random() < rate:
                alts = _variants(ch) or FALLBACK
                wrong = rng.choice(alts)
                # a single lazy slash reads as a real correction; a full cross
                # looks like an annotation, so keep it rare
                strike = "\\cancel" if rng.random() < 0.82 else "\\xcancel"
                if rng.random() < 0.5:
                    # wrong first, correction after
                    chars[i] = f"{strike}{{{wrong}}}{ch}"
                else:
                    # correction first, wrong crossed after
                    chars[i] = f"{ch}{strike}{{{wrong}}}"
                injected += 1
        out_lines.append("".join(chars))

    return "\n".join(out_lines), injected
