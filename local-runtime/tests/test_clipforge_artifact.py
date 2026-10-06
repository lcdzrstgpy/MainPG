from __future__ import annotations

import json
from pathlib import Path

import pytest

from wh_local.modules.clipforge.artifact import resolve_clipforge_build


def write_valid_artifact(root: Path, artifact_id: str = "artifact-a", runtime: str = "node") -> Path:
    """Write the minimal runtime subset every deploy root must carry."""
    for directory in (".next/server", ".next/static", "node_modules/next", "public", "drizzle"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    (root / "server.js").write_text("export {};", encoding="utf-8")
    (root / "mainpg-sidecar.json").write_text(
        json.dumps({"schemaVersion": 1, "artifactId": artifact_id, "buildId": "build-a", "runtime": runtime}),
        encoding="utf-8",
    )
    (root / ".next" / "BUILD_ID").write_text("build-a\n", encoding="utf-8")
    (root / ".next" / "server" / "middleware-manifest.json").write_text("{}", encoding="utf-8")
    (root / ".next" / "required-server-files.json").write_text(json.dumps({"files": []}), encoding="utf-8")
    (root / "node_modules" / "next" / "package.json").write_text("{}", encoding="utf-8")
    return root


def write_pointer(data_root: Path, relative_path: str, artifact_id: str = "dev-a") -> Path:
    pointer = data_root / "clipforge" / "current.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(
        json.dumps({"schemaVersion": 1, "artifactId": artifact_id, "relativePath": relative_path}),
        encoding="utf-8",
    )
    return pointer


@pytest.fixture(autouse=True)
def _clear_app_root_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WH_CLIPFORGE_APP_ROOT", raising=False)


def test_packaged_app_root_uses_server_at_root(tmp_path: Path) -> None:
    app_root = tmp_path / "clipforge" / "app"
    write_valid_artifact(app_root, artifact_id="packaged-a")

    build = resolve_clipforge_build(tmp_path, tmp_path / "data")

    assert build.state == "available"
    assert build.app_root == app_root.resolve()
    assert build.app_root is not None
    assert build.app_root / "server.js" == app_root / "server.js"
    assert build.artifact_id == "packaged-a"


def test_dev_pointer_resolves_inside_artifacts_root(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    artifact = write_valid_artifact(data_root / "clipforge" / "artifacts" / "dev-a", artifact_id="dev-a")
    write_pointer(data_root, "artifacts/dev-a")

    build = resolve_clipforge_build(tmp_path / "install", data_root)

    assert build.state == "available"
    assert build.app_root == artifact.resolve()
    assert build.artifact_id == "dev-a"


def test_dev_pointer_cannot_escape_artifacts_root(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    outside = write_valid_artifact(tmp_path / "outside", artifact_id="outside-a")
    write_pointer(data_root, "../../outside")

    build = resolve_clipforge_build(tmp_path / "install", data_root)

    assert outside.is_dir()
    assert build.state == "invalid"
    assert build.app_root is None


def test_packaged_app_takes_precedence_over_development_pointer(tmp_path: Path) -> None:
    write_valid_artifact(tmp_path / "clipforge" / "app", artifact_id="packaged-a")
    data_root = tmp_path / "data"
    write_valid_artifact(data_root / "clipforge" / "artifacts" / "dev-a", artifact_id="dev-a")
    write_pointer(data_root, "artifacts/dev-a")

    build = resolve_clipforge_build(tmp_path, data_root)

    assert build.artifact_id == "packaged-a"


def test_env_override_wins_over_both_layouts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    override = write_valid_artifact(tmp_path / "elsewhere" / "app", artifact_id="env-a")
    write_valid_artifact(tmp_path / "clipforge" / "app", artifact_id="packaged-a")
    monkeypatch.setenv("WH_CLIPFORGE_APP_ROOT", str(override))

    build = resolve_clipforge_build(tmp_path, tmp_path / "data")

    assert build.state == "available"
    assert build.artifact_id == "env-a"
    assert build.app_root == override.resolve()


def test_missing_build_is_reported_without_absolute_paths(tmp_path: Path) -> None:
    build = resolve_clipforge_build(tmp_path / "install", tmp_path / "data")

    assert build.state == "missing"
    assert build.app_root is None
    assert build.artifact_id is None
    assert str(tmp_path) not in build.message


def test_electron_metadata_is_rejected(tmp_path: Path) -> None:
    write_valid_artifact(tmp_path / "clipforge" / "app", artifact_id="packaged-a", runtime="electron")

    build = resolve_clipforge_build(tmp_path, tmp_path / "data")

    assert build.state == "invalid"
    assert build.app_root is None
    assert str(tmp_path) not in build.message


def test_missing_build_id_is_rejected(tmp_path: Path) -> None:
    app_root = write_valid_artifact(tmp_path / "clipforge" / "app", artifact_id="packaged-a")
    (app_root / ".next" / "BUILD_ID").unlink()

    build = resolve_clipforge_build(tmp_path, tmp_path / "data")

    assert build.state == "invalid"
    assert build.app_root is None


def test_missing_middleware_manifest_is_rejected(tmp_path: Path) -> None:
    app_root = write_valid_artifact(tmp_path / "clipforge" / "app", artifact_id="packaged-a")
    (app_root / ".next" / "server" / "middleware-manifest.json").unlink()

    build = resolve_clipforge_build(tmp_path, tmp_path / "data")

    assert build.state == "invalid"
    assert build.app_root is None


def test_corrupt_dev_pointer_is_rejected(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    pointer = data_root / "clipforge" / "current.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text("{not json", encoding="utf-8")

    build = resolve_clipforge_build(tmp_path / "install", data_root)

    assert build.state == "invalid"
    assert build.app_root is None
    assert str(tmp_path) not in build.message
