"""Text/OCR extraction: digital PDF text layer, .tex source, or MinerU for scans."""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from .config import Config
from .ir import Block, Page

log = logging.getLogger("handwrite.ocr")

# ---- .tex source -----------------------------------------------------------

TEX_MATH_RE = re.compile(r"(\$\$.*?\$\$|\$.*?\$|\\\[.*?\\\]|\\\(.*?\\\))", re.DOTALL)


MATH_JUNK_RE = re.compile(r"\s+")


def clean_latex_math(s: str) -> str:
    """Normalise OCR-produced LaTeX so XeLaTeX accepts it.

    MinerU 4.x wraps formulas in \\begin{array}{r}...\\end{array} and inserts
    spaces between every token; both are cosmetic, so strip them.
    """
    if not s:
        return s
    t = s.strip()
    # OCR inserts newlines inside $$...$$; normalise them first so the
    # line-separator (\\ or \\) is never split across two segments.
    t = t.replace("\r\n", " ").replace("\n", " ")
    # Preserve the row separator '\\' *before* any brace surgery: it is the
    # only thing that keeps array/cases/matrix rows apart, and the later
    # \s*([{}^_])\s* pass would otherwise delete it. Stash it out of the way
    # so single backslashes belonging to commands stay intact.
    t = t.replace("\\\\", "\x00ROWSEP\x00")
    # OCR escapes real braces as \{ and \} (MinerU does this for scripts like
    # a_{1234} -> a \{1 2 3 4 \}). Turn them back into real braces BEFORE any
    # subscript surgery, otherwise the numbers render as printed text, not as a
    # subscript in the handwriting font.
    # BUT \left\{, \right\}, \big\{, \bigg\{ etc. are real TeX delimiter
    # commands and must keep their escaped braces.  Protect them first, then
    # unescape the rest, then restore.
    _delim_braces: dict[str, str] = {}
    _db_cnt = 0

    def _stash(m: re.Match) -> str:
        nonlocal _db_cnt
        key = f"\x01DB{_db_cnt}\x01"
        _delim_braces[key] = m.group(0)
        _db_cnt += 1
        return key

    t = re.sub(
        r"\\(?:left|right|big[lr]?|Big[lr]?|bigg[lr]?|Bigg[lr]?)\\.",
        _stash,
        t,
    )
    t = t.replace("\\{", "{").replace("\\}", "}")
    for key, val in _delim_braces.items():
        t = t.replace(key, val)
    # MinerU sometimes writes the exponent caret as ^{\wedge}; collapse it to ^.
    t = re.sub(r"\^\s*\{\s*\\?(?:wedge|hat)\s*\}", "^", t)
    # MinerU encodes "otherwise" branches as a 2-column array {l l}; that is
    # exactly the cases environment. Other (matrix/determinant) arrays keep
    # their real column spec, so only convert the {l l} / {l|l} form, and pair
    # the closing tag to whatever the matching begin became.
    depth = {"n": 0}

    def _count_begin(m: re.Match) -> str:
        spec = re.sub(r"\s+", "", m.group(1))
        if re.fullmatch(r"[l|]{2}", spec):
            depth["n"] += 1
            return "\\begin{cases}"
        return m.group(0)

    def _end_array(m: re.Match) -> str:
        if depth["n"] > 0:
            depth["n"] -= 1
            return "\\end{cases}"
        return m.group(0)

    t = re.sub(r"\\begin\{array\}\s*\{\s*([^}]*?)\s*\}", _count_begin, t)
    t = re.sub(r"\\end\{array\}", _end_array, t)
    t = re.sub(r"\\begin\{aligned\}", "", t)
    t = re.sub(r"\\end\{aligned\}", "", t)
    # collapse the inter-token spaces the OCR model inserts
    t = MATH_JUNK_RE.sub(" ", t)
    # spaces around braces are never meaningful in math mode (but NOT around
    # the row separator we stashed above)
    t = re.sub(r"\s*([{}^_])\s*", r"\1", t)
    # restore the row separator as a proper double backslash
    t = t.replace("\x00ROWSEP\x00", "\\\\")
    # \operatorname{ l i m } -> \operatorname{lim}: OCR spaces out letters
    def _join_letters(m: re.Match) -> str:
        return m.group(1) + re.sub(r"\s+", "", m.group(2)) + m.group(3)

    t = re.sub(r"(\\operatorname\*?\s*\{)([^{}]*)(\})", _join_letters, t)
    t = re.sub(r"(\\text\s*\{)([^{}]*)(\})", _join_letters, t)
    t = re.sub(r"\\operatorname\s*\*\s*", r"\\operatorname ", t)
    # \in R / \mathbb R style fragments the OCR split out of the run
    t = re.sub(r"\\(in|subseteq|subset|cup|cap)\s+([A-Z])\b",
               r"\\\1 \\mathbb{\2}", t)
    t = re.sub(r"\\mathbb\s+([A-Z])\b", r"\\mathbb{\1}", t)
    # a subscript with several tokens, e.g. a _ {k i}, should be a_{ki} so the
    # index reads as one symbol (OCR spaces out the digits/letters)
    t = re.sub(r"_\s*\{([^{}]*)\}", lambda m: "_{" + re.sub(r"\s+", "", m.group(1)) + "}", t)
    t = re.sub(r"\^\s*\{([^{}]*)\}", lambda m: "^{" + re.sub(r"\s+", "", m.group(1)) + "}", t)
    # MinerU escaped subscripts as a \{1 2 3 4 \} (letter directly followed by a
    # braced group of digits/letters). Restore it as a real _-subscript so the
    # index lands on the handwriting font instead of printed text. Only apply
    # when the group is purely letters/digits (avoids touching \cmd{...} and
    # fences like {a+b}), and never right after a backslash (commands).
    def _bare_brace_sub(m: re.Match) -> str:
        lead, content = m.group(1), m.group(2)
        if re.fullmatch(r"[\sA-Za-z0-9]+", content):
            return lead + "_{" + re.sub(r"\s+", "", content) + "}"
        return m.group(0)

    # a letter is a subscript base only when it stands alone (not the tail of a
    # \command like \sin), so exclude letters/backslashes just before it.
    t = re.sub(r"(?<![A-Za-z\\])([A-Za-z0-9])\s*\{([^{}]*)\}", _bare_brace_sub, t)
    # tidy: stray double row separators and leading separators
    t = re.sub(r"\\\\(\s*&)?\s*\\\\", "\\\\", t)
    return t.strip()


