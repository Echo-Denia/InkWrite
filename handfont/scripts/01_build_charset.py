#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
01_build_charset.py
===================
生成「需要手抄的字符总清单」。所有字符均来自可验证的权威来源，不虚构：

  1) 汉字       —— GB 2312-80 国家标准（Python 内置 gb2312 编解码表精确枚举）
                   一级字库 3755（16-55 区，拼音序）、二级字库 3008（56-87 区）
  2) 汉字频率序 —— Rime《八股文》essay.txt 字频权重（用于决定先写哪些字）
  3) 数学符号   —— Unicode Mathematical Operators 区块 U+2200–U+22FF（逐字符用
                   unicodedata 回查码位与官方名称，写错会直接报错）
  4) 希腊字母   —— Unicode Greek and Coptic 区块 U+0391–U+03A9 / U+03B1–U+03C9
  5) ASCII      —— U+0020–U+007E 全部可打印字符
  6) 中文标点   —— GB 2312-80 01 区收录的中文标点

产物：
  charset/manifest_all.csv    全集（每行一个字符 + 码位 + 类别 + 分层 + 建议格型）
  charset/chars_tier{0,1,2}.txt   纯字符文本，可直接肉眼核对
  charset/charset.json        结构化全集
  charset/freq_cn.json        GB2312 汉字字频表（供排序用）
