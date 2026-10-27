"""爆款复刻 worker 消费路径：主图锚定、派生三格、重试/重启/暂停恢复与规格卡重印。

覆盖初始生成、第二次尝试、单款再生成、失败重试、暂停恢复、重启与指定款规格卡重印。
全程使用 fake runtime 捕获每款请求，绝不请求真实提供商、不真实扣费。
"""

from __future__ import annotations

import hashlib
import io
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw

from wh_local.modules.pod_customization.billing_contract import (
    PodCallPlan,
    PodExecutionGrant,
)
from wh_local.modules.pod_customization.contracts import (
    ListingFields,
    ReplicaAnalyzeRequest,
    ReplicaBatchCreate,
    ReplicaTargetCreate,
)
from wh_local.modules.pod_customization.images import split_grid_2x2
from wh_local.modules.pod_customization.replica_analysis_runtime import (
    MAIN_PROMPT_MODEL,
    MAIN_PROMPT_VERSION,
    ReplicaMainPromptResult,
)
from wh_local.modules.pod_customization.replica_context import load_frozen_plan
from wh_local.modules.pod_customization.service import PodCustomizationService
from wh_local.modules.pod_customization.title_runtime import PodTitleResult
from wh_local.modules.product_processing.infrastructure.media import (
    GeneratedMedia,
    MediaProcessingError,
)
from wh_local.session import Actor

_MAIN_PROMPT = (
    "Main image: reference image 1 is the exact product, reference image 2 supplies the pattern."
)


def _encode(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _solid(color: str, size: tuple[int, int] = (200, 200)) -> bytes:
    return _encode(Image.new("RGB", size, color))


def _pattern(index: int) -> Image.Image:
    color = ((index * 47 + 40) % 220 + 20, (index * 71 + 60) % 210 + 20, (index * 29 + 90) % 200 + 30)
    image = Image.new("RGB", (96, 96), color)
    draw = ImageDraw.Draw(image)
    for offset in range(-96, 192, 5 + index % 13):
        draw.line((offset, 0, offset - 96, 96), fill="white", width=2)
    return image


def _grid(seed: int) -> bytes:
    """2×2 母图：裂变/全定制仍走 grid 策略，四格必须互不相同。"""
    image = Image.new("RGB", (192, 192), "white")
    for pattern, position in zip(
        (_pattern(seed + index) for index in range(4)),
        ((0, 0), (96, 0), (0, 96), (96, 96)),
        strict=True,
    ):
        image.paste(pattern, position)
    return _encode(image)


def _parse_trial(trial_id: str) -> tuple[int, int, int]:
    """解析 trial id：面板请求 → (style, panel, attempt)；grid 请求 → (style, 0, attempt)。"""
    if "-panel-" not in trial_id:
        style, attempt = _parse_grid_trial(trial_id)
        return style, 0, attempt
    after = trial_id.split("-style-", 1)[1]
    style = int(after.split("-panel-", 1)[0])
    rest = after.split("-panel-", 1)[1]
    panel = int(rest.split("-attempt-", 1)[0])
    attempt = int(rest.rsplit("-attempt-", 1)[1])
    return style, panel, attempt


def _parse_grid_trial(trial_id: str) -> tuple[int, int]:
    """``{batch}-style-{n}-[call-id-]attempt-{a}`` → (style, attempt)。"""
    after = trial_id.split("-style-", 1)[1]
    style = int(after.split("-", 1)[0])
    attempt = int(trial_id.rsplit("-attempt-", 1)[1])
    return style, attempt


class CapturingReplicaRuntime:
    """捕获每款每面板的请求；可按「款」或「款+面板」配置瞬时失败。"""

    def __init__(
        self,
        *,
        fail_style_attempt1: set[int] | None = None,
        fail_style_always: set[int] | None = None,
    ) -> None:
        self.requests: list = []
        self.publications: list[tuple[str, str]] = []
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="replica-ai")
        self._lock = threading.Lock()
        self._counter = 0
        self.fail_style_attempt1 = set(fail_style_attempt1 or ())
        self.fail_style_always = set(fail_style_always or ())

    def submit(self, function, *args, **kwargs) -> Future:
        return self._executor.submit(function, *args, **kwargs)

    def generate_listing_grid(self, request, *, grant=None, call_id="") -> GeneratedMedia:
        assert grant is not None and grant.provider_key("wuyin")
        with self._lock:
            self.requests.append(request)
            self._counter += 1
            index = self._counter
        is_panel = "-panel-" in request.trial_id
        if not is_panel:
            style, _attempt = _parse_grid_trial(request.trial_id)
            if style in self.fail_style_always or (
                _attempt == 1 and style in self.fail_style_attempt1
            ):
                raise MediaProcessingError("transient provider failure", status_class="transient")
            return GeneratedMedia(
                stage="grid_image",
                content=_grid(index * 4),
                content_type="image/png",
                suffix=".png",
                provider="fake-replica",
                model=request.model_id,
                reference_count=len(request.reference_images),
            )
        style, _panel, attempt = _parse_trial(request.trial_id)
        if style in self.fail_style_always or (
            attempt == 1 and style in self.fail_style_attempt1
        ):
            raise MediaProcessingError("transient provider failure", status_class="transient")
        return GeneratedMedia(
            stage="grid_image",
            content=_solid(f"#{index:02x}{index:02x}{index:02x}"),
            content_type="image/png",
            suffix=".png",
            provider="fake-replica",
            model=request.model_id,
            reference_count=len(request.reference_images),
        )

    def split_listing_grid(self, media: GeneratedMedia) -> list[GeneratedMedia]:
        """裂变/全定制仍走 grid 策略，需要本地拆四格；复刻面板路径不会调用这里。"""
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
        digest = hashlib.sha256(media.content).hexdigest()[:12]
        return f"https://cos.example.com/{namespace}/{role}/{digest}.jpg"

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)


