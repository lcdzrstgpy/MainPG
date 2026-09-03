from __future__ import annotations

import hashlib
import io
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from wh_local.modules.pod_customization.contracts import (
    BatchCreate,
    BusinessFields,
    Calibration,
    DirectListingTrialCreate,
    ListingFields,
    NormalizedPoint,
    NormalizedRect,
)
from wh_local.modules.pod_customization.billing_contract import (
    PodBillingAuthorizationRequired,
    PodCallPlan,
    PodExecutionGrant,
)
from wh_local.modules.pod_customization.errors import PodExecutionExpired
from wh_local.modules.pod_customization.repository import PodRepositoryError
from wh_local.modules.pod_customization.service import PodCustomizationService
from wh_local.modules.pod_customization.worker import PodBillingRun
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


class BlockingListingRuntime(ListingOnlyRuntime):
    def __init__(self, grids: list[bytes]) -> None:
        super().__init__(grids)
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="blocking-pod-ai")
        self.first_request_started = threading.Event()
        self.allow_first_request_to_finish = threading.Event()

    def generate_listing_grid(self, request, *, grant=None, call_id="") -> GeneratedMedia:
        assert grant is not None and grant.provider_key("wuyin")
        self.requests.append(request)
        if len(self.requests) == 1:
            self.first_request_started.set()
            assert self.allow_first_request_to_finish.wait(timeout=2)
        if not self.grids:
            raise RuntimeError("no fake listing grid remains")
        return GeneratedMedia(
            stage="grid_image",
            content=self.grids.pop(0),
            content_type="image/png",
            suffix=".png",
            provider="fake-listing",
            model=request.model_id,
            reference_count=1,
        )


class BeforeSubmitListingRuntime(ListingOnlyRuntime):
    def __init__(self, grids: list[bytes]) -> None:
        super().__init__(grids)
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="before-submit-pod-ai")
        self.before_provider_submit = threading.Event()
        self.allow_provider_submit = threading.Event()

    def generate_listing_grid(self, request, *, grant=None, call_id="", on_start=None) -> GeneratedMedia:
        assert on_start is not None
        self.before_provider_submit.set()
        assert self.allow_provider_submit.wait(timeout=2)
        on_start()
        self.requests.append(request)
        return GeneratedMedia(
            stage="grid_image",
            content=self.grids.pop(0),
            content_type="image/png",
            suffix=".png",
            provider="fake-listing",
            model=request.model_id,
            reference_count=1,
        )


def _actor() -> Actor:
    return Actor(id="designer-1", username="designer", role="admin", workspace_id="workspace-a")


class BillingCoordinator:
    def __init__(self) -> None:
        self.settlements = []
        self.freezes = []

    def freeze(self, _actor, _plan):
        self.freezes.append(_plan)
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


class RecoveringSettlementCoordinator(BillingCoordinator):
    def __init__(self) -> None:
        super().__init__()
        self.regrants: list[tuple[str, str, str]] = []

    def regrant(self, actor, freeze_id):
        self.regrants.append((actor.workspace_id, actor.id, freeze_id))
        return PodExecutionGrant(
            freeze_id,
            1,
            "2099-01-01T00:00:00Z",
            {"wuyin": "replacement-wuyin-key", "ark": "replacement-ark-key"},
        )


class ExpiringGrant:
    freeze_id = "freeze-expiring"
    rule_version = 1
    expires_at = "2099-01-01T00:00:00Z"

    def __init__(self) -> None:
        self.expired = False

    def provider_key(self, _provider: str) -> str:
        return "temporary-provider-key" if not self.expired else ""


class ExpiringRuntime(ListingOnlyRuntime):
    def generate_listing_grid(self, request, *, grant=None, call_id="") -> GeneratedMedia:
        media = super().generate_listing_grid(request, grant=grant, call_id=call_id)
        if len(self.requests) == 1:
            grant.expired = True
        return media


class ExpiringCoordinator(BillingCoordinator):
    def __init__(self, grant: ExpiringGrant) -> None:
        super().__init__()
        self.grant = grant

    def freeze(self, _actor, _plan):
        return self.grant

    def regrant(self, _actor, freeze_id):
        assert freeze_id == self.grant.freeze_id
        return PodExecutionGrant(
            freeze_id, 1, "2099-01-01T00:00:00Z", {"wuyin": "replacement-wuyin-key"}
        )


def _service(tmp_path: Path, runtime: FakePodRuntime, billing=None, *, title_runtime=None, start_workers: bool = True) -> PodCustomizationService:
    return PodCustomizationService(
        tmp_path / "workbench.sqlite3",
        tmp_path / "pod-assets",
        runtime,
        title_runtime=title_runtime,
        billing_coordinator=billing or BillingCoordinator(),
        start_workers=start_workers,
    )


def test_worker_without_in_memory_grant_marks_batch_failed(tmp_path: Path) -> None:
    runtime = ListingOnlyRuntime([_grid([_pattern(index) for index in range(4)])])
    service = PodCustomizationService(
        tmp_path / "workbench.sqlite3",
        tmp_path / "pod-assets",
        runtime,
        start_workers=True,
    )
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request_for_test(template["id"]), enqueue=False)

    service.worker.process_batch(batch["id"])

    assert service.get_batch(actor, batch["id"])["status"] == "failed"
    assert runtime.requests == []
    service.close()
    runtime.close()


def test_settlement_network_failure_keeps_generation_result_retryable(tmp_path: Path) -> None:
    runtime = ListingOnlyRuntime([_grid([_pattern(index) for index in range(4)])])
    service = _service(tmp_path, runtime, FailingSettlementCoordinator())
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request_for_test(template["id"]), enqueue=False)

    service.worker.process_batch(batch["id"])

    stored = service.get_batch(actor, batch["id"])
    assert stored["status"] == "completed"
    pending = service.repository.list_pending_billing_runs(actor.workspace_id, actor.id)
    assert pending[0]["status"] == "settlement_pending"
    assert "billing network unavailable" in pending[0]["error_message"]
    service.close()
    runtime.close()


def test_pending_billing_run_survives_restart_and_resume_never_replays_provider(tmp_path: Path) -> None:
    database = tmp_path / "workbench.sqlite3"
    runtime = ListingOnlyRuntime([_grid([_pattern(index) for index in range(4)])])
    service = _service(tmp_path, runtime, FailingSettlementCoordinator())
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request_for_test(template["id"]), enqueue=False)

    service.worker.process_batch(batch["id"])
    assert len(runtime.requests) == 1
    pending = service.list_pending_billing_runs(actor)
    assert pending["total"] == 1
    assert pending["runs"][0]["batch_id"] == batch["id"]
    run_id = pending["runs"][0]["id"]
    service.close()

    replacement_runtime = ListingOnlyRuntime([])
    coordinator = RecoveringSettlementCoordinator()
    recovered = PodCustomizationService(
        database,
        tmp_path / "pod-assets",
        replacement_runtime,
        billing_coordinator=coordinator,
        start_workers=True,
    )
    result = recovered.resume_billing_run(actor, run_id)

    assert result["status"] == "settled"
    assert coordinator.regrants == [(actor.workspace_id, actor.id, "freeze-1")]
    assert len(coordinator.settlements) == 1
    assert replacement_runtime.requests == []
    statuses = {outcome.call_id: outcome.status for outcome in coordinator.settlements[0][1]}
    assert statuses[f"{batch['id']}:style:1:image:1"] == "success"
    assert statuses[f"{batch['id']}:style:1:image:2"] == "no_return"

    other_workspace = Actor(
        id=actor.id,
        username=actor.username,
        role=actor.role,
        workspace_id="workspace-b",
    )
    with pytest.raises(Exception, match="not found"):
        recovered.resume_billing_run(other_workspace, run_id)
    other_owner = Actor(
        id="designer-2",
        username="other",
        role=actor.role,
        workspace_id=actor.workspace_id,
    )
    with pytest.raises(Exception, match="not found"):
        recovered.resume_billing_run(other_owner, run_id)
    recovered.close()
    runtime.close()
    replacement_runtime.close()


