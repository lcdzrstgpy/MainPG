from __future__ import annotations

import io
import os
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from wh_local.modules.pod_customization.repository import PodRepositoryError
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


# ---- 选择性清扫（「存储空间」勾选回收） ----


def _exec_fixture_sql(service: PodCustomizationService, statement: str, params: tuple) -> None:
    """夹具直插：用默认（外键关闭）连接构造悬空引用场景，测试只关心清扫判定。"""

    connection = sqlite3.connect(service.repository.database_path)
    try:
        connection.execute(statement, params)
        connection.commit()
    finally:
        connection.close()


def _insert_style_result(
    service: PodCustomizationService,
    *,
    result_id: str,
    pattern_asset_id: str,
    published: bool,
) -> None:
    _exec_fixture_sql(
        service,
        """INSERT INTO pod_customization_style_grid_results
           (result_id, batch_id, workspace_id, owner_user_id, style_index, variant_index,
            status, pattern_asset_id, composite_asset_id, pattern_fingerprint,
            scene_optimized, error_message, created_at, updated_at)
           VALUES (?, ?, ?, ?, 0, 0, 'completed', ?, '', '', 0, '', ?, ?)""",
        (
            result_id,
            f"fixture-batch-{result_id}",
            WORKSPACE,
            OWNER,
            pattern_asset_id,
            OLD_STAMP,
            OLD_STAMP,
        ),
    )
    if published:
        _exec_fixture_sql(
            service,
            """INSERT INTO pod_customization_style_grid_publications
               (result_id, role, public_url, updated_at) VALUES (?, 'product', ?, ?)""",
            (result_id, "https://cos.example.test/panel.jpg", OLD_STAMP),
        )


def _insert_batch_with_call(
    service: PodCustomizationService,
    *,
    batch_id: str,
    status: str,
    grid_asset_id: str,
) -> None:
    _exec_fixture_sql(
        service,
        """INSERT INTO pod_customization_batches
           (batch_id, workspace_id, owner_user_id, title, status, template_id,
            template_snapshot_id, template_name, requested_count, initial_call_count,
            max_refill_calls, prompt_version, prompt_snapshot, business_fields_json,
            created_at, updated_at)
           VALUES (?, ?, ?, 'fixture', ?, 'tpl', 'snap', 'tpl', 20, 1, 2, 'v1', '{}', '{}', ?, ?)""",
        (batch_id, WORKSPACE, OWNER, status, OLD_STAMP, OLD_STAMP),
    )
    _exec_fixture_sql(
        service,
        """INSERT INTO pod_customization_generation_calls
           (call_id, batch_id, workspace_id, owner_user_id, call_kind, call_index,
            status, prompt_snapshot, grid_asset_id, created_at)
           VALUES (?, ?, ?, ?, 'grid', 0, 'done', '{}', ?, ?)""",
        (f"call-{batch_id}", batch_id, WORKSPACE, OWNER, grid_asset_id, OLD_STAMP),
    )


def _category_map(payload: dict) -> dict:
    return {item["key"]: item for item in payload["categories"]}


def test_preview_reports_categories_without_deleting_anything(tmp_path: Path) -> None:
    """扫描是只读的：分类数量准确、不删文件、不清引用；随后的全量清扫与预览总量一致。"""
    service = _service(tmp_path)
    stale = _store_asset(service, _png("#888888"))
    _backdate_asset(service, stale["asset_id"])
    stale_bytes = (service.assets.root / stale["relative_path"]).stat().st_size
    orphan = _put_file(service, "scope-a/aa/old-orphan.png", _png("#ff0000"))
    _age_file(orphan)
    orphan_bytes = orphan.stat().st_size

    preview = service.preview_local_cache(older_than_hours=48)

    by_key = _category_map(preview)
    assert by_key["unreferenced_residue"]["files"] == 1
    assert by_key["unreferenced_residue"]["bytes"] == stale_bytes
    assert by_key["disk_orphans"]["files"] == 1
    assert by_key["disk_orphans"]["bytes"] == orphan_bytes
    assert by_key["published_release"]["files"] == 0
    assert by_key["terminal_intermediates"]["files"] == 0
    assert preview["total_files"] == 2
    assert preview["total_bytes"] == stale_bytes + orphan_bytes

    # 预览绝不产生任何副作用
    assert orphan.exists()
    assert (service.assets.root / stale["relative_path"]).exists()
    with service.repository._connect() as connection:
        rows = connection.execute(
            "SELECT COUNT(*) FROM pod_customization_assets WHERE asset_id = ?",
            (stale["asset_id"],),
        ).fetchone()[0]
    assert rows == 1

    # 预览 → 执行：释放量与预览完全一致
    result = service.sweep_local_cache(older_than_hours=48)
    assert result["files_removed"] == preview["total_files"]
    assert result["bytes_freed"] == preview["total_bytes"]


