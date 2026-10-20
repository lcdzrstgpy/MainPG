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

from wh_local.runtime_logs import business_logger, runtime_log_dir

# windowed 模式（PyInstaller console=False）下 sys.stdout/stderr 为 None，
# uvicorn 日志初始化访问 sys.stdout.isatty() 会抛异常；替换为空设备流。
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

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


def _resolve_port_conflict(host: str, port: int, card) -> bool:
    """8010 被占时决定怎么办 —— 不再弹 Windows 对话框。

    分两种情况，都是「不问用户」的确定性规则：

    * 占用者是**本程序自己的残留进程**（上次没退干净）→ 直接结束它、继续用 8010。
      这种进程本来就该被清掉，问一次只会多一次打断。
    * 占用者是**别的程序** → 一律不杀，顺延端口，并在卡片上写明「谁占了、换到了
      哪个端口、插件可能连不上」。杀别人的进程不可逆，不能替用户决定。

    返回 True 表示端口已经腾出来了。
    """
    log = logging.getLogger("run_workbench")
    pids = _port_owners(port)
    if not pids:
        return False
    pid = pids[0]
    name = _process_name(pid)
    # 只清「本程序自己」的残留。以前这里也算上 python —— 但用户机器上任何
    # python 进程（开发者的脚本、别的本地服务）都可能占这个端口，杀掉是不可逆的，
    # 不能因为它叫 python 就当是我们自己。宁可顺延端口，也不替用户决定杀进程。
    if "mainpg" not in name.lower():
        # 别的程序占着：把选择权交给用户（卡片上两个按钮），20 秒没人选就顺延。
        if card is None:
            # --no-browser（协议拉起）没有卡片可问，只能顺延。
            return False
        choice = card.ask_port_conflict(port=port, process_name=name, pid=pid)
        if choice != "kill":
            card.set_status(
                "正在启动",
                f"{port} 被「{name}」占用，已改用其它端口启动（浏览器插件可能连不上）",
            )
            log.warning("%s 被「%s」(%s) 占用，改用其它端口", port, name, pid)
            return False
        log.warning("用户选择关闭占用进程 %s(%s) 以使用 %s", name, pid, port)

    log.warning("清理本程序残留进程 %s(%s)，继续使用 %s", name, pid, port)
    _kill_pid(pid)
    for _ in range(20):
        time.sleep(0.25)
        if not _port_in_use(host, port):
            return True
    log.warning("残留进程已结束但 %s 仍未释放，改为顺延端口", port)
    return False


def _is_backend_ready(host: str, port: int, timeout: float = 1.0) -> bool:
    """``/health`` 回 ``{"ok": true}`` 就算本次后端已经能服务。

    比 ``_probe_running_workbench`` 轻：不关心是谁的库、哪个版本，启动卡片
    每 0.6 秒问一次，只回答「能不能开浏览器了」。
    """
    import http.client
    import json

    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        try:
            conn.request("GET", "/health")
            response = conn.getresponse()
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8", "replace"))
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return False
    return isinstance(payload, dict) and payload.get("ok") is True


def _probe_running_workbench(
    host: str, port: int, timeout: float = 1.5
) -> tuple[str, str] | None:
    """探测端口上是否已有本程序后端在正常服务。

    返回 ``(database_path, app_version)``（都可能是空串：旧版本不报 app_version）；
    不可达、或回应得不像本程序时返回 None。

    为什么需要它：「端口被占」不等于「有残留」—— 真正的残留进程早已退出、端口会
    自动释放；反过来，能正常应答 /health 恰恰说明**有实例正在干活**（页面开着、
    可能还有任务在跑）。旧逻辑只看「端口被占」就弹窗让用户 taskkill 掉占用者，
    于是「再启动一次」变成「把正在用的那个杀掉」，前一个页面的接口全部失败、立刻
    弹「本地服务掉线了」（2026-10-16 实测复现）。

    用 http.client 而不是 urllib：前者不读 http_proxy 环境变量，避免本机探测被
    系统代理劫持（本仓库多次踩到 127.0.0.1 走代理变 502 的坑）。
    """
    import http.client
    import json

    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        try:
            conn.request("GET", "/health")
            response = conn.getresponse()
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8", "replace"))
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - 探测失败一律按「不是本程序」处理
        return None
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return None
    database_path = payload.get("database_path")
    app_version = payload.get("app_version")
    return (
        database_path if isinstance(database_path, str) else "",
        app_version if isinstance(app_version, str) else "",
    )


