from __future__ import annotations

import io
import os
import time
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from wh_local.modules.pod_customization.service import PodCustomizationService

WORKSPACE = "workspace-a"
OWNER = "designer-1"
OLD_STAMP = "2020-01-01T00:00:00.000+00:00"


def _png(color: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (48, 48), color).save(buffer, "PNG")
    return buffer.getvalue()


def _service(tmp_path: Path) -> PodCustomizationService:
    return PodCustomizationService(
        tmp_path / "workbench.sqlite3",
        tmp_path / "pod-assets",
        SimpleNamespace(),
        start_workers=False,
    )


def _store_asset(service: PodCustomizationService, content: bytes, *, kind: str = "direct_listing_grid") -> dict:
    stored = service.assets.save_image(WORKSPACE, OWNER, content)
    return service.repository.create_asset(
        workspace_id=WORKSPACE,
        owner_user_id=OWNER,
        kind=kind,
        filename=f"{kind}.png",
        relative_path=stored.relative_path,
        content_type=stored.content_type,
        byte_size=stored.byte_size,
        sha256=stored.sha256,
        width=stored.width,
        height=stored.height,
    )


def _backdate_asset(service: PodCustomizationService, asset_id: str) -> None:
    with service.repository._connect() as connection:
        connection.execute(
            "UPDATE pod_customization_assets SET created_at = ? WHERE asset_id = ?",
            (OLD_STAMP, asset_id),
        )


def _put_file(service: PodCustomizationService, relative_path: str, content: bytes) -> Path:
    target = service.assets.root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def _age_file(path: Path, seconds: float = 3 * 86400) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def test_sweep_reaps_stale_rows_and_reports_released_bytes(tmp_path: Path) -> None:
    """超窗且无任何引用的资产行：行级清扫删行删文件，并如实上报释放量。"""
    service = _service(tmp_path)
    stale = _store_asset(service, _png("#888888"))
    _backdate_asset(service, stale["asset_id"])
    file_path = service.assets.root / stale["relative_path"]
    expected_bytes = file_path.stat().st_size

    result = service.sweep_local_cache(older_than_hours=48)

    assert result["reaped_assets"] == 1
    assert result["files_removed"] == 1
    assert result["bytes_freed"] == expected_bytes
    assert not file_path.exists()


def test_sweep_removes_orphan_files_but_never_touches_in_flight_or_fresh(tmp_path: Path) -> None:
    """孤儿对账：删除无行引用的旧文件；窗口内、*.tmp、点前缀文件一律不碰。"""
    service = _service(tmp_path)
    old_orphan = _put_file(service, "scope-a/aa/old-orphan.png", _png("#ff0000"))
    fresh_orphan = _put_file(service, "scope-a/bb/fresh-orphan.png", _png("#00ff00"))
    temp_file = _put_file(service, "scope-a/cc/in-flight.tmp", b"partial-bytes")
    dot_file = _put_file(service, "scope-a/dd/.hidden.png", _png("#0000ff"))
    _age_file(old_orphan)
    _age_file(temp_file)
    _age_file(dot_file)
    expected_bytes = old_orphan.stat().st_size

    result = service.sweep_local_cache(older_than_hours=48)

    assert not old_orphan.exists(), "old orphan must be removed"
    assert fresh_orphan.exists(), "fresh file is inside the retention window"
    assert temp_file.exists(), "*.tmp marks an in-flight write"
    assert dot_file.exists(), "dot-prefixed files are in-flight writes"
    assert result["orphan_files_removed"] == 1
    assert result["files_removed"] == 1
    assert result["bytes_freed"] == expected_bytes


def test_sweep_never_removes_templates_recent_rows_or_shared_files(tmp_path: Path) -> None:
    """负向对照：模板、窗口内的行、被其它行共享的文件都必须原地不动。"""
    service = _service(tmp_path)

    template_asset = _store_asset(service, _png("#ff8800"), kind="template")
    _backdate_asset(service, template_asset["asset_id"])

    recent_asset = _store_asset(service, _png("#00ffff"))

    shared_content = _png("#8800ff")
    stale_shared = _store_asset(service, shared_content)
    _backdate_asset(service, stale_shared["asset_id"])
    keeper_shared = _store_asset(service, shared_content)
    assert stale_shared["relative_path"] == keeper_shared["relative_path"]

    result = service.sweep_local_cache(older_than_hours=48)

    assert (service.assets.root / template_asset["relative_path"]).exists()
    assert (service.assets.root / recent_asset["relative_path"]).exists()
    assert (service.assets.root / keeper_shared["relative_path"]).exists(), (
        "a file shared with a live row must survive the stale row's reaping"
    )
    assert result["files_removed"] == 0
    assert result["bytes_freed"] == 0

    with service.repository._connect() as connection:
        remaining = {
            row["asset_id"]
            for row in connection.execute("SELECT asset_id FROM pod_customization_assets")
        }
    assert template_asset["asset_id"] in remaining
    assert recent_asset["asset_id"] in remaining
    assert stale_shared["asset_id"] not in remaining, "stale unreferenced row should be reaped"
    assert keeper_shared["asset_id"] in remaining


def test_sweep_is_idempotent_and_reports_zero_on_second_run(tmp_path: Path) -> None:
    service = _service(tmp_path)
    orphan = _put_file(service, "scope-a/aa/old-orphan.png", _png("#123456"))
    _age_file(orphan)

    first = service.sweep_local_cache(older_than_hours=48)
    second = service.sweep_local_cache(older_than_hours=48)

    assert first["files_removed"] == 1
    assert second["files_removed"] == 0
    assert second["bytes_freed"] == 0
    assert second["reaped_assets"] == 0
    assert second["orphan_files_removed"] == 0


def test_asset_scan_helpers_skip_temporaries_and_list_registered_paths(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _put_file(service, "scope-a/aa/normal.png", _png("#101010"))
    _put_file(service, "scope-a/bb/pending.tmp", b"x")
    _put_file(service, "scope-a/cc/.dot.png", _png("#202020"))

    scanned = {path for path, _size, _mtime in service.assets.iter_stored_files()}
    assert scanned == {"scope-a/aa/normal.png"}
    assert service.repository.list_asset_relative_paths() == set()

    asset = _store_asset(service, _png("#303030"))
    assert asset["relative_path"] in service.repository.list_asset_relative_paths()
