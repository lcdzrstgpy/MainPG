from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from wh_local.app.main import create_app
from wh_local.modules.pod_customization.ai_runtime import PodCustomizationAiRuntime
from wh_local.modules.pod_customization.billing_contract import (
    PodBillingAuthorizationRequired,
    PodCallPlan,
    PodExecutionGrant,
)
from wh_local.modules.pod_customization.contracts import BusinessFields
from wh_local.modules.pod_customization.repository import PodCustomizationRepository
from wh_local.modules.pod_customization.runtime import (
    AiRuntime,
    AiRuntimeConfig,
    RuntimeClosedError,
)
from wh_local.modules.pod_customization.runtime_contracts import DirectListingGridRequest
from wh_local.modules.pod_customization.title_runtime import PodTitleRequest, PodTitleRuntime
from wh_local.modules.pod_customization.worker import PodBatchWorker


class _ClosingSession:
    def __init__(self) -> None:
        self.closed = threading.Event()
        self.requests: list[object] = []

    def close(self) -> None:
        self.closed.set()


def _grant() -> PodExecutionGrant:
    return PodExecutionGrant(
        "freeze-shutdown",
        1,
        "2099-01-01T00:00:00Z",
        {"ark": "ark-key", "wuyin": "wuyin-key"},
    )


def test_runtime_close_is_bounded_and_interrupts_provider_slot_waiter() -> None:
    session = _ClosingSession()
    runtime = AiRuntime(
        AiRuntimeConfig(
            name="shutdown-test",
            executor_workers=2,
            provider_concurrency=1,
        ),
        session=session,
    )
    holder_ready = threading.Event()
    release_holder = threading.Event()

    def hold_provider_slot() -> None:
        with runtime.provider_slot():
            holder_ready.set()
            release_holder.wait(timeout=5)

    holder = runtime.submit(hold_provider_slot)
    assert holder_ready.wait(timeout=1)
    waiter = runtime.submit(lambda: runtime.provider_slot().__enter__())

    started = time.monotonic()
    runtime.close(wait=False, cancel_futures=True)
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert session.closed.is_set()
    with pytest.raises(RuntimeClosedError):
        waiter.result(timeout=1)
    release_holder.set()
    holder.result(timeout=1)


def test_runtime_close_interrupts_rate_limiter_wait() -> None:
    runtime = AiRuntime(
        AiRuntimeConfig(
            name="rate-shutdown-test",
            executor_workers=1,
            requests_per_minute=1,
        ),
        session=_ClosingSession(),
    )
    runtime.acquire_request_token()
    waiting = runtime.submit(runtime.acquire_request_token)
    time.sleep(0.05)

    runtime.close(wait=False, cancel_futures=True)

    with pytest.raises(RuntimeClosedError):
        waiting.result(timeout=1)


def test_image_download_shutdown_is_injectable_and_uses_bounded_timeout() -> None:
    started = threading.Event()
    captured: dict[str, object] = {}

    def blocking_fetcher(_url: str, **kwargs):
        captured.update(kwargs)
        shutdown_event = kwargs["shutdown_event"]
        started.set()
        assert isinstance(shutdown_event, threading.Event)
        shutdown_event.wait(timeout=5)
        return SimpleNamespace(content=b"never-decoded")

    runtime = PodCustomizationAiRuntime(
        image_workers=1,
        public_image_fetcher=blocking_fetcher,
        public_image_timeout_seconds=15,
    )
    future = runtime.submit(runtime._download_suchuang_grid, "https://images.example.com/result.png")
    assert started.wait(timeout=1)

    started_at = time.monotonic()
    runtime.close(wait=False, cancel_futures=True)
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.5
    assert captured["timeout_seconds"] == 15
    with pytest.raises(RuntimeClosedError):
        future.result(timeout=1)


def test_title_shutdown_before_http_keeps_call_unstarted() -> None:
    session = _ClosingSession()
    runtime = PodTitleRuntime(
        executor_workers=1,
        provider_concurrency=1,
        session=session,
    )
    assert runtime._providers.acquire(timeout=0.1)
    starts: list[str] = []
    request = PodTitleRequest(
        style_task_id="style-shutdown",
        style_index=1,
        hero_image=b"image",
        hero_content_type="image/png",
        business_fields=BusinessFields(product_name="Canvas tote"),
        creative_prompt="geometric lines",
    )
    future = runtime.submit(
        runtime.generate_title,
        request,
        grant=_grant(),
        call_id="style-shutdown:title:1",
        on_start=starts.append,
    )
    time.sleep(0.05)

    runtime.close(wait=False, cancel_futures=True)

    with pytest.raises(RuntimeClosedError):
        future.result(timeout=1)
    assert starts == []
    assert session.requests == []
    runtime._providers.release()


