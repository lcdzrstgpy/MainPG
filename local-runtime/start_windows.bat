@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo  W-H 本地工作台启动器 (Windows)
echo  http://127.0.0.1:8010
echo ============================================

REM ---- 1. 检查 Python ----
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.11-3.13 并勾选 "Add python.exe to PATH"。
    pause
    exit /b 1
)

REM ---- 2. 创建虚拟环境 ----
if not exist ".venv\Scripts\python.exe" (
    echo [1/3] 创建虚拟环境 .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败
        pause
        exit /b 1
    )
) else (
    echo [1/3] 虚拟环境已存在
)

REM ---- 3. 依赖配置（每次启动前校验，缺什么装什么）----
echo [2/3] 检查并安装依赖 ...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络或 requirements.txt
    pause
    exit /b 1
)

REM ---- 4. 启动后端 ----
echo [3/3] 启动工作台: http://127.0.0.1:8010
start "" "http://127.0.0.1:8010"
".venv\Scripts\python.exe" -m uvicorn wh_local.app.main:app --host 127.0.0.1 --port 8010

pause
