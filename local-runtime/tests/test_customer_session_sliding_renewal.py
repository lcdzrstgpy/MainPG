from datetime import datetime, timedelta, timezone
import hashlib

from wh_local.customer.contracts import CustomerAuthResult
from wh_local.customer.db_store import SQLiteCustomerSessionStore
from wh_local.customer.local_session import LocalSessionService
from wh_local.db import connect, init_db


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _login(store: SQLiteCustomerSessionStore) -> str:
    sessions = LocalSessionService(store)
    session = sessions.login_customer(
        CustomerAuthResult(
            customer_id="customer-1",
            username="operator",
            role="operator",
            workspace_code="workspace-1",
            remote_token="remote-secret-token",
        )
    )
    return session.token


def test_get_session_slides_expiry_for_active_sessions(tmp_path) -> None:
    """活跃会话每次使用都顺延过期时间，避免长期使用的用户被硬过期误踢。"""
    database = tmp_path / "workbench.sqlite3"
    init_db(database)
    store = SQLiteCustomerSessionStore(database)
    token = _login(store)

    # 先把过期时间改成「1 小时后」，这样续期与否的差异是确定性的
    soon = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(timespec="seconds")
    with connect(database) as connection:
        connection.execute(
            "UPDATE customer_sessions SET expires_at = ? WHERE token_hash = ?",
            (soon, _token_hash(token)),
        )
        connection.commit()

    seen = store.get_session(token)
    assert seen is not None
    assert seen.expires_at > (datetime.now(timezone.utc) + timedelta(days=6)).isoformat(timespec="seconds"), (
        f"活跃会话应顺延到 7 天量级，实际 {seen.expires_at}"
    )

    with connect(database) as connection:
        stored = connection.execute(
            "SELECT expires_at FROM customer_sessions WHERE token_hash = ?",
            (_token_hash(token),),
        ).fetchone()
    assert stored is not None
    assert stored["expires_at"] == seen.expires_at, "返回值与库内续期后的过期时间应一致"


def test_get_session_does_not_revive_expired_sessions(tmp_path) -> None:
    """过期会话查不到，且不会被滑动续期复活。"""
    database = tmp_path / "workbench.sqlite3"
    init_db(database)
    store = SQLiteCustomerSessionStore(database)
    token = _login(store)
    token_hash = _token_hash(token)

    expired = "2000-01-01T00:00:00+00:00"
    with connect(database) as connection:
        connection.execute(
            "UPDATE customer_sessions SET expires_at = ? WHERE token_hash = ?",
            (expired, token_hash),
        )
        connection.commit()

    assert store.get_session(token) is None

    with connect(database) as connection:
        stored = connection.execute(
            "SELECT expires_at FROM customer_sessions WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
    assert stored is not None
    assert stored["expires_at"] == expired, "过期行不应被续期复活"
