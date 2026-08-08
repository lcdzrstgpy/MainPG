"""本地工作台桌面启动器（PyInstaller 打包入口）。

用法：
    python run_workbench.py [--host 127.0.0.1] [--port 8010] [--no-browser]

打包后用户双击 MainPG.exe，等效于：启动 FastAPI/uvicorn 并自动打开浏览器。
- 数据目录：源码运行在当前工作目录；打包运行在 %APPDATA%\\MainPG（见 wh_local.config）。
- 端口占用：自动顺延（避免与正在开发的旧实例冲突）。
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import webbrowser

import uvicorn

# windowed 模式（PyInstaller console=False）下 sys.stdout/stderr 为 None，
# uvicorn 日志初始化访问 sys.stdout.isatty() 会抛异常；替换为空设备流。
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8010


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def _first_free_port(host: str, start: int) -> int:
    port = start
    while port < start + 50:
        if not _port_in_use(host, port):
            return port
        port += 1
    raise RuntimeError("no free port available in range")


def _open_browser_later(url: str, delay: float = 2.5) -> None:
    def _open() -> None:
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:  # 浏览器打开失败不阻断服务
            pass

    threading.Thread(target=_open, daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser(description="H Smart Ecommerce Local Workbench")
    parser.add_argument("--host", default=DEFAULT_HOST, help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口，默认 8010")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    port = _first_free_port(args.host, args.port)
    if port != args.port:
        print(f"[run-workbench] port {args.port} in use, fallback to {port}")

    from wh_local.app.main import app

    url = f"http://{args.host}:{port}/"
    if not args.no_browser:
        _open_browser_later(url)
    print(f"[run-workbench] serving workbench at {url}")

    uvicorn.run(app, host=args.host, port=port, log_level="info", access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
