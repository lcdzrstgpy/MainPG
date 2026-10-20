"""远端令牌的落盘规格：**只允许密文、明文绝不落盘**，且重启可恢复。

规格沿革
--------
* 旧规格（已废弃）：``remote_token`` 盘上恒空串、只存在进程内存。
  代价：本地后端一重启令牌即丢失 → 需要远端的接口返回 401
  ``remote customer session is missing`` → 前端判定会话失效并退回登录页，
  用户表现为"登录态过一会儿就掉"。而本地后端在这套桌面运行时里被回收/重启
  （看门狗、更新、异常退出）是常态，所以这个问题会反复发生。
* 新规格（2026-10-14，用户批准）：落库只允许 Windows DPAPI 密文
  （``customer/token_vault.py``，密文绑定当前 Windows 用户），**明文绝不落盘**；
  重启后解密恢复会话。DPAPI 不可用（非 Windows / 调用失败）时整体降级回旧行为。

因此本文件断言两件事：**明文永不入库**、**可用时必须能重启恢复**。
"""
from __future__ import annotations

import pytest

from wh_local.customer import token_vault
from wh_local.customer.contracts import CustomerAuthResult
from wh_local.customer.db_store import SQLiteCustomerSessionStore
from wh_local.customer.local_session import LocalSessionService
from wh_local.db import connect, init_db

SECRET = "remote-secret-token"


def _login(tmp_path):
    database = tmp_path / "workbench.sqlite3"
    init_db(database)
    store = SQLiteCustomerSessionStore(database)
    sessions = LocalSessionService(store)
    session = sessions.login_customer(
        CustomerAuthResult(
            customer_id="customer-1",
            username="operator",
            role="operator",
            workspace_code="workspace-1",
            remote_token=SECRET,
        )
    )
    return database, store, sessions, session


def _stored_token(database, user_id: str) -> str:
    with connect(database) as connection:
        row = connection.execute(
            "SELECT remote_token FROM customer_sessions WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    assert row is not None
    return str(row["remote_token"])


def test_remote_token_is_encrypted_at_rest_never_plaintext(tmp_path) -> None:
    database, store, sessions, session = _login(tmp_path)

    at_rest = _stored_token(database, session.user_id)
    # 最高优先级的安全断言：明文绝不落盘
    assert SECRET not in at_rest
    if token_vault.vault_available():
        assert at_rest.startswith(token_vault.VAULT_PREFIX)
    else:
        # DPAPI 不可用 → 按设计降级为"只存内存"
        assert at_rest == ""

    # 同进程内照常可用（内存副本）
    assert store.get_session(session.token).remote_token == SECRET
    assert sessions.remote_token_for_actor(session.user_id, session.workspace_id) == SECRET


def test_remote_token_survives_restart_when_dpapi_available(tmp_path) -> None:
    """旧规格下这里必然为 ""（正是"登录态过一会儿就掉"的根因）。"""
    if not token_vault.vault_available():
        pytest.skip("DPAPI 不可用：按设计降级为只存内存，重启即失效")
    database, _store, _sessions, session = _login(tmp_path)

    restarted = SQLiteCustomerSessionStore(database)
    assert restarted.active_remote_tokens() == [], "新实例的内存表本来就该是空的"

    restored = restarted.get_session(session.token)
    assert restored is not None
    assert restored.remote_token == SECRET, "重启后必须能从密文恢复远端令牌"


def test_dpapi_unavailable_degrades_to_memory_only(tmp_path, monkeypatch) -> None:
    """降级路径：加密不可用时写空串，行为与旧规格一致（不报错、不影响登录）。"""
    monkeypatch.setattr(token_vault, "_primitives", lambda: (None, None))
    database, store, _sessions, session = _login(tmp_path)

    assert _stored_token(database, session.user_id) == ""
    assert store.get_session(session.token).remote_token == SECRET  # 内存仍可用
    restarted = SQLiteCustomerSessionStore(database)
    assert restarted.get_session(session.token).remote_token == ""


def test_init_db_scrubs_legacy_plaintext_remote_tokens(tmp_path) -> None:
    """历史明文/脏值仍必须被 init_db 清除（安全规格未放宽）。"""
    database = tmp_path / "workbench.sqlite3"
    init_db(database)
    with connect(database) as connection:
        connection.execute(
            """INSERT INTO customer_users
               (user_id, remote_customer_id, username, role, workspace_id)
               VALUES ('legacy-user', 'legacy-user', 'legacy', 'operator', 'default')"""
        )
        connection.execute(
            """INSERT INTO customer_sessions
               (session_id, user_id, token_hash, expires_at, remote_token)
               VALUES ('legacy-session', 'legacy-user', 'hash', '2099-01-01T00:00:00Z', 'legacy-secret')"""
        )
        connection.commit()

    init_db(database)

    with connect(database) as connection:
        assert connection.execute(
            "SELECT remote_token FROM customer_sessions WHERE session_id = 'legacy-session'"
        ).fetchone()["remote_token"] == ""
