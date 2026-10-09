#!/usr/bin/env bash
# W-H 本地工作台启动器 (macOS/Linux)
# 启动前自动完成依赖配置（创建虚拟环境 + 安装 requirements），再拉起 8010 后端。
set -euo pipefail
cd "$(dirname "$0")"

echo "============================================"
echo " W-H 本地工作台启动器"
echo " http://127.0.0.1:8010"
echo "============================================"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[错误] 未检测到 python3，请先安装 Python 3.11-3.13。"
  exit 1
fi

# 1. 创建虚拟环境
if [ ! -x ".venv/bin/python" ]; then
  echo "[1/3] 创建虚拟环境 .venv ..."
  python3 -m venv .venv
else
  echo "[1/3] 虚拟环境已存在"
fi

# 2. 依赖配置
echo "[2/3] 检查并安装依赖 ..."
".venv/bin/python" -m pip install --disable-pip-version-check -q -r requirements.txt

# 3. 启动后端
echo "[3/3] 启动工作台: http://127.0.0.1:8010"
if command -v open >/dev/null 2>&1; then
  open "http://127.0.0.1:8010"
fi
exec ".venv/bin/python" -m uvicorn wh_local.app.main:app --host 127.0.0.1 --port 8010
