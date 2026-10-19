<!-- @format -->

# 墨书 InkWrite

把数字版 / 电子文档（PDF、图片、`.tex` 源码）变成**逼真的手写稿**；并附一套**手写字体制作工具**，
让你用纸笔手写、拍照上传，自己生成专属手写字体（TTF）。

> **💡 关于本项目的定位**
>
> - **免费、开源**：以 CC BY-NC 4.0 协议发布，可自由部署、修改、离线使用，不按纸张 / 页数 / 字数 / 会员等任何标准收费，也没有云端计费墙。
>   相比之下，网上多数同类手写工具是封闭的在线服务、按用量计费；墨书把全部代码与流程交给使用者。
> - **数学有特殊优化**：得益于底层走 **XeLaTeX** 排版，公式（行列式、分式、积分、矩阵等）由真正的 TeX 数学引擎渲染，
>   而非简单套字体或贴图，结构 100% 保真（详见[数学优化](#数学优化得益于-xelatex)）。
> - **附带的手写字体制作工具**：「纸笔手写 → 拍照 → 生成 TTF」的字体制作工具，也随本项目开源。
>   用它生成你自己的手写字体后，可直接喂给手写稿生成器，产出「你本人笔迹」的手写稿。

本项目包含两部分：

| 部分                   | 目录               | 用途                                                     |
| ---------------------- | ------------------ | -------------------------------------------------------- |
| **① 手写稿生成器**     | `iwlib/`、`run.py` | 把任意文档渲染成带手写字体 + 做旧纸张的图片 / PDF        |
| **② 手写字体制作工具** | `handfont/`        | 打印模板 → 拍照 → 切图 → 组装 TTF → 质检（你自己的笔迹） |

两部分都围绕「你的真实手写」展开；字体制作工具产出的 TTF 可直接喂给①使用。

---

## 目录

- [一、手写稿生成器（iwlib）](#一手写稿生成器iwlib)
- [二、手写字体制作工具（handfont）](#二手写字体制作工具handfont)
- [三、许可协议与依赖声明](#三许可协议与依赖声明)
- [四、免责声明](#四免责声明)

---

# 一、手写稿生成器（iwlib）

把数字版 / 电子文档变成逼真的手写稿：手写中文字体 + 公式里的拉丁字母 / 数字也用手写字体（mathastext），
最后加字块扰动、背景纸、扫描做旧。

## 原理

```
输入(PDF/图片/.tex)
  → ① 判型：有文字层直接抽文本，.tex 直接解析；扫描件才走 MinerU OCR
  → ② IR(JSON)：block 类型 + text + 行内/行间公式
  → ③ XeLaTeX 重建：ctexart + mathastext，中文用楷体、公式字母用手写体
  → ④ 600dpi 渲染 → 反相灰度取墨迹 alpha
  → ⑤ 字块级扰动：位移/旋转/缩放/墨色
  → ⑥ 背景纸 + 噪声 + 光照 + 轻微模糊 → 输出 PNG / PDF
```

## 依赖

- **Python 3.10–3.13**
- `pip install fitz pymupdf pillow numpy scipy`（注意：`fitz` 即 `PyMuPDF`，见[依赖声明](#三许可协议与依赖声明)）
- **TeX Live**（xelatex 必需），需要 `ctex`、`mathastext`（`tlmgr install mathastext`）
- 手写字体：**本仓库不附带任何字体**（`*.ttf` 已 gitignore），需自备，用
  `--font-cjk` / `--font-latin` 指定任意 TTF/OTF（**必须用文件名加载**，家族名会导致
  mathastext 失效）。
  - 自己制作：用 [手写字体制作工具](#二手写字体制作工具handfont) 生成专属字体。
  - 临时试用：可用系统自带中文字体，例如 `C:/Windows/Fonts/STKAITI.TTF`（楷体）。
  - 字体**必须含空格字形 U+0020**，否则 XeLaTeX 会整篇缺字。
- **MinerU**（可选，仅扫描件 / 含公式的 PDF 需要）：
  - `pip install mineru`（4.x 用 `mineru-kit parse`，2.x 用 `mineru -p`，脚本自动识别）
  - 建议设置 `MINERU_MODEL_SOURCE=modelscope`（默认已开启，国内下载快）

## 输入判型（重要）

| 输入                   | 处理方式                                                                                                       |
| ---------------------- | -------------------------------------------------------------------------------------------------------------- |
| `.tex` 源码            | 直接解析，公式 100% 保真（**最优路径**）                                                                       |
| 无公式的数字版 PDF     | 直接抽文字层，快                                                                                               |
| **含公式的数字版 PDF** | 走 MinerU OCR——LaTeX 排版后的公式在文字层里已碎成字形流（`\frac`、`\lim` 结构丢失），只有视觉 OCR 能还原 LaTeX |
| 扫描件 / 图片          | 走 MinerU OCR                                                                                                  |

未装 MinerU 时可降级：`--textlayer-fallback`（公式会退化成字形文本，结构丢失但不丢内容）。

## 使用

```powershell
# .tex 源文件（最优，免 OCR）
python -m iwlib.cli "paper.tex" -o output

# 含公式的 PDF（需要 MinerU）
python -m iwlib.cli "doc.pdf" -o output

# 一个文件夹里的所有 PDF
python -m iwlib.cli "path/to/folder" -o output

# 自定义选项
python -m iwlib.cli "doc.pdf" -o output --bg lined --dpi 300 --ink "#2B2A29"
python -m iwlib.cli "doc.pdf" -o output --bg grid --font-cjk "C:\fonts\MyHan.ttf"

# 合并整个文件夹 + 抄 3 遍 + 手写体放大 1.15 + 数学符号用手写体
python -m iwlib.cli "path/to/folder" -o output --merge --repeat 3 --font-scale 1.15 --math-symbols
```

## 参数

| 参数           | 默认       | 说明                                              |
| -------------- | ---------- | ------------------------------------------------- |
| `-o, --out`    | `./output` | 输出目录                                          |
| `--bg`         | `plain`    | 背景纸：`plain` / `lined`（横线）/ `grid`（格子） |
| `--dpi`        | `300`      | 输出分辨率                                        |
| `--font-cjk`   | 见下       | 中文手写字体文件（TTF/OTF）                       |
| `--font-latin` | 见下       | 拉丁 / 公式字母手写字体文件（TTF/OTF）            |
| `--font-scale` | `1.0`      | 手写字体相对 Latin Modern 数学符号的放大倍数       |
| `--ink`        | `#2B2A29`  | 墨色（非纯黑更真实）                              |
| `--tier`       | `standard` | MinerU 档位                                       |
| `--skip-ocr`   | off        | 扫描件不 OCR 直接失败（省去 MinerU 下载）         |
| `--modelscope` | on         | MinerU 模型从 ModelScope 下载                     |
| `--merge`      | off        | 把文件夹里所有文档合并成**一个** PDF              |
| `--repeat N`   | `1`        | 抄 N 遍（内容重复 N 次）                          |
| `--math-symbols` | off      | 数学符号也用手写体（见下）                        |
| `--math-delimiters` | off    | 行列式竖线 `\|` 也用手写体（**不建议**，见下）    |

> **默认字体**：`--font-cjk` / `--font-latin` 默认指向 `handfont/` 下你自己做的手写字体
> （`iwlib/config.py` 里的 `cjk_font_file` / `latin_font_file`）。
>
> ⚠️ **本仓库不附带任何 TTF/OTF 字体**：所有 `*.ttf` 已按 `.gitignore` 排除
> （个人笔迹不入库）。克隆仓库后默认字体**并不存在**，必须用 `--font-cjk`
> 显式指定你自己的字体，或先用
> [手写字体制作工具](#二手写字体制作工具handfont) 生成一份。
>
> 另外字体**必须含空格字形 U+0020**，否则 XeLaTeX 会整篇缺字（详见
> [局限](#局限)）。

### `--repeat N`：抄 N 遍

把正文重复 N 遍写进**同一个连续的文本流**（不在副本之间插入 `\newpage`/`\clearpage`），
因此 TeX 用自己的排版器整体重排：

- 第 2 遍会**接着第 1 遍最后一页剩下的版面继续写**，不会另起一张纸；
- 与 `--merge` 一起用时，多份文档 + 多遍全部拼成**一个 PDF**；
- 错字涂改只在生成「第一遍」时注入一次，再整体重复，所以每遍的涂改位置一致
  （像真的把同一段重抄一遍）。

```powershell
python -m iwlib.cli "notes.tex" -o out --merge --repeat 3
```

### `--font-scale`：让手写体不显得小

手写字的笔画在 em 框里占比天然比印刷体小，直接和 Latin Modern 的数学符号混排时会偏小。
**正确做法是给 fontspec 传 `Scale=`：不要改 TTF 的 `unitsPerEm`/升降部**——改字体内部的
度量会让 XeLaTeX 的字形渲染出问题（缺字、基线错位）。

```powershell
python -m iwlib.cli "notes.tex" -o out --font-scale 1.15
```

### `--math-symbols`：数学符号也用手写体

默认**关闭**：为了让公式可读，运算符 / 关系符 / 括号 / 大运算符 / 箭头 / 希腊字母
都保留 Latin Modern Math（`range` 只映射了 `up/{latin,num}`，即字母和数字）。
打开后这些符号改用手写字体，前提是**你的字体确实含有这些字形**
（用 fontTools 查 `TTFont(...).getBestCmap()` 即可；一份完整的手写字体通常含
括号、`= + - × ÷ ± ≠ ≈ ≤ ≥ → ∈`、`∑ ∏ ∫ √ ∞`、希腊字母等）。

另有两个前提要注意：

- **标点不会被映射**：见下，`,` `.` `;` `!` `?` 只能沿用 Latin Modern。
- **没写过的字会是印刷体**：模板上留空的格子（如 `j`、`.`、`:`、`'`、空格 U+0020 等
  在纸上"写不出来"的字符）会由**系统字体兜底**渲染成印刷体。可在
  `data/glyphs_merged/merge_manifest.csv` 里查每个字的来源
  （`hand` 手写 / `ai:...` AI 补 / `font:simsun.ttc` 印刷兜底）。
- **半角 vs 全角标点**：手写里没必要严格区分，所以 `scripts/12_remap_punct.py`
  会把**半角码位指向你已经写好的全角/中文标点**（`.` ← `。`、`，` `、`'` ← `’`、
  `:` ← `：`，`j` 用 AI 版本），避免这几处冒出印刷体。注意顺序：
  **先跑 `11_merge_and_build.py` 填充 `data/glyphs_merged`，再跑 `12_remap_punct.py`，
  最后直接调 `05_build_pixel_font.py --glyphs data/glyphs_merged` 重建字体**
  ——因为 `11` 启动时会清空 `data/glyphs_merged`，先替换会被冲掉。

```powershell
python -m iwlib.cli "math.pdf" -o out --math-symbols
```

> 注意：密集公式换成手写符号后**可读性会下降**，建议正文作业开，纯演算草稿可关掉。
> unicode-math 的 `range` 只接受真实命令名。已实测**无效**的名字（会报 "not recognised"
> 或 "Undefined control sequence"）：
> `\plus \neq \to \semicolon \period \comma \exclam \question \beta \pi \mu \dots \degree \iff
> \mathminus \unaryminus \binaryminus \ellipsis \dots`。
> 其中 **`+` 的正确名字是 `\mathplus`**（`\plus` 无效），省略号用 `\ldots \cdots \vdots \ddots`。

#### 加号 / 减号 / 省略号的两个坑

- **减号**：数学模式输出的是**真减号 U+2212**，而模板上写的是**连字符 U+002D**，
  两者不同码位。字体里若只有 U+002D，公式里的负号就会退回印刷体。
  `handfont/fix_minus.py` 会把 U+2212 补上（直接复用你写的连字符字形，二者都是一横）。
- **省略号**：`\ldots` 才是有效名（`\dots` / `\ellipsis` 无效），映射后指向 U+2026。

验证方式：分别用/不用该 range 渲染 `$a + b - c \ldots d$`，比较墨量与形状是否不同。
> 因此**标点（`,` `.` `;` `!` `?`）无法经 range 映射**，只能沿用 Latin Modern；代码里
> 已改用经验证的名字（`\minus \ne \rightarrow` …）。

### `--math-delimiters`：行列式的竖线（**默认关，建议保持关**）

`\left| ... \right|`（行列式）用的 `|` 是**可拉伸定界符**。位图生成的手写字体没有
`MATH` 表的**可延展/变体字形数据**，无法随内容变高，结果 4 行行列式左右只剩**两小截短竖线**：
实测手写竖线只有 99px（占内容高 8%），而 Latin Modern 是 288px（24%）。

所以 `| \lvert \rvert \vert \mid` 默认**保留印刷体**（保证竖线拉满）。若你确实想要手写竖线
并接受它拉不长，可显式打开：

```powershell
python -m iwlib.cli "math.pdf" -o out --math-symbols --math-delimiters
```

## 调参

全部参数在 `iwlib/config.py`：

- **排版密度**：`fontsize_pt`(默认 22，字大)、`linespread`(默认 0.5，行距挤)、`margin_mm`
- **字间距**：`letter_spacing`(默认 0.0)，单位 **em**（1.0 = 一个字宽），
  **支持负值**：

  | 值 | 效果 |
  | --- | --- |
  | `-0.10` | **收紧**（字挤在一起） |
  | `0.0` | 默认 |
  | `0.05` | 略松 |
  | `0.15` | 明显疏朗 |

  走 xeCJK 的 `CJKglue`（中文）+ `\spaceskip`（拉丁），随 `fontsize_pt` 自动缩放。
  实测同一行（`pdftotext -bbox` 量字形跨度）：
  `-0.10` → 100.8、`0.0` → 109.6、`+0.10` → 118.4，正负两个方向都生效。

  实现要点：必须用 `\kern` 而不是 `\hspace`——**`\hspace` 不接受负值**（会被 clamp 或报错），
  `\kern` 才能双向调整。
  配套开关 `letter_spacing_latin`(默认 True) 可只让中文变、拉丁不变。
- **手写感核心**：`jit_slant`(整页斜度，默认 4°)、`jit_line_rot`(行旋转)、`jit_baseline`(基线漂移 px)
- **随机化**：`jit_size`(字号 ±5.5%)、`jit_gap`(字距 px，默认 1.4)、`jit_line_gap`(行距随机游走 px，默认 3.0)
- **扰动强度**：`jit_dx` / `jit_dy`(px)、`jit_rot`(度)、`jit_scale`(比例)、`jit_alpha_min`
- **错字涂改**：`error_rate`(默认 3%)。在 LaTeX 层注入：随机把某个汉字换成形近错字（内置形近字表），用 `\cancel` 斜杠划掉，旁边（前或后随机）补上正确字。标题和公式内不注入
  - **不会画出灰方块**：形近字表里很多是生僻字（`兲 亽 尐 氺 釒 …`），个人手写字体通常**没有这些字形**，
    XeLaTeX 会渲染成 `.notdef`（一小块灰色空方框）。`iwlib/mistake.py` 现在会**先查字体再选错字**
    （`font_has()`），只从字体真正含有的码位里挑；实测注入的错字缺失数 6 → **0**。
    换字体后自动生效，无需改字表。
- **纸张质感**：`bg_gradient`(纸色)、`noise_sigma`、`blur_radius`、`jitter_rot`(整页旋转)
- **抄写遍数**：`repeat`（默认 1）。同 `--repeat`，重复进同一文本流，遍与遍之间不留空页
- **手写体放大**：`font_scale`（默认 1.0）。同 `--font-scale`，走 `fontspec Scale=`，不改字体度量
- **手写数学符号**：`handwrite_math_symbols`（默认 False）。同 `--math-symbols`

## 输出结构

```
output/<文档名>/
  page_001.png ...    # 手写页 PNG
output/<文档名>_handwritten.pdf   # 合并 PDF
work/<文档名>/        # 中间产物：ir.json / handwrite.tex / raster/
```

## 局限

- **公式保真依赖 OCR 质量**：MinerU 的 LaTeX 输出并非 100%，编译失败的公式块会跳过（`render_math_block` 返回空），整页不崩。
- **数学符号**（∫∑√≤∈ 等）来自数学字体，不是手写体——与真实学生作业一致，可接受。
- **扫描件 OCR 速度**：MinerU CPU 单页约 15s，建议 GPU 或先只做数字版。

## 数学优化（得益于 XeLaTeX）

很多同类手写工具（包括在线服务）对数学无能为力：它们只是把文本「换套字体」，公式一旦复杂（行列式、分式、积分、矩阵、多行推导）就会错位、缺符号，或干脆要求你先把公式算好贴成图片。

墨书的文档重建链路**全程由 XeLaTeX 排版驱动**，公式是被真正的 TeX 数学引擎渲染出来的，因此：

- **结构保真**：`.tex` 源文件直接解析时，行列式（`vmatrix`）、分式（`\frac`）、上下限（`\sum\int\lim`）、分段函数（`cases`）、矩阵等原样还原，不丢结构。
- **手写体只作用在「字母 / 数字」上**：通过 `mathastext` 把公式里的拉丁字母与数字替换成你的手写字体，而 ∫ ∑ √ ≤ ∈ 等数学符号仍取自专业数学字体——与真实学生作业一致，既手写又不失严谨。
- **中文 + 公式混排自然**：ctex + mathastext 让正文楷体、公式字母手写体在同一页里协调共存，不会出现「公式突然跳成印刷体方块」的违和感。

```powershell
# 含公式的 .tex 是最优路径：公式 100% 保真，免 OCR
python -m iwlib.cli "paper.tex" -o output
```

> 墨书本地运行、完全免费：无论生成多厚的数学手写稿都不产生任何费用，
> 而且能直接生成**自己笔迹**的手写公式（配合下面的手写字体工具）。

---

# 二、手写字体制作工具（handfont）

用「纸 + 拍照」把自己的真实笔迹做成可安装的 TTF 字体。整个流程的分工是：
使用者只需产出高质量的纸和照片，其余的切图、组装、质检等机械性工作全部由脚本完成。

> 本仓库只保留可再生的部分（脚本、字符集清单、打印模板、一键脚本、依赖说明）。
> 个人手写字照片 / 切图 / 质检产物不入库；**AI 补字**相关代码与第三方模型也**不入库**（见[许可协议](#三许可协议与依赖声明)）。

## 安装依赖

```bat
conda activate moreother
uv pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r handfont/requirements.txt
```

- 自检：`python -c "import cv2,fontTools; print(hasattr(cv2,'aruco'))"` 应输出 `True`
  （若 `aruco` 为 False，说明装成了 `opencv-python`，需重装 `opencv-contrib-python`）。
- **矢量化模式**（可选，出片体积更小）需要 `potrace`。Linux：`apt install potrace`；
  Windows 工程已附带便携版 `handfont/lib/potrace-1.16.win64/`，`04` 会自动加进 PATH。
  没有 potrace 也能跑，会自动退化成 OpenCV 轮廓（笔画边缘偏硬）。
- 本流程**用不上 GPU**（纯 CPU 图像处理）；AI 补字才需要，见下文。

## 制作流程（使用者自己写的字）

### 第 0 步：决定规模

| 路线               | 需书写              | 预计耗时    | 结果                                                     |
| ------------------ | ------------------- | ----------- | -------------------------------------------------------- |
| **Tier0 启动集**   | 866 个（6 页）      | 2–3 小时    | 高频 500 汉字 + 英文 + 数学符号 + 希腊字母，日常打字够用 |
| **Tier1 常用全集** | 4311 个（29 页）    | 12–20 小时  | GB2312 全部 3755 常用字，覆盖 99.75% 日常用字            |
| **Tier2 完整**     | 7319 个（约 50 页） | 40 小时以上 | 含 3008 个次常用字                                       |

**建议：直接印 Tier1（29 页），但只需先写其中 6 页** —— Tier0 的 866 字符已排在 Tier1 最前面，行列位置一致，先写完即等于完成 Tier0。

### 第 1 步：准备与打印模板

- 笔：黑色中性笔 / 针管笔 0.5 或 0.7，**不要铅笔**（太浅）、**不要钢笔**（洇墨）。
- 纸：80g A4 普通纸；打印机**按 100% 实际大小打印** `handfont/templates/tier1_常用全集.pdf`（已含 Tier0 全部内容）。
- 每格：右下角极小的灰色数字 = Unicode 码位；左下角极小的淡灰参考字 = 告诉你写什么（**不要照着描**，会丢失笔迹特征）。
- 打印后自检：汉字页格子边长应约 **16 mm**，拉丁 / 符号页 **13 mm**，差 >0.5mm 重打。

### 第 2 步：书写规范

1. 写在格内不出血；2. 字占格子高度 60%–75%；3. 踩在格内基线（实线）上；
2. 不连写到邻格；5. 整批同一支笔、同力度、同坐姿；6. 每写完一页休息，避免疲劳歪斜。

### 第 3 步：拍照

- 白天自然光或从左右两侧打光；手机在纸正上方垂直俯拍，四角黑色标记全部在画面内；纸压平。
- 按页码命名放入 `handfont/data/photos/`：`page_001.jpg`、`page_002.jpg` …
  （脚本会用四角 ArUco 反解页号交叉校验，与文件名对不上会告警）。

### 第 4 步：一键运行（切图 → 组装 → 质检）

```bat
handfont\一键运行_Windows.bat            # 默认 pixel（像素保真，推荐）
handfont\一键运行_Windows.bat vector     # 矢量化（体积小，细笔画可能糊/断）
```

```bash
# Linux / macOS
bash handfont/一键运行_Linux.sh            # 或 vector
```

产物：

- `handfont/MyHand-Regular.ttf` —— 右键安装，Word / PPT / PS 立刻可用
- `handfont/preview.html` —— 浏览器看中英数混排效果
- `handfont/review.html` —— 把写得明显不一致的字挑出来，只需重写这些

### 命令行分步（按需）

| 步骤       | 脚本                                                                                                     | 说明                                    |
| ---------- | -------------------------------------------------------------------------------------------------------- | --------------------------------------- |
| ① 建字符集 | `01_build_charset.py`                                                                                    | 生成 `charset/`（已生成，一般不用重跑） |
| ② 出模板   | `02_make_template.py --tier 1 --out templates/tier1_常用全集 --ghost`                                    | 打印给人写                              |
| ③ 切图     | `03_slice_sheet.py --layout templates/tier1_常用全集.layout.json --photos data/photos --out data/glyphs` | 验收：`empty=0`、告警 0                 |
| ④ 组装     | `05_build_pixel_font.py --glyphs data/glyphs --out MyHand-Regular.ttf --family MyHand`                   | 失败 0 个                               |
| ⑤ 质检     | `05_check.py --tier 1`                                                                                   | 缺失 → 回 ② 补写                        |

- ④ 备选用 `04_build_font.py`（矢量化 / potrace 贝塞尔）。
- **改字流程**：用 `data/cleaner.html` 修完 PNG 后，只重跑 ④，不要重跑 ③（否则修改被覆盖）。

### 目录契约（保留在仓库内的部分）

```
handfont/
├── charset/              ① 产出：charset.json / manifest_all.csv / chars_tier{0,1,2}.txt / freq_cn.json
├── templates/            ② 产出：*.pdf（打印给人写）+ *.layout.json（几何地图，关键）
├── data/                 用户数据（photos/、glyphs/ 等，按 .gitignore 忽略）
├── scripts/              我们自己写的脚本
│   ├── 01_build_charset.py
│   ├── 02_make_template.py
│   ├── 03_slice_sheet.py
│   ├── 04_build_font.py        # 矢量化
│   ├── 05_build_pixel_font.py  # 像素保真（默认）
│   ├── 05_check.py
│   └── 06_make_reference_sheet.py
├── lib/potrace-1.16.win64/   矢量化工具（GPLv2，随工程附带）
├── 一键运行_Windows.bat / 一键运行_Linux.sh
├── 00_安装依赖.md
└── requirements.txt
```

> 单字文件名用 **Unicode 十进制** 而非汉字本身（扩展字符不允许做文件名，Windows 命令行传汉字参数也容易踩编码坑）。

## 可选：用 AI 微调补充自己的字体（高级）

> ⚠️ **许可提醒**：AI 补字依赖第三方模型 **FontDiffuser**（依据其作者声明，**仅限非商业研究用途**，商用需联系原作者），
> 以及其官方预训练权重。因此**本仓库不收录 AI 补字代码与模型**（`handfont/lib/fontdiffuser/`、`handfont/ckpt/`、`handfont/data/glyphs_ai*/` 等已在 `.gitignore` 中忽略）。
> 如果你只想用自己的真实手写字做字体，**完全可以跳过这一步**，上面的流程已足够。
>
> 若你希望了解如何用「少量手写样本 + 本地扩散模型」把字体扩充到完整常用字集（全程不上传数据），
> 思路是：① 用上面流程写好 Tier0/Tier1；② 以你的手写字为风格参考，用 FontDiffuser 微调生成未写的字；
> ③ 合并 + 重建字体。具体实现请自行参考 FontDiffuser 上游仓库并遵守其**非商业研究**许可。

---

# 三、许可协议与依赖声明

## 本项目许可

本项目（**仅限本仓库中由作者编写的代码、文档与可再生的字符集/模板数据**）采用
**知识共享 署名-非商业性使用 4.0 国际许可协议（CC BY-NC 4.0）**。

- ✅ 任何人可**免费**复制、分发、修改、部署本项目（包括部署到服务器）供自己或他人**免费**使用；
- ❌ **禁止将本项目用于任何商业 / 营利目的**（例如收费服务、售卖、以营利为目的的再分发）。
- 使用、修改、再分发时须**保留原作者署名**，并标明是否作了修改、指明本协议。

完整条款见仓库根目录 `LICENSE` 文件（CC BY-NC 4.0）。

## 第三方依赖（与协议的关系）

本项目自身采用非商业许可，**并不重新许可或捆绑**下列第三方组件；它们各自以原许可条款提供，由使用者自行安装 / 获取：

| 依赖                                                              | 用途                             | 上游许可                                                | 与本项目关系                                                                                     |
| ----------------------------------------------------------------- | -------------------------------- | ------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| PyMuPDF（`fitz`）                                                 | 主流程 PDF 渲染 / 文本抽取       | **AGPLv3**（或商业许可）                                | 仅作为外部依赖 `pip` 安装，**不随本仓库分发、不修改、不打包**；AGPL 的 copyleft 不波及本仓库源码 |
| MinerU                                                            | 可选，扫描件 / 含公式 PDF 的 OCR | MinerU Open Source License（基于 Apache 2.0，附加条款） | 外部依赖，可免费用于商业（达门槛需商业许可）；不影响本项目许可                                   |
| fontTools                                                         | 组装 TTF                         | MIT / BSD                                               | 宽松许可，无冲突                                                                                 |
| Pillow / numpy / scipy / reportlab / opencv-contrib-python / tqdm | 图像处理、模板生成、切图等       | 各自 MIT / BSD / Apache 等宽松许可                      | 无冲突                                                                                           |
| potrace（便携版随工程附带）                                       | 矢量化                           | **GPLv2**                                               | 以独立可执行文件形式附带，未修改其源码；GPLv2 仅约束 potrace 自身                                |
| FontDiffuser + 官方权重                                           | **AI 补字（第三方模型）**        | 非商业研究用途（作者声明）                              | **不收录进本仓库**（见 `.gitignore`）；如需使用请遵守其非商业研究许可                            |

> 简言之：本仓库的「非商业」限制**只管我们自己的代码**；第三方依赖按各自许可由用户另行获取，互不传染。
> 但若你将本项目与 AGPL 组件（如 PyMuPDF）结合**再分发修改后的整体**，需留意 AGPL 对「整体」的源码公开要求——本项目源码本身已公开，故不冲突。

---

# 四、免责声明

本软件按**「原样」**提供，不作任何明示或暗示的担保，包括但不限于对适销性、特定用途适用性、
不侵权、无潜在缺陷或错误、准确性之担保。

- 使用本项目（包括但不限于运行脚本、生成字体、部署服务、处理任何文档或图像）所产生的一切**风险由使用者自行承担**。
- 在适用法律允许的最大范围内，作者 / 贡献者**不对**因使用或无法使用本软件所导致的任何直接、间接、
  偶然、特殊、 consequential 或惩罚性损害（包括但不限于数据丢失、设备损坏、业务中断、利润损失）承担责任，
  即使已被告知此类损害的可能性。
- 你手写上传的照片、生成的字体及任何衍生内容，其版权与合规责任由你本人负责。
- 本项目可能调用第三方依赖 / 模型（见上文），其许可与风险由相应权利人规定，作者不对第三方组件的行为或许可合规性负责。
- 任何人基于本项目所进行的一切行为，均视为已充分知悉并同意上述免责条款；作者不因提供本软件而承担任何保证或赔偿责任。
