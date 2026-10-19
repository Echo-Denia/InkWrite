"""LaTeX generation + xelatex compilation."""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from .config import Config
from .ir import Block, Document

log = logging.getLogger("handwrite.texgen")

LATEX_SPECIAL = {
    "\\": r"\textbackslash{}",
    "{": r"\{", "}": r"\}",
    "$": r"\$", "&": r"\&", "#": r"\#", "_": r"\_", "%": r"\%",
    "^": r"\textasciicircum{}", "~": r"\textasciitilde{}",
}


def escape_text(s: str) -> str:
    out = []
    for ch in s:
        out.append(LATEX_SPECIAL.get(ch, ch))
    return "".join(out)


#: standalone latin letter in math — not part of a command like \sin, and not
#: already inside a \mathrm / \text / \operatorname brace.
#:
#: We use \\mathrm (not \\symup) because \\symup renders from the Mathematical
#: Alphanumeric Symbols block (e.g. U+1D403 for upright D), which handwriting
#: fonts don't have.  \\mathrm uses the text font (\\setmainfont → handwriting
#: font) and renders Basic Latin codepoints (U+0044 for D) that the font
#: definitely provides.
#: match a *standalone* Latin letter — not part of a command (\sin, \det) and
#: not adjacent to other letters.  Used for bare (un-braced) letters in the
#: body after all braces and font commands have been stashed away.
_MATH_LATIN_RE = re.compile(
    r"(?<!\\)(?<![a-zA-Z])([A-Za-z])(?![a-zA-Z{])"
)

#: match a run of Latin letters that is NOT part of a command name (i.e. the
#: first letter is not preceded by a backslash).  Only used inside brace
#: contents AFTER backslash-commands have been temporarily stashed, so
#: sequences like "ij"/"nj" are wrapped while \leftrightarrow etc. are safe.
_LATIN_SEQ_RE = re.compile(r"(?<!\\)[A-Za-z]+")


def _wrap_math_latin(body: str) -> str:
    """Wrap bare Latin letters in \\mathrm so they use the handwriting font.

    Processes brace contents so subscript/superscript letters are wrapped too:
      a_{ij} → a_{\\mathrm{ij}} → \\mathrm{a}_{\\mathrm{ij}}.
    Letters inside font commands (\\mathbb, \\mathbf, \\text, …), existing
    \\mathrm{} wrappers, and array/tabular column-specs ({c c c}) are left
    untouched.
    """
    body = body.replace("\\\\", "\x00ROWSEP\x00")

    # 1) protect known LaTeX font/text commands AND \\begin{env}[colspec] /
    #    \\end{env} so that column-spec letters (e.g. the four 'c's in
    #    \\begin{array}{c c c c}) are never wrapped in \\mathrm.
    _PROTECT = re.compile(
        r"\\(?:begin|end)\{[^}]*\}(?:\{[^}]*\})?"
        r"|"
        r"\\(?:mathbb|mathbf|mathcal|mathfrak|mathscr|mathsf|mathtt|"
        r"text|operatorname|mathrm|symup|symbf|symit|symsf|symtt|symrm)\*?\{[^}]*\}"
    )
    prot_font: dict[str, str] = {}
    _f = 0

    def _stash_font(m: re.Match) -> str:
        nonlocal _f
        key = f"\x03{_f}\x03"
        prot_font[key] = m.group(0)
        _f += 1
        return key

    body = _PROTECT.sub(_stash_font, body)

    # 2) stash remaining {…} groups, wrapping Latin letter sequences inside.
    #    Backslash-commands inside the brace (\\leftrightarrow, \\frac, ...)
    #    are temporarily stashed so they aren't partially wrapped.
    _CMD_RE = re.compile(r"\\[A-Za-z]+")
    prot_braces: dict[str, str] = {}
    _b = 0

    def _stash_brace(m: re.Match) -> str:
        nonlocal _b
        inner = m.group(1)
        # 2a) protect backslash-commands inside this brace
        cmd_map: dict[str, str] = {}
        _ci = 0

        def _stash_cmd(mm: re.Match) -> str:
            nonlocal _ci
            key = f"\x06{_ci}\x06"
            cmd_map[key] = mm.group(0)
            _ci += 1
            return key

        inner = _CMD_RE.sub(_stash_cmd, inner)
        # 2b) wrap remaining Latin letter sequences
        inner = _LATIN_SEQ_RE.sub(
            lambda s: r"\mathrm{" + s.group(0) + "}", inner
        )
        # 2c) restore backslash-commands
        for key, val in cmd_map.items():
            inner = inner.replace(key, val)

        key = f"\x04{_b}\x04"
        prot_braces[key] = "{" + inner + "}"
        _b += 1
        return key

    body = re.sub(r"\{([^{}]*)\}", _stash_brace, body)

    # 3) wrap bare (un-braced) standalone Latin letters.  At this point all
    #    braces and font commands are stashed, so only command names (\\det,
    #    \\sin, ... — anchored by \\) and truly bare letters remain.
    body = _MATH_LATIN_RE.sub(
        lambda m: r"\mathrm{" + m.group(1) + "}", body
    )

    # 4) restore (braces first so \mathrm{} from step 2 is visible to font
    #    commands that may wrap them, e.g. \mathbf{\mathrm{x}})
    for key, val in prot_braces.items():
        body = body.replace(key, val)
    for key, val in prot_font.items():
        body = body.replace(key, val)

    body = body.replace("\x00ROWSEP\x00", "\\\\")
    return body


