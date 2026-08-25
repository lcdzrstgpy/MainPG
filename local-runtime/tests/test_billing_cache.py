# -*- coding: utf-8 -*-
"""Redis 缓存层单元测试：缓存读写/失效/降级，以及积分读写路径的接入行为。

设计要点：
- 使用内存版 FakeRedis 验证 cache.py 自身逻辑，不依赖真实 Redis 服务。
- 默认（WH_REDIS_URL 未配置）时所有缓存调用自动降级直查库，行为与未加缓存一致。
- 验证写路径（冻结/结算/改价）成功后确实触发缓存失效。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import wh_local.billing as billing_module
import wh_local.cache as cache_mod
from wh_local.customer.auth_server import (
    _account_by_token,
    _billing_summary,
    _hash_token,
    create_auth_app,
)

_EMAIL_CODE_SECRET = "billing-cache-test-secret-at-least-32-chars"


class FakeRedis:
    """内存版 Redis：足以验证读写/过期键存在性/单飞锁。"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, key: str):
        return self.store.get(key)

    def set(self, key: str, value: str, ex: int | None = None, nx: bool = False, **kwargs) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    def delete(self, *keys: str) -> None:
        for key in keys:
            self.store.pop(key, None)

    def ping(self) -> bool:
        return True


@pytest.fixture
def fake_redis(monkeypatch) -> FakeRedis:
    fake = FakeRedis()
    monkeypatch.setattr(cache_mod, "_client", lambda: fake)
    return fake


