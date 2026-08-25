import base64
import hashlib
import io
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wh_local.config import default_config


def test_runtime_version_is_release_owned() -> None:
    assert default_config().app_version == "1.3.0"  # APP_VERSION in wh_local/config.py


def test_default_update_manifest_uses_the_official_https_release_host() -> None:
    from wh_local.config import (
        UPDATE_MANIFEST_ALLOWED_HOSTS,
        UPDATE_MANIFEST_URL,
        UPDATE_RELEASE_HOST,
    )

    parsed = urlparse(UPDATE_MANIFEST_URL)

    assert UPDATE_RELEASE_HOST == "workbench.haocoming.top"
    assert UPDATE_MANIFEST_URL == (
        "https://workbench.haocoming.top/mainpg/windows/manifest.json"
    )
    assert parsed.scheme == "https"
    assert parsed.hostname == UPDATE_RELEASE_HOST
    assert parsed.hostname in UPDATE_MANIFEST_ALLOWED_HOSTS
    assert not parsed.hostname.endswith(".invalid")


def test_runtime_config_exposes_the_root_used_for_updates(tmp_path: Path) -> None:
    import wh_local.config as config_module

    config = default_config(tmp_path)

    root_accessor = getattr(config_module, "runtime_root", None)
    assert root_accessor is not None
    assert root_accessor(tmp_path) == tmp_path
    assert getattr(config, "runtime_root", None) == tmp_path
    assert config.data_dir == tmp_path / "outputs" / "wh-local"


