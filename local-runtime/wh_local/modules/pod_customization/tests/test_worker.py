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
from wh_local.modules.pod_customization.billing_contract import PodExecutionGrant
from wh_local.modules.pod_customization.repository import PodRepositoryError
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


def _service(tmp_path: Path, runtime: FakePodRuntime, billing=None, *, title_runtime=None) -> PodCustomizationService:
    return PodCustomizationService(
        tmp_path / "workbench.sqlite3",
        tmp_path / "pod-assets",
        runtime,
        title_runtime=title_runtime,
        billing_coordinator=billing or BillingCoordinator(),
        start_workers=True,
    )


def test_worker_without_in_memory_grant_pauses_for_billing_auth(tmp_path: Path) -> None:
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

    assert service.get_batch(actor, batch["id"])["status"] == "billing_auth_required"
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


def test_expired_grant_pauses_unstarted_calls_and_resume_does_not_replay_success(tmp_path: Path) -> None:
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

    assert service.get_batch(actor, batch["id"])["status"] == "billing_auth_required"
    pending = service.list_pending_billing_runs(actor)["runs"]
    assert pending[0]["status"] == "auth_required"
    assert len(runtime.requests) == 1

    grant.expired = False
    resumed = service.resume_billing_run(actor, pending[0]["id"])

    assert resumed["status"] == "settled"
    assert len(runtime.requests) == 2
    recovered_batch = service.get_batch(actor, batch["id"])
    assert recovered_batch["completed_count"] == 2
    assert sum(item["status"] == "completed" for item in recovered_batch["items"]) == 8
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
    service.repository.mark_billing_auth_required(run["action_key"], "restart")
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
    service.repository.mark_billing_auth_required(stored["action_key"], "restart")

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
def test_batch_retry_rejects_billing_interrupted_batch_before_freezing(
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

    with pytest.raises(PodRepositoryError, match="billing is not recovered") as raised:
        service.retry_failed(
            actor, batch["id"], image_style_indices=[1], title_style_indices=[2]
        )

    assert raised.value.status_code == 409
    # 预检在冻结前拒绝：除初始批次创建外，未新增任何计费任务。
    assert len(billing.freezes) == 1
    assert service.get_batch(actor, batch["id"])["status"] == blocked_status
    service.close()
    runtime.close()


def test_batch_retry_allows_settlement_pending_batch_before_old_billing_is_recovered(
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

    service.retry_failed(actor, batch["id"], image_style_indices=[1], title_style_indices=[2])

    assert len(billing.freezes) == 2
    assert service.get_batch(actor, batch["id"])["status"] == "generating_patterns"
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
    service.repository.mark_billing_auth_required(stored["action_key"], "restart")
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


def test_completed_whole_style_retry_is_rejected_without_freezing_or_mutating(tmp_path: Path) -> None:
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
    before_items = service.get_batch(actor, batch["id"])["items"]
    target_style = 2
    freezes_before = len(billing.freezes)

    with pytest.raises(PodRepositoryError, match="only a failed POD style") as captured:
        service.regenerate_style(
            actor,
            batch["id"],
            target_style,
            creative_prompt="smaller botanical elements",
            enqueue=False,
        )
    after_items = service.get_batch(actor, batch["id"])["items"]

    assert captured.value.status_code == 409
    assert len(billing.freezes) == freezes_before
    assert after_items == before_items
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


def test_resume_batch_requires_pending_billing_run(tmp_path: Path) -> None:
    runtime = ListingOnlyRuntime([])
    service = _service(tmp_path, runtime)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request_for_test(template["id"]), enqueue=False)
    batch_id = batch["id"]

    service.repository.request_pause(batch_id)
    service.repository.mark_batch_paused(batch_id, "已暂停")
    # 计费 run 已结算后不再可恢复，继续应被拒绝。
    with service.repository._connect() as connection:
        connection.execute(
            "UPDATE pod_customization_billing_runs SET status = 'settled' WHERE batch_id = ?",
            (batch_id,),
        )
    with pytest.raises(PodRepositoryError, match="缺少可恢复的计费授权"):
        service.resume_batch(actor, batch_id)
    # 批次仍处于 paused，未被误改。
    assert service.repository.get_batch_status(batch_id) == "paused"

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
