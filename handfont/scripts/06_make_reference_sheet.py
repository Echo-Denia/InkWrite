#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
06_make_reference_sheet.py
==========================
生成「书写对照表」HTML：把每个格子对应的字符按 PDF 的真实行列排出来。

用户在印刷题时，只需要 02 的 layout.json（写死了「第几页第几行第几列 = 哪个码位」），
就能把「character -> 页行列」还原出来。这个脚本不做任何猜测：
直接读取 layout.json 的 order 数组，所以 HTML 与 PDF 永远一致。

用法：
  python scripts/06_make_reference_sheet.py --layout templates/tier0_启动集.layout.json
  python scripts/06_make_reference_sheet.py --layout templates/tier0_启动集_带参考字.layout.json
"""

import argparse
import html
import json
import os
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def display_char(ch):
    """给空格、控制字符这类不可见字符一个可见表示。"""
    if ch == " ":
        return "␣"
    if ch == "　":
        return "␣␣"
    if ord(ch) < 0x20 or unicodedata.category(ch) in ("Cc", "Cf"):
        return "·"
    return ch


def char_name(cp):
    try:
        return unicodedata.name(chr(cp))
    except ValueError:
        return ""


def render(layout, layout_path):
    pages = layout["pages"]
    title = os.path.basename(layout_path).replace(".layout.json", "")

    total = sum(len(p["order"]) for p in pages)
    css = """
    <style>
    body{font-family:"Segoe UI","Microsoft YaHei",sans-serif;margin:24px;background:#fff;color:#111}
    h1{font-size:20px;margin-bottom:4px}
    .meta{color:#888;font-size:12px;margin-bottom:18px}
    .page{margin-bottom:26px;page-break-inside:avoid;break-inside:avoid}
    .pnum{font-size:14px;font-weight:600;margin-bottom:6px}
    .pnum span{font-weight:400;color:#888;font-size:12px;margin-left:8px}
    table{border-collapse:collapse}
    td{border:1px solid #ccc;width:38px;height:38px;text-align:center;vertical-align:middle;
       font-size:19px;position:relative}
    td.idx{width:auto;height:auto;padding:0 4px;font-size:9px;color:#bbb;border:0;
           text-align:right;font-family:Consolas,monospace}
    td.empty{border:0}
    .cp{position:absolute;right:1px;bottom:0;font-size:7px;color:#d0d0d0;
        font-family:Consolas,monospace}
    .sp{color:#c00;font-weight:700}
    .lat td{width:32px;height:32px;font-size:17px}
    @media print{body{margin:10px}.page{page-break-inside:avoid}}
    </style>
    """

    out = ["<!doctype html><meta charset='utf-8'>",
           "<title>书写对照表 — %s</title>" % html.escape(title), css,
           "<h1>书写对照表 · %s</h1>" % html.escape(title),
           "<div class='meta'>共 %d 页，%d 个字符。"
           "行列与 PDF 完全一致：左边数字是行号，顶部列所在行即第 1 行。"
           "格子右下角小字是 Unicode 十进制码位。</div>" % (len(pages), total)]

    # 每页一个表格：第一列是行号，其余是格子
    for pg in pages:
        cols = pg["cols"]
        rows_n = pg["rows"]
        cell_mm = pg["cell_mm"]
        cls = "lat" if pg["cell"] in ("lat", "sym") else ""
        out.append("<div class='page'>")
        out.append("<div class='pnum'>第 %d 页<span>%s 格型 · 格边长 %.0fmm · "
                   "%d 行 × %d 列 · 本页 %d 字</span></div>"
                   % (pg["page"] + 1, pg["cell"], cell_mm, rows_n, cols, len(pg["order"])))
        out.append("<table class='%s'>" % cls)
        for r in range(rows_n):
            out.append("<tr>")
            out.append("<td class='idx'>%02d</td>" % (r + 1))
            for c in range(cols):
                i = r * cols + c
                if i >= len(pg["order"]):
                    out.append("<td class='empty'></td>")
                    continue
                cp = pg["order"][i]
                ch = display_char(chr(cp))
                # 空格 / 全角空格用红色提示：那一格是留空的，不要写
                extra = " sp" if cp in (0x20, 0x3000) else ""
                out.append("<td class='%s'>%s<span class='cp'>%d</span></td>"
                           % (extra.strip(), html.escape(ch), cp))
            out.append("</tr>")
        out.append("</table></div>")

    out.append("</body>")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout", required=True, help="*.layout.json")
    ap.add_argument("--out", default=None, help="输出 HTML 路径")
    args = ap.parse_args()

    with open(args.layout, encoding="utf-8") as f:
        layout = json.load(f)

    if args.out:
        out_path = args.out
    else:
        base = os.path.basename(args.layout).replace(".layout.json", "")
        out_path = os.path.join(ROOT, "templates", "%s_书写对照表.html" % base)

    html_text = render(layout, args.layout)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_text)

    total = sum(len(p["order"]) for p in layout["pages"])
    print("[ok] 书写对照表：", out_path)
    print("      %d 页 / %d 个字符" % (len(layout["pages"]), total))


if __name__ == "__main__":
    main()
