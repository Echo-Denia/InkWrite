#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
05_build_pixel_font.py  ——  像素级保真出字体（不经过 potrace 矢量化）
====================================================================
背景：
  04_build_font.py 用 potrace 追贝塞尔，会把 2~3px 的细笔画糊成墨团 / 断笔。
  而 data/glyphs/<cp>.png（review.html / cleaner.html 里看到的图）本身是干净的。
  本脚本跳过矢量化，直接把位图转成「像素矩形并集」的 TrueType 轮廓：
    每一行里每一段连续黑像素 -> 一个矩形轮廓。
  这样得到的字形与源图逐像素一致，任何渲染器/任何字号都正常显示，
  且用的是 fontTools 现成的 setupGlyf，不碰脆弱的 EBLC/EBDT 手工编码。

优点：像素级保真、无墨团、无断笔、无需 hinting 也清晰。
代价：轮廓点数比贝塞尔多（平均每字 ~180 个矩形），但字体体积/性能完全可接受。

输入：data/glyphs/<cp>.png（白底黑字；来自 03_slice_sheet.py 或 cleaner.html 改过的）
输出：MyHand-Regular.ttf + preview.html
"""

import argparse
import csv
import glob
import json
import os
import sys

import numpy as np
from PIL import Image

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# 必须与 03_slice_sheet.py 的裁剪策略一致：
#   03 现在默认 INSET_MM = 0（沿格线裁），所以这里 INSET 也必须是 0，
#   否则 cell_px = iw/(1-2*INSET) 会把整格尺寸算大，拉丁/符号的字会偏大。
#   如果 03 用了 --inset-mm 0.5 之类，这里要同步改成 0.5/格子毫米数。
INSET = 0.0
# 基线在单字图里的高度占比。INSET=0 时「格底往上 25%」就是 0.75。
BASELINE_IN_IMG = 0.75 - INSET
UNITS_PER_EM = 1000
ASCENT, DESCENT = 800, -200
SIDE_BEARING = 55        # 拉丁/符号的左右边距（em 千分单位）
THRESH = 128            # <128 视为墨


# ---------------------------------------------------------------- 位图 -> 矩形轮廓
def horizontal_runs(mask):
    """
    返回每个横向连续墨段的矩形列表 [(x0, y0, w, h), ...]，坐标为像素、y 向下。
    只切「横向段」，不做纵向合并（保持简单、点数可控）。
    """
    runs = []
    h, w = mask.shape
    for y in range(h):
        row = mask[y]
        x = 0
        while x < w:
            if row[x]:
                x0 = x
                while x < w and row[x]:
                    x += 1
                runs.append((x0, y, x - x0, 1))
            else:
                x += 1
    return runs


def rects_to_ops(runs, scale, dx, baseline_px):
    """
    像素矩形 -> 绝对坐标轮廓命令（字体单位，y 向上，基线归零）。
    每个矩形是一个闭合子路径（TrueType 用逆时针=填充）。
    """
    ops = []
    for (x, y, w, h) in runs:
        x0 = x * scale + dx
        x1 = (x + w) * scale + dx
        # y 向下 -> 向上；矩形顶端 y，底端 y+h
        ytop = (baseline_px - y) * scale
        ybot = (baseline_px - (y + h)) * scale
        ops.append(("M", [(x0, ybot)]))
        ops.append(("L", [(x1, ybot)]))
        ops.append(("L", [(x1, ytop)]))
        ops.append(("L", [(x0, ytop)]))
        ops.append(("Z", []))
    return ops


# ---------------------------------------------------------------- 载入 charset
def load_cell_type():
    meta = {}
    cp_meta = {}
    cs_path = os.path.join(ROOT, "charset", "charset.json")
    if os.path.exists(cs_path):
        with open(cs_path, encoding="utf-8") as f:
            for r in json.load(f):
                cp_meta[r["cp"]] = r
    mani = os.path.join(ROOT, "data", "glyph_manifest.csv")
    cell_of = {}
    if os.path.exists(mani):
        with open(mani, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                cell_of[int(r["cp"])] = cp_meta.get(int(r["cp"]), {}).get("cell", "han")

    import unicodedata

    def guess(cp):
        if cp in cell_of:
            return cell_of[cp]
        ch = chr(cp)
        return "han" if unicodedata.east_asian_width(ch) in "WF" else "lat"

    return guess


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glyphs", default=os.path.join(ROOT, "data", "glyphs"))
    ap.add_argument("--out", default=os.path.join(ROOT, "MyHand-Regular.ttf"))
    ap.add_argument("--family", default="MyHand")
    ap.add_argument("--baseline", type=float, default=BASELINE_IN_IMG)
    ap.add_argument("--em-scale", type=float, default=1.0,
                    help="整体放大系数。手写字通常只占字格 55%% 高度，而标准字体占 85%%+；"
                         "设 1.5 左右可让手写体与标准字体混排时大小相当。>1 有截断风险")
    args = ap.parse_args()
    baseline_frac = args.baseline
    em_scale = args.em_scale

    files = sorted(glob.glob(os.path.join(args.glyphs, "*.png")),
                   key=lambda p: int(os.path.basename(p)[:-4]))
    if not files:
        sys.exit("没有找到单字位图：%s" % args.glyphs)

    guess_cell = load_cell_type()

    glyphs, cmap, hmtx = {}, {}, {}
    order = [".notdef"]
    pen0 = TTGlyphPen(None)
    glyphs[".notdef"] = pen0.glyph()
    hmtx[".notdef"] = (UNITS_PER_EM, 0)

    fails = []
    stats = []
    for p in files:
        cp = int(os.path.basename(p)[:-4])
        cell_type = guess_cell(cp)
        try:
            mask = (np.array(Image.open(p).convert("L")) < THRESH).astype(np.uint8)
            if mask.sum() == 0:
                raise RuntimeError("空图")
            ih, iw = mask.shape
            cell_px = iw / (1 - 2 * INSET)          # 还原整格边长（像素）
            scale = UNITS_PER_EM * em_scale / cell_px   # 像素 -> 字体单位（含放大）
            baseline_px = baseline_frac * ih

            runs = horizontal_runs(mask)
            xs = []
            for (x, y, w, h) in runs:
                xs.append(x); xs.append(x + w)
            min_x, max_x = min(xs), max(xs)
            bw = (max_x - min_x) * scale
            if cell_type == "han":
                advance = UNITS_PER_EM
                lsb = (UNITS_PER_EM - bw) / 2.0
            else:
                advance = bw + 2 * SIDE_BEARING
                lsb = SIDE_BEARING
            dx = lsb - min_x * scale

            ops = rects_to_ops(runs, scale, dx, baseline_px)
            pen = TTGlyphPen(None)
            for cmd, pts in ops:
                if cmd == "M":
                    pen.moveTo(pts[0])
                elif cmd == "L":
                    for pt in pts:
                        pen.lineTo(pt)
                elif cmd == "Z":
                    pen.closePath()
            name = "uni%04X" % cp
            glyphs[name] = pen.glyph()
            cmap[cp] = name
            hmtx[name] = (int(round(advance)), int(round(lsb)))
            order.append(name)
            stats.append(len(runs))
        except Exception as e:
            fails.append((cp, str(e)[:80]))

    fb = FontBuilder(UNITS_PER_EM, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(hmtx)
    # em_scale>1 时字形会超出默认 ascent/descent，行高同步放大避免行重叠
    ascent = int(round(ASCENT * em_scale))
    descent = int(round(DESCENT * em_scale))
    fb.setupHorizontalHeader(ascent=ascent, descent=descent, lineGap=0)
    fb.setupNameTable({
        "familyName": args.family,
        "styleName": "Regular",
        "uniqueFontIdentifier": "%s-Regular" % args.family,
        "fullName": "%s Regular" % args.family,
        "psName": "%s-Regular" % args.family,
        "version": "Version 1.000",
    })
    fb.setupOS2(sTypoAscender=ascent, sTypoDescender=descent, sTypoLineGap=0,
                usWinAscent=ascent, usWinDescent=-descent,
                sxHeight=int(round(500 * em_scale)),
                capHeight=int(round(700 * em_scale)))
    fb.setupPost()
    fb.save(args.out)

    if stats:
        print("矩形轮廓点数：平均 %d，最大 %d" % (sum(stats) // len(stats), max(stats)))
    print("\n[ok] 像素字体：", args.out)
    print("    收录字符 %d 个，失败 %d 个" % (len(cmap), len(fails)))
    if fails:
        print("    失败样例：", fails[:8])
        with open(os.path.join(ROOT, "data", "build_failed.csv"), "w", encoding="utf-8-sig") as f:
            f.write("cp,hex,char,reason\n")
            for cp, why in fails:
                f.write("%d,U+%04X,%s,%s\n" % (cp, cp, chr(cp), why))

    # 预览页
    import base64
    with open(args.out, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    html = """<!doctype html><meta charset="utf-8"><title>%s 预览</title>