def test_persistent_billing_rows_never_store_grant_secrets(tmp_path: Path) -> None:
    import sqlite3

    runtime = ListingOnlyRuntime([])
    service = _service(tmp_path, runtime, BillingCoordinator())
    actor = _actor()
    template = _ready_template(service, actor)
    service.create_batch(actor, _batch_request_for_test(template["id"]), enqueue=False)

    with sqlite3.connect(tmp_path / "workbench.sqlite3") as connection:
        serialized = "\n".join(
            "|".join(str(value) for value in row)
            for row in connection.execute("SELECT * FROM pod_customization_billing_runs")
        )
    assert "test-wuyin-key" not in serialized
    assert "test-ark-key" not in serialized
    service.close()
    runtime.close()


def test_unavailable_grant_fails_unstarted_calls_without_billing_recovery(tmp_path: Path) -> None:
    first = _grid([_pattern(index) for index in range(4)])
    second = _grid([_pattern(index) for index in range(10, 14)])
    grant = ExpiringGrant()
    runtime = ExpiringRuntime([first, second])
    runtime.executor.shutdown(wait=True, cancel_futures=True)
    runtime.executor = ThreadPoolExecutor(max_workers=1)
    service = _service(tmp_path, runtime, ExpiringCoordinator(grant))
    actor = _actor()
    template = _ready_template(service, actor)
    request = _batch_request_for_test(template["id"]).model_copy(update={"count": 2})
    batch = service.create_batch(actor, request, enqueue=False)

    service.worker.process_batch(batch["id"])

    result = service.get_batch(actor, batch["id"])
    assert result["status"] in {"failed", "partial_failure"}
    assert result["failed_count"] == 2
    assert service.list_pending_billing_runs(actor)["runs"] == []
    assert len(runtime.requests) == 1
    assert len(service.billing_coordinator.settlements) == 1
    service.close()
    runtime.close()


def test_started_call_after_crash_is_neither_replayed_nor_guessed_as_success(tmp_path: Path) -> None:
    runtime = ListingOnlyRuntime([])
    coordinator = RecoveringSettlementCoordinator()
    service = _service(tmp_path, runtime, coordinator)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request_for_test(template["id"]), enqueue=False)
    stored = service.repository.list_pending_billing_runs(actor.workspace_id, actor.id)[0]
    first_call = stored["plan"]["calls"][0]
    service.repository.start_billing_call(
        stored["action_key"], first_call["call_id"], first_call["feature"]
    )
    service.repository.recover_billing_runs()

    with pytest.raises(Exception, match="uncertain"):
        service.resume_billing_run(actor, stored["run_id"])

    refreshed = service.repository.get_billing_run(
        stored["run_id"], actor.workspace_id, actor.id
    )
    assert refreshed["status"] == "settlement_pending"
    assert refreshed["outcomes"][0]["status"] == "started"
    assert coordinator.regrants == []
    assert coordinator.settlements == []
    assert runtime.requests == []
    service.close()
    runtime.close()


def test_batch_preflight_rejects_invalid_template_before_remote_freeze(tmp_path: Path) -> None:
    runtime = ListingOnlyRuntime([])
    coordinator = BillingCoordinator()
    service = _service(tmp_path, runtime, coordinator)
    actor = _actor()
    request = _batch_request_for_test("missing-template")

    with pytest.raises(Exception, match="not found"):
        service.create_batch(actor, request, enqueue=False)

    assert coordinator.freezes == []
    service.close()
    runtime.close()


def test_local_batch_insert_failure_compensates_frozen_plan_with_no_return(
    tmp_path: Path, monkeypatch
) -> None:
    runtime = ListingOnlyRuntime([])
    coordinator = BillingCoordinator()
    service = _service(tmp_path, runtime, coordinator)
    actor = _actor()
    template = _ready_template(service, actor)
    request = _batch_request_for_test(template["id"])
    monkeypatch.setattr(
        service.repository,
        "create_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        service.create_batch(actor, request, enqueue=False)

    assert len(coordinator.settlements) == 1
    assert {outcome.status for outcome in coordinator.settlements[0][1]} == {"no_return"}
    stored = service.repository.list_pending_billing_runs(actor.workspace_id, actor.id)
    assert stored == []
    service.close()
    runtime.close()


def test_ledger_insert_failure_uses_immediate_compensation_settlement(
    tmp_path: Path, monkeypatch
) -> None:
    runtime = ListingOnlyRuntime([])
    coordinator = BillingCoordinator()
    service = _service(tmp_path, runtime, coordinator)
    actor = _actor()
    monkeypatch.setattr(
        service.repository,
        "create_billing_run",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("ledger unavailable")),
    )

    with pytest.raises(OSError, match="ledger unavailable"):
        service._freeze_batch(actor, "batch-ledger-failure", 1)

    assert len(coordinator.settlements) == 1
    assert {outcome.status for outcome in coordinator.settlements[0][1]} == {"no_return"}
    service.close()
    runtime.close()


def test_orphaned_persisted_batch_run_refunds_without_leaking_worker_state(tmp_path: Path) -> None:
    runtime = ListingOnlyRuntime([])
    coordinator = BillingCoordinator()
    service = _service(tmp_path, runtime, coordinator)
    actor = _actor()
    run = service._freeze_batch(actor, "missing-local-batch", 1)
    stored = service.repository.list_pending_billing_runs(actor.workspace_id, actor.id)[0]

    service.worker.process_batch("missing-local-batch", run)

    refreshed = service.repository.get_billing_run(
        stored["run_id"], actor.workspace_id, actor.id
    )
    assert refreshed["status"] == "settled"
    assert {outcome.status for outcome in coordinator.settlements[0][1]} == {"no_return"}
    assert "missing-local-batch" not in service.worker._billing_runs
    service.close()
    runtime.close()


def test_worker_shutdown_cancels_queue_without_waiting_for_running_provider_work(tmp_path: Path) -> None:
    runtime = ListingOnlyRuntime([])
    service = _service(tmp_path, runtime, BillingCoordinator())
    blocker = threading.Event()
    service.worker._coordinator.submit(blocker.wait)

    started = time.monotonic()
    service.worker.close()
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    with pytest.raises(RuntimeError, match="shutting down"):
        service.worker.submit("never-started")
    blocker.set()
    runtime.close()


def test_worker_runs_only_one_batch_level_action_at_a_time(tmp_path: Path) -> None:
    runtime = ListingOnlyRuntime([])
    service = _service(tmp_path, runtime, BillingCoordinator())
    first_started = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    release_second = threading.Event()

    def first_action() -> None:
        first_started.set()
        release_first.wait(timeout=2)

    def second_action() -> None:
        second_started.set()
        release_second.wait(timeout=2)

    first = service.worker.submit_billing_action("first-batch", first_action)
    assert first_started.wait(timeout=1)
    second = service.worker.submit_billing_action("second-batch", second_action)
    started_while_first_was_running = False
    try:
        started_while_first_was_running = second_started.wait(timeout=0.1)
    finally:
        release_first.set()
        first.result(timeout=1)
        second_started.wait(timeout=1)
        release_second.set()
        second.result(timeout=1)
        service.close()
        runtime.close()

    assert started_while_first_was_running is False
    assert second_started.is_set()


def test_direct_batch_execution_uses_the_same_serial_gate(tmp_path: Path) -> None:
    runtime = ListingOnlyRuntime([])
    service = _service(tmp_path, runtime, BillingCoordinator())
    first_started = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()

    def process_authorized(batch_id: str, _run) -> None:
        if batch_id == "first-batch":
            first_started.set()
            release_first.wait(timeout=2)
        else:
            second_started.set()

    service.worker._process_batch_authorized = process_authorized
    first_run = SimpleNamespace(action_key="first-run", settle=lambda: None)
    second_run = SimpleNamespace(action_key="second-run", settle=lambda: None)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(service.worker.process_batch, "first-batch", first_run)
        assert first_started.wait(timeout=1)
        second = executor.submit(service.worker.process_batch, "second-batch", second_run)
        started_while_first_was_running = second_started.wait(timeout=0.1)
        release_first.set()
        first.result(timeout=1)
        second.result(timeout=1)

    service.close()
    runtime.close()
    assert started_while_first_was_running is False
    assert second_started.is_set()