def test_selective_sweep_runs_only_checked_categories(tmp_path: Path) -> None:
    """只勾选的项目才执行：未勾选分类一步都不跑，空选择什么都不清，未知分类直接拒绝。"""
    service = _service(tmp_path)
    stale = _store_asset(service, _png("#888888"))
    _backdate_asset(service, stale["asset_id"])
    orphan = _put_file(service, "scope-a/aa/old-orphan.png", _png("#ff0000"))
    _age_file(orphan)

    # 空选择：什么都不清
    empty = service.sweep_local_cache(older_than_hours=48, categories=[])
    assert empty["files_removed"] == 0
    assert orphan.exists()
    assert (service.assets.root / stale["relative_path"]).exists()

    # 只勾「磁盘孤儿文件」：孤儿清掉，资产行与其文件原地不动
    only_disk = service.sweep_local_cache(older_than_hours=48, categories=["disk_orphans"])
    assert only_disk["categories"]["disk_orphans"]["files"] == 1
    assert only_disk["categories"]["unreferenced_residue"]["files"] == 0
    assert only_disk["reaped_assets"] == 0
    assert not orphan.exists()
    assert (service.assets.root / stale["relative_path"]).exists()

    # 勾一个「空跑」分类（已发布，本夹具没有）：其它分类的行与文件都不许动
    only_published = service.sweep_local_cache(
        older_than_hours=48, categories=["published_release"]
    )
    assert only_published["files_removed"] == 0
    assert (service.assets.root / stale["relative_path"]).exists()
    with service.repository._connect() as connection:
        remaining = connection.execute(
            "SELECT COUNT(*) FROM pod_customization_assets WHERE asset_id = ?",
            (stale["asset_id"],),
        ).fetchone()[0]
    assert remaining == 1, "未勾选的分类不得删任何资产行"

    # 再勾「历史无引用残留」：这次才轮到资产
    only_residue = service.sweep_local_cache(
        older_than_hours=48, categories=["unreferenced_residue"]
    )
    assert only_residue["categories"]["unreferenced_residue"]["files"] == 1
    assert only_residue["categories"]["disk_orphans"]["files"] == 0
    assert only_residue["files_removed"] == 1
    assert not (service.assets.root / stale["relative_path"]).exists()

    # 未知分类：明确拒绝（400），不静默忽略
    with pytest.raises(PodRepositoryError) as excinfo:
        service.sweep_local_cache(older_than_hours=48, categories=["not-a-category"])
    assert excinfo.value.status_code == 400


def test_published_release_frees_only_host_backed_result_copies(tmp_path: Path) -> None:
    """已发布分类只释放「图床有备份」的旧结果图；未发布的结果图必须原地不动（负向对照）。"""
    service = _service(tmp_path)
    published = _store_asset(service, _png("#111111"))
    unpublished = _store_asset(service, _png("#222222"))
    _backdate_asset(service, published["asset_id"])
    _backdate_asset(service, unpublished["asset_id"])
    _insert_style_result(
        service, result_id="res-pub", pattern_asset_id=published["asset_id"], published=True
    )
    _insert_style_result(
        service, result_id="res-off", pattern_asset_id=unpublished["asset_id"], published=False
    )

    # 光清残留：两条结果仍引用资产 → 一个都不能被清
    residue_only = service.sweep_local_cache(
        older_than_hours=48, categories=["unreferenced_residue"]
    )
    assert residue_only["files_removed"] == 0
    assert (service.assets.root / published["relative_path"]).exists()
    assert (service.assets.root / unpublished["relative_path"]).exists()

    # 扫描：只有「已发布」分类有 1 个可释放
    preview = service.preview_local_cache(older_than_hours=48)
    by_key = _category_map(preview)
    assert by_key["published_release"]["files"] == 1
    assert by_key["unreferenced_residue"]["files"] == 0

    # 勾「已发布旧图」：有备份的释放，没备份的分毫不动
    released = service.sweep_local_cache(older_than_hours=48, categories=["published_release"])
    assert released["categories"]["published_release"]["files"] == 1
    assert not (service.assets.root / published["relative_path"]).exists()
    assert (service.assets.root / unpublished["relative_path"]).exists()

    with service.repository._connect() as connection:
        refs = {
            row["result_id"]: row["pattern_asset_id"]
            for row in connection.execute(
                "SELECT result_id, pattern_asset_id FROM pod_customization_style_grid_results"
            )
        }
    assert refs["res-pub"] == ""
    assert refs["res-off"] == unpublished["asset_id"]


