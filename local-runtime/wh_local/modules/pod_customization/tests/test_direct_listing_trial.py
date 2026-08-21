from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from wh_local.modules.pod_customization.contracts import BusinessFields, DirectListingTrialCreate
from wh_local.modules.pod_customization.billing_contract import PodExecutionGrant
from wh_local.modules.pod_customization.service import PodCustomizationService
from wh_local.modules.product_processing.infrastructure.media import GeneratedMedia, MediaProcessingError
from wh_local.session import Actor


def _png(color: str, *, size: tuple[int, int] = (2048, 2048)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, "PNG")
    return output.getvalue()


def _jpeg(color: str = "#ffffff", *, size: tuple[int, int] = (2048, 2048)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, "JPEG")
    return output.getvalue()


def _media(content: bytes, *, stage: str = "grid_image", model: str = "test-model") -> GeneratedMedia:
    return GeneratedMedia(
        stage=stage,
        content=content,
        content_type="image/png",
        suffix=".png",
        provider="test-provider",
        model=model,
        reference_count=1,
    )


class DirectTrialRuntime:
    def __init__(self, grids: list[GeneratedMedia | Exception], *, publish_failure_role: str = "") -> None:
        self.grids = list(grids)
        self.publish_failure_role = publish_failure_role
        self.requests = []
        self.published = []

    def generate_listing_grid(self, request, *, grant, call_id):
        assert grant.provider_key("wuyin") == "test-wuyin-key"
        assert call_id.endswith(f":image:{request.attempt}")
        self.requests.append(request)
        next_grid = self.grids.pop(0)
        if isinstance(next_grid, Exception):
            raise next_grid
        return next_grid

    def split_listing_grid(self, media: GeneratedMedia) -> list[GeneratedMedia]:
        if media.model == "invalid-layout":
            raise MediaProcessingError("generated four-grid image cannot be split")
        return [
            _media(_png(color, size=(1024, 1024)), stage=f"grid_image_{index}")
            for index, color in enumerate(("#bfdbfe", "#fed7aa", "#bbf7d0", "#ddd6fe"), start=1)
        ]

    def publish_listing_image(self, media: GeneratedMedia, *, namespace: str, role: str) -> str:
        self.published.append((media, namespace, role))
        if role == self.publish_failure_role:
            raise RuntimeError(f"COS upload failed for {role}")
        return f"https://bucket.cos.ap-guangzhou.myqcloud.com/pod/{namespace}/{role}.jpg"


def _actor() -> Actor:
    return Actor(id="designer-1", username="designer", role="admin", workspace_id="workspace-a")


class BillingCoordinator:
    def __init__(self) -> None:
        self.settlements = []

    def freeze(self, _actor, _plan):
        return PodExecutionGrant("freeze-1", 1, "2099-01-01T00:00:00Z", {"wuyin": "test-wuyin-key"})

    def settle(self, _actor, _grant, plan, outcomes):
        self.settlements.append((plan, tuple(outcomes)))

    def regrant(self, _actor, _freeze_id):
        return self.freeze(_actor, None)


def _service(tmp_path: Path, runtime: DirectTrialRuntime) -> PodCustomizationService:
    return PodCustomizationService(
        tmp_path / "workbench.sqlite3",
        tmp_path / "pod-assets",
        runtime,
        billing_coordinator=BillingCoordinator(),
        start_workers=False,
    )


def _request(template_id: str) -> DirectListingTrialCreate:
    return DirectListingTrialCreate(
        template_id=template_id,
        business_fields=BusinessFields(
            product_name="WPS short-sleeve shirt",
            product_category="apparel",
            target_market="US",
            design_theme="national park botanical",
            style_keywords=["vintage", "outdoor"],
        ),
        creative_prompt="breathable black-and-red short-sleeve",
    )


