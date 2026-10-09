from __future__ import annotations

import io
import sqlite3
import zipfile
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
import pytest

from wh_local.modules.pod_customization.router import create_router
from wh_local.modules.pod_customization.billing_contract import PodExecutionGrant
from wh_local.modules.product_processing.infrastructure.media import GeneratedMedia
from wh_local.customer.contracts import (
    CustomerAuthRejected,
    CustomerAuthUnavailable,
    CustomerBillingPermissionError,
    CustomerBillingProtocolError,
)
from wh_local.customer.remote_client import CustomerAuthClient
from wh_local.modules.pod_customization.remote_billing import RemotePodBillingCoordinator
from wh_local.modules.pod_customization.contracts import (
    BatchCreate,
    BusinessFields,
    ListingFields,
)
from wh_local.session import Actor


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (320, 240), "#eee9df").save(output, "PNG")
    return output.getvalue()


def _panel(index: int) -> bytes:
    image = Image.new("RGB", (320, 240), (40 + index * 30, 70 + index * 20, 100 + index * 15))
    draw = ImageDraw.Draw(image)
    for offset in range(-240, 500, 20 + index):
        draw.line((offset, 0, offset - 240, 240), fill="white", width=4)
    output = io.BytesIO()
    image.save(output, "PNG")
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
                content=_panel(index),
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


def _billing_error_client(tmp_path, error: Exception) -> TestClient:
    class Billing:
        def freeze(self, actor, plan):
            raise error

        def settle(self, actor, grant, plan, outcomes):
            return None

        def regrant(self, actor, freeze_id):
            raise error

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


def _post_trial_that_freezes(client: TestClient) -> object:
    headers = {"Authorization": "Bearer dev-admin-token"}
    template = client.post(
        "/api/pod-customization/templates",
        headers=headers,
        data={"name": "Billing error template"},
        files={"file": ("scene.png", _png(), "image/png")},
    ).json()
    return client.post(
        "/api/pod-customization/direct-listing-trials",
        headers=headers,
        json={"template_id": template["id"], "business_fields": {}, "creative_prompt": ""},
    )


def test_router_preserves_validated_remote_402_without_leaking_remote_detail(tmp_path) -> None:
    response = _post_trial_that_freezes(
        _billing_error_client(
            tmp_path,
            CustomerAuthRejected(
                402,
                "balance low key=LEAK https://billing.example.test/freeze?token=LEAK",
            ),
        )
    )

    assert response.status_code == 402
    assert response.json()["detail"] == "POD billing request was rejected"
    assert "LEAK" not in response.text
    assert "billing.example" not in response.text


def test_router_maps_real_remote_session_and_protocol_errors_to_stable_statuses(tmp_path) -> None:
    cases = (
        (CustomerBillingPermissionError(), 401, "POD billing authentication is required"),
        (CustomerAuthUnavailable("https://upstream.test?key=LEAK"), 503, "POD billing service is unavailable"),
        (CustomerBillingProtocolError(), 502, "POD billing service returned an invalid response"),
    )

    for index, (error, status_code, detail) in enumerate(cases):
        response = _post_trial_that_freezes(
            _billing_error_client(tmp_path / str(index), error)
        )
        assert response.status_code == status_code
        assert response.json()["detail"] == detail
        assert "LEAK" not in response.text