<style>
@font-face{font-family:MyHand;src:url(data:font/ttf;base64,%s) format('truetype');}
body{font-family:MyHand,sans-serif;margin:32px;line-height:1.9;background:#fff;color:#111}
h1{font-size:28px}.s{font-size:44px;margin:18px 0}
.small{font-size:20px}.box{border:1px solid #ddd;padding:16px;margin:12px 0;border-radius:8px}
label{font-family:sans-serif;font-size:12px;color:#888;display:block}
</style>
<h1>%s &mdash; 手写字体预览</h1>
<div class="box"><label>中文 44px</label><div class="s">春风又绿江南岸，明月何时照我还。</div></div>
<div class="box"><label>中文 20px</label><div class="small">手写字体在小字号下最容易暴露基线不齐、字重不均的问题，这一行专门用来检查。</div></div>
<div class="box"><label>English</label><div class="s">The quick brown fox jumps over the lazy dog. 0123456789</div></div>
<div class="box"><label>数学符号</label><div class="s">
f(x)=\\int_{a}^{b} \\sum_{i=1}^{n} x_i\\,dx \\quad \\forall x \\in \\mathbb{R},\\ \\alpha+\\beta \\leq \\gamma \\approx \\infty
</div></div>
<div class="box"><label>希腊字母</label><div class="s">&Alpha;&Beta;&Gamma;&Delta; &alpha;&beta;&gamma;&delta;&theta;&lambda;&mu;&pi;&sigma;&omega;&phi;</div></div>
""" % (args.family, b64, args.family)
    with open(os.path.join(ROOT, "preview.html"), "w", encoding="utf-8") as f:
        f.write(html)
    print("[ok] 预览页：", os.path.join(ROOT, "preview.html"))


if __name__ == "__main__":
    main()
