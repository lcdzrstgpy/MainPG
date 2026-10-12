"""产品处理侧「缓存清扫」测试（个人中心「存储空间」的产品处理分区）。

覆盖：扫描只读、分类计数、行级孤儿判定（绑定 / 预览 / 尺寸画布 / JSON 提及）、
磁盘反向对账（含「被 JSON 引用的复核原图绝不删」这一关键回归场景）、选择性执行、
未知分类拒绝、幂等。
"""

from __future__ import annotations

import io
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from wh_local.modules.product_processing.cache_sweep import (
    PP_CACHE_CATEGORY_ASSET_ORPHANS,
    PP_CACHE_CATEGORY_DISK_ORPHANS,
    PP_CACHE_CATEGORY_REVIEW_IMAGES,
    ProductProcessingCacheSweep,
)
from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.media_asset_orm import (
    MediaAssetRow,
    MediaBindingRow,
)
from wh_local.modules.product_processing.infrastructure.orm import (
    ProcessingTaskItemRow,
    ProcessingTaskRow,
    ProductDraftRow,
)
from wh_local.modules.product_processing.service import ProductProcessingValidationError

WORKSPACE = "local"
OLD = "2020-01-01T00:00:00.000000+00:00"


def _png(color: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (48, 48), color).save(buffer, "PNG")
    return buffer.getvalue()


def _setup(tmp_path: Path) -> tuple:
    database = create_database(f"sqlite:///{(tmp_path / 'pp.sqlite3').as_posix()}")
    assets = ProductProcessingAssets(tmp_path / "assets")
    return database, assets, ProductProcessingCacheSweep(assets, database)


def _write(assets: ProductProcessingAssets, relative: str, content: bytes) -> Path:
    target = assets.root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def _age(path: Path, seconds: float = 3 * 86400) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _insert_media_asset(
    database,
    managed_path: Path | str,
    *,
    created_at: str = OLD,
) -> str:
    path = Path(managed_path)
    with database.sessions.begin() as session:
        row = MediaAssetRow(
            workspace_id=WORKSPACE,
            origin="local_upload",
            managed_path=str(path),
            content_hash=uuid.uuid4().hex,
            content_type="image/png",
            byte_size=path.stat().st_size if path.exists() else 0,
            status="ready",
            created_at=created_at,
            updated_at=created_at,
        )
        session.add(row)
        session.flush()
        return str(row.id)


def _draft(database) -> int:
    with database.sessions.begin() as session:
        row = ProductDraftRow(workspace_id=WORKSPACE, skc=f"skc-{uuid.uuid4().hex[:8]}", status="draft")
        session.add(row)
        session.flush()
        return int(row.id)


def _bind(database, asset_id: str, draft_id: int) -> None:
    with database.sessions.begin() as session:
        session.add(
            MediaBindingRow(
                workspace_id=WORKSPACE,
                asset_id=asset_id,
                product_draft_id=draft_id,
                binding_key=uuid.uuid4().hex,
            )
        )


def _task_item_result_json(database, payload: dict) -> None:
    with database.sessions.begin() as session:
        task = ProcessingTaskRow(workspace_id=WORKSPACE, title="fixture", status="completed")
        session.add(task)
        session.flush()
        session.add(
            ProcessingTaskItemRow(
                task_id=task.id,
                skc="fixture",
                status="completed",
                result_json=json.dumps(payload),
            )
        )


def _category_map(payload: dict) -> dict:
    return {item["key"]: item for item in payload["categories"]}