class FakeAnalysisRuntime:
    def __init__(self) -> None:
        self.calls = 0

    def generate_main_prompt(
        self, source_asset, target_asset, *, grant, call_id, call_ids=None, on_start=None, on_outcome=None
    ) -> ReplicaMainPromptResult:
        self.calls += 1
        if on_outcome is not None:
            on_outcome(call_id, "success")
        return ReplicaMainPromptResult(
            main_prompt=_MAIN_PROMPT,
            model=MAIN_PROMPT_MODEL,
            prompt_version=MAIN_PROMPT_VERSION,
            attempt_count=1,
        )


class CapturingTitleRuntime:
    def __init__(self) -> None:
        self.requests: list = []
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="replica-title")

    def submit(self, function, *args, **kwargs) -> Future:
        return self._executor.submit(function, *args, **kwargs)

    def generate_title(self, request, *, grant=None, call_id="", call_ids=None, on_start=None, on_outcome=None):
        self.requests.append(request)
        if on_start is not None:
            on_start(call_id)
        if on_outcome is not None:
            on_outcome(call_id, "success")
        name = request.business_fields.product_name or "POD product"
        title = (
            f"{name} Replica Series Faithful Pattern Transfer Product Listing Title "
            "For Home Storage And Daily Organization Use"
        )
        return PodTitleResult(
            title=title,
            english_title=f"{name} replica product",
            description=f"A faithful replica {name} for everyday use.",
            visual_theme=f"{name} replica visual theme",
            motif_keywords=(f"{name}-motif", "pattern"),
            color_keywords=("multicolor",),
            normalized_title=title,
            attempt_count=1,
            model="fake-ark",
            prompt_version="pod-title-v1",
        )

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)


class BillingCoordinator:
    def __init__(self) -> None:
        self.freezes: list[PodCallPlan] = []
        self.settlements: list[tuple[PodCallPlan, tuple]] = []

    def freeze(self, _actor, plan):
        self.freezes.append(plan)
        return PodExecutionGrant(
            plan.idempotency_key, 1, "2099-01-01T00:00:00Z",
            {"wuyin": "test-wuyin-key", "ark": "test-ark-key"},
        )

    def settle(self, _actor, _grant, plan, outcomes):
        self.settlements.append((plan, tuple(outcomes)))

    def regrant(self, actor, freeze_id):
        return self.freeze(actor, None)


def _actor() -> Actor:
    return Actor(id="operator-1", username="operator", role="operator", workspace_id="workspace-a")


def _listing_fields(name: str, category: str, *, spec_cells: list[list[str]] | None = None) -> ListingFields:
    return ListingFields(
        suggested_price_usd=19.99,
        category_name=category,
        skus=[{"name": name, "declared_price": 6.0, "weight_g": 300.0}],
        spec_card={
            "enabled": True,
            "style": "light",
            "corner": "bottom-right",
            "display_unit": "cm",
            "cells": spec_cells or [["SKU", "Length"], [name, "40"]],
        },
    )


