from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import func, select

from wh_local.modules.product_processing.domain.models import DailySelectionHandoffEnvelope
from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.orm import ProductDraftRow
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository
from wh_local.modules.product_processing.service import ProductProcessingService

# 同一个商品页的两种入口算出来的 source_ref 是同一个（都过 _canonical_url）。
SHARED_URL = "https://detail.1688.com/offer/660000000001.html"


def _service(tmp_path: Path) -> ProductProcessingService:
    return ProductProcessingService(
        ProductProcessingRepository(create_database("sqlite:///:memory:")),
        ProductProcessingAssets(tmp_path / "assets"),
    )


def _plugin_payload(*, candidate_id: str, source_ref: str = SHARED_URL) -> dict[str, object]:
    """插件单品采集落到服务层的载荷形状（见 _plugin_product_to_draft）。"""
    return {
        "source_type": "web_manual_capture",
        "collection_channel": "plugin_capture",
        "candidate_id": candidate_id,
        "source_ref": source_ref,
        "source_platform": "1688",
        "product_name": "Quilted laundry hamper",
        "title": "Quilted laundry hamper",
        "image_url": "https://cbu01.alicdn.com/img/main.jpg",
    }


def _handoff(handoff_id: str, *, source_url: str = SHARED_URL) -> DailySelectionHandoffEnvelope:
    candidate_id = "1688:660000000001"
    return DailySelectionHandoffEnvelope(
        handoff_id=handoff_id,
        run_id=f"run-{handoff_id}",
        candidate_id=candidate_id,
        workspace_id="local",
        payload_json=json.dumps(
            {
                "candidate": {
                    "candidate_id": candidate_id,
                    "offer_id": "660000000001",
                    "source_platform": "1688",
                    "source_url": source_url,
                    "source_title": "Quilted laundry hamper",
                },
                "images": {"main": "", "gallery": [], "detail": []},
                "skus": [],
                "attributes": {},
                "selection_metadata": {},
            },
            ensure_ascii=False,
        ),
        status="pending",
        idempotency_key=f"idempotency-{handoff_id}",
        created_at="2026-11-02T00:00:00+00:00",
    )


def _draft_count(service: ProductProcessingService) -> int:
    with service.repository.database.sessions() as session:
        return int(session.scalar(select(func.count()).select_from(ProductDraftRow)) or 0)


def test_plugin_recapture_of_same_url_reuses_the_first_draft(tmp_path: Path) -> None:
    """插件同一个商品页采两次（candidate_id 不同）只留一条草稿。"""

    service = _service(tmp_path)
    first, created_first = service.create_draft(
        _plugin_payload(candidate_id=f"plugin:1688:{SHARED_URL}"), workspace_id="local"
    )
    second, created_second = service.create_draft(
        _plugin_payload(candidate_id="plugin:1688:660000000001"), workspace_id="local"
    )

    assert created_first is True
    assert created_second is False
    assert second["id"] == first["id"]
    assert _draft_count(service) == 1


def test_plugin_capture_skips_when_daily_selection_already_pooled_the_same_product(
    tmp_path: Path,
) -> None:
    """选品先入池、插件后采同一个 URL：不新增草稿，返回选品那条。"""

    service = _service(tmp_path)
    pooled = service.consume_daily_selection_handoffs([_handoff("handoff-1")])
    assert pooled["created"] == 1
    pooled_id = pooled["drafts"][0]["id"]

    draft, created = service.create_draft(
        _plugin_payload(candidate_id="plugin:1688:660000000001"), workspace_id="local"
    )

    assert created is False
    assert draft["id"] == pooled_id
    assert draft["source_type"] == "onebound_api"
    assert _draft_count(service) == 1


def test_plugin_capture_still_creates_when_duplicates_are_explicitly_allowed(
    tmp_path: Path,
) -> None:
    """显式要求重复时仍要新建（逃生口，用于同一个商品另起一条处理配置）。"""

    service = _service(tmp_path)
    service.create_draft(
        _plugin_payload(candidate_id="plugin:1688:a"), workspace_id="local"
    )
    draft, created = service.create_draft(
        _plugin_payload(candidate_id="plugin:1688:b"),
        workspace_id="local",
        allow_duplicate_candidate=True,
    )

    assert created is True
    assert _draft_count(service) == 2


def test_plugin_capture_ignores_deleted_draft_of_the_same_url(tmp_path: Path) -> None:
    """已删除的同款草稿不算重复，重新采集要能建回来。"""

    service = _service(tmp_path)
    first, _ = service.create_draft(
        _plugin_payload(candidate_id="plugin:1688:a"), workspace_id="local"
    )
    service.delete_drafts([int(first["id"])], "local")

    draft, created = service.create_draft(
        _plugin_payload(candidate_id="plugin:1688:b"), workspace_id="local"
    )

    assert created is True
    assert draft["id"] != first["id"]
    assert _draft_count(service) == 2


def test_empty_source_ref_never_dedupes(tmp_path: Path) -> None:
    """没有来源 URL 的草稿（手工建）不能互相误判成同款。"""

    service = _service(tmp_path)
    first, created_first = service.create_draft(
        _plugin_payload(candidate_id="plugin:1688:a", source_ref=""), workspace_id="local"
    )
    second, created_second = service.create_draft(
        _plugin_payload(candidate_id="plugin:1688:b", source_ref=""), workspace_id="local"
    )

    assert created_first is True
    assert created_second is True
    assert second["id"] != first["id"]


def test_daily_selection_keeps_one_draft_per_run(tmp_path: Path) -> None:
    """选品「每个 run 一条」是刻意设计，本次去重不能顺手改掉它。"""

    service = _service(tmp_path)
    first = service.consume_daily_selection_handoffs([_handoff("handoff-1")])
    second = service.consume_daily_selection_handoffs([_handoff("handoff-2")])

    assert first["created"] == 1
    assert second["created"] == 1
    assert second["drafts"][0]["id"] != first["drafts"][0]["id"]
    assert _draft_count(service) == 2