def test_scan_is_read_only_and_sweep_matches_preview(tmp_path: Path) -> None:
    """扫描只读且逐项计数；随后全量执行的释放量与预览完全一致。"""
    database, assets, sweeper = _setup(tmp_path)
    row_file = _write(assets, "outputs/media-assets/workspaces/w/aa/row.png", _png("#112233"))
    stray = _write(assets, "outputs/task_9/images/draft_1/stray.png", _png("#445566"))
    _age(stray)
    asset_id = _insert_media_asset(database, row_file)

    preview = sweeper.preview_local_cache(older_than_hours=48)

    by_key = _category_map(preview)
    assert by_key[PP_CACHE_CATEGORY_ASSET_ORPHANS]["files"] == 1
    assert by_key[PP_CACHE_CATEGORY_DISK_ORPHANS]["files"] == 1
    assert preview["total_files"] == 2
    assert preview["total_bytes"] == row_file.stat().st_size + stray.stat().st_size

    # 扫描绝不产生副作用
    assert row_file.exists() and stray.exists()
    with database.sessions() as session:
        assert session.get(MediaAssetRow, asset_id) is not None

    result = sweeper.sweep_local_cache(older_than_hours=48)
    assert result["files_removed"] == preview["total_files"]
    assert result["bytes_freed"] == preview["total_bytes"]
    assert result["reaped_assets"] == 1
    assert result["orphan_files_removed"] == 1
    assert not row_file.exists() and not stray.exists()
    with database.sessions() as session:
        assert session.get(MediaAssetRow, asset_id) is None


def test_asset_orphans_respect_references_window_and_json_mentions(tmp_path: Path) -> None:
    """行级孤儿判定：绑定引用 / 窗口内 / JSON 提及三者任一成立都保留（负向对照）。"""
    database, assets, sweeper = _setup(tmp_path)
    draft_id = _draft(database)

    freed_file = _write(assets, "outputs/media-assets/workspaces/w/bb/freed.png", _png("#010101"))
    bound_file = _write(assets, "outputs/media-assets/workspaces/w/cc/bound.png", _png("#020202"))
    json_file = _write(assets, "outputs/media-assets/workspaces/w/dd/json-only.png", _png("#030303"))
    recent_file = _write(assets, "outputs/media-assets/workspaces/w/ee/recent.png", _png("#040404"))

    freed_id = _insert_media_asset(database, freed_file)
    bound_id = _insert_media_asset(database, bound_file)
    json_id = _insert_media_asset(database, json_file)
    recent_id = _insert_media_asset(database, recent_file, created_at=_now_iso())
    _bind(database, bound_id, draft_id)
    _task_item_result_json(database, {"provider_original_image_paths": [str(json_file)]})

    swept = sweeper.sweep_local_cache(
        older_than_hours=48, categories=[PP_CACHE_CATEGORY_ASSET_ORPHANS]
    )

    assert swept["categories"][PP_CACHE_CATEGORY_ASSET_ORPHANS]["files"] == 1
    assert not freed_file.exists()
    assert bound_file.exists(), "被媒体绑定引用的资产绝不删"
    assert json_file.exists(), "被任务结果 JSON 提及的资产（行与文件）绝不删"
    assert recent_file.exists(), "窗口内的资产不删"
    with database.sessions() as session:
        assert session.get(MediaAssetRow, freed_id) is None
        assert session.get(MediaAssetRow, bound_id) is not None
        assert session.get(MediaAssetRow, json_id) is not None
        assert session.get(MediaAssetRow, recent_id) is not None