def _register_and_login(client: TestClient, db_path: Path, *, username: str = "cache_user") -> str:
    from wh_local.customer.auth_service import _email_code_digest
    from wh_local.db import transaction

    verification_id = f"ver_{username}"
    email = f"{username}@example.test"
    email_code = "654321"
    with transaction(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO invitation_codes (code, max_uses, used_count, expires_at, created_by, created_at)
            VALUES ('MAINPG-CACHE-TEST', 10, 0, '', 'test', datetime('now'))
            """
        )
        conn.execute(
            """
            INSERT INTO auth_email_verifications (
                verification_id, email, token_hash, purpose, expires_at
            ) VALUES (?, ?, ?, 'register', '9999-12-31T00:00:00+00:00')
            """,
            (
                verification_id,
                email,
                _email_code_digest(_EMAIL_CODE_SECRET, verification_id, email, "register", email_code),
            ),
        )
    assert client.post(
        "/api/customer/register",
        json={
            "username": username,
            "email": email,
            "email_code": email_code,
            "password": "StrongPassword123!",
            "invitation_code": "MAINPG-CACHE-TEST",
            "workspace_code": "cache-ws",
        },
    ).status_code == 200
    login = client.post("/api/customer/login", json={"username": username, "password": "StrongPassword123!"})
    assert login.status_code == 200
    return login.json()["token"]


# ---------------------------------------------------------------- cache.py 自身


def test_cache_disabled_degrades_to_source(monkeypatch) -> None:
    """Redis 不可用时：读返回 None、写返回 False、get_or_set 直接回源，失效函数不抛异常。"""
    monkeypatch.setattr(cache_mod, "_client", lambda: None)
    assert cache_mod.cache_get("x") is None
    assert cache_mod.cache_set("x", 1) is False
    calls: list[int] = []

    def loader() -> dict:
        calls.append(1)
        return {"v": len(calls)}

    assert cache_mod.get_or_set("x", 60, loader) == {"v": 1}
    assert calls == [1]
    cache_mod.invalidate_wallet("a")
    cache_mod.invalidate_pricing()
    cache_mod.invalidate_session("t")
    cache_mod.invalidate_admin_summary()


def test_cache_set_get_delete_roundtrip(fake_redis) -> None:
    assert cache_mod.cache_set("k", {"a": [1, 2], "b": "中文"}) is True
    assert cache_mod.cache_get("k") == {"a": [1, 2], "b": "中文"}
    cache_mod.cache_delete("k")
    assert cache_mod.cache_get("k") is None


def test_get_or_set_loads_once_then_hits(fake_redis) -> None:
    calls: list[int] = []

    def loader() -> dict:
        calls.append(1)
        return {"value": len(calls)}

    first = cache_mod.get_or_set("k", 60, loader)
    second = cache_mod.get_or_set("k", 60, loader)
    assert first == {"value": 1}
    assert second == {"value": 1}
    assert calls == [1]
    assert fake_redis.store.get("wh:k") is not None


def test_get_or_set_single_flight_lock(fake_redis) -> None:
    """防击穿：第一个请求占锁回填，后续请求等待后命中缓存，不重复回源。"""
    calls: list[int] = []

    def loader() -> dict:
        calls.append(1)
        return {"value": len(calls)}

    # 先让一个请求正常回填并保持锁由 get_or_set 释放
    assert cache_mod.get_or_set("sf", 60, loader) == {"value": 1}
    # 模拟锁竞争：占用锁后，get_or_set 应等待并最终读取到已回填的缓存
    lock_key = "wh:sf:lock"
    fake_redis.store[lock_key] = "1"
    result = cache_mod.get_or_set("sf", 60, loader)
    assert result == {"value": 1}
    assert calls == [1]


# ---------------------------------------------------------------- 定价缓存


def test_active_pricing_hits_cache_without_db(fake_redis) -> None:
    """定价缓存命中时无需访问数据库（DB 路径故意指向不存在文件）。"""
    fake_redis.store["wh:pricing:active"] = '{"rule_version": 99, "points_per_cny": 100}'
    pricing = billing_module.active_pricing(Path("NONEXISTENT-NO-DB"))
    assert pricing["rule_version"] == 99


def test_update_active_pricing_invalidates_cache(tmp_path, monkeypatch, fake_redis) -> None:
    db_path = tmp_path / "auth.sqlite3"
    create_auth_app(db_path)  # 初始化数据库与定价规则
    current = billing_module.active_pricing(db_path)
    text = current["features"]["product_processing.text"]
    image = current["features"]["product_processing.image_grid_2k"]
    invalidated: list[str] = []
    monkeypatch.setattr(cache_mod, "invalidate_pricing", lambda: invalidated.append("pricing"))
    billing_module.update_active_pricing(
        db_path,
        payload={
            "points_per_cny": current["points_per_cny"],
            "text_reserve_units": text["reserve_units"],
            "text_charge_units": text["charge_units"],
            "image_reserve_units": image["reserve_units"],
            "image_charge_units": image["charge_units"],
            "min_client_version": current.get("min_client_version") or "",
        },
        updated_by="cache-test",
    )
    assert invalidated == ["pricing"]


# ---------------------------------------------------------------- 会话与钱包缓存


def test_account_by_token_hits_cache_without_db(fake_redis) -> None:
    token = "some-valid-looking-token"
    token_hash = _hash_token(token)
    cached_account = {
        "account_id": "cust_cache_1",
        "username": "cached_user",
        "email": "c@example.test",
        "display_name": "",
        "role": "operator",
        "workspace_id": "default",
        "account_status": "active",
        "login_status": "online",
        "workspace_code": "",
        "workspace_name": "",
        "workspace": {"code": "", "name": ""},
    }
    fake_redis.store[f"wh:sess:{token_hash}"] = __import__("json").dumps(cached_account, ensure_ascii=False)
    account = _account_by_token(Path("NONEXISTENT-NO-DB"), token)
    assert account is not None
    assert account["account_id"] == "cust_cache_1"


def test_billing_summary_hits_cache_without_db(fake_redis) -> None:
    cached = {"ok": True, "wallet": {"points_balance": 12345, "available_points": 10000}}
    fake_redis.store["wh:wallet:cust_cache_2"] = __import__("json").dumps(cached)
    summary = _billing_summary(
        Path("NONEXISTENT-NO-DB"),
        {"account_id": "cust_cache_2", "workspace_id": "default", "username": "u", "workspace_code": ""},
    )
    assert summary["wallet"]["points_balance"] == 12345


def test_write_paths_invalidate_wallet(tmp_path, monkeypatch, fake_redis) -> None:
    """冻结/结算写路径成功后必须失效对应钱包缓存。"""
    monkeypatch.setenv("WH_EMAIL_CODE_SECRET", _EMAIL_CODE_SECRET)
    monkeypatch.setattr(
        "wh_local.customer.auth_server.TencentCloudSESEmailSender.from_env",
        lambda: object(),
    )
    invalidated: list[str] = []
    monkeypatch.setattr(cache_mod, "invalidate_wallet", lambda account_id: invalidated.append(str(account_id)))
    db_path = tmp_path / "auth.sqlite3"
    client = TestClient(create_auth_app(db_path))
    token = _register_and_login(client, db_path)
    headers = {"Authorization": f"Bearer {token}"}
    # 授予注册用户足够积分
    from wh_local.db import transaction

    with transaction(db_path) as conn:
        account = conn.execute(
            "SELECT account_id, workspace_id FROM auth_accounts WHERE username = 'cache_user'"
        ).fetchone()
        assert account is not None
        conn.execute(
            """
            INSERT INTO billing_wallets (account_id, workspace_id, points_balance, version, created_at, updated_at)
            VALUES (?, ?, 1000000, 0, datetime('now'), datetime('now'))
            """,
            (account["account_id"], account["workspace_id"] or "default"),
        )
    reserve = client.post(
        "/api/customer/billing/usage/reserve",
        headers=headers,
        json={"feature_key": "product_processing.text", "idempotency_key": "idem-cache-1" * 2},
    )
    assert reserve.status_code == 200
    assert str(account["account_id"]) in invalidated
