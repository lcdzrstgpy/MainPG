"""货源成本页的草稿状态筛选：处理完成（processed）后成本数据仍必须可见。"""

from __future__ import annotations

from pathlib import Path

from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository
from wh_local.modules.product_processing.service import ProductProcessingService


def _service(tmp_path: Path) -> ProductProcessingService:
    return ProductProcessingService(
        ProductProcessingRepository(create_database("sqlite:///:memory:")),
        ProductProcessingAssets(tmp_path / "assets"),
    )


def _draft_ids(service: ProductProcessingService, status: str) -> set[int]:
    response = service.list_drafts(status, 100, 0, summary=False, workspace_id="local")
    return {int(draft["id"]) for draft in response["drafts"]}


def test_processed_draft_stays_visible_in_all_status_view(tmp_path: Path) -> None:
    service = _service(tmp_path)
    kept, _ = service.create_draft(
        {"source_type": "manual", "title": "待处理商品", "cost": 12.5},
        workspace_id="local",
    )
    done, _ = service.create_draft(
        {"source_type": "manual", "title": "已完成商品", "cost": 33.0},
        workspace_id="local",
    )
    running, _ = service.create_draft(
        {"source_type": "manual", "title": "处理中商品", "cost": 8.0},
        workspace_id="local",
    )

    service.repository.mark_drafts_status([done["id"]], "processed", workspace_id="local")
    service.repository.mark_drafts_status([running["id"]], "processing", workspace_id="local")

    # 处理后数据仍在：processed 视图能查到，且成本字段没有被清空。
    processed = service.list_drafts("processed", 100, 0, summary=False, workspace_id="local")
    assert [draft["id"] for draft in processed["drafts"]] == [done["id"]]
    assert processed["drafts"][0]["cost"] == 33.0

    # 货源成本页默认的「全部状态」必须同时包含待处理 / 处理中 / 已完成。
    assert _draft_ids(service, "all") == {kept["id"], done["id"], running["id"]}
    # 草稿池沿用原口径：不带 status 时只看待处理。
    assert _draft_ids(service, "") == {kept["id"]}


def test_all_status_view_excludes_deleted_drafts(tmp_path: Path) -> None:
    service = _service(tmp_path)
    alive, _ = service.create_draft({"source_type": "manual", "title": "保留"}, workspace_id="local")
    removed, _ = service.create_draft({"source_type": "manual", "title": "删除"}, workspace_id="local")

    service.repository.mark_drafts_status([removed["id"]], "deleted", workspace_id="local")

    assert _draft_ids(service, "all") == {alive["id"]}
