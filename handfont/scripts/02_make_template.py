#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
02_make_template.py
===================
生成「可拍照的手写模板 PDF」+「layout.json（每页每格对应哪个字符的精确地图）」。

关键点：
  * 四角放 ArUco 标记（4X4_50），标记 ID 编码页号 → 拍照后可自动识别页号 + 几何矫正
  * 同时输出 layout.json：把「第几页第几行第几列 = 哪个 Unicode 码位」写死
    → 下游切图不需要"猜"字是什么，只需按坐标切，杜绝整体错位
  * 每页只放同一种格型（han 大方格 / lat 拉丁窄格 / sym 符号格），避免串行

用法：
  python scripts/02_make_template.py --tier 0 --out data/template_tier0
  python scripts/02_make_template.py --tier 1 --out data/template_tier1 --ghost
"""

import argparse
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

# ---- A4 与版面几何（单位 mm） ----
PAGE_W, PAGE_H = 210.0, 297.0
MARKER_MM = 16.0        # ArUco 标记边长
MARKER_PAD = 4.0        # 标记距页边
GRID_X0 = MARKER_PAD + MARKER_MM + 3.0     # 23.0
GRID_Y0 = GRID_X0
GRID_W = PAGE_W - 2 * GRID_X0              # 164.0
GRID_H = PAGE_H - 2 * GRID_Y0              # 251.0

CELL_SPEC = {           # 格型 -> (格边长 mm)
    "han": 16.0,        # 汉字：150 格/页
    "lat": 13.0,        # 拉丁/希腊：12 x 19
    "sym": 13.0,        # 数学符号 / 标点
}

FONT_DIRS = [
    "C:/Windows/Fonts",
    "/usr/share/fonts",
    "/System/Library/Fonts",
    "/Library/Fonts",
]
# 注意：reportlab 的 TTFont 只支持 TrueType(glyf) 轮廓，不支持 CFF/OTF，
# 所以优先挑 .ttf，再试 .ttc（逐个 subfontIndex 尝试）。
FONT_PRIORITY = [
    "simhei.ttf", "msyhbd.ttf", "Dengb.ttf", "simkai.ttf", "simfang.ttf", "simsun.ttf",
    "**/NotoSansSC-Regular.ttf", "**/NotoSansSC-Medium.ttf", "**/NotoSansSC-Light.ttf",
    "**/NotoSansSC-Bold.ttf", "**/MiSans-*.ttf", "**/DroidSansFallbackFull.ttf",
    "**/wqy-zenhei.ttc", "**/wqy-microhei.ttc", "**/WenQuanYi*.ttc",
    "msyh.ttc", "msyhbd.ttc", "simsun.ttc",
]


def find_cjk_font():
    """在常见位置找一份 reportlab 能用、且能显示汉字的字体。"""
    hits = []
    for d in FONT_DIRS:
        if not os.path.isdir(d):
            continue
        for pat in FONT_PRIORITY:
            hits += glob.glob(os.path.join(d, pat), recursive=True)
    seen = set()
    for h in hits:
        if h in seen:
            continue
        seen.add(h)
        # 粗筛：文件里是否含 CJK 常用字（用 fontTools 查 cmap，快且准）
        try:
            from fontTools.ttLib import TTFont as FT
            ft = FT(h, fontNumber=0 if h.lower().endswith(".ttc") else 0)
            if "glyf" not in ft:            # CFF 轮廓，reportlab 不支持
                continue
            cmap = ft.getBestCmap()
            if all(ord(c) in cmap for c in "的手写字体"):
                ft.close()
                return h
            ft.close()
        except Exception:
            continue
    return hits[0] if hits else None


def grid_geometry(cell_mm):
    cols = int(GRID_W // cell_mm)
    rows = int(GRID_H // cell_mm)
    used_w = cols * cell_mm
    used_h = rows * cell_mm
    x0 = GRID_X0 + (GRID_W - used_w) / 2.0
    y0 = GRID_Y0 + (GRID_H - used_h) / 2.0
    return cols, rows, x0, y0


def make_marker_image(aruco, dict_id, marker_id, px=180):
    """返回 PIL.Image（灰度），画一个 ArUco 标记；没有 opencv-contrib 则回退为纯黑块。"""
    from PIL import Image
    if aruco is not None:
        img = aruco.generateImageMarker(dict_id, marker_id, px)
        img = cv2_border(img)
        return Image.fromarray(img)
    arr = Image.new("L", (px, px), 255)
    return arr


def cv2_border(img):
    """ArUco 标记四周留 4px 白边，提升检测率。"""
    import numpy as np
    p = 4
    out = np.full((img.shape[0] + 2 * p, img.shape[1] + 2 * p), 255, dtype=np.uint8)
    out[p:p + img.shape[0], p:p + img.shape[1]] = img
    return out


def try_import_aruco():
    try:
        import cv2
        if not hasattr(cv2, "aruco"):
            return None, None, None
        # OpenCV 4.7+ 与旧版 API 兼容
        d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        return cv2, d, "DICT_4X4_50"
    except Exception:
        return None, None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", type=int, default=0, help="生成 Tier0(启动集)/1/2")
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "template_tier0"))
    ap.add_argument("--ghost", action="store_true", help="格内左下角印极小的淡灰参考字（不铺满格子，避免描红）")
    ap.add_argument("--ghost-size", type=float, default=3.2,
                    help="参考字字号(mm)，默认 3.2；与右下角码位数字相当")
    ap.add_argument("--mizi", action="store_true", help="汉字格加米字格辅助线")
    ap.add_argument("--no-aruco", action="store_true", help="不用 ArUco，改用四角纯黑方块")
    ap.add_argument("--han-cell", type=float, default=CELL_SPEC["han"])
    ap.add_argument("--sym-cell", type=float, default=CELL_SPEC["sym"])
    ap.add_argument("--chars-file", default=None,
                    help="只生成这个列表里的字符（补字用）：文件里写字符即可，也可写 U+4E00 或十进制码位")
    args = ap.parse_args()

    charset_path = os.path.join(ROOT, "charset", "charset.json")
    if not os.path.exists(charset_path):
        sys.exit("缺少 charset/charset.json，请先运行 01_build_charset.py")
    with open(charset_path, encoding="utf-8") as f:
        rows = json.load(f)

    if args.chars_file:
        # 补字模式：只要文件里列出的字符
        wanted = set()
        with open(args.chars_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                for tok in re.split(r"[\s,]+", line):
                    tok = tok.strip()
                    if not tok:
                        continue
                    if tok.upper().startswith("U+"):
                        wanted.add(int(tok[2:], 16))
                    elif tok.isdigit():
                        wanted.add(int(tok))
                    else:
                        wanted.update(ord(ch) for ch in tok)
        idx = {r["cp"]: r for r in rows}
        sel = [idx[c] for c in sorted(wanted) if c in idx]
        miss = [c for c in sorted(wanted) if c not in idx]
        if miss:
            print("[warn] 有 %d 个字符不在字符集里（已跳过）：%s"
                  % (len(miss), "".join(chr(c) for c in miss[:20])))
    else:
        # 分层累积：Tier N 包含 Tier 0..N
        sel = [r for r in rows if r["tier"] <= args.tier]
    # ===================================================================
    # 书写顺序：Tier0 优先，保证「先写的一批」在任何 Tier 里都位置一致
    # ===================================================================
    # 需求：用户只想印一次全集（Tier1），先写完「够用的一批」就去干活，
    #       以后接着往下写。因此 Tier0 的字符必须排在 Tier1 的最前面，
    #       且在每一页上的行列位置与单独生成 Tier0 时完全相同。
    #
    # 实现要点：排序键的第一位是 tier（0 在前），而不是类别。
    # 同 tier 内部再按「类别 -> 原始顺序」排，这样：
    #   * Tier0 的 han 部分 -> Tier0 的 cjk_punct -> Tier0 的 lat -> Tier0 的 sym
    #   * 接着才是 Tier1 新增的部分
    # 由于每格型的分页是独立的（han 页满一页才翻页），同 tier 同类别的
    # 内容与单独跑 --tier 0 时逐格一致。
    order_cat = {"han": 0, "cjk_punct": 1, "ascii": 2, "greek": 3, "math": 4, "math_ext": 5, "supsub": 6}
    order_cell = {"han": 0, "lat": 1, "sym": 2}

    # charset.json 是按「汉字字频降序」写入的，所以它在数组里的天然次序就是字频序。
    # 用这个下标做稳定键，避免把 500 个高频字按 Unicode 码位重排（那样就乱了）。
    seq_of = {}
    for i, r in enumerate(rows):
        seq_of[r["cp"]] = i

    def sort_key(r):
        return (r["tier"], order_cell.get(r["cell"], 9), order_cat.get(r["cat"], 9),
                seq_of.get(r["cp"], 999999))

    sel = sorted(sel, key=sort_key)

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    font_path = find_cjk_font()
    font_name = "Helvetica"
    if font_path:
        try:
            pdfmetrics.registerFont(TTFont("CJKFont", font_path))
            font_name = "CJKFont"
            print("[ok] 中文字体:", font_path)
        except Exception as e:
            print("[warn] 字体注册失败，页眉将不显示汉字:", e)

    cv2mod, aruco_dict, dict_name = (None, None, None)
    if not args.no_aruco:
        cv2mod, aruco_dict, dict_name = try_import_aruco()
        if aruco_dict is None:
            print("[warn] 未检测到 opencv-contrib 的 aruco 模块，四角改用纯黑方块。"
                  "（pip install opencv-contrib-python 可启用）")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    pdf_path = args.out + ".pdf"
    c = canvas.Canvas(pdf_path, pagesize=A4)

    # 按格型分组
    groups = {"han": [], "lat": [], "sym": []}
    for r in sel:
        groups.setdefault(r["cell"], []).append(r)

    layout = {
        "batch_id": os.path.basename(args.out),
        "tier": args.tier,
        "page_mm": [PAGE_W, PAGE_H],
        "marker": dict_name or "black_square",
        "marker_mm": MARKER_MM,
        "marker_pad_mm": MARKER_PAD,
        "grid_rect_mm": [GRID_X0, GRID_Y0, GRID_W, GRID_H],
        "pages": [],
    }

    page_no = 0
    for cell_type in ("han", "lat", "sym"):
        items = groups.get(cell_type, [])
        if not items:
            continue
        cell_mm = args.han_cell if cell_type == "han" else args.sym_cell
        cols, rows_n, gx, gy = grid_geometry(cell_mm)
        per_page = cols * rows_n
        for start in range(0, len(items), per_page):
            chunk = items[start:start + per_page]
            order = [r["cp"] for r in chunk]
            mid = chunk[0]["char"] + " … " + chunk[-1]["char"]
            _draw_page(c, font_name, chunk, cell_type, cell_mm, cols, rows_n, gx, gy,
                       page_no, len(items), mid, dict_name, aruco_dict, args)
            layout["pages"].append({
                "page": page_no,
                "cell": cell_type,
                "cols": cols,
                "rows": rows_n,
                "cell_mm": cell_mm,
                "origin_mm": [gx, gy],
                "order": order,
                "label": mid,
            })
            page_no += 1
            c.showPage()

    c.save()
    layout["page_count"] = page_no
    with open(args.out + ".layout.json", "w", encoding="utf-8") as f:
        json.dump(layout, f, ensure_ascii=False, indent=1)

    print("[ok] PDF :", pdf_path)
    print("[ok] 页数:", page_no)
    for cell_type in ("han", "lat", "sym"):
        n = len(groups.get(cell_type, []))
        if n:
            print("     %-4s 字符 %d 个" % (cell_type, n))
    print("[ok] layout:", args.out + ".layout.json")


def _mm2pt(v):
    return v * 72.0 / 25.4


def _draw_page(c, font_name, chunk, cell_type, cell_mm, cols, rows_n, gx, gy,
               page_no, total, label, dict_name, aruco_dict, args):
    """y 轴：这里统一用「从页面顶部往下」的毫米坐标，绘制时再换算成 PDF 坐标。"""
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics

    W, H = PAGE_W, PAGE_H

    def P(x_mm, y_mm):
        """(左起 x, 上起 y) -> PDF 坐标（左下原点）"""
        return _mm2pt(x_mm), _mm2pt(H - y_mm)

    # ---------- 四角标记 ----------
    marker_ids = [page_no % 50, (page_no // 50) % 50, (page_no // 2500) % 50, 0]
    corners = [
        (MARKER_PAD, MARKER_PAD),                                   # 左上
        (W - MARKER_PAD - MARKER_MM, MARKER_PAD),                   # 右上
        (MARKER_PAD, H - MARKER_PAD - MARKER_MM),                   # 左下
        (W - MARKER_PAD - MARKER_MM, H - MARKER_PAD - MARKER_MM),   # 右下
    ]
    for (mx, my), mid in zip(corners, marker_ids):
        x, y = P(mx, my)                       # 左上角
        if aruco_dict is not None:
            import io
            from PIL import Image
            import numpy as np
            try:
                import cv2
                img = cv2.aruco.generateImageMarker(aruco_dict, mid, 160)
                p = 8
                out = np.full((img.shape[0] + 2 * p, img.shape[1] + 2 * p), 255, dtype=np.uint8)
                out[p:p + img.shape[0], p:p + img.shape[1]] = img
                buf = io.BytesIO()
                Image.fromarray(out).save(buf, format="PNG")
                buf.seek(0)
                from reportlab.lib.utils import ImageReader
                c.drawImage(ImageReader(buf), x, y - _mm2pt(MARKER_MM),
                            _mm2pt(MARKER_MM), _mm2pt(MARKER_MM))
                continue
            except Exception:
                pass
        c.setFillColor(colors.black)
        c.rect(x, y - _mm2pt(MARKER_MM), _mm2pt(MARKER_MM), _mm2pt(MARKER_MM), stroke=0, fill=1)

    # ---------- 页眉 / 页脚 ----------
    c.setFont(font_name, 8)
    c.setFillColor(colors.HexColor("#444444"))
    hdr = "batch %s   page %03d   [%s]   %s" % ("TODO", page_no + 1, cell_type, label)
    try:
        c.drawString(_mm2pt(GRID_X0), _mm2pt(H - 12), hdr)
    except Exception:
        c.drawString(_mm2pt(GRID_X0), _mm2pt(H - 12), "page %03d [%s]" % (page_no + 1, cell_type))
    c.setFont(font_name, 6.5)
    c.drawString(_mm2pt(GRID_X0), _mm2pt(8),
                 "黑笔写在格内，不出格不连笔；四角标记勿遮挡。cell=%.0fmm  %dx%d" % (cell_mm, cols, rows_n))

    # ---------- 网格 ----------
    c.setStrokeColor(colors.HexColor("#BBBBBB"))
    c.setLineWidth(0.5)
    for ri in range(rows_n):
        for ci in range(cols):
            idx = ri * cols + ci
            cx = gx + ci * cell_mm
            cy = gy + ri * cell_mm
            x, y = P(cx, cy)
            s = _mm2pt(cell_mm)
            c.rect(x, y - s, s, s, stroke=1, fill=0)

            if idx >= len(chunk):
                continue
            r = chunk[idx]

            # 辅助线：基线（实线，格底往上 25%）
            c.setStrokeColor(colors.HexColor("#CCCCCC"))
            c.setLineWidth(0.4)
            base_up = cell_mm * 0.25
            c.line(x + _mm2pt(1), y - s + _mm2pt(base_up),
                   x + s - _mm2pt(1), y - s + _mm2pt(base_up))
            if args.mizi and cell_type == "han":
                c.setStrokeColor(colors.HexColor("#DDDDDD"))
                c.setDash(2, 2)
                c.line(x, y - s / 2, x + s, y - s / 2)
                c.line(x + s / 2, y, x + s / 2, y - s)
                c.line(x, y, x + s, y - s)
                c.line(x, y - s, x + s, y)
                c.setDash()

            # 淡灰参考字（可选）：缩到左下角、和右上代码号一样小。
            # 放在角落而不是格子正中，是为了避免照着描——一旦在正中间放大 print，
            # 手会不自觉跟印刷体走，字就失去自己的笔迹特征，白做。
            if args.ghost:
                c.setFillColor(colors.HexColor("#C8C8C8"))
                try:
                    c.setFont(font_name, _mm2pt(args.ghost_size))
                    c.drawString(x + _mm2pt(0.7), y - s + _mm2pt(args.ghost_size * 0.62),
                                 r["char"])
                except Exception:
                    pass

            # 格号（右下角，极小，便于出问题时人工定位）
            c.setFillColor(colors.HexColor("#999999"))
            c.setFont("Helvetica", 4.2)
            c.drawRightString(x + s - _mm2pt(0.8), y - s + _mm2pt(1.0), str(r["cp"]))

    # ---------- 外框：网格区参考框（浅） ----------
    c.setStrokeColor(colors.HexColor("#999999"))
    c.setLineWidth(0.6)
    gxp, gyp = P(GRID_X0, GRID_Y0)
    c.rect(gxp, gyp - _mm2pt(GRID_H), _mm2pt(GRID_W), _mm2pt(GRID_H), stroke=1, fill=0)


if __name__ == "__main__":
    main()