def math_safe(s: str) -> str:
    """Strip common OCR/markdown noise before dropping into math mode."""
    from .ocr import clean_latex_math
    s = s.strip()
    s = s.replace(r"\(", "").replace(r"\)", "").replace(r"\[", "").replace(r"\]", "")
    s = re.sub(r"^\\begin\{equation\*\?\}", "", s)
    s = re.sub(r"\\end\{equation\*\?\}$", "", s)
    s = re.sub(r"\\label\{[^}]*\}", "", s)
    return clean_latex_math(s)


def _has_tex_spaces(s: str) -> bool:
    """True if the math string contains spaces/backslashes -> treat as code."""
    return bool(re.search(r"[\s\\]", s))


def _display_body(block: Block) -> str:
    body = math_safe(block.text)
    # strip trailing \nonumber
    body = re.sub(r"\\nonumber\s*$", "", body)
    return body


def render_math_block(block: Block) -> str:
    """Render a display-math block; return '' if too risky to compile.

    Display math is emitted as ``$\\displaystyle …$`` inside a ``center``
    environment instead of ``equation``/``align``.  Those environments are
    single unbreakable horizontal boxes, so an over-long formula runs off the
    page; making it an ordinary paragraph lets TeX's own line breaker wrap it
    at relation operators (``=``, ``\\xlongequal``, …) using the standard
    \\relpenalty/\\binoppenalty.  That is far more robust than picking break
    points by hand: a hand-placed ``\\\\`` cannot know the available width, and
    when it guesses a spot inside a group (e.g. ``\\underline{…}``) it
    produces an empty continuation line plus, inside an ``align`` cell, a
    spurious ``&``.
    """
    body = _display_body(block)
    if not body:
        return ""
    body = _wrap_math_latin(body)

    if block.numbered:
        return (
            "\\begin{center}\\refstepcounter{equation}$\\displaystyle\n"
            + body
            + "\n$\\quad(\\theequation)\\end{center}"
        )
    return "\\begin{center}$\\displaystyle\n" + body + "\n$\\end{center}"


def render_runs(runs: list[dict]) -> str:
    parts = []
    for r in runs:
        k = r.get("kind", "text")
        v = r.get("v", "")
        if k == "text":
            parts.append(escape_text(v))
        elif k == "inline_math":
            mm = math_safe(v)
            if not mm:
                continue
            mm = _wrap_math_latin(mm)
            parts.append("$" + mm + "$")
        else:
            parts.append(escape_text(v))
    return "".join(parts)


def render_heading(block: Block) -> str:
    depth = max(1, min(block.level or 1, 3))
    cmd = {1: "\\section", 2: "\\subsection", 3: "\\subsubsection"}[depth]
    return f"{cmd}{{{escape_text(block.text)}}}"


def render_block(block: Block) -> str:
    if block.type == "heading":
        return render_heading(block)
    if block.type == "math":
        return render_math_block(block)
    if block.type == "text":
        if block.runs:
            return render_runs(block.runs) + "\n\n"
        return escape_text(block.text) + "\n\n"
    if block.type == "list":
        return render_runs(block.runs) + "\n\n"
    if block.type == "table":
        # degrade gracefully: plain text
        return escape_text(block.text) + "\n\n"
    if block.type == "title":
        return "\\section*{" + escape_text(block.text) + "}"
    return escape_text(block.text) + "\n\n"


