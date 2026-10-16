from __future__ import annotations

import pytest
from pydantic import ValidationError
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import io

from wh_local.modules.pod_customization.router import create_router
from wh_local.modules.pod_customization.contracts import (
    FissionAnalyzeRequest,
    FissionBatchCreate,
    FissionImageRole,
)


class _Runtime:
    batch_workers = 1

    def __getattr__(self, _name):
        raise AssertionError("image runtime must not be called in upload tests")


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(output, "PNG")
    return output.getvalue()


def test_fission_count_accepts_one_through_ten_and_rejects_other_types() -> None:
    base = {
        "client_request_id": "request-1",
        "analysis_id": "analysis-1",
        "listing_fields": {
            "category_name": "Bags",
            "skus": [{"name": "One size", "declared_price": 10, "weight_g": 100}],
        },
    }
    for count in (1, 10):
        assert FissionBatchCreate(**base, requested_count=count).requested_count == count
    for count in (0, 11, True, "4"):
        with pytest.raises(ValidationError):
            FissionBatchCreate(**base, requested_count=count)


def test_fission_analysis_requires_two_distinct_asset_ids() -> None:
    with pytest.raises(ValidationError):
        FissionAnalyzeRequest(source_asset_id="same", target_asset_id="same")
    request = FissionAnalyzeRequest(source_asset_id="source", target_asset_id="product")
    assert request.source_asset_id == "source"
    assert request.target_asset_id == "product"


def test_fission_image_role_is_explicit() -> None:
    assert FissionImageRole.__args__ == ("source_artwork", "target_product")


def test_fission_upload_persists_role_and_rejects_unknown_role(tmp_path) -> None:
    app = FastAPI()
    router = create_router(
        tmp_path / "pod.sqlite3",
        tmp_path / "assets",
        _Runtime(),
        start_workers=False,
    )
    app.include_router(router)
    client = TestClient(app)
    headers = {"Authorization": "Bearer dev-admin-token"}

    uploaded = client.post(
        "/api/pod-customization/fission/images",
        headers=headers,
        files={"file": ("source.png", _png(), "image/png")},
        data={"role": "source_artwork"},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["role"] == "source_artwork"
    assert router.pod_customization_service.repository.get_asset(
        uploaded.json()["asset_id"], "default", "local-demo-admin"
    )["kind"] == "fission_source_artwork"

    rejected = client.post(
        "/api/pod-customization/fission/images",
        headers=headers,
        files={"file": ("unknown.png", _png(), "image/png")},
        data={"role": "target"},
    )
    assert rejected.status_code == 400
