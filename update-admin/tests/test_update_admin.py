from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module  # noqa: E402
from app import (  # noqa: E402
    MANIFEST_FIELDS,
    Settings,
    canonical_manifest_bytes,
    canonical_patch_manifest_bytes,
    create_app,
)


@pytest.fixture()
def test_context(tmp_path: Path):
    private_key = Ed25519PrivateKey.generate()
    private_key_path = tmp_path / "release-key.pem"
    private_key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    public_key_b64 = base64.b64encode(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    settings = Settings(
        db_path=tmp_path / "admin.sqlite3",
        staging_dir=tmp_path / "staging",
        publish_dir=tmp_path / "published",
        public_base_url="https://localhost/downloads",
        signing_key_path=private_key_path,
        expected_public_key_b64=public_key_b64,
        initial_password="123456",
        boss_initial_password="Boss-Test-Password!",
        secure_cookie=False,
        require_authenticode=False,
    )
    hasher = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1)
    app = create_app(settings, password_hasher=hasher)
    with TestClient(app) as client:
        yield client, settings, private_key


def login(client: TestClient, username: str, password: str):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_seeded_accounts_and_first_login_password_gate(test_context):
    client, _, _ = test_context

    boss = login(client, "boss", "Boss-Test-Password!")
    assert boss.status_code == 200
    assert boss.json()["user"]["must_change_password"] is False
    assert client.get("/api/releases").status_code == 200
    client.post("/api/auth/logout", json={})

    for username in ("He123", "Liu123", "Dai123", "Yang123", "Shen123"):
        response = login(client, username, "123456")
        assert response.status_code == 200
        assert response.json()["user"]["must_change_password"] is True
        forbidden = client.get("/api/releases")
        assert forbidden.status_code == 403
        assert forbidden.json()["detail"]["code"] == "password_change_required"
        client.post("/api/auth/logout", json={})


def test_password_change_invalidates_existing_session(test_context):
    client, _, _ = test_context
    assert login(client, "He123", "123456").status_code == 200

    changed = client.post(
        "/api/auth/change-password",
        json={"current_password": "123456", "new_password": "A-new-safe-password-2026"},
    )
    assert changed.status_code == 200
    assert client.get("/api/auth/me").status_code == 401

    relogin = login(client, "He123", "A-new-safe-password-2026")
    assert relogin.status_code == 200
    assert relogin.json()["user"]["must_change_password"] is False


def test_failed_logins_are_audited_and_temporarily_lock_account(test_context):
    client, _, _ = test_context
    for _ in range(5):
        response = login(client, "He123", "wrong-password")
        assert response.status_code == 401
    locked = login(client, "He123", "123456")
    assert locked.status_code == 429
    assert locked.json()["detail"]["code"] == "account_locked"


def test_publish_raw_exe_creates_signed_atomic_manifest(test_context):
    client, settings, private_key = test_context
    assert login(client, "boss", "Boss-Test-Password!").status_code == 200
    payload = b"MZ" + (b"internal-installer" * 128)

    published = client.post(
        "/api/releases/publish",
        data={
            "version": "1.3.4-beta.1",
            "mandatory": "false",
            "release_notes": "修复公告同步\n增加定时更新检查",
        },
        files={"installer": ("colleague-build.exe", payload, "application/octet-stream")},
    )
    assert published.status_code == 200, published.text
    result = published.json()
    assert result["release"]["version"] == "1.3.4-beta.1"
    assert result["release"]["sha256"] == hashlib.sha256(payload).hexdigest()

    installer_path = settings.publish_dir / "MainPG-Setup-1.3.4-beta.1.exe"
    manifest_path = settings.publish_dir / "manifest.json"
    history_manifest_path = settings.publish_dir / "releases" / "1.3.4-beta.1" / "manifest.json"
    assert installer_path.read_bytes() == payload
    assert history_manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert set(MANIFEST_FIELDS).issubset(manifest)
    private_key.public_key().verify(
        base64.b64decode(manifest["signature"]),
        canonical_manifest_bytes(manifest),
    )

    # Cross-check with the actual desktop client validator, not only the
    # publisher's own canonical serializer.
    runtime_path = Path(__file__).resolve().parents[2] / "local-runtime"
    sys.path.insert(0, str(runtime_path))
    from wh_local.app_update import UpdateManager, UpdateSettings  # noqa: PLC0415

    manager = UpdateManager(
        UpdateSettings(
            current_version="1.3.3",
            manifest_url="https://localhost/downloads/manifest.json",
            allowed_hosts=frozenset({"localhost"}),
            public_key_b64=settings.expected_public_key_b64,
            runtime_root=settings.publish_dir,
            platform="win32",
        ),
        manifest_fetcher=lambda _: manifest,
    )
    assert manager.check()["state"] == "available"

    releases = client.get("/api/releases").json()["items"]
    assert releases[0]["version"] == "1.3.4-beta.1"
    durable_status = client.get("/api/releases/status/1.3.4-beta.1")
    assert durable_status.status_code == 200
    assert durable_status.json()["published"] is True
    assert durable_status.json()["release"]["sha256"] == hashlib.sha256(payload).hexdigest()
    missing_status = client.get("/api/releases/status/1.3.4-beta.2")
    assert missing_status.status_code == 200
    assert missing_status.json()["published"] is False
    assert missing_status.json()["release"] is None
    audits = client.get("/api/audit-logs").json()["items"]
    assert any(item["action"] == "release_published" for item in audits)


