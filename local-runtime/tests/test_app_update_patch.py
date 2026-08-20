"""End-to-end tests for the incremental patch update flow.

Covers: patch manifest builder (diff + Ed25519 signing), PatchManager
verify/download/state rendering, signature tamper rejection, and the real
MainPG-Updater.exe application of replace/add/delete (compiled with csc.exe).
"""
from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from wh_local.app_update import (
    PATCH_CONTRACT_VERSION,
    PatchFile,
    PatchManager,
    PatchRelease,
    PatchSettings,
    canonical_patch_payload,
)
from wh_local.runtime.patch_manifest_builder import build_patch
from wh_local.runtime.release_manifest import load_private_key

UPDATER_EXE = Path(__file__).resolve().parents[1] / "updater" / "MainPG-Updater.exe"


def _make_key(tmp_path: Path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    key_path = tmp_path / "signing.key"
    key_path.write_bytes(base64.b64encode(key.private_bytes_raw()))
    public_b64 = base64.b64encode(bytes(key.public_key().public_bytes_raw())).decode("ascii")
    return key, key_path, public_b64


def _dist(root: Path, version: str, files: dict[str, str]) -> Path:
    dist = root / f"dist-{version}"
    dist.mkdir(parents=True)
    for rel, content in files.items():
        target = dist / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8"))
    (dist / "version.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    return dist


class _BytesSource:
    """Mimics the file-like object returned by urlopen."""

    def __init__(self, data: bytes, url: str):
        self._buffer = io.BytesIO(data)
        self._url = url

    def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)

    def close(self) -> None:
        self._buffer.close()

    def geturl(self) -> str:
        return self._url


@pytest.fixture(scope="module")
def updater_present():
    if not UPDATER_EXE.is_file():
        pytest.skip("MainPG-Updater.exe not compiled; run updater build first")
    return UPDATER_EXE


def test_build_patch_diff_and_signature(tmp_path: Path) -> None:
    key, key_path, public_b64 = _make_key(tmp_path)
    old = _dist(tmp_path, "1.1.0", {
        "unchanged.txt": "same",
        "changed.txt": "old-content",
        "removed.txt": "bye",
        "sub/dir/kept.txt": "nested",
    })
    new = _dist(tmp_path, "1.2.0", {
        "unchanged.txt": "same",
        "changed.txt": "new-content",
        "added.txt": "hello",
        "sub/dir/kept.txt": "nested",
        "sub/dir/new.txt": "fresh",
    })
    out = tmp_path / "patch"
    manifest = build_patch(
        from_dir=old,
        to_dir=new,
        from_version="1.1.0",
        to_version="1.2.0",
        file_base_url="https://workbench.haocoming.top/mainpg/windows/patch/1.2.0",
        private_key=load_private_key(key_path),
        output_dir=out,
    )
    by_path = {f["path"]: f for f in manifest["files"]}
    assert by_path["changed.txt"]["action"] == "replace"
    assert by_path["added.txt"]["action"] == "add"
    assert by_path["removed.txt"]["action"] == "delete"
    assert "unchanged.txt" not in by_path
    assert "version.json" not in by_path
    assert "MainPG-Updater.exe" not in by_path
    # the output payload carries the changed files
    assert (out / "changed.txt").read_bytes() == b"new-content"
    assert (out / "added.txt").read_bytes() == b"hello"
    assert not (out / "removed.txt").exists()

    # signature round-trips through PatchManager with the public key
    manager = PatchManager(
        PatchSettings(
            current_version="1.1.0",
            patch_manifest_url="https://workbench.haocoming.top/mainpg/windows/patch-manifest.json",
            allowed_hosts=frozenset({"workbench.haocoming.top"}),
            public_key_b64=public_b64,
            runtime_root=tmp_path / "data",
            install_root=tmp_path / "install",
        )
    )
    release = manager._validate_manifest(manifest)
    assert release.from_version == "1.1.0"
    assert release.to_version == "1.2.0"
    assert len(release.files) == 4

    # tampering must be rejected
    tampered = dict(manifest)
    tampered["files"] = [{"path": "evil.txt", "action": "add", "sha256": "0" * 64, "size": 1}]
    with pytest.raises(Exception):
        manager._validate_manifest(tampered)
    tampered_signature = dict(manifest)
    tampered_signature["signature"] = base64.b64encode(b"x" * 64).decode("ascii")
    with pytest.raises(Exception):
        manager._validate_manifest(tampered_signature)


def test_patch_manager_downloads_and_renders_state(tmp_path: Path, updater_present) -> None:
    key, key_path, public_b64 = _make_key(tmp_path)
    old = _dist(tmp_path, "1.1.0", {"a.txt": "old-a", "b.txt": "keep", "c.txt": "remove"})
    new = _dist(tmp_path, "1.2.0", {"a.txt": "new-a", "b.txt": "keep", "d.txt": "added-d"})
    out = tmp_path / "patch"
    manifest = build_patch(
        from_dir=old,
        to_dir=new,
        from_version="1.1.0",
        to_version="1.2.0",
        file_base_url="https://workbench.haocoming.top/mainpg/windows/patch/1.2.0",
        private_key=load_private_key(key_path),
        output_dir=out,
    )

    def fetcher(_url: str):
        return manifest

    def downloader(url: str):
        rel = url.rsplit("/patch/1.2.0/", 1)[1]
        return _BytesSource((out / rel).read_bytes(), url)

    install_root = tmp_path / "install"
    install_root.mkdir(parents=True)
    (install_root / "MainPG.exe").write_bytes(b"placeholder")
    (install_root / "MainPG-Updater.exe").write_bytes(updater_present.read_bytes())
    runtime_root = tmp_path / "data"

    launched: list[tuple[str, list[str]]] = []

    def launcher(path: Path, args: list[str]):
        launched.append((str(path), args))

    manager = PatchManager(
        PatchSettings(
            current_version="1.1.0",
            patch_manifest_url="https://workbench.haocoming.top/mainpg/windows/patch-manifest.json",
            allowed_hosts=frozenset({"workbench.haocoming.top"}),
            public_key_b64=public_b64,
            runtime_root=runtime_root,
            install_root=install_root,
        ),
        manifest_fetcher=fetcher,
        downloader=downloader,
        launcher=launcher,
    )
    assert manager._begin("checking")
    manager._check_after_begin()
    assert manager._state == "available"

    assert manager._begin("downloading")
    manager._install_after_begin()
    assert manager._state == "installing", f"state={manager._state} error={manager._error}"  # updater launched; main process keeps running until exit
    assert launched and launched[0][0] == str(install_root / "MainPG-Updater.exe")
    assert launched[0][1] == ["--apply", str(runtime_root / "updates" / "patch-state.txt")]

    state_text = (runtime_root / "updates" / "patch-state.txt").read_text(encoding="utf-8")
    assert state_text.startswith("# mainpg-patch-state v1")
    assert f"base_dir={install_root}" in state_text
    assert f"staging_dir={runtime_root / 'updates' / 'patch' / '1.2.0'}" in state_text
    assert "replace|a.txt|" in state_text
    assert "add|d.txt|" in state_text
    assert "delete|c.txt" in state_text
    assert (runtime_root / "updates" / "patch" / "1.2.0" / "a.txt").read_bytes() == b"new-a"


def test_updater_applies_patch_and_updates_version(tmp_path: Path, updater_present) -> None:
    # build a patch like PatchManager would stage it
    key, key_path, public_b64 = _make_key(tmp_path)
    old = _dist(tmp_path, "1.1.0", {"a.txt": "old-a", "c.txt": "remove", "keep/nested.txt": "nested-keep"})
    new = _dist(tmp_path, "1.2.0", {"a.txt": "new-a", "d.txt": "added-d", "keep/nested.txt": "nested-keep"})
    out = tmp_path / "patch"
    manifest = build_patch(
        from_dir=old,
        to_dir=new,
        from_version="1.1.0",
        to_version="1.2.0",
        file_base_url="https://workbench.haocoming.top/mainpg/windows/patch/1.2.0",
        private_key=load_private_key(key_path),
        output_dir=out,
    )
    # assemble an install tree that mirrors an old installation
    install = tmp_path / "install"
    install.mkdir(parents=True)
    (install / "MainPG.exe").write_bytes(updater_present.read_bytes())  # placeholder exe
    (install / "MainPG-Updater.exe").write_bytes(updater_present.read_bytes())
    for rel, content in [("a.txt", "old-a"), ("c.txt", "remove"), ("keep/nested.txt", "nested-keep")]:
        target = install / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (install / "version.json").write_text(json.dumps({"version": "1.1.0"}), encoding="utf-8")

    staging = tmp_path / "data" / "updates" / "patch" / "1.2.0"
    for entry in manifest["files"]:
        if entry["action"] == "delete":
            continue
        src = out / entry["path"]
        target = staging / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(src.read_bytes())

    release = PatchRelease(
        from_version=manifest["from_version"],
        to_version=manifest["to_version"],
        published_at=manifest["published_at"],
        file_base_url=manifest["file_base_url"],
        files=tuple(PatchFile(**f) for f in manifest["files"]),
    )
    manager = PatchManager(
        PatchSettings(
            current_version="1.1.0",
            patch_manifest_url="https://workbench.haocoming.top/mainpg/windows/patch-manifest.json",
            allowed_hosts=frozenset({"workbench.haocoming.top"}),
            public_key_b64=public_b64,
            runtime_root=tmp_path / "data",
            install_root=install,
        )
    )
    state_file = tmp_path / "data" / "updates" / "patch-state.txt"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(manager._render_state(release, staging), encoding="utf-8")

    # run the real updater (detached launch is fine; it applies synchronously before MainPG.exe)
    env = dict(os.environ)
    proc = subprocess.run(
        [str(install / "MainPG-Updater.exe"), "--apply", str(state_file)],
        capture_output=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")

    assert (install / "a.txt").read_text(encoding="utf-8") == "new-a"
    assert (install / "d.txt").read_text(encoding="utf-8") == "added-d"
    assert not (install / "c.txt").exists()
    assert (install / "keep" / "nested.txt").read_text(encoding="utf-8") == "nested-keep"
    assert json.loads((install / "version.json").read_text(encoding="utf-8"))["version"] == "1.2.0"
    assert not state_file.exists()