def _service(tmp_path: Path, runtime, title_runtime=None, billing=None) -> PodCustomizationService:
    return PodCustomizationService(
        tmp_path / "workbench.sqlite3",
        tmp_path / "pod-assets",
        runtime,
        title_runtime=title_runtime,
        analysis_runtime=FakeAnalysisRuntime(),
        billing_coordinator=billing or BillingCoordinator(),
        start_workers=True,
    )


def _asset_bytes(service: PodCustomizationService, actor: Actor, asset_id: str) -> bytes:
    asset = service.repository.get_asset(asset_id, actor.workspace_id, actor.id)
    return service.assets.read(asset["relative_path"])


def _create_two_target_batch(service: PodCustomizationService, actor: Actor) -> dict:
    source_id = service.upload_replica_image(
        actor, role="source", filename="source.png", content=_solid("#cc1111")
    )["asset_id"]
    cushion_id = service.upload_replica_image(
        actor, role="target", filename="cushion.png", content=_solid("#11aa33")
    )["asset_id"]
    basket_id = service.upload_replica_image(
        actor, role="target", filename="basket.png", content=_solid("#2233cc")
    )["asset_id"]
    analyses = {
        asset_id: service.analyze_replica_assets(
            actor, ReplicaAnalyzeRequest(source_asset_id=source_id, target_asset_id=asset_id)
        )["analysis_id"]
        for asset_id in (cushion_id, basket_id)
    }
    request = ReplicaBatchCreate(
        client_request_id="req-worker-1",
        source_asset_id=source_id,
        targets=[
            ReplicaTargetCreate(
                target_asset_id=cushion_id,
                product_name="抱枕",
                listing_fields=_listing_fields("40cm", "家居收纳 > 抱枕"),
                analysis_id=analyses[cushion_id],
            ),
            ReplicaTargetCreate(
                target_asset_id=basket_id,
                product_name="收纳篮",
                listing_fields=_listing_fields("小号", "家居收纳 > 收纳篮"),
                analysis_id=analyses[basket_id],
            ),
        ],
    )
    batch = service.create_replica_batch(actor, request, enqueue=False)
    batch["_source_id"] = source_id  # type: ignore[index]
    batch["_cushion_id"] = cushion_id  # type: ignore[index]
    batch["_basket_id"] = basket_id  # type: ignore[index]
    return batch


def _requests_by_style(runtime: CapturingReplicaRuntime) -> dict[int, list]:
    grouped: dict[int, list] = {}
    for request in runtime.requests:
        style, _panel, _attempt = _parse_trial(request.trial_id)
        grouped.setdefault(style, []).append(request)
    return grouped


def _main_requests(runtime: CapturingReplicaRuntime) -> dict[int, object]:
    return {
        style: request
        for request in runtime.requests
        for style, panel, attempt in [_parse_trial(request.trial_id)]
        if panel == 1 and attempt == 1
    }


# --------------------------------------------------------------------------- #
# 初始生成：每款自己的主图参考图 + 三张派生
# --------------------------------------------------------------------------- #

def test_replica_initial_generation_uses_main_image_anchor(tmp_path: Path) -> None:
    runtime = CapturingReplicaRuntime()
    titles = CapturingTitleRuntime()
    service = _service(tmp_path, runtime, titles)
    actor = _actor()
    batch = _create_two_target_batch(service, actor)

    service.worker.process_batch(batch["id"])

    # 2 款 × 4 面板 = 8 次单面板调用。
    assert len(runtime.requests) == 8
    source_bytes = _asset_bytes(service, actor, batch["_source_id"])
    target_bytes = {
        1: _asset_bytes(service, actor, batch["_cushion_id"]),
        2: _asset_bytes(service, actor, batch["_basket_id"]),
    }

    by_style = _requests_by_style(runtime)
    for style, requests in by_style.items():
        panels = {
            panel: request
            for request in requests
            for _s, panel, attempt in [_parse_trial(request.trial_id)]
            if attempt == 1
        }
        assert sorted(panels) == [1, 2, 3, 4]
        main = panels[1]
        assert main.prompt == _MAIN_PROMPT
        # 主图参考图固定 [B, A]。
        assert [image.role for image in main.reference_images] == ["target_product", "pattern_source"]
        assert main.reference_images[0].content == target_bytes[style]
        assert main.reference_images[1].content == source_bytes
        assert main.template_image == b""
        for panel in (2, 3, 4):
            assert [image.role for image in panels[panel].reference_images] == ["main_view"]

    stored = service.get_batch(actor, batch["id"])
    assert stored["status"] == "completed"
    assert sorted(request.business_fields.product_name for request in titles.requests) == [
        "抱枕",
        "收纳篮",
    ]
    assert {request.style_index for request in titles.requests} == {1, 2}
    service.close()
    runtime.close()
    titles.close()


