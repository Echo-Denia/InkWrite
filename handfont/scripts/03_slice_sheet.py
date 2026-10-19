#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
03_slice_sheet.py  ——  【在 Win11 本机跑】
==========================================
输入：一叠模板照片 + 02 生成的 layout.json
输出：每个字符一张干净的单字位图  data/glyphs/<Unicode十进制>.png
      + 质检报告（缺字 / 出血 / 疑似串行）

为什么必须配 layout.json：
  切图完全按「几何坐标」切，第几页第几行第几列 = 哪个码位，是 02 写死在 layout 里的。
  下游不做任何字符识别，因此不可能出现"整页错位一个格"这种灾难性错误。

照片命名：建议 page_001.jpg / page_002.jpg …（按 PDF 页码顺序）。
  脚本也会用四角 ArUco 的 ID 反解页号做交叉校验，两者不一致会报警。
"""

import argparse
import csv
import glob
import json
import os
import re
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

PX_PER_MM = 12.0           # 矫正后工作分辨率：A4 -> 2520 x 3564
# 每格向内缩多少毫米，用于躲开格线。默认 0 = 直接沿格线裁。
#   * 0（推荐）：格线本身是浅灰 #BBBBBB=187，而二值化阈值上限 MAX_BIN_THRESHOLD=143，
#     所以格线天然不会被当成笔画，不需要靠缩进来躲。沿格线裁能完整保留你写得出格的笔画，
#     也不受格子大小 / 打印缩放 / 透视残余误差的影响。
#   * >0：老行为，按固定比例缩。只在你换了一种「格线是深色/黑色」的模板时才需要。
INSET_MM = 0.0
# 沿格线裁切后，再把每格最外圈这么多个像素涂白。
# 这一圈只能是「格线 + 拍照造成的格线晕染/阴影」，绝不可能是你的笔画；
# 固定 3px 足够吃掉格线，比过去按比例缩 7%（≈13px）的误伤小得多。
GRID_STRIP_PX = 3
MIN_INK_RATIO = 0.004      # 低于此黑像素占比 => 判为空白格
OVERFLOW_PX = 2            # 贴边多少像素算出血

# 模板灰色标记的灰度（见 02_make_template.py）：
#   格线 #BBBBBB=187，基线 #CCCCCC=204，参考字 #C8C8C8=200，编号 #999999=153
# 这些都不是笔画，二值化时必须排除掉。
GRID_LINE_GRAY = 187
BASELINE_GRAY = 204
GHOST_GRAY = 200
CP_NUMBER_GRAY = 153
# 阈值上限取「最深的模板标记」再留 10 的余量：只要拍照后这些标记的 norm 值
# 没有比原模板深 10 以上，就绝不会被当成墨。超过这个上限一律按本值切。
MAX_BIN_THRESHOLD = CP_NUMBER_GRAY - 10     # = 143


# ---------------------------------------------------------------- 图像矫正
def detect_markers(img):
    """返回 [(marker_id, center, corners[4x2])]，没有 aruco 时返回 []。"""
    try:
        aruco = cv2.aruco
        d = aruco.getPredefinedDictionary(aruco.DICT_4X4_50)
        det = aruco.ArucoDetector(d, aruco.DetectorParameters())
        corners, ids, _ = det.detectMarkers(img)
    except Exception:
        return []
    if ids is None:
        return []
    out = []
    for c, i in zip(corners, ids.flatten()):
        pts = c.reshape(4, 2)
        out.append((int(i), pts.mean(axis=0), pts))
    return out


def order_corners(markers):
    """用几何位置把 4 个 marker 排成 [左上, 右上, 左下, 右下]。"""
    if len(markers) < 4:
        return None
    ms = sorted(markers, key=lambda m: -cv2.contourArea(m[2].astype(np.float32)))
    ms = ms[:4]
    pts = np.array([m[1] for m in ms])
    # 页面方向：用 marker 自身角点顺序 (tl,tr,br,bl) 的 "top 边" 平均方向修正
    vecs = []
    for _, _, p in ms:
        v = p[1] - p[0]      # tl -> tr
        vecs.append(v / (np.linalg.norm(v) + 1e-9))
    v = np.mean(vecs, axis=0)
    ang = np.degrees(np.arctan2(v[1], v[0]))
    ang = ((ang + 22.5) // 45) * 45          # 量化到 45° 的整数倍
    ang = ((ang + 180) % 360) - 180
    # 把中心点旋回页面坐标系
    a = np.radians(-ang)
    R = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    rot = pts @ R.T
    xs, ys = rot[:, 0], rot[:, 1]
    mx, my = xs.mean(), ys.mean()
    order = []
    for sx, sy in [(xs < mx, ys < my), (xs > mx, ys < my), (xs < mx, ys > my), (xs > mx, ys > my)]:
        idx = np.where(sx & sy)[0]
        order.append(ms[idx[0]] if len(idx) else None)
    if any(o is None for o in order):
        return None
    return [o[0] for o in order], [o[1] for o in order], [o[2] for o in order], ang


def warp_page(img, layout):
    """用四角 marker 把照片矫正成正射的 A4。返回 (warped, source_page_no, warn)。"""
    markers = detect_markers(img)
    warn = []
    src_page = None
    if len(markers) >= 4:
        oc = order_corners(markers)
        if oc:
            ids, centers, corners, ang = oc
            src_page = ids[0] + ids[1] * 50 + ids[2] * 2500
            if ids[3] != 0:
                warn.append("右下角锚点 ID=%d（应为 0），marker 顺序可能反了" % ids[3])
            # 取 4 个 marker 的对应角点做透视
            src = np.array([
                corners[0][0],   # 左上 marker 的 tl
                corners[1][1],   # 右上 marker 的 tr
                corners[2][3],   # 左下 marker 的 bl
                corners[3][2],   # 右下 marker 的 br
            ], dtype=np.float32)
            Wmm, Hmm = layout["page_mm"]
            W = int(round(Wmm * PX_PER_MM))
            H = int(round(Hmm * PX_PER_MM))
            pad = layout["marker_pad_mm"] * PX_PER_MM
            dst = np.array([[pad, pad], [W - pad, pad], [pad, H - pad], [W - pad, H - pad]],
                           dtype=np.float32)
            M = cv2.getPerspectiveTransform(src, dst)
            warped = cv2.warpPerspective(img, M, (W, H), flags=cv2.INTER_CUBIC)
            return warped, src_page, warn
        warn.append("4 个 marker 的几何排序失败")
    # ---- 回退：四角纯黑方块 ----
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    cnts, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    h, w = gray.shape
    for c in cnts:
        x, y, ww, hh = cv2.boundingRect(c)
        if not (0.005 * w < ww < 0.2 * w):
            continue
        ar = ww / float(hh)
        if 0.7 < ar < 1.4 and cv2.contourArea(c) / (ww * hh) > 0.6:
            boxes.append((x + ww / 2, y + hh / 2))
    if len(boxes) == 4:
        pts = np.array(boxes)
        s = pts.sum(axis=1)
        diff = pts[:, 0] - pts[:, 1]
        src = np.array([pts[np.argmin(s)], pts[np.argmin(diff)],
                        pts[np.argmax(diff)], pts[np.argmax(s)]], dtype=np.float32)
        Wmm, Hmm = layout["page_mm"]
        W = int(round(Wmm * PX_PER_MM)); H = int(round(Hmm * PX_PER_MM))
        pad = layout["marker_pad_mm"] * PX_PER_MM
        dst = np.array([[pad, pad], [W - pad, pad], [pad, H - pad], [W - pad, H - pad]],
                       dtype=np.float32)
        warped = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst), (W, H),
                                     flags=cv2.INTER_CUBIC)
        warn.append("未用 ArUco，改用四角黑方块（页号以文件名为准）")
        return warped, src_page, warn
    return None, None, ["四角标记全部检测失败，请检查照片"]


# ---------------------------------------------------------------- 单格处理
def clean_cell(cell_bgr, border_px=GRID_STRIP_PX):
    """格子图 -> (白底黑字的二值图 uint8, 诊断 dict)

    border_px：先把格子图最外圈这一圈直接涂白。切图是沿格线裁的（inset=0），
    最外这一圈必然是「格线本身 + 拍照时格线附近的阴影/晕染」，绝不可能是你的笔画。
    固定圈 2~3px 就够了——远小于过去按比例缩 7%（≈13px）的误伤。
    """
    gray = cv2.cvtColor(cell_bgr, cv2.COLOR_BGR2GRAY) if cell_bgr.ndim == 3 else cell_bgr
    # 沿格线裁进来的最外圈直接涂白：格线 + 格线晕染，不是笔迹
    if border_px and border_px > 0:
        gray = gray.copy()
        b = int(border_px)
        gray[:b, :] = 255
        gray[-b:, :] = 255
        gray[:, :b] = 255
        gray[:, -b:] = 255
    # 光照不均补偿
    se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31))
    bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, se)
    norm = cv2.divide(gray, bg, scale=255)
    # 二值化：Otsu 但上限压到 MAX_BIN_THRESHOLD，确保模板的浅灰参考字/编号/格线
    # 不会被当成笔画（见文件头灰度常量表）
    t, bw = cv2.threshold(norm, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    t = min(t, MAX_BIN_THRESHOLD)
    _, bw = cv2.threshold(norm, t, 255, cv2.THRESH_BINARY_INV)
    # 模板残留清理（只删「绝不可能与真实笔画重叠」的元素，绝不动笔迹）：
    #   1) 噪点（面积 <24）
    #   2) 贴边的发丝网格线（厚<=2px 且贴裁剪边）——真实笔画更粗且不贴边
    #   3) 顶 / 底 边带里的横发丝线（cy<0.13 或 cy>0.83，厚<=3px，宽>45%）：
    #      切图时格子内缩了 7%，所以底网格线落在距边 ~7px、cy≈0.85 处，不贴边但仍属模板
    #   4) 基线灰线（cy 在 0.72~0.86、宽>55%、厚<=3px）
    #   5) 四角码号 / 参考字标签（右下 xr>0.82&yb>0.55；左下 xl<0.18&yb>0.55）
    #   真实笔画（含下伸笔画 g y p q ∫ 等）都连在主块上或面积大，绝不会被删；
    #   若仍有极个别残留，用 data/cleaner.html 手动抹。
    n, lab, stats, cent = cv2.connectedComponentsWithStats(bw, 8)
    Hh, Ww = bw.shape
    for i in range(1, n):
        a = stats[i, cv2.CC_STAT_AREA]
        if a < 24:
            bw[lab == i] = 0
            continue
        xl = stats[i, cv2.CC_STAT_LEFT]
        xr = xl + stats[i, cv2.CC_STAT_WIDTH]
        yt = stats[i, cv2.CC_STAT_TOP]
        yb = yt + stats[i, cv2.CC_STAT_HEIGHT]
        hh = stats[i, cv2.CC_STAT_HEIGHT]
        ww = stats[i, cv2.CC_STAT_WIDTH]
        cyc = (yt + hh / 2) / Hh
        cxr = (xl + ww / 2) / Ww
        # 7) 底部贴边 + 偏左右侧或横跨较宽（任意面积）：底网格线 / 左下角参考字 / 右下角码号残留。
        #    真实下伸笔画（g y p q ∫）是居中窄竖条，不贴左右侧、也不横跨；据此区分
        if yt > 0.78 * Hh and yb >= Hh - 2 and (xl < 0.35 * Ww or xr > 0.65 * Ww or ww > 0.20 * Ww):
            bw[lab == i] = 0
            continue
        if a >= 400:
            continue
        touches_border = (xl <= 1 or yt <= 1 or xr >= Ww - 1 or yb >= Hh - 1)
        is_hairline = (hh <= 2) or (ww <= 2)
        # 2) 贴边发丝线
        if touches_border and is_hairline:
            bw[lab == i] = 0
            continue
        # 3) 顶/底边带横发丝线
        if hh <= 3 and ww > 0.45 * Ww and (cyc < 0.13 or cyc > 0.83):
            bw[lab == i] = 0
            continue
        # 4) 基线灰线
        if hh <= 3 and ww > 0.55 * Ww and 0.72 * Hh < (yt + hh / 2) < 0.88 * Hh:
            bw[lab == i] = 0
            continue
        # 5) 四角标签
        if (xr > 0.82 * Ww and yb > 0.55 * Hh) or (xl < 0.18 * Ww and yb > 0.55 * Hh):
            bw[lab == i] = 0
            continue
        # 6) 极端外缘边带里的孤立小残点（面积<130）：网格线断裂段、角标残片、
        #    参考字溢出的碎屑都又小又贴在四边极端处；真实笔画/偏旁部首（亻口手…）
        #    面积远大于此且更靠内，绝不会落进这圈 6~8% 的死角
        if a < 130 and (cyc < 0.07 or cyc > 0.85 or cxr < 0.07 or cxr > 0.93):
            bw[lab == i] = 0
            continue
    ink = float((bw > 0).sum()) / bw.size
    diag = {"ink_ratio": round(ink, 5), "threshold": float(t)}
    # 出血检测
    edge = np.zeros_like(bw)
    edge[:OVERFLOW_PX, :] = 1; edge[-OVERFLOW_PX:, :] = 1
    edge[:, :OVERFLOW_PX] = 1; edge[:, -OVERFLOW_PX:] = 1
    diag["overflow"] = bool((bw > 0)[edge > 0].sum() > 8)
    out = 255 - bw                      # 白底黑字
    return out, diag


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout", required=True, help="*.layout.json")
    ap.add_argument("--photos", required=True, help="照片目录或 glob")
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "glyphs"))
    ap.add_argument("--report", default=os.path.join(ROOT, "data", "slice_report.json"))
    ap.add_argument("--inset-mm", type=float, default=INSET_MM,
                    help="每格向内缩多少毫米（躲开格线）。默认 %.1f = 直接沿格线裁："
                         "格线是浅灰 #BBBBBB，低于二值化阈值上限 %d，天然不会被当成笔画，"
                         "所以无需缩进，还能完整保留出格的笔画。" % (INSET_MM, MAX_BIN_THRESHOLD))
    ap.add_argument("--force", action="store_true",
                    help="重新生成全部单字图（会覆盖 cleaner.html 手动修改过的 PNG）。"
                         "默认跳过已存在的 <cp>.png，保留手动清理结果。")
    args = ap.parse_args()

    with open(args.layout, encoding="utf-8") as f:
        layout = json.load(f)
    page_by_no = {p["page"]: p for p in layout["pages"]}

    if os.path.isdir(args.photos):
        files = sorted(glob.glob(os.path.join(args.photos, "*.jpg")) +
                       glob.glob(os.path.join(args.photos, "*.jpeg")) +
                       glob.glob(os.path.join(args.photos, "*.png")) +
                       glob.glob(os.path.join(args.photos, "*.webp")))
    else:
        files = sorted(glob.glob(args.photos))
    files = [f for f in files if os.path.isfile(f)]
    print("照片数：", len(files))

    os.makedirs(args.out, exist_ok=True)
    report = {"pages": [], "missing": [], "overflow": [], "warnings": []}
    manifest_rows = []

    for fi, path in enumerate(files):
        img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            img = cv2.imread(path)
        if img is None:
            report["warnings"].append("读图失败: %s" % path)
            continue
        # 文件名里的页码
        m = re.findall(r"(\d+)", os.path.basename(path))
        fn_page = int(m[-1]) - 1 if m else fi      # page_001 -> 0
        warped, mk_page, warn = warp_page(img, layout)
        if warped is None:
            report["warnings"].append("%s: %s" % (os.path.basename(path), "; ".join(warn)))
            continue
        if mk_page is not None and mk_page != fn_page:
            report["warnings"].append(
                "%s: 文件名页码=%d 但 ArUco 解出=%d（以文件名优先，请核对顺序）"
                % (os.path.basename(path), fn_page + 1, mk_page + 1))
        page = page_by_no.get(fn_page)
        if page is None:
            report["warnings"].append("%s: layout 中没有第 %d 页" % (os.path.basename(path), fn_page + 1))
            continue

        cols, rows = page["cols"], page["rows"]
        cell_px = page["cell_mm"] * PX_PER_MM
        gx, gy = page["origin_mm"]
        inset = args.inset_mm * PX_PER_MM        # 默认 0 = 沿格线裁，不躲

        pinfo = {"file": os.path.basename(path), "page": fn_page, "cell": page["cell"],
                 "cells": 0, "ok": 0, "empty": 0, "overflow": 0, "warn": warn}
        for idx, cp in enumerate(page["order"]):
            r, c = divmod(idx, cols)
            x0 = (gx * PX_PER_MM) + c * cell_px + inset
            y0 = (gy * PX_PER_MM) + r * cell_px + inset
            side = cell_px - 2 * inset
            x1, y1 = x0 + side, y0 + side
            sub = warped[int(round(y0)):int(round(y1)), int(round(x0)):int(round(x1))]
            if sub.size == 0:
                continue
            pinfo["cells"] += 1
            clean, diag = clean_cell(sub)
            if diag["ink_ratio"] < MIN_INK_RATIO:
                pinfo["empty"] += 1
                report["missing"].append({"cp": cp, "page": fn_page, "row": r, "col": c})
                continue
            if diag["overflow"]:
                pinfo["overflow"] += 1
                report["overflow"].append({"cp": cp, "page": fn_page, "row": r, "col": c})
            pinfo["ok"] += 1
            fn = os.path.join(args.out, "%d.png" % cp)
            # 默认保留手动清理结果：已存在的 PNG 不覆盖（除非 --force）。
            # cleaner.html 直接改的是 data/glyphs/*.png，重跑 03 不会再切图把它冲掉。
            if os.path.exists(fn) and not args.force:
                pinfo["kept"] = pinfo.get("kept", 0) + 1
                manifest_rows.append({
                    "cp": cp, "hex": "U+%04X" % cp, "char": chr(cp),
                    "file": os.path.relpath(fn, ROOT), "page": fn_page, "row": r, "col": c,
                    "ink_ratio": diag["ink_ratio"], "overflow": int(diag["overflow"]),
                })
                continue
            cv2.imwrite(fn, clean)
            manifest_rows.append({
                "cp": cp, "hex": "U+%04X" % cp, "char": chr(cp),
                "file": os.path.relpath(fn, ROOT), "page": fn_page, "row": r, "col": c,
                "ink_ratio": diag["ink_ratio"], "overflow": int(diag["overflow"]),
            })
        report["pages"].append(pinfo)
        print("[%02d] %s  page=%d %s  ok=%d empty=%d overflow=%d kept=%d"
              % (fi + 1, os.path.basename(path), fn_page + 1, page["cell"],
                 pinfo["ok"], pinfo["empty"], pinfo["overflow"], pinfo.get("kept", 0)))

    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    mp = os.path.join(ROOT, "data", "glyph_manifest.csv")
    with open(mp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["cp", "hex", "char", "file", "page", "row", "col",
                                          "ink_ratio", "overflow"])
        w.writeheader()
        w.writerows(manifest_rows)
    print("\n单字位图：%s（%d 个）" % (args.out, len(manifest_rows)))
    print("质检报告：", args.report)
    print("缺字 %d 个，出血 %d 个，告警 %d 条"
          % (len(report["missing"]), len(report["overflow"]), len(report["warnings"])))


if __name__ == "__main__":
    main()
