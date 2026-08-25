from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wh_local.billing import set_topup_promotion_active, settle_payment_order, topup_promotion_status
from wh_local.customer.auth_server import create_auth_app
from wh_local.db import init_db, transaction

from test_customer_billing import _EMAIL_CODE_SECRET, _register_and_login


def _client_and_headers(tmp_path: Path, monkeypatch) -> tuple[TestClient, Path, dict[str, str]]:
    database_path = tmp_path / "auth.sqlite3"
    monkeypatch.setenv("WH_EMAIL_CODE_SECRET", _EMAIL_CODE_SECRET)
    monkeypatch.setattr(
        "wh_local.customer.auth_server.TencentCloudSESEmailSender.from_env",
        lambda: object(),
    )
    client = TestClient(create_auth_app(database_path))
    token = _register_and_login(client, database_path)
    return client, database_path, {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize(
    ("package_id", "amount_cents", "base_points"),
    [
        ("points_50", 5_000, 5_000),
        ("points_99", 9_900, 9_900),
        ("points_199", 19_900, 19_900),
        ("points_499", 49_900, 49_900),
        ("points_999", 99_900, 99_900),
    ],
)
def test_active_topup_promotion_snapshots_every_fixed_package(
    tmp_path: Path,
    monkeypatch,
    package_id: str,
    amount_cents: int,
    base_points: int,
) -> None:
    client, database_path, headers = _client_and_headers(tmp_path, monkeypatch)
    set_topup_promotion_active(database_path, active=True, updated_by="test")

    response = client.post(
        "/api/customer/billing/topup-orders",
        json={
            "provider": "alipay",
            "package_id": package_id,
            "idempotency_key": f"promotion-fixed-{package_id}-0001",
        },
        headers=headers,
    )

    assert response.status_code == 200
    order = response.json()["order"]
    assert order["amount_cents"] == amount_cents
    assert order["points"] == base_points
    assert order["base_points"] == base_points
    assert order["promotion_bonus_points"] == base_points
    assert order["total_points"] == base_points * 2
    assert order["promotion_id"] == "topup_double"


def test_topup_promotion_controls_custom_quote_and_disabled_orders(tmp_path: Path, monkeypatch) -> None:
    client, database_path, headers = _client_and_headers(tmp_path, monkeypatch)

    disabled_quote = client.post(
        "/api/customer/billing/topup-quote",
        json={"amount_cents": 12_300},
        headers=headers,
    )
    assert disabled_quote.status_code == 200
    assert disabled_quote.json()["product"] == {
        "package_id": "custom",
        "label": "自定义积分充值",
        "amount_cents": 12_300,
        "points": 12_300,
        "base_points": 12_300,
        "promotion_bonus_points": 0,
        "total_points": 12_300,
        "promotion_id": "",
        "promotion_name": "",
    }

    set_topup_promotion_active(database_path, active=True, updated_by="test")
    active_quote = client.post(
        "/api/customer/billing/topup-quote",
        json={"amount_cents": 12_300},
        headers=headers,
    )
    assert active_quote.status_code == 200
    assert active_quote.json()["product"]["base_points"] == 12_300
    assert active_quote.json()["product"]["promotion_bonus_points"] == 12_300
    assert active_quote.json()["product"]["total_points"] == 24_600

    set_topup_promotion_active(database_path, active=False, updated_by="test")
    order_response = client.post(
        "/api/customer/billing/topup-orders",
        json={
            "provider": "alipay",
            "package_id": "custom",
            "amount_cents": 12_300,
            "idempotency_key": "promotion-custom-disabled-0001",
        },
        headers=headers,
    )
    assert order_response.status_code == 200
    order = order_response.json()["order"]
    assert order["base_points"] == 12_300
    assert order["promotion_bonus_points"] == 0
    assert order["total_points"] == 12_300


def test_topup_promotion_order_snapshot_survives_later_disable(tmp_path: Path, monkeypatch) -> None:
    client, database_path, headers = _client_and_headers(tmp_path, monkeypatch)
    set_topup_promotion_active(database_path, active=True, updated_by="test")
    promoted = client.post(
        "/api/customer/billing/topup-orders",
        json={
            "provider": "alipay",
            "package_id": "points_50",
            "idempotency_key": "promotion-snapshot-enabled-0001",
        },
        headers=headers,
    ).json()["order"]

    set_topup_promotion_active(database_path, active=False, updated_by="test")
    plain = client.post(
        "/api/customer/billing/topup-orders",
        json={
            "provider": "alipay",
            "package_id": "points_50",
            "idempotency_key": "promotion-snapshot-disabled-0001",
        },
        headers=headers,
    ).json()["order"]
    assert promoted["total_points"] == 10_000
    assert plain["total_points"] == 5_000

    first_settlement = settle_payment_order(
        database_path,
        provider="alipay",
        out_trade_no=promoted["out_trade_no"],
        gateway_transaction_id="trade_snapshot_enabled",
        amount_cents=promoted["amount_cents"],
        provider_status="TRADE_SUCCESS",
    )
    second_settlement = settle_payment_order(
        database_path,
        provider="alipay",
        out_trade_no=plain["out_trade_no"],
        gateway_transaction_id="trade_snapshot_disabled",
        amount_cents=plain["amount_cents"],
        provider_status="TRADE_SUCCESS",
    )
    assert first_settlement["already_paid"] is False
    assert second_settlement["already_paid"] is False

    summary = client.get("/api/customer/billing/summary", headers=headers).json()
    assert summary["wallet"]["available_points"] == 15_000
    assert summary["topup_promotion"]["active"] is False


def test_promoted_payment_callback_is_idempotent_and_writes_two_ledger_rows(tmp_path: Path, monkeypatch) -> None:
    client, database_path, headers = _client_and_headers(tmp_path, monkeypatch)
    set_topup_promotion_active(database_path, active=True, updated_by="test")
    order = client.post(
        "/api/customer/billing/topup-orders",
        json={
            "provider": "alipay",
            "package_id": "points_99",
            "idempotency_key": "promotion-idempotent-callback-0001",
        },
        headers=headers,
    ).json()["order"]

    settlement = {
        "provider": "alipay",
        "out_trade_no": order["out_trade_no"],
        "gateway_transaction_id": "trade_idempotent_promotion",
        "amount_cents": order["amount_cents"],
        "provider_status": "TRADE_SUCCESS",
    }
    assert settle_payment_order(database_path, **settlement)["already_paid"] is False
    assert settle_payment_order(database_path, **settlement)["already_paid"] is True

    with transaction(database_path) as conn:
        account_id = conn.execute("SELECT account_id FROM auth_accounts WHERE username = 'billing_user'").fetchone()[0]
        wallet = conn.execute(
            "SELECT points_balance FROM billing_wallets WHERE account_id = ?", (account_id,)
        ).fetchone()
        rows = conn.execute(
            """
            SELECT source_type, points_delta FROM billing_point_ledger
            WHERE account_id = ? AND source_id = ? ORDER BY source_type
            """,
            (account_id, order["order_id"]),
        ).fetchall()
    assert wallet["points_balance"] == 198_000
    assert [(row["source_type"], row["points_delta"]) for row in rows] == [
        ("payment_alipay", 99_000),
        ("topup_promotion_bonus", 99_000),
    ]


def test_promotion_cli_and_legacy_order_snapshot_migration(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    init_db(database_path)
    with transaction(database_path) as conn:
        conn.execute(
            """
            INSERT INTO auth_accounts (account_id, username, email, workspace_id)
            VALUES ('legacy-account', 'legacy-account', 'legacy-account@example.test', 'default')
            """
        )
        conn.execute(
            """
            INSERT INTO billing_payment_orders (
                order_id, out_trade_no, account_id, workspace_id, provider, package_id,
                amount_cents, points, status, idempotency_key, request_hash
            ) VALUES ('legacy-order', 'legacy-trade', 'legacy-account', 'default', 'alipay',
                      'points_10', 1000, 10000, 'paid', 'legacy-idempotency', 'legacy-hash')
            """
        )

    init_db(database_path)
    with transaction(database_path) as conn:
        migrated = conn.execute(
            """
            SELECT base_points, promotion_bonus_points, total_points, promotion_id
            FROM billing_payment_orders WHERE order_id = 'legacy-order'
            """
        ).fetchone()
    assert dict(migrated) == {
        "base_points": 10_000,
        "promotion_bonus_points": 0,
        "total_points": 10_000,
        "promotion_id": "",
    }

    script = Path(__file__).resolve().parents[1] / "manage_topup_promotion.py"
    for command, expected_active in (("status", False), ("enable", True), ("disable", False)):
        result = subprocess.run(
            [sys.executable, str(script), command, "--database", str(database_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        assert f'"active": {str(expected_active).lower()}' in result.stdout
    assert topup_promotion_status(database_path)["active"] is False