def test_frozen_runtime_root_is_appdata_mainpg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import wh_local.config as config_module

    monkeypatch.setattr(config_module.sys, "frozen", True, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("WH_LOCAL_DATA_DIR", raising=False)

    config = default_config()

    assert config.runtime_root == tmp_path / "MainPG"
    assert config.data_dir == tmp_path / "MainPG" / "outputs" / "wh-local"


def _signed_manifest(private_key: Ed25519PrivateKey, **overrides: object) -> dict[str, object]:
    from wh_local.app_update import canonical_manifest_payload

    manifest: dict[str, object] = {
        "version": "1.2.0",
        "mandatory": False,
        "installer_url": "https://updates.example.invalid/MainPG-1.2.0.exe",
        "sha256": hashlib.sha256(b"installer").hexdigest(),
        "release_notes": "A safer updater.",
        "published_at": "2026-08-17T00:00:00Z",
    }
    manifest.update(overrides)
    manifest["signature"] = base64.b64encode(
        private_key.sign(canonical_manifest_payload(manifest))
    ).decode("ascii")
    return manifest


def test_updater_canonical_payload_matches_release_manifest_signer() -> None:
    from wh_local.app_update import canonical_manifest_payload
    from wh_local.runtime.release_manifest import canonical_manifest_bytes

    manifest = {
        "version": "1.2.0",
        "mandatory": False,
        "installer_url": "https://updates.example.invalid/MainPG-1.2.0.exe",
        "sha256": "a" * 64,
        "release_notes": "Unicode stays UTF-8: 安全更新",
        "published_at": "2026-08-17T00:00:00Z",
    }

    assert canonical_manifest_payload(manifest) == canonical_manifest_bytes(manifest)


def _manager(tmp_path: Path, platform: str = "win32"):
    from wh_local.app_update import UpdateManager, UpdateSettings

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes_raw()
    settings = UpdateSettings(
        current_version="1.1.0",
        manifest_url="https://updates.example.invalid/mainpg/windows/manifest.json",
        allowed_hosts=frozenset({"updates.example.invalid"}),
        public_key_b64=base64.b64encode(public_key).decode("ascii"),
        runtime_root=tmp_path,
        platform=platform,
    )
    return UpdateManager(settings), private_key


@pytest.mark.parametrize(
    "value",
    [
        "1",
        "01.2.3",
        "1.2",
        "1.2.3.4",
        "1.2.03",
        "v1.2.3",
        "1.2.3-01",
        "1.2.3-alpha.01",
        "1.2٣.4",
    ],
)
def test_semantic_versions_reject_invalid_values(value: str) -> None:
    from wh_local.app_update import SemanticVersion

    with pytest.raises(ValueError):
        SemanticVersion.parse(value)


def test_semantic_versions_order_prereleases_before_final_release() -> None:
    from wh_local.app_update import SemanticVersion

    assert SemanticVersion.parse("1.2.3-alpha.1") < SemanticVersion.parse("1.2.3")
    assert SemanticVersion.parse("1.10.0") > SemanticVersion.parse("1.2.99")


def test_check_accepts_a_valid_signed_manifest(tmp_path: Path) -> None:
    manager, private_key = _manager(tmp_path)
    manifest = _signed_manifest(private_key)
    manager = manager.with_manifest_fetcher(lambda _url: manifest)

    assert manager.check()["state"] == "available"
    assert manager.status()["release"]["version"] == "1.2.0"


def test_failed_refresh_preserves_previously_verified_mandatory_release(tmp_path: Path) -> None:
    manager, private_key = _manager(tmp_path)
    manifest = _signed_manifest(private_key, mandatory=True)
    checks = 0

    def fetch(_url: str) -> dict[str, object]:
        nonlocal checks
        checks += 1
        if checks == 1:
            return manifest
        raise OSError("release service temporarily unavailable")

    manager.with_manifest_fetcher(fetch)
    available = manager.check()
    failed = manager.check()

    assert available["state"] == "available"
    assert available["release"]["mandatory"] is True
    assert failed["state"] == "failed"
    assert failed["release"] == available["release"]
    assert failed["release"]["mandatory"] is True


def test_status_response_contract_is_stable(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path)

    status = manager.status()

    assert set(status) == {"state", "current_version", "release", "progress", "error"}
    assert status == {
        "state": "idle",
        "current_version": "1.1.0",
        "release": None,
        "progress": None,
        "error": None,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"mandatory": "false"},
        {"published_at": "not-a-date"},
        {"sha256": "not-a-sha"},
        {"installer_url": "http://updates.example.invalid/MainPG.exe"},
        {"installer_url": "https://untrusted.example/MainPG.exe"},
    ],
)
def test_check_rejects_malformed_or_unsafe_manifests(tmp_path: Path, overrides: dict[str, object]) -> None:
    manager, private_key = _manager(tmp_path)
    manifest = _signed_manifest(private_key, **overrides)
    manager = manager.with_manifest_fetcher(lambda _url: manifest)

    status = manager.check()

    assert status["state"] == "failed"
    assert status["error"]