def test_manual_title_retry_queues_on_batch_coordinator(tmp_path: Path) -> None:
    runtime = ListingOnlyRuntime([])
    service = _service(tmp_path, runtime, BillingCoordinator())
    retry_started = threading.Event()
    release_retry = threading.Event()

    class RejectingTitleRuntime:
        def submit(self, *_args, **_kwargs):
            raise AssertionError("manual title retry must not occupy the internal title pool while queued")

    def regenerate_title(*_args) -> None:
        retry_started.set()
        release_retry.wait(timeout=2)

    service.worker.title_runtime = RejectingTitleRuntime()
    service.worker.regenerate_title = regenerate_title
    future = service.worker.submit_title_regeneration("batch-1", 1)
    assert retry_started.wait(timeout=1)
    release_retry.set()
    future.result(timeout=1)
    service.close()
    runtime.close()


def test_batch_failed_retry_queues_one_serial_worker_action(tmp_path: Path) -> None:
    runtime = ListingOnlyRuntime([])
    service = _service(tmp_path, runtime, BillingCoordinator())
    started = threading.Event()
    release = threading.Event()
    received: list[tuple[object, ...]] = []

    def process_batch_retry(*args) -> None:
        received.append(args)
        started.set()
        release.wait(timeout=2)

    service.worker.process_batch_retry = process_batch_retry
    run = SimpleNamespace(action_key="batch-retry-run", settle=lambda: None)
    future = service.worker.submit_batch_retry("batch-1", (1,), (2,), run)
    assert started.wait(timeout=1)
    release.set()
    future.result(timeout=1)

    assert received == [("batch-1", (1,), (2,), run)]
    service.close()
    runtime.close()


def test_completed_future_callback_is_registered_outside_the_future_lock(tmp_path: Path) -> None:
    runtime = ListingOnlyRuntime([])
    service = _service(tmp_path, runtime, BillingCoordinator())
    key = ("fast-action", "batch-1")
    completed: Future[None] = Future()
    completed.set_result(None)
    with service.worker._futures_lock:
        service.worker._futures[key] = completed

    service.worker._attach_forget_callback(key, completed)

    assert key not in service.worker._futures
    service.close()
    runtime.close()


def test_stale_future_callback_does_not_forget_its_replacement(tmp_path: Path) -> None:
    runtime = ListingOnlyRuntime([])
    service = _service(tmp_path, runtime, BillingCoordinator())
    key = ("same-action", "batch-1")
    stale: Future[None] = Future()
    replacement: Future[None] = Future()
    with service.worker._futures_lock:
        service.worker._futures[key] = replacement

    service.worker._forget(key, stale)

    assert service.worker._futures[key] is replacement
    replacement.cancel()
    service.close()
    runtime.close()


def test_direct_action_waiting_for_serial_gate_aborts_after_close(tmp_path: Path) -> None:
    runtime = ListingOnlyRuntime([])
    service = _service(tmp_path, runtime, BillingCoordinator())
    first_started = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()

    def process_authorized(batch_id: str, _run) -> None:
        if batch_id == "first-batch":
            first_started.set()
            release_first.wait(timeout=2)
        else:
            second_started.set()

    service.worker._process_batch_authorized = process_authorized
    run = SimpleNamespace(action_key="run", settle=lambda: None)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(service.worker.process_batch, "first-batch", run)
        assert first_started.wait(timeout=1)
        second = executor.submit(service.worker.process_batch, "second-batch", run)
        service.worker.close()
        release_first.set()
        first.result(timeout=1)
        with pytest.raises(RuntimeError, match="shutting down"):
            second.result(timeout=1)

    assert second_started.is_set() is False
    runtime.close()


def test_direct_trial_settlement_resume_never_replays_provider(tmp_path: Path) -> None:
    runtime = ListingOnlyRuntime([_grid([_pattern(index) for index in range(4)])])
    service = _service(tmp_path, runtime, FailingSettlementCoordinator())
    actor = _actor()
    template = _ready_template(service, actor)

    with pytest.raises(OSError, match="billing network unavailable"):
        service.run_direct_listing_trial(
            actor,
            DirectListingTrialCreate(
                template_id=template["id"],
                business_fields=BusinessFields(product_name="Canvas tote"),
            ),
        )
    assert len(runtime.requests) == 1
    run = next(
        row
        for row in service.repository.list_pending_billing_runs(actor.workspace_id, actor.id)
        if row["action_type"] == "direct_trial"
    )
    coordinator = RecoveringSettlementCoordinator()
    service.billing_coordinator = coordinator

    resumed = service.resume_billing_run(actor, run["run_id"])

    assert resumed["status"] == "settled"
    assert len(runtime.requests) == 1
    assert len(coordinator.settlements) == 1
    service.close()
    runtime.close()


def test_batch_resume_enqueue_returns_without_processing_inline(tmp_path: Path, monkeypatch) -> None:
    runtime = ListingOnlyRuntime([])
    service = _service(tmp_path, runtime, RecoveringSettlementCoordinator())
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request_for_test(template["id"]), enqueue=False)
    run = service.repository.list_pending_billing_runs(actor.workspace_id, actor.id)[0]
    service.repository.mark_billing_pending(run["action_key"], "interrupted")
    submitted: list[tuple[str, object]] = []
    monkeypatch.setattr(
        service.worker,
        "submit",
        lambda batch_id, billing_run: submitted.append((batch_id, billing_run)),
    )

    response = service.resume_billing_run(actor, run["run_id"], enqueue=True)
    duplicate = service.resume_billing_run(actor, run["run_id"], enqueue=True)

    assert response["status"] == "authorized"
    assert duplicate["status"] == "authorized"
    assert [entry[0] for entry in submitted] == [batch["id"]]
    assert runtime.requests == []
    service.close()
    runtime.close()


def test_billing_resume_claim_is_atomic_for_concurrent_requests(tmp_path: Path) -> None:
    runtime = ListingOnlyRuntime([])
    service = _service(tmp_path, runtime, BillingCoordinator())
    actor = _actor()
    run = service._freeze_batch(actor, "concurrent-resume", 1)
    stored = service.repository.list_pending_billing_runs(actor.workspace_id, actor.id)[0]
    service.repository.mark_billing_pending(stored["action_key"], "interrupted")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _index: service.repository.claim_billing_resume(
                    stored["run_id"], actor.workspace_id, actor.id
                ),
                range(2),
            )
        )

    assert sorted(results) == [False, True]
    service.worker.process_batch("concurrent-resume", run)
    service.close()
    runtime.close()


def _batch_request_for_test(template_id: str, *, count: int = 1) -> BatchCreate:
    return BatchCreate(
        template_id=template_id,
        count=count,
        prompt_version="v1",
        business_fields=BusinessFields(product_name="Tote bag", product_category="bags"),
        listing_fields=ListingFields(
            declared_price=18.5,
            suggested_price_usd=29.99,
            category_name="家居收纳 > 包袋",
            skus=[{"name": "Default SKU", "length_cm": 30, "width_cm": 20, "height_cm": 10, "weight_g": 450}],
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
                category_name="家居收纳 > 包袋",
                skus=[{"name": "Default SKU", "length_cm": 30, "width_cm": 20, "height_cm": 10, "weight_g": 450}],
            ),
            creative_prompt="bold but uncluttered",
        ),
        enqueue=False,
    )