def test_disk_orphans_protect_referenced_review_images_and_temporaries(tmp_path: Path) -> None:
    """磁盘对账：只删真正无引用的旧文件；被列引用 / JSON 引用 / 窗口内 / 临时文件全保留。"""
    database, assets, sweeper = _setup(tmp_path)

    stray = _write(assets, "outputs/task_1/images/draft_1/stray.png", _png("#111111"))
    _age(stray)

    referenced = _write(assets, "outputs/media-assets/workspaces/w/ff/ref.png", _png("#222222"))
    _age(referenced)
    _insert_media_asset(database, referenced)

    review = _write(
        assets, "outputs/task_2/images/draft_3/grid_image_provider_original_1.png", _png("#333333")
    )
    _age(review)
    _task_item_result_json(database, {"provider_original_image_paths": [str(review)]})

    fresh = _write(assets, "outputs/task_7/images/draft_1/fresh.png", _png("#444444"))

    in_flight = _write(assets, "outputs/task_1/images/draft_1/.writing.tmp", b"partial")
    _age(in_flight)
    manifest = _write(assets, "source-image-library/task_5_source_images.txt", b"https://x.test/a.jpg")
    _age(manifest)

    preview = sweeper.preview_local_cache(older_than_hours=48)
    assert _category_map(preview)[PP_CACHE_CATEGORY_DISK_ORPHANS]["files"] == 1

    swept = sweeper.sweep_local_cache(older_than_hours=48, categories=[PP_CACHE_CATEGORY_DISK_ORPHANS])
    assert swept["categories"][PP_CACHE_CATEGORY_DISK_ORPHANS]["files"] == 1
    assert not stray.exists()
    assert referenced.exists(), "被资产行 managed_path 引用的文件绝不删"
    assert review.exists(), "被任务结果 JSON 引用的复核原图绝不删（关键回归）"
    assert fresh.exists(), "窗口内的文件不删"
    assert in_flight.exists(), "*.tmp 是写入中的临时文件"
    assert manifest.exists(), "源图清单（辅助文件）保守保留"


def test_selective_sweep_and_unknown_category_validation(tmp_path: Path) -> None:
    """只执行勾选的分类；空选择什么都不清；未知分类明确拒绝。"""
    database, assets, sweeper = _setup(tmp_path)
    row_file = _write(assets, "outputs/media-assets/workspaces/w/gg/only-row.png", _png("#515151"))
    stray = _write(assets, "outputs/task_3/images/draft_1/stray.png", _png("#616161"))
    _age(stray)
    _insert_media_asset(database, row_file)

    empty = sweeper.sweep_local_cache(older_than_hours=48, categories=[])
    assert empty["files_removed"] == 0
    assert row_file.exists() and stray.exists()

    only_disk = sweeper.sweep_local_cache(older_than_hours=48, categories=[PP_CACHE_CATEGORY_DISK_ORPHANS])
    assert only_disk["categories"][PP_CACHE_CATEGORY_DISK_ORPHANS]["files"] == 1
    assert only_disk["categories"][PP_CACHE_CATEGORY_ASSET_ORPHANS]["files"] == 0
    assert not stray.exists()
    assert row_file.exists(), "未勾选的行级分类不得删任何资产"

    with pytest.raises(ProductProcessingValidationError):
        sweeper.sweep_local_cache(older_than_hours=48, categories=["not-a-category"])


def test_sweep_is_idempotent_on_second_run(tmp_path: Path) -> None:
    database, assets, sweeper = _setup(tmp_path)
    stray = _write(assets, "outputs/task_4/images/draft_1/stray.png", _png("#717171"))
    _age(stray)

    first = sweeper.sweep_local_cache(older_than_hours=48)
    second = sweeper.sweep_local_cache(older_than_hours=48)

    assert first["files_removed"] == 1
    assert second["files_removed"] == 0
    assert second["bytes_freed"] == 0
    assert second["reaped_assets"] == 0
    assert second["orphan_files_removed"] == 0


# ---- 历史任务的复核原图（pp_review_images） ----


def _review_image(assets: ProductProcessingAssets, relative: str, age_seconds: float) -> Path:
    path = _write(assets, relative, _png("#818181"))
    _age(path, age_seconds)
    return path