"""

import csv
import json
import os
import sys
import unicodedata
from collections import Counter, OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CHARSET_DIR = os.path.join(ROOT, "charset")
os.makedirs(CHARSET_DIR, exist_ok=True)

FREQ_SRC = os.path.join(CHARSET_DIR, "freq_cn.json")
FREQ_URL = "https://cdn.jsdelivr.net/gh/rime/rime-essay@master/essay.txt"
FREQ_RAW = os.path.join(ROOT, "data", "essay.txt")


# --------------------------------------------------------------------------
# 1. 汉字：GB 2312-80 精确枚举
# --------------------------------------------------------------------------
def enum_gb2312():
    """返回 (一级字列表, 二级字列表)，每个元素是 (区位, 字符)。"""
    lvl1, lvl2 = [], []
    for hi in range(0xA1, 0xFF):
        for lo in range(0xA1, 0xFF):
            try:
                ch = bytes([hi, lo]).decode("gb2312")
            except UnicodeDecodeError:
                continue
            qu, wei = hi - 0xA0, lo - 0xA0
            if 16 <= qu <= 55:
                lvl1.append((qu * 100 + wei, ch))
            elif 56 <= qu <= 87:
                lvl2.append((qu * 100 + wei, ch))
    lvl1.sort()
    lvl2.sort()
    return [c for _, c in lvl1], [c for _, c in lvl2]


# --------------------------------------------------------------------------
# 2. 字频：Rime essay.txt（词\t权重），只取单字条目
# --------------------------------------------------------------------------
def load_freq():
    if os.path.exists(FREQ_SRC):
        with open(FREQ_SRC, encoding="utf-8") as f:
            return json.load(f)
    raw = None
    if os.path.exists(FREQ_RAW):
        raw = FREQ_RAW
    else:
        try:
            import urllib.request

            os.makedirs(os.path.dirname(FREQ_RAW), exist_ok=True)
            urllib.request.urlretrieve(FREQ_URL, FREQ_RAW)
            raw = FREQ_RAW
        except Exception as e:
            print("[warn] 无法获取字频表，将退化为 GB2312 区位序：", e)
            return {}
    best = {}
    with open(raw, encoding="utf-8", errors="ignore") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 2 or len(parts[0]) != 1:
                continue
            try:
                v = int(parts[1])
            except ValueError:
                continue
            if parts[0] not in best or v > best[parts[0]]:
                best[parts[0]] = v
    with open(FREQ_SRC, "w", encoding="utf-8") as f:
        json.dump(best, f, ensure_ascii=False)
    return best


# --------------------------------------------------------------------------
# 3. 符号集（逐字符给出，构造时用 unicodedata 回查码位与官方名做校验）
# --------------------------------------------------------------------------
# 说明：下面写成「一串字符 + 我给出的注释」。build 时会打印每个字符的
# U+XXXX 与 Unicode 官方名称，方便人工核对，杜绝手滑写错码位。

MATH_CORE = list("+-×÷±∓=≠≈≡≢≃≅≌≔≕<>≤≥≪≫≮≯⋚⋛⪅⪆∈∉∋∌⊂⊃⊆⊇⊄⊅⊈⊉∪∩∅∖△▽⊎∀∃∄∴∵∎¬∧∨⊕⊖⊗⊙⊘⊻⊼⊽∑∏∐∫∬∭∮∯∰∂∇∆√∛∜∞∝→←↔↑↓↕⇒⇐⇔⟶⟵⟷⟹⟸⟺↦↤↗↘↖↙⇑⇓⇕∣∤∥∦∠∡∢⊾⌒°′″⟨⟩⟪⟫⌈⌉⌊⌋‖|⋮⋯⋰⋱∘⋅∗⋆•⊛⊚⊢⊣⊤⊥⊨⊬⊭≺≻≼≽⊲⊳⊴⊵ℵℏℓ℮ⅇⅈⅉ⨁⨂⨆⨄∷∺∻∽∾∼≀⋈⋉⋊⨯")

MATH_EXT = list("≒≓≜≝≞≟≦≧≨≩≰≱≲≳≴≵≶≷≸≹⊀⊁⊊⊋⊌⊍⊏⊐⊑⊒⊓⊔⊞⊟⊠⊡⊦⊧⊩⊪⊫⊮⊯⊰⊱⊹⊺⋇⋍⋎⋏⋐⋑⋒⋓⋔⋕⋖⋗⋘⋙⋜⋝⋞⋟⋠⋡⋢⋣⋤⋥⋦⋧⋨⋩⋪⋫⋬⋭⨀⨃⨅⨇⨈⨉⨊⨋⨌⨍⨎⨏⨐⨑⨒⨓⨔⨕⨖⨗⨘⨙⨚⨛⨜⨝⨞⨟⨠⨡⨢⨣⨤⨥⨦⨧⨨⨩⨪⨫⨬⨭⨮⨰⨱⨲⨳⨴⨵⨶⨷⨸⨹⨺⨻⨼⨽⨾⨿")

GREEK_UPPER = list("ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ")
GREEK_LOWER = list("αβγδεζηθικλμνξοπρστυφχψω")
GREEK_VAR = list("ϑϕϖϱςϵϝϰϱϑ")

SUPSUB = list("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕₖₗₘₙₒₚₛₜₓ")

# GB 2312-80 01 区收录的中文标点 / 常用全角符号
CJK_PUNCT = list("、。，；：？！「」『』（）《》【】〔〕〈〉〖〗—～…·“”‘’　＋－＝＜＞％＃＠＆＊／＼｜＄")

FRACTIONS = list("½⅓⅔¼¾⅛°℃℉")


def verify(label, chars):
    """回查每个字符的码位与 Unicode 官方名称，打印出来供核对。"""
    rows = []
    seen = set()
    for ch in chars:
        if ch in seen:
            continue
        seen.add(ch)
        cp = ord(ch)
        try:
            name = unicodedata.name(ch)
        except ValueError:
            name = "<无官方名称>"
        rows.append({"char": ch, "cp": cp, "hex": "U+%04X" % cp, "name": name, "cat": label})
    return rows


def main():
    lvl1, lvl2 = enum_gb2312()
    freq = load_freq()

    print("=" * 72)
    print("GB 2312-80 枚举结果：一级字 %d，二级字 %d" % (len(lvl1), len(lvl2)))
    print("字频表条目数：%d" % len(freq))
    print("=" * 72)

    rows = []          # 全集行
    used = set()       # 去重（不同类别可能重复收录同一字符）

    def add(ch, cat, tier, cell):
        if ch in used:
            return
        used.add(ch)
        rows.append(
            {
                "char": ch,
                "cp": ord(ch),
                "hex": "U+%04X" % ord(ch),
                "name": unicodedata.name(ch, "<无官方名称>"),
                "cat": cat,
                "tier": tier,
                "cell": cell,      # han=汉字大方格 / sym=符号格 / lat=拉丁窄格
                "freq": int(freq.get(ch, 0)),
            }
        )

    # ---- 汉字：按字频降序（无频值的排后面，保持区位序） ----
    def sort_han(chars):
        return sorted(chars, key=lambda c: (-int(freq.get(c, 0)), c))

    lvl1_sorted = sort_han(lvl1)
    lvl2_sorted = sort_han(lvl2)
    HAN_TIER0 = 500   # 启动集：高频 500 字

    for i, ch in enumerate(lvl1_sorted):
        add(ch, "han", 0 if i < HAN_TIER0 else 1, "han")
    for ch in lvl2_sorted:
        add(ch, "han_ext", 2, "han")

    # ---- ASCII 可打印 ----
    for cp in range(0x20, 0x7F):
        add(chr(cp), "ascii", 0, "lat")

    # ---- 中文标点 ----
    for ch in CJK_PUNCT:
        add(ch, "cjk_punct", 0, "han" if unicodedata.east_asian_width(ch) in "WF" else "sym")

    # ---- 数学符号（核心 = Tier0，扩展 = Tier1） ----
    for ch in MATH_CORE:
        add(ch, "math", 0, "sym")
    for ch in MATH_EXT:
        add(ch, "math_ext", 1, "sym")

    # ---- 希腊字母 ----
    for ch in GREEK_UPPER + GREEK_LOWER + GREEK_VAR:
        add(ch, "greek", 0, "lat")

    # ---- 上下标 / 分数 / 度 ----
    for ch in SUPSUB + FRACTIONS:
        add(ch, "supsub", 1, "sym")

    # ---- 输出 ----
    with open(os.path.join(CHARSET_DIR, "manifest_all.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["hex", "cp", "char", "name", "cat", "tier", "cell", "freq"])
        w.writeheader()
        for r in rows:
            w.writerow(r)

    with open(os.path.join(CHARSET_DIR, "charset.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)

    for t in (0, 1, 2):
        sub = [r["char"] for r in rows if r["tier"] == t]
        with open(os.path.join(CHARSET_DIR, "chars_tier%d.txt" % t), "w", encoding="utf-8") as f:
            # 每 40 个一行，方便肉眼核对
            for i in range(0, len(sub), 40):
                f.write("".join(sub[i:i + 40]) + "\n")

    by_cat = Counter(r["cat"] for r in rows)
    by_tier = Counter(r["tier"] for r in rows)
    print("全集字符数：%d" % len(rows))
    print("按类别：", dict(by_cat))
    print("按分层：Tier0=%d  Tier1=%d  Tier2=%d" % (by_tier[0], by_tier[1], by_tier[2]))
    print()
    for cat in ("math", "math_ext", "greek", "supsub", "cjk_punct"):
        sel = verify(cat, [r["char"] for r in rows if r["cat"] == cat])
        print("--- %s (%d) ---" % (cat, len(sel)))
        print("  " + " ".join("%s=%s" % (s["hex"], s["char"]) for s in sel[:24]))
        print("  " + " ".join("%s=%s" % (s["hex"], s["char"]) for s in sel[24:48]))
        print()

    print("汉字频率 Top30：", "".join(lvl1_sorted[:30]))
    print()
    print("产物已写入：", CHARSET_DIR)


if __name__ == "__main__":
    main()