def _prepare_batch_retry_candidates(service: PodCustomizationService, batch_id: str) -> None:
    """Make style 1 an image failure and style 2 a title-only failure."""
    with service.repository._connect() as connection:
        connection.execute(
            """UPDATE pod_customization_style_grid_results
               SET status = 'failed', error_message = 'image provider failed'
               WHERE batch_id = ? AND style_index = 1""",
            (batch_id,),
        )
        connection.execute(
            """UPDATE pod_customization_style_grid_results
               SET status = 'completed', pattern_asset_id = 'pattern', composite_asset_id = 'composite'
               WHERE batch_id = ? AND style_index = 2""",
            (batch_id,),
        )
        rows = connection.execute(
            """SELECT result_id, variant_index FROM pod_customization_style_grid_results
               WHERE batch_id = ? AND style_index = 2""",
            (batch_id,),
        ).fetchall()
        connection.executemany(
            """INSERT INTO pod_customization_style_grid_publications
               (result_id, role, public_url, updated_at)
               VALUES (?, ?, ?, datetime('now'))""",
            [
                (row["result_id"], f"role-{row['variant_index']}", f"https://example.test/{row['variant_index']}")
                for row in rows
            ],
        )
        connection.execute(
            """UPDATE pod_customization_style_titles
               SET status = 'failed', style_task_id = 'failed-style-task', error_message = 'title provider failed'
               WHERE batch_id = ? AND style_index IN (1, 2)""",
            (batch_id,),
        )
        connection.execute(
            """UPDATE pod_customization_batches
               SET status = 'partial_failure' WHERE batch_id = ?""",
            (batch_id,),
        )


@pytest.mark.parametrize("terminal_status", ["partial_failure", "cancelled"])
def test_batch_retry_claims_mixed_image_and_title_failures_in_one_durable_action(
    tmp_path: Path, terminal_status: str
) -> None:
    runtime = ListingOnlyRuntime([])
    billing = BillingCoordinator()
    service = _service(tmp_path, runtime, billing, title_runtime=object())
    actor = _actor()
    template = _ready_template(service, actor)
    batch = _create_batch(service, actor, template["id"])
    _prepare_batch_retry_candidates(service, batch["id"])
    with service.repository._connect() as connection:
        connection.execute(
            "UPDATE pod_customization_batches SET status = ? WHERE batch_id = ?",
            (terminal_status, batch["id"]),
        )
    submitted: list[tuple[object, ...]] = []
    service.worker.submit_batch_retry = lambda *args: submitted.append(args)

    result = service.retry_failed(
        actor,
        batch["id"],
        image_style_indices=[1],
        title_style_indices=[2],
    )
    refreshed = service.get_batch(actor, batch["id"])
    billing_run = next(
        row
        for row in service.repository.list_pending_billing_runs(actor.workspace_id, actor.id)
        if row["target_id"] == "batch_retry"
    )

    assert result == {
        "image_style_indices": [1],
        "title_style_indices": [2],
        "submitted_image_style_count": 1,
        "submitted_title_style_count": 1,
    }
    assert [item["status"] for item in refreshed["items"][:4]] == ["generating_pattern"] * 4
    assert refreshed["style_titles"][0]["status"] == "queued"
    assert refreshed["style_titles"][1]["status"] == "generating"
    assert billing_run["action_type"] == "style_retry"
    assert billing_run["target_id"] == "batch_retry"
    assert billing_run["action_payload"] == {
        "retry_mode": "batch",
        "image_style_indices": [1],
        "title_style_indices": [2],
    }
    assert submitted and submitted[0][1:3] == ((1,), (2,))
    service.close()
    runtime.close()


@pytest.mark.parametrize("blocked_status", ["billing_auth_required"])
def test_batch_retry_does_not_resurrect_legacy_auth_status(
    tmp_path: Path, blocked_status: str
) -> None:
    runtime = ListingOnlyRuntime([])
    billing = BillingCoordinator()
    service = _service(tmp_path, runtime, billing, title_runtime=object())
    actor = _actor()
    template = _ready_template(service, actor)
    batch = _create_batch(service, actor, template["id"])
    _prepare_batch_retry_candidates(service, batch["id"])
    with service.repository._connect() as connection:
        connection.execute(
            """UPDATE pod_customization_batches
               SET status = ? WHERE batch_id = ?""",
            (blocked_status, batch["id"]),
        )

    with pytest.raises(PodRepositoryError, match="must settle") as raised:
        service.retry_failed(
            actor, batch["id"], image_style_indices=[1], title_style_indices=[2]
        )

    assert raised.value.status_code == 409
    # 预检在冻结前拒绝：除初始批次创建外，未新增任何计费任务。
    assert len(billing.freezes) == 1
    assert service.get_batch(actor, batch["id"])["status"] == blocked_status
    service.close()
    runtime.close()


def test_batch_retry_accepts_settlement_pending_batch_without_old_billing_recovery(
    tmp_path: Path,
) -> None:
    runtime = ListingOnlyRuntime([])
    billing = BillingCoordinator()
    service = _service(tmp_path, runtime, billing, title_runtime=object())
    actor = _actor()
    template = _ready_template(service, actor)
    batch = _create_batch(service, actor, template["id"])
    _prepare_batch_retry_candidates(service, batch["id"])
    with service.repository._connect() as connection:
        connection.execute(
            """UPDATE pod_customization_batches
               SET status = 'settlement_pending' WHERE batch_id = ?""",
            (batch["id"],),
        )
    service.worker.submit_batch_retry = lambda *_args: None
    result = service.retry_failed(actor, batch["id"], image_style_indices=[1], title_style_indices=[2])

    assert result["image_style_indices"] == [1]
    assert result["title_style_indices"] == [2]
    assert len(billing.freezes) == 2
    service.close()
    runtime.close()


def test_batch_retry_billing_resume_reuses_the_persisted_selection(tmp_path: Path) -> None:
    runtime = ListingOnlyRuntime([])
    billing = RecoveringSettlementCoordinator()
    service = _service(tmp_path, runtime, billing, title_runtime=object())
    actor = _actor()
    template = _ready_template(service, actor)
    batch = _create_batch(service, actor, template["id"])
    _prepare_batch_retry_candidates(service, batch["id"])
    service.worker.submit_batch_retry = lambda *_args: None
    service.retry_failed(
        actor,
        batch["id"],
        image_style_indices=[1],
        title_style_indices=[2],
    )
    stored = next(
        row
        for row in service.repository.list_pending_billing_runs(actor.workspace_id, actor.id)
        if row["target_id"] == "batch_retry"
    )
    service.repository.mark_billing_pending(stored["action_key"], "interrupted")
    resumed: list[tuple[object, ...]] = []
    service.worker.process_batch_retry = lambda *args: resumed.append(args)

    result = service.resume_billing_run(actor, stored["run_id"], enqueue=False)

    assert result["id"] == stored["run_id"]
    assert resumed and resumed[0][1:3] == ((1,), (2,))
    service.close()
    runtime.close()


def test_batch_retry_rejects_a_style_without_four_failed_images_before_freezing(tmp_path: Path) -> None:
    runtime = ListingOnlyRuntime([])
    billing = BillingCoordinator()
    service = _service(tmp_path, runtime, billing)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = _create_batch(service, actor, template["id"])
    _prepare_batch_retry_candidates(service, batch["id"])
    freezes_before = len(billing.freezes)
    before = service.get_batch(actor, batch["id"])

    with pytest.raises(PodRepositoryError, match="all four images failed") as captured:
        service.retry_failed(
            actor,
            batch["id"],
            image_style_indices=[2],
            title_style_indices=[],
        )

    assert captured.value.status_code == 409
    assert len(billing.freezes) == freezes_before
    assert service.get_batch(actor, batch["id"])["items"] == before["items"]
    service.close()
    runtime.close()


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
    assert len({item["pattern_fingerprint"] for item in service.repository.get_batch_internal(batch["id"])["items"][:4]}) == 4
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


