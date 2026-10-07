"""爆款复刻计费：N 款冻结/结算、余额不足、幂等、暂停取消与部分失败。

复用现有 ``pod_random_v1`` 画像与 ``:style:{款}:image:{尝试}`` 调用 ID；
断言一款一次成功生图只对应一次图像调用，绝不因两张参考图重复计费。
"""

from __future__ import annotations

import hashlib
import io
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import pytest
from PIL import Image

from wh_local.modules.pod_customization.billing_contract import (
    POD_BILLING_PROFILE_RANDOM,
    PodCallPlan,
    PodExecutionGrant,
)
from wh_local.modules.pod_customization.contracts import (
    ListingFields,
    ReplicaBatchCreate,
    ReplicaTargetCreate,
)
from wh_local.modules.pod_customization.images import split_grid_2x2
from wh_local.modules.pod_customization.repository import PodRepositoryError
from wh_local.modules.pod_customization.service import PodCustomizationService
from wh_local.modules.product_processing.infrastructure.media import (
    GeneratedMedia,
    MediaProcessingError,
)
from wh_local.session import Actor


def _encode(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _solid(color: str) -> bytes:
    return _encode(Image.new("RGB", (160, 120), color))


def _grid(seed: int) -> bytes:
    image = Image.new("RGB", (192, 192), "white")
    for index, position in enumerate(((0, 0), (96, 0), (0, 96), (96, 96))):
        color = ((seed * 31 + index * 53) % 200 + 30, (index * 71) % 200 + 30, (seed * 17) % 200 + 30)
        image.paste(Image.new("RGB", (96, 96), color), position)
    return _encode(image)


def _parse_trial(trial_id: str) -> tuple[int, int]:
    after = trial_id.split("-style-", 1)[1]
    return int(after.split("-", 1)[0]), int(trial_id.rsplit("-attempt-", 1)[1])


# --------------------------------------------------------------------------- #
# 计费协作器
# --------------------------------------------------------------------------- #

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


class InsufficientBalanceCoordinator(BillingCoordinator):
    def freeze(self, _actor, _plan):
        raise RuntimeError("积分余额不足，无法冻结复刻批次")


# --------------------------------------------------------------------------- #
# 生图 runtime
# --------------------------------------------------------------------------- #

class CapturingReplicaRuntime:
    def __init__(self) -> None:
        self.requests: list = []
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="billing-ai")
        self._lock = threading.Lock()
        self._counter = 0

    def submit(self, function, *args, **kwargs) -> Future:
        return self._executor.submit(function, *args, **kwargs)

    def _next_grid(self) -> bytes:
        with self._lock:
            self._counter += 1
            return _grid(self._counter)

    def generate_listing_grid(self, request, *, grant=None, call_id="") -> GeneratedMedia:
        assert grant is not None and grant.provider_key("wuyin")
        with self._lock:
            self.requests.append(request)
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
        digest = hashlib.sha256(media.content).hexdigest()[:12]
        return f"https://cos.example.com/{namespace}/{role}/{digest}.jpg"

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)


class FailingStyleRuntime(CapturingReplicaRuntime):
    """指定款两次尝试都失败，其余款正常，以覆盖部分失败结算。"""

    def __init__(self, failing_style: int) -> None:
        super().__init__()
        self.failing_style = failing_style

    def generate_listing_grid(self, request, *, grant=None, call_id="") -> GeneratedMedia:
        with self._lock:
            self.requests.append(request)
        if _parse_trial(request.trial_id)[0] == self.failing_style:
            raise MediaProcessingError("style provider failure", status_class="transient")
        return GeneratedMedia(
            stage="grid_image",
            content=self._next_grid(),
            content_type="image/png",
            suffix=".png",
            provider="fake-replica",
            model=request.model_id,
            reference_count=len(request.reference_images),
        )


class GatedReplicaRuntime(CapturingReplicaRuntime):
    """首个生图请求阻塞，直到测试放行，用于暂停/取消的检查点测试。"""

    def __init__(self) -> None:
        super().__init__()
        self.first_started = threading.Event()
        self.release_first = threading.Event()

    def generate_listing_grid(self, request, *, grant=None, call_id="") -> GeneratedMedia:
        with self._lock:
            first = not self.requests
            self.requests.append(request)
        if first:
            self.first_started.set()
            self.release_first.wait(timeout=3)
        return GeneratedMedia(
            stage="grid_image",
            content=self._next_grid(),
            content_type="image/png",
            suffix=".png",
            provider="fake-replica",
            model=request.model_id,
            reference_count=len(request.reference_images),
        )


