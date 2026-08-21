from __future__ import annotations

import hashlib
import io
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw

from wh_local.modules.pod_customization.contracts import (
    BatchCreate,
    BusinessFields,
    Calibration,
    ListingFields,
    NormalizedPoint,
    NormalizedRect,
)
from wh_local.modules.pod_customization.billing_contract import PodExecutionGrant
from wh_local.modules.pod_customization.images import PatternQualityGate
from wh_local.modules.pod_customization.service import PodCustomizationService
from wh_local.modules.product_processing.infrastructure.media import GeneratedMedia
from wh_local.session import Actor


def _encode(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _pattern(index: int, *, text_error: bool = False) -> Image.Image:
    color = ((index * 47 + 40) % 220 + 20, (index * 71 + 60) % 210 + 20, (index * 29 + 90) % 200 + 30)
    image = Image.new("RGB", (96, 96), color)
    draw = ImageDraw.Draw(image)
    gap = 5 + index % 13
    for offset in range(-96, 192, gap):
        draw.line((offset, 0, offset - 96, 96), fill=(255 - color[0], 255 - color[1], 255 - color[2]), width=2)
    draw.ellipse((20 + index % 10, 20, 60, 60 + index % 7), outline="white", width=2)
    if text_error:
        draw.rectangle((0, 0, 12, 12), fill="black")
    return image


def _grid(patterns: list[Image.Image]) -> bytes:
    assert len(patterns) == 4
    image = Image.new("RGB", (192, 192), "white")
    for pattern, position in zip(patterns, ((0, 0), (96, 0), (0, 96), (96, 96)), strict=True):
        image.paste(pattern, position)
    return _encode(image)


class FakePodRuntime:
    def __init__(
        self,
        grids: list[bytes | Exception],
        *,
        optimized: bytes | None = None,
        publish_failures: dict[str, int] | None = None,
    ) -> None:
        self.grids = list(grids)
        self.optimized = optimized
        self.publish_failures = dict(publish_failures or {})
        self.requests = []
        self.optimization_requests = []
        self.publications: list[tuple[str, str]] = []
        self.executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="test-pod-ai")

    def submit(self, function, *args, **kwargs) -> Future:
        return self.executor.submit(function, *args, **kwargs)

    def generate_pattern_grid(self, request, *, grant=None, call_id="") -> bytes:
        self.requests.append(request)
        if not self.grids:
            raise RuntimeError("no fake grid remains")
        result = self.grids.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def generate_listing_grid(self, request, *, grant=None, call_id="") -> GeneratedMedia:
        assert grant is not None and grant.provider_key("wuyin")
        self.requests.append(request)
        if not self.grids:
            raise RuntimeError("no fake listing grid remains")
        result = self.grids.pop(0)
        if isinstance(result, Exception):
            raise result
        return GeneratedMedia(
            stage="grid_image",
            content=result,
            content_type="image/png",
            suffix=".png",
            provider="fake-listing",
            model=request.model_id,
            reference_count=1,
        )

    def split_listing_grid(self, media: GeneratedMedia) -> list[GeneratedMedia]:
        from wh_local.modules.pod_customization.images import split_grid_2x2

        return [
            GeneratedMedia(
                stage=f"grid_image_{index}",
                content=content,
                content_type="image/png",
                suffix=".png",
                provider=media.provider,
                model=media.model,
                reference_count=1,
            )
            for index, content in enumerate(split_grid_2x2(media.content), start=1)
        ]

    def publish_listing_image(self, media: GeneratedMedia, *, namespace: str, role: str) -> str:
        self.publications.append((namespace, role))
        remaining_failures = self.publish_failures.get(role, 0)
        if remaining_failures:
            self.publish_failures[role] = remaining_failures - 1
            raise RuntimeError(f"configured publication failure for {role}")
        digest = hashlib.sha256(media.content).hexdigest()[:12]
        return f"https://cos.example.com/{namespace}/{role}/{digest}.png"

    def optimize_scene(self, request, *, grant=None, call_id="") -> bytes:
        self.optimization_requests.append(request)
        if self.optimized is None:
            raise RuntimeError("scene optimization was not configured")
        return self.optimized

    def close(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=True)


class ListingOnlyRuntime(FakePodRuntime):
    def generate_pattern_grid(self, _request, *, grant=None, call_id="") -> bytes:
        raise AssertionError("style-grid v2 must use the reference-locked direct listing runtime")


def _actor() -> Actor:
    return Actor(id="designer-1", username="designer", role="admin", workspace_id="workspace-a")