def _repair_run_breaks(runs: list[dict]) -> list[dict]:
    """Merge a trailing math run that clearly continues into the next text.

    OCR sometimes closes $...$ one token early ("$x \\in$ R 且 ..."), leaving a
    bare letter/symbol outside math mode where it renders in the text font.
    Pull a following lone identifier into the math run.
    """
    if len(runs) < 2:
        return runs
    out: list[dict] = []
    i = 0
    while i < len(runs):
        r = runs[i]
        if (r["kind"] == "inline_math" and i + 1 < len(runs)
                and runs[i + 1]["kind"] == "text"):
            nxt = runs[i + 1]["v"]
            lead = len(nxt) - len(nxt.lstrip())
            stripped = nxt.lstrip()
            # lone capital letter or short identifier right after math
            m = re.match(r"^([A-Za-z]{1,3})(?=[\s,;:，。、；：]|$)(.*)$", stripped, re.DOTALL)
            if m and re.search(r"\\(in|subset|subseteq|cup|cap|leq|geq|neq|neq|to|forall|exists|sim|approx)$",
                               r["v"].strip()):
                r = {"kind": "inline_math", "v": r["v"] + " " + m.group(1), "display": False}
                rest = nxt[:lead] + m.group(2)
                out.append(r)
                if rest:
                    out.append({"kind": "text", "v": rest})
                i += 2
                continue
        out.append(r)
        i += 1
    return out


