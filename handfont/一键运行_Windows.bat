@echo off
chcp 65001 >nul
REM ==========================================================
REM  手写字体流水线 · Windows 侧（切图 -> 组装字体 -> 质检）
REM  前提：照片已按页码命名放进 data\photos，例如 page_001.jpg
REM
REM  用法：
REM    一键运行_Windows.bat           默认 = pixel（像素保真，推荐）
REM    一键运行_Windows.bat pixel     像素保真：直接用位图生成，笔画不糊不断
REM    一键运行_Windows.bat vector   矢量化：potrace 贝塞尔，体积小、可无损放大，
REM                                    但细笔画可能糊成墨团 / 断笔
REM ==========================================================

REM --- 模式参数（默认 pixel）---
set "MODE=%~1"
if "%MODE%"=="" set "MODE=pixel"

REM --- 定位工程根目录 ---
REM 脚本可能放在工程根目录，也可能放在 scripts\ 下；
REM 通过「是否存在 scripts\03_slice_sheet.py」自行判断。
set "SCRIPT_DIR=%~dp0"
if exist "%SCRIPT_DIR%..\scripts\03_slice_sheet.py" (
  for %%I in ("%SCRIPT_DIR%..") do set "ROOT=%%~fI"
) else (
  set "ROOT=%SCRIPT_DIR%"
)
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

REM --- potrace 便携版随工程附带，加到 PATH（vector 模式必需） ---
set "PATH=%PATH%;%ROOT%\lib\potrace-1.16.win64"

REM --- Python 环境 ---
call conda activate moreother
if errorlevel 1 (
  echo [错误] conda 环境 moreother 激活失败，请确认已创建该环境并装好依赖。
  pause
  exit /b 1
)

REM --- 校验模式 ---
if /i not "%MODE%"=="pixel" if /i not "%MODE%"=="vector" (
  echo [错误] 未知模式 "%MODE%"，只支持 pixel 或 vector。
  pause
  exit /b 1
)

echo.
echo ==== 模式：%MODE% ====

echo.
echo ==== ③ 切图 ====
python "%ROOT%\scripts\03_slice_sheet.py" --layout "%ROOT%\templates\tier1_常用全集.layout.json" --photos "%ROOT%\data\photos" --out "%ROOT%\data\glyphs"
if errorlevel 1 (
  echo [错误] 切图失败，中止。
  pause
  exit /b 1
)

echo.
if /i "%MODE%"=="pixel" (
  echo ==== ④ 组装字体（像素保真）====
  python "%ROOT%\scripts\05_build_pixel_font.py" --glyphs "%ROOT%\data\glyphs" --out "%ROOT%\MyHand-Regular.ttf" --family MyHand
) else (
  echo ==== ④ 组装字体（矢量化）====
  python "%ROOT%\scripts\04_build_font.py" --glyphs "%ROOT%\data\glyphs" --out "%ROOT%\MyHand-Regular.ttf" --family MyHand
)
if errorlevel 1 (
  echo [错误] 组装字体失败，中止。
  pause
  exit /b 1
)

echo.
echo ==== ⑤ 质检 ====
python "%ROOT%\scripts\05_check.py" --tier 1

echo.
echo ==========================================
echo 完成（%MODE% 模式）。产物：
echo   MyHand-Regular.ttf   右键安装
echo   preview.html         排版效果预览
echo   review.html          可疑字复核
echo ==========================================
pause