class BillingCoordinator:
    def __init__(self) -> None:
        self.settlements = []

    def freeze(self, _actor, _plan):
        return PodExecutionGrant(
            "freeze-1", 1, "2099-01-01T00:00:00Z", {"wuyin": "test-wuyin-key", "ark": "test-ark-key"}
        )

    def settle(self, _actor, _grant, plan, outcomes):
        self.settlements.append((plan, tuple(outcomes)))

    def regrant(self, actor, freeze_id):
        return self.freeze(actor, None)


class FailingSettlementCoordinator(BillingCoordinator):
    def settle(self, _actor, _grant, _plan, _outcomes):
        raise OSError("billing network unavailable")


def _service(tmp_path: Path, runtime: FakePodRuntime, billing=None) -> PodCustomizationService:
    def inspect_text(content: bytes) -> list[str]:
        image = Image.open(io.BytesIO(content)).convert("RGB")
        return ["SALE"] if image.getpixel((2, 2)) == (0, 0, 0) else []

    return PodCustomizationService(
        tmp_path / "workbench.sqlite3",
        tmp_path / "pod-assets",
        runtime,
        billing_coordinator=billing or BillingCoordinator(),
        quality_gate=PatternQualityGate(text_inspector=inspect_text),
        start_workers=True,
    )


def test_worker_without_in_memory_grant_pauses_for_billing_auth(tmp_path: Path) -> None:
    runtime = ListingOnlyRuntime([_grid([_pattern(index) for index in range(4)])])
    service = PodCustomizationService(
        tmp_path / "workbench.sqlite3",
        tmp_path / "pod-assets",
        runtime,
        quality_gate=PatternQualityGate(text_inspector=lambda _content: []),
        start_workers=True,
    )
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request_for_test(template["id"]), enqueue=False)

    service.worker.process_batch(batch["id"])

    assert service.get_batch(actor, batch["id"])["status"] == "billing_auth_required"
    assert runtime.requests == []
    service.close()
    runtime.close()


def test_settlement_network_failure_moves_batch_to_pending(tmp_path: Path) -> None:
    runtime = ListingOnlyRuntime([_grid([_pattern(index) for index in range(4)])])
    service = _service(tmp_path, runtime, FailingSettlementCoordinator())
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request_for_test(template["id"]), enqueue=False)

    service.worker.process_batch(batch["id"])

    stored = service.get_batch(actor, batch["id"])
    assert stored["status"] == "settlement_pending"
    assert "billing network unavailable" in stored["error_message"]
    service.close()
    runtime.close()


def _batch_request_for_test(template_id: str) -> BatchCreate:
    return BatchCreate(
        template_id=template_id,
        count=1,
        prompt_version="v1",
        business_fields=BusinessFields(product_name="Tote bag", product_category="bags"),
        listing_fields=ListingFields(
            declared_price=18.5,
            suggested_price_usd=29.99,
            length_cm=30,
            width_cm=20,
            height_cm=10,
            weight_g=450,
            category_id="123456",
            product_code_prefix="POD-PROD",
            sku_prefix="POD-SKU",
        ),
    )


def _ready_template(service: PodCustomizationService, actor: Actor) -> dict:
    scene = Image.new("RGB", (240, 200), "#e9ecef")
    template = service.upload_template(actor, name="Fixed tote scene", filename="scene.png", content=_encode(scene))
    return service.update_template_calibration(
        actor,
        template["id"],
        Calibration(
            mask=NormalizedRect(x=0.25, y=0.2, width=0.5, height=0.6),
            anchor=NormalizedPoint(x=0.5, y=0.5),
        ),
    )


def _create_batch(
    service: PodCustomizationService,
    actor: Actor,
    template_id: str,
    *,
    count: int = 20,
) -> dict:
    return service.create_batch(
        actor,
        BatchCreate(
            template_id=template_id,
            count=count,
            prompt_version="v1",
            business_fields=BusinessFields(
                product_name="Tote bag", product_category="bags", design_theme="modern botanical"
            ),
            listing_fields=ListingFields(
                declared_price=18.5,
                suggested_price_usd=29.99,
                length_cm=30,
                width_cm=20,
                height_cm=10,
                weight_g=450,
                category_id="123456",
                product_code_prefix="POD-PROD",
                sku_prefix="POD-SKU",
            ),
            creative_prompt="bold but uncluttered",
        ),
        enqueue=False,
    )


