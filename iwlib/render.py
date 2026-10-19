"""Render compiled PDF pages to high-res images."""

from __future__ import annotations

import logging
from pathlib import Path

import fitz
from PIL import Image

from .config import Config

log = logging.getLogger("handwrite.render")


def pdf_to_images(cfg: Config, pdf_path: Path, out_dir: Path) -> list[Image.Image]:
    out_dir.mkdir(parents=True, exist_ok=True)
    images: list[Image.Image] = []
    doc = fitz.open(pdf_path)
    for idx in range(len(doc)):
        pix = doc[idx].get_pixmap(dpi=cfg.dpi_render)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        png = out_dir / f"page_{idx + 1:03d}.png"
        img.save(png)
        images.append(img)
        log.info("rendered page %d (%dx%d)", idx + 1, pix.width, pix.height)
    doc.close()
    return images