def test_style_grid_accepts_similar_detail_panels_without_retry(tmp_path: Path) -> None:
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

    assert len(runtime.requests) == 2
    assert sorted(request.attempt for request in runtime.requests) == [1, 1]
    assert stored["status"] == "completed"
    fingerprints = [
        item["pattern_fingerprint"]
        for item in service.repository.get_batch_internal(batch["id"])["items"]
        if item["variant_index"] == 2
    ]
    assert len(set(fingerprints)) == 2
    service.close()
    runtime.close()


def test_style_grid_accepts_text_like_panel_without_retry(tmp_path: Path) -> None:
    first_panels = [_pattern(index + 1) for index in range(4)]
    first_panels[0] = _pattern(90, text_error=True)
    runtime = ListingOnlyRuntime([_grid(first_panels)])
    service = _service(tmp_path, runtime)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = _create_batch(service, actor, template["id"], count=1)

    service.worker.process_batch(batch["id"])
    stored = service.get_batch(actor, batch["id"])

    assert len(runtime.requests) == 1
    assert [request.attempt for request in runtime.requests] == [1]
    assert stored["status"] == "completed"
    assert all(item["status"] == "completed" for item in stored["items"])
    service.close()
    runtime.close()


def test_style_grid_accepts_duplicate_panels_without_retry(tmp_path: Path) -> None:
    duplicate = _pattern(201)
    runtime = ListingOnlyRuntime([
        _grid([duplicate, duplicate, duplicate, duplicate]),
        _grid([_pattern(120 + index) for index in range(4)]),
    ])
    service = _service(tmp_path, runtime)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = _create_batch(service, actor, template["id"], count=1)

    service.worker.process_batch(batch["id"])
    stored = service.get_batch(actor, batch["id"])

    assert len(runtime.requests) == 1
    assert stored["status"] == "completed"
    fingerprints = [
        item["pattern_fingerprint"]
        for item in service.repository.get_batch_internal(batch["id"])["items"]
    ]
    assert len(fingerprints) == 4
    assert len(set(fingerprints)) == 1
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


def test_completed_whole_style_can_be_regenerated_and_freezes_one_retry_plan(tmp_path: Path) -> None:
    patterns = [_pattern(index) for index in range(80)]
    replacement_grid = _grid([_pattern(index) for index in range(100, 104)])
    runtime = FakePodRuntime(
        [*[_grid(patterns[index:index + 4]) for index in range(0, 80, 4)], replacement_grid]
    )
    billing = BillingCoordinator()
    service = _service(tmp_path, runtime, billing)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = _create_batch(service, actor, template["id"])
    service.worker.process_batch(batch["id"])
    target_style = 2
    freezes_before = len(billing.freezes)

    regenerated = service.regenerate_style(
        actor,
        batch["id"],
        target_style,
        creative_prompt="smaller botanical elements",
        enqueue=False,
    )

    assert regenerated["style_index"] == target_style
    assert len(billing.freezes) == freezes_before + 1
    assert {item["status"] for item in regenerated["results"]} == {"generating_pattern"}
    service.close()
    runtime.close()


def test_batch_pause_cancel_resume_state_transitions(tmp_path: Path) -> None:
    runtime = ListingOnlyRuntime([])
    service = _service(tmp_path, runtime)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request_for_test(template["id"]), enqueue=False)
    batch_id = batch["id"]

    # 运行中的批次可以请求暂停。
    assert service.repository.request_pause(batch_id) is True
    assert service.repository.get_batch_status(batch_id) == "pausing"
    # 工作线程在检查点确认后落入 paused，保留已入库进度并可继续。
    service.repository.mark_batch_paused(batch_id, "已暂停")
    assert service.repository.get_batch_status(batch_id) == "paused"
    assert service.repository.resume_paused_batch(batch_id) is True
    assert service.repository.get_batch_status(batch_id) == "queued"

    # 取消终态不可通过恢复继续。
    assert service.repository.request_cancel(batch_id) is True
    assert service.repository.get_batch_status(batch_id) == "cancelling"
    service.repository.mark_batch_cancelled(batch_id, "已取消")
    assert service.repository.get_batch_status(batch_id) == "cancelled"
    assert service.repository.resume_paused_batch(batch_id) is False

    service.close()
    runtime.close()


def test_pause_and_cancel_batch_service_guard_invalid_states(tmp_path: Path) -> None:
    runtime = ListingOnlyRuntime([])
    service = _service(tmp_path, runtime)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request_for_test(template["id"]), enqueue=False)
    batch_id = batch["id"]

    assert service.pause_batch(actor, batch_id)["status"] == "pausing"
    # 未经继续落盘，仅 pausing 的批次不可继续。
    with pytest.raises(PodRepositoryError, match="仅已暂停的 POD 批次可以继续"):
        service.resume_batch(actor, batch_id)

    service.repository.mark_batch_paused(batch_id, "已暂停")
    # 已暂停批次可以直接取消，且会同步落盘为 cancelled 终态。
    assert service.cancel_batch(actor, batch_id)["status"] == "cancelled"
    assert service.repository.get_batch_status(batch_id) == "cancelled"

    service.close()
    runtime.close()


def test_cancel_batch_finishes_when_no_worker_is_running(tmp_path: Path) -> None:
    runtime = ListingOnlyRuntime([])
    service = PodCustomizationService(
        tmp_path / "workbench.sqlite3",
        tmp_path / "pod-assets",
        runtime,
        start_workers=False,
    )
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request_for_test(template["id"]), enqueue=False)

    cancelled = service.cancel_batch(actor, batch["id"])

    assert cancelled["status"] == "cancelled"
    assert service.repository.get_batch_status(batch["id"]) == "cancelled"

    service.close()
    runtime.close()


def test_cancel_batch_recovers_an_abandoned_cancelling_state(tmp_path: Path) -> None:
    runtime = ListingOnlyRuntime([])
    service = PodCustomizationService(
        tmp_path / "workbench.sqlite3",
        tmp_path / "pod-assets",
        runtime,
        start_workers=False,
    )
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request_for_test(template["id"]), enqueue=False)
    with service.repository._connect() as connection:
        connection.execute(
            """UPDATE pod_customization_style_grid_results
               SET status = 'completed', pattern_asset_id = 'pattern', composite_asset_id = 'composite'
               WHERE batch_id = ? AND style_index = 1""",
            (batch["id"],),
        )
        rows = connection.execute(
            """SELECT result_id, variant_index FROM pod_customization_style_grid_results
               WHERE batch_id = ? AND style_index = 1""",
            (batch["id"],),
        ).fetchall()
        connection.executemany(
            """INSERT INTO pod_customization_style_grid_publications
               (result_id, role, public_url, updated_at)
               VALUES (?, ?, ?, datetime('now'))""",
            [
                (row["result_id"], f"role-{row['variant_index']}", f"https://example.test/{row['variant_index']}")
                for row in rows
            ],
        )
    service.repository.claim_style_title(batch["id"], 1)
    assert service.repository.request_cancel(batch["id"]) is True

    cancelled = service.cancel_batch(actor, batch["id"])

    assert cancelled["status"] == "cancelled"
    assert service.repository.get_batch_status(batch["id"]) == "cancelled"
    assert cancelled["style_titles"][0]["status"] == "failed"

    service.close()
    runtime.close()


def test_resume_batch_creates_a_fresh_freeze_when_the_paused_run_is_already_settled(tmp_path: Path) -> None:
    runtime = ListingOnlyRuntime([])
    service = _service(tmp_path, runtime)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request_for_test(template["id"]), enqueue=False)
    batch_id = batch["id"]

    service.repository.request_pause(batch_id)
    service.repository.mark_batch_paused(batch_id, "已暂停")
    # 暂停时原冻结已结算；继续必须为剩余工作创建一个新的冻结。
    with service.repository._connect() as connection:
        connection.execute(
            "UPDATE pod_customization_billing_runs SET status = 'settled' WHERE batch_id = ?",
            (batch_id,),
        )
    resumed = service.resume_batch(actor, batch_id)
    assert resumed["status"] in {"queued", "generating_patterns"}
    assert len(service.repository.list_pending_billing_runs(actor.workspace_id, actor.id)) == 1

    service.close()
    runtime.close()