def test_terminal_intermediates_only_freed_for_ended_batches(tmp_path: Path) -> None:
    """中间图分类只回收「已结束批次」的产物；进行中批次的引用必须原样保留（负向对照）。"""
    service = _service(tmp_path)
    ended = _store_asset(service, _png("#333333"))
    running = _store_asset(service, _png("#444444"))
    _backdate_asset(service, ended["asset_id"])
    _backdate_asset(service, running["asset_id"])
    _insert_batch_with_call(
        service, batch_id="batch-ended", status="completed", grid_asset_id=ended["asset_id"]
    )
    _insert_batch_with_call(
        service, batch_id="batch-running", status="running", grid_asset_id=running["asset_id"]
    )

    preview = service.preview_local_cache(older_than_hours=48)
    by_key = _category_map(preview)
    assert by_key["terminal_intermediates"]["files"] == 1

    swept = service.sweep_local_cache(older_than_hours=48, categories=["terminal_intermediates"])
    assert swept["categories"]["terminal_intermediates"]["files"] == 1
    assert not (service.assets.root / ended["relative_path"]).exists()
    assert (service.assets.root / running["relative_path"]).exists()

    with service.repository._connect() as connection:
        refs = {
            row["call_id"]: row["grid_asset_id"]
            for row in connection.execute(
                "SELECT call_id, grid_asset_id FROM pod_customization_generation_calls"
            )
        }
    assert refs["call-batch-ended"] == ""
    assert refs["call-batch-running"] == running["asset_id"]


def test_full_sweep_matches_preview_totals_across_categories(tmp_path: Path) -> None:
    """四类互不共享引用时：全量执行的释放量与扫描预览逐项一致、总量一致。"""
    service = _service(tmp_path)
    released_asset = _store_asset(service, _png("#aa0000"))
    ended_asset = _store_asset(service, _png("#00aa00"))
    residue_asset = _store_asset(service, _png("#0000aa"))
    for asset in (released_asset, ended_asset, residue_asset):
        _backdate_asset(service, asset["asset_id"])
    _insert_style_result(
        service, result_id="res-1", pattern_asset_id=released_asset["asset_id"], published=True
    )
    _insert_batch_with_call(
        service, batch_id="batch-done", status="completed", grid_asset_id=ended_asset["asset_id"]
    )
    orphan = _put_file(service, "scope-a/aa/old-orphan.png", _png("#f0f0f0"))
    _age_file(orphan)

    preview = service.preview_local_cache(older_than_hours=48)
    result = service.sweep_local_cache(older_than_hours=48)

    preview_by_key = _category_map(preview)
    for key in ("published_release", "terminal_intermediates", "unreferenced_residue", "disk_orphans"):
        assert preview_by_key[key]["files"] == 1, key
        assert result["categories"][key]["files"] == 1, key
        assert result["categories"][key]["bytes"] == preview_by_key[key]["bytes"], key
    assert result["files_removed"] == preview["total_files"] == 4
    assert result["bytes_freed"] == preview["total_bytes"]
    assert result["reaped_assets"] == 3
    assert result["orphan_files_removed"] == 1