# --------------------------------------------------------------------------- #
# fixture
# --------------------------------------------------------------------------- #

def _actor() -> Actor:
    return Actor(id="operator-1", username="operator", role="operator", workspace_id="workspace-a")


def _listing_fields(name: str, category: str) -> ListingFields:
    return ListingFields(
        suggested_price_usd=19.99,
        category_name=category,
        skus=[{"name": name, "declared_price": 6.0, "weight_g": 300.0}],
        spec_card={"cells": [["SKU", "Length"], [name, "40"]]},
    )


def _service(tmp_path: Path, runtime, billing=None) -> PodCustomizationService:
    return PodCustomizationService(
        tmp_path / "workbench.sqlite3",
        tmp_path / "pod-assets",
        runtime,
        billing_coordinator=billing or BillingCoordinator(),
        start_workers=True,
    )


def _batch_request(service: PodCustomizationService, actor: Actor, *, count: int, request_id: str):
    source_id = service.upload_replica_image(
        actor, role="source", filename="source.png", content=_solid("#cc1111")
    )["asset_id"]
    targets = []
    for index in range(count):
        target_id = service.upload_replica_image(
            actor, role="target", filename=f"t{index}.png", content=_solid(f"#0{index}aa33")
        )["asset_id"]
        targets.append(
            ReplicaTargetCreate(
                target_asset_id=target_id,
                product_name=f"产品{index}",
                listing_fields=_listing_fields(f"SKU{index}", f"类目{index}"),
            )
        )
    return ReplicaBatchCreate(
        client_request_id=request_id, source_asset_id=source_id, targets=targets
    )


def _image_outcomes(outcomes) -> list:
    return [outcome for outcome in outcomes if outcome.feature == "pod.image"]


# --------------------------------------------------------------------------- #
# N 款冻结 / 结算
# --------------------------------------------------------------------------- #

def test_replica_batch_freezes_one_pod_random_link_per_style_and_settles(tmp_path: Path) -> None:
    runtime = CapturingReplicaRuntime()
    billing = BillingCoordinator()
    service = _service(tmp_path, runtime, billing)
    actor = _actor()
    batch = service.create_replica_batch(
        actor, _batch_request(service, actor, count=3, request_id="req-billing-3"), enqueue=False
    )

    # 冻结一次，画像为 pod_random_v1，调用 ID 沿用 :style:{款}:image:{尝试}。
    assert len(billing.freezes) == 1
    plan = billing.freezes[0]
    assert plan.billing_profile == POD_BILLING_PROFILE_RANDOM
    image_calls = [call.call_id for call in plan.calls if call.feature == "pod.image"]
    assert sorted(image_calls) == sorted(
        f"{batch['id']}:style:{style}:image:{attempt}"
        for style in (1, 2, 3)
        for attempt in (1, 2)
    )
    assert plan.product_batch_freeze_payload()["link_count"] == 3

    service.worker.process_batch(batch["id"])

    # 三款各一次成功生图 → 恰好三次 provider 请求，每款只成功一次图像调用。
    assert len(runtime.requests) == 3
    assert len(billing.settlements) == 1
    _plan, outcomes = billing.settlements[0]
    successes = [
        outcome for outcome in _image_outcomes(outcomes) if outcome.status == "success"
    ]
    assert len(successes) == 3
    assert {outcome.call_id.rsplit(":", 1)[0] for outcome in successes} == {
        f"{batch['id']}:style:{style}:image" for style in (1, 2, 3)
    }
    assert service.repository.list_pending_billing_runs(actor.workspace_id, actor.id) == []
    service.close()
    runtime.close()


# --------------------------------------------------------------------------- #
# 余额不足：冻结失败不得落库、不得扣费
# --------------------------------------------------------------------------- #

def test_replica_insufficient_balance_fails_before_persisting_any_batch(tmp_path: Path) -> None:
    runtime = CapturingReplicaRuntime()
    service = _service(tmp_path, runtime, InsufficientBalanceCoordinator())
    actor = _actor()

    with pytest.raises(RuntimeError, match="余额不足"):
        service.create_replica_batch(
            actor, _batch_request(service, actor, count=2, request_id="req-poor"), enqueue=False
        )

    with service.repository._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM pod_customization_batches"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM pod_customization_replica_batches"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM pod_customization_billing_runs"
        ).fetchone()[0] == 0
    service.close()
    runtime.close()


