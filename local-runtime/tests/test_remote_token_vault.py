"""远端令牌加密落库：本地后端重启后仍能恢复会话。

回归背景：remote_token 原只存进程内存，后端一重启就丢，前端仍持有有效本地会话却
在需要远端的接口上收到 401 ``remote customer session is missing``，被判会话失效并
退回登录页 —— 用户表现为"登录态过一会儿就掉"。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from wh_local.customer import token_vault
from wh_local.customer.contracts import CustomerAuthResult, LocalSession
from wh_local.customer.db_store import SQLiteCustomerSessionStore
from wh_local.db import init_db, transaction

LOCAL_TOKEN = "local-token-abc"
REMOTE_TOKEN = "remote-tok-123"


def _customer(remote_token: str = REMOTE_TOKEN) -> CustomerAuthResult:
    return CustomerAuthResult(
        customer_id="cust_vault_test",
        username="vault_tester",
        email="vault@example.com",
        remote_token=remote_token,
        workspace_code="local-demo",
        workspace_name="本地演示工作区",
    )


def _session(local_token: str = LOCAL_TOKEN, remote_token: str = REMOTE_TOKEN) -> LocalSession:
    expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(timespec="seconds")
    return LocalSession(
        user_id="cust_vault_test",
        token=local_token,
        expires_at=expires,
        username="vault_tester",
        remote_token=remote_token,
    )


def _seed(database_path, local_token: str = LOCAL_TOKEN, remote_token: str = REMOTE_TOKEN):
    store = SQLiteCustomerSessionStore(database_path)
    store.upsert_customer_user(_customer(remote_token))
    store.save_session(_session(local_token, remote_token), _customer(remote_token))
    return store


requires_dpapi = pytest.mark.skipif(
    not token_vault.vault_available(),
    reason="DPAPI 不可用（非 Windows 或 crypt32 加载失败）—— 此时整体降级为只存内存",
)


# ---------------- token_vault 本体 ----------------


@requires_dpapi
def test_vault_roundtrip_hides_plaintext():
    cipher = token_vault.protect_remote_token(REMOTE_TOKEN)
    assert cipher.startswith(token_vault.VAULT_PREFIX)
    assert REMOTE_TOKEN not in cipher  # 密文里不能出现明文
    assert token_vault.unprotect_remote_token(cipher) == REMOTE_TOKEN


def test_vault_empty_token_short_circuits():
    assert token_vault.protect_remote_token("") == ""
    assert token_vault.unprotect_remote_token("") == ""


def test_vault_rejects_plaintext_and_garbage():
    # 无前缀的历史值一律不当作令牌使用（安全规格：绝不放行明文）
    assert token_vault.unprotect_remote_token(REMOTE_TOKEN) == ""
    assert token_vault.unprotect_remote_token("dpapi:v1:!!!not-base64!!!") == ""
    # 合法 base64 但不是密文
    assert token_vault.unprotect_remote_token("dpapi:v1:YWJj") == ""


# ---------------- 落库：重启可恢复 ----------------


@requires_dpapi
def test_remote_token_survives_store_restart(tmp_path):
    database_path = tmp_path / "workbench.sqlite3"
    init_db(database_path)
    _seed(database_path)

    # 模拟"重启"：全新实例，进程内存为空 —— 旧实现下 remote_token 就此丢失
    fresh = SQLiteCustomerSessionStore(database_path)
    assert fresh.active_remote_tokens() == []

    restored = fresh.get_session(LOCAL_TOKEN)
    assert restored is not None
    assert restored.remote_token == REMOTE_TOKEN, "重启后应当能解密恢复远端令牌"


@requires_dpapi
def test_restart_token_also_survives_a_second_init_db(tmp_path):
    """init_db 是每次启动都会跑的：它必须保留密文（但不能保留明文）。"""
    database_path = tmp_path / "workbench.sqlite3"
    init_db(database_path)
    _seed(database_path)

    init_db(database_path)  # 再跑一遍，等价于重启

    fresh = SQLiteCustomerSessionStore(database_path)
    restored = fresh.get_session(LOCAL_TOKEN)
    assert restored is not None and restored.remote_token == REMOTE_TOKEN


def test_init_db_clears_plaintext_but_keeps_ciphertext(tmp_path):
    database_path = tmp_path / "workbench.sqlite3"
    init_db(database_path)
    _seed(database_path)
    # 塞一条历史明文行（旧规格遗留），必须被清掉
    with transaction(database_path) as conn:
        conn.execute(
            """
            INSERT INTO customer_sessions (
                session_id, user_id, token_hash, expires_at, revoked_at,
                last_used_at, created_at, remote_token
            ) VALUES ('sess_plain', 'cust_vault_test', 'deadbeef',
                      '2099-01-01T00:00:00+00:00', '',
                      '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 'plain-tok')
            """
        )

    init_db(database_path)

    with transaction(database_path) as conn:
        rows = list(conn.execute("SELECT session_id, remote_token FROM customer_sessions"))
    by_id = {str(r["session_id"]): str(r["remote_token"]) for r in rows}
    assert by_id["sess_plain"] == "", "历史明文必须被清除"
    for session_id, value in by_id.items():
        if value:
            assert value.startswith(token_vault.VAULT_PREFIX), f"{session_id} 落库里只允许密文"


# ---------------- 内存回填与撤销 ----------------


@requires_dpapi
def test_restored_token_backfills_process_memory(tmp_path):
    """保活线程、登出流程、remote_token_for_actor 都只读内存表，必须被回填。"""
    database_path = tmp_path / "workbench.sqlite3"
    init_db(database_path)
    _seed(database_path)

    fresh = SQLiteCustomerSessionStore(database_path)
    assert fresh.active_remote_tokens() == []

    fresh.get_session(LOCAL_TOKEN)

    assert fresh.active_remote_tokens() == [REMOTE_TOKEN]


@requires_dpapi
def test_revoke_session_clears_ciphertext(tmp_path):
    database_path = tmp_path / "workbench.sqlite3"
    init_db(database_path)
    store = _seed(database_path)

    store.revoke_session(LOCAL_TOKEN)

    with transaction(database_path) as conn:
        row = conn.execute("SELECT revoked_at, remote_token FROM customer_sessions").fetchone()
    assert str(row["revoked_at"]) != "", "会话应被标记撤销"
    assert str(row["remote_token"]) == "", "登出后必须清掉落库密文"
    assert store.get_session(LOCAL_TOKEN) is None


@requires_dpapi
def test_revoke_all_sessions_clears_ciphertext(tmp_path):
    database_path = tmp_path / "workbench.sqlite3"
    init_db(database_path)
    store = _seed(database_path)

    store.revoke_all_sessions()

    with transaction(database_path) as conn:
        row = conn.execute("SELECT remote_token FROM customer_sessions").fetchone()
    assert str(row["remote_token"]) == ""
    assert store.active_remote_tokens() == []


def test_empty_remote_token_is_not_persisted(tmp_path):
    """没有远端令牌（例如本地演示登录）时不应写入任何东西。"""
    database_path = tmp_path / "workbench.sqlite3"
    init_db(database_path)
    store = SQLiteCustomerSessionStore(database_path)
    store.upsert_customer_user(_customer(""))
    store.save_session(_session(LOCAL_TOKEN, ""), _customer(""))

    with transaction(database_path) as conn:
        row = conn.execute("SELECT remote_token FROM customer_sessions").fetchone()
    assert str(row["remote_token"]) == ""
    assert store.get_session(LOCAL_TOKEN).remote_token == ""
