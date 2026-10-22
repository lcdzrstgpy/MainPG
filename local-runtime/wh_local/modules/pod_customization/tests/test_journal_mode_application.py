"""journal 模式切换必须「尽力而为」，不能因锁竞争把 repository 构造搞崩。

背景（2026-10-22）：`_connect()` 里原来是裸执行 ``PRAGMA journal_mode=<mode>``。
需要**切换**模式时这条 PRAGMA 要拿排他锁，别的连接还开着就直接抛
``database is locked``；全量跑测试时 ``test_app_update`` import 期触发模块级
``app = create_app()``，正好撞上残留连接，连带 3 个用例报红（单跑全过）。

约定：
- 库已经是目标模式 → 一次 PRAGMA 都不发（生产常态，零锁开销）；
- 需要切换且切不动 → 只告警，不抛，沿用当前模式。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from wh_local.modules.pod_customization import repository


def _switches(seen: list[str]) -> list[str]:
    return [
        sql for sql in seen if sql.strip().lower().startswith("pragma journal_mode=")
    ]


def test_already_in_target_mode_issues_no_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POD_SQLITE_JOURNAL_MODE", "WAL")
    connection = sqlite3.connect(tmp_path / "already-wal.sqlite3")
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        seen: list[str] = []
        connection.set_trace_callback(seen.append)

        repository._apply_journal_mode(connection)

        assert _switches(seen) == []
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        connection.close()


def test_switch_blocked_by_other_connection_is_tolerated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POD_SQLITE_JOURNAL_MODE", "DELETE")
    database = tmp_path / "contended.sqlite3"
    holder = sqlite3.connect(database, isolation_level=None)
    try:
        holder.execute("PRAGMA journal_mode=WAL")
        holder.execute("BEGIN IMMEDIATE")  # 握着一个写事务，挡住模式切换

        other = sqlite3.connect(database, timeout=0.05)
        try:
            # 切不动也不许抛：调用方（repository 构造）只关心能不能用上库。
            repository._apply_journal_mode(other)
        finally:
            other.close()
    finally:
        holder.rollback()
        holder.close()


def test_fresh_database_still_gets_wal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """新建库（默认 DELETE）必须被切到目标模式，别把「跳过」写成一刀切。"""
    monkeypatch.setenv("POD_SQLITE_JOURNAL_MODE", "WAL")
    connection = sqlite3.connect(tmp_path / "fresh.sqlite3")
    try:
        repository._apply_journal_mode(connection)
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        connection.close()
