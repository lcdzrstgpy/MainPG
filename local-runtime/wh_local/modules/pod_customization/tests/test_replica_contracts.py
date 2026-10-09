from __future__ import annotations

import pytest
from pydantic import ValidationError

from wh_local.modules.pod_customization.contracts import (
    POD_BATCH_MODES,
    ListingFields,
    ReplicaBatchCreate,
    ReplicaImageUploadResponse,
    ReplicaTargetCreate,
)
from wh_local.modules.pod_customization.replica_context import (
    ReplicaStyleSnapshotMissingError,
    style_product_context,
)


def _listing_fields(category: str = "家居收纳 > 抱枕") -> ListingFields:
    return ListingFields(
        suggested_price_usd=19.99,
        category_name=category,
        skus=[{"name": "40cm", "declared_price": 6.0, "weight_g": 300.0}],
        spec_card={"cells": [["SKU", "Length", "Width"], ["40cm", "40", "40"]]},
    )


def _target(asset_id: str, product_name: str, category: str = "家居收纳 > 抱枕") -> ReplicaTargetCreate:
    return ReplicaTargetCreate(
        target_asset_id=asset_id,
        product_name=product_name,
        listing_fields=_listing_fields(category),
    )


def _request(*targets: ReplicaTargetCreate, client_request_id: str = "req-1") -> ReplicaBatchCreate:
    return ReplicaBatchCreate(
        client_request_id=client_request_id,
        source_asset_id="asset-source",
        targets=list(targets) or [_target("asset-1", "抱枕")],
    )


def test_pod_batch_modes_accept_full_semi_replica() -> None:
    assert POD_BATCH_MODES == ("full", "semi", "replica")


@pytest.mark.parametrize("count", [0, 201])
def test_replica_batch_create_rejects_out_of_range_target_counts(count: int) -> None:
    with pytest.raises(ValidationError):
        ReplicaBatchCreate(
            client_request_id="req-1",
            source_asset_id="asset-source",
            targets=[_target(f"asset-{index}", f"目标{index}") for index in range(count)],
        )


@pytest.mark.parametrize("count", [1, 200])
def test_replica_batch_create_accepts_bounded_target_counts(count: int) -> None:
    request = _request(*[_target(f"asset-{index}", f"目标{index}") for index in range(count)])
    assert len(request.targets) == count


def test_replica_batch_create_rejects_non_empty_creative_prompt() -> None:
    with pytest.raises(ValidationError, match="复刻模式不接受指示词"):
        ReplicaBatchCreate(
            client_request_id="req-1",
            source_asset_id="asset-source",
            creative_prompt="重新设计图案",
            targets=[_target("asset-1", "抱枕")],
        )


def test_replica_target_requires_product_name_and_valid_listing_fields() -> None:
    with pytest.raises(ValidationError):
        ReplicaTargetCreate(
            target_asset_id="asset-1",
            product_name="",
            listing_fields=_listing_fields(),
        )
    with pytest.raises(ValidationError):
        # 缺 SKU（最小 1 条）必须被现有 ListingFields 约束拦截
        ReplicaTargetCreate(
            target_asset_id="asset-1",
            product_name="抱枕",
            listing_fields=ListingFields(
                suggested_price_usd=19.99,
                category_name="家居收纳 > 抱枕",
                skus=[],
            ),
        )
    with pytest.raises(ValidationError):
        # 申报价必须 > 0（复用现有金额约束）
        ReplicaTargetCreate(
            target_asset_id="asset-1",
            product_name="抱枕",
            listing_fields=ListingFields(
                suggested_price_usd=19.99,
                category_name="家居收纳 > 抱枕",
                skus=[{"name": "SKU", "declared_price": 0, "weight_g": 1}],
            ),
        )


def test_replica_image_upload_response_enforces_role() -> None:
    response = ReplicaImageUploadResponse(
        asset_id="asset-1", role="source", width=100, height=80, preview_url="/api/pod-customization/assets/asset-1"
    )
    assert response.model_dump()["preview_url"] == "/api/pod-customization/assets/asset-1"
    with pytest.raises(ValidationError):
        ReplicaImageUploadResponse(
            asset_id="asset-1", role="side", width=100, height=80, preview_url="u"
        )


def test_style_product_context_full_mode_returns_batch_context() -> None:
    template = {"asset_id": "template-asset"}
    business_fields = {"product_name": "帆布袋"}
    listing_fields = {"category_name": "包袋"}
    batch = {
        "mode": "full",
        "template": template,
        "business_fields": business_fields,
        "listing_fields": listing_fields,
    }
    context = style_product_context(batch, 1)
    assert context["template"] is template
    assert context["business_fields"] is business_fields
    assert context["listing_fields"] is listing_fields


def test_style_product_context_semi_mode_returns_batch_context() -> None:
    business_fields = {"design_theme": "简约"}
    batch = {"mode": "semi", "template": None, "business_fields": business_fields, "listing_fields": None}
    context = style_product_context(batch, 2)
    assert context["template"] is None
    assert context["business_fields"] is business_fields
    assert context["listing_fields"] is None


def test_style_product_context_replica_returns_frozen_target() -> None:
    target = {
        "style_index": 2,
        "asset_id": "target-asset-2",
        "asset": {"asset_id": "target-asset-2", "filename": "bag.png", "width": 100, "height": 80},
        "business_fields": {"product_name": "收纳篮", "product_category": "收纳"},
        "listing_fields": {"category_name": "收纳"},
    }
    batch = {
        "mode": "replica",
        "template_id": "anchor-template",
        "workspace_id": "ws",
        "owner_user_id": "owner",
        "created_at": "now",
        "replica_targets_by_index": {2: target},
    }
    context = style_product_context(batch, 2)
    assert context["template"]["asset_id"] == "target-asset-2"
    assert context["template"]["width"] == 100
    assert context["template"]["height"] == 80
    assert context["business_fields"]["product_name"] == "收纳篮"
    assert context["listing_fields"]["category_name"] == "收纳"


def test_style_product_context_replica_missing_snapshot_raises() -> None:
    # 少一款快照是明确失败，绝不回退首款数据。
    batch = {"mode": "replica", "replica_targets_by_index": {1: {"style_index": 1, "asset": {}}}}
    with pytest.raises(ReplicaStyleSnapshotMissingError):
        style_product_context(batch, 2)