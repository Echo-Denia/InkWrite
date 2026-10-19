"""12_remap_punct.py —— 把「印刷体兜底」的半角标点/字母换成手写字形
================================================================
背景
----
模板上有些格子切图判为空（半角标点 `. : '` 和字母 `j` 等），于是 merge 阶段
由 simsun 兜底渲染成**印刷体**，在手写字里显得突兀。

半角(ASCII) 与全角/中文标点在手写里没必要严格区分，所以这里把半角码位
直接指向你已经写好的手写字形：

    U+002E  .   <- U+3002 。  (你写的句号)
    U+0027  '   <- U+2019 ’   (你写的右单引号)
    U+003A  :   <- U+FF1A ：  (AI 生成的全角冒号；全角冒号格也空，故用 AI)
    其余      ·  •  ′  等符号：能找到手写/AI 同形字就替换

字母 j 单独处理：AI 生成的 j（data/glyphs_ai/106.png）比印刷体协调，
用它替换 simsun 版本。

用法
----
python scripts/12_remap_punct.py            # 改 data/glyphs_merged 并重建字体
python scripts/12_remap_punct.py --dry-run  # 只看会改什么
"""
import argparse
import csv
import json
import os
import shutil
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# 半角码位 -> 优先使用的手写/AI 源码位（按顺序尝试）
REMAP = {
    0x2E: [0x3002, 0x2026],        # .  <- 。 / …
    0x27: [0x2019, 0x2018],        # '  <- ’ / ‘
    0x3A: [0xFF1A, 0x3B],          # :  <- ： / ;
    0x6A: [0x6A],                  # j  用 AI 版本（见下）
}
# 这些符号若有手写/AI 版本就替换印刷体
SOFT = [0xB2, 0xB3, 0xB7, 0xB9, 0xBC, 0xBD, 0xBE, 0x2022, 0x2032, 0x2103, 0x2109]

# 与半角同形的候选（按优先级），用于 SOFT 的模糊替换
SOFT_CAND = {
    0xB7: [0x30FB, 0x2022],        # ·
    0x2022: [0xB7, 0x30FB],        # •
    0x2032: [0x2019, 0x27],        # ′
}


def load_src():
    src = {}
    with open(os.path.join(ROOT, "data", "glyphs_merged", "merge_manifest.csv"),
              encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            src[int(r["cp"])] = r["src"]
    return src


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    merged = os.path.join(ROOT, "data", "glyphs_merged")
    ai = os.path.join(ROOT, "data", "glyphs_ai")
    src = load_src()

    sys.stdout.reconfigure(encoding="utf-8")
    changes = []

    def find_good(cands):
        """返回第一个「手写或 AI」来源且文件存在的码位。"""
        for cp in cands:
            if src.get(cp, "").startswith(("hand", "ai:")):
                p = os.path.join(merged, "%d.png" % cp)
                if os.path.exists(p):
                    return cp, p
        return None, None

    plan = []
    for cp, cands in REMAP.items():
        if cp == 0x6A:
            # j：优先 AI 版本
            p = os.path.join(ai, "106.png")
            if os.path.exists(p):
                plan.append((cp, "ai j (data/glyphs_ai/106.png)", p))
            continue
        good, p = find_good(cands)
        if good:
            plan.append((cp, "U+%04X (%s)" % (good, chr(good)), p))

    for cp in SOFT:
        if not src.get(cp, "").startswith("font:"):
            continue
        cands = SOFT_CAND.get(cp, [])
        good, p = find_good(cands)
        if good:
            plan.append((cp, "U+%04X (%s)" % (good, chr(good)), p))

    if not plan:
        print("没有可替换的项")
        return

    print("计划替换 %d 项：" % len(plan))
    for cp, why, p in plan:
        print("  U+%04X %-4s  <- %s" % (cp, chr(cp), why))

    if args.dry_run:
        print("\n(dry-run，未写入)")
        return

    for cp, why, p in plan:
        dst = os.path.join(merged, "%d.png" % cp)
        im = Image.open(p).convert("L")
        if im.size != (96, 96):
            im = im.resize((96, 96), Image.BILINEAR)
        im.save(dst)
        changes.append(cp)

    # 更新 manifest
    for r in [None]:
        pass
    mf = os.path.join(merged, "merge_manifest.csv")
    rows = []
    with open(mf, encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        fields = rd.fieldnames
        for r in rd:
            if int(r["cp"]) in changes:
                r["src"] = "remap:punct"
            rows.append(r)
    with open(mf, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    print("\n已替换 %d 个，manifest 已更新" % len(changes))
    print("下一步：重跑 11_merge_and_build.py --no-fallback 重建字体")
    print("   python scripts/11_merge_and_build.py --out MyHand-Full.ttf")


if __name__ == "__main__":
    main()
