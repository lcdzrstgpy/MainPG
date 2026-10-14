"""independent_panels 生成策略：每款 4 次单面板 provider 调用、4 张面板、计费 link 不变。

全程使用假豆包分析与假图片 provider，不触发任何真实付费调用。
"""

from __future__ import annotations

import hashlib
import io
import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from wh_local.modules.pod_customization.billing_contract import (
    PodExecutionGrant,
)
from wh_local.modules.pod_customization.contracts import (
    ListingFields,
    ReplicaAnalyzeRequest,
    ReplicaBatchCreate,
    ReplicaTargetCreate,
)
from wh_local.modules.pod_customization.replica_analysis_runtime import (
    ReplicaAnalysisResult,
    build_composition_plan,
)
from wh_local.modules.pod_customization.replica_context import load_frozen_plan
from wh_local.modules.pod_customization.replica_specs import (
    REPLICA_ANALYSIS_SCHEMA_VERSION,
    SOURCE_FACT_FIELDS,
    TARGET_FACT_FIELDS,
    PresentationPreferences,
    normalize_source_analysis,
    normalize_target_analysis,
)
from wh_local.modules.product_processing.infrastructure.media import GeneratedMedia, MediaProcessingError
from wh_local.modules.pod_customization.service import PodCustomizationService
from wh_local.modules.pod_customization.title_runtime import PodTitleResult
from wh_local.session import Actor

_SOURCE_VALUES: dict[str, object] = {
    "source_product_name": "原素材帆布袋",
    "artwork_motifs": ["玫瑰", "藤蔓"],
    "artwork_colors": ["酒红"],
    "artwork_layout": "all_over_repeat",
    "artwork_density": "medium",
    "artwork_repeat_pattern": "seamless",
    "artwork_text": "BLOOM",
    "artwork_print_region": "front_panel",
    "source_product_structure": "tote_bag",
    "source_background_elements": ["木桌"],
    "source_people_and_props": ["模特"],
    "source_watermarks_and_labels": ["水印"],
}
# 五片凝胶美甲：命中 multi_piece，服务端据此整批选择 independent_panels。
_TARGET_VALUES: dict[str, object] = {
    "product_name": "美甲套装",
    "product_type": "nail_tip_set",
    "component_count": 5,
    "component_structure": "five_separate_nail_tips",
    "material": "gel",
    "surface_texture": "glossy_gel",
    "printable_regions": ["front_surface"],
    "non_printable_regions": ["cuticle_edge"],
    "inner_outer_structure": "none",
    "openings_and_hardware": [],
    "product_orientation": "upright",
    "supported_usage_modes": ["hand_worn"],
    "occlusion_risk": "low",
}


def _png(color: str) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (96, 96), color).save(output, "PNG")
    return output.getvalue()


def _raw(values: dict[str, object], fields: tuple[str, ...]) -> dict[str, object]:
    return {
        name: {"value": values[name], "confidence": 0.95, "source": "model"} for name in fields
    }


class FakeAnalysisRuntime:
    def __init__(self, target_values: dict[str, object] | None = None) -> None:
        self.target_values = target_values or _TARGET_VALUES

    def analyze_assets(
        self, source_asset, target_asset, *, grant, call_id, call_ids=None, on_start=None, on_outcome=None
    ) -> ReplicaAnalysisResult:
        if on_outcome is not None:
            on_outcome(call_id, "success")
        raw_source = _raw(_SOURCE_VALUES, SOURCE_FACT_FIELDS)
        raw_target = _raw(self.target_values, TARGET_FACT_FIELDS)
        return ReplicaAnalysisResult(
            source_artwork_spec=normalize_source_analysis(raw_source, source_asset.asset_id),
            target_product_spec=normalize_target_analysis(raw_target, target_asset.asset_id),
            raw_source=raw_source,
            raw_target=raw_target,
            model="doubao-fake",
            schema_version=REPLICA_ANALYSIS_SCHEMA_VERSION,
            prompt_version="pod-replica-analysis-v1",
            attempt_count=1,
        )

    def generate_composition_plan(
        self, request, *, grant, call_id, call_ids=None, on_start=None, on_outcome=None
    ):
        if on_outcome is not None:
            on_outcome(call_id, "success")
        payload = {
            "template_id": request.template.template_id,
            "template_version": request.template.template_version,
            "panels": [
                {
                    "slot_id": slot_id,
                    "role": f"role_{slot_id}",
                    "camera": "",
                    "lighting": "",
                    "background": "",
                    "composition_notes": "",
                }
                for slot_id in request.template.panel_slots
            ],
            "camera": "",
            "lighting": "",
            "background": "",
            "composition_notes": "",
        }
        return build_composition_plan(payload, request)


