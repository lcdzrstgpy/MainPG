from wh_local.customer.contracts import CustomerAuthResult
from wh_local.customer.db_store import SQLiteCustomerSessionStore
from wh_local.customer.local_session import LocalSessionService
from wh_local.db import connect, init_db


def test_remote_customer_token_stays_in_process_memory_only(tmp_path) -> None:
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
            remote_token="remote-secret-token",
        )
    )

    with connect(database) as connection:
        stored = connection.execute(
            "SELECT remote_token FROM customer_sessions WHERE user_id = ?",
            (session.user_id,),
        ).fetchone()
    assert stored is not None
    assert stored["remote_token"] == ""
    assert store.get_session(session.token).remote_token == "remote-secret-token"
    assert sessions.remote_token_for_actor(session.user_id, session.workspace_id) == "remote-secret-token"

    restarted = SQLiteCustomerSessionStore(database)
    assert restarted.get_session(session.token).remote_token == ""


def test_init_db_scrubs_legacy_plaintext_remote_tokens(tmp_path) -> None:
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