def test_evsign_runs_before_final_hash_and_atomic_publish(tmp_path: Path, monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    private_key_path = tmp_path / "evsign-release-key.pem"
    private_key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    public_key_b64 = base64.b64encode(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    settings = Settings(
        db_path=tmp_path / "evsign-admin.sqlite3",
        staging_dir=tmp_path / "evsign-staging",
        publish_dir=tmp_path / "evsign-published",
        public_base_url="https://localhost/downloads",
        signing_key_path=private_key_path,
        expected_public_key_b64=public_key_b64,
        initial_password="123456",
        boss_initial_password="Boss-Test-Password!",
        secure_cookie=False,
        evsign_license_key="test-license-key",
        evsign_required=True,
        require_authenticode=True,
    )
    signed_suffix = b"AUTHENTICODE-SIGNED"

    def fake_sign(path: Path, filename: str, resolved: Settings):
        assert filename == "unsigned-build.exe"
        signed = resolved.staging_dir / "fake-signed.part"
        signed.write_bytes(path.read_bytes() + signed_suffix)
        return signed, {"status": "signed", "message": "test signer"}

    monkeypatch.setattr(app_module, "sign_with_evsign", fake_sign)
    monkeypatch.setattr(
        app_module,
        "verify_authenticode",
        lambda path, resolved: {"status": "Valid", "subject": "CN=Test Publisher", "message": "ok"},
    )
    app = create_app(
        settings,
        password_hasher=PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1),
    )
    payload = b"MZ" + b"unsigned-installer" * 64
    with TestClient(app) as client:
        assert login(client, "boss", "Boss-Test-Password!").status_code == 200
        response = client.post(
            "/api/releases/publish",
            data={"version": "8.0.0", "mandatory": "false", "release_notes": "signed"},
            files={"installer": ("unsigned-build.exe", payload, "application/octet-stream")},
        )
    assert response.status_code == 200, response.text
    signed_payload = payload + signed_suffix
    result = response.json()
    assert result["evsign"]["status"] == "signed"
    assert result["authenticode"]["status"] == "Valid"
    assert result["release"]["sha256"] == hashlib.sha256(signed_payload).hexdigest()
    assert (settings.publish_dir / "MainPG-Setup-8.0.0.exe").read_bytes() == signed_payload


def test_second_release_automatically_publishes_signed_incremental_patch(test_context):
    client, settings, private_key = test_context
    assert login(client, "boss", "Boss-Test-Password!").status_code == 200

    first = client.post(
        "/api/releases/publish",
        data={"version": "2.0.0-beta.1", "release_notes": "first"},
        files={"installer": ("first.exe", b"MZ-first-installer", "application/octet-stream")},
    )
    assert first.status_code == 200, first.text

    def make_bundle(root: Path, bundle_version: str):
        (root / "_internal").mkdir(parents=True)
        (root / "MainPG.exe").write_bytes(f"main-{bundle_version}".encode())
        (root / "MainPG-Updater.exe").write_bytes(b"stable-updater")
        (root / "version.json").write_text(json.dumps({"version": bundle_version}), encoding="utf-8")
        (root / "_internal" / "unchanged.dat").write_bytes(b"same")
        if bundle_version.endswith(".1"):
            (root / "_internal" / "removed.dat").write_bytes(b"remove-me")
        else:
            (root / "_internal" / "added.dat").write_bytes(b"new-file")

    old_bundle = settings.staging_dir.parent / "old-bundle"
    new_bundle = settings.staging_dir.parent / "new-bundle"
    make_bundle(old_bundle, "2.0.0-beta.1")
    make_bundle(new_bundle, "2.0.0-beta.2")
    embedded_installer = settings.staging_dir.parent / "second-with-patch.exe"
    embedded_installer.write_bytes(b"MZ-second-installer")
    from wh_local.runtime.embedded_patch_builder import append_embedded_patch  # noqa: PLC0415

    append_embedded_patch(
        installer=embedded_installer,
        from_dir=old_bundle,
        to_dir=new_bundle,
        from_version="2.0.0-beta.1",
        to_version="2.0.0-beta.2",
    )
    second = client.post(
        "/api/releases/publish",
        data={"version": "2.0.0-beta.2", "release_notes": "small update"},
        files={"installer": ("second.exe", embedded_installer.read_bytes(), "application/octet-stream")},
    )
    assert second.status_code == 200, second.text
    result = second.json()
    assert result["patch"]["status"] == "published"
    assert result["patch"]["from_version"] == "2.0.0-beta.1"
    assert result["patch"]["file_count"] == 3
    assert result["patch"]["total_bytes"] > 0

    patch_manifest = json.loads((settings.publish_dir / "patch-manifest.json").read_text(encoding="utf-8"))
    assert patch_manifest["from_version"] == "2.0.0-beta.1"
    assert patch_manifest["to_version"] == "2.0.0-beta.2"
    private_key.public_key().verify(
        base64.b64decode(patch_manifest["signature"]),
        canonical_patch_manifest_bytes(patch_manifest),
    )
    entries = {entry["path"]: entry for entry in patch_manifest["files"]}
    assert entries["MainPG.exe"]["action"] == "replace"
    assert entries["_internal/added.dat"]["action"] == "add"
    assert entries["_internal/removed.dat"]["action"] == "delete"
    assert "MainPG-Updater.exe" not in entries
    assert "version.json" not in entries
    assert (settings.publish_dir / "patch" / "2.0.0-beta.2" / "_internal" / "added.dat").is_file()

    release = client.get("/api/releases/status/2.0.0-beta.2").json()["release"]
    assert release["patch_status"] == "published"
    assert release["patch_file_count"] == 3


def test_patch_failure_keeps_full_installer_release_available(test_context, monkeypatch):
    client, settings, _ = test_context
    assert login(client, "boss", "Boss-Test-Password!").status_code == 200
    first = client.post(
        "/api/releases/publish",
        data={"version": "3.0.0", "release_notes": "first"},
        files={"installer": ("first.exe", b"MZ-first", "application/octet-stream")},
    )
    assert first.status_code == 200, first.text

    monkeypatch.setattr(
        app_module,
        "extract_installer_bundle",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("extract failed")),
    )
    second = client.post(
        "/api/releases/publish",
        data={"version": "3.0.1", "release_notes": "fallback"},
        files={"installer": ("second.exe", b"MZ-second", "application/octet-stream")},
    )
    assert second.status_code == 200, second.text
    result = second.json()
    assert result["patch"]["status"] == "failed"
    assert (settings.publish_dir / "MainPG-Setup-3.0.1.exe").is_file()
    assert json.loads((settings.publish_dir / "manifest.json").read_text(encoding="utf-8"))["version"] == "3.0.1"