def test_worker_makes_one_initial_grid_call_per_style_and_keeps_four_results_together(tmp_path: Path) -> None:
    patterns = [_pattern(index) for index in range(80)]
    runtime = ListingOnlyRuntime([_grid(patterns[index:index + 4]) for index in range(0, 80, 4)])
    service = _service(tmp_path, runtime)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = _create_batch(service, actor, template["id"])

    service.worker.process_batch(batch["id"])
    stored = service.get_batch(actor, batch["id"])

    assert len(runtime.requests) == 20
    assert all(request.template_image for request in runtime.requests)
    assert [request.attempt for request in runtime.requests] == [1] * 20
    assert len({request.prompt for request in runtime.requests}) == 20
    assert all("Style creative signature: STYLE-" in request.prompt for request in runtime.requests)
    assert stored["status"] == "completed"
    assert stored["completed_count"] == 20
    assert stored["style_grid"] is True
    assert len(stored["items"]) == 80
    assert [(item["style_index"], item["variant_index"]) for item in stored["items"][:4]] == [
        (1, 1), (1, 2), (1, 3), (1, 4),
    ]
    assert all(item["status"] == "completed" for item in stored["items"])
    assert [item["role"] for item in stored["items"][:4]] == ["hero", "detail_a", "detail_b", "lifestyle"]
    assert all(item["public_url"].startswith("https://cos.example.com/") for item in stored["items"])
    assert len(runtime.publications) == 80
    assert all(item["pattern_fingerprint"] for item in service.repository.get_batch_internal(batch["id"])["items"])
    assert len({item["pattern_fingerprint"] for item in service.repository.get_batch_internal(batch["id"])["items"][:4]}) == 1
    assert all(
        item["composite_preview_url"].startswith("/api/pod-customization/assets/")
        for item in stored["items"]
    )
    assert all(
        item["composite_download_url"].startswith("/api/pod-customization/assets/")
        for item in stored["items"]
    )
    service.close()
    runtime.close()


def test_style_grid_retries_one_generation_failure_only_once(tmp_path: Path) -> None:
    first = [_pattern(index) for index in range(4)]
    retry = [_pattern(index) for index in range(20, 24)]
    runtime = ListingOnlyRuntime([RuntimeError("temporary generation failure"), _grid(first), _grid(retry)])
    service = _service(tmp_path, runtime)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = _create_batch(service, actor, template["id"], count=2)

    service.worker.process_batch(batch["id"])
    stored = service.get_batch(actor, batch["id"])

    assert len(runtime.requests) == 3
    assert sorted(request.attempt for request in runtime.requests) == [1, 1, 2]
    assert sum("RETRY ATTEMPT 2 OF 2" in request.prompt for request in runtime.requests) == 1
    assert stored["status"] == "completed"
    assert stored["completed_count"] == 2
    service.close()
    runtime.close()


def test_style_grid_retries_a_duplicate_detail_panel_with_a_new_design(tmp_path: Path) -> None:
    shared_detail = _pattern(70)
    near_duplicate_detail = shared_detail.copy()
    near_duplicate_detail.putpixel((95, 95), (1, 2, 3))
    first = _grid([_pattern(1), shared_detail, _pattern(2), _pattern(3)])
    duplicate = _grid([_pattern(4), near_duplicate_detail, _pattern(5), _pattern(6)])
    retry = _grid([_pattern(7), _pattern(71), _pattern(8), _pattern(9)])
    runtime = ListingOnlyRuntime([first, duplicate, retry])
    service = _service(tmp_path, runtime)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = _create_batch(service, actor, template["id"], count=2)

    service.worker.process_batch(batch["id"])
    stored = service.get_batch(actor, batch["id"])

    assert len(runtime.requests) == 3
    assert sorted(request.attempt for request in runtime.requests) == [1, 1, 2]
    assert stored["status"] == "completed"
    fingerprints = [
        item["pattern_fingerprint"]
        for item in service.repository.get_batch_internal(batch["id"])["items"]
        if item["variant_index"] == 2
    ]
    assert len(set(fingerprints)) == 2
    service.close()
    runtime.close()


def test_style_grid_stops_after_second_generation_failure(tmp_path: Path) -> None:
    runtime = ListingOnlyRuntime([RuntimeError("first failure"), RuntimeError("second failure")])
    service = _service(tmp_path, runtime)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = _create_batch(service, actor, template["id"], count=1)

    service.worker.process_batch(batch["id"])
    stored = service.get_batch(actor, batch["id"])

    assert len(runtime.requests) == 2
    assert [request.attempt for request in runtime.requests] == [1, 2]
    assert stored["status"] == "failed"
    assert stored["failed_count"] == 1
    service.close()
    runtime.close()