@pytest.mark.parametrize(
    ("remote_status", "local_status", "detail"),
    (
        (401, 401, "POD billing authentication is required"),
        (403, 403, "POD billing permission is required"),
    ),
)
def test_real_remote_client_composition_preserves_pod_auth_status_without_detail_leak(
    tmp_path,
    monkeypatch,
    remote_status: int,
    local_status: int,
    detail: str,
) -> None:
    remote_client = CustomerAuthClient("https://customer.example.test")
    monkeypatch.setattr(
        remote_client._session,
        "request",
        lambda *_args, **_kwargs: SimpleNamespace(
            status_code=remote_status,
            text='{"detail":"permission denied Authorization: Bearer LEAK token=LEAK"}',
        ),
    )
    billing = RemotePodBillingCoordinator(
        remote_client,
        lambda _actor: "live-remote-token",
    )
    app = FastAPI()
    app.include_router(
        create_router(
            tmp_path / "workbench.sqlite3",
            tmp_path / "pod-assets",
            RouterRuntime(),
            billing_coordinator=billing,
            start_workers=False,
        )
    )

    response = _post_trial_that_freezes(TestClient(app))

    assert response.status_code == local_status
    assert response.json()["detail"] == detail
    assert "LEAK" not in response.text
    assert "customer.example" not in response.text


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
                "suggested_price_usd": 29.99,
                "category_name": "家居收纳 > 杯具",
                "skus": [
                    {"name": "Default SKU", "declared_price": 18.5, "weight_g": 450}
                ],
                "spec_card": {
                    "cells": [["尺寸图", "长", "宽", "高"], ["Default SKU", "30", "20", "10"]]
                },
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

    scene = client.post(
        f"/api/pod-customization/batches/{batch['id']}/items/{batch['items'][0]['id']}/optimize-scene",
        headers=headers,
        json={"instruction": "warmer light"},
    )
    assert scene.status_code == 409
    assert scene.json()["detail"] == "POD scene optimization is not available in this release"
    unchanged = client.get(f"/api/pod-customization/batches/{batch['id']}", headers=headers).json()
    assert unchanged["items"][0]["status"] == batch["items"][0]["status"]
    item_retry = client.post(
        f"/api/pod-customization/batches/{batch['id']}/items/{batch['items'][0]['id']}/regenerate",
        headers=headers,
        json={"creative_prompt": "try again"},
    )
    assert item_retry.status_code == 409
    assert item_retry.json()["detail"] == "POD single-image regeneration is not available in this release"


def test_export_selection_patch_contract_returns_current_selection(tmp_path) -> None:
    app = FastAPI()
    router = create_router(
        tmp_path / "workbench.sqlite3",
        tmp_path / "assets",
        RouterRuntime(),
        start_workers=False,
    )
    app.include_router(router)
    client = TestClient(app)
    headers = {"Authorization": "Bearer dev-admin-token"}
    service = getattr(router, "pod_customization_service")
    actor = Actor(id="local-demo-admin", username="local-demo", role="admin", workspace_id="default")
    template = service.upload_template(actor, name="Ready", filename="scene.png", content=_png())
    service.calibrate_template(actor, template["id"])
    batch = service.create_batch(
        actor,
        BatchCreate(
            template_id=template["id"],
            count=1,
            business_fields=BusinessFields(product_name="Bag", product_category="bags"),
            listing_fields=ListingFields(
                suggested_price_usd=29.99,
                category_name="bags",
                skus=[{"name": "SKU", "declared_price": 18.5, "weight_g": 1}],
                spec_card={"cells": [["尺寸图", "长", "宽", "高"], ["SKU", "1", "1", "1"]]},
            ),
        ),
        enqueue=False,
    )
    with sqlite3.connect(service.database_path) as connection:
        rows = connection.execute(
            "SELECT result_id, variant_index FROM pod_customization_style_grid_results WHERE batch_id = ? ORDER BY variant_index",
            (batch["id"],),
        ).fetchall()
        for (result_id, _), role in zip(rows, ("hero", "detail_a", "detail_b", "lifestyle"), strict=True):
            connection.execute(
                "UPDATE pod_customization_style_grid_results SET status = 'completed' WHERE result_id = ?",
                (result_id,),
            )
            connection.execute(
                "INSERT INTO pod_customization_style_grid_publications (result_id, role, public_url, updated_at) VALUES (?, ?, ?, 'now')",
                (result_id, role, f"https://images.example.com/{role}.png"),
            )
        connection.execute(
            "UPDATE pod_customization_style_titles SET status = 'completed' WHERE batch_id = ? AND style_index = 1",
            (batch["id"],),
        )
        connection.execute("UPDATE pod_customization_batches SET status = 'completed' WHERE batch_id = ?", (batch["id"],))
    service.repository.upsert_style_copy(
        batch["id"], actor.workspace_id, actor.id, 1,
        title="Title", english_title="English", description="Description",
    )

    response = client.patch(
        f"/api/pod-customization/batches/{batch['id']}/styles/1/export-selection",
        headers=headers,
        json={"selected": False},
    )
    repeated = client.patch(
        f"/api/pod-customization/batches/{batch['id']}/styles/1/export-selection",
        headers=headers,
        json={"selected": False},
    )

    assert response.status_code == 200
    assert response.json() == {"style_index": 1, "export_selected": False}
    assert repeated.status_code == 200
    assert repeated.json() == {"style_index": 1, "export_selected": False}