def _expected_database_path() -> str | None:
    """本实例期望的数据库路径（用于识别端口上那个实例是否同一套数据）。"""
    try:
        from wh_local.config import default_config

        return str(default_config().database_path)
    except Exception:  # noqa: BLE001
        return None


def _same_path(left: str, right: str) -> bool:
    """路径比较：忽略大小写与分隔符差异（Windows）。"""
    try:
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))
    except Exception:  # noqa: BLE001
        return left == right


def _read_app_version() -> str:
    """读取本程序的版本号；失败返回空串（不影响启动）。"""
    try:
        from wh_local.config import APP_VERSION

        return str(APP_VERSION)
    except Exception:  # noqa: BLE001
        return ""


def _reuse_running_workbench(
    args: argparse.Namespace, running_db: str, running_version: str = ""
) -> None:
    """复用已在运行的实例：把页面指向它，本实例不启动后端、不杀任何进程。

    --no-browser（协议拉起、页面点「重新连接」）时不另开页面 —— 旧页面自己会恢复，
    这正是该参数的原意。
    """
    _log = logging.getLogger("run_workbench")
    expected_db = _expected_database_path()
    if running_db and expected_db and not _same_path(running_db, expected_db):
        # 端口上跑的是「另一套数据的 MainPG」（例如安装版被本地开发后端占着端口）。
        # 静默复用会让用户以为打开了 A、实际连的是 B，必须明说。
        _log.warning(
            "port %s already serves MainPG with a different data dir: running=%s expected=%s",
            args.port, running_db, expected_db,
        )
        _show_notice_box(
            f"{args.port} 端口上已经有一个界野电商平台在运行，但它用的是另一套数据目录：\n\n"
            f"　　正在运行：{running_db}\n"
            f"　　本次启动：{expected_db}\n\n"
            "为避免看到或改错数据，建议先完全退出那个程序，再启动本次的。\n"
            "本次不会重复启动后台服务。"
        )
    # 版本校验：端口上跑着「上个版本的旧进程」时，database_path 完全一样，
    # 静默复用会让用户升级后看到的还是旧界面（2026-10-19 排查确认的缺口）。
    # 这里只提示、不阻断——旧进程里可能正跑着任务，强杀代价更大。
    expected_version = _read_app_version()
    if (
        running_version
        and expected_version
        and running_version != expected_version
        and not args.no_browser
    ):
        _log.warning(
            "port %s serves an older build: running=%s expected=%s",
            args.port, running_version, expected_version,
        )
        _show_notice_box(
            f"{args.port} 端口上正在运行的界野电商平台是 {running_version}，"
            f"本次打开的是 {expected_version}。\n\n"
            "本次不会重复启动后台服务，所以界面可能仍是旧版本。\n"
            "如需使用新版本，请先**完全退出**正在运行的程序（含托盘图标）再启动。"
        )
    try:
        from wh_local.config import APP_VERSION as app_version
    except Exception:  # noqa: BLE001
        app_version = "unknown"
    url = _workbench_url(args.host, args.port, app_version)
    _log.info("reusing running workbench at %s (data dir: %s)", url, running_db or "(unknown)")
    print(f"[run-workbench] reusing running workbench at {url}")
    if not args.no_browser:
        # 后端已就绪，无需像冷启动那样等 2.5 秒；这里同步打开——本实例马上要退出，
        # daemon 线程来不及跑完就会被解释器回收（_open_browser_later 的延时在复用
        # 路径下必然丢）。
        opened = False
        try:
            opened = webbrowser.open(url)
        except Exception:  # noqa: BLE001 - 打开失败下面会给手动指引
            opened = False
        if not opened:
            # 不能静默退出：用户双击后毫无反应会以为程序坏了。
            _show_notice_box(
                "界野电商平台已经在运行，本次没有重复启动后台服务。\n\n"
                f"请手动在浏览器打开：\n{url}"
            )


def _first_free_port(host: str, start: int) -> int:
    port = start
    while port < start + 50:
        if not _port_in_use(host, port):
            return port
        port += 1
    raise RuntimeError("no free port available in range")


