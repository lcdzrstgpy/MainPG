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
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path
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


# 日志文件放在数据目录下（与 workbench.sqlite3 同级），避免 read-only 安装目录。
# 打包运行时在 %APPDATA%\MainPG；源码运行在当前目录。
def _runtime_log_path() -> Path:
    header = "WH_LOCAL_RUNTIME_LOGDIR"
    override_dir = os.environ.get(header)
    if override_dir:
        return Path(override_dir) / "runtime.log"
    if getattr(sys, "frozen", False):
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / "MainPG" / "runtime.log"
    return Path.cwd() / "runtime.log"


def _configure_runtime_logging() -> Path:
    """落盘运行日志并接管未捕获异常，使后端启动故障可回传定位。

    打包为 PyInstaller windowed（console=False）且 stdout/stderr 被替换为
    devnull，任何未捕获异常都会静默丢失（用户只见“页面开了、端口没有”）。
    这里把日志写入数据目录 runtime.log，并让 uvicorn/内部模块日志同样落盘。

    返回日志文件路径。
    """
    log_path = _runtime_log_path()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        log_path = Path.cwd() / "runtime.log"

    # root logger 同时路由到文件与控制台，关键：uvicorn/内部模块日志都会进来。
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    has_file = any(isinstance(h, logging.FileHandler) for h in root.handlers)
    has_stream = any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in root.handlers
    )
    if not has_file:
        try:
            fh = logging.FileHandler(log_path, encoding="utf-8")
            fh.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"))
            root.addHandler(fh)
        except OSError:
            pass
    if not has_stream:
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"))
        root.addHandler(sh)

    # 捕获未捕获异常（主线程与后台线程），写入日志而非静默退出。
    def _excepthook(exc_type, exc_value, exc_tb):
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        logging.getLogger("run_workbench").critical("UNCAUGHT EXCEPTION:\n%s", text)
        _show_fatal_box("程序发生未捕获异常，详细信息已写入日志文件，请联系技术支持。")

    def _thread_excepthook(args):
        _excepthook(args.exc_type, args.exc_value, args.exc_traceback)

    # 避免重复安装 hook（如模块被多次 import）。
    if not getattr(sys, "_wh_run_excepthook_installed", False):
        sys.excepthook = _excepthook
        threading.excepthook = _thread_excepthook
        sys._wh_run_excepthook_installed = True  # type: ignore[attr-defined]

    logging.getLogger("run_workbench").info(
        "workbench launcher starting | log=%s | frozen=%s | cwd=%s",
        log_path, getattr(sys, "frozen", False), os.getcwd())
    return log_path


def _show_fatal_box(message: str) -> None:
    """弹窗提示严重错误（windowed 模式下用户看不到控制台输出）。"""
    try:
        ctypes.windll.user32.MessageBoxW(
            0, message, "界野电商平台", _MB_ICONWARNING | _MB_DEFBUTTON2)
    except Exception:
        pass


# uvicorn 默认 LOGGING_CONFIG 会 disable_existing_loggers=True，从而禁用我们
# 挂到 root 的 file handler。这里用 False，并向三个关键 logger 关闭 propagate，
# 让其错误也能被 root/file handler 捕获，完整落盘。
_UVICORN_LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {"format": "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "default"},
    },
    "loggers": {
        "uvicorn": {"handlers": ["console"], "level": "INFO"},
        "uvicorn.error": {"level": "INFO"},
        "uvicorn.access": {"level": "WARNING"},
    },
}


def main() -> int:
    # 尽早初始化文件日志：即便后端在 import / init_db 阶段崩溃，也能落盘定位。
    log_path = _configure_runtime_logging()
    _log = logging.getLogger("run_workbench")

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

    # 后端 app 在模块导入阶段即执行 create_app()/init_db()；此处捕获其异常并落盘，
    # 否则 uvicorn 永远不会启动，用户只看到“页面开了、端口没有”且无任何报错。
    try:
        from wh_local.app.main import app
        from wh_local.config import APP_VERSION
    except BaseException as exc:  # noqa: BLE001 导入期异常需全部捕获
        _log.critical(
            "FATAL: failed to import backend / init db.\n%s",
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        )
        _show_fatal_box(
            "后端启动失败（初始化后端服务/数据库时出错）。\n"
            f"详细信息已写入日志：{log_path}\n请将此文件反馈给技术支持。"
        )
        return 1

    url = _workbench_url(args.host, port, APP_VERSION)
    if not args.no_browser:
        _open_browser_later(url)
    print(f"[run-workbench] serving workbench at {url}")

    # uvicorn 运行期发生致命异常时同样落盘后退出，避免进程静默消失。
    try:
        uvicorn.run(
            app,
            host=args.host,
            port=port,
            log_level="info",
            access_log=False,
            log_config=_UVICORN_LOG_CONFIG,
        )
    except BaseException as exc:  # noqa: BLE001 运行期致命异常需全部捕获
        _log.critical(
            "FATAL: uvicorn exited.\n%s",
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        )
        _show_fatal_box(
            "后端服务运行时发生错误并退出。\n"
            f"详细信息已写入日志：{log_path}\n请将此文件反馈给技术支持。"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