def render_document(cfg: Config, doc: Document) -> str:
    margin = cfg.margin_mm
    cjk_file = Path(cfg.cjk_font_file)
    latin_file = Path(cfg.latin_font_file)
    # ctexart only accepts 10/11/12pt as a class option; arbitrary sizes need
    # the fontsize package or the option is silently ignored.
    fs = int(cfg.fontsize_pt)
    script = max(6, round(fs * 0.72))
    scriptscript = max(5, round(fs * 0.54))
    scaleopt = f"Scale={cfg.font_scale}," if cfg.font_scale != 1.0 else ""
    # Stretchy delimiters (| used by \left|..\right| for determinants, and
    # \lvert/\rvert/\vert/\mid).  A bitmap-derived handwriting font has NO
    # glyph-variant/extensible data, so these CANNOT grow with the content:
    # a 4-row determinant ends up with two short stubs instead of full-height
    # bars.  Measured: handwriting bar 99px (8% of content height) vs Latin
    # Modern 288px (24%).  Hence they stay on Latin Modern unless you opt in
    # with handwrite_math_delimiters=True (accepting the visual loss).
    delim_cmds = [r"\lvert", r"\rvert", r"\vert", r"\mid"]
    delimrange = ""
    if getattr(cfg, "handwrite_math_delimiters", False):
        delimrange = ("\n" + r"\setmathfont[range={" + ",".join(delim_cmds) + "}]"
                      + "{" + latin_file.name + "}"
                      + "[" + scaleopt + "Path=" + latin_file.parent.as_posix() + "/]")
    # Optional: render math operators/relations/big-operators/arrows with the
    # handwriting font instead of Latin Modern Math.  Names must be the actual
    # unicode-math commands (``\ne`` not ``\neq``, ``\minus`` not ``\plus``) or
    # unicode-math raises "not recognised".  Ranges the font lacks are silently
    # skipped by unicode-math, so listing extra ones is harmless.
    if getattr(cfg, "handwrite_math_symbols", False):
        # NOTE: names must be real unicode-math commands or it errors
        # "not recognised".  Verified invalid: \plus \neq \to \beta \pi \mu.
        sym_cmds = ",".join([
            # binary operators / relations
            # NOTE: the command for "+" is \mathplus (plain \plus is invalid).
            r"\mathplus", r"\minus", r"\equal", r"\less", r"\greater",
            r"\times", r"\divide", r"\pm", r"\cdot", r"\ne",
            r"\leq", r"\geq", r"\propto", r"\approx", r"\equiv", r"\sim",
            # big operators
            r"\sum", r"\prod", r"\int", r"\sqrt", r"\infty",
            # arrows
            r"\leftrightarrow", r"\rightarrow", r"\leftarrow",
            r"\Rightarrow", r"\Leftarrow",
            # set/logic
            r"\in", r"\forall", r"\exists", r"\partial", r"\nabla",
            # brackets (these DO scale if the font has size variants; keep on
            # unless handwrite_math_delimiters is on)
            r"\lparen", r"\rparen", r"\lbrace", r"\rbrace",
            r"\lbrack", r"\rbrack",
            # NOTE: \semicolon \period \comma \exclam \question are NOT valid
            # unicode-math range names (they raise "Undefined control
            # sequence"), so punctuation is deliberately left on Latin Modern.
            r"\colon", r"\slash", r"\backslash",
            # ellipsis / primes
            r"\ldots", r"\cdots", r"\vdots", r"\ddots", r"\prime",
            # binary ops / sets / logic
            r"\oplus", r"\otimes", r"\cap", r"\cup", r"\setminus", r"\neg",
            r"\mapsto", r"\implies", r"\perp", r"\angle", r"\triangle",
            # Greek (only the names unicode-math accepts as ranges)
            r"\alpha", r"\gamma", r"\theta", r"\lambda", r"\sigma",
        ])
        symrange = ("\n" + r"\setmathfont[range={" + sym_cmds + "}]"
                    + "{" + latin_file.name + "}"
                    + "[" + scaleopt + "Path=" + latin_file.parent.as_posix() + "/]")
    else:
        symrange = ""

    # Letter spacing (字间距).  xeCJK inserts CJKglue between CJK chars; for
    # Latin we widen the inter-word/letter skip via \spaceskip/\xspaceskip.
    # Value is in em (relative to the current font size), so it scales with
    # fontsize_pt automatically.
    ls = getattr(cfg, "letter_spacing", 0.0) or 0.0
    lsblock = ""
    if abs(ls) > 1e-6:
        lsblock += "\n%% --- letter spacing: %+.3f em ---" % ls
        # IMPORTANT: use \kern, not \hspace.  \hspace cannot shrink (a negative
        # \hspace is clamped/errors in LaTeX), while \kern accepts negative
        # values, so the same setting can TIGHTEN (negative) or loosen
        # (positive) the tracking.
        lsblock += "\n\\xeCJKsetup{CJKglue=\\kern %.4f em}" % ls
        if getattr(cfg, "letter_spacing_latin", True):
            # Latin: shift the natural inter-word glue.  \fontdimen2 is the
            # normal space; adding a negative amount tightens it.  Keep it
            # shrinkable/stretchable so line breaking still works.
            lsblock += (
                "\n\\spaceskip=\\dimexpr\\fontdimen2\\font %+.4f em\\relax"
                % (ls * 0.5)
            )
            lsblock += (
                "\n\\xspaceskip=\\dimexpr\\fontdimen2\\font %+.4f em\\relax" % ls
            )
        lsblock += "\n"

    preamble = rf"""% Auto-generated by handwrite
\documentclass[a4paper]{{ctexart}}
\usepackage[fontsize={fs}pt]{{fontsize}}
\usepackage{{ctex}}
\setCJKmainfont{{{cjk_file.name}}}[{scaleopt}Path={cjk_file.parent.as_posix()}/]
\setmainfont{{{latin_file.name}}}[{scaleopt}Path={latin_file.parent.as_posix()}/]
\DeclareMathSizes{{{fs}}}{{{fs}}}{{{script}}}{{{scriptscript}}}
\usepackage{{amsmath}}
\usepackage{{cancel}}
% extarrows provides \xlongequal, which OCR output uses for row/column
% operations on determinants.  Without it the command is undefined.
\usepackage{{extarrows}}
% unicode-math: handwriting font for letters + digits only (upright, via
% \\mathrm).  Math-italic ranges are NOT mapped because handwriting fonts lack
% the Mathematical Alphanumeric Symbols block (U+1D400–U+1D7FF).
% Standalone Latin letters in formulas are wrapped in \\mathrm{{}} by the
% renderer so they use the text font (\\setmainfont → handwriting → Basic Latin
% codepoints the font actually has).
% All operators (=, <, >, +, -), symbols (\\times, \\neq, \\sum, arrows...) and
% Greek letters stay on Latin Modern Math.
\usepackage{{unicode-math}}
\setmathfont{{Latin Modern Math}}
\setmathfont[range=up/{{latin,num}}]{{{latin_file.name}}}[{scaleopt}Path={latin_file.parent.as_posix()}/]
\setmathfont[range=bfup/{{latin}}]{{{latin_file.name}}}[{scaleopt}Path={latin_file.parent.as_posix()}/]{symrange}{delimrange}

\usepackage[margin={margin}mm]{{geometry}}
\usepackage{{parskip}}
\linespread{{{cfg.linespread}}}
\pagestyle{{plain}}{lsblock}
\begin{{document}}
"""
    body_blocks = []
    for pg in doc.pages:
        for b in pg.blocks:
            rendered = render_block(b)
            if rendered.strip():
                body_blocks.append(rendered)
    body = "\n".join(body_blocks)
    if not body.strip():
        body = "\\mbox{}"

    # inject crossed-out mistakes (real wrong character + right one after)
    if cfg.error_rate > 0:
        from .mistake import inject_mistakes
        import random as _random
        rng = _random.Random()
        body, n_mist = inject_mistakes(body, cfg.error_rate, rng)
        if n_mist:
            log.debug("injected %d mistake(s)", n_mist)

    # "抄几遍": append N-1 extra copies to the SAME flow.  No \newpage or
    # \clearpage between copies, so TeX's line/page breaker reflows everything
    # and copy 2 continues on whatever space is left after copy 1's last page.
    # Mistake injection is applied once (per unique mistake seed) before the
    # copies so repeated text is identical, like actually re-copying a text.
    n = max(1, int(getattr(cfg, "repeat", 1) or 1))
    if n > 1:
        parts = [body] * n
        body = "\n".join(parts)
        log.debug("repeated body %d times", n)

    return preamble + body + "\n\\end{document}\n"