def test_direct_listing_trial_makes_one_reference_grid_and_returns_four_public_roles(tmp_path: Path) -> None:
    runtime = DirectTrialRuntime([_media(_png("#f8fafc"))])
    service = _service(tmp_path, runtime)
    actor = _actor()
    template = service.upload_template(actor, name="WPS shirt", filename="wps-shirt.png", content=_png("#ffffff"))

    result = service.run_direct_listing_trial(actor, _request(template["id"]))
    persisted = service.get_direct_listing_trial(actor, result["id"])

    assert len(runtime.requests) == 1
    assert runtime.requests[0].template_image == _png("#ffffff")
    assert runtime.requests[0].template_id == template["id"]
    assert "same exact product" in runtime.requests[0].prompt
    assert "The template is not a background plate" in runtime.requests[0].prompt
    assert "template's existing artwork" in runtime.requests[0].prompt
    assert "Panel 1 — HERO IMAGE" in runtime.requests[0].prompt
    assert "Panel 2 — DETAIL IMAGE A" in runtime.requests[0].prompt
    assert "Panel 3 — DETAIL IMAGE B" in runtime.requests[0].prompt
    assert "Panel 4 — LIFESTYLE IMAGE" in runtime.requests[0].prompt
    assert result["status"] == "completed"
    assert result["grid"]["preview_url"].startswith("/api/pod-customization/assets/")
    assert [image["role"] for image in result["images"]] == [
        "hero",
        "detail_a",
        "detail_b",
        "lifestyle",
    ]
    assert all(image["public_url"].startswith("https://bucket.cos.") for image in result["images"])
    assert len(runtime.published) == 4
    assert persisted == result


def test_direct_listing_trial_carries_uploaded_jpeg_content_type_to_runtime(tmp_path: Path) -> None:
    runtime = DirectTrialRuntime([_media(_png("#f8fafc"))])
    service = _service(tmp_path, runtime)
    actor = _actor()
    template = service.upload_template(actor, name="WPS shirt", filename="wps-shirt.jpg", content=_jpeg())

    service.run_direct_listing_trial(actor, _request(template["id"]))

    assert runtime.requests[0].template_content_type == "image/jpeg"


def test_direct_listing_trial_retries_only_once_for_invalid_grid_and_retains_both_grid_attempts(tmp_path: Path) -> None:
    runtime = DirectTrialRuntime([
        _media(_png("#fef3c7"), model="invalid-layout"),
        _media(_png("#f8fafc")),
    ])
    service = _service(tmp_path, runtime)
    actor = _actor()
    template = service.upload_template(actor, name="WPS shirt", filename="wps-shirt.png", content=_png("#ffffff"))

    result = service.run_direct_listing_trial(actor, _request(template["id"]))

    assert len(runtime.requests) == 2
    assert [attempt["attempt"] for attempt in result["grid_attempts"]] == [1, 2]
    assert all(attempt["preview_url"].startswith("/api/pod-customization/assets/") for attempt in result["grid_attempts"])


def test_direct_listing_trial_returns_a_fetchable_failed_trial_after_two_invalid_splits(tmp_path: Path) -> None:
    runtime = DirectTrialRuntime([
        _media(_png("#fef3c7"), model="invalid-layout"),
        _media(_png("#fee2e2"), model="invalid-layout"),
    ])
    service = _service(tmp_path, runtime)
    actor = _actor()
    template = service.upload_template(actor, name="WPS shirt", filename="wps-shirt.png", content=_png("#ffffff"))

    result = service.run_direct_listing_trial(actor, _request(template["id"]))

    assert result["status"] == "failed"
    assert result["images"] == []
    assert len(result["grid_attempts"]) == 2
    assert service.get_direct_listing_trial(actor, result["id"]) == result


def test_direct_listing_trial_reports_image_auth_error_without_persisting_or_publishing(tmp_path: Path) -> None:
    runtime = DirectTrialRuntime([
        MediaProcessingError("provider returned HTTP 401", status_code=401, status_class="non_retryable_4xx")
    ])
    service = _service(tmp_path, runtime)
    actor = _actor()
    template = service.upload_template(actor, name="WPS shirt", filename="wps-shirt.png", content=_png("#ffffff"))

    with pytest.raises(RuntimeError, match="图片服务鉴权失败.*HTTP 401"):
        service.run_direct_listing_trial(actor, _request(template["id"]))

    assert service.list_direct_listing_trials(actor) == {"trials": [], "total": 0}
    assert runtime.published == []


def test_direct_listing_trial_returns_persisted_partial_result_when_publication_fails(tmp_path: Path) -> None:
    runtime = DirectTrialRuntime([_media(_png("#f8fafc"))], publish_failure_role="detail_a")
    service = _service(tmp_path, runtime)
    actor = _actor()
    template = service.upload_template(actor, name="WPS shirt", filename="wps-shirt.png", content=_png("#ffffff"))

    result = service.run_direct_listing_trial(actor, _request(template["id"]))

    assert result["status"] == "failed"
    assert "COS upload failed for detail_a" in result["error_message"]
    assert [image["role"] for image in result["images"]] == [
        "hero",
        "detail_a",
        "detail_b",
        "lifestyle",
    ]
    assert result["images"][0]["public_url"].startswith("https://bucket.cos.")
    assert result["images"][1]["public_url"] is None
    assert service.get_direct_listing_trial(actor, result["id"]) == result
