#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
05_check.py  ——  【质检 / 补字】
================================
做三件事：
  1. 覆盖率：字符集里还有哪些字没写（输出 missing.txt，可直接喂给 02 --chars-file 补写）
  2. 一致性：找出「写得明显不一致」的字（太大 / 太小 / 偏上 / 偏下 / 左右歪）
     手写字体最难看的不是某个字丑，而是字与字之间大小、高低不齐——这一步专门抓这个
  3. 生成 review.html：把可疑的字排成一行行的图，肉眼扫一遍就能决定哪些要重写

用法：
  python scripts/05_check.py
  python scripts/05_check.py --tier 0        # 只按 Tier0 检查覆盖率
"""

import argparse
import csv
import glob
import json
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INSET = 0.07
BASELINE_IN_IMG = (0.75 - INSET) / (1 - 2 * INSET)


def ink_stats(path):
    a = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if a is None:
        return None
    h, w = a.shape
    m = a < 128
    if m.sum() < 10:
        return None
    ys, xs = np.where(m)
    return {
        "h": h, "w": w,
        "x0": xs.min(), "x1": xs.max(), "y0": ys.min(), "y1": ys.max(),
        "ink": float(m.mean()),
        "bw": (xs.max() - xs.min()) / w,
        "bh": (ys.max() - ys.min()) / h,
        "cx": ((xs.min() + xs.max()) / 2) / w - 0.5,          # 相对格心，理想 0
        "bottom": (ys.max() / h) - BASELINE_IN_IMG,           # 底边相对基线，理想 ≈0
        "top": (ys.min() / h) - BASELINE_IN_IMG,              # 顶边相对基线，负值
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glyphs", default=os.path.join(ROOT, "data", "glyphs"))
    ap.add_argument("--tier", type=int, default=0)
    ap.add_argument("--z", type=float, default=2.0, help="离群阈值（标准差倍数）")
    args = ap.parse_args()

    cs_path = os.path.join(ROOT, "charset", "charset.json")
    if not os.path.exists(cs_path):
        sys.exit("缺少 charset/charset.json")
    with open(cs_path, encoding="utf-8") as f:
        rows = json.load(f)
    target = [r for r in rows if r["tier"] <= args.tier]
    target_map = {r["cp"]: r for r in target}

    have = {}
    for p in glob.glob(os.path.join(args.glyphs, "*.png")):
        have[int(os.path.basename(p)[:-4])] = p

    missing = [r for r in target if r["cp"] not in have]
    print("目标字符 %d 个，已有 %d 个，缺失 %d 个" % (len(target), len(have), len(missing)))

    # ---------- 一致性 ----------
    stats = {}
    for cp, p in have.items():
        if cp not in target_map:
            continue
        s = ink_stats(p)
        if s:
            s["cat"] = target_map[cp]["cat"]
            s["char"] = target_map[cp]["char"]
            stats[cp] = s
    flags = []
    cp_of = {id(s): cp for cp, s in stats.items()}       # 反查表，避免 O(n^2)
    for cat in ("han", "han_ext"):
        grp = [s for s in stats.values() if s["cat"] == cat]
        if len(grp) < 30:
            continue
        for key in ("bh", "bw", "bottom", "cx"):
            v = np.array([s[key] for s in grp])
            mu, sd = v.mean(), v.std()
            if sd < 1e-6:
                continue
            for s in grp:
                z = (s[key] - mu) / sd
                if abs(z) > args.z:
                    flags.append({"cp": cp_of[id(s)],
                                  "char": s["char"], "metric": key,
                                  "value": round(s[key], 4),
                                  "mean": round(float(mu), 4), "z": round(float(z), 2)})

    mu_bh = np.mean([s["bh"] for s in stats.values() if s["cat"] in ("han", "han_ext")]) if stats else 0
    print("汉字平均字高占格 %.1f%%；离群 %d 项" % (mu_bh * 100, len(flags)))

    # ---------- 输出 ----------
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    mp = os.path.join(ROOT, "data", "missing.txt")
    with open(mp, "w", encoding="utf-8") as f:
        for r in missing:
            f.write(r["char"] + "\n")
    fp = os.path.join(ROOT, "data", "review_flags.csv")
    with open(fp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["cp", "char", "metric", "value", "mean", "z"])
        w.writeheader()
        w.writerows(flags)

    # 可疑字去重（按 z 绝对值排序）
    sus = {}
    for x in flags:
        sus.setdefault(x["cp"], []).append(abs(x["z"]))
    sus = sorted(sus.items(), key=lambda kv: -max(kv[1]))[:200]

    # review.html：把可疑字 + 随机抽样字排成网格，配参考格线
    tiles = []
    for cp, zs in sus:
        p = have[cp]
        tiles.append((cp, os.path.relpath(p, ROOT), "可疑 z=%.1f" % max(zs)))
    import random
    random.seed(0)
    sample = random.sample([c for c in have if c in target_map],
                           min(120, len([c for c in have if c in target_map])))
    for cp in sample:
        tiles.append((cp, os.path.relpath(have[cp], ROOT), "随机抽样"))

    import base64
    def b64(p):
        with open(os.path.join(ROOT, p), "rb") as f:
            return base64.b64encode(f.read()).decode()
    html = ["<!doctype html><meta charset='utf-8'><title>质检</title>",
            "<style>body{font-family:sans-serif;margin:24px;background:#fafafa}"
            ".g{display:flex;flex-wrap:wrap;gap:6px}"
            ".t{width:74px;text-align:center;background:#fff;border:1px solid #ddd;border-radius:6px;padding:4px}"
            ".t img{width:64px;height:64px;image-rendering:pixelated}"
            ".t b{font-size:22px;display:block}.t span{font-size:10px;color:#888}</style>",
            "<h2>可疑字（%d）</h2><div class='g'>" % len(sus)]
    for cp, p, tag in tiles[:200]:
        html.append("<div class='t'><b>%s</b><img src='data:image/png;base64,%s'><span>%s<br>U+%04X</span></div>"
                    % (chr(cp), b64(p), tag, cp))
    html.append("</div><h2>随机抽样</h2><div class='g'>")
    for cp, p, tag in tiles[200:]:
        html.append("<div class='t'><b>%s</b><img src='data:image/png;base64,%s'><span>U+%04X</span></div>"
                    % (chr(cp), b64(p), cp))
    html.append("</div>")
    rp = os.path.join(ROOT, "review.html")
    with open(rp, "w", encoding="utf-8") as f:
        f.write("\n".join(html))

    with open(os.path.join(ROOT, "data", "check_report.json"), "w", encoding="utf-8") as f:
        json.dump({"target": len(target), "have": len(have), "missing": len(missing),
                   "flags": len(flags)}, f, ensure_ascii=False, indent=1)

    print("\n待补字清单：", mp, "（%d 个）" % len(missing))
    print("离群明细：", fp)
    print("人工复核页：", rp)


if __name__ == "__main__":
    main()
