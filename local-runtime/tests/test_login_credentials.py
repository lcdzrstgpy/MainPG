# -*- coding: utf-8 -*-
"""登录页「账号管理」本地凭据存储的单元测试。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from wh_local import login_credentials
from wh_local.login_credentials import LoginCredentialStore, create_router


def _store(tmp_path: Path, **kwargs) -> LoginCredentialStore:
    return LoginCredentialStore(tmp_path / "login-credentials.bin", **kwargs)


def _client(store: LoginCredentialStore) -> TestClient:
    app = FastAPI()
    app.include_router(create_router(store))
    return TestClient(app)


def test_save_and_reveal_round_trip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save("alex@example.com", password="secret-123", remember=True)

    entries = store.list_entries()

    assert [item["identifier"] for item in entries] == ["alex@example.com"]
    assert entries[0]["has_password"] is True
    assert store.reveal("alex@example.com") == "secret-123"


def test_file_on_disk_is_encrypted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save("alex@example.com", password="topsecret-value", remember=True)

    raw = (tmp_path / "login-credentials.bin").read_bytes()

    # DPAPI 密文里既不应出现密码，也不应出现账号明文。
    assert b"topsecret-value" not in raw
    assert b"alex@example.com" not in raw


def test_remember_false_keeps_account_without_password(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save("alex@example.com", password="secret-123", remember=False)

    entries = store.list_entries()

    assert [item["identifier"] for item in entries] == ["alex@example.com"]
    assert entries[0]["has_password"] is False
    assert store.reveal("alex@example.com") is None


def test_unchecked_remember_clears_previously_saved_password(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save("alex@example.com", password="old-secret", remember=True)

    store.save("alex@example.com", password="new-secret", remember=False)

    assert store.reveal("alex@example.com") is None
    assert store.list_entries()[0]["has_password"] is False


def test_save_updates_password_and_moves_account_to_front(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save("first@example.com", password="one", remember=True)
    store.save("second@example.com", password="two", remember=True)

    store.save("first@example.com", password="one-updated", remember=True)

    entries = store.list_entries()
    assert [item["identifier"] for item in entries] == [
        "first@example.com",
        "second@example.com",
    ]
    assert store.reveal("first@example.com") == "one-updated"


def test_remember_true_without_password_keeps_existing_password(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save("alex@example.com", password="secret-123", remember=True)

    store.save("alex@example.com", password=None, remember=True)

    assert store.reveal("alex@example.com") == "secret-123"


def test_delete_account(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save("alex@example.com", password="secret", remember=True)

    assert store.delete("alex@example.com") is True
    assert store.list_entries() == []
    assert store.delete("alex@example.com") is False


def test_account_limit_evicts_oldest(tmp_path: Path) -> None:
    store = _store(tmp_path, max_accounts=3)
    for index in range(4):
        store.save(f"user{index}@example.com", password=f"p{index}", remember=True)

    identifiers = [item["identifier"] for item in store.list_entries()]

    assert len(identifiers) == 3
    assert "user0@example.com" not in identifiers
    # 最近登录的排最前。
    assert identifiers[0] == "user3@example.com"


def test_clear_password_keeps_account(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save("alex@example.com", password="secret", remember=True)

    assert store.clear_password("alex@example.com") is True
    assert store.list_entries()[0]["has_password"] is False
    assert store.clear_password("alex@example.com") is False


def test_corrupt_file_is_quarantined_and_recovered(tmp_path: Path) -> None:
    path = tmp_path / "login-credentials.bin"
    path.write_bytes(b"not-a-dpapi-blob")

    store = _store(tmp_path)

    assert store.list_entries() == []
    assert list(tmp_path.glob("login-credentials.bin.corrupt-*"))
    # 自愈后仍可正常写入。
    store.save("alex@example.com", password="secret", remember=True)
    assert store.reveal("alex@example.com") == "secret"


def test_identifier_is_trimmed_and_password_keeps_spaces(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save("  alex@example.com  ", password="  pad  ", remember=True)

    assert store.list_entries()[0]["identifier"] == "alex@example.com"
    # 密码必须原样保存（可能含首尾空格）。
    assert store.reveal("alex@example.com") == "  pad  "


def test_router_round_trip(tmp_path: Path) -> None:
    client = _client(_store(tmp_path))

    saved = client.post(
        "/api/login-credentials",
        json={"identifier": "alex@example.com", "password": "secret-1", "remember": True},
    )
    assert saved.status_code == 200

    listing = client.get("/api/login-credentials").json()
    assert listing["ok"] is True
    assert listing["accounts"][0]["has_password"] is True
    assert "password" not in listing["accounts"][0]

    revealed = client.post(
        "/api/login-credentials/reveal", json={"identifier": "alex@example.com"}
    )
    assert revealed.json()["password"] == "secret-1"

    cleared = client.post(
        "/api/login-credentials/clear-password",
        json={"identifier": "alex@example.com"},
    )
    assert cleared.json()["cleared"] is True

    removed = client.delete("/api/login-credentials/alex@example.com")
    assert removed.json()["removed"] is True
    assert client.get("/api/login-credentials").json()["accounts"] == []


def test_router_save_returns_503_when_dpapi_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(login_credentials, "_IS_WINDOWS", False)
    client = _client(_store(tmp_path))

    response = client.post(
        "/api/login-credentials",
        json={"identifier": "alex@example.com", "password": "secret", "remember": True},
    )

    assert response.status_code == 503