@pytest.mark.parametrize(
    "payload",
    (
        {"image_style_indices": [], "title_style_indices": []},
        {"image_style_indices": [1, 1], "title_style_indices": []},
        {"image_style_indices": [1], "title_style_indices": [1]},
        {"image_style_indices": [True], "title_style_indices": []},
    ),
)
def test_batch_retry_failed_contract_rejects_invalid_selection_before_queueing(tmp_path, payload) -> None:
    client = _client(tmp_path)
    response = client.post(
        "/api/pod-customization/batches/missing/retry-failed",
        headers={"Authorization": "Bearer dev-admin-token"},
        json=payload,
    )

    assert response.status_code == 422


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


def _completed_semi_batch(tmp_path) -> tuple[TestClient, str]:
    """Create a semi batch and mark its four patterns as completed, ready to zip."""
    app = FastAPI()
    router = create_router(
        tmp_path / "workbench.sqlite3",
        tmp_path / "pod-assets",
        RouterRuntime(),
        start_workers=False,
    )
    app.include_router(router)
    client = TestClient(app)
    headers = {"Authorization": "Bearer dev-admin-token"}
    created = client.post(
        "/api/pod-customization/semi/batches",
        headers=headers,
        json={"count": 4, "business_fields": {"design_theme": "法式田园碎花"}},
    )
    assert created.status_code == 200, created.text
    batch_id = created.json()["id"]

    service = router.pod_customization_service
    stored = service.assets.save_image("default", "local-demo-admin", _panel(1))
    asset = service.repository.create_asset(
        workspace_id="default",
        owner_user_id="local-demo-admin",
        kind="direct_listing_panel",
        filename="style-1-pattern_1.png",
        relative_path=stored.relative_path,
        content_type=stored.content_type,
        byte_size=stored.byte_size,
        sha256=stored.sha256,
        width=stored.width,
        height=stored.height,
    )
    with service.repository._connect() as connection:
        connection.execute(
            """UPDATE pod_customization_style_grid_results
               SET status = 'completed', pattern_asset_id = ?, composite_asset_id = ?
               WHERE batch_id = ?""",
            (asset["asset_id"], asset["asset_id"], batch_id),
        )
    return client, batch_id


def test_semi_zip_download_encodes_chinese_filename_header(tmp_path) -> None:
    """zip 名含中文，响应头只能按 latin-1 编码，必须走 RFC 5987 的 filename*。

    直接写进 Content-Disposition 会让 Starlette 编码响应头时抛 UnicodeEncodeError，
    前端只拿到 500 且响应体不是 JSON，最终只显示「下载失败 (HTTP 500)」。
    """
    client, batch_id = _completed_semi_batch(tmp_path)
    headers = {"Authorization": "Bearer dev-admin-token"}

    response = client.get(
        f"/api/pod-customization/semi/batches/{batch_id}/download", headers=headers
    )

    assert response.status_code == 200, response.text
    disposition = response.headers["content-disposition"]
    disposition.encode("latin-1")  # 中文必须以百分号编码出现，否则 Starlette 会 500
    assert "filename*=UTF-8''" in disposition
    assert response.headers["x-pod-semi-count"] == "4"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == [
            "style_001.png",
            "style_002.png",
            "style_003.png",
            "style_004.png",
        ]


def test_semi_zip_download_rejects_a_batch_without_patterns(tmp_path) -> None:
    client = _client(tmp_path)
    headers = {"Authorization": "Bearer dev-admin-token"}
    created = client.post(
        "/api/pod-customization/semi/batches",
        headers=headers,
        json={"count": 4},
    )
    assert created.status_code == 200, created.text

    response = client.get(
        f"/api/pod-customization/semi/batches/{created.json()['id']}/download", headers=headers
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "当前批次没有可下载的图案"