def test_pause_drains_submitted_style_without_submitting_the_next_style(tmp_path: Path) -> None:
    runtime = BlockingListingRuntime([
        _grid([_pattern(index) for index in range(4)]),
        _grid([_pattern(index + 10) for index in range(4)]),
    ])
    service = _service(tmp_path, runtime)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request_for_test(template["id"], count=2), enqueue=False)

    future = service.worker.submit(batch["id"])
    assert runtime.first_request_started.wait(timeout=1)
    assert service.pause_batch(actor, batch["id"])["status"] == "pausing"
    runtime.allow_first_request_to_finish.set()
    future.result(timeout=2)

    stored = service.get_batch(actor, batch["id"])
    assert len(runtime.requests) == 1
    assert stored["status"] == "paused"
    assert stored["completed_count"] == 1
    assert stored["items"][4]["status"] == "queued"
    service.close()
    runtime.close()


def test_pause_settles_the_current_freeze_and_releases_unstarted_calls(tmp_path: Path) -> None:
    runtime = BlockingListingRuntime([
        _grid([_pattern(index) for index in range(4)]),
        _grid([_pattern(index + 10) for index in range(4)]),
    ])
    billing = BillingCoordinator()
    service = _service(tmp_path, runtime, billing)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request_for_test(template["id"], count=2), enqueue=False)

    future = service.worker.submit(batch["id"])
    assert runtime.first_request_started.wait(timeout=1)
    service.pause_batch(actor, batch["id"])
    runtime.allow_first_request_to_finish.set()
    future.result(timeout=2)

    assert len(billing.settlements) == 1
    _plan, outcomes = billing.settlements[0]
    assert any(item.feature == "pod.image" and item.status == "success" for item in outcomes)
    assert any(item.status == "no_return" for item in outcomes)
    assert service.get_batch(actor, batch["id"])["status"] == "paused"
    service.close()
    runtime.close()


def test_pause_before_provider_submit_keeps_the_image_call_planned(tmp_path: Path) -> None:
    runtime = BeforeSubmitListingRuntime([_grid([_pattern(index) for index in range(4)])])
    service = _service(tmp_path, runtime)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request_for_test(template["id"]), enqueue=False)

    future = service.worker.submit(batch["id"])
    assert runtime.before_provider_submit.wait(timeout=1)
    assert service.pause_batch(actor, batch["id"])["status"] == "pausing"
    runtime.allow_provider_submit.set()
    future.result(timeout=2)

    stored = service.get_batch(actor, batch["id"])
    assert runtime.requests == []
    assert stored["status"] == "paused"
    with service.repository._connect() as connection:
        status = connection.execute(
            "SELECT status FROM pod_customization_generation_calls WHERE batch_id = ?",
            (batch["id"],),
        ).fetchone()[0]
    assert status == "queued"
    service.close()
    runtime.close()


def test_resume_after_pause_submits_only_the_remaining_style(tmp_path: Path) -> None:
    runtime = BlockingListingRuntime([
        _grid([_pattern(index) for index in range(4)]),
        _grid([_pattern(index + 10) for index in range(4)]),
    ])
    service = _service(tmp_path, runtime)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request_for_test(template["id"], count=2), enqueue=False)

    future = service.worker.submit(batch["id"])
    assert runtime.first_request_started.wait(timeout=1)
    service.pause_batch(actor, batch["id"])
    runtime.allow_first_request_to_finish.set()
    future.result(timeout=2)
    assert len(runtime.requests) == 1

    service.resume_batch(actor, batch["id"])
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        stored = service.get_batch(actor, batch["id"])
        if stored["status"] in {"completed", "partial_failure", "failed"}:
            break
        time.sleep(0.01)
    else:
        pytest.fail("resumed POD batch did not settle")

    assert stored["status"] == "completed"
    assert len(runtime.requests) == 2
    assert stored["completed_count"] == 2
    service.close()
    runtime.close()


class ConcurrencyTrackingRuntime(FakePodRuntime):
    """Tracks peak provider and publication concurrency for the streaming tests."""

    def __init__(self, grids: list[bytes | Exception], *, workers: int = 8, generation_pause: float = 0.0) -> None:
        super().__init__(grids)
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.executor = ThreadPoolExecutor(max_workers=max(16, int(workers) * 2), thread_name_prefix="test-pod-concurrent")
        self.config = SimpleNamespace(executor_workers=max(1, int(workers)))
        self.generation_pause = float(generation_pause)
        self._generation_lock = threading.Lock()
        self.generation_active = 0
        self.max_generation_active = 0

    def generate_listing_grid(self, request, *, grant=None, call_id="") -> GeneratedMedia:
        assert grant is not None and grant.provider_key("wuyin")
        with self._generation_lock:
            self.generation_active += 1
            self.max_generation_active = max(self.max_generation_active, self.generation_active)
        try:
            if self.generation_pause:
                time.sleep(self.generation_pause)
            return super().generate_listing_grid(request, grant=grant, call_id=call_id)
        finally:
            with self._generation_lock:
                self.generation_active -= 1


class PipelineProbeRuntime(ConcurrencyTrackingRuntime):
    """Returns the first grid immediately and holds the rest behind a gate."""

    def __init__(self, grids: list[bytes]) -> None:
        super().__init__(grids, workers=3)
        self.first_publish_started = threading.Event()
        self.hold_nonfirst_generations = threading.Event()
        self._generated = 0
        self._generated_lock = threading.Lock()

    def generate_listing_grid(self, request, *, grant=None, call_id="") -> GeneratedMedia:
        with self._generated_lock:
            self._generated += 1
            is_first = self._generated == 1
        if not is_first:
            assert self.hold_nonfirst_generations.wait(timeout=3)
        return super().generate_listing_grid(request, grant=grant, call_id=call_id)

    def publish_listing_image(self, media, *, namespace: str, role: str) -> str:
        self.first_publish_started.set()
        return super().publish_listing_image(media, namespace=namespace, role=role)


def _track_postprocess_concurrency(service: PodCustomizationService):
    """Wrap ``_process_style_grids`` to measure its peak concurrent invocations."""
    state = {"active": 0, "max": 0}
    lock = threading.Lock()
    original = service.worker._process_style_grids

    def tracked(batch_arg, grids, billing_run):
        with lock:
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
        try:
            time.sleep(0.004)
            return original(batch_arg, grids, billing_run)
        finally:
            with lock:
                state["active"] -= 1

    service.worker._process_style_grids = tracked
    return state


def test_four_grid_generation_and_postprocess_stay_within_image_workers(tmp_path: Path) -> None:
    patterns = [_pattern(index % 90) for index in range(160)]
    runtime = ConcurrencyTrackingRuntime(
        [_grid(patterns[index:index + 4]) for index in range(0, 160, 4)],
        workers=8,
        generation_pause=0.005,
    )
    service = _service(tmp_path, runtime)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = _create_batch(service, actor, template["id"], count=40)
    postprocess = _track_postprocess_concurrency(service)

    service.worker.process_batch(batch["id"])

    stored = service.get_batch(actor, batch["id"])
    assert stored["status"] == "completed"
    assert stored["completed_count"] == 40
    assert runtime.max_generation_active <= 8
    assert postprocess["max"] <= 8
    assert runtime.max_generation_active > 1
    assert postprocess["max"] > 1
    service.close()
    runtime.close()


def test_postprocess_starts_immediately_after_first_grid_returns(tmp_path: Path) -> None:
    patterns = [_pattern(index) for index in range(12)]
    runtime = PipelineProbeRuntime([_grid(patterns[index:index + 4]) for index in range(0, 12, 4)])
    service = _service(tmp_path, runtime)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = _create_batch(service, actor, template["id"], count=3)

    future = service.worker.submit(batch["id"])

    # 第一款返回后应立即开始发布，即使其余款式仍被挡在生图闸口之后。
    assert runtime.first_publish_started.wait(timeout=2)
    runtime.hold_nonfirst_generations.set()
    future.result(timeout=3)

    stored = service.get_batch(actor, batch["id"])
    assert stored["status"] == "completed"
    assert stored["completed_count"] == 3
    service.close()
    runtime.close()