# --------------------------------------------------------------------------- #
# 重复提交幂等：只冻结一次、只保留一个批次
# --------------------------------------------------------------------------- #

def test_replica_duplicate_submit_is_idempotent_and_freezes_once(tmp_path: Path) -> None:
    runtime = CapturingReplicaRuntime()
    billing = BillingCoordinator()
    service = _service(tmp_path, runtime, billing)
    actor = _actor()
    request = _batch_request(service, actor, count=2, request_id="req-idem")

    first = service.create_replica_batch(actor, request, enqueue=False)
    second = service.create_replica_batch(actor, request, enqueue=False)

    assert first["id"] == second["id"]
    assert len(billing.freezes) == 1
    with service.repository._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM pod_customization_batches"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM pod_customization_billing_runs"
        ).fetchone()[0] == 1
    service.close()
    runtime.close()


# --------------------------------------------------------------------------- #
# 暂停：已完成的款保留，剩余款结算为 no_return
# --------------------------------------------------------------------------- #

def test_replica_pause_settles_unstarted_calls(tmp_path: Path) -> None:
    runtime = GatedReplicaRuntime()
    billing = BillingCoordinator()
    service = _service(tmp_path, runtime, billing)
    actor = _actor()
    batch = service.create_replica_batch(
        actor, _batch_request(service, actor, count=2, request_id="req-pause"), enqueue=False
    )

    future = service.worker.submit(batch["id"])
    assert runtime.first_started.wait(timeout=2)
    assert service.pause_batch(actor, batch["id"])["status"] == "pausing"
    runtime.release_first.set()
    future.result(timeout=3)

    stored = service.get_batch(actor, batch["id"])
    assert stored["status"] == "paused"
    assert len(billing.settlements) == 1
    _plan, outcomes = billing.settlements[0]
    # 未开始的调用必须以 no_return 结算，已用冻结不重复计费。
    assert any(outcome.status == "no_return" for outcome in _image_outcomes(outcomes))
    service.close()
    runtime.close()


# --------------------------------------------------------------------------- #
# 取消：批次落 cancelled 终态并结算
# --------------------------------------------------------------------------- #

def test_replica_cancel_settles_and_marks_terminal(tmp_path: Path) -> None:
    runtime = GatedReplicaRuntime()
    billing = BillingCoordinator()
    service = _service(tmp_path, runtime, billing)
    actor = _actor()
    batch = service.create_replica_batch(
        actor, _batch_request(service, actor, count=2, request_id="req-cancel"), enqueue=False
    )

    future = service.worker.submit(batch["id"])
    assert runtime.first_started.wait(timeout=2)
    assert service.cancel_batch(actor, batch["id"])["status"] == "cancelling"
    runtime.release_first.set()
    future.result(timeout=3)

    assert service.get_batch(actor, batch["id"])["status"] == "cancelled"
    assert len(billing.settlements) == 1
    assert service.repository.list_pending_billing_runs(actor.workspace_id, actor.id) == []
    service.close()
    runtime.close()


# --------------------------------------------------------------------------- #
# 部分失败：成功款计费，失败款各尝试都是 no_return
# --------------------------------------------------------------------------- #

def test_replica_partial_failure_bills_only_successful_style(tmp_path: Path) -> None:
    runtime = FailingStyleRuntime(failing_style=2)
    billing = BillingCoordinator()
    service = _service(tmp_path, runtime, billing)
    actor = _actor()
    batch = service.create_replica_batch(
        actor, _batch_request(service, actor, count=2, request_id="req-partial"), enqueue=False
    )

    service.worker.process_batch(batch["id"])

    stored = service.get_batch(actor, batch["id"])
    assert stored["status"] == "partial_failure"
    assert len(billing.settlements) == 1
    _plan, outcomes = billing.settlements[0]
    by_call = {outcome.call_id: outcome.status for outcome in _image_outcomes(outcomes)}
    assert by_call[f"{batch['id']}:style:1:image:1"] == "success"
    assert by_call[f"{batch['id']}:style:2:image:1"] == "no_return"
    assert by_call[f"{batch['id']}:style:2:image:2"] == "no_return"
    assert service.repository.list_pending_billing_runs(actor.workspace_id, actor.id) == []
    service.close()
    runtime.close()
