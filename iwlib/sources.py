"""Source discovery: PDF files, image piles, .tex sources."""

from __future__ import annotations

from pathlib import Path

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
PDF_EXTS = {".pdf"}
TEX_EXTS = {".tex"}


def discover(inp: Path) -> list[Path]:
    """Return an ordered list of documents (single PDF, .tex, or folder of PDFs/images)."""
    if inp.is_file():
        if inp.suffix.lower() in (IMAGE_EXTS | PDF_EXTS | TEX_EXTS):
            return [inp]
        return []

    if inp.is_dir():
        pdfs = sorted(p for p in inp.rglob("*") if p.suffix.lower() in PDF_EXTS)
        if pdfs:
            return pdfs
        # fallback: treat each image as its own document
        imgs = sorted(p for p in inp.rglob("*") if p.suffix.lower() in IMAGE_EXTS)
        return imgs

    return []
