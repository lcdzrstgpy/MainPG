"""爆款复刻 worker 消费路径：按款解析参考图、标题上下文与规格卡。

覆盖初始生成、第二次尝试、单款再生成、失败重试、暂停恢复、重启与指定款规格卡重印。
全程使用 fake runtime 捕获每款请求，绝不请求真实提供商、不真实扣费。
"""

from __future__ import annotations

import hashlib
import io
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from wh_local.modules.pod_customization.billing_contract import (
    PodCallPlan,
    PodExecutionGrant,
)
from wh_local.modules.pod_customization.contracts import (
    ListingFields,
    ReplicaBatchCreate,
    ReplicaTargetCreate,
)
from wh_local.modules.pod_customization.images import split_grid_2x2
from wh_local.modules.pod_customization.service import PodCustomizationService
from wh_local.modules.pod_customization.title_runtime import PodTitleResult
from wh_local.modules.product_processing.infrastructure.media import (
    GeneratedMedia,
    MediaProcessingError,
)
from wh_local.session import Actor


# --------------------------------------------------------------------------- #
# 图片工具
# --------------------------------------------------------------------------- #

def _encode(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _solid(color: str, size: tuple[int, int] = (200, 160)) -> bytes:
    return _encode(Image.new("RGB", size, color))


def _pattern(index: int) -> Image.Image:
    color = ((index * 47 + 40) % 220 + 20, (index * 71 + 60) % 210 + 20, (index * 29 + 90) % 200 + 30)
    image = Image.new("RGB", (96, 96), color)
    draw = ImageDraw.Draw(image)
    for offset in range(-96, 192, 5 + index % 13):
        draw.line((offset, 0, offset - 96, 96), fill="white", width=2)
    return image


def _grid(seed: int) -> bytes:
    image = Image.new("RGB", (192, 192), "white")
    for pattern, position in zip(
        (_pattern(seed + index) for index in range(4)),
        ((0, 0), (96, 0), (0, 96), (96, 96)),
        strict=True,
    ):
        image.paste(pattern, position)
    return _encode(image)


def _parse_trial(trial_id: str) -> tuple[int, int]:
    after = trial_id.split("-style-", 1)[1]
    style = int(after.split("-", 1)[0])
    attempt = int(trial_id.rsplit("-attempt-", 1)[1])
    return style, attempt


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


# --------------------------------------------------------------------------- #
# fake runtime
# --------------------------------------------------------------------------- #

class CapturingReplicaRuntime:
    """捕获每款生图请求；可按「款+尝试」配置一次瞬时失败以触发第二次尝试。"""

    def __init__(self, *, fail_style_attempt1: set[int] | None = None) -> None:
        self.requests: list = []
        self.publications: list[tuple[str, str]] = []
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="replica-ai")
        self._lock = threading.Lock()
        self._counter = 0
        self.fail_style_attempt1 = set(fail_style_attempt1 or ())

    def submit(self, function, *args, **kwargs) -> Future:
        return self._executor.submit(function, *args, **kwargs)

    def _next_grid(self) -> bytes:
        with self._lock:
            self._counter += 1
            return _grid(self._counter * 4)

    def generate_listing_grid(self, request, *, grant=None, call_id="") -> GeneratedMedia:
        assert grant is not None and grant.provider_key("wuyin")
        with self._lock:
            self.requests.append(request)
        style, attempt = _parse_trial(request.trial_id)
        if attempt == 1 and style in self.fail_style_attempt1:
            raise MediaProcessingError("transient provider failure", status_class="transient")
        return GeneratedMedia(
            stage="grid_image",
            content=self._next_grid(),
            content_type="image/png",
            suffix=".png",
            provider="fake-replica",
            model=request.model_id,
            reference_count=len(request.reference_images),
        )

    def split_listing_grid(self, media: GeneratedMedia) -> list[GeneratedMedia]:
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
            "freeze-1", 1, "2099-01-01T00:00:00Z",
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
    request = ReplicaBatchCreate(
        client_request_id="req-worker-1",
        source_asset_id=source_id,
        targets=[
            ReplicaTargetCreate(
                target_asset_id=cushion_id,
                product_name="抱枕",
                listing_fields=_listing_fields("40cm", "家居收纳 > 抱枕"),
            ),
            ReplicaTargetCreate(
                target_asset_id=basket_id,
                product_name="收纳篮",
                listing_fields=_listing_fields("小号", "家居收纳 > 收纳篮"),
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
        style, _attempt = _parse_trial(request.trial_id)
        grouped.setdefault(style, []).append(request)
    return grouped


# --------------------------------------------------------------------------- #
# 初始生成：每款自己的样图 + 白底图 + 字段
# --------------------------------------------------------------------------- #

def test_replica_initial_generation_uses_per_style_references_and_fields(tmp_path: Path) -> None:
    runtime = CapturingReplicaRuntime()
    titles = CapturingTitleRuntime()
    service = _service(tmp_path, runtime, titles)
    actor = _actor()
    batch = _create_two_target_batch(service, actor)

    service.worker.process_batch(batch["id"])

    assert len(runtime.requests) == 2
    source_bytes = _asset_bytes(service, actor, batch["_source_id"])
    cushion_bytes = _asset_bytes(service, actor, batch["_cushion_id"])
    basket_bytes = _asset_bytes(service, actor, batch["_basket_id"])
    assert cushion_bytes != basket_bytes

    by_style = _requests_by_style(runtime)
    expected_target = {1: cushion_bytes, 2: basket_bytes}
    expected_name = {1: "抱枕", 2: "收纳篮"}
    for style, requests in by_style.items():
        assert len(requests) == 1
        request = requests[0]
        # 固定顺序 [pattern_source, target_product]，两张图各按内容寻址。
        assert [image.role for image in request.reference_images] == [
            "pattern_source",
            "target_product",
        ]
        assert request.reference_images[0].content == source_bytes
        assert request.reference_images[1].content == expected_target[style]
        # 复刻不使用全定制的单模板字段。
        assert request.template_image == b""
        # prompt 来自该款业务字段，绝不含样图产品名。
        assert f"Product name: {expected_name[style]}" in request.prompt
        assert request.attempt == 1

    stored = service.get_batch(actor, batch["id"])
    assert stored["status"] == "completed"
    # 标题上下文同样按款解析产品名。
    assert sorted(request.business_fields.product_name for request in titles.requests) == [
        "抱枕",
        "收纳篮",
    ]
    assert {request.style_index for request in titles.requests} == {1, 2}
    service.close()
    runtime.close()
    titles.close()


# --------------------------------------------------------------------------- #
# 第二次尝试：同一款自己的参考图与 attempt=2 提示词
# --------------------------------------------------------------------------- #

def test_replica_second_attempt_reuses_this_styles_reference_images(tmp_path: Path) -> None:
    runtime = CapturingReplicaRuntime(fail_style_attempt1={2})
    service = _service(tmp_path, runtime)
    actor = _actor()
    batch = _create_two_target_batch(service, actor)

    service.worker.process_batch(batch["id"])

    style2 = [
        request for request in runtime.requests if _parse_trial(request.trial_id)[0] == 2
    ]
    assert sorted(_parse_trial(request.trial_id)[1] for request in style2) == [1, 2]
    basket_bytes = _asset_bytes(service, actor, batch["_basket_id"])
    source_bytes = _asset_bytes(service, actor, batch["_source_id"])
    for request in style2:
        assert [image.role for image in request.reference_images] == [
            "pattern_source",
            "target_product",
        ]
        assert request.reference_images[0].content == source_bytes
        assert request.reference_images[1].content == basket_bytes
    attempt2 = next(request for request in style2 if _parse_trial(request.trial_id)[1] == 2)
    assert "RETRY ATTEMPT 2 OF 2" in attempt2.prompt
    assert service.get_batch(actor, batch["id"])["status"] == "completed"
    service.close()
    runtime.close()


# --------------------------------------------------------------------------- #
# 单款再生成：只解析该款的参考图
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
    for request in runtime.requests:
        assert request.reference_images[0].content == source_bytes
        assert request.reference_images[1].content == basket_bytes
        assert "Product name: 收纳篮" in request.prompt
    service.close()
    runtime.close()


# --------------------------------------------------------------------------- #
# 失败重试 / 重启：新服务实例重跑失败款仍按款解析
# --------------------------------------------------------------------------- #

def test_replica_failed_style_retry_after_restart_uses_per_style_context(tmp_path: Path) -> None:
    runtime = CapturingReplicaRuntime()
    service = _service(tmp_path, runtime)
    actor = _actor()
    batch = _create_two_target_batch(service, actor)

    # 让第 1 款的两次尝试都失败（瞬时失败会被自动重试一次，两次都命中失败集合）。
    def _fail_first_style_always(request, *, grant=None, call_id=""):
        style, _attempt = _parse_trial(request.trial_id)
        with runtime._lock:
            runtime.requests.append(request)
        if style == 1:
            raise MediaProcessingError("persistent failure", status_class="transient")
        return GeneratedMedia(
            stage="grid_image",
            content=runtime._next_grid(),
            content_type="image/png",
            suffix=".png",
            provider="fake-replica",
            model=request.model_id,
            reference_count=len(request.reference_images),
        )

    runtime.generate_listing_grid = _fail_first_style_always  # type: ignore[assignment]
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
    finally:
        recovered.close()

    style1 = [
        request for request in recovered_runtime.requests if _parse_trial(request.trial_id)[0] == 1
    ]
    assert style1
    cushion_bytes = _asset_bytes(recovered, actor, batch["_cushion_id"])
    source_bytes = _asset_bytes(recovered, actor, batch["_source_id"])
    for request in style1:
        assert request.reference_images[0].content == source_bytes
        assert request.reference_images[1].content == cushion_bytes
        assert "Product name: 抱枕" in request.prompt
    recovered_runtime.close()


# --------------------------------------------------------------------------- #
# 暂停恢复：剩余款仍按自己的参考图继续
# --------------------------------------------------------------------------- #

def test_replica_resume_after_pause_uses_remaining_style_context(tmp_path: Path) -> None:
    runtime = CapturingReplicaRuntime()
    service = _service(tmp_path, runtime)
    actor = _actor()
    batch = _create_two_target_batch(service, actor)

    # 冻结后直接标记暂停再继续，走暂停恢复的剩余款计划。
    service.repository.request_pause(batch["id"])
    service.repository.mark_batch_paused(batch["id"], "已暂停")

    # 继续会重新冻结剩余款并提交；等待收敛。
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
    for request in runtime.requests:
        style, _attempt = _parse_trial(request.trial_id)
        assert request.reference_images[0].content == source_bytes
        assert request.reference_images[1].content == target_bytes[style]
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

    service.reprint_batch_spec_card(
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

    after = service.get_batch(actor, batch["id"])
    style1_hero_after = next(
        item for item in after["items"] if item["style_index"] == 1 and item["role"] == "hero"
    )["public_url"]
    assert style1_hero_after != style1_hero_before

    # 其他款（style 2）四张图与字段快照原封不动。
    style2_after = {
        (item["style_index"], item["role"]): item["public_url"]
        for item in after["items"]
        if item["style_index"] == 2
    }
    assert style2_after == style2_before
    assert after["targets"][1]["listing_fields"]["spec_card"] == style2_spec_before

    # 被指定款的快照已更新为新的规格卡。
    assert after["targets"][0]["listing_fields"]["spec_card"]["style"] == "dark"
    # 整批镜像配置未被改写。
    mirror_after = service.repository.get_batch_internal(batch["id"])["listing_fields"]["spec_card"]
    assert mirror_after == mirror_spec_before
    service.close()
    runtime.close()
