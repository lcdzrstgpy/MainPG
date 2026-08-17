from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from wh_local.app_update import SemanticVersion
from wh_local.runtime.release_manifest import canonical_manifest_bytes, create_signed_manifest


def test_canonical_manifest_bytes_are_sorted_compact_utf8() -> None:
    payload = {
        "sha256": "abc",
        "installer_url": "https://updates.example/MainPG-Setup-1.1.0.exe",
        "mandatory": False,
        "published_at": "2026-08-17T00:00:00Z",
        "release_notes": "Bug fixes",
        "version": "1.1.0",
    }

    assert canonical_manifest_bytes(payload) == (
        b'{"installer_url":"https://updates.example/MainPG-Setup-1.1.0.exe","mandatory":false,"published_at":"2026-08-17T00:00:00Z","release_notes":"Bug fixes","sha256":"abc","version":"1.1.0"}'
    )


def test_create_signed_manifest_signs_the_canonical_unsigned_payload() -> None:
    private_key = Ed25519PrivateKey.generate()

    manifest = create_signed_manifest(
        version="1.1.0",
        installer_url="https://updates.example/MainPG-Setup-1.1.0.exe",
        sha256="a" * 64,
        mandatory=True,
        release_notes="Security update",
        published_at="2026-08-17T00:00:00Z",
        private_key=private_key,
    )

    unsigned = {
        key: manifest[key]
        for key in ("version", "mandatory", "installer_url", "sha256", "release_notes", "published_at")
    }
    signature = base64.b64decode(manifest["signature"])
    private_key.public_key().verify(signature, canonical_manifest_bytes(unsigned))
    assert json.loads(json.dumps(manifest)) == manifest


def test_signing_is_deterministic_for_the_same_key_and_payload() -> None:
    private_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    values = {
        "version": "1.1.0",
        "mandatory": False,
        "installer_url": "https://updates.example/MainPG-Setup-1.1.0.exe",
        "sha256": "b" * 64,
        "release_notes": "Bug fixes",
        "published_at": "2026-08-17T00:00:00Z",
        "private_key": private_key,
    }

    assert create_signed_manifest(**values) == create_signed_manifest(**values)


@pytest.mark.parametrize(
    "version",
    [
        "0.0.0",
        "1.2.3",
        "1.2.3-alpha",
        "1.2.3-alpha.1",
        "1.2.3-0",
        "1.2.3+build.01",
        "1.2.3-alpha+build.7",
        "01.2.3",
        "1.2.3.foo",
        "1.2.3-01",
        "1.2.3-alpha.01",
        "1.2.3+",
        "1.2.3-",
        "1.2.3\n",
        "1.2٣.4",
    ],
)
def test_build_installer_version_validation_matches_runtime_semver(version: str) -> None:
    runtime_root = Path(__file__).resolve().parents[1]
    build_script = (runtime_root / "build_installer.ps1").read_text(encoding="utf-8")
    match = re.search(
        r"\[ValidatePattern\('([^']+)'\)\]\s*\[string\]\$Version",
        build_script,
    )

    assert match is not None, "build script must declare an explicit release SemVer validator"
    # PowerShell/.NET uses \z for strict end-of-input; Python spells it \Z.
    build_pattern = re.compile(match.group(1).replace(r"\z", r"\Z"))
    try:
        SemanticVersion.parse(version)
    except ValueError:
        runtime_accepts = False
    else:
        runtime_accepts = True

    assert (build_pattern.fullmatch(version) is not None) is runtime_accepts


def test_release_scripts_use_versioned_installer_and_preserve_installer_child() -> None:
    runtime_root = Path(__file__).resolve().parents[1]
    installer = (runtime_root / "mainpg-installer.iss").read_text(encoding="utf-8")
    build_script = (runtime_root / "build_installer.ps1").read_text(encoding="utf-8")
    launcher = (runtime_root / "run_workbench.py").read_text(encoding="utf-8")

    assert '#define MySetupBaseFilename "MainPG-Setup-" + MyAppVersion' in installer
    assert "OutputBaseFilename={#MySetupBaseFilename}" in installer
    assert 'Filename: "{app}\\{#MyAppExeName}"; Flags: nowait skipifnotsilent' in installer
    assert "Exec('taskkill.exe', '/F /IM MainPG.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);" in installer
    assert "Exec('taskkill.exe', '/F /IM MainPG.exe /T'" not in installer
    assert '"/DMySetupBaseFilename=MainPG-Setup-$Version"' in build_script
    assert '"dist\\MainPG-Setup-$Version.exe"' in build_script
    assert 'MAINPG_RELEASE_SIGNING_KEY_PATH is required' in build_script
    assert '"wh_local\\config.py"' in build_script
    assert "$versionMatch = [regex]::Match($configText, '(?m)^APP_VERSION = \"[^\"]*\"$')" in build_script
    assert "if (-not $versionMatch.Success) { throw \"APP_VERSION metadata entry missing from $runtimeConfig\" }" in build_script
    assert '"taskkill", "/PID", str(pid), "/F"' in launcher
    assert '"taskkill", "/PID", str(pid), "/F", "/T"' not in launcher
