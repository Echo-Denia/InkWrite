"""Post-processing: ink-alpha extraction, glyph jitter, background paper, aging."""

from __future__ import annotations

import logging
import math
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageEnhance

from .config import Config

log = logging.getLogger("handwrite.post")

INK_DEFAULT = (43, 42, 41, 255)  # #2B2A29


def _to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def ink_alpha(img: Image.Image) -> np.ndarray:
    """Grayscale -> ink alpha (white=0, black=255) with anti-aliasing preserved."""
    gray = np.array(img.convert("L")).astype(np.float32)
    return np.clip(255.0 - gray, 0, 255).astype(np.uint8)


def split_glyphs(alpha: np.ndarray, min_area: int = 12) -> list[tuple[int, int, int, int]]:
    """Find connected ink components.

    Fraction bars, equals signs and underlines are 1-2px tall but wide, so a
    naive min-height filter deletes them. Keep a component when it has enough
    *ink* (area) and either spans several rows or is horizontally long.
    """
    from scipy import ndimage
    bin_ = (alpha > 60).astype(np.uint8)
    labels, n = ndimage.label(bin_, structure=np.ones((3, 3)))
    if n == 0:
        return []
    boxes: list[tuple[int, int, int, int]] = []
    for lab in range(1, n + 1):
        ys, xs = np.where(labels == lab)
        if len(ys) < min_area:
            continue
        y0, y1 = int(ys.min()), int(ys.max())
        x0, x1 = int(xs.min()), int(xs.max())
        gh, gw = y1 - y0 + 1, x1 - x0 + 1
        # thin horizontal strokes (rules, minus, equals, overline) survive
        is_rule = (gh <= 4 and gw >= 6) or (gw >= 25 and gh <= max(3, gw // 8))
        if gh < 2 or gw < 2:
            continue
        if not is_rule and (gh < 5 or gw < 4):
            continue
        boxes.append((x0, y0, x1, y1))
    return boxes


def slant_ink(alpha: np.ndarray, angle_deg: float) -> np.ndarray:
    """Shear the whole page horizontally: the single strongest 'handwritten' cue.

    Real handwriting slants *within the text block*, not across the full page
    height, so the shear is centred on the text's vertical midpoint and the
    maximum shift is capped to keep glyphs on the page.
    """
    h, w = alpha.shape
    if abs(angle_deg) < 1e-3:
        return alpha

    # vertical centre of the ink block
    rows_with_ink = (alpha > 40).sum(axis=1)
    ys = np.nonzero(rows_with_ink > 0)[0]
    if len(ys) == 0:
        return alpha
    yc = (ys[0] + ys[-1]) / 2.0
    span = max(1.0, ys[-1] - ys[0])

    t = np.tan(np.radians(angle_deg))
    # shift grows from the block centre, capped so nothing leaves the canvas
    max_shift = int(w * 0.05)
    off = np.clip((np.arange(h, dtype=np.float32) - yc) * t,
                  -max_shift, max_shift).astype(int)

    out = np.zeros_like(alpha)
    for y in range(h):
        s = off[y]
        if s == 0:
            out[y] = alpha[y]
        elif s > 0:
            out[y, s:] = alpha[y, :w - s]
        else:
            out[y, :w + s] = alpha[y, -s:]
    return out


def find_text_lines(alpha: np.ndarray, min_run: int = 6,
                    max_band_h: int = 0) -> list[tuple[int, int]]:
    """Row bands that contain ink -> (y0, y1) per text line.

    Bands are split at empty rows. If a band is taller than max_band_h it is
    split again at its thinnest ink row, which prevents a formula line and the
    body line next to it from being fused into one oversized band (rotating
    such a band crops the upper line).
    """
    rows = (alpha > 40).sum(axis=1)
    active = rows > 3
    lines: list[tuple[int, int]] = []
    y = 0
    h = len(active)
    while y < h:
        if active[y]:
            y0 = y
            gap = 0
            while y < h:
                if active[y]:
                    gap = 0
                else:
                    gap += 1
                    if gap >= min_run:
                        break
                y += 1
            y1 = y - gap
            if y1 - y0 >= min_run:
                lines.append((y0, y1))
        else:
            y += 1

    if max_band_h > 0:
        split: list[tuple[int, int]] = []
        for (y0, y1) in lines:
            band = rows[y0:y1]
            while y1 - y0 > max_band_h:
                # thinnest row inside the band = best split point
                rel = int(np.argmin(band))
                cut = y0 + rel
                if cut <= y0 + 2 or cut >= y1 - 2:
                    cut = y0 + max_band_h // 2
                split.append((y0, cut))
                y0 = cut
            split.append((y0, y1))
        lines = split
    return lines


def wobble_lines(alpha: np.ndarray, cfg: Config, rng: random.Random) -> np.ndarray:
    """Per-line rotation, baseline drift and *randomised leading*.

    Handwriting never keeps an even baseline pitch, so the vertical offset of
    each line is a random walk rather than independent noise. Bands are moved
    rigidly (never expanded) so glyph heights are never cropped.
    """
    h, w = alpha.shape
    lines = find_text_lines(alpha, max_band_h=_max_band_h(alpha))
    if not lines:
        return alpha

    out = np.zeros_like(alpha)
    img = Image.fromarray(alpha, "L")

    # random-walk leading: each line's offset depends on the previous one
    drift = 0.0
    for (y0, y1) in lines:
        bh = y1 - y0
        pad = int(bh * 0.35) + 2
        cy0, cy1 = max(0, y0 - pad), min(h, y1 + pad)
        band = img.crop((0, cy0, w, cy1))

        theta = rng.gauss(0, cfg.jit_line_rot)
        band = band.rotate(theta, resample=Image.BICUBIC, expand=False, fillcolor=0)
        arr = np.array(band)

        drift += rng.gauss(0, cfg.jit_line_gap * 0.5)
        drift = max(-cfg.jit_line_gap * 2.0, min(cfg.jit_line_gap * 2.0, drift))
        dy = int(round(rng.gauss(0, cfg.jit_baseline) + drift))
        dx = int(round(rng.gauss(0, cfg.jit_baseline * 0.8)))

        src_y0 = pad
        src_y1 = pad + bh
        tgt_y0 = cy0 + dy
        if tgt_y0 < 0:
            src_y0 -= tgt_y0
            tgt_y0 = 0
        tgt_y1 = tgt_y0 + (src_y1 - src_y0)
        if tgt_y1 > h:
            src_y1 -= (tgt_y1 - h)
            tgt_y1 = h
        if src_y1 <= src_y0:
            continue

        src = arr[src_y0:src_y1]
        if dx > 0:
            out[tgt_y0:tgt_y1, dx:] = np.maximum(out[tgt_y0:tgt_y1, dx:], src[:, :w - dx])
        elif dx < 0:
            out[tgt_y0:tgt_y1, :w + dx] = np.maximum(out[tgt_y0:tgt_y1, :w + dx], src[:, -dx:])
        else:
            out[tgt_y0:tgt_y1] = np.maximum(out[tgt_y0:tgt_y1], src)
    return out


def _max_band_h(alpha: np.ndarray) -> int:
    """Cap band height at ~1.8x the median band so neighbours never fuse."""
    rows = (alpha > 40).sum(axis=1)
    active = rows > 3
    heights: list[int] = []
    y, h = 0, len(active)
    while y < h:
        if active[y]:
            y0, gap = y, 0
            while y < h:
                if active[y]:
                    gap = 0
                else:
                    gap += 1
                    if gap >= 6:
                        break
                y += 1
            heights.append(y - gap - y0)
        else:
            y += 1
    if not heights:
        return 0
    return max(24, int(np.median(heights) * 1.8))


def _classify_glyph(gw: int, gh: int, median_area: float) -> str:
    """Classify a glyph by its bounding-box proportions.

    Returns one of 'cjk', 'latin', 'digit', 'symbol'.
    Heuristic — no OCR is run, so classification is approximate.

    Aspect-ratio ranges observed at 300 dpi (approximate):
      CJK      0.65 – 1.54  (roughly square, largest area)
      digit    0.12 – 0.55  (narrower than letters; "1" is extreme, "0" ~0.50)
      latin    0.55 – 0.72  (taller-than-wide; A-Z, a-z)
      symbol   outside those, or very small
    """
    aspect = gw / gh if gh > 0 else 1.0
    area = gw * gh
    min_area = max(55, median_area * 0.18)

    # punctuation, diacritics, tiny fragments
    if area < min_area:
        return "symbol"
    # wide shapes → operators, rules, arrows
    if aspect > 2.4:
        return "symbol"
    # roughly square & reasonably sized → CJK
    if 0.65 <= aspect <= 1.54 and area >= median_area * 0.45:
        return "cjk"
    # moderately wide → symbol (e.g. "=", "→")
    if aspect > 1.54:
        return "symbol"
    # tall/narrow → digit vs latin
    if area >= median_area * 0.15:
        # Digits are narrower than letters in virtually every handwriting font
        if aspect <= 0.55:
            return "digit"
        return "latin"
    return "symbol"


def jitter_ink(alpha: np.ndarray, cfg: Config, rng: random.Random) -> np.ndarray:
    """Handwriting treatment.

    slant -> per-line wobble (with random leading) -> per-glyph size/spacing/
    rotation jitter -> occasional crossed-out mistakes.

    Per-category multipliers (jit_mul_*) scale the base jitter values so
    different glyph types can drift independently.
    """
    alpha = slant_ink(alpha, rng.uniform(-cfg.jit_slant, cfg.jit_slant))
    alpha = wobble_lines(alpha, cfg, rng)

    h, w = alpha.shape
    canvas = np.zeros((h, w), dtype=np.float32)
    boxes = split_glyphs(alpha)
    if not boxes:
        return alpha

    # compute median glyph area for classification threshold
    areas = [(x1 - x0 + 1) * (y1 - y0 + 1) for (x0, y0, x1, y1) in boxes]
    median_area = float(np.median(areas)) if areas else 400.0

    # base jitter values
    dx = cfg.jit_dx
    dy = cfg.jit_dy
    rot = math.radians(cfg.jit_rot)
    size_jit = cfg.jit_size

    # per-category multipliers
    mul = {
        "cjk": cfg.jit_mul_cjk,
        "latin": cfg.jit_mul_latin,
        "digit": cfg.jit_mul_digit,
        "symbol": cfg.jit_mul_symbol,
    }

    boxes.sort(key=lambda b: (b[1], b[0]))

    for (x0, y0, x1, y1) in boxes:
        gw, gh = x1 - x0 + 1, y1 - y0 + 1
        cat = _classify_glyph(gw, gh, median_area)
        m = mul.get(cat, 1.0)

        # random size (handwriting never repeats the exact glyph size)
        sx = 1.0 + rng.gauss(0, size_jit * m)
        sy = 1.0 + rng.gauss(0, size_jit * m)
        nx = max(1, int(round(gw * sx)))
        ny = max(1, int(round(gh * sy)))
        g = Image.fromarray(alpha[y0:y1 + 1, x0:x1 + 1], mode="L")
        g = g.resize((nx, ny), Image.LANCZOS)

        theta = rng.uniform(-rot, rot) * m
        if abs(theta) > 1e-6:
            g = g.rotate(theta, resample=Image.BICUBIC, expand=False, fillcolor=0)

        # random inter-glyph spacing on top of the positional jitter
        ox = int(round(x0 + rng.gauss(0, dx * m) + rng.gauss(0, cfg.jit_gap * m)))
        oy = int(round(y0 + rng.gauss(0, dy * m)))
        g = np.array(g, dtype=np.float32)
        g = g * rng.uniform(cfg.jit_alpha_min, 1.0)
        gh_, gw_ = g.shape
        xa, ya = max(0, ox), max(0, oy)
        xb, yb = min(w, ox + gw_), min(h, oy + gh_)
        if xb <= xa or yb <= ya:
            continue
        ga, gb = xa - ox, xb - ox
        gya, gyb = ya - oy, yb - oy
        canvas[ya:yb, xa:xb] = np.maximum(canvas[ya:yb, xa:xb], g[gya:gyb, ga:gb])

    out = np.clip(canvas, 0, 255).astype(np.uint8)

    # ---- mistake marks -----------------------------------------------------
    # Real corrections (wrong character + struck out) are injected in LaTeX
    # by mistake.py so the wrong glyph is a real character with the right one
    # written next to it. Here we only add faint pen traces so the raster does
    # not look sterile.
    if cfg.error_rate > 0:
        big_boxes = [b for b in boxes if (b[2] - b[0]) >= 14 and (b[3] - b[1]) >= 16]
        if big_boxes:
            n_trace = max(1, int(len(big_boxes) * cfg.error_rate * 0.5))
            for (x0, y0, x1, y1) in rng.sample(big_boxes, min(n_trace, len(big_boxes))):
                _pen_trace(out, x0, y0, x1, y1, rng)
    return out


def _pen_trace(canvas: np.ndarray, x0: int, y0: int, x1: int, y1: int,
               rng: random.Random) -> None:
    """A faint, lazy stroke across a glyph — a lingering smudge, not a mark."""
    h, w = canvas.shape
    if rng.random() < 0.5:
        # short diagonal flick
        y = rng.randint(y0, y1)
        xa = max(0, x0 - 2)
        xb = min(w, x1 + 3)
        if xb - xa < 4:
            return
        for px in range(xa, xb):
            t = (px - xa) / max(1, xb - xa - 1)
            py = int(y + (t - 0.5) * (y1 - y0) * 0.35)
            if 0 <= py < h:
                canvas[py, px] = min(255, int(canvas[py, px]) + rng.randint(90, 150))
    else:
        # light horizontal smear over the lower half
        xa, xb = max(0, x0 - 2), min(w, x1 + 3)
        ya, yb = max(0, y1 - (y1 - y0) // 2), min(h, y1 + 1)
        if xb <= xa or yb <= ya:
            return
        band = canvas[ya:yb, xa:xb]
        mask = rng.random() < 0.55
        if mask:
            canvas[ya:yb, xa:xb] = np.clip(
                band.astype(np.int16) + rng.randint(70, 120), 0, 255).astype(np.uint8)


def make_background(w: int, h: int, cfg: Config, rng: random.Random) -> Image.Image:
    """Plain off-white, ruled lines, or grid paper."""
    top, bottom = cfg.bg_gradient
    bg = np.zeros((h, w, 3), dtype=np.float32)
    ramp = np.linspace(top, bottom, h).astype(np.float32)
    bg[:, :, 0] = ramp[:, None]
    bg[:, :, 1] = ramp[:, None] * 1.005
    bg[:, :, 2] = ramp[:, None] * 1.01

    img = Image.fromarray(np.clip(bg * 255, 0, 255).astype(np.uint8))
    draw = ImageDraw.Draw(img)
    if cfg.bg_mode == "lined":
        ls = int(round(h / 22))
        col = cfg.bg_line_color
        for y in range(ls, h, ls):
            draw.line([(0, y), (w, y)], fill=col, width=1)
    elif cfg.bg_mode == "grid":
        gs = int(round(h / 24))
        col = cfg.bg_grid_color
        for y in range(gs, h, gs):
            draw.line([(0, y), (w, y)], fill=col, width=1)
        for x in range(gs, w, gs):
            draw.line([(x, 0), (x, h)], fill=col, width=1)

    # subtle paper grain
    grain = np.random.normal(0, 2.5, (h, w, 1))
    arr = np.array(img).astype(np.float32)
    arr = arr + grain
    arr = np.clip(arr, 0, 255)
    return Image.fromarray(arr.astype(np.uint8))


def composite(paper: Image.Image, alpha: np.ndarray, ink: tuple[int, int, int]) -> Image.Image:
    """Draw ink over paper using alpha mask."""
    ink_arr = np.zeros((alpha.shape[0], alpha.shape[1], 4), dtype=np.uint8)
    ink_arr[:, :, 0] = ink[0]
    ink_arr[:, :, 1] = ink[1]
    ink_arr[:, :, 2] = ink[2]
    ink_arr[:, :, 3] = alpha
    ink_img = Image.fromarray(ink_arr, "RGBA")
    paper = paper.convert("RGBA")
    out = Image.alpha_composite(paper, ink_img)
    return out.convert("RGB")


def aging(img: Image.Image, cfg: Config, rng: random.Random) -> Image.Image:
    """Blur, noise, uneven lighting, slight rotation."""
    theta = rng.uniform(-cfg.jitter_rot, cfg.jitter_rot)
    if abs(theta) > 1e-3:
        # sample the paper colour so rotated corners match the background
        corner = np.array(img.convert("RGB"))[2, 2]
        img = img.rotate(theta, resample=Image.BICUBIC, expand=False,
                         fillcolor=tuple(int(c) for c in corner))

    if cfg.blur_radius > 0:
        img = img.filter(ImageFilter.GaussianBlur(cfg.blur_radius))

    arr = np.array(img).astype(np.float32)
    noise = np.random.normal(0, cfg.noise_sigma, arr.shape)
    arr = arr + noise

    # low-frequency illumination gradient
    h, w = arr.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    light = 1.0 + 0.05 * np.sin(xx / w * np.pi * rng.uniform(1, 3)) \
                  * np.cos(yy / h * np.pi * rng.uniform(1, 3))
    arr = arr * light[..., None]
    arr = np.clip(arr, 0, 255)
    return Image.fromarray(arr.astype(np.uint8))


def decorate_page(page: Image.Image, cfg: Config, rng: random.Random) -> Image.Image:
    """Full pipeline for one rendered page image."""
    alpha = ink_alpha(page)
    if alpha.max() < 10:
        # blank page: keep paper only
        paper = make_background(page.width, page.height, cfg, rng)
        return aging(paper, cfg, rng)

    jittered = jitter_ink(alpha, cfg, rng)
    ink = _to_rgb(cfg.ink_color)
    paper = make_background(page.width, page.height, cfg, rng)
    composed = composite(paper, jittered, ink)
    return aging(composed, cfg, rng)


def decorate_pages(cfg: Config, images: list[Image.Image], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    rng = random.Random()
    for i, img in enumerate(images):
        decorated = decorate_page(img, cfg, rng)
        p = out_dir / f"page_{i + 1:03d}.png"
        decorated.save(p)
        written.append(p)
        log.info("decorated page %d -> %s", i + 1, p)
    return written


def images_to_pdf(cfg: Config, paths: list[Path], out_pdf: Path) -> None:
    import fitz
    doc = fitz.open()
    for p in paths:
        page = doc.new_page(width=595, height=842)
        rect = page.rect
        page.insert_image(rect, filename=str(p))
    doc.save(out_pdf, deflate=True)
    doc.close()
    log.info("wrote %s", out_pdf)
