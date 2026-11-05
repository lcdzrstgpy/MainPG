from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from wh_local.data_collection.plugin_queue import (
    TEMU_CAPTURE_ALL,
    DataCollectionPluginQueue,
)

CAPS = {"temu_capture_all": True}


def test_queued_command_is_reassigned_to_new_session(tmp_path: Path) -> None:
    """插件重连换新 session 后，旧会话名下的排队指令要改派给新会话。

    回归：旧实现只按 session_id 取队列，插件重连后指令会永远停在「排队中」，
    界面表现为一直转圈、没有新页面打开。
    """
    queue = DataCollectionPluginQueue(tmp_path / "queue.sqlite3")
    old = queue.create_session(
        actor_id="local-demo-admin", workspace_id="default", capabilities=CAPS
    )
    command = queue.queue_command(
        actor_id="local-demo-admin",
        workspace_id="default",
        session_id=old["session_id"],
        command_type=TEMU_CAPTURE_ALL,
        payload={},
        idempotency_key="rebind-1",
    )

    with sqlite3.connect(tmp_path / "queue.sqlite3") as conn:
        conn.execute(
            "UPDATE data_collection_plugin_sessions SET last_seen_at = ? WHERE id = ?",
            ("2026-01-01T00:00:00+00:00", old["session_id"]),
        )

    new = queue.create_session(
        actor_id="local-demo-admin", workspace_id="default", capabilities=CAPS
    )
    delivered = queue.poll(new["session_token"], capabilities=CAPS)
    assert [item.command_id for item in delivered] == [command.command_id]

    # 改派后结果回传按新会话校验，也能正常对上
    updated = queue.receive_result(
        session_token=new["session_token"],
        command_id=command.command_id,
        status="succeeded",
        result={"ok": True},
    )
    assert updated.status == "succeeded"


def test_live_other_session_keeps_its_commands(tmp_path: Path) -> None:
    """另一个仍在轮询的会话（双开浏览器）不能被抢走指令。"""
    queue = DataCollectionPluginQueue(tmp_path / "queue.sqlite3")
    first = queue.create_session(
        actor_id="local-demo-admin", workspace_id="default", capabilities=CAPS
    )
    command = queue.queue_command(
        actor_id="local-demo-admin",
        workspace_id="default",
        session_id=first["session_id"],
        command_type=TEMU_CAPTURE_ALL,
        payload={},
        idempotency_key="rebind-2",
    )
    second = queue.create_session(
        actor_id="local-demo-admin", workspace_id="default", capabilities=CAPS
    )
    delivered = queue.poll(second["session_token"], capabilities=CAPS)
    assert delivered == ()
    assert queue.poll(first["session_token"], capabilities=CAPS)[0].command_id == command.command_id


def test_queue_command_prefers_fresh_session(tmp_path: Path) -> None:
    """插件刚重载：旧会话 20 秒没轮询，新指令应直接派给在轮询的新会话。"""
    queue = DataCollectionPluginQueue(tmp_path / "queue.sqlite3")
    old = queue.create_session(
        actor_id="local-demo-admin", workspace_id="default", capabilities=CAPS
    )
    stale = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat()
    with sqlite3.connect(tmp_path / "queue.sqlite3") as conn:
        conn.execute(
            "UPDATE data_collection_plugin_sessions SET last_seen_at = ? WHERE id = ?",
            (stale, old["session_id"]),
        )
    fresh = queue.create_session(
        actor_id="local-demo-admin", workspace_id="default", capabilities=CAPS
    )
    command = queue.queue_command(
        actor_id="local-demo-admin",
        workspace_id="default",
        session_id=old["session_id"],
        command_type=TEMU_CAPTURE_ALL,
        payload={},
        idempotency_key="prefer-fresh-1",
    )
    delivered = queue.poll(fresh["session_token"], capabilities=CAPS)
    assert [item.command_id for item in delivered] == [command.command_id]
