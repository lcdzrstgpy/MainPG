"""本地运行时业务域日志：登录 / AI 处理 / POD 处理三份独立日志文件。

与 run_workbench.py 的 runtime.log 放在同一数据目录（按批次记录、尽量详细，
供用户本地排查；纯本地落盘，与任何上报链路无关）：

- login.log          账号事件：登录成功/失败、注册、激活、验证码、改密、重置、登出
- ai_processing.log  产品处理（AI 生文 / 生图 / 质检）任务批次全过程
- pod_processing.log POD 定制批次全过程

目录规则与 runtime.log 完全一致（WH_LOCAL_RUNTIME_LOGDIR 覆盖 → 打包时
%APPDATA%\\MainPG → 源码运行时当前目录）。文件打不开时静默降级到 root
handler（runtime.log），绝不影响业务主流程。
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_LOGDIR_ENV = "WH_LOCAL_RUNTIME_LOGDIR"

_LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"

# 领域日志文件名（均写入 runtime.log 同目录）
_BUSINESS_LOG_FILES = {
    "login": "login.log",
    "ai_processing": "ai_processing.log",
    "pod_processing": "pod_processing.log",
}

# 每个领域 logger 只配置一次 FileHandler（幂等，防止重复打开/重复写行）
_configured: set[str] = set()


def runtime_log_dir() -> Path:
    """返回本地运行时数据目录（runtime.log 所在的目录）。"""
    override = os.environ.get(_LOGDIR_ENV)
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / "MainPG"
    return Path.cwd()


def _is_testing() -> bool:
    """pytest 环境下不写业务日志文件，避免测试产物污染用户目录。"""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    return "pytest" in str(sys.argv[0] or "").lower()


def business_logger(name: str) -> logging.Logger:
    """获取业务域日志记录器（login / ai_processing / pod_processing）。

    惰性初始化：首次调用时创建独立 FileHandler（追加写、UTF-8、与
    runtime.log 同目录）。如果目录或文件创建失败，则 fallback 到 root
    handler（propagate=True，信息仍进 runtime.log），保证不丢、不报错。
    """
    if name not in _BUSINESS_LOG_FILES:
        raise ValueError(f"unknown business log domain: {name}")
    logger = logging.getLogger(f"business.{name}")
    if name in _configured:
        return logger

    if _is_testing():
        logger.setLevel(logging.INFO)
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        _configured.add(name)
        return logger

    directory = runtime_log_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        directory = Path.cwd()

    handler: logging.Handler | None = None
    try:
        handler = logging.FileHandler(directory / _BUSINESS_LOG_FILES[name], encoding="utf-8")
    except OSError:
        handler = None
    if handler is None:
        # 打开失败：信息回退到 root（runtime.log），不阻断业务。
        logger.setLevel(logging.INFO)
        logger.propagate = True
    else:
        handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        handler.setLevel(logging.INFO)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False  # 独立文件，不再重复写入 runtime.log
    _configured.add(name)
    return logger