def test_check_rejects_an_invalid_signature(tmp_path: Path) -> None:
    manager, private_key = _manager(tmp_path)
    manifest = _signed_manifest(private_key, release_notes="tampered after signing")
    manifest["signature"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=="
    manager = manager.with_manifest_fetcher(lambda _url: manifest)

    assert manager.check()["state"] == "failed"


def test_check_rejects_unknown_manifest_fields_even_when_known_fields_are_signed(tmp_path: Path) -> None:
    manager, private_key = _manager(tmp_path)
    manifest = _signed_manifest(private_key, unsigned_extension="not covered by the signature")
    manager = manager.with_manifest_fetcher(lambda _url: manifest)

    status = manager.check()

    assert status["state"] == "failed"
    assert "fields" in str(status["error"]).lower()


@pytest.mark.parametrize("version", ["1.1.0", "1.0.9"])
def test_check_reports_idle_for_the_same_or_older_version(tmp_path: Path, version: str) -> None:
    manager, private_key = _manager(tmp_path)
    manifest = _signed_manifest(private_key, version=version)
    manager = manager.with_manifest_fetcher(lambda _url: manifest)

    status = manager.check()

    assert status["state"] == "idle"
    assert status["release"] is None


def test_snoozed_optional_release_stays_hidden_until_a_newer_version(tmp_path: Path) -> None:
    manager, private_key = _manager(tmp_path)
    current_release = _signed_manifest(private_key, version="1.3.0")
    manager.with_manifest_fetcher(lambda _url: current_release)

    assert manager.check()["state"] == "available"
    assert manager.snooze()["state"] == "unavailable"
    assert manager.status()["release"] is None

    from wh_local.app_update import UpdateManager

    restarted = UpdateManager(manager.settings)
    restarted.with_manifest_fetcher(lambda _url: current_release)
    assert restarted.check()["state"] == "unavailable"

    newer_release = _signed_manifest(private_key, version="1.4.0")
    restarted.with_manifest_fetcher(lambda _url: newer_release)
    status = restarted.check()
    assert status["state"] == "available"
    assert status["release"]["version"] == "1.4.0"


def test_check_does_not_fetch_updates_on_unsupported_platform(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path, platform="darwin")
    manager = manager.with_manifest_fetcher(lambda _url: pytest.fail("must not fetch"))

    assert manager.check()["state"] == "unavailable"


def test_install_rejects_a_download_with_a_sha256_mismatch(tmp_path: Path) -> None:
    manager, private_key = _manager(tmp_path)
    manifest = _signed_manifest(private_key)
    manager = manager.with_manifest_fetcher(lambda _url: manifest)
    manager.check()
    launches: list[tuple[Path, list[str]]] = []
    manager = manager.with_downloader(lambda _url: [b"corrupt installer"])
    manager = manager.with_launcher(lambda path, args: launches.append((path, args)))

    status = manager.install()

    assert status["state"] == "failed"
    assert "SHA-256" in status["error"]
    assert launches == []


class _UrlResponse(io.BytesIO):
    def __init__(self, payload: bytes, final_url: str, content_length: bool = False) -> None:
        super().__init__(payload)
        self._final_url = final_url
        self.headers = {"Content-Length": str(len(payload))} if content_length else {}

    def geturl(self) -> str:
        return self._final_url

    def __enter__(self) -> "_UrlResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def test_check_rejects_a_manifest_redirect_to_an_untrusted_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    import wh_local.app_update as app_update

    manager, private_key = _manager(tmp_path)
    payload = json.dumps(_signed_manifest(private_key)).encode("utf-8")
    monkeypatch.setattr(
        app_update,
        "urlopen",
        lambda *_args, **_kwargs: _UrlResponse(payload, "https://evil.example/manifest.json"),
    )

    status = manager.check()

    assert status["state"] == "failed"
    assert "allowlisted" in str(status["error"]).lower()


def test_install_rejects_an_installer_redirect_to_an_untrusted_host(tmp_path: Path) -> None:
    manager, private_key = _manager(tmp_path)
    manager.with_manifest_fetcher(lambda _url: _signed_manifest(private_key))
    assert manager.check()["state"] == "available"
    launches: list[tuple[Path, list[str]]] = []
    manager.with_downloader(
        lambda _url: _UrlResponse(b"installer", "https://evil.example/MainPG-1.2.0.exe")
    )
    manager.with_launcher(lambda path, args: launches.append((path, args)))

    status = manager.install()

    assert status["state"] == "failed"
    assert "allowlisted" in str(status["error"]).lower()
    assert launches == []


def test_install_streams_to_runtime_updates_and_launches_silently(tmp_path: Path) -> None:
    manager, private_key = _manager(tmp_path)
    manager.with_manifest_fetcher(lambda _url: _signed_manifest(private_key))
    assert manager.check()["state"] == "available"
    source = _UrlResponse(
        b"installer",
        "https://updates.example.invalid/MainPG-1.2.0.exe",
        content_length=True,
    )
    launches: list[tuple[Path, list[str]]] = []
    manager.with_downloader(lambda _url: source)
    manager.with_launcher(lambda path, args: launches.append((path, args)))

    status = manager.install()

    destination = tmp_path / "updates" / "MainPG-1.2.0.exe"
    assert status["state"] == "installing"
    assert status["progress"] == {
        "downloaded_bytes": len(b"installer"),
        "total_bytes": len(b"installer"),
        "percentage": 100.0,
    }
    assert destination.read_bytes() == b"installer"
    assert launches == [(destination, ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])]
    assert source.closed