def _bind_server_socket(host: str, start_port: int, attempts: int = 50) -> tuple[socket.socket, int]:
    """原子式绑定监听端口，避开「先探测再绑定」的竞态。

    _first_free_port 用 connect_ex 探测端口空闲，然后才把选择交给 uvicorn 去 bind。
    当桌面端在极短时间内并发拉起多个后端实例时，每个实例都在任何一方 bind 之前
    探测出 8010 空闲，于是全都选中 8010，接着一起 bind，只有一个成功，其余报
    WinError 10048（地址已被占用）并触发“后端服务运行时发生错误并退出”弹窗。

    这里直接自己 bind：bind 本身具备原子性，同一端口只有第一个实例能成功，失败方
    立即落入下一个端口，从而彻底消除该竞态。绑定成功的 socket 用 sockets=[sock]
    交给 uvicorn（Server.run(sockets=[sock])），避免 uvicorn 再次重复绑定。

    返回 (sock, actual_port)；若范围内全部被占用则抛出 OSError。
    """
    for port in range(start_port, start_port + attempts):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if os.name != "nt":
                # POSIX：允许快速重启（TIME_WAIT），但处于 LISTEN 的活跃端口仍会
                # 因 EADDRINUSE 而绑定失败，独占性不受影响。
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # Windows 下默认 bind 即独占：同一活跃端口第二次 bind 会报 WinError
            # 10048。务必不要设置 SO_REUSEADDR，否则将允许两个监听器共用同一端口，
            # 反而破坏本函数的防竞态目的。
            sock.bind((host, port))
            # 交给 asyncio create_server 前无需手动 listen/setblocking，其内部处理。
            return sock, port
        except OSError:
            sock.close()
            continue
    raise OSError("no free port available in range")


def _open_browser_later(url: str, delay: float = 2.5) -> None:
    def _open() -> None:
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:  # 浏览器打开失败不阻断服务
            pass

    threading.Thread(target=_open, daemon=True).start()


def _start_splash(host: str, port: int, version: str):
    """在桌面中间浮一张启动卡片；返回 SplashCard，任何异常都返回 None。

    必须在 ``import wh_local.app.main`` **之前**调用：import 后端 + init_db +
    结构迁移是冷启动耗时的大头（实测 1~18 秒），等它跑完再开浏览器，慢机器上
    必然是「浏览器先到、后端还没 listen」—— 连接被拒，页面直接弹「本地服务掉线
    了」。先给画面、再慢慢起后端，这个空窗就消失了。

    卡片只是体验层：非 Windows、素材缺失、窗口创建失败，一律安静回落成
    「等 2.5 秒再开浏览器」的老行为，绝不阻塞或中断启动。
    """
    log = logging.getLogger("run_workbench")
    try:
        from launcher.splash_qt import SplashCard, splash_asset_path, splash_available

        if not splash_available():
            return None
        asset = splash_asset_path()
        if asset is None:
            log.warning("启动卡片素材缺失（launcher/assets/splash），回落为延迟开浏览器")
            return None
        card = SplashCard(background=asset, version=version, host=host, port=port)
    except Exception:  # noqa: BLE001 - 启动画面失败不影响启动
        log.warning("启动卡片初始化失败，回落为延迟开浏览器", exc_info=True)
        return None
    if not card.start():
        return None
    print("[run-workbench] splash card shown")
    return card


def _workbench_url(host: str, port: int, app_version: str) -> str:
    """Use a versioned document URL so a desktop upgrade cannot reuse stale HTML.

    还要再追加一次性时间戳：``webbrowser.open`` 遇到「已经打开着同一个 URL 的标签」
    时，多数浏览器只会把窗口切到前台、**不会重新加载**。用户「叉掉浏览器 → 点启动」
    却依旧看到关闭前那一屏（包括停在「本地服务掉线了」遮罩上的冻结页面），就是因为
    每次的 URL 一模一样（2026-10-19 用户反馈，复现路径完全对得上）。

    带上 nonce 后每次启动都是**新导航**：必然重新拉 index.html 与最新 assets，
    也不会被浏览器的会话恢复/bfcache 里的旧 DOM 顶替。
    """
    version = quote(app_version.strip() or "unknown", safe="")
    nonce = int(time.time() * 1000)
    return f"http://{host}:{port}/?app_version={version}&t={nonce}"


# --------------------------------------------------------------- 恢复协议