def test_cross_category_shared_asset_preview_underreports_but_never_overdeletes(
    tmp_path: Path,
) -> None:
    """跨分类共享引用的资产：单独勾任一类都不会动它（预览如实不含），
    补齐另一类、失去最后引用后才被回收。

    这保证「扫描预览 ≤ 实际释放」——预览只会少报、绝不虚报；而任何一步执行
    都不会删掉仍有其它分类引用的资产（负向对照：单独勾 published 后，共享资产
    必须原地存活）。
    """
    service = _service(tmp_path)
    shared = _store_asset(service, _png("#aa0000"))
    terminal_only = _store_asset(service, _png("#00aa00"))
    published_only = _store_asset(service, _png("#0000aa"))
    for asset in (shared, terminal_only, published_only):
        _backdate_asset(service, asset["asset_id"])
    _insert_style_result(
        service, result_id="res-shared", pattern_asset_id=shared["asset_id"], published=True
    )
    _insert_style_result(
        service, result_id="res-pub", pattern_asset_id=published_only["asset_id"], published=True
    )
    _insert_batch_with_call(
        service, batch_id="batch-a", status="completed", grid_asset_id=shared["asset_id"]
    )
    _insert_batch_with_call(
        service, batch_id="batch-b", status="completed", grid_asset_id=terminal_only["asset_id"]
    )

    # 单独看：两类都只报自己的「独占」资产，共享资产谁都报不了
    preview = service.preview_local_cache(older_than_hours=48)
    by_key = _category_map(preview)
    assert by_key["published_release"]["files"] == 1
    assert by_key["terminal_intermediates"]["files"] == 1

    # 只勾「已发布」：独占的释放；共享的因终态候选仍引用而不动（负向对照）
    only_published = service.sweep_local_cache(
        older_than_hours=48, categories=["published_release"]
    )
    assert only_published["categories"]["published_release"]["files"] == 1
    assert not (service.assets.root / published_only["relative_path"]).exists()
    assert (service.assets.root / shared["relative_path"]).exists()

    # 再把「终态中间图」补上：共享资产此时才失去最后引用，被一次性回收
    then_terminal = service.sweep_local_cache(
        older_than_hours=48, categories=["terminal_intermediates"]
    )
    assert then_terminal["categories"]["terminal_intermediates"]["files"] == 2
    assert not (service.assets.root / shared["relative_path"]).exists()
    assert not (service.assets.root / terminal_only["relative_path"]).exists()


def _insert_replica_analysis(
    service: PodCustomizationService, *, analysis_id: str, source_asset_id: str, target_asset_id: str
) -> None:
    _exec_fixture_sql(
        service,
        """INSERT INTO pod_customization_replica_analyses
           (analysis_id, workspace_id, owner_user_id, source_asset_id, target_asset_id,
            analysis_version, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 'replica-v1', 'succeeded', ?, ?)""",
        (analysis_id, WORKSPACE, OWNER, source_asset_id, target_asset_id, OLD_STAMP, OLD_STAMP),
    )


def _insert_fission_analysis(
    service: PodCustomizationService, *, analysis_id: str, source_asset_id: str, target_asset_id: str
) -> None:
    _exec_fixture_sql(
        service,
        """INSERT INTO pod_customization_fission_analyses
           (analysis_id, workspace_id, owner_user_id, source_asset_id, target_asset_id,
            status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 'succeeded', ?, ?)""",
        (analysis_id, WORKSPACE, OWNER, source_asset_id, target_asset_id, OLD_STAMP, OLD_STAMP),
    )


def test_residue_sweep_keeps_assets_referenced_by_analysis_snapshots(tmp_path: Path) -> None:
    """复刻/裂变识图快照（A 图 / B 图）引用的资产不能被孤儿清扫删除。

    回归：``_ASSET_IN_USE_PREDICATES`` 早先只覆盖 replica_batches / replica_targets，
    漏了 replica_analyses 与 fission_analyses 的四列外键，清扫器一启动就撞
    ``FOREIGN KEY constraint failed``。
    """
    service = _service(tmp_path)
    replica_source = _store_asset(service, _png("#111111"))
    replica_target = _store_asset(service, _png("#222222"))
    fission_source = _store_asset(service, _png("#333333"))
    fission_target = _store_asset(service, _png("#444444"))
    unreferenced = _store_asset(service, _png("#555555"))
    assets = (replica_source, replica_target, fission_source, fission_target, unreferenced)
    for asset in assets:
        _backdate_asset(service, asset["asset_id"])
    _insert_replica_analysis(
        service,
        analysis_id="replica-analysis-1",
        source_asset_id=replica_source["asset_id"],
        target_asset_id=replica_target["asset_id"],
    )
    _insert_fission_analysis(
        service,
        analysis_id="fission-analysis-1",
        source_asset_id=fission_source["asset_id"],
        target_asset_id=fission_target["asset_id"],
    )

    result = service.sweep_local_cache(older_than_hours=48, categories=["unreferenced_residue"])

    # 只有真正无引用的那条被回收；被分析快照引用的四条全部保留。
    assert result["categories"]["unreferenced_residue"]["files"] == 1
    assert not (service.assets.root / unreferenced["relative_path"]).exists()
    for asset in assets[:4]:
        assert (service.assets.root / asset["relative_path"]).exists()
    with service.repository._connect() as connection:
        remaining = {
            row["asset_id"]
            for row in connection.execute("SELECT asset_id FROM pod_customization_assets")
        }
    assert remaining == {asset["asset_id"] for asset in assets[:4]}
