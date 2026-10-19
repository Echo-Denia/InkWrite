#!/usr/bin/env bash
# ==========================================================
#  手写字体流水线 · Linux 侧（切图 -> 组装字体 -> 质检）
#  前提：照片已按页码命名放进 data/photos，例如 page_001.jpg
#
#  用法：
#    ./一键运行_Linux.sh           默认 = pixel（像素保真，推荐）
#    ./一键运行_Linux.sh pixel     像素保真：直接用位图生成，笔画不糊不断
#    ./一键运行_Linux.sh vector    矢量化：potrace 贝塞尔，体积小、可无损放大，
#                                   但细笔画可能糊成墨团 / 断笔
# ==========================================================
set -e

MODE="${1:-pixel}"
case "$MODE" in
  pixel|vector) ;;
  *) echo "[错误] 未知模式 '$MODE'，只支持 pixel 或 vector。"; exit 1 ;;
esac

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo
echo "==== 模式：$MODE ===="

if [ "$MODE" = "vector" ]; then
  command -v potrace >/dev/null || echo "[warn] 未找到 potrace，将退化成 OpenCV 轮廓（建议 apt install -y potrace）"
fi

echo
echo "==== ③ 切图 ===="
python3 scripts/03_slice_sheet.py \
  --layout "$ROOT/templates/tier1_常用全集.layout.json" \
  --photos "$ROOT/data/photos" \
  --out "$ROOT/data/glyphs"

echo
if [ "$MODE" = "pixel" ]; then
  echo "==== ④ 组装字体（像素保真）===="
  python3 scripts/05_build_pixel_font.py \
    --glyphs "$ROOT/data/glyphs" \
    --out "$ROOT/MyHand-Regular.ttf" \
    --family MyHand
else
  echo "==== ④ 组装字体（矢量化）===="
  python3 scripts/04_build_font.py \
    --glyphs "$ROOT/data/glyphs" \
    --out "$ROOT/MyHand-Regular.ttf" \
    --family MyHand
fi

echo
echo "==== ⑤ 质检 ===="
python3 scripts/05_check.py --tier 1

echo
echo "=========================================="
echo "完成（$MODE 模式）。产物："
echo "  MyHand-Regular.ttf   右键安装"
echo "  preview.html         排版效果预览"
echo "  review.html          可疑字复核"
echo "=========================================="