# 工作台页「重新连接」借 mainpg:// 协议重新拉起本程序——浏览器唯一被允许「叫醒」
# 本机程序的通道。应用每次启动时在 HKCU 自注册/自修复该协议（无需管理员权限）；
# 安装脚本里同时声明同名键，负责卸载时清理。
URL_PROTOCOL_SCHEME = "mainpg"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="H Smart Ecommerce Local Workbench")
    parser.add_argument("--host", default=DEFAULT_HOST, help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口，默认 8010")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    # 协议注册命令带回：启动前切到此目录（源码运行的数据目录跟随 cwd；打包不带）。
    parser.add_argument("--chdir", default="", help="启动前先切换到的工作目录")
    return parser


def _windowless_python() -> str:
    """源码运行时优先用 pythonw 解释器，避免协议拉起时闪出黑色控制台窗口。"""
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    if os.name == "nt" and candidate.is_file():
        return str(candidate)
    return str(exe)


def _protocol_open_command() -> str:
    """协议触发时执行的命令行：静默拉起本程序（不开新浏览器页，旧页面会自动恢复）。

    末尾 "%1" 为 URL 协议标准占位符（系统替换为被触发的 URI，程序自行忽略）；
    源码运行额外用 --chdir 固定工作目录，保证数据目录与手动开发启动一致。
    """
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable)}" --no-browser "%1"'
    script = Path(__file__).resolve()
    return (
        f'"{_windowless_python()}" "{script}" --no-browser'
        f' --chdir "{script.parent}" "%1"'
    )


def ensure_url_protocol() -> bool:
    """在 HKCU 注册 mainpg:// 协议（幂等，每次启动自修复）；失败静默返回 False。

    主程序被安全软件终止后，已打开的工作台页面无法自行拉起进程，只能经系统协议
    重启本程序（页面点「重新连接」→ 浏览器确认 → 系统执行本命令）。
    """
    if os.name != "nt":
        return False
    try:
        import winreg

        base = rf"Software\Classes\{URL_PROTOCOL_SCHEME}"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, base, 0, winreg.KEY_WRITE) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f"URL:{URL_PROTOCOL_SCHEME} Protocol")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, base + r"\shell\open\command", 0, winreg.KEY_WRITE
        ) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, _protocol_open_command())
        return True
    except OSError:
        # 注册表不可写（策略限制等）不阻塞启动；页面侧会自动退化为手动引导。
        return False




# 日志文件放在数据目录下（与 workbench.sqlite3 同级），避免 read-only 安装目录。
# 打包运行时在 %APPDATA%\MainPG；源码运行在当前目录。
def _runtime_log_path() -> Path:
    return runtime_log_dir() / "runtime.log"


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
    # 业务域三份日志（登录 / AI 处理 / POD 处理）与 runtime.log 同目录。
    try:
        business_logger("login")
        business_logger("ai_processing")
        business_logger("pod_processing")
        logging.getLogger("run_workbench").info(
            "business logs at %s: login.log / ai_processing.log / pod_processing.log",
            runtime_log_dir())
    except Exception as exc:  # noqa: BLE001 业务日志初始化失败不阻断启动
        logging.getLogger("run_workbench").warning(
            "business log setup failed (falling back to runtime.log): %s", exc)
    return log_path


def _show_fatal_box(message: str) -> None:
    """弹窗提示严重错误（windowed 模式下用户看不到控制台输出）。"""
    try:
        ctypes.windll.user32.MessageBoxW(
            0, message, "界野电商平台", _MB_ICONWARNING | _MB_DEFBUTTON2)
    except Exception:
        pass


