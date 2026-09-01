"""本地工作台桌面启动器（PyInstaller 打包入口）。

用法：
    python run_workbench.py [--host 127.0.0.1] [--port 8010] [--no-browser]

打包后用户双击 MainPG.exe，等效于：启动 FastAPI/uvicorn 并自动打开浏览器。
- 数据目录：源码运行在当前工作目录；打包运行在 %APPDATA%\\MainPG（见 wh_local.config）。
- 端口占用：默认端口（8010）被占时弹窗征询用户，同意后自动释放占用进程；
  用户拒绝或释放失败才自动顺延（插件默认只连接 8010）。
"""
from __future__ import annotations

import argparse
import ctypes
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from urllib.parse import quote

import uvicorn

# windowed 模式（PyInstaller console=False）下 sys.stdout/stderr 为 None，
# uvicorn 日志初始化访问 sys.stdout.isatty() 会抛异常；替换为空设备流。
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

# 全量开启产品处理客户端直连模式：任务走批次冻结 + 客户端直连（火山方舟/无印），
# 不再经服务器 AI 网关中转（网关并发闸会串行化图片生成导致处理变慢）。
# setdefault 允许运维用显式环境变量覆盖关闭（WH_PRODUCT_AI_DIRECT=0）。
os.environ.setdefault("WH_PRODUCT_AI_DIRECT", "1")

# 桌面端：关闭前端页面后自动终止后端进程（服务器 systemd 不设此变量，永不自动退出）。
# 浏览器插件默认只连 8010；关页即停可避免后端残留进程锁定 MainPG.exe 导致安装失败。
os.environ.setdefault("WH_LOCAL_RUNTIME_EXIT_ON_CLOSE", "1")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8010

_MB_YESNO = 0x00000004
_MB_ICONWARNING = 0x00000030
_MB_DEFBUTTON2 = 0x00000100
_IDYES = 6


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def _port_owners(port: int) -> list[int]:
    """返回监听指定端口的所有进程 PID（用于识别占用者）。"""
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"], stderr=subprocess.DEVNULL, timeout=15
        ).decode("gbk", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return []
    pids: list[int] = []
    seen: set[int] = set()
    marker = f":{port}"
    for line in out.splitlines():
        parts = line.split()
        # 形如: TCP  0.0.0.0:8010  0.0.0.0:0  LISTENING  97124
        if len(parts) >= 5 and parts[0].upper() in {"TCP", "TCP6"} and marker in parts[1]:
            if parts[3].upper() != "LISTENING":
                continue
            try:
                pid = int(parts[4])
            except ValueError:
                continue
            if pid > 0 and pid not in seen:
                seen.add(pid)
                pids.append(pid)
    return pids


def _process_name(pid: int) -> str:
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            stderr=subprocess.DEVNULL,
            timeout=15,
        ).decode("gbk", errors="replace").strip()
        if out:
            name = out.split('","')[0].lstrip('"').strip()
            if name:
                return name
    except (OSError, subprocess.SubprocessError):
        pass
    return "未知程序"


def _ask_release_port(port: int, pid: int, name: str) -> bool:
    """弹窗征询：用户同意后调用方负责释放端口。返回 True 表示同意。"""
    is_workbench = "mainpg" in name.lower() or "mainpg" in os.path.basename(name).lower()
    display = "另一个界野电商平台（残留实例）" if is_workbench else f"「{name}」"
    text = (
        f"界野电商平台需要使用 {port} 端口启动，\n"
        f"但该端口当前被 {display}（进程号 {pid}）占用。\n\n"
        f"点击“是”：自动关闭该程序，然后用 {port} 端口继续启动；\n"
        f"点击“否”：改用其他端口启动（浏览器插件可能无法连接）。"
    )
    try:
        result = ctypes.windll.user32.MessageBoxW(
            0, text, "界野电商平台 - 端口提示",
            _MB_YESNO | _MB_ICONWARNING | _MB_DEFBUTTON2,
        )
        return result == _IDYES
    except Exception:
        return False


def _kill_pid(pid: int) -> bool:
    """强制结束指定进程，不终止由 MainPG 启动的更新安装器。"""
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            timeout=20,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _release_default_port(host: str, port: int) -> bool:
    """尝试通过弹窗征询 + 自动结束占用进程的方式释放默认端口。"""
    pids = _port_owners(port)
    if not pids:
        return False
    pid = pids[0]
    name = _process_name(pid)
    if not _ask_release_port(port, pid, name):
        print(f"[run-workbench] user declined to release port {port}")
        return False
    _kill_pid(pid)
    for _ in range(20):
        time.sleep(0.25)
        if not _port_in_use(host, port):
            return True
    return False


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


def _workbench_url(host: str, port: int, app_version: str) -> str:
    """Use a versioned document URL so a desktop upgrade cannot reuse stale HTML."""
    version = quote(app_version.strip() or "unknown", safe="")
    return f"http://{host}:{port}/?app_version={version}"


def main() -> int:
    parser = argparse.ArgumentParser(description="H Smart Ecommerce Local Workbench")
    parser.add_argument("--host", default=DEFAULT_HOST, help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口，默认 8010")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    # 默认端口被占时：弹窗征询用户，同意后自动释放占用进程（插件默认只连默认端口）。
    if args.port == DEFAULT_PORT and _port_in_use(args.host, args.port):
        _release_default_port(args.host, args.port)

    port = _first_free_port(args.host, args.port)
    if port != args.port:
        print(f"[run-workbench] port {args.port} in use, fallback to {port}")

    from wh_local.app.main import app
    from wh_local.config import APP_VERSION

    url = _workbench_url(args.host, port, APP_VERSION)
    if not args.no_browser:
        _open_browser_later(url)
    print(f"[run-workbench] serving workbench at {url}")

    uvicorn.run(app, host=args.host, port=port, log_level="info", access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