def test_rejects_duplicate_or_lower_version_before_replacing_manifest(test_context):
    client, settings, _ = test_context
    assert login(client, "boss", "Boss-Test-Password!").status_code == 200
    payload = b"MZ" + b"x" * 100
    first = client.post(
        "/api/releases/publish",
        data={"version": "2.0.0", "mandatory": "true", "release_notes": "first"},
        files={"installer": ("first.exe", payload, "application/octet-stream")},
    )
    assert first.status_code == 200
    original_manifest = (settings.publish_dir / "manifest.json").read_bytes()

    rejected = client.post(
        "/api/releases/publish",
        data={"version": "1.9.9", "mandatory": "false", "release_notes": "old"},
        files={"installer": ("old.exe", payload, "application/octet-stream")},
    )
    assert rejected.status_code == 409
    assert (settings.publish_dir / "manifest.json").read_bytes() == original_manifest


def test_existing_live_manifest_prevents_downgrade_with_empty_database(test_context):
    client, settings, _ = test_context
    assert login(client, "boss", "Boss-Test-Password!").status_code == 200
    settings.publish_dir.mkdir(parents=True, exist_ok=True)
    (settings.publish_dir / "manifest.json").write_text(
        json.dumps({"version": "5.0.0"}),
        encoding="utf-8",
    )
    rejected = client.post(
        "/api/releases/publish",
        data={"version": "4.9.9", "mandatory": "false", "release_notes": "old"},
        files={"installer": ("old.exe", b"MZ" + b"x" * 100, "application/octet-stream")},
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "version_not_newer"


def test_rejects_non_executable_and_wrong_signing_key(test_context, tmp_path: Path):
    client, settings, _ = test_context
    assert login(client, "boss", "Boss-Test-Password!").status_code == 200
    invalid = client.post(
        "/api/releases/publish",
        data={"version": "1.0.0", "mandatory": "false", "release_notes": ""},
        files={"installer": ("not-exe.exe", b"not a pe file", "application/octet-stream")},
    )
    assert invalid.status_code == 422

    other_key = Ed25519PrivateKey.generate()
    settings.signing_key_path.write_bytes(
        other_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    mismatch = client.post(
        "/api/releases/publish",
        data={"version": "1.0.0", "mandatory": "false", "release_notes": ""},
        files={"installer": ("valid.exe", b"MZ" + b"x" * 100, "application/octet-stream")},
    )
    assert mismatch.status_code == 503
    assert mismatch.json()["detail"]["code"] == "signing_key_mismatch"
