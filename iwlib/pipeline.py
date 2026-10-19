"""Pipeline orchestration."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import Config, font_family_of
from . import sources, ir, texgen, render, postprocess, ocr

log = logging.getLogger("handwrite")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def build_config(args: argparse.Namespace) -> Config:
    cfg = Config.load()
    cfg.input = args.input
    cfg.bg_mode = args.bg
    cfg.dpi_render = args.dpi
    if args.out:
        cfg.output_dir = Path(args.out)
    if args.font_cjk:
        cfg.cjk_font_file = str(Path(args.font_cjk))
    if args.font_latin:
        cfg.latin_font_file = str(Path(args.font_latin))
    if args.font_scale is not None:
        cfg.font_scale = float(args.font_scale)
    if args.ink:
        cfg.ink_color = args.ink
    if args.skip_ocr:
        cfg.mineru_bin = ""
    if args.textlayer_fallback:
        cfg.allow_textlayer_fallback = True
    if args.tier:
        cfg.mineru_tier = args.tier
    if args.modelscope is not None:
        cfg.modelscope = args.modelscope
    if args.merge:
        cfg.merge_docs = True

    for key, attr in (
        ("cjk_font_file", "cjk_font_name"),
        ("latin_font_file", "latin_font_name"),
    ):
        fam = font_family_of(getattr(cfg, key))
        if fam:
            setattr(cfg, attr, fam)
    cfg.ensure()
    return cfg


def parse_doc(cfg: Config, doc_dir: Path) -> None:
    ocr.parse_with_mineru(cfg, doc_dir)


def _process_single(cfg: Config, doc_path: Path, job_dir: Path) -> int:
    pages = ocr.extract(cfg, doc_path, job_dir)
    if not pages:
        log.warning("no text pages extracted from %s", doc_path.name)
        return 0

    ir_doc = ir.Document.from_pages(pages)
    ir_file = job_dir / "ir.json"
    ir_doc.save(ir_file)
    log.info("IR saved: %s (%d blocks)", ir_file, len(ir_doc.blocks))

    tex_path = texgen.write_tex(cfg, ir_doc, job_dir / "handwrite.tex")
    pdf_path = texgen.compile_tex(cfg, tex_path, job_dir)
    if pdf_path is None:
        log.error("xelatex failed for %s, skipping raster", doc_path.name)
        return 0

    raster_dir = job_dir / "raster"
    raster_dir.mkdir(parents=True, exist_ok=True)
    page_images = render.pdf_to_images(cfg, pdf_path, raster_dir)

    done = postprocess.decorate_pages(cfg, page_images, cfg.output_dir / doc_path.stem)
    if not done:
        log.warning("no output pages produced for %s", doc_path.name)
        return 0

    out_pdf = cfg.output_dir / (doc_path.stem + "_handwritten.pdf")
    postprocess.images_to_pdf(cfg, done, out_pdf)
    log.info("done: %s (+ %d PNGs in %s)", out_pdf, len(done), cfg.output_dir / doc_path.stem)
    return 1


def _merge_name(inp: Path, docs: list[Path]) -> str:
    if inp.is_dir():
        return inp.name
    if len(docs) == 1:
        return docs[0].stem
    return "merged"


def run(cfg: Config) -> int:
    cfg.ensure()
    inp = Path(cfg.input).resolve()
    if not inp.exists():
        log.error("input path does not exist: %s", inp)
        return 2

    docs = sources.discover(inp)
    if not docs:
        log.error("no supported documents found under %s", inp)
        return 2
    log.info("found %d document(s)", len(docs))

    if not cfg.merge_docs or len(docs) <= 1:
        for doc_path in docs:
            log.info("=== processing %s ===", doc_path.name)
            job_dir = cfg.work_dir / doc_path.stem
            job_dir.mkdir(parents=True, exist_ok=True)
            _process_single(cfg, doc_path, job_dir)
        return 0

    # merge mode: collect pages from all documents
    log.info("=== merge mode: combining %d document(s) ===", len(docs))
    all_pages: list[ir.Page] = []
    page_counter = 0
    for doc_path in docs:
        log.info("--- extracting %s ---", doc_path.name)
        job_dir = cfg.work_dir / ("_merge_" + doc_path.stem)
        job_dir.mkdir(parents=True, exist_ok=True)
        pages = ocr.extract(cfg, doc_path, job_dir)
        if not pages:
            log.warning("no text pages extracted from %s, skipping", doc_path.name)
            continue
        for pg in pages:
            pg.index = page_counter
            page_counter += 1
            all_pages.append(pg)

    if not all_pages:
        log.error("no pages extracted from any document")
        return 2

    merged_name = _merge_name(inp, docs)
    merge_job_dir = cfg.work_dir / ("_merged_" + merged_name)
    merge_job_dir.mkdir(parents=True, exist_ok=True)

    ir_doc = ir.Document.from_pages(all_pages)
    ir_file = merge_job_dir / "ir.json"
    ir_doc.save(ir_file)
    log.info("merged IR saved: %s (%d blocks, %d pages)",
             ir_file, len(ir_doc.blocks), len(all_pages))

    tex_path = texgen.write_tex(cfg, ir_doc, merge_job_dir / "handwrite.tex")
    pdf_path = texgen.compile_tex(cfg, tex_path, merge_job_dir)
    if pdf_path is None:
        log.error("xelatex failed for merged document")
        return 1

    raster_dir = merge_job_dir / "raster"
    raster_dir.mkdir(parents=True, exist_ok=True)
    page_images = render.pdf_to_images(cfg, pdf_path, raster_dir)

    done = postprocess.decorate_pages(cfg, page_images, cfg.output_dir / merged_name)
    if not done:
        log.warning("no output pages produced for merged document")
        return 1

    out_pdf = cfg.output_dir / (merged_name + "_handwritten.pdf")
    postprocess.images_to_pdf(cfg, done, out_pdf)
    log.info("done: %s (+ %d PNGs in %s)", out_pdf, len(done), cfg.output_dir / merged_name)

    return 0


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    p = argparse.ArgumentParser(
        prog="handwrite",
        description="Turn digital/printed documents into realistic handwritten pages (Chinese + LaTeX math).",
    )
    p.add_argument("input", help="input PDF file, .tex file, or folder of PDFs/images")
    p.add_argument("-o", "--out", help="output directory (default ./output)")
    p.add_argument("--bg", choices=["plain", "lined", "grid"], default="plain",
                   help="background paper style")
    p.add_argument("--dpi", type=int, default=300, help="output resolution (default 300)")
    p.add_argument("--font-cjk", help="path to a handwriting CJK font (TTF/OTF)")
    p.add_argument("--font-latin", help="path to a handwriting Latin font (TTF/OTF)")
    p.add_argument("--font-scale", type=float, default=None,
                   help="scale the handwriting font vs Latin Modern math (default 1.0; "
                        ">1 makes handwriting larger without editing the TTF metrics)")
    p.add_argument("--ink", default=None, help="ink color hex, e.g. #2B2A29")
    p.add_argument("--tier", default="standard", help="MinerU tier (standard/premium/performance)")
    p.add_argument("--skip-ocr", action="store_true",
                   help="skip MinerU for scanned documents (fail on no text layer)")
    p.add_argument("--textlayer-fallback", action="store_true",
                   help="when MinerU is unavailable, degrade to the raw text layer "
                        "(formulas lose their LaTeX structure)")
    p.add_argument("--modelscope", action=argparse.BooleanOptionalAction, default=True,
                   help="prefer ModelScope mirrors for MinerU model downloads")
    p.add_argument("--merge", action="store_true",
                   help="merge all input documents into a single output (folder input only)")
    args = p.parse_args(argv)

    cfg = build_config(args)
    return run(cfg)