def test_review_images_category_cleans_stale_json_only_provider_originals(tmp_path: Path) -> None:
    """复核原图分类：超 30 天 + 纯 JSON 留档引用 + 复核命名 → 只删文件不改记录。"""
    database, assets, sweeper = _setup(tmp_path)
    stale = _review_image(
        assets, "outputs/task_2/images/draft_3/grid_image_provider_original_1.png", 31 * 86400
    )
    _task_item_result_json(database, {"provider_original_image_paths": [str(stale)]})

    preview = sweeper.preview_local_cache(older_than_hours=48)
    by_key = _category_map(preview)
    assert by_key[PP_CACHE_CATEGORY_REVIEW_IMAGES]["files"] == 1
    # JSON 引用挡住 disk_orphans：两分类不重叠、不重复计数
    assert by_key[PP_CACHE_CATEGORY_DISK_ORPHANS]["files"] == 0

    swept = sweeper.sweep_local_cache(
        older_than_hours=48, categories=[PP_CACHE_CATEGORY_REVIEW_IMAGES]
    )
    assert swept["categories"][PP_CACHE_CATEGORY_REVIEW_IMAGES]["files"] == 1
    assert not stale.exists()
    # 只删文件、不改 JSON 记录（记录里的路径悬空无害，业务数据不动）
    with database.sessions() as session:
        row = session.query(ProcessingTaskItemRow).one()
        assert "provider_original_image_paths" in str(row.result_json)


def test_review_images_never_touch_column_referenced_or_renamed_or_fresh_files(tmp_path: Path) -> None:
    """负向对照：列引用的 / 命名不符的 / 未超 30 天的，一个都不删。"""
    database, assets, sweeper = _setup(tmp_path)
    draft_id = _draft(database)

    # ① 被 *_path 列（媒体绑定 → 资产行 managed_path）指着的复核命名文件
    column_referenced = _review_image(
        assets, "outputs/task_2/images/draft_1/grid_image_provider_original_2.png", 40 * 86400
    )
    _insert_media_asset(database, column_referenced)
    _task_item_result_json(database, {"provider_original_image_paths": [str(column_referenced)]})

    # ② 纯 JSON 引用但命名不符（不是复核原图）
    other_name = _review_image(assets, "outputs/task_2/images/draft_1/other_keep.png", 40 * 86400)
    _task_item_result_json(database, {"provider_original_image_paths": [str(other_name)]})

    # ③ 纯 JSON 引用 + 复核命名但没超 30 天
    fresh = _review_image(
        assets, "outputs/task_2/images/draft_1/grid_image_provider_original_3.png", 3 * 86400
    )
    _task_item_result_json(database, {"provider_original_image_paths": [str(fresh)]})

    swept = sweeper.sweep_local_cache(
        older_than_hours=48, categories=[PP_CACHE_CATEGORY_REVIEW_IMAGES]
    )
    assert swept["categories"][PP_CACHE_CATEGORY_REVIEW_IMAGES]["files"] == 0
    assert column_referenced.exists(), "被业务字段引用的文件绝不删"
    assert other_name.exists(), "非复核原图命名的 JSON 留档文件不归本分类"
    assert fresh.exists(), "30 天保留期内的复核原图不删"
    _ = draft_id  # 夹具完整性：绑定表存在即可


def test_review_images_not_touched_without_explicit_selection(tmp_path: Path) -> None:
    """不勾选复核原图分类时，即便全量清扫其它分类也绝不碰它（区分好）。"""
    database, assets, sweeper = _setup(tmp_path)
    stale = _review_image(
        assets, "outputs/task_2/images/draft_3/grid_image_provider_original_4.png", 31 * 86400
    )
    _task_item_result_json(database, {"provider_original_image_paths": [str(stale)]})

    swept = sweeper.sweep_local_cache(
        older_than_hours=48,
        categories=[PP_CACHE_CATEGORY_ASSET_ORPHANS, PP_CACHE_CATEGORY_DISK_ORPHANS],
    )
    assert swept["files_removed"] == 0
    assert stale.exists(), "未勾选 pp_review_images 时复核原图必须存活"

    checked = sweeper.sweep_local_cache(
        older_than_hours=48, categories=[PP_CACHE_CATEGORY_REVIEW_IMAGES]
    )
    assert checked["categories"][PP_CACHE_CATEGORY_REVIEW_IMAGES]["files"] == 1
    assert not stale.exists()