def write_tex(cfg: Config, doc: Document, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tex = render_document(cfg, doc)
    path.write_text(tex, encoding="utf-8")
    log.debug("wrote %s (%d bytes)", path, len(tex))
    return path


def _decode(b: bytes | None) -> str:
    if not b:
        return ""
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return b.decode(enc, errors="replace")
        except Exception:
            continue
    return b.decode("latin-1", errors="replace")


def compile_tex(cfg: Config, tex_path: Path, work_dir: Path) -> Path | None:
    """Compile with xelatex twice; return the PDF path or None on failure."""
    xelatex = shutil_which(cfg.xelatex)
    if not xelatex:
        log.error("xelatex not found on PATH (TeX Live / MiKTeX required)")
        return None

    for _ in range(2):
        proc = subprocess.run(
            [xelatex, "-interaction=nonstopmode", "-no-shell-escape",
             tex_path.name],
            cwd=tex_path.parent, capture_output=True, text=False,
            timeout=600,
        )
        pdf = tex_path.with_suffix(".pdf")
        if pdf.exists():
            continue
        out = _decode(proc.stdout)
        log.error("xelatex failed:\n%s", out[-3000:])
        return None

    pdf = tex_path.with_suffix(".pdf")
    return pdf if pdf.exists() else None


def shutil_which(name: str) -> str | None:
    import shutil
    return shutil.which(name)
