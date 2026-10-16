"""关程序退出必须保留登录态（2026-10-16 决策）。

回归背景：退出流程原本接线 ``release_all_sessions()`` —— 撤销本机全部本地会话并
把落库的 remote_token 密文清空。于是「关掉程序再打开就要重新登录」，把
``test_remote_token_vault`` 那套「DPAPI 落盘 + 重启可恢复」的修复整个废掉。

实证：本地库 ``customer_sessions`` 共 148 条，其中 146 条被撤销（98.6%）；
10-13 一早上 06:48/06:57/07:30/07:31/07:47/08:33 连续 6 条会话全部被撤销 ——
即每次开关程序掉一次登录。

现在退出不再释放会话；主动退出登录仍走 ``/api/customer/logout``（只撤销当前一条）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from wh_local.customer.contracts import CustomerAuthResult, LocalSession
from wh_local.customer.db_store import SQLiteCustomerSessionStore
from wh_local.db import init_db

LOCAL_TOKEN = "exit-retention-token"
REMOTE_TOKEN = "exit-retention-remote"

MAIN_PY = Path(__file__).resolve().parents[1] / "wh_local" / "app" / "main.py"


def _seed(database_path) -> None:
    store = SQLiteCustomerSessionStore(database_path)
    customer = CustomerAuthResult(
        customer_id="cust_exit_test",
        username="exit_tester",
        email="exit@example.com",
        remote_token=REMOTE_TOKEN,
        workspace_code="local-demo",
        workspace_name="本地演示工作区",
    )
    expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(timespec="seconds")
    store.upsert_customer_user(customer)
    store.save_session(
        LocalSession(
            user_id="cust_exit_test",
            token=LOCAL_TOKEN,
            expires_at=expires,
            username="exit_tester",
            remote_token=REMOTE_TOKEN,
        ),
        customer,
    )


def test_exit_does_not_wire_session_release() -> None:
    """退出流程不得把会话释放接上（源码级守护：接线会让关程序即登出）。"""
    source = MAIN_PY.read_text(encoding="utf-8")
    active_calls = [
        line.strip()
        for line in source.splitlines()
        if line.strip().startswith("runtime_exit.set_on_release_sessions(")
    ]
    assert active_calls == [], (
        "发现处于生效状态的会话释放接线：它会在退出时清空落库的 remote_token 密文，"
        "导致「关掉程序再打开就要重新登录」（见 main.py 该处注释）"
    )
    # 接口本身要保留，便于将来需要时恢复
    assert "def set_on_release_sessions" in source


def test_release_all_sessions_destroys_restorable_login_state(tmp_path) -> None:
    """对照实验：说明为什么不能接线 —— 一旦释放，重启后会话再也恢复不了。

    与 ``test_remote_token_vault::test_remote_token_survives_store_restart`` 互为
    正反面：那条证明「不释放就能恢复」，本条证明「释放了就恢复不了」。
    """
    database_path = tmp_path / "workbench.sqlite3"
    init_db(database_path)
    _seed(database_path)

    # 关程序之前的状况：重启后仍能恢复登录
    assert SQLiteCustomerSessionStore(database_path).get_session(LOCAL_TOKEN) is not None

    # 若退出时释放会话（旧行为：LocalSessionService.release_all_sessions()
    # 最终就调到这里）：
    SQLiteCustomerSessionStore(database_path).revoke_all_sessions()

    # 再启动就再也拿不到会话 —— 用户被迫重新登录
    assert SQLiteCustomerSessionStore(database_path).get_session(LOCAL_TOKEN) is None
