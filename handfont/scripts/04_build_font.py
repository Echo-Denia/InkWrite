#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
04_build_font.py  ——  【建议在远程 Linux 上跑】
===============================================
输入：03 切好的单字位图 data/glyphs/<cp>.png
输出：可直接安装的 MyHand-Regular.ttf + 预览页 preview.html

为什么建议在 Linux 上跑：
  * FontForge 的 Windows 版是 32 位，处理 3000+ 字的大字库时超过约 2.1GB 内存就崩；
    Linux / macOS 一直是 64 位，几千字毫无压力。
  * 本脚本不依赖 FontForge，只用 potrace + fontTools 两个纯命令行/纯 Python 工具，
    跨平台一致，且 potrace 在 Linux 上一条 apt 就能装。

矢量化策略：
  优先调用系统 potrace（贝塞尔输出，笔画平滑，质量明显更好）；
  找不到 potrace 时自动回退到 OpenCV 轮廓提取（多边形，能用但边缘偏硬）。
"""

import argparse
import base64
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import cv2
import numpy as np

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# potrace 便携版随工程附带（lib/potrace-1.16.win64/），但它不在系统 PATH 上。
# 这里主动把它加进去，否则 have_potrace() 返回 False，会静默退化成 OpenCV
# 多边形轮廓（直线段、笔画发硬），出片质量明显下降。
_POTRACE_DIR = os.path.join(ROOT, "lib", "potrace-1.16.win64")
if os.path.isdir(_POTRACE_DIR) and _POTRACE_DIR not in os.environ.get("PATH", ""):
    os.environ["PATH"] = os.environ["PATH"] + os.pathsep + _POTRACE_DIR

# 必须与 03_slice_sheet.py 的裁剪策略一致：
#   03 现在默认 INSET_MM = 0（沿格线裁），所以这里 INSET 也必须是 0，
#   否则 cell_px = img_w/(1-2*INSET) 会把整格尺寸算大，拉丁/符号的字会偏大。
#   如果 03 用了 --inset-mm 0.5 之类，这里要同步改成 0.5/格子毫米数。
INSET = 0.0
# 基线在单字图里的高度占比。INSET=0 时「格底往上 25%」就是 0.75。
BASELINE_IN_IMG = 0.75 - INSET
UNITS_PER_EM = 1000
ASCENT, DESCENT = 800, -200
SIDE_BEARING = 55        # 拉丁/符号的左右边距（em 千分单位）
CU2QU_MAX_ERR = 0.5      # 三次->二次贝塞尔降阶的最大容许误差（字体单位）


# ------------------------------------------------------------------ 矢量化
def have_potrace():
    return shutil.which("potrace") is not None


UPSCALE = 3      # 矢量化前放大倍数。用 NEAREST（不用 LANCZOS）：细笔画只有 1~2px，
                 # LANCZOS 会把相邻细笔画糊成一团（墨团）；NEAREST 保持笔画分明，
                 # 又能给 potrace 足够像素去追平滑曲线。svg_d_to_pixel 按 viewBox
                 # 比例缩回原像素系，不影响最终坐标。

def png_to_pgm(src, dst):
    import PIL.Image
    im = PIL.Image.open(src).convert("L")
    if UPSCALE != 1:
        im = im.resize((im.width * UPSCALE, im.height * UPSCALE), PIL.Image.NEAREST)
    # potrace 接受 PGM(P5)
    a = np.array(im)
    with open(dst, "wb") as f:
        f.write(b"P5\n%d %d\n255\n" % (a.shape[1], a.shape[0]))
        f.write(a.tobytes())
    return a.shape[1], a.shape[0]


def _run_potrace(pgm, svg, invert=False):
    # --turdsize 8（配合 UPSCALE=4，等效原图 2px）：丢弃极小噪点，保留真实细笔画
    cmd = ["potrace", pgm, "-s", "--turdsize", str(2 * UPSCALE), "-o", svg]
    if invert:
        cmd.insert(2, "--invert")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(svg):
        raise RuntimeError(r.stderr[:300])
    with open(svg, encoding="utf-8") as f:
        return f.read()


def vectorize_potrace(png, svg):
    """
    potrace 默认把「暗像素」当前景；我们的图是白底黑字，正常即可。
    但不同发行版/版本行为有差异，所以做一个反色自检：
    若输出轮廓的包围盒几乎铺满整格，说明把背景当成了字，自动加 --invert 重跑。
    """
    with tempfile.TemporaryDirectory() as td:
        pgm = os.path.join(td, "g.pgm")
        w, h = png_to_pgm(png, pgm)
        txt = _run_potrace(pgm, svg, invert=False)
        ops, _ = parse_svg_path(txt)
        ops = svg_d_to_pixel(ops, txt, w, h)      # 先回到像素系再判断
        pts = [p for _, ps in ops for p in ps]
        if pts:
            xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
            if (max(xs) - min(xs)) > 0.92 * w and (max(ys) - min(ys)) > 0.92 * h:
                txt = _run_potrace(pgm, svg, invert=True)
    return txt


def vectorize_cv(png):
    """
    回退方案：轮廓 -> 近似多边形 -> SVG（只有直线段）。
    RETR_CCOMP 会把「洞」也取出来，这里全部保留：TrueType 用非零环绕规则，
    外轮廓与洞的方向相反，正好挖空（如「口」「中」的中间）。
    """
    a = cv2.imread(png, cv2.IMREAD_GRAYSCALE)
    _, bw = cv2.threshold(a, 128, 255, cv2.THRESH_BINARY_INV)
    cnts, _ = cv2.findContours(bw, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    parts = []
    for c in cnts:
        peri = cv2.arcLength(c, True)
        if peri < 8:
            continue
        eps = max(0.5, peri * 0.0035)
        ap = cv2.approxPolyDP(c, eps, True)
        pts = ap.reshape(-1, 2)
        if len(pts) < 3:
            continue
        d = "M " + " L ".join("%.2f %.2f" % (p[0], p[1]) for p in pts) + " Z"
        parts.append(d)
    return '<svg xmlns="http://www.w3.org/2000/svg"><path d="%s"/></svg>' % " ".join(parts)


# ------------------------------------------------------------- SVG path 解析
_PATH_RE = re.compile(r"([MmLlCcZzSsQqTtHhVvAa])\s*([^MmLlCcZzSsQqTtHhVvAa]*)")
_NUM_RE = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def parse_svg_path(svg_text):
    """
    把 SVG path 解析成绝对坐标的 [(cmd, [(x,y), ...]), ...]。

    这是 Bug A 的修复点。旧实现有两个致命问题：

      1. 不分大小写：`c`（相对三次贝塞尔）被当成 `C`（绝对），
         于是 potrace 输出的「增量」被当作「绝对坐标」累加，轮廓直接散架。
      2. 不维护当前点：绝对/相对混排时坐标全部错位。

    现在严格按 SVG 规范处理：
      * 大写 = 绝对坐标，小写 = 相对当前点
      * M/L/C 显式支持；另外把 Q/T/S/H/V/A 一并处理，避免遇到非 potrace 输出时静默出错
      * 返回值里的坐标全为「绝对」，调用方不必再关心大小写差异
    """
    ds = re.findall(r'<path[^>]*\sd="([^"]+)"', svg_text, re.S)
    d = " ".join(ds)
    out = []
    allpts = []

    cur = (0.0, 0.0)        # 当前点
    start = (0.0, 0.0)      # 当前子路径起点（Z / 隐式重复用）

    def rel(pt):
        return (cur[0] + pt[0], cur[1] + pt[1])

    for raw_cmd, raw in _PATH_RE.findall(d):
        cmd = raw_cmd.upper()
        nums = [float(x) for x in _NUM_RE.findall(raw)]
        raw_pairs = [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]

        if cmd == "Z":
            out.append(("Z", []))
            cur = start
            continue

        if cmd == "H":                      # 水平线：只给 x
            pts = [(nums[i], 0.0) for i in range(len(nums))] if raw_cmd == "H" \
                else [(nums[i], 0.0) for i in range(len(nums))]
            conv = []
            for (px, _ign) in pts:
                target = (px, cur[1]) if raw_cmd == "H" else (cur[0] + px, cur[1])
                conv.append(target)
            out.append(("L", conv))
            if conv:
                cur = conv[-1]
                allpts += conv
            continue

        if cmd == "V":                      # 垂直线：只给 y
            conv = []
            for py in nums:
                target = (cur[0], py) if raw_cmd == "V" else (cur[0], cur[1] + py)
                conv.append(target)
            out.append(("L", conv))
            if conv:
                cur = conv[-1]
                allpts += conv
            continue

        if cmd == "Q":                      # 二次贝塞尔 -> 升阶成三次
            conv = []
            for i in range(0, len(raw_pairs), 2):
                if i + 1 >= len(raw_pairs):
                    break
                ctrl, end = raw_pairs[i], raw_pairs[i + 1]
                if raw_cmd == "q":
                    ctrl, end = rel(ctrl), rel(end)
                # 二次 -> 三次的升阶公式
                c1 = (cur[0] + 2.0 / 3.0 * (ctrl[0] - cur[0]),
                      cur[1] + 2.0 / 3.0 * (ctrl[1] - cur[1]))
                c2 = (end[0] + 2.0 / 3.0 * (ctrl[0] - end[0]),
                      end[1] + 2.0 / 3.0 * (ctrl[1] - end[1]))
                conv += [c1, c2, end]
                cur = end
            if conv:
                out.append(("C", conv))
                allpts += conv
            continue

        if cmd == "C":                      # 本流水线真正会遇到的主力指令
            conv = []
            for i in range(0, len(raw_pairs), 3):
                trio = raw_pairs[i:i + 3]
                if len(trio) < 3:
                    break
                if raw_cmd == "c":
                    trio = [rel(p) for p in trio]
                conv += trio
                cur = trio[-1]
            if conv:
                out.append(("C", conv))
                allpts += conv
            continue

        if cmd == "L":
            conv = [rel(p) if raw_cmd == "l" else p for p in raw_pairs]
            if conv:
                out.append(("L", conv))
                cur = conv[-1]
                allpts += conv
            continue

        if cmd == "M":
            conv = []
            for i, p in enumerate(raw_pairs):
                target = p if raw_cmd == "M" else rel(p)
                # M 之后隐式跟着的是 L
                conv.append(target)
                cur = target
                if i == 0:
                    start = target
            if conv:
                out.append(("M", conv))
                allpts += conv
            continue

        if cmd in ("S", "T"):               # 平滑贝塞尔：本流水线用不到，安全降级
            conv = []
            for p in raw_pairs:
                target = p if raw_cmd.isupper() else rel(p)
                conv.append(target)
            if conv:
                out.append(("L", conv))
                cur = conv[-1]
                allpts += conv
            continue

        if cmd == "A":                      # 圆弧：降级为直线
            conv = []
            for i in range(5, len(raw_pairs) + 1, 1):
                pass
            # 只取每段的终点
            for i in range(6, len(raw_pairs), 7):
                target = raw_pairs[i] if raw_cmd.isupper() else rel(raw_pairs[i])
                conv.append(target)
            if conv:
                out.append(("L", conv))
                cur = conv[-1]
                allpts += conv
            continue

    return out, allpts


def transform(ops, img_w, img_h, cell_type):
    """像素坐标(含基线) -> 字体单位坐标，返回 (ops', advance, lsb, bbox)。"""
    cell_px = img_w / (1 - 2 * INSET)             # 还原整格边长
    scale = UNITS_PER_EM / cell_px
    baseline_px = BASELINE_IN_IMG * img_h
    xs, ys = [], []
    for cmd, pts in ops:
        for x, y in pts:
            xs.append(x); ys.append(y)
    if not xs:
        return None
    min_x, max_x = min(xs), max(xs)
    # y: 像素向下 -> 字体向上，基线归零
    fy = lambda y: (baseline_px - y) * scale
    if cell_type == "han":
        bw = (max_x - min_x) * scale
        advance = UNITS_PER_EM
        lsb = (UNITS_PER_EM - bw) / 2.0            # 汉字：格内居中
    else:
        bw = (max_x - min_x) * scale
        advance = bw + 2 * SIDE_BEARING
        lsb = SIDE_BEARING
    dx = lsb - min_x * scale
    new = []
    for cmd, pts in ops:
        new.append((cmd, [(x * scale + dx, fy(y)) for x, y in pts]))
    ymin = min(fy(y) for y in ys); ymax = max(fy(y) for y in ys)
    return new, int(round(advance)), int(round(lsb)), (0, ymin, advance, ymax)


def extract_svg_frame(svg_text):
    """
    从 potrace 的 SVG 里取出 viewBox 与 <g transform="translate(..) scale(..)">，
    用于把 path 里的坐标精确换算回输入位图的像素坐标系。
    解析不到时返回 None（调用方按「d 坐标即像素坐标」处理）。
    """
    vb = re.search(r'viewBox="\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*"', svg_text)
    g = re.search(r'<g[^>]*transform="([^"]+)"', svg_text)
    if not vb or not g:
        return None
    vw, vh = float(vb.group(3)), float(vb.group(4))
    t = g.group(1)
    tr = re.search(r'translate\(\s*([-\d.eE]+)[ ,]+([-\d.eE]+)\s*\)', t)
    sc = re.search(r'scale\(\s*([-\d.eE]+)[ ,]+([-\d.eE]+)\s*\)', t)
    tx, ty = (float(tr.group(1)), float(tr.group(2))) if tr else (0.0, 0.0)
    sx, sy = (float(sc.group(1)), float(sc.group(2))) if sc else (1.0, 1.0)
    if vw <= 0 or vh <= 0 or sx == 0 or sy == 0:
        return None
    return dict(tx=tx, ty=ty, sx=sx, sy=sy, vw=vw, vh=vh)


def svg_d_to_pixel(ops, svg_text, img_w, img_h):
    """
    potrace 的 path 坐标是「pt 空间」，且 y 轴方向由 scale 的符号决定。
    这里统一换算成与输入位图一致的「像素、y 向下」坐标系，
    这样后面 transform() 只认一种坐标，不必关心 potrace 的版本差异。
    """
    fr = extract_svg_frame(svg_text)
    if fr is None:
        return ops                      # 假定 d 就是像素坐标
    kw = img_w / fr["vw"]
    kh = img_h / fr["vh"]
    new = []
    for cmd, pts in ops:
        new.append((cmd, [((x * fr["sx"] + fr["tx"]) * kw,
                           (y * fr["sy"] + fr["ty"]) * kh) for x, y in pts]))
    return new


def remove_phantom_paths(ops, src_ink):
    """
    删除 potrace 产生的「幽灵轮廓」——某些笔画/残留会让 potrace 额外生成一条
    与真实笔画不重叠、却又落在源图空白处的闭合填充轮廓（看起来就是字里凭空多出的
    小墨团 / 三角块）。真实笔画与计数器（如「口」的洞）一定与源图笔迹或洞重叠，
    而幽灵轮廓落在源图空白区。

    规则：把每个子路径（M..Z）单独栅格化，若它是「外层轮廓」（不被其他路径包围）
    且其填充像素落在源图空白处的比例 > 70%，即判定为幽灵，丢弃。
    被其它路径包围的（即计数器 / 洞）一律保留，避免误删「口」字心等。
    """
    import cv2 as _cv2
    Hh, Ww = src_ink.shape
    # 拆分子路径：每个以 M 开头、到下一个 M 或结尾为一个子路径
    subs = []          # 每个元素是一段 ops 列表 [(cmd,[pts]), ...]
    cur = []
    for cmd, pts in ops:
        if cmd == "M" and cur:
            subs.append(cur); cur = []
        cur.append((cmd, pts))
    if cur:
        subs.append(cur)

    def poly_of(sub):
        pts = []
        for c, p in sub:
            if c == "M":
                pts.extend(p)
            elif c == "L":
                pts.extend(p)
            elif c == "C":
                pts.extend(p)
        return pts  # [(x,y), ...]

    def raster_sub(sub):
        poly = poly_of(sub)
        if len(poly) < 3:
            return None
        arr = np.array([(min(max(x, 0), Ww - 1), min(max(y, 0), Hh - 1)) for x, y in poly], np.float32)
        m = np.zeros((Hh, Ww), np.uint8)
        _cv2.fillPoly(m, [arr.astype(np.int32)], 255)
        return m

    def enclosed(idx):
        poly = poly_of(subs[idx])
        if len(poly) < 3: return False
        cx = float(np.mean([p[0] for p in poly]))
        cy = float(np.mean([p[1] for p in poly]))
        for j, sub2 in enumerate(subs):
            if j == idx: continue
            poly2 = poly_of(sub2)
            if len(poly2) < 3: continue
            arr2 = np.array([(min(max(x, 0), Ww - 1), min(max(y, 0), Hh - 1)) for x, y in poly2], np.float32).astype(np.int32).reshape(-1, 1, 2)
            if _cv2.pointPolygonTest(arr2, (float(cx), float(cy)), False) >= 0:
                return True
        return False

    keep_idx = []
    for idx, sub in enumerate(subs):
        m = raster_sub(sub)
        if m is None:
            # 退化为点，丢弃
            continue
        area = int(m.sum())
        if area < 25:
            continue
        overlap = int(((m > 0) & (src_ink > 0)).sum())
        ratio = overlap / area
        if not enclosed(idx) and ratio < 0.30:
            continue
        keep_idx.append(idx)

    out = []
    for idx in keep_idx:
        out.extend(subs[idx])
    return out




def pen_from_ops(ops):
    """
    把绝对坐标的轮廓喂进 TTGlyphPen。

    这是 Bug B 的修复点。potrace 输出的是三次贝塞尔（cubic），而 TrueType 的
    glyf 表只认二次贝塞尔（quadratic）。新版 fontTools（>=4.5x）不再自动转换，
    会在 setupGlyf 时直接抛：
        ValueError: Glyph has cubic Bezier outlines, but glyphDataFormat=0
    这里用 Cu2QuPen 显式降阶：最大误差 0.5 字体单位（em=1000 的 0.05%），
    肉眼与 Hinting 层面都无差别，但能保住 potrace 的平滑笔画优势。
    """
    glyph_pen = TTGlyphPen(None)
    target = glyph_pen
    try:
        from fontTools.pens.cu2quPen import Cu2QuPen

        # Cu2QuPen 是「过滤笔」：外层包裹，自己不持有字形
        # 更小的降阶误差：细笔画在变小字号时（20~44px）少了锯齿化/粘连，
        # 0.5 -> 0.25 字体单位（em=1000）肉眼无差但更保真。
        target = Cu2QuPen(glyph_pen, max_err=0.25)
    except Exception:
        pass          # 极老版本 fontTools 没带 Cu2QuPen，退回原样（可能报错，但至少不掩盖原因）

    for cmd, pts in ops:
        if cmd == "M":
            target.moveTo(pts[0])
        elif cmd == "L":
            for p in pts:
                target.lineTo(p)
        elif cmd == "C":
            for i in range(0, len(pts) - 2, 3):
                target.curveTo(pts[i], pts[i + 1], pts[i + 2])
        elif cmd == "Z":
            target.closePath()
    return glyph_pen.glyph()


# ------------------------------------------------------------------- 主流程
def main():
    global BASELINE_IN_IMG
    ap = argparse.ArgumentParser()
    ap.add_argument("--glyphs", default=os.path.join(ROOT, "data", "glyphs"))
    ap.add_argument("--out", default=os.path.join(ROOT, "MyHand-Regular.ttf"))
    ap.add_argument("--family", default="MyHand")
    ap.add_argument("--baseline", type=float, default=BASELINE_IN_IMG,
                    help="基线在单字图里的高度占比，默认 %.4f；字整体偏高/偏低时微调" % BASELINE_IN_IMG)
    ap.add_argument("--threshold-ink", type=float, default=0.0)
    args = ap.parse_args()

    BASELINE_IN_IMG = args.baseline

    cs_path = os.path.join(ROOT, "charset", "charset.json")
    meta = {}
    if os.path.exists(cs_path):
        with open(cs_path, encoding="utf-8") as f:
            for r in json.load(f):
                meta[r["cp"]] = r
    mani = os.path.join(ROOT, "data", "glyph_manifest.csv")
    cell_of = {}
    if os.path.exists(mani):
        import csv
        with open(mani, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                cell_of[int(r["cp"])] = meta.get(int(r["cp"]), {}).get("cell", "han")
    # 没有 manifest 也行：按字符属性猜
    import unicodedata
    def guess_cell(cp):
        if cp in cell_of:
            return cell_of[cp]
        ch = chr(cp)
        if unicodedata.east_asian_width(ch) in "WF":
            return "han"
        return "lat"

    files = sorted(glob.glob(os.path.join(args.glyphs, "*.png")),
                   key=lambda p: int(os.path.basename(p)[:-4]))
    if not files:
        sys.exit("没有找到单字位图：%s" % args.glyphs)
    use_pt = have_potrace()
    print("矢量化引擎：%s" % ("potrace (贝塞尔)" if use_pt else
                              "OpenCV 轮廓（未安装 potrace，建议 apt install potrace）"))

    glyphs, cmap, hmtx = {}, {}, {}
    order = [".notdef"]
    pen0 = TTGlyphPen(None)
    glyphs[".notdef"] = pen0.glyph()
    hmtx[".notdef"] = (UNITS_PER_EM, 0)

    td = tempfile.mkdtemp()
    fails = []
    for i, p in enumerate(files):
        cp = int(os.path.basename(p)[:-4])
        cell_type = guess_cell(cp)
        try:
            img = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
            if img is None:
                raise RuntimeError("读图失败")
            ih, iw = img.shape
            if use_pt:
                svg = os.path.join(td, "%d.svg" % cp)
                txt = vectorize_potrace(p, svg)
                ops, _ = parse_svg_path(txt)
                ops = svg_d_to_pixel(ops, txt, iw, ih)   # 把 potrace 的坐标统一回像素系
            else:
                txt = vectorize_cv(p)
                ops, _ = parse_svg_path(txt)
            if not ops:
                raise RuntimeError("空轮廓")
            tr = transform(ops, iw, ih, cell_type=cell_type)
            if tr is None:
                raise RuntimeError("空点集")
            ops2, advance, lsb, bbox = tr
            name = "uni%04X" % cp
            glyphs[name] = pen_from_ops(ops2)
            cmap[cp] = name
            hmtx[name] = (advance, lsb)
            order.append(name)
        except Exception as e:
            fails.append((cp, str(e)[:80]))
        if (i + 1) % 500 == 0:
            print("  已处理 %d / %d" % (i + 1, len(files)))

    shutil.rmtree(td, ignore_errors=True)

    fb = FontBuilder(UNITS_PER_EM, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(hmtx)
    fb.setupHorizontalHeader(ascent=ASCENT, descent=DESCENT, lineGap=0)
    fb.setupNameTable({
        "familyName": args.family,
        "styleName": "Regular",
        "uniqueFontIdentifier": "%s-Regular" % args.family,
        "fullName": "%s Regular" % args.family,
        "psName": "%s-Regular" % args.family,
        "version": "Version 1.000",
    })
    fb.setupOS2(sTypoAscender=ASCENT, sTypoDescender=DESCENT, sTypoLineGap=0,
                usWinAscent=ASCENT, usWinDescent=-DESCENT,
                sxHeight=500, sCapHeight=700)
    fb.setupPost()
    fb.save(args.out)

    print("\n[ok] 字体：", args.out)
    print("    收录字符 %d 个，失败 %d 个" % (len(cmap), len(fails)))
    if fails:
        print("    失败样例：", fails[:8])
        with open(os.path.join(ROOT, "data", "build_failed.csv"), "w", encoding="utf-8-sig") as f:
            f.write("cp,hex,char,reason\n")
            for cp, why in fails:
                f.write("%d,U+%04X,%s,%s\n" % (cp, cp, chr(cp), why))

    # ---------- 预览页 ----------
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
    pv = os.path.join(ROOT, "preview.html")
    with open(pv, "w", encoding="utf-8") as f:
        f.write(html)
    print("[ok] 预览页：", pv, "（浏览器直接打开）")


if __name__ == "__main__":
    main()