def test_style_grid_retries_publication_without_regenerating_the_grid(tmp_path: Path) -> None:
    runtime = ListingOnlyRuntime(
        [_grid([_pattern(index) for index in range(4)])],
        publish_failures={"detail_a": 1},
    )
    service = _service(tmp_path, runtime)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = _create_batch(service, actor, template["id"], count=1)

    service.worker.process_batch(batch["id"])
    stored = service.get_batch(actor, batch["id"])

    assert len(runtime.requests) == 1
    assert len(runtime.publications) == 5
    assert stored["status"] == "completed"
    assert stored["completed_count"] == 1
    service.close()
    runtime.close()


def test_worker_retries_failed_style_once_without_moving_results_between_styles(tmp_path: Path) -> None:
    patterns = [_pattern(index) for index in range(76)]
    grids: list[bytes | Exception] = [
        _grid(patterns[index:index + 4]) for index in range(0, 76, 4)
    ]
    grids.append(RuntimeError("listing request failed"))
    runtime = FakePodRuntime(grids)
    service = _service(tmp_path, runtime)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = _create_batch(service, actor, template["id"])

    service.worker.process_batch(batch["id"])
    stored = service.get_batch(actor, batch["id"])
    assert len(runtime.requests) == 21
    assert [request.attempt for request in runtime.requests].count(1) == 20
    assert [request.attempt for request in runtime.requests].count(2) == 1
    assert stored["refill_call_count"] == 0
    assert stored["status"] == "partial_failure"
    assert stored["completed_count"] == 19
    assert stored["failed_count"] == 1
    assert not any(item["status"] == "awaiting_selection" for item in stored["items"])
    service.close()
    runtime.close()


def test_single_item_scene_optimization_is_optional_and_preserves_pattern_asset(tmp_path: Path) -> None:
    patterns = [_pattern(index) for index in range(80)]
    optimized = _encode(Image.new("RGB", (240, 200), "#6d597a"))
    runtime = FakePodRuntime(
        [_grid(patterns[index:index + 4]) for index in range(0, 80, 4)],
        optimized=optimized,
    )
    service = _service(tmp_path, runtime)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = _create_batch(service, actor, template["id"])
    service.worker.process_batch(batch["id"])
    before = service.get_batch(actor, batch["id"])["items"][0]

    service.optimize_scene(actor, batch["id"], before["id"], instruction="warmer daylight", enqueue=False)
    service.worker.optimize_scene(batch["id"], before["id"], "warmer daylight")
    after = service.get_batch(actor, batch["id"])["items"][0]

    assert len(runtime.optimization_requests) == 1
    assert after["scene_optimized"] is True
    assert after["status"] == "completed"
    assert after["pattern_preview_url"] == before["pattern_preview_url"]
    assert after["composite_preview_url"] != before["composite_preview_url"]
    service.close()
    runtime.close()


def test_whole_style_regeneration_replaces_exactly_its_four_results_with_one_request(tmp_path: Path) -> None:
    patterns = [_pattern(index) for index in range(80)]
    replacement_grid = _grid([_pattern(index) for index in range(100, 104)])
    runtime = FakePodRuntime(
        [*[_grid(patterns[index:index + 4]) for index in range(0, 80, 4)], replacement_grid]
    )
    service = _service(tmp_path, runtime)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = _create_batch(service, actor, template["id"])
    service.worker.process_batch(batch["id"])
    before_items = service.get_batch(actor, batch["id"])["items"]
    target_style = 2
    target_items = before_items[4:8]

    claimed = service.regenerate_style(
        actor,
        batch["id"],
        target_style,
        creative_prompt="smaller botanical elements",
        enqueue=False,
    )
    service.worker.regenerate_style(batch["id"], target_style, "smaller botanical elements")
    after_items = service.get_batch(actor, batch["id"])["items"]

    assert all(item["status"] == "generating_pattern" for item in claimed["results"])
    assert all(item["status"] == "completed" for item in after_items[4:8])
    assert [item["pattern_preview_url"] for item in after_items[4:8]] != [item["pattern_preview_url"] for item in target_items]
    assert [item["composite_preview_url"] for item in after_items[4:8]] != [item["composite_preview_url"] for item in target_items]
    assert [item["pattern_preview_url"] for index, item in enumerate(after_items) if not 4 <= index < 8] == [
        item["pattern_preview_url"] for index, item in enumerate(before_items) if not 4 <= index < 8
    ]
    assert "-style-2-" in runtime.requests[-1].trial_id
    assert runtime.requests[-1].attempt == 1
    service.close()
    runtime.close()