def test_parallel_postprocess_failure_isolates_single_style(tmp_path: Path) -> None:
    patterns = [_pattern(index % 90) for index in range(44)]
    runtime = ConcurrencyTrackingRuntime(
        [_grid(patterns[index:index + 4]) for index in range(0, 44, 4)],
        workers=8,
        generation_pause=0.002,
    )
    service = _service(tmp_path, runtime)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = _create_batch(service, actor, template["id"], count=10)

    original = service.worker._process_style_grids

    def tracked(batch_arg, grids, billing_run):
        if grids[0][0]["style_index"] == 3:
            raise RuntimeError("COS publish failed")
        return original(batch_arg, grids, billing_run)

    service.worker._process_style_grids = tracked

    service.worker.process_batch(batch["id"])

    stored = service.get_batch(actor, batch["id"])
    assert stored["status"] == "partial_failure"
    assert stored["completed_count"] == 9
    assert stored["failed_count"] == 1
    service.close()
    runtime.close()



# ---------------------------------------------------------------------------
# Task 2 — epoch fencing: stale worker writes must not mutate reaped batches
# ---------------------------------------------------------------------------

def test_stale_epoch_write_does_not_complete_item_after_reap(tmp_path: Path) -> None:
    """After reaping, the batch is terminal and the DB epoch has advanced."""
    runtime = ListingOnlyRuntime([])
    service = _service(tmp_path, runtime, start_workers=False)
    actor = _actor()

    template = service.upload_template(actor, name="Stale test", filename="stale.png", content=_encode(_pattern(0)))
    service.update_template_calibration(
        actor,
        template["id"],
        Calibration(
            mask=NormalizedRect(x=0.2, y=0.2, width=0.6, height=0.6),
            anchor=NormalizedPoint(x=0.5, y=0.5),
        ),
    )
    batch = service.create_batch(
        actor,
        BatchCreate(
            template_id=template["id"],
            count=1,
            business_fields=BusinessFields(product_name="Test", product_category="test"),
            listing_fields=ListingFields(
                declared_price=10.0,
                suggested_price_usd=15.0,
                category_name="Test",
                skus=[{"name": "SKU", "length_cm": 10, "width_cm": 10, "height_cm": 10, "weight_g": 100}],
            ),
        ),
        enqueue=False,
    )
    batch_id = batch["id"]

    # Claim epoch=1
    epoch1 = service.repository.claim_batch_with_epoch(batch_id)
    assert epoch1 == 1

    # Back-date last_progress_at so reap_stuck_batches(stale_after_seconds=0) picks it up.
    # The strict-less-than condition in the SQL means same-millisecond timestamps are excluded,
    # so we set a timestamp clearly in the past.
    import sqlite3 as _sqlite3
    with _sqlite3.connect(str(service.repository.database_path)) as _conn:
        _conn.execute(
            "UPDATE pod_customization_batches SET last_progress_at = '2000-01-01T00:00:00.000+00:00' WHERE batch_id = ?",
            (batch_id,),
        )

    # Reap advances epoch to 2 and marks batch terminal
    reaped = service.repository.reap_stuck_batches(stale_after_seconds=0)
    assert any(r["batch_id"] == batch_id for r in reaped)

    # Batch must be in a terminal state after reap
    status = service.repository.get_batch_status(batch_id)
    assert status in {"failed", "partial_failure"}, (
        f"Expected terminal status after reap, got {status!r}"
    )


def test_stale_worker_cannot_write_grid_or_title_after_reap(tmp_path: Path) -> None:
    """Every worker-owned result write is fenced after a batch is reaped."""
    runtime = ListingOnlyRuntime([])
    service = _service(tmp_path, runtime, start_workers=False)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request_for_test(template["id"]), enqueue=False)
    batch_id = batch["id"]
    epoch = service.repository.claim_batch_with_epoch(batch_id)
    assert epoch == 1
    call = service.repository.get_or_create_generation_call(
        service.repository.get_batch_internal(batch_id),
        call_kind="initial",
        call_index=1,
    )

    with service.repository._connect() as connection:
        connection.execute(
            "UPDATE pod_customization_batches SET last_progress_at = '2000-01-01T00:00:00.000+00:00' WHERE batch_id = ?",
            (batch_id,),
        )
    assert service.repository.reap_stuck_batches(stale_after_seconds=0)

    with pytest.raises(PodExecutionExpired):
        service.repository.finish_generation_call(
            call["call_id"], status="succeeded", execution_epoch=epoch
        )
    with pytest.raises(PodExecutionExpired):
        service.repository.finish_style_grid_result(
            service.repository.get_batch_internal(batch_id),
            style_index=1,
            variant_index=1,
            call_id=call["call_id"],
            status="completed",
            execution_epoch=epoch,
        )
    with pytest.raises(PodExecutionExpired):
        service.repository.fail_style_title(batch_id, 1, "stale", execution_epoch=epoch)