# --------------------------------------------------------------------------- #
# 第二次尝试：主图重试仍是 [B, A]，并追加 retry 指令
# --------------------------------------------------------------------------- #

def test_replica_second_attempt_reuses_main_image_inputs(tmp_path: Path) -> None:
    runtime = CapturingReplicaRuntime(fail_style_attempt1={2})
    service = _service(tmp_path, runtime)
    actor = _actor()
    batch = _create_two_target_batch(service, actor)

    service.worker.process_batch(batch["id"])

    style2 = [
        request for request in runtime.requests if _parse_trial(request.trial_id)[0] == 2
    ]
    attempts = {
        (panel, attempt)
        for request in style2
        for _s, panel, attempt in [_parse_trial(request.trial_id)]
    }
    # 每个面板都落到第二个尝试。
    assert attempts == {(panel, attempt) for panel in (1, 2, 3, 4) for attempt in (1, 2)}

    main_attempt2 = next(
        request
        for request in style2
        if _parse_trial(request.trial_id)[1:] == (1, 2)
    )
    basket_bytes = _asset_bytes(service, actor, batch["_basket_id"])
    source_bytes = _asset_bytes(service, actor, batch["_source_id"])
    assert [image.role for image in main_attempt2.reference_images] == [
        "target_product",
        "pattern_source",
    ]
    assert main_attempt2.reference_images[0].content == basket_bytes
    assert main_attempt2.reference_images[1].content == source_bytes
    assert "RETRY ATTEMPT 2 OF 2" in main_attempt2.prompt
    assert service.get_batch(actor, batch["id"])["status"] == "completed"
    service.close()
    runtime.close()


# --------------------------------------------------------------------------- #
# 单款再生成：只解析该款的主图与派生
# --------------------------------------------------------------------------- #

def test_replica_single_style_regeneration_resolves_only_that_style(tmp_path: Path) -> None:
    runtime = CapturingReplicaRuntime()
    service = _service(tmp_path, runtime)
    actor = _actor()
    batch = _create_two_target_batch(service, actor)
    service.worker.process_batch(batch["id"])
    runtime.requests.clear()

    service.regenerate_style(actor, batch["id"], 2, enqueue=True)
    with service.worker._futures_lock:
        future = service.worker._futures[("regenerate-style", f"{batch['id']}:2")]
    future.result(timeout=10)

    assert runtime.requests
    assert {_parse_trial(request.trial_id)[0] for request in runtime.requests} == {2}
    basket_bytes = _asset_bytes(service, actor, batch["_basket_id"])
    source_bytes = _asset_bytes(service, actor, batch["_source_id"])
    for request in _main_requests(runtime).values():
        assert request.prompt == _MAIN_PROMPT
        assert request.reference_images[0].content == basket_bytes
        assert request.reference_images[1].content == source_bytes
        break
    service.close()
    runtime.close()


# --------------------------------------------------------------------------- #
# 失败重试 / 重启：新服务实例重跑失败款仍按款解析
# --------------------------------------------------------------------------- #

def test_replica_failed_style_retry_after_restart_uses_per_style_context(tmp_path: Path) -> None:
    runtime = CapturingReplicaRuntime(fail_style_always={1})
    service = _service(tmp_path, runtime)
    actor = _actor()
    batch = _create_two_target_batch(service, actor)

    service.worker.process_batch(batch["id"])
    stored = service.get_batch(actor, batch["id"])
    assert stored["status"] in {"partial_failure", "failed"}
    service.close()
    runtime.close()

    # 重启：同一 DB 上的新服务实例，重试失败的第 1 款。
    recovered_runtime = CapturingReplicaRuntime()
    recovered = PodCustomizationService(
        tmp_path / "workbench.sqlite3",
        tmp_path / "pod-assets",
        recovered_runtime,
        billing_coordinator=BillingCoordinator(),
        start_workers=True,
    )
    try:
        recovered.retry_failed(
            actor, batch["id"], image_style_indices=[1], title_style_indices=[], enqueue=False
        )
        recovered.worker.process_batch_retry(batch["id"], (1,), ())
        style1 = [
            request for request in recovered_runtime.requests if _parse_trial(request.trial_id)[0] == 1
        ]
        assert style1
        cushion_bytes = _asset_bytes(recovered, actor, batch["_cushion_id"])
        source_bytes = _asset_bytes(recovered, actor, batch["_source_id"])
        for request in _main_requests(recovered_runtime).values():
            assert request.prompt == _MAIN_PROMPT
            assert request.reference_images[0].content == cushion_bytes
            assert request.reference_images[1].content == source_bytes
            break
    finally:
        recovered.close()
    recovered_runtime.close()


