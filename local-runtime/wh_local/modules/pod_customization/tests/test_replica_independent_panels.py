"""复刻独立面板生成：主图锚定 + 三张换机位派生，每款 4 次单面板调用。

全程使用假豆包方案与假图片 provider，不触发任何真实付费调用。
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
    POD_BILLING_PROFILE_REPLICA_HD,
    PodExecutionGrant,
)
from wh_local.modules.pod_customization.contracts import (
    ListingFields,
    ReplicaAnalyzeRequest,
    ReplicaBatchCreate,
    ReplicaTargetCreate,
)
from wh_local.modules.pod_customization.replica_analysis_runtime import (
    MAIN_PROMPT_MODEL,
    MAIN_PROMPT_VERSION,
    ReplicaMainPromptResult,
)
from wh_local.modules.pod_customization.replica_context import load_frozen_plan
from wh_local.modules.pod_customization.replica_panel_template import DERIVED_PANEL_KEYS
from wh_local.modules.pod_customization.service import PodCustomizationService
from wh_local.modules.pod_customization.title_runtime import PodTitleResult
from wh_local.modules.product_processing.infrastructure.media import GeneratedMedia, MediaProcessingError
from wh_local.session import Actor

_MAIN_PROMPT = (
    "Main image: reference image 1 is the exact product, reference image 2 supplies the pattern. "
    "Place the complete product on a warm wooden desk with directional afternoon window light."
)


def _png(color: str) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (96, 96), color).save(output, "PNG")
    return output.getvalue()


class FakeAnalysisRuntime:
    """假豆包方案：一次调用返回固定主图提示词。"""

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


def _parse_panel_trial(trial_id: str) -> tuple[int, int, int]:
    after = trial_id.split("-style-", 1)[1]
    style = int(after.split("-panel-", 1)[0])
    rest = after.split("-panel-", 1)[1]
    panel = int(rest.split("-attempt-", 1)[0])
    attempt = int(rest.rsplit("-attempt-", 1)[1])
    return style, panel, attempt


class CapturingPanelRuntime:
    """捕获每款每面板的单面板请求；每个面板返回一张独立图片。"""

    def __init__(
        self,
        fail_attempt1_panels: set[tuple[int, int]] | None = None,
        fail_panels: set[tuple[int, int]] | None = None,
    ) -> None:
        self.requests: list = []
        self.publications: list[tuple[str, str]] = []
        self.outputs: dict[str, bytes] = {}
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="panel-ai")
        self._lock = threading.Lock()
        self._counter = 0
        self.fail_attempt1_panels = set(fail_attempt1_panels or ())
        self.fail_panels = set(fail_panels or ())

    def submit(self, function, *args, **kwargs) -> Future:
        return self._executor.submit(function, *args, **kwargs)

    def generate_listing_grid(self, request, *, grant=None, call_id="") -> GeneratedMedia:
        assert grant is not None and grant.provider_key("wuyin")
        style, panel, attempt = _parse_panel_trial(request.trial_id)
        with self._lock:
            self.requests.append(request)
            self._counter += 1
            index = self._counter
        if (style, panel) in self.fail_panels or (
            attempt == 1 and (style, panel) in self.fail_attempt1_panels
        ):
            raise MediaProcessingError("transient panel failure", status_class="transient")
        content = _png(f"#{index:02x}{index:02x}{index:02x}")
        with self._lock:
            self.outputs[request.trial_id] = content
        return GeneratedMedia(
            stage="grid_image",
            content=content,
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
            plan.idempotency_key, 1, "2099-01-01T00:00:00Z",
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
    )


def _prepare(
    tmp_path: Path,
    runtime: CapturingPanelRuntime,
    *,
    request_id: str = "req-panels",
    billing: Billing | None = None,
):
    titles = FakeTitleRuntime()
    billing = billing or Billing()
    service = PodCustomizationService(
        tmp_path / "workbench.sqlite3",
        tmp_path / "pod-assets",
        runtime,
        title_runtime=titles,
        analysis_runtime=FakeAnalysisRuntime(),
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
    analysis = service.analyze_replica_assets(
        actor, ReplicaAnalyzeRequest(source_asset_id=source_id, target_asset_id=target_id)
    )
    batch = service.create_replica_batch(
        actor,
        ReplicaBatchCreate(
            client_request_id=request_id,
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
    return service, titles, billing, actor, batch, source_id, target_id


def test_main_image_then_three_derived_panels(tmp_path: Path) -> None:
    runtime = CapturingPanelRuntime()
    service, titles, billing, actor, batch, source_id, target_id = _prepare(tmp_path, runtime)
    try:
        internal = service.repository.get_batch_internal(batch["id"])
        plan = load_frozen_plan(internal, 1)
        assert plan.generation_strategy == "independent_panels"
        assert plan.main_prompt == _MAIN_PROMPT
        assert internal["prompt_snapshot"] == _MAIN_PROMPT

        frozen = billing.freezes[-1]
        assert frozen.billing_profile == POD_BILLING_PROFILE_REPLICA_HD
        assert frozen.product_batch_freeze_payload()["link_count"] == 1
        assert sum(call.feature == "pod.image" for call in frozen.calls) == 8

        service.worker.process_batch(batch["id"])

        by_panel = {
            panel: request
            for request in runtime.requests
            for _style, panel, attempt in [_parse_panel_trial(request.trial_id)]
            if attempt == 1
        }
        assert sorted(by_panel) == [1, 2, 3, 4]

        main = by_panel[1]
        assert main.prompt == _MAIN_PROMPT
        # 主图参考图固定 [B, A]：第 1 张目标白底图，第 2 张样图。
        assert [image.role for image in main.reference_images] == ["target_product", "pattern_source"]
        assert main.reference_images[0].content == _asset_bytes(service, actor, target_id)
        assert main.reference_images[1].content == _asset_bytes(service, actor, source_id)

        main_bytes = runtime.outputs[main.trial_id]
        assert main_bytes
        for panel in (2, 3, 4):
            request = by_panel[panel]
            assert [image.role for image in request.reference_images] == ["main_view"]
            # 派生三格的唯一参考图就是刚才那次主图调用的输出。
            assert request.reference_images[0].content == main_bytes
            assert plan.derived_panels[f"panel_{panel}"] in request.prompt
            assert "SINGLE FRAME OUTPUT" in request.prompt
            assert "Create one square 2x2" not in request.prompt
            assert request.attempt == 1

        published_roles = sorted(role for _ns, role in runtime.publications)
        assert published_roles == ["detail_a", "detail_b", "hero", "lifestyle"]

        stored = service.get_batch(actor, batch["id"])
        assert stored["status"] == "completed"
        assert len([item for item in stored["items"] if item["style_index"] == 1]) == 4

        # 调用级快照 = 本次真实提交的 4 个单面板提示词。
        with service.repository._connect() as connection:
            row = connection.execute(
                """SELECT prompt_snapshot FROM pod_customization_generation_calls
                   WHERE batch_id = ? ORDER BY rowid DESC LIMIT 1""",
                (batch["id"],),
            ).fetchone()
        manifest = json.loads(row["prompt_snapshot"])
        assert manifest["strategy"] == "independent_panels"
        assert [entry["panel_index"] for entry in manifest["panels"]] == [1, 2, 3, 4]
        assert manifest["panels"][0]["prompt"] == _MAIN_PROMPT

        # 整款重试：主图重新生成，三张派生再以新主图为锚点。
        runtime.requests.clear()
        service.regenerate_style(actor, batch["id"], 1, enqueue=True)
        with service.worker._futures_lock:
            future = service.worker._futures[("regenerate-style", f"{batch['id']}:1")]
        future.result(timeout=10)
        assert len(runtime.requests) == 4
        retried = {_parse_panel_trial(request.trial_id)[1] for request in runtime.requests}
        assert retried == {1, 2, 3, 4}
    finally:
        service.close()
        runtime.close()
        titles.close()


def _asset_bytes(service: PodCustomizationService, actor: Actor, asset_id: str) -> bytes:
    asset = service.repository.get_asset(asset_id, actor.workspace_id, actor.id)
    return service.assets.read(asset["relative_path"])


def test_main_image_failure_stops_derived_panels(tmp_path: Path) -> None:
    runtime = CapturingPanelRuntime(fail_panels={(1, 1)})
    service, titles, _billing, actor, batch, _source, _target = _prepare(
        tmp_path, runtime, request_id="req-main-fail"
    )
    try:
        service.worker.process_batch(batch["id"])

        panels = {_parse_panel_trial(request.trial_id)[1] for request in runtime.requests}
        # 主图两次尝试都失败后整款失败，派生三格一次都不发起。
        assert panels == {1}
        assert service.get_batch(actor, batch["id"])["status"] in {"failed", "partial_failure"}
        assert runtime.publications == []
    finally:
        service.close()
        runtime.close()
        titles.close()


def test_derived_panel_failure_retries_on_the_second_attempt(tmp_path: Path) -> None:
    runtime = CapturingPanelRuntime(fail_attempt1_panels={(1, 2)})
    service, titles, _billing, actor, batch, _source, _target = _prepare(
        tmp_path, runtime, request_id="req-panel-retry"
    )
    try:
        service.worker.process_batch(batch["id"])

        seen: dict[tuple[int, int], set[int]] = {}
        for request in runtime.requests:
            style, panel, attempt = _parse_panel_trial(request.trial_id)
            seen.setdefault((style, panel), set()).add(attempt)
        assert seen[(1, 1)] == {1}
        assert seen[(1, 2)] == {1, 2}
        assert seen[(1, 3)] == {1}
        assert seen[(1, 4)] == {1}
        assert len(runtime.publications) == 4
        assert service.get_batch(actor, batch["id"])["status"] == "completed"
    finally:
        service.close()
        runtime.close()
        titles.close()


def test_derived_panels_use_the_frozen_plan_directions(tmp_path: Path) -> None:
    runtime = CapturingPanelRuntime()
    service, titles, _billing, actor, batch, _source, _target = _prepare(
        tmp_path, runtime, request_id="req-plan-directions"
    )
    try:
        internal = service.repository.get_batch_internal(batch["id"])
        plan = load_frozen_plan(internal, 1)
        assert set(plan.derived_panels) == set(DERIVED_PANEL_KEYS)

        service.worker.process_batch(batch["id"])
        by_panel = {
            panel: request
            for request in runtime.requests
            for _style, panel, _attempt in [_parse_panel_trial(request.trial_id)]
        }
        for panel in (2, 3, 4):
            assert plan.derived_panels[f"panel_{panel}"] in by_panel[panel].prompt
    finally:
        service.close()
        runtime.close()
        titles.close()