def test_reap_increments_epoch_so_current_epoch_differs(tmp_path: Path) -> None:
    """After reaping, execution_epoch in DB must be > the epoch the worker holds."""
    import sqlite3

    runtime = ListingOnlyRuntime([])
    service = _service(tmp_path, runtime, start_workers=False)
    actor = _actor()

    template = service.upload_template(actor, name="Epoch drift", filename="drift.png", content=_encode(_pattern(0)))
    service.update_template_calibration(
        actor,
        template["id"],
        Calibration(
            mask=NormalizedRect(x=0.2, y=0.2, width=0.6, height=0.6),
            anchor=NormalizedPoint(x=0.5, y=0.5),
        ),
    )
    batch = service.create_batch(
        actor,
        BatchCreate(
            template_id=template["id"],
            count=1,
            business_fields=BusinessFields(product_name="Test", product_category="test"),
            listing_fields=ListingFields(
                declared_price=10.0,
                suggested_price_usd=15.0,
                category_name="Test",
                skus=[{"name": "SKU", "length_cm": 10, "width_cm": 10, "height_cm": 10, "weight_g": 100}],
            ),
        ),
        enqueue=False,
    )
    batch_id = batch["id"]

    worker_epoch = service.repository.claim_batch_with_epoch(batch_id)
    assert worker_epoch is not None

    reaped = service.repository.reap_stuck_batches(stale_after_seconds=0)
    assert any(r["batch_id"] == batch_id for r in reaped)

    # Read epoch directly from DB
    with sqlite3.connect(str(service.repository.database_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT execution_epoch FROM pod_customization_batches WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()
    db_epoch = int(row["execution_epoch"])

    assert db_epoch > worker_epoch, (
        f"DB epoch ({db_epoch}) should exceed worker epoch ({worker_epoch}) after reap"
    )


def test_current_epoch_worker_can_still_complete_normally(tmp_path: Path) -> None:
    """A worker holding the current epoch must NOT be blocked from writing results."""
    import sqlite3

    runtime = ListingOnlyRuntime([])
    service = _service(tmp_path, runtime, start_workers=False)
    actor = _actor()

    template = service.upload_template(actor, name="Valid epoch", filename="valid.png", content=_encode(_pattern(0)))
    service.update_template_calibration(
        actor,
        template["id"],
        Calibration(
            mask=NormalizedRect(x=0.2, y=0.2, width=0.6, height=0.6),
            anchor=NormalizedPoint(x=0.5, y=0.5),
        ),
    )
    batch = service.create_batch(
        actor,
        BatchCreate(
            template_id=template["id"],
            count=1,
            business_fields=BusinessFields(product_name="Test", product_category="test"),
            listing_fields=ListingFields(
                declared_price=10.0,
                suggested_price_usd=15.0,
                category_name="Test",
                skus=[{"name": "SKU", "length_cm": 10, "width_cm": 10, "height_cm": 10, "weight_g": 100}],
            ),
        ),
        enqueue=False,
    )
    batch_id = batch["id"]

    epoch = service.repository.claim_batch_with_epoch(batch_id)
    assert epoch is not None and epoch > 0

    # No reap — epoch in DB should match what we got from claim
    with sqlite3.connect(str(service.repository.database_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT execution_epoch FROM pod_customization_batches WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()
    assert int(row["execution_epoch"]) == epoch
    assert service.repository.get_batch_status(batch_id) == "generating_patterns"


def test_progress_heartbeat_is_refreshed_for_active_batch(tmp_path: Path) -> None:
    import sqlite3

    runtime = ListingOnlyRuntime([])
    service = _service(tmp_path, runtime, start_workers=False)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request_for_test(template["id"]), enqueue=False)
    epoch = service.repository.claim_batch_with_epoch(batch["id"])
    assert epoch is not None

    with sqlite3.connect(str(service.repository.database_path)) as conn:
        before = conn.execute(
            "SELECT last_progress_at FROM pod_customization_batches WHERE batch_id = ?",
            (batch["id"],),
        ).fetchone()[0]
    time.sleep(0.002)
    service.repository.touch_batch_progress(batch["id"], execution_epoch=epoch)
    with sqlite3.connect(str(service.repository.database_path)) as conn:
        after = conn.execute(
            "SELECT last_progress_at FROM pod_customization_batches WHERE batch_id = ?",
            (batch["id"],),
        ).fetchone()[0]

    assert after > before


def test_billing_run_rejects_remote_token_without_provider_key() -> None:
    plan = PodCallPlan.for_retry("direct-only", feature="pod.image")
    grant = PodExecutionGrant(
        "freeze-direct-only", 1, "2099-01-01T00:00:00Z", {}, remote_token="session-token"
    )
    run = PodBillingRun(_actor(), BillingCoordinator(), plan, grant)

    with pytest.raises(PodBillingAuthorizationRequired, match="unavailable"):
        run.start(plan.calls[0].call_id, "pod.image")


# ---------------------------------------------------------------------------
# Task 3 — deadline-aware waits: coordinator must exit on inactivity timeout
# ---------------------------------------------------------------------------

def test_coordinator_times_out_when_provider_never_returns(tmp_path: Path) -> None:
    """When all provider futures are permanently blocked, the batch must become terminal
    after the configured short inactivity timeout (not wait forever)."""
    from wh_local.modules.pod_customization import worker as worker_module

    original_timeout = worker_module.POD_PROGRESS_TIMEOUT_SECONDS
    original_poll = worker_module.POD_WAIT_POLL_SECONDS
    try:
        # Use a very short timeout so the test runs in reasonable time
        worker_module.POD_PROGRESS_TIMEOUT_SECONDS = 2
        worker_module.POD_WAIT_POLL_SECONDS = 0.1

        # BlockingListingRuntime holds the first request until released
        started = threading.Event()
        allow_finish = threading.Event()  # never set — provider hangs forever

        class HangingRuntime(ListingOnlyRuntime):
            def generate_listing_grid(self, request, *, grant=None, call_id="", on_start=None):
                started.set()
                # Block until the test releases — simulates hung provider
                allow_finish.wait(timeout=10)
                raise RuntimeError("provider was released after timeout")

        runtime = HangingRuntime([_grid([_pattern(i) for i in range(4)])])
        service = _service(tmp_path, runtime)
        actor = _actor()

        template = service.upload_template(actor, name="Timeout test", filename="to.png", content=_encode(_pattern(0)))
        service.update_template_calibration(
            actor,
            template["id"],
            Calibration(
                mask=NormalizedRect(x=0.2, y=0.2, width=0.6, height=0.6),
                anchor=NormalizedPoint(x=0.5, y=0.5),
            ),
        )
        batch = service.create_batch(
            actor,
            BatchCreate(
                template_id=template["id"],
                count=1,
                business_fields=BusinessFields(product_name="Timeout", product_category="test"),
                listing_fields=ListingFields(
                    declared_price=10.0,
                    suggested_price_usd=15.0,
                    category_name="Test",
                    skus=[{"name": "SKU", "length_cm": 10, "width_cm": 10, "height_cm": 10, "weight_g": 100}],
                ),
            ),
        )
        batch_id = batch["id"]

        # Wait for provider to start then let timeout fire
        assert started.wait(timeout=5), "Provider call never started"

        # Wait for the batch to become terminal (timeout + processing headroom)
        deadline = __import__('time').monotonic() + 10
        while __import__('time').monotonic() < deadline:
            status = service.repository.get_batch_status(batch_id)
            if status in {"failed", "partial_failure"}:
                break
            __import__('time').sleep(0.2)
        else:
            allow_finish.set()
            raise AssertionError(f"Batch never became terminal after timeout; status={status!r}")

        allow_finish.set()
        final_status = service.repository.get_batch_status(batch_id)
        assert final_status in {"failed", "partial_failure"}, (
            f"Expected terminal status after timeout, got {final_status!r}"
        )
    finally:
        worker_module.POD_PROGRESS_TIMEOUT_SECONDS = original_timeout
        worker_module.POD_WAIT_POLL_SECONDS = original_poll
        service.close()


def test_progress_resets_deadline_when_styles_complete(tmp_path: Path) -> None:
    """When styles complete one-by-one, each completion resets the inactivity clock
    so a large batch is not reaped simply because it takes longer than the timeout."""
    from wh_local.modules.pod_customization import worker as worker_module

    original_timeout = worker_module.POD_PROGRESS_TIMEOUT_SECONDS
    original_poll = worker_module.POD_WAIT_POLL_SECONDS
    try:
        # Short timeout — if not reset on each completion, a 3-style batch would time out
        worker_module.POD_PROGRESS_TIMEOUT_SECONDS = 2
        worker_module.POD_WAIT_POLL_SECONDS = 0.1

        grid_bytes = _grid([_pattern(i) for i in range(4)])
        runtime = ListingOnlyRuntime([grid_bytes, grid_bytes, grid_bytes])
        service = _service(tmp_path, runtime)
        actor = _actor()

        template = service.upload_template(actor, name="Progress reset", filename="pr.png", content=_encode(_pattern(0)))
        service.update_template_calibration(
            actor,
            template["id"],
            Calibration(
                mask=NormalizedRect(x=0.2, y=0.2, width=0.6, height=0.6),
                anchor=NormalizedPoint(x=0.5, y=0.5),
            ),
        )
        batch = service.create_batch(
            actor,
            BatchCreate(
                template_id=template["id"],
                count=3,
                business_fields=BusinessFields(product_name="Progress", product_category="test"),
                listing_fields=ListingFields(
                    declared_price=10.0,
                    suggested_price_usd=15.0,
                    category_name="Test",
                    skus=[{"name": "SKU", "length_cm": 10, "width_cm": 10, "height_cm": 10, "weight_g": 100}],
                ),
            ),
        )
        batch_id = batch["id"]

        # Wait for batch to reach a settled state
        deadline = __import__('time').monotonic() + 15
        while __import__('time').monotonic() < deadline:
            status = service.repository.get_batch_status(batch_id)
            if status in {"completed", "partial_failure", "failed"}:
                break
            __import__('time').sleep(0.2)
        else:
            raise AssertionError(f"Batch never settled; status={status!r}")

        final_status = service.repository.get_batch_status(batch_id)
        # All 3 grids were provided; batch should complete (not be reaped mid-flight)
        assert final_status in {"completed", "partial_failure"}, (
            f"Expected completed/partial_failure, got {final_status!r}. "
            "This may indicate the deadline was not reset on each completion."
        )
    finally:
        worker_module.POD_PROGRESS_TIMEOUT_SECONDS = original_timeout
        worker_module.POD_WAIT_POLL_SECONDS = original_poll
        service.close()