def _split_math_runs(text: str) -> list[dict]:
    """Split plain text into text/inline_math runs by $...$ and \\(...\\).

    Unpaired $ are escaped as literal \\$ so they can never leak into math mode.
    """
    runs: list[dict] = []
    # normalise \[ \] \( \) delimiters to $ first
    text = re.sub(r"\\\[(.+?)\\\]", lambda m: "$$" + m.group(1) + "$$", text, flags=re.DOTALL)
    text = re.sub(r"\\\((.+?)\\\)", lambda m: "$" + m.group(1) + "$", text, flags=re.DOTALL)

    pos = 0
    for m in TEX_MATH_RE.finditer(text):
        if m.start() > pos:
            seg = text[pos:m.start()]
            seg = _escape_stray_dollars(seg)
            if seg:
                runs.append({"kind": "text", "v": seg})
        raw = m.group(0)
        if raw.startswith("$$") or raw.startswith("\\["):
            continue  # display math handled as block
        inner = _math_body(raw)
        if inner:
            runs.append({"kind": "inline_math", "v": clean_latex_math(inner), "display": False})
        pos = m.end()
    tail = text[pos:]
    tail = _escape_stray_dollars(tail)
    if tail:
        runs.append({"kind": "text", "v": tail})
    return _repair_run_breaks(runs)


def split_embedded_math(runs: list[dict]) -> list[dict]:
    """Re-split text runs that contain literal $...$ / \\(...\\) into math runs."""
    out: list[dict] = []
    for r in runs:
        if r.get("kind") != "text":
            out.append(r)
            continue
        parts = _split_math_runs(r["v"])
        out.extend(parts)
    return out


def _sanitize_tex_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    for ln in lines:
        s = ln.strip()
        if not s or s.startswith("%"):
            continue
        if s.startswith("\\documentclass") or s.startswith("\\usepackage") or s.startswith("\\setCJK") \
           or s.startswith("\\setmainfont") or s.startswith("\\begin{document}") \
           or s.startswith("\\end{document}") or s.startswith("\\pagestyle") \
           or s.startswith("\\linespread"):
            continue
        out.append(s)
    return out