def _parse_panel_trial(trial_id: str) -> tuple[int, int, int]:
    after = trial_id.split("-style-", 1)[1]
    style = int(after.split("-panel-", 1)[0])
    rest = after.split("-panel-", 1)[1]
    panel = int(rest.split("-attempt-", 1)[0])
    attempt = int(rest.rsplit("-attempt-", 1)[1])
    return style, panel, attempt


class CapturingPanelRuntime:
    """捕获每款每面板的单面板请求；每个面板返回一张独立图片。

    ``fail_attempt1_panels`` 中的 (款, 面板) 在第一次尝试时抛瞬时错误，用于验证
    面板级重试会落到第二个尝试。
    """

    def __init__(self, fail_attempt1_panels: set[tuple[int, int]] | None = None) -> None:
        self.requests: list = []
        self.publications: list[tuple[str, str]] = []
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="panel-ai")
        self._lock = threading.Lock()
        self._counter = 0
        self.fail_attempt1_panels = set(fail_attempt1_panels or ())

    def submit(self, function, *args, **kwargs) -> Future:
        return self._executor.submit(function, *args, **kwargs)

    def generate_listing_grid(self, request, *, grant=None, call_id="") -> GeneratedMedia:
        assert grant is not None and grant.provider_key("wuyin")
        style, panel, attempt = _parse_panel_trial(request.trial_id)
        with self._lock:
            self.requests.append(request)
            self._counter += 1
            index = self._counter
        if attempt == 1 and (style, panel) in self.fail_attempt1_panels:
            raise MediaProcessingError("transient panel failure", status_class="transient")
        return GeneratedMedia(
            stage="grid_image",
            content=_png(f"#{index:02x}{index:02x}{index:02x}"),
            content_type="image/png",
            suffix=".png",
            provider="fake-panel",
            model=request.model_id,
            reference_count=len(request.reference_images),
        )

    def split_listing_grid(self, media: GeneratedMedia) -> list[GeneratedMedia]:  # pragma: no cover
        raise AssertionError("independent_panels must not split a grid")

    def publish_listing_image(self, media: GeneratedMedia, *, namespace: str, role: str) -> str:
        self.publications.append((namespace, role))
        digest = hashlib.sha256(media.content).hexdigest()[:12]
        return f"https://cos.example.com/{namespace}/{role}/{digest}.jpg"

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)


class FakeTitleRuntime:
    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="panel-title")

    def submit(self, function, *args, **kwargs) -> Future:
        return self._executor.submit(function, *args, **kwargs)

    def generate_title(self, request, *, grant=None, call_id="", call_ids=None, on_start=None, on_outcome=None):
        if on_start is not None:
            on_start(call_id)
        if on_outcome is not None:
            on_outcome(call_id, "success")
        title = f"{request.business_fields.product_name} Replica Title For Daily Use And Storage"
        return PodTitleResult(
            title=title,
            english_title=f"{request.business_fields.product_name} replica",
            description="desc",
            visual_theme="theme",
            motif_keywords=("motif",),
            color_keywords=("multicolor",),
            normalized_title=title,
            attempt_count=1,
            model="fake-ark",
            prompt_version="pod-title-v1",
        )

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)


class Billing:
    def __init__(self) -> None:
        self.freezes: list = []

    def freeze(self, _actor, plan):
        self.freezes.append(plan)
        return PodExecutionGrant(
            "freeze-1", 1, "2099-01-01T00:00:00Z",
            {"wuyin": "test-wuyin-key", "ark": "test-ark-key"},
        )

    def settle(self, _actor, _grant, _plan, _outcomes) -> None:
        return None

    def regrant(self, actor, _freeze_id):
        return self.freeze(actor, None)


def _actor() -> Actor:
    return Actor(id="operator-1", username="operator", role="operator", workspace_id="workspace-a")


def _listing_fields() -> ListingFields:
    return ListingFields(
        suggested_price_usd=19.99,
        category_name="美妆 > 美甲",
        skus=[{"name": "5片", "declared_price": 6.0, "weight_g": 300.0}],
        spec_card={
            "enabled": True,
            "style": "light",
            "corner": "bottom-right",
            "display_unit": "cm",
            "cells": [["SKU", "Length"], ["5片", "2"]],
        },
    )