# --------------------------------------------------------------------------- #
# 暂停恢复：剩余款仍按自己的参考图继续
# --------------------------------------------------------------------------- #

def test_replica_resume_after_pause_uses_remaining_style_context(tmp_path: Path) -> None:
    runtime = CapturingReplicaRuntime()
    service = _service(tmp_path, runtime)
    actor = _actor()
    batch = _create_two_target_batch(service, actor)

    service.repository.request_pause(batch["id"])
    service.repository.mark_batch_paused(batch["id"], "已暂停")

    service.resume_batch(actor, batch["id"])
    import time as _time

    deadline = _time.monotonic() + 8
    while _time.monotonic() < deadline:
        if service.get_batch(actor, batch["id"])["status"] in {"completed", "partial_failure", "failed"}:
            break
        _time.sleep(0.02)

    assert runtime.requests
    source_bytes = _asset_bytes(service, actor, batch["_source_id"])
    target_bytes = {
        1: _asset_bytes(service, actor, batch["_cushion_id"]),
        2: _asset_bytes(service, actor, batch["_basket_id"]),
    }
    for request in _main_requests(runtime).values():
        style, _panel, _attempt = _parse_trial(request.trial_id)
        assert request.prompt == _MAIN_PROMPT
        assert request.reference_images[0].content == target_bytes[style]
        assert request.reference_images[1].content == source_bytes
    service.close()
    runtime.close()


# --------------------------------------------------------------------------- #
# 指定款规格卡重印：只改该款，其他款不变
# --------------------------------------------------------------------------- #

def test_replica_spec_card_reprint_updates_only_the_selected_style(tmp_path: Path) -> None:
    runtime = CapturingReplicaRuntime()
    service = _service(tmp_path, runtime)
    actor = _actor()
    batch = _create_two_target_batch(service, actor)
    service.worker.process_batch(batch["id"])

    before = service.get_batch(actor, batch["id"])
    style1_hero_before = next(
        item for item in before["items"] if item["style_index"] == 1 and item["role"] == "hero"
    )["public_url"]
    style2_before = {
        (item["style_index"], item["role"]): item["public_url"]
        for item in before["items"]
        if item["style_index"] == 2
    }
    style2_spec_before = batch["targets"][1]["listing_fields"]["spec_card"]
    mirror_spec_before = service.repository.get_batch_internal(batch["id"])["listing_fields"]["spec_card"]

    outcome = service.reprint_batch_spec_card(
        actor,
        batch["id"],
        {
            "enabled": True,
            "style": "dark",
            "corner": "top-left",
            "display_unit": "cm",
            "cells": [["SKU", "Length"], ["40cm", "50"]],
        },
        style_index=1,
    )
    assert outcome["reprinted"] == 1, outcome

    after = service.get_batch(actor, batch["id"])
    style1_hero_after = next(
        item for item in after["items"] if item["style_index"] == 1 and item["role"] == "hero"
    )["public_url"]
    assert style1_hero_after != style1_hero_before

    style2_after = {
        (item["style_index"], item["role"]): item["public_url"]
        for item in after["items"]
        if item["style_index"] == 2
    }
    assert style2_after == style2_before
    assert after["targets"][1]["listing_fields"]["spec_card"] == style2_spec_before

    assert after["targets"][0]["listing_fields"]["spec_card"]["style"] == "dark"
    mirror_after = service.repository.get_batch_internal(batch["id"])["listing_fields"]["spec_card"]
    assert mirror_after == mirror_spec_before
    service.close()
    runtime.close()


def test_frozen_plan_is_v4_for_every_style(tmp_path: Path) -> None:
    runtime = CapturingReplicaRuntime()
    service = _service(tmp_path, runtime)
    actor = _actor()
    batch = _create_two_target_batch(service, actor)
    internal = service.repository.get_batch_internal(batch["id"])
    for style_index in (1, 2):
        plan = load_frozen_plan(internal, style_index)
        assert plan.plan_version == "replica-plan-v4"
        assert plan.main_prompt == _MAIN_PROMPT
    service.close()
    runtime.close()
