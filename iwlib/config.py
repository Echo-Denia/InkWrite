"""Config module: paths, fonts, tunable knobs."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    input: str = ""
    output_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "output")

    # --- source extraction ---
    dpi_pdf_extract: int = 300  # render PDF pages for OCR (scanned fallback)
    default_lang: str = "ch"  # MinerU language hint

    # --- typography ---
    fontsize_pt: int = 22  # larger glyphs -> denser, fuller page
    linespread: float = 1  # tight leading (handwriting is dense)
    margin_mm: int = 15

    # --- output raster ---
    dpi_render: int = 300  # resolution of the final handwritten image
    ink_color: str = "#2B2A29"  # near-black with warm tint
    bg_mode: str = "plain"  # "plain" | "lined" | "grid"
    bg_line_color: str = "#A9C8EC"
    bg_grid_color: str = "#A9C8EC"
    bg_gradient: tuple = (0.985, 0.95)  # off-white warm paper
    noise_sigma: float = 4.5
    blur_radius: float = 0.5
    jitter_rot: float = 0.6  # whole page rotation deg

    # --- jitter (ink layer) ---
    jit_dx: float = 1.4
    jit_dy: float = 1.4
    jit_rot: float = 0.5  # per-glyph rotation deg
    jit_scale: float = 0.035
    jit_alpha_min: float = 0.82
    jit_slant: float = 4.0  # whole-page handwriting slant, degrees
    jit_baseline: float = 2.6  # per-line baseline drift px @300dpi
    jit_line_rot: float = 0.44  # per-line rotation deg
    jit_gap: float = 1.6  # per-glyph horizontal spacing drift px
    jit_size: float = 0.055  # per-glyph font-size variation (ratio)
    jit_line_gap: float = 2.2  # per-line vertical spacing drift px

    # --- per-category jitter multipliers ---
    # Each multiplier scales the base jit_* values for a glyph category.
    # Set to 0 to disable jitter for that category, >1 to exaggerate.
    jit_mul_cjk: float = 1.2  # Chinese / CJK characters
    jit_mul_latin: float = 0.2  # English letters (A-Z, a-z)
    jit_mul_digit: float = 0  # Numeric digits (0-9)
    jit_mul_symbol: float = 0  # Math symbols, punctuation

    # --- corrections (mistake simulation) ---
    error_rate: float = 0.030  # fraction of glyphs that get corrected
    error_mode: str = "mix"  # "cross" | "blob" | "mix"
    error_subtle: bool = True  # lighter marks that read as pen, not annotation

    # --- latex tooling ---
    xelatex: str = "xelatex"

    # --- fonts (auto-detected on init) ---
    cjk_font_file: str = str(PROJECT_ROOT / "handfont" / "MyHand-Full.ttf")
    latin_font_file: str = str(PROJECT_ROOT / "handfont" / "MyHand-Full.ttf")
    cjk_font_name: str = "MyHand"
    latin_font_name: str = "MyHand"

    # --- font scaling ---
    # Multiply the handwriting font size relative to Latin Modern (math
    # operators / symbols).  Handwriting glyphs sit smaller inside the em than
    # a text font, so a value >1 makes them read as the "main" text.  This is
    # done via fontspec Scale=, NOT by editing the TTF metrics (editing the
    # font's advance/UPM breaks XeLaTeX glyph rendering).
    font_scale: float = 1.0

    # --- MinerU ---
    mineru_bin: str = "mineru"  # CLI name; "mineru" or "mineru-kit"
    mineru_tier: str = "standard"
    modelscope: bool = True  # prefer ModelScope mirrors when downloading
    allow_textlayer_fallback: bool = False  # degrade to raw text when OCR missing

    # --- merge ---
    merge_docs: bool = False  # merge multiple input docs into one output

    # --- job persistence ---
    work_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "work")

    def ensure(self) -> None:
        self.output_dir = Path(self.output_dir)
        self.work_dir = Path(self.work_dir)
        for d in (self.output_dir, self.work_dir):
            d.mkdir(parents=True, exist_ok=True)

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        env = os.environ
        if env.get("HW_INPUT"):
            cfg.input = env["HW_INPUT"]
        if env.get("HW_OUT"):
            cfg.output_dir = Path(env["HW_OUT"])
        if env.get("HW_BG"):
            cfg.bg_mode = env["HW_BG"].lower()
        if env.get("HW_DPI"):
            cfg.dpi_render = int(env["HW_DPI"])
        if env.get("HW_FONT_CJK"):
            cfg.cjk_font_file = env["HW_FONT_CJK"]
        if env.get("HW_FONT_LATIN"):
            cfg.latin_font_file = env["HW_FONT_LATIN"]
        if env.get("HW_FONT_SCALE"):
            cfg.font_scale = float(env["HW_FONT_SCALE"])
        if env.get("HW_INK"):
            cfg.ink_color = env["HW_INK"]
        if env.get("HW_MERGE"):
            cfg.merge_docs = env["HW_MERGE"].lower() in ("1", "true", "yes")
        return cfg


def font_family_of(path: str) -> str | None:
    """Best-effort: map a font file to its family name using fontTools (if present)."""
    try:
        from fontTools.ttLib import TTFont  # type: ignore
    except Exception:
        return None
    try:
        font = TTFont(path, lazy=True)
        name = None
        for record in font["name"].names:
            if record.nameID == 1 and record.platformID in (1, 3):
                try:
                    name = record.toUnicode()
                    break
                except Exception:
                    continue
        return name
    except Exception:
        return None
