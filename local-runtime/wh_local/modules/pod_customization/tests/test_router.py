from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from wh_local.modules.pod_customization.router import create_router
from wh_local.modules.pod_customization.billing_contract import PodExecutionGrant
from wh_local.modules.product_processing.infrastructure.media import GeneratedMedia


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (320, 240), "#eee9df").save(output, "PNG")
    return output.getvalue()


class RouterRuntime:
    def __init__(self) -> None:
        self.executor = ThreadPoolExecutor(max_workers=2)

    def submit(self, function, *args, **kwargs):
        return self.executor.submit(function, *args, **kwargs)

    def calibrate_template(self, _content: bytes):
        return {
            "mask": {"x": 0.25, "y": 0.2, "width": 0.5, "height": 0.6},
            "anchor": {"x": 0.5, "y": 0.5},
        }

    def generate_listing_grid(self, _request, *, grant, call_id):
        assert grant.provider_key("wuyin")
        return GeneratedMedia(
            stage="grid_image",
            content=_png(),
            content_type="image/png",
            suffix=".png",
            provider="test",
            model="test",
            reference_count=1,
        )

    def split_listing_grid(self, _media):
        return [
            GeneratedMedia(
                stage=f"grid_image_{index}",
                content=_png(),
                content_type="image/png",
                suffix=".png",
                provider="local-split",
                model="pillow",
                reference_count=1,
            )
            for index in range(1, 5)
        ]

    def publish_listing_image(self, _media, *, namespace: str, role: str):
        return f"https://bucket.cos.ap-guangzhou.myqcloud.com/pod/{namespace}/{role}.png"


def _client(tmp_path) -> TestClient:
    class Billing:
        def freeze(self, actor, plan):
            return PodExecutionGrant("freeze-1", 1, "2099-01-01T00:00:00Z", {"wuyin": "test-key"})

        def settle(self, actor, grant, plan, outcomes):
            return None

        def regrant(self, actor, freeze_id):
            return self.freeze(actor, None)

    app = FastAPI()
    app.include_router(
        create_router(
            tmp_path / "workbench.sqlite3",
            tmp_path / "pod-assets",
            RouterRuntime(),
            billing_coordinator=Billing(),
            start_workers=False,
        )
    )
    return TestClient(app)


def test_template_upload_list_calibrate_and_asset_download_contract(tmp_path) -> None:
    client = _client(tmp_path)
    headers = {"Authorization": "Bearer dev-admin-token"}

    assert client.get("/api/pod-customization/templates").status_code == 401
    uploaded = client.post(
        "/api/pod-customization/templates",
        headers=headers,
        data={"name": "Tote scene"},
        files={"file": ("scene.png", _png(), "image/png")},
    )
    assert uploaded.status_code == 200
    template = uploaded.json()
    assert template["calibration_status"] == "pending"

    calibrated = client.post(
        f"/api/pod-customization/templates/{template['id']}/calibrate",
        headers=headers,
        json={},
    )
    assert calibrated.status_code == 200
    assert calibrated.json()["calibration"]["mask"]["width"] == 0.5
    listed = client.get("/api/pod-customization/templates", headers=headers).json()["templates"]
    assert [item["id"] for item in listed] == [template["id"]]

    downloaded = client.get(template["original_url"], headers=headers)
    assert downloaded.status_code == 200
    assert downloaded.content == _png()


def test_batch_create_list_detail_and_scene_optimization_contract(tmp_path) -> None:
    client = _client(tmp_path)
    headers = {"Authorization": "Bearer dev-admin-token"}
    template = client.post(
        "/api/pod-customization/templates",
        headers=headers,
        data={"name": "Mug scene"},
        files={"file": ("scene.png", _png(), "image/png")},
    ).json()
    client.patch(
        f"/api/pod-customization/templates/{template['id']}/calibration",
        headers=headers,
        json={
            "calibration": {
                "mask": {"x": 0.2, "y": 0.2, "width": 0.6, "height": 0.6},
                "anchor": {"x": 0.5, "y": 0.5},
            }
        },
    )

    created = client.post(
        "/api/pod-customization/batches",
        headers=headers,
        json={
            "template_id": template["id"],
            "count": 20,
            "prompt_version": "v1",
            "business_fields": {
                "product_name": "Stoneware mug", "product_category": "drinkware", "target_market": "US"
            },
            "listing_fields": {
                "declared_price": 18.5,
                "suggested_price_usd": 29.99,
                "length_cm": 30,
                "width_cm": 20,
                "height_cm": 10,
                "weight_g": 450,
                "category_id": "123456",
                "product_code_prefix": "POD-PROD",
                "sku_prefix": "POD-SKU"
            },
            "creative_prompt": "coastal geometry",
        },
    )
    assert created.status_code == 200
    batch = created.json()
    assert batch["count"] == 20
    assert batch["style_grid"] is True
    assert len(batch["items"]) == 80
    assert [(item["style_index"], item["variant_index"]) for item in batch["items"][:4]] == [
        (1, 1), (1, 2), (1, 3), (1, 4),
    ]

    listed = client.get("/api/pod-customization/batches?limit=20&offset=0", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    detail = client.get(f"/api/pod-customization/batches/{batch['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["prompt_version"] == "v1"


def test_direct_listing_trial_api_returns_one_grid_and_four_public_listing_images(tmp_path) -> None:
    client = _client(tmp_path)
    headers = {"Authorization": "Bearer dev-admin-token"}
    template = client.post(
        "/api/pod-customization/templates",
        headers=headers,
        data={"name": "WPS shirt"},
        files={"file": ("wps-shirt.png", _png(), "image/png")},
    ).json()

    response = client.post(
        "/api/pod-customization/direct-listing-trials",
        headers=headers,
        json={
            "template_id": template["id"],
            "business_fields": {"product_name": "WPS short-sleeve shirt"},
            "creative_prompt": "national park print",
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["grid"]["preview_url"].startswith("/api/pod-customization/assets/")
    assert [image["role"] for image in result["images"]] == [
        "hero",
        "detail_a",
        "detail_b",
        "lifestyle",
    ]
    stored = client.get(f"/api/pod-customization/direct-listing-trials/{result['id']}", headers=headers)
    assert stored.status_code == 200
    assert stored.json()["images"] == result["images"]
