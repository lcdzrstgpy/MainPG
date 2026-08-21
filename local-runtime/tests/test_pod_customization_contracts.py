from __future__ import annotations

import pytest
from pydantic import ValidationError

from wh_local.modules.pod_customization.contracts import (
    BatchCreate,
    BusinessFields,
    Calibration,
    ListingFields,
    NormalizedPoint,
    NormalizedRect,
    style_grid_call_count,
)


def listing_fields() -> ListingFields:
    return ListingFields(
        declared_price=18.5,
        suggested_price_usd=29.99,
        length_cm=30,
        width_cm=20,
        height_cm=10,
        weight_g=450,
        category_id="123456",
        product_code_prefix="POD-PROD",
        sku_prefix="POD-SKU",
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


def test_calibration_must_stay_inside_normalized_canvas() -> None:
    valid = Calibration(
        mask=NormalizedRect(x=0.1, y=0.2, width=0.5, height=0.4),
        anchor=NormalizedPoint(x=0.35, y=0.4),
    )
    assert valid.mask.width == 0.5
    with pytest.raises(ValidationError):
        Calibration(
            mask=NormalizedRect(x=0.8, y=0.2, width=0.3, height=0.4),
            anchor=NormalizedPoint(x=0.5, y=0.5),
        )