def test_independent_panels_generates_four_single_panel_requests(tmp_path: Path) -> None:
    panel_runtime = CapturingPanelRuntime()
    titles = FakeTitleRuntime()
    billing = Billing()
    service = PodCustomizationService(
        tmp_path / "workbench.sqlite3",
        tmp_path / "pod-assets",
        panel_runtime,
        title_runtime=titles,
        analysis_runtime=FakeAnalysisRuntime(),
        billing_coordinator=billing,
        start_workers=True,
    )
    actor = _actor()
    try:
        source_id = service.upload_replica_image(
            actor, role="source", filename="source.png", content=_png("#cc1111")
        )["asset_id"]
        target_id = service.upload_replica_image(
            actor, role="target", filename="nail.png", content=_png("#11aa33")
        )["asset_id"]
        analysis = service.analyze_replica_assets(
            actor,
            ReplicaAnalyzeRequest(source_asset_id=source_id, target_asset_id=target_id),
        )
        assert "multi_piece" in analysis["candidate_capabilities"]

        batch = service.create_replica_batch(
            actor,
            ReplicaBatchCreate(
                client_request_id="req-panel-1",
                source_asset_id=source_id,
                targets=[
                    ReplicaTargetCreate(
                        target_asset_id=target_id,
                        product_name="美甲套装",
                        listing_fields=_listing_fields(),
                        analysis_id=analysis["analysis_id"],
                    )
                ],
            ),
            enqueue=False,
        )
        # 冻结计划已按独立面板策略落库。
        internal = service.repository.get_batch_internal(batch["id"])
        plan = load_frozen_plan(internal, 1)
        assert plan.generation_strategy == "independent_panels"
        # 冻结的是真实编译提示词，不再是占位空对象。
        assert internal["prompt_snapshot"] not in ("", "{}")
        assert "=== PART 1" in internal["prompt_snapshot"]

        # 计费：一款仍是一个 link，但预留 4 面板 × 2 次尝试。
        frozen = billing.freezes[-1]
        assert frozen.product_batch_freeze_payload()["link_count"] == 1
        assert sum(call.feature == "pod.image" for call in frozen.calls) == 8

        service.worker.process_batch(batch["id"])

        # 每款 4 个单面板请求，attempt=1，prompt 为单张全幅格式。
        assert len(panel_runtime.requests) == 4
        panels = sorted(
            request.trial_id.rsplit("-panel-", 1)[1] for request in panel_runtime.requests
        )
        assert panels == ["1-attempt-1", "2-attempt-1", "3-attempt-1", "4-attempt-1"]
        for request in panel_runtime.requests:
            assert request.attempt == 1
            assert "Create ONE single full-frame square ecommerce image" in request.prompt
            assert "2x2" not in request.prompt
            assert [image.role for image in request.reference_images] == [
                "pattern_source",
                "target_product",
            ]

        # 四张面板各自发布一次。
        published_roles = sorted(role for _ns, role in panel_runtime.publications)
        assert published_roles == ["detail_a", "detail_b", "hero", "lifestyle"]

        stored = service.get_batch(actor, batch["id"])
        assert stored["status"] == "completed"
        assert len([item for item in stored["items"] if item["style_index"] == 1]) == 4

        # 调用级快照 = 本次真实提交的 4 个单面板提示词（不是款级四宫格文本）。
        with service.repository._connect() as connection:
            row = connection.execute(
                """SELECT prompt_snapshot FROM pod_customization_generation_calls
                   WHERE batch_id = ? ORDER BY rowid DESC LIMIT 1""",
                (batch["id"],),
            ).fetchone()
        manifest = json.loads(row["prompt_snapshot"])
        assert manifest["strategy"] == "independent_panels"
        assert len(manifest["panels"]) == 4
        assert [entry["panel_index"] for entry in manifest["panels"]] == [1, 2, 3, 4]
        assert all(
            "Create ONE single full-frame square ecommerce image" in entry["prompt"]
            for entry in manifest["panels"]
        )

        # 整款重试：同样走 4 次单面板调用（重试动作自带新的面板调用计划）。
        panel_runtime.requests.clear()
        service.regenerate_style(actor, batch["id"], 1, enqueue=True)
        with service.worker._futures_lock:
            future = service.worker._futures[("regenerate-style", f"{batch['id']}:1")]
        future.result(timeout=10)
        assert len(panel_runtime.requests) == 4
        assert all(request.attempt == 1 for request in panel_runtime.requests)
        assert all(
            "Create ONE single full-frame square ecommerce image" in request.prompt
            for request in panel_runtime.requests
        )
    finally:
        service.close()
        panel_runtime.close()
        titles.close()


# --------------------------------------------------------------------------- #
# 展示偏好语义：能力支持 ≠ 本次必拍；面板级失败落到第二个尝试
# --------------------------------------------------------------------------- #