def test_installing_state_keeps_the_single_operation_slot_until_process_exit(tmp_path: Path) -> None:
    manager, private_key = _manager(tmp_path)
    manifest = _signed_manifest(private_key)
    fetch_count = 0

    def fetch(_url: str) -> dict[str, object]:
        nonlocal fetch_count
        fetch_count += 1
        return manifest

    manager.with_manifest_fetcher(fetch)
    assert manager.check()["state"] == "available"
    manager.with_downloader(lambda _url: [b"installer"])
    manager.with_launcher(lambda _path, _args: None)
    assert manager.install()["state"] == "installing"

    assert manager.check()["state"] == "installing"
    assert manager.install()["state"] == "installing"
    assert fetch_count == 1


def test_windows_launcher_detaches_the_installer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import wh_local.app_update as app_update

    calls: list[tuple[list[str], dict[str, Any]]] = []
    monkeypatch.setattr(app_update.sys, "platform", "win32")
    monkeypatch.setattr(app_update.subprocess, "DETACHED_PROCESS", 8, raising=False)
    monkeypatch.setattr(app_update.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    monkeypatch.setattr(
        app_update.subprocess,
        "Popen",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )
    installer = tmp_path / "updates" / "MainPG-1.2.0.exe"

    app_update.UpdateManager._launch_installer(installer, ["/VERYSILENT"])

    assert calls == [
        (
            [str(installer), "/VERYSILENT"],
            {"cwd": str(installer.parent), "creationflags": 520},
        )
    ]


def test_update_actions_reject_cross_origin_posts_but_allow_local_and_native_calls(tmp_path: Path) -> None:
    from wh_local.app_update import create_router

    class ManagerStub:
        def status(self) -> dict[str, object]:
            return {"state": "idle"}

        def start_check(self) -> dict[str, object]:
            return {"state": "checking"}

        def start_install(self) -> dict[str, object]:
            return {"state": "downloading"}

    app = FastAPI()
    app.include_router(create_router(ManagerStub()))  # type: ignore[arg-type]
    client = TestClient(app, base_url="http://127.0.0.1:8000")

    assert client.post("/api/app-update/check").status_code == 200
    assert client.post(
        "/api/app-update/check", headers={"Origin": "http://127.0.0.1:8000"}
    ).status_code == 200
    assert client.post(
        "/api/app-update/install", headers={"Origin": "https://evil.example"}
    ).status_code == 403
    assert client.post(
        "/api/app-update/install", headers={"Origin": "null"}
    ).status_code == 403
    assert client.post(
        "/api/app-update/check",
        headers={"Host": "attacker.example:8000", "Origin": "http://attacker.example:8000"},
    ).status_code == 403


def test_runtime_registers_update_status_and_action_routes(tmp_path: Path) -> None:
    from wh_local.app.main import create_app

    with TestClient(
        create_app(tmp_path / "workbench.sqlite3"), base_url="http://127.0.0.1:8000"
    ) as client:
        status = client.get("/api/app-update/status")
        check = client.post("/api/app-update/check")
        install = client.post("/api/app-update/install")

    assert status.status_code == 200
    assert status.json()["current_version"] == "1.3.0"
    assert check.status_code == 200
    assert install.status_code == 200


def test_offline_startup_keeps_health_available(tmp_path: Path) -> None:
    from wh_local.app.main import create_app

    fetch_started = threading.Event()

    def offline(_url: str) -> dict[str, object]:
        fetch_started.set()
        raise OSError("release server offline")

    app = create_app(tmp_path / "workbench.sqlite3")
    manager = app.state.update_manager
    object.__setattr__(manager.settings, "platform", "win32")
    manager.with_manifest_fetcher(offline)

    with TestClient(app) as client:
        response = client.get("/health")
        assert fetch_started.wait(timeout=1)

    assert response.status_code == 200
    assert response.json()["ok"] is True