def test_title_blocking_request_is_released_by_session_shutdown() -> None:
    class BlockingRequestSession(_ClosingSession):
        def post(self, *_args, **_kwargs):
            self.requests.append(object())
            self.closed.wait(timeout=5)
            raise OSError("transport closed")

    session = BlockingRequestSession()
    runtime = PodTitleRuntime(session=session)
    starts: list[str] = []
    outcomes: list[tuple[str, str]] = []
    request = PodTitleRequest(
        style_task_id="style-blocking-request",
        style_index=1,
        hero_image=b"image",
        hero_content_type="image/png",
        business_fields=BusinessFields(product_name="Canvas tote"),
        creative_prompt="geometric lines",
    )
    future = runtime.submit(
        runtime.generate_title,
        request,
        grant=_grant(),
        call_id="style-blocking-request:title:1",
        on_start=starts.append,
        on_outcome=lambda call_id, status: outcomes.append((call_id, status)),
    )
    deadline = time.monotonic() + 1
    while not session.requests and time.monotonic() < deadline:
        time.sleep(0.01)
    assert session.requests

    started_at = time.monotonic()
    runtime.close(wait=False, cancel_futures=True)
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.5
    with pytest.raises(RuntimeClosedError):
        future.result(timeout=1)
    assert starts == ["style-blocking-request:title:1"]
    assert outcomes == [("style-blocking-request:title:1", "no_return")]


def test_worker_converts_preflight_runtime_shutdown_to_recoverable_auth_pause() -> None:
    class Repository:
        def __init__(self) -> None:
            self.running: list[str] = []
            self.failed: list[str] = []

        def mark_generation_call_running(self, call_id: str) -> None:
            self.running.append(call_id)

        def finish_generation_call(self, call_id: str, **_kwargs) -> None:
            self.failed.append(call_id)

    class ClosedBeforeStartRuntime:
        def generate_listing_grid(self, _request, *, grant, call_id, on_start):
            raise RuntimeClosedError("runtime is closed")

    class BillingRun:
        grant = _grant()

        def __init__(self) -> None:
            self.starts: list[tuple[str, str]] = []
            self.outcomes: list[tuple[str, str, str]] = []

        def start(self, call_id: str, feature: str) -> None:
            self.starts.append((call_id, feature))

        def record(self, call_id: str, feature: str, status: str) -> None:
            self.outcomes.append((call_id, feature, status))

    worker = object.__new__(PodBatchWorker)
    worker.repository = Repository()
    worker.ai_runtime = ClosedBeforeStartRuntime()
    worker._closing = threading.Event()
    billing_run = BillingRun()

    with pytest.raises(PodBillingAuthorizationRequired, match="resume"):
        worker._generate_listing_grid(
            {"workspace_id": "workspace", "owner_user_id": "owner"},
            {"call_id": "generation-1", "call_index": 1},
            DirectListingGridRequest(
                trial_id="trial-1",
                template_id="template-1",
                template_image=b"image",
                template_content_type="image/png",
                prompt="prompt",
                attempt=1,
            ),
            billing_run,
            "batch-1:style:1:image:1",
        )

    assert billing_run.starts == []
    assert billing_run.outcomes == []
    assert worker.repository.running == ["generation-1"]
    assert worker.repository.failed == []


def test_worker_shutdown_persists_planned_calls_for_regrant_resume(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    repository = PodCustomizationRepository(database_path)
    plan = PodCallPlan.for_retry("shutdown-action", feature="pod.image")
    stored = repository.create_billing_run(
        action_key="shutdown-action",
        action_type="item_retry",
        target_id="item-1",
        batch_id="",
        actor_id="owner-1",
        workspace_id="workspace-1",
        plan=plan,
        grant=_grant(),
    )
    worker = object.__new__(PodBatchWorker)
    worker.repository = repository
    worker._closing = threading.Event()
    worker._futures = {}
    worker._futures_lock = threading.Lock()
    worker._coordinator = ThreadPoolExecutor(max_workers=1)

    worker.close()

    reopened = PodCustomizationRepository(database_path)
    recovered = reopened.get_billing_run(stored["run_id"], "workspace-1", "owner-1")
    assert recovered["status"] == "auth_required"
    assert [row["status"] for row in recovered["outcomes"]] == ["planned"]
    assert reopened.claim_billing_resume(stored["run_id"], "workspace-1", "owner-1")


def test_app_lifespan_uses_non_blocking_runtime_shutdown(tmp_path) -> None:
    events: list[object] = []

    class Service:
        def close(self) -> None:
            events.append("service")

    class Runtime:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self, **kwargs) -> None:
            events.append((self.name, kwargs))

    app = create_app(tmp_path / "runtime.sqlite3")
    original_service = app.state.pod_customization_service
    original_title = app.state.pod_customization_title_runtime
    original_image = app.state.pod_customization_ai_runtime
    app.state.shop_collection_worker = None
    app.state.pod_customization_service = Service()
    app.state.pod_customization_title_runtime = Runtime("title")
    app.state.pod_customization_ai_runtime = Runtime("image")
    client = TestClient(app)
    try:
        client.__enter__()
        started_at = time.monotonic()
        client.__exit__(None, None, None)
        shutdown_elapsed = time.monotonic() - started_at
    finally:
        original_service.close()
        original_title.close(wait=False, cancel_futures=True)
        original_image.close(wait=False, cancel_futures=True)

    assert shutdown_elapsed < 0.5
    assert events == [
        "service",
        ("title", {"wait": False, "cancel_futures": True}),
        ("image", {"wait": False, "cancel_futures": True}),
    ]
