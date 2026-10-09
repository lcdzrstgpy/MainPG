from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wh_local.billing import settle_payment_order, topup_promotion_status
from wh_local.customer.auth_server import create_auth_app
from wh_local.db import transaction

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
    ("package_id", "amount_cents", "base_points", "bonus_percent", "bonus_points", "total_points"),
    [
        ("points_49", 4_900, 4_900, 25, 1_225, 6_125),
        ("points_99", 9_900, 9_900, 50, 4_950, 14_850),
        ("points_499", 49_900, 49_900, 75, 37_425, 87_325),
        ("points_999", 99_900, 99_900, 100, 99_900, 199_800),
    ],
)
def test_fixed_topup_tiers_snapshot_their_permanent_bonus(
    tmp_path: Path,
    monkeypatch,
    package_id: str,
    amount_cents: int,
    base_points: int,
    bonus_percent: int,
    bonus_points: int,
    total_points: int,
) -> None:
    client, _, headers = _client_and_headers(tmp_path, monkeypatch)
    response = client.post(
        "/api/customer/billing/topup-orders",
        json={
            "provider": "alipay",
            "package_id": package_id,
            "idempotency_key": f"tiered-topup-{package_id}-0001",
        },
        headers=headers,
    )

    assert response.status_code == 200
    order = response.json()["order"]
    assert order["amount_cents"] == amount_cents
    assert order["points"] == base_points
    assert order["base_points"] == base_points
    assert order["promotion_bonus_percent"] == bonus_percent
    assert order["promotion_bonus_points"] == bonus_points
    assert order["total_points"] == total_points
    assert order["promotion_id"] == "fixed_package_tiered_bonus"


def test_custom_topup_is_quoted_and_created_without_a_bonus(tmp_path: Path, monkeypatch) -> None:
    client, _, headers = _client_and_headers(tmp_path, monkeypatch)
    quote = client.post(
        "/api/customer/billing/topup-quote",
        json={"amount_cents": 12_300},
        headers=headers,
    )
    assert quote.status_code == 200
    assert quote.json()["product"] == {
        "package_id": "custom",
        "label": "自定义积分充值",
        "amount_cents": 12_300,
        "points": 12_300,
        "base_points": 12_300,
        "promotion_bonus_points": 0,
        "promotion_bonus_percent": 0,
        "total_points": 12_300,
        "promotion_id": "",
        "promotion_name": "",
    }

    order = client.post(
        "/api/customer/billing/topup-orders",
        json={
            "provider": "alipay",
            "package_id": "custom",
            "amount_cents": 12_300,
            "idempotency_key": "tiered-topup-custom-0001",
        },
        headers=headers,
    )
    assert order.status_code == 200
    assert order.json()["order"]["promotion_bonus_points"] == 0
    assert order.json()["order"]["total_points"] == 12_300


@pytest.mark.parametrize("package_id", ("points_50", "points_199", "points_4999"))
def test_retired_fixed_packages_cannot_create_new_orders(tmp_path: Path, monkeypatch, package_id: str) -> None:
    client, _, headers = _client_and_headers(tmp_path, monkeypatch)
    response = client.post(
        "/api/customer/billing/topup-orders",
        json={
            "provider": "alipay",
            "package_id": package_id,
            "idempotency_key": f"retired-package-{package_id}-0001",
        },
        headers=headers,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "unknown topup package"


def test_successful_999_topup_is_idempotent_and_writes_two_ledger_rows(tmp_path: Path, monkeypatch) -> None:
    client, database_path, headers = _client_and_headers(tmp_path, monkeypatch)
    order = client.post(
        "/api/customer/billing/topup-orders",
        json={
            "provider": "alipay",
            "package_id": "points_999",
            "idempotency_key": "tiered-topup-idempotent-0001",
        },
        headers=headers,
    ).json()["order"]
    settlement = {
        "provider": "alipay",
        "out_trade_no": order["out_trade_no"],
        "gateway_transaction_id": "trade_tiered_idempotent",
        "amount_cents": order["amount_cents"],
        "provider_status": "TRADE_SUCCESS",
    }

    assert settle_payment_order(database_path, **settlement)["already_paid"] is False
    assert settle_payment_order(database_path, **settlement)["already_paid"] is True

    with transaction(database_path) as conn:
        account_id = conn.execute(
            "SELECT account_id FROM auth_accounts WHERE username = 'billing_user'"
        ).fetchone()[0]
        rows = conn.execute(
            """
            SELECT source_type, points_delta FROM billing_point_ledger
            WHERE account_id = ? AND source_id = ? ORDER BY source_type
            """,
            (account_id, order["order_id"]),
        ).fetchall()
    assert [(row["source_type"], row["points_delta"]) for row in rows] == [
        ("payment_alipay", 999_000),
        ("topup_promotion_bonus", 999_000),
    ]


def test_historical_4999_pending_order_keeps_its_double_snapshot(tmp_path: Path, monkeypatch) -> None:
    client, database_path, headers = _client_and_headers(tmp_path, monkeypatch)
    with transaction(database_path) as conn:
        account = conn.execute(
            "SELECT account_id, workspace_id FROM auth_accounts WHERE username = 'billing_user'"
        ).fetchone()
        conn.execute(
            """
            INSERT INTO billing_payment_orders (
                order_id, out_trade_no, account_id, workspace_id, provider, package_id,
                amount_cents, currency, points, base_points, promotion_bonus_points,
                total_points, promotion_id, promotion_name, status, idempotency_key,
                request_hash, expires_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'alipay', 'points_4999', 499900, 'CNY', ?, ?, ?, ?,
                      'topup_double', '充值积分翻倍活动', 'pending', ?, ?,
                      '9999-12-31T00:00:00+00:00', '2026-09-01T00:00:00+00:00',
                      '2026-09-01T00:00:00+00:00')
            """,
            (
                "legacy-4999-order",
                "legacy-4999-trade",
                account["account_id"],
                account["workspace_id"],
                4_999_000,
                4_999_000,
                4_999_000,
                9_998_000,
                "legacy-4999-idempotency",
                "legacy-4999-hash",
            ),
        )

    settled = settle_payment_order(
        database_path,
        provider="alipay",
        out_trade_no="legacy-4999-trade",
        gateway_transaction_id="trade_legacy_4999",
        amount_cents=499_900,
        provider_status="TRADE_SUCCESS",
    )
    assert settled["already_paid"] is False

    summary = client.get("/api/customer/billing/summary", headers=headers).json()
    legacy_order = next(order for order in summary["recent_orders"] if order["order_id"] == "legacy-4999-order")
    assert legacy_order["promotion_bonus_percent"] == 100
    assert legacy_order["promotion_bonus_points"] == 499_900
    assert legacy_order["total_points"] == 999_800


def test_permanent_tiered_bonus_status_lists_the_four_current_packages() -> None:
    assert topup_promotion_status() == {
        "active": True,
        "name": "固定套餐档位递增赠送（25%~100%）",
        "bonus_rate_percent": 100,
        "tiers": [
            {"package_id": "points_49", "bonus_rate_percent": 25},
            {"package_id": "points_99", "bonus_rate_percent": 50},
            {"package_id": "points_499", "bonus_rate_percent": 75},
            {"package_id": "points_999", "bonus_rate_percent": 100},
        ],
        "applies_to": "fixed_packages",
    }
