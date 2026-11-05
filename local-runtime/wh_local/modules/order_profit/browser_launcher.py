"""按店铺账号拉起独立浏览器实例。

Temu 的登录态是浏览器 cookie，同一配置目录只能同时登一个账号。这里给每个账号
分配独立的 user-data-dir，并把本仓库的浏览器插件一起加载进去，实现「多账号并行」。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

_EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)
_CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)


class BrowserLaunchError(RuntimeError):
    """找不到浏览器或插件目录时抛出。"""


def _repo_root() -> Path:
    # local-runtime/wh_local/modules/order_profit/browser_launcher.py
    return Path(__file__).resolve().parents[4]


def find_browser() -> str:
    override = os.environ.get("WH_BROWSER_PATH", "").strip()
    if override and Path(override).exists():
        return override
    for candidate in _EDGE_CANDIDATES + _CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    for name in ("msedge", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    raise BrowserLaunchError("没有找到 Edge/Chrome，请安装或在 WH_BROWSER_PATH 指定浏览器路径")


def find_extension_dir() -> Path:
    override = os.environ.get("WH_EXTENSION_DIR", "").strip()
    if override:
        path = Path(override)
        if (path / "manifest.json").exists():
            return path
        raise BrowserLaunchError(f"WH_EXTENSION_DIR 下没有 manifest.json：{path}")
    candidates = sorted(
        _repo_root().glob("W-H-browser-extension-v*"),
        key=lambda item: item.name,
    )
    for path in reversed(candidates):
        if (path / "manifest.json").exists():
            return path
    raise BrowserLaunchError("仓库里没有找到已解压的浏览器插件目录（W-H-browser-extension-v*）")


def profile_dir(data_dir: str | Path, tag: str) -> Path:
    path = Path(data_dir) / "browser-profiles" / f"acc-{tag}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def open_instance(
    *,
    tag: str,
    start_url: str,
    data_dir: str | Path,
) -> dict[str, Any]:
    """用独立配置目录启动一个浏览器实例，返回 {pid, profile, browser}。"""
    browser = find_browser()
    extension = find_extension_dir()
    profile = profile_dir(data_dir, tag)
    args = [
        browser,
        f"--user-data-dir={profile}",
        f"--load-extension={extension}",
        f"--disable-extensions-except={extension}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--new-window",
        start_url,
    ]
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "DETACHED_PROCESS", 0
        )
    process = subprocess.Popen(  # noqa: S603 - 参数固定，无 shell
        args,
        creationflags=creationflags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    return {"pid": int(process.pid), "profile": str(profile), "browser": browser}


def close_instance(pid: int) -> bool:
    if not pid:
        return False
    if sys.platform == "win32":
        result = subprocess.run(  # noqa: S603
            ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0
    try:
        os.kill(int(pid), 15)
        return True
    except OSError:
        return False
