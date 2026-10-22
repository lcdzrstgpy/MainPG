from __future__ import annotations

import pytest
from pydantic import ValidationError

from wh_local.modules.pod_customization.contracts import (
    BatchCreate,
    BusinessFields,
    ListingFields,
    style_grid_call_count,
)


def listing_fields() -> ListingFields:
    # 新契约（contracts.ListingFields）：申报价/重量下沉到每个 SKU
    #（ListingSku{name, declared_price, weight_g}），长/宽/高改由可选的
    # spec_card 规格卡承载；旧的顶层 declared_price/length_cm/sku_prefix 等
    # 平铺字段已废弃（extra="forbid" 会拒绝注入）。
    return ListingFields(
        suggested_price_usd=29.99,
        category_name="bags",
        skus=[{"name": "POD-SKU", "declared_price": 18.5, "weight_g": 450}],
    )


@pytest.mark.parametrize("count", [1, 2, 10, 20, 40, 60, 100, 200])
def test_batch_contract_accepts_every_bounded_style_count(count: int) -> None:
    request = BatchCreate(
        template_id="template-1",
        count=count,
        business_fields=BusinessFields(product_name="Canvas bag", product_category="bags"),
        listing_fields=listing_fields(),
    )
    assert request.count == count
    assert style_grid_call_count(count) == count


@pytest.mark.parametrize("count", [0, 201, -1, 1.5, True])
def test_batch_contract_rejects_invalid_style_counts(count: object) -> None:
    with pytest.raises(ValidationError):
        BatchCreate(
            template_id="template-1",
            count=count,
            business_fields=BusinessFields(product_name="Canvas bag", product_category="bags"),
            listing_fields=listing_fields(),
        )


def test_batch_contract_requires_listing_category_and_safe_listing_fields() -> None:
    with pytest.raises(ValidationError, match="product_category"):
        BatchCreate(
            template_id="template-1",
            count=1,
            business_fields=BusinessFields(product_name="Canvas bag", product_category="  "),
            listing_fields=listing_fields(),
        )

    unsafe = listing_fields().model_dump()
    unsafe["sku_prefix"] = "../escape"
    with pytest.raises(ValidationError):
        ListingFields.model_validate(unsafe)


def test_column_layout_requires_a_multiple_of_four_styles() -> None:
    from pydantic import ValidationError

    from wh_local.modules.pod_customization.contracts import (
        ArtDirection,
        BatchCreate,
        BusinessFields,
    )

    def build(count: int):
        return BatchCreate(
            template_id="template-1",
            count=count,
            business_fields=BusinessFields(product_name="Canvas bag", product_category="bags"),
            listing_fields=listing_fields(),
            generation_layout="column_v1",
            art_direction=ArtDirection(scene="木质岛台", lighting="右后方侧逆光"),
        )

    assert build(4).generation_layout == "column_v1"
    assert build(200).art_direction.scene == "木质岛台"
    for invalid in (1, 2, 6, 202):
        with pytest.raises(ValidationError):
            build(invalid)
    # legacy 批次仍允许 1–200 的任意整数。
    legacy = BatchCreate(
        template_id="template-1",
        count=6,
        business_fields=BusinessFields(product_name="Canvas bag", product_category="bags"),
        listing_fields=listing_fields(),
    )
    assert legacy.generation_layout == "legacy"