def parse_tex_source(path: Path) -> list[Page]:
    """Convert a .tex source to IR pages (flow re-layout, block granularity)."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    body = raw.split(r"\begin{document}")[-1].split(r"\end{document}")[0]
    lines = _sanitize_tex_lines(body.splitlines())

    blocks: list[Block] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        # display math blocks
        if ln.startswith(r"\["):
            buf = [ln[2:]]
            while i + 1 < len(lines) and r"\]" not in lines[i + 1]:
                buf.append(lines[i + 1])
                i += 1
            buf.append(lines[i + 1].split(r"\]")[0])
            blocks.append(Block(type="math", text="\n".join(buf), numbered=False))
            i += 2
            continue
        m = re.match(r"\\section\*?\{(.+?)\}", ln)
        if m:
            blocks.append(Block(type="heading", text=m.group(1), level=1))
            i += 1
            continue
        m = re.match(r"\\subsection\*?\{(.+?)\}", ln)
        if m:
            blocks.append(Block(type="heading", text=m.group(1), level=2))
            i += 1
            continue
        if ln.startswith(r"\begin{equation") or ln.startswith(r"\begin{align"):
            buf: list[str] = []
            while i < len(lines) and not lines[i].startswith(r"\end{"):
                buf.append(lines[i])
                i += 1
            body_buf = "\n".join(buf)
            body_buf = re.sub(r"\\begin\{equation\*\?\}", "", body_buf)
            body_buf = re.sub(r"\\begin\{align\*\?\}", "", body_buf)
            blocks.append(Block(type="math", text=body_buf, numbered=True))
            i += 1
            continue
        # plain paragraph line -> runs
        runs = _split_math_runs(ln)
        if runs:
            blocks.append(Block(type="text", text="", runs=runs))
        i += 1

    # merge consecutive text blocks (paragraph flow)
    merged: list[Block] = []
    for b in blocks:
        if b.type == "text" and merged and merged[-1].type == "text":
            merged[-1].runs += [{"kind": "text", "v": "\n"}] + b.runs
        else:
            merged.append(b)
    return [Page(index=0, size=(595, 842), blocks=merged)]


# ---- digital PDF text layer -------------------------------------------------

MATH_FONT_HINTS = ("cmm", "cmsy", "cmex", "math", "cambria math", "xits", "stix", "latinmodern-math",
                   "asana", "mtmi", "mtmr", "lmmath", "latinmodern math")


def _looks_math(font: str) -> bool:
    f = font.lower()
    return any(h in f for h in MATH_FONT_HINTS)


MATH_ONLY_RE = re.compile(r"^[\s0-9a-zA-Z+\-*/=<>()\[\]{}.,;:!?^_|'\"]*$")
LATEX_CMD_RE = re.compile(r"\\([a-zA-Z]+|.)")
DOLLAR_RE = re.compile(r"\$")


def _math_ratio(text: str) -> float:
    """Fraction of characters that look like math content."""
    if not text:
        return 0.0
    mathish = sum(1 for c in text if c in "0123456789abcdefghijklmnopqrstuvwxyz"
                  "ABCDEFGHIJKLMNOPQRSTUVWXYZ+-*/=<>()[]{},;:!^_|. '")
    return mathish / len(text)


def _is_short_math_line(text: str) -> bool:
    """A line that is mostly math AND has no CJK words is a formula line."""
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    return cjk <= 2 and len(text.strip()) <= 120


def _math_body(text: str) -> str:
    """Strip wrapping $ / \\( \\) / \\[ \\] from a formula string."""
    s = text.strip()
    s = re.sub(r"^\$\$?", "", s)
    s = re.sub(r"\$\$?$", "", s)
    s = re.sub(r"^\\[()\[\]]", "", s)
    s = re.sub(r"\\[()\[\]]$", "", s)
    return s.strip()


def _escape_stray_dollars(text: str) -> str:
    """Odd number of $ -> escape them all so nothing leaks into math mode."""
    if text.count("$") % 2 == 0:
        return text
    return text.replace("$", r"\$")


def _sanitize_line_text(s: str) -> str:
    """fitz keeps layout glue (soft spaces at span ends); normalise it."""
    return s.replace("\u200b", "").replace("\xa0", " ")


def extract_pdf_text(doc, dpi: int) -> list[Page]:
    """Extract text per page.

    Two detection channels, both applied at *line* granularity so that
    delimiters never straddle span boundaries:
      1. math-font spans (real LaTeX/Word PDFs) -> inline math
      2. literal $...$ / \\(...\\) in the joined text (tool exports) -> inline math
    """
    pages: list[Page] = []
    for idx in range(len(doc)):
        page = doc[idx]
        rect = page.rect
        blocks: list[Block] = []

        for blk in page.get_text("rawdict")["blocks"]:
            if blk.get("type", 0) != 0:
                continue
            bbox = list(blk.get("bbox", (0, 0, 0, 0)))

            for line in blk.get("lines", []):
                spans = line.get("spans", [])
                # join spans in reading order, keeping font info per span
                line_text = _sanitize_line_text("".join(
                    s.get("text", "") or "".join(c["c"] for c in s.get("chars", []))
                    for s in spans
                ))
                if not line_text.strip():
                    continue

                runs = _line_to_runs(line_text, spans)
                if not runs:
                    continue

                # whole line is math-font only -> display math
                if all(r["kind"] == "inline_math" for r in runs) and _is_short_math_line(line_text):
                    blocks.append(Block(
                        type="math",
                        text=" ".join(r["v"] for r in runs),
                        bbox=bbox, numbered=False))
                    continue

                blocks.append(Block(type="text", text="", bbox=bbox, runs=runs))

        pages.append(Page(index=idx, size=(int(rect.width), int(rect.height)), blocks=blocks))
    return pages


def _line_to_runs(line_text: str, spans: list[dict]) -> list[dict]:
    """Channel 1: math-font spans. Channel 2: literal $ delimiters."""
    if not line_text.strip():
        return []

    has_dollar = "$" in line_text or r"\(" in line_text or r"\[" in line_text
    if has_dollar:
        return _split_math_runs(line_text)

    # font-based: walk spans, group math-font spans together
    runs: list[dict] = []
    pos = 0
    for s in spans:
        text = _sanitize_line_text(s.get("text", "") or "".join(c["c"] for c in s.get("chars", [])))
        if not text:
            continue
        if _looks_math(s.get("font", "")):
            body = _math_body(text)
            if body:
                # merge with previous math run if adjacent
                if runs and runs[-1]["kind"] == "inline_math":
                    runs[-1]["v"] += " " + body
                else:
                    runs.append({"kind": "inline_math", "v": body, "display": False})
        else:
            runs.append({"kind": "text", "v": text})
        pos += len(text)
    return runs


# ---- OCR via MinerU ---------------------------------------------------------

def _candidate_mineru_paths() -> list[str]:
    """Look for mineru CLIs on PATH and inside known conda envs."""
    import glob
    names = ("mineru-kit", "mineru")
    found: list[str] = []
    for n in names:
        p = shutil.which(n)
        if p:
            found.append(p)
    # conda env Scripts dirs (env not activated in this shell)
    for pattern in (
        os.environ.get("CONDA_PREFIX", ""),
        os.environ.get("MINERU_HOME", ""),
        r"D:\Miniconda\envs\*\Scripts",
        os.path.expanduser(r"~\miniconda3\envs\*\Scripts"),
        os.path.expanduser(r"~\anaconda3\envs\*\Scripts"),
        os.path.expanduser(r"~\AppData\Local\miniconda3\envs\*\Scripts"),
    ):
        if not pattern:
            continue
        for n in names:
            for hit in sorted(glob.glob(os.path.join(pattern, n + ".exe"))):
                found.append(hit)
    # dedupe, keep order
    seen = set()
    out = []
    for f in found:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def _candidate_exes(cfg: Config) -> list[str]:
    """All MinerU executables worth trying, best first.

    MinerU 4.x ships two different fronts: ``mineru-kit parse`` parses a file
    directly, while ``mineru parse`` is a client for a local server. The plain
    file-drop interface is the one that works unattended, so anything named
    ``mineru-kit`` is tried before the server-backed CLI.
    """
    out: list[str] = []
    configured = (cfg.mineru_bin or "").strip()
    if configured:
        explicit = ""
        if os.sep in configured or "/" in configured or configured.lower().endswith(".exe"):
            explicit = configured
        else:
            explicit = shutil.which(configured) or ""
        if explicit:
            out.append(explicit)
    found = _candidate_mineru_paths()
    found.sort(key=lambda p: 0 if os.path.basename(p).lower().startswith("mineru-kit") else 1)
    out.extend(found)
    seen: set[str] = set()
    uniq: list[str] = []
    for p in out:
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def _looks_like_server_error(text: str) -> bool:
    """``mineru parse`` needs a running local server; that is not a parse error."""
    low = text.lower()
    return ("服务未运行" in text or "server start" in low
            or "server is not running" in low or "connection refused" in low)


def _env_for(cfg: Config) -> dict:
    env = dict(os.environ)
    if cfg.modelscope:
        env.setdefault("MINERU_MODEL_SOURCE", "modelscope")
    return env


def _decode(b: bytes | None) -> str:
    if not b:
        return ""
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return b.decode(enc, errors="replace")
        except Exception:
            continue
    return b.decode("latin-1", errors="replace")


_CLI_KIND_CACHE: dict[str, str] = {}


def _cli_kind(exe: str, env: dict) -> str:
    """Classify a MinerU executable by inspecting its help output.

    * ``kit``    — ``mineru-kit parse <file> -o <md>`` (stateless, no server)
    * ``server`` — MinerU 4.x ``mineru parse`` (delegates to a local server)
    * ``legacy`` — 2.x style ``mineru -p <file> -o <dir>``
    """
    key = os.path.normcase(os.path.abspath(exe))
    if key in _CLI_KIND_CACHE:
        return _CLI_KIND_CACHE[key]

    def help_text(args: list[str]) -> str:
        try:
            proc = subprocess.run([exe] + args, env=env, capture_output=True,
                                  text=False, timeout=120)
            return _decode(proc.stdout) + _decode(proc.stderr)
        except Exception:  # noqa: BLE001
            return ""

    kind = "legacy"
    sub = help_text(["parse", "--help"]).lower()
    if "parse" in sub and "usage" in sub:
        # both 4.x fronts share `parse`; the server client adds wait/json flags
        kind = "server" if ("--wait" in sub or "--json" in sub) else "kit"
    elif os.path.basename(exe).lower().startswith("mineru-kit"):
        kind = "kit"
    _CLI_KIND_CACHE[key] = kind
    return kind


def _commands_for(kind: str, exe: str, doc_path: Path, md_target: Path,
                  out_dir: Path, cfg: Config) -> list[list[str]]:
    """Invocation variants to try, most capable first."""
    if kind == "legacy":
        return [[exe, "-p", str(doc_path), "-o", str(out_dir), "-l", cfg.default_lang]]
    base = [exe, "parse", str(doc_path), "-o", str(md_target)]
    return [
        base + ["--tier", cfg.mineru_tier, "--ocr-mode", "ocr"],
        base + ["--tier", cfg.mineru_tier],
        base,
    ]


def parse_with_mineru(cfg: Config, doc_path: Path, out_dir: Path) -> bool:
    if cfg.mineru_bin == "":
        log.info("OCR disabled (--skip-ocr)")
        return False
    candidates = _candidate_exes(cfg)
    if not candidates:
        log.warning("MinerU not found; install with: pip install mineru")
        return False
    env = _env_for(cfg)
    md_target = out_dir / "mineru.md"
    server_error = ""

    for exe in candidates:
        kind = _cli_kind(exe, env)
        for cmd in _commands_for(kind, exe, doc_path, md_target, out_dir, cfg):
            log.info("running: %s", " ".join(cmd))
            try:
                proc = subprocess.run(cmd, env=env, capture_output=True,
                                      text=False, timeout=7200)
            except FileNotFoundError:
                break
            except Exception as e:  # noqa: BLE001
                log.warning("MinerU failed to start: %s", e)
                break
            out = _decode(proc.stdout)
            err = _decode(proc.stderr)
            if proc.returncode == 0 and (md_target.exists()
                                         or any(out_dir.rglob("*.md"))):
                return True
            blob = out + err
            if kind == "server" and _looks_like_server_error(blob):
                # this front needs `mineru server start`; try another executable
                server_error = blob.strip().splitlines()[-1] if blob.strip() else ""
                break
            log.debug("MinerU attempt failed (%d): %s", proc.returncode,
                      blob[-800:])
            if proc.returncode != 0 and "--tier" in cmd:
                continue  # retry without the optional tier flag
            break

    if server_error:
        log.warning("MinerU 4.x CLI needs a running local server (%s); "
                    "install mineru-kit for offline parsing", server_error)
    log.warning("MinerU could not parse %s", doc_path.name)
    return False


DISPLAY_DOLLAR_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
DISPLAY_BRACKET_RE = re.compile(r"\\\[(.+?)\\\]", re.DOTALL)


def _iter_md_units(text: str):
    """Yield ('heading'|'math'|'text', payload) units from MinerU markdown.

    Handles multi-line $$...$$ display blocks (MinerU emits them across
    several lines) as well as single-line forms.
    """
    # split on display-math blocks first, keeping them as separate units
    parts: list[tuple[str, str]] = []
    pos = 0
    for m in re.finditer(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]", text, re.DOTALL):
        if m.start() > pos:
            parts.append(("text", text[pos:m.start()]))
        body = m.group(1) if m.group(1) is not None else m.group(2)
        parts.append(("math", body))
        pos = m.end()
    if pos < len(text):
        parts.append(("text", text[pos:]))

    for kind, payload in parts:
        if kind == "math":
            yield ("math", payload)
            continue
        for ln in payload.splitlines():
            s = ln.strip()
            if not s:
                continue
            m = re.match(r"^(#{1,4})\s+(.*)", s)
            if m:
                yield ("heading", m.group(2))
                continue
            yield ("text", s)


def read_mineru_markdown(out_dir: Path, size: tuple[int, int]) -> list[Page]:
    """Parse MinerU output back into IR pages."""
    md_path = out_dir / "mineru.md"
    if not md_path.exists():
        mds = list(out_dir.rglob("*.md"))
        if not mds:
            return []
        md_path = mds[0]
    text = md_path.read_text(encoding="utf-8", errors="replace")

    pages: list[Page] = []
    blocks: list[Block] = []
    page_no = 0

    def flush() -> None:
        nonlocal blocks, page_no
        if blocks:
            pages.append(Page(index=page_no, size=size, blocks=blocks))
            page_no += 1
            blocks = []

    for kind, payload in _iter_md_units(text):
        if kind == "math":
            body = clean_latex_math(payload)
            if body:
                blocks.append(Block(type="math", text=body, numbered=False))
            continue
        if kind == "heading":
            # MinerU prepends its own auto-numbering ("## 1.1 3.1 导数的定义"):
            # drop the leading auto number, keep the document's own.
            title = payload
            m = re.match(r"^\d+(\.\d+)*\s+(?:(\d+(?:\.\d+)*)\s+)?(.*)$", title)
            if m:
                own_no = m.group(2) or ""
                rest = (m.group(3) or "").strip()
                title = (own_no + " " + rest).strip() if own_no else rest
            if not title:
                title = payload.strip()
            level = 2 if re.match(r"^\d+\.\d+", payload) else 1
            blocks.append(Block(type="heading", text=title, level=level))
            continue
        blocks.append(Block(type="text", text=payload, runs=_split_math_runs(payload)))
    flush()
    return pages


# ---- top-level -----------------------------------------------------------------

def _page_has_math_fonts(page) -> bool:
    """True if any span uses a dedicated math font (CMMI/CMSY/CMEX/Cambria Math...)."""
    for blk in page.get_text("rawdict")["blocks"]:
        if blk.get("type", 0) != 0:
            continue
        for line in blk.get("lines", []):
            for span in line.get("spans", []):
                if _looks_math(span.get("font", "")):
                    return True
    return False


def extract(cfg: Config, doc_path: Path, job_dir: Path) -> list[Page]:
    """Extract pages: .tex source > plain-text digital PDF > MinerU OCR.

    Note: a text layer alone is NOT enough when the document contains math —
    typeset formulas lose their LaTeX structure once flattened to glyphs, so
    anything with math fonts is routed through MinerU for real LaTeX recovery.
    """
    if doc_path.suffix.lower() == ".tex":
        return parse_tex_source(doc_path)

    pages: list[Page] = []
    try:
        import fitz  # imported lazily: only the extraction path needs PyMuPDF

        doc = fitz.open(doc_path)
    except Exception as e:  # noqa: BLE001
        log.error("cannot open %s: %s", doc_path, e)
        return []

    if doc.needs_pass:
        log.error("PDF is password protected: %s", doc_path)
        return []

    has_text = sum(len(p.get_text().strip()) for p in doc) > 40
    has_math = any(_page_has_math_fonts(p) for p in doc)

    if has_text and not has_math:
        pages = extract_pdf_text(doc, cfg.dpi_pdf_extract)
        log.info("digital PDF (no math): %d pages via text layer", len(pages))
    else:
        why = "no text layer" if not has_text else "text layer contains typeset math"
        log.info("%s -> invoking MinerU OCR for %s ...", why, doc_path.name)
        mineru_dir = job_dir / "mineru"
        mineru_dir.mkdir(parents=True, exist_ok=True)
        ok = parse_with_mineru(cfg, doc_path, mineru_dir)
        if ok:
            size = (int(doc[0].rect.width), int(doc[0].rect.height)) if len(doc) else (595, 842)
            pages = read_mineru_markdown(mineru_dir, size)
            log.info("MinerU produced %d pages", len(pages))
        if not pages:
            if not getattr(cfg, "allow_textlayer_fallback", False):
                log.warning(
                    "MinerU unavailable. Re-run with --textlayer-fallback to use the "
                    "raw text layer instead (formulas will lose their LaTeX structure), "
                    "or install MinerU: pip install mineru")
            else:
                log.warning("falling back to raw text layer (formulas degrade to glyph runs)")
                pages = extract_pdf_text(doc, cfg.dpi_pdf_extract)
    doc.close()
    return pages