_APPAREL_VALUES: dict[str, object] = {
    "product_name": "短袖T恤",
    "product_type": "t_shirt",
    "component_count": 1,
    "component_structure": "single_piece_garment",
    "material": "cotton",
    "surface_texture": "matte_fabric",
    "printable_regions": ["front_torso"],
    "non_printable_regions": ["collar"],
    "inner_outer_structure": "none",
    "openings_and_hardware": [],
    "product_orientation": "upright",
    "supported_usage_modes": ["body_worn"],
    "occlusion_risk": "medium",
}


def _prepare(
    tmp_path: Path,
    runtime: CapturingPanelRuntime,
    *,
    target_values: dict[str, object],
    preferences: PresentationPreferences | None = None,
    request_id: str = "req",
):
    titles = FakeTitleRuntime()
    billing = Billing()
    service = PodCustomizationService(
        tmp_path / "workbench.sqlite3",
        tmp_path / "pod-assets",
        runtime,
        title_runtime=titles,
        analysis_runtime=FakeAnalysisRuntime(target_values),
        billing_coordinator=billing,
        start_workers=True,
    )
    actor = _actor()
    source_id = service.upload_replica_image(
        actor, role="source", filename="source.png", content=_png("#cc1111")
    )["asset_id"]
    target_id = service.upload_replica_image(
        actor, role="target", filename="target.png", content=_png("#11aa33")
    )["asset_id"]
    prefs = preferences or PresentationPreferences()
    analysis = service.analyze_replica_assets(
        actor,
        ReplicaAnalyzeRequest(
            source_asset_id=source_id,
            target_asset_id=target_id,
            presentation_preferences=prefs,
        ),
    )
    batch = service.create_replica_batch(
        actor,
        ReplicaBatchCreate(
            client_request_id=request_id,
            source_asset_id=source_id,
            targets=[
                ReplicaTargetCreate(
                    target_asset_id=target_id,
                    product_name=str(target_values["product_name"]),
                    listing_fields=_listing_fields(),
                    analysis_id=analysis["analysis_id"],
                    presentation_preferences=prefs,
                )
            ],
        ),
        enqueue=False,
    )
    return service, titles, actor, batch


def test_nail_target_has_no_hand_panel_by_default(tmp_path: Path) -> None:
    runtime = CapturingPanelRuntime()
    service, titles, actor, batch = _prepare(tmp_path, runtime, target_values=_TARGET_VALUES)
    try:
        internal = service.repository.get_batch_internal(batch["id"])
        plan = load_frozen_plan(internal, 1)
        assert plan.composition_plan is not None
        # 支持 hand_worn 使用方式，但用户没选，本次计划不得出现手部画面。
        assert all(not slot.hand_worn for slot in plan.composition_plan.slots)
        service.worker.process_batch(batch["id"])
        assert not any("; hand_worn" in request.prompt for request in runtime.requests)
        assert service.get_batch(actor, batch["id"])["status"] == "completed"
    finally:
        service.close()
        runtime.close()
        titles.close()


def test_apparel_target_with_body_preference_renders_a_body_worn_panel(tmp_path: Path) -> None:
    runtime = CapturingPanelRuntime()
    service, titles, actor, batch = _prepare(
        tmp_path,
        runtime,
        target_values=_APPAREL_VALUES,
        preferences=PresentationPreferences(use_body_model=True),
        request_id="req-apparel",
    )
    try:
        internal = service.repository.get_batch_internal(batch["id"])
        plan = load_frozen_plan(internal, 1)
        assert plan.composition_plan is not None
        assert any(slot.body_worn for slot in plan.composition_plan.slots)
        service.worker.process_batch(batch["id"])
        assert any("; body_worn" in request.prompt for request in runtime.requests)
        assert service.get_batch(actor, batch["id"])["status"] == "completed"
    finally:
        service.close()
        runtime.close()
        titles.close()


def test_panel_failure_retries_on_the_second_attempt(tmp_path: Path) -> None:
    runtime = CapturingPanelRuntime(fail_attempt1_panels={(1, 2)})
    service, titles, actor, batch = _prepare(
        tmp_path, runtime, target_values=_TARGET_VALUES, request_id="req-panel-retry"
    )
    try:
        service.worker.process_batch(batch["id"])
        seen: dict[tuple[int, int], set[int]] = {}
        for request in runtime.requests:
            style, panel, attempt = _parse_panel_trial(request.trial_id)
            seen.setdefault((style, panel), set()).add(attempt)
        # 面板 2 第一次失败后落到第二个尝试；其余面板仍只用第一次尝试。
        assert seen[(1, 2)] == {1, 2}
        assert all(attempts == {1} for key, attempts in seen.items() if key != (1, 2))
        assert len(runtime.publications) == 4
        assert service.get_batch(actor, batch["id"])["status"] == "completed"
    finally:
        service.close()
        runtime.close()
        titles.close()