def _show_notice_box(message: str) -> None:
    """普通告知弹窗（同样为了 windowed 模式下用户能看到）。"""
    try:
        ctypes.windll.user32.MessageBoxW(0, message, "界野电商平台", _MB_ICONWARNING)
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
    parser = _build_parser()
    # 忽略未知参数：协议拉起时系统会把 mainpg://... 追加在命令行末尾。
    args, _ignored = parser.parse_known_args()
    # 协议注册命令带回的显式工作目录（源码运行的数据目录跟随 cwd，打包版与 cwd
    # 无关、不带该参数）：必须赶在日志初始化之前切换。
    if args.chdir:
        try:
            os.chdir(args.chdir)
        except OSError:
            pass

    # 尽早初始化文件日志：即便后端在 import / init_db 阶段崩溃，也能落盘定位。
    log_path = _configure_runtime_logging()
    _log = logging.getLogger("run_workbench")

    # 自注册/自修复 mainpg:// 协议（页面「重新连接」据此拉起本程序）；失败静默。
    ensure_url_protocol()

    # 端口被占时先问一句：占着它的是不是本程序在正常服务？
    #
    # 「端口被占」不等于「有残留」（详见 _probe_running_workbench）。能应答 /health
    # 的实例是「正在运行的兄弟」——它的页面还开着、可能还有任务在跑。旧逻辑一律弹窗
    # 让用户 taskkill 掉占用者，于是「再启动一次」＝「把正在用的那个杀掉」，前一个
    # 页面的请求全部失败、立刻弹「本地服务掉线了」（2026-10-16 实测复现）。
    # 真正的「历史残留」进程早已退出、端口自动释放，轮不到这里杀。
    # 端口独占仍由下方 _bind_server_socket 的原子 bind 保证，防并发拉起竞态。
    if _port_in_use(args.host, args.port):
        probed = _probe_running_workbench(args.host, args.port)
        if probed is not None:
            # 已经有可用实例 → 复用它：只把页面指向它，本实例不启后端、不杀任何进程。
            running_db, running_version = probed
            _reuse_running_workbench(args, running_db, running_version)
            return 0

    # 卡片必须在「端口决策」之前起来：要在上面显示「被谁占了」，就得先有画面。
    # --no-browser（协议拉起 / 页面点「重新连接」）时不起卡片 —— 旧页面自己会
    # 恢复，再浮一张启动卡片只会让人以为又开了一个工作台。
    card = None
    if not args.no_browser:
        card = _start_splash(args.host, args.port, _read_app_version())

    if _port_in_use(args.host, args.port) and args.port == DEFAULT_PORT:
        _resolve_port_conflict(args.host, args.port, card)

    try:
        sock, port = _bind_server_socket(args.host, args.port)
    except OSError:
        _log.critical(
            "FATAL: no free port available in range starting at %s.", args.port)
        if card is not None:
            card.close()
        _show_fatal_box(
            "后端服务启动失败（端口全部被占用）。\n"
            f"详细信息已写入日志：{log_path}\n请将此文件反馈给技术支持。"
        )
        return 1
    if port != args.port:
        print(f"[run-workbench] port {args.port} in use, fallback to {port}")
        # 顺延端口对用户是「隐形故障」：浏览器插件默认只连 8010，顺延后插件就
        # 连不上了，而打包版没有任何地方能看到当前端口。现在挂在卡片上常驻显示。
        if card is not None:
            card.set_status(
                "正在启动",
                f"{args.port} 端口被占用，已改用 {port} 启动（浏览器插件可能连不上）",
            )
    if card is not None:
        card.set_port(port)
        card.set_progress(0.25)  # 端口已绑定

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
        if card is not None:
            card.close()
        _show_fatal_box(
            "后端启动失败（初始化后端服务/数据库时出错）。\n"
            f"详细信息已写入日志：{log_path}\n请将此文件反馈给技术支持。"
        )
        return 1

    if card is not None:
        # import 后端（create_app + init_db + 结构迁移）是冷启动最耗时的一段，
        # 也是唯一拿不到细粒度进度的一段 —— 这里只在它结束时打一个点。
        card.set_progress(0.55)

    url = _workbench_url(args.host, port, APP_VERSION)
    if card is not None:
        card.set_progress(0.65)  # 交给 uvicorn，剩下一段由探活慢慢爬到 95%
        # 卡片自己探 /health，就绪后淡出并打开浏览器；主线程不等它，直接起 uvicorn。
        card.watch(
            probe=lambda: _is_backend_ready(args.host, port),
            on_ready=lambda: webbrowser.open(url),
        )
    elif not args.no_browser:
        _open_browser_later(url)
    print(f"[run-workbench] serving workbench at {url}")

    # uvicorn 运行期发生致命异常时同样落盘后退出，避免进程静默消失。
    # 用已绑定好的 sock 通过 sockets=[sock] 交给 uvicorn，避免其再次 bind 触发
    # 端口竞态；port/host 仅用于日志与 URL 显示。
    try:
        config = uvicorn.Config(
            app,
            host=args.host,
            port=port,
            log_level="info",
            access_log=False,
            log_config=_UVICORN_LOG_CONFIG,
        )
        uvicorn.Server(config).run(sockets=[sock])
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
