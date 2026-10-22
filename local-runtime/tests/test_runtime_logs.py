from __future__ import annotations

import logging
import threading
from pathlib import Path

from wh_local import runtime_logs


def _reset_logger(name: str) -> logging.Logger:
    """把全局 logger 还原成「本进程尚未初始化过」的状态，避免测试互相污染。"""
    logger = logging.getLogger(f"business.{name}")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.propagate = True
    if hasattr(logger, runtime_logs._READY_ATTR):
        delattr(logger, runtime_logs._READY_ATTR)
    return logger


def _file_handlers(logger: logging.Logger) -> list[logging.Handler]:
    return [handler for handler in logger.handlers if isinstance(handler, logging.FileHandler)]


def test_concurrent_first_use_mounts_only_one_file_handler(
    tmp_path: Path, monkeypatch
) -> None:
    """并发首次调用只能挂一个 FileHandler，否则同一行日志会落盘两次。"""
    name = "pod_processing"
    logger = _reset_logger(name)
    monkeypatch.setattr(runtime_logs, "_is_testing", lambda: False)
    monkeypatch.setattr(runtime_logs, "runtime_log_dir", lambda: tmp_path)

    barrier = threading.Barrier(8)

    def initialize() -> None:
        barrier.wait()
        runtime_logs.business_logger(name)

    threads = [threading.Thread(target=initialize) for _ in range(8)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(_file_handlers(logger)) == 1
        runtime_logs.business_logger(name).info("并发初始化探针")
        lines = (tmp_path / "pod_processing.log").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert "并发初始化探针" in lines[0]
    finally:
        _reset_logger(name)


def test_existing_handler_for_same_file_is_reused(tmp_path: Path, monkeypatch) -> None:
    """标记丢失但 handler 还在（模块被两个路径 import）时，不重复挂第二个。"""
    name = "login"
    logger = _reset_logger(name)
    monkeypatch.setattr(runtime_logs, "_is_testing", lambda: False)
    monkeypatch.setattr(runtime_logs, "runtime_log_dir", lambda: tmp_path)

    try:
        runtime_logs.business_logger(name).info("第一条")
        # 模拟另一份模块实例：READY 标记不在，但同文件的 FileHandler 已经挂上。
        delattr(logger, runtime_logs._READY_ATTR)
        runtime_logs.business_logger(name).info("第二条")

        assert len(_file_handlers(logger)) == 1
        lines = (tmp_path / "login.log").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
    finally:
        _reset_logger(name)
