from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock

import pytest
from fastapi import APIRouter, BackgroundTasks, FastAPI
from fastapi.testclient import TestClient

from wh_local.data_collection.plugin_queue import DataCollectionPluginQueue


class _Budget:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    def reserve(self, *, workspace_id: str, provider_fingerprint: str, max_api_calls: int, api_calls: int = 1):
        self.calls.append((workspace_id, provider_fingerprint, api_calls))
        return type("State", (), {"reservation_granted": True})()


class _ExhaustedBudget(_Budget):
    def reserve(self, **kwargs):
        self.calls.append((kwargs["workspace_id"], kwargs["provider_fingerprint"], kwargs["api_calls"]))
        return type("State", (), {"reservation_granted": False})()


class _Provider:
    def __init__(self) -> None:
        self.calls = 0

    def get_item_detail(self, offer_id: str):
        self.calls += 1
        return type(
            "Result",
            (),
            {
                "ok": True,
                "response": {
                    "item": {
                        "offer_id": offer_id,
                        "detail_url": f"https://detail.1688.com/offer/{offer_id}.html",
                        "title": "Capture item",
                        "main_image_url": "https://img.example.com/main.jpg",
                    }
                },
                "audit": None,
            },
        )()


class _Drafts:
    def __init__(self) -> None:
        self.by_candidate: dict[str, dict] = {}
        self.intakes: list[dict] = []
        self.materialized: list[str] = []

    @property
    def repository(self):
        return self

    def draft_by_candidate(self, candidate_id: str, workspace_id: str):
        return self.by_candidate.get(candidate_id)

    def intake_shop_candidate(self, *, batch_id: str, workspace_id: str, candidate: dict):
        self.intakes.append({"batch_id": batch_id, "workspace_id": workspace_id, "candidate": candidate})
        draft = {"id": len(self.intakes), "status": "draft", "candidate_id": candidate["candidate_id"]}
        self.by_candidate[candidate["candidate_id"]] = draft
        return {"action": "created", "draft": draft}

    @property
    def media_assets(self):
        return self

    def materialize_until_idle(self, *, workspace_id: str):
        self.materialized.append(workspace_id)
        return {"materialized": 0}


def test_create_app_registers_all_onebound_batch_routes(tmp_path: Path) -> None:
    from wh_local.app.main import create_app

    app = create_app(tmp_path / "workbench.sqlite3")
    registered = {
        (route.path, method)
        for route in app.routes
        for method in (getattr(route, "methods", None) or set())
    }

    for stage in ("prepare", "start", "item", "finish"):
        assert (f"/plugin/product-capture/onebound-batches/{stage}", "POST") in registered
    assert ("/desktop/data-collection/plugin-onebound-batches/{batch_id}/start", "POST") in registered
    assert app.state.plugin_onebound_capture_service is not None


def _client(tmp_path: Path):
    from wh_local.data_collection.plugin_onebound_capture import (
        PluginOneBoundCaptureDependencies,
        register_plugin_onebound_capture_routes,
    )

    queue = DataCollectionPluginQueue(tmp_path / "runtime.sqlite3")
    session = queue.create_session(actor_id="actor-1", workspace_id="workspace-1")
    provider = _Provider()
    drafts = _Drafts()
    router = APIRouter()
    register_plugin_onebound_capture_routes(
        router,
        PluginOneBoundCaptureDependencies(
            plugin_queue=queue,
            provider_config_resolver=lambda _actor: {"api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com"},
            provider_factory=lambda _config: provider,
            budget=_Budget(),
            draft_writer=drafts,
        ),
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), session, provider, drafts


def test_onebound_capture_batch_is_session_scoped_idempotent_and_materializes(tmp_path: Path) -> None:
    client, session, provider, drafts = _client(tmp_path)
    token = session["session_token"]
    links = [
        "https://detail.1688.com/offer/12345678.html?spm=ignored",
        "https://detail.1688.com/offer/12345678.html",
        "https://detail.1688.com/offer/87654321.html",
    ]

    prepared = client.post("/plugin/product-capture/onebound-batches/prepare", json={
        "session_token": token,
        "page_url": "https://detail.1688.com/offer/12345678.html",
        "source_urls": links,
    })
    assert prepared.status_code == 200
    batch_token = prepared.json()["batch_token"]
    assert prepared.json()["total_count"] == 2
    assert prepared.json()["pending_count"] == 2

    assert client.post("/plugin/product-capture/onebound-batches/start", json={"session_token": token, "batch_token": batch_token}).status_code == 200
    first = client.post("/plugin/product-capture/onebound-batches/item", json={"session_token": token, "batch_token": batch_token, "source_url": links[0]})
    replay = client.post("/plugin/product-capture/onebound-batches/item", json={"session_token": token, "batch_token": batch_token, "source_url": links[0]})
    assert first.status_code == replay.status_code == 200
    assert first.json()["outcome"] == "created"
    assert replay.json()["outcome"] == "created"
    assert provider.calls == 1
    assert drafts.intakes[0]["candidate"]["candidate_id"] == "1688:12345678"

    finished = client.post("/plugin/product-capture/onebound-batches/finish", json={"session_token": token, "batch_token": batch_token, "cancelled": False})
    assert finished.status_code == 200
    assert finished.json()["created_count"] == 1
    assert finished.json()["unprocessed_count"] == 1
    assert drafts.materialized == ["workspace-1"]


def test_prepare_skips_active_onebound_drafts_allows_deleted_and_limits_identity_batches(tmp_path: Path) -> None:
    client, session, _provider, drafts = _client(tmp_path)
    token = session["session_token"]
    drafts.by_candidate["1688:12345678"] = {
        "id": 9,
        "status": "draft",
        "source_type": "onebound_api",
    }
    drafts.by_candidate["1688:87654321"] = {
        "id": 10,
        "status": "deleted",
        "source_type": "onebound_api",
    }
    payload = {
        "session_token": token,
        "page_url": "https://detail.1688.com/offer/12345678.html",
        "source_urls": [
            "https://detail.1688.com/offer/12345678.html",
            "https://detail.1688.com/offer/87654321.html",
        ],
    }

    first = client.post("/plugin/product-capture/onebound-batches/prepare", json=payload)
    assert first.status_code == 200
    assert first.json()["existing_offer_ids"] == ["12345678"]
    assert first.json()["pending_urls"] == ["https://detail.1688.com/offer/87654321.html"]
    assert client.post("/plugin/product-capture/onebound-batches/prepare", json=payload).status_code == 200
    assert client.post("/plugin/product-capture/onebound-batches/prepare", json=payload).status_code == 409


def test_start_resolves_credentials_once_and_item_failures_do_not_block_other_items(tmp_path: Path) -> None:
    from wh_local.data_collection.plugin_onebound_capture import (
        PluginOneBoundCaptureDependencies,
        register_plugin_onebound_capture_routes,
    )

    class Provider(_Provider):
        def get_item_detail(self, offer_id: str):
            self.calls += 1
            if offer_id == "12345678":
                return type("Failed", (), {"ok": False})()
            return super().get_item_detail(offer_id)

    queue = DataCollectionPluginQueue(tmp_path / "runtime.sqlite3")
    session = queue.create_session(actor_id="actor-1", workspace_id="workspace-1")
    provider = Provider()
    drafts = _Drafts()
    config_calls = 0

    def resolve(_actor):
        nonlocal config_calls
        config_calls += 1
        return {"api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com"}

    router = APIRouter()
    register_plugin_onebound_capture_routes(router, PluginOneBoundCaptureDependencies(
        plugin_queue=queue, provider_config_resolver=resolve, provider_factory=lambda _config: provider,
        budget=_Budget(), draft_writer=drafts,
    ))
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    token = session["session_token"]
    prepared = client.post("/plugin/product-capture/onebound-batches/prepare", json={
        "session_token": token, "page_url": "https://detail.1688.com/offer/12345678.html",
        "source_urls": ["https://detail.1688.com/offer/12345678.html", "https://detail.1688.com/offer/87654321.html"],
    }).json()
    start_payload = {"session_token": token, "batch_token": prepared["batch_token"]}
    assert client.post("/plugin/product-capture/onebound-batches/start", json=start_payload).status_code == 200
    assert client.post("/plugin/product-capture/onebound-batches/start", json=start_payload).status_code == 200
    failed = client.post("/plugin/product-capture/onebound-batches/item", json={**start_payload, "source_url": "https://detail.1688.com/offer/12345678.html"})
    succeeded = client.post("/plugin/product-capture/onebound-batches/item", json={**start_payload, "source_url": "https://detail.1688.com/offer/87654321.html"})

    assert config_calls == 1
    assert failed.json()["error_code"] == "capture_failed"
    assert succeeded.json()["outcome"] == "created"


def test_concurrent_same_offer_waits_for_one_provider_call_and_one_intake(tmp_path: Path) -> None:
    from wh_local.data_collection.plugin_onebound_capture import (
        PluginOneBoundCaptureDependencies,
        register_plugin_onebound_capture_routes,
    )

    class BlockingProvider(_Provider):
        def __init__(self) -> None:
            super().__init__()
            self.entered = Event()
            self.release = Event()

        def get_item_detail(self, offer_id: str):
            self.calls += 1
            self.entered.set()
            assert self.release.wait(timeout=3)
            return type("Result", (), {
                "ok": True,
                "response": {"item": {
                    "offer_id": offer_id,
                    "detail_url": f"https://detail.1688.com/offer/{offer_id}.html",
                    "title": "Concurrent capture",
                }},
                "audit": None,
            })()

    queue = DataCollectionPluginQueue(tmp_path / "runtime.sqlite3")
    session = queue.create_session(actor_id="actor-1", workspace_id="workspace-1")
    provider = BlockingProvider()
    drafts = _Drafts()
    router = APIRouter()
    service = register_plugin_onebound_capture_routes(router, PluginOneBoundCaptureDependencies(
        plugin_queue=queue,
        provider_config_resolver=lambda _actor: {
            "api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com",
        },
        provider_factory=lambda _config: provider,
        budget=_Budget(),
        draft_writer=drafts,
    ))
    token = session["session_token"]
    prepared = service.prepare(
        session_token=token,
        page_url="https://detail.1688.com/offer/12345678.html",
        source_urls=["https://detail.1688.com/offer/12345678.html"],
    )
    service.start(session_token=token, batch_token=prepared["batch_token"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            service.item, session_token=token, batch_token=prepared["batch_token"],
            source_url="https://detail.1688.com/offer/12345678.html",
        )
        assert provider.entered.wait(timeout=1)
        second = pool.submit(
            service.item, session_token=token, batch_token=prepared["batch_token"],
            source_url="https://detail.1688.com/offer/12345678.html",
        )
        provider.release.set()
        assert first.result(timeout=3)["outcome"] == "created"
        assert second.result(timeout=3)["outcome"] == "created"

    assert provider.calls == 1
    assert len(drafts.intakes) == 1


def test_start_missing_batch_token_is_a_validation_error(tmp_path: Path) -> None:
    client, session, _provider, _drafts = _client(tmp_path)

    response = client.post(
        "/plugin/product-capture/onebound-batches/start",
        json={"session_token": session["session_token"]},
    )

    assert response.status_code == 422


def test_budget_exhaustion_marks_batch_fatal_and_stops_future_items(tmp_path: Path) -> None:
    from wh_local.data_collection.plugin_onebound_capture import (
        PluginOneBoundCaptureDependencies,
        register_plugin_onebound_capture_routes,
    )

    queue = DataCollectionPluginQueue(tmp_path / "runtime.sqlite3")
    session = queue.create_session(actor_id="actor-1", workspace_id="workspace-1")
    provider = _Provider()
    router = APIRouter()
    register_plugin_onebound_capture_routes(router, PluginOneBoundCaptureDependencies(
        plugin_queue=queue,
        provider_config_resolver=lambda _actor: {
            "api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com",
        },
        provider_factory=lambda _config: provider,
        budget=_ExhaustedBudget(),
        draft_writer=_Drafts(),
    ))
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    token = session["session_token"]
    prepared = client.post("/plugin/product-capture/onebound-batches/prepare", json={
        "session_token": token,
        "page_url": "https://detail.1688.com/offer/12345678.html",
        "source_urls": [
            "https://detail.1688.com/offer/12345678.html",
            "https://detail.1688.com/offer/87654321.html",
        ],
    }).json()
    batch = {"session_token": token, "batch_token": prepared["batch_token"]}
    assert client.post("/plugin/product-capture/onebound-batches/start", json=batch).status_code == 200

    first = client.post("/plugin/product-capture/onebound-batches/item", json={
        **batch, "source_url": "https://detail.1688.com/offer/12345678.html",
    })
    second = client.post("/plugin/product-capture/onebound-batches/item", json={
        **batch, "source_url": "https://detail.1688.com/offer/87654321.html",
    })

    assert first.status_code == 429
    assert second.status_code == 429
    assert provider.calls == 0


def test_finish_closes_the_batch_waits_for_inflight_item_and_materializes_once(tmp_path: Path) -> None:
    from wh_local.data_collection.plugin_onebound_capture import (
        PluginOneBoundCaptureDependencies,
        register_plugin_onebound_capture_routes,
    )

    class BlockingProvider(_Provider):
        def __init__(self) -> None:
            super().__init__()
            self.entered = Event()
            self.release = Event()

        def get_item_detail(self, offer_id: str):
            self.calls += 1
            self.entered.set()
            assert self.release.wait(timeout=3)
            return super().get_item_detail(offer_id)

    queue = DataCollectionPluginQueue(tmp_path / "runtime.sqlite3")
    session = queue.create_session(actor_id="actor-1", workspace_id="workspace-1")
    provider = BlockingProvider()
    drafts = _Drafts()
    service = register_plugin_onebound_capture_routes(APIRouter(), PluginOneBoundCaptureDependencies(
        plugin_queue=queue,
        provider_config_resolver=lambda _actor: {
            "api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com",
        },
        provider_factory=lambda _config: provider,
        budget=_Budget(),
        draft_writer=drafts,
    ))
    token = session["session_token"]
    prepared = service.prepare(
        session_token=token,
        page_url="https://detail.1688.com/offer/12345678.html",
        source_urls=["https://detail.1688.com/offer/12345678.html"],
    )
    service.start(session_token=token, batch_token=prepared["batch_token"])

    with ThreadPoolExecutor(max_workers=3) as pool:
        item = pool.submit(
            service.item, session_token=token, batch_token=prepared["batch_token"],
            source_url="https://detail.1688.com/offer/12345678.html",
        )
        assert provider.entered.wait(timeout=1)
        finish_one = pool.submit(
            service.finish_deferred, session_token=token, batch_token=prepared["batch_token"], cancelled=False,
        )
        finish_two = pool.submit(
            service.finish_deferred, session_token=token, batch_token=prepared["batch_token"], cancelled=False,
        )
        assert not finish_one.done()
        assert not finish_two.done()
        # One other active batch plus this closing/in-flight batch reaches the
        # per-identity limit; closing work must still count until finalization.
        service.prepare(
            session_token=token,
            page_url="https://detail.1688.com/offer/87654321.html",
            source_urls=["https://detail.1688.com/offer/87654321.html"],
        )
        with pytest.raises(RuntimeError):
            service.prepare(
                session_token=token,
                page_url="https://detail.1688.com/offer/99999999.html",
                source_urls=["https://detail.1688.com/offer/99999999.html"],
            )
        provider.release.set()
        assert item.result(timeout=3)["outcome"] == "created"
        first_summary, first_claim = finish_one.result(timeout=3)
        second_summary, second_claim = finish_two.result(timeout=3)
        assert first_summary["created_count"] == 1
        assert second_summary["created_count"] == 1

    assert sum((first_claim, second_claim)) == 1
    service.materialize_best_effort("workspace-1")
    assert drafts.materialized == ["workspace-1"]


def test_ttl_reaper_proactively_closes_and_removes_a_batch(tmp_path: Path) -> None:
    from wh_local.data_collection.plugin_onebound_capture import (
        PluginOneBoundCaptureDependencies,
        PluginOneBoundCaptureService,
    )

    queue = DataCollectionPluginQueue(tmp_path / "runtime.sqlite3")
    session = queue.create_session(actor_id="actor-1", workspace_id="workspace-1")
    timers = []

    class Timer:
        def __init__(self, _delay, callback) -> None:
            self.callback = callback
            self.cancelled = False

        def start(self) -> None:
            return None

        def cancel(self) -> None:
            self.cancelled = True

        def fire(self) -> None:
            if not self.cancelled:
                self.callback()

    def schedule(delay, callback):
        timer = Timer(delay, callback)
        timers.append(timer)
        return timer

    service = PluginOneBoundCaptureService(
        PluginOneBoundCaptureDependencies(
            plugin_queue=queue,
            provider_config_resolver=lambda _actor: {},
            provider_factory=lambda _config: _Provider(),
            budget=_Budget(),
            draft_writer=_Drafts(),
        ),
        ttl_seconds=30 * 60,
        timer_factory=schedule,
    )
    prepared = service.prepare(
        session_token=session["session_token"],
        page_url="https://detail.1688.com/offer/12345678.html",
        source_urls=["https://detail.1688.com/offer/12345678.html"],
    )

    assert len(timers) == 1
    timers[0].fire()
    with pytest.raises(LookupError):
        service.start(session_token=session["session_token"], batch_token=prepared["batch_token"])


def test_other_plugin_identity_cannot_use_a_batch_and_prepare_enforces_eighty_urls(tmp_path: Path) -> None:
    client, session, _provider, _drafts = _client(tmp_path)
    queue = DataCollectionPluginQueue(tmp_path / "runtime.sqlite3")
    other = queue.create_session(actor_id="actor-2", workspace_id="workspace-2")
    urls = [f"https://detail.1688.com/offer/{10000000 + index}.html" for index in range(80)]
    prepared = client.post("/plugin/product-capture/onebound-batches/prepare", json={
        "session_token": session["session_token"],
        "page_url": urls[0],
        "source_urls": urls,
    })
    assert prepared.status_code == 200
    assert prepared.json()["pending_count"] == 80
    assert client.post("/plugin/product-capture/onebound-batches/start", json={
        "session_token": other["session_token"], "batch_token": prepared.json()["batch_token"],
    }).status_code == 404
    truncated = client.post("/plugin/product-capture/onebound-batches/prepare", json={
        "session_token": session["session_token"],
        "page_url": urls[0],
        "source_urls": urls + ["https://detail.1688.com/offer/20000000.html"],
    })
    assert truncated.status_code == 200
    assert truncated.json()["pending_count"] == 80


def test_start_failure_is_batch_fatal_and_provider_guard_is_installed_once(tmp_path: Path) -> None:
    from wh_local.data_collection.plugin_onebound_capture import (
        PluginOneBoundCaptureDependencies,
        PluginOneBoundCaptureService,
    )

    queue = DataCollectionPluginQueue(tmp_path / "runtime.sqlite3")
    session = queue.create_session(actor_id="actor-1", workspace_id="workspace-1")
    factory_calls = 0

    def failing_factory(_config):
        nonlocal factory_calls
        factory_calls += 1
        raise RuntimeError("factory failed")

    service = PluginOneBoundCaptureService(PluginOneBoundCaptureDependencies(
        plugin_queue=queue,
        provider_config_resolver=lambda _actor: {
            "api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com",
        },
        provider_factory=failing_factory,
        budget=_Budget(),
        draft_writer=_Drafts(),
    ))
    prepared = service.prepare(
        session_token=session["session_token"],
        page_url="https://detail.1688.com/offer/12345678.html",
        source_urls=["https://detail.1688.com/offer/12345678.html"],
    )

    with pytest.raises(RuntimeError):
        service.start(session_token=session["session_token"], batch_token=prepared["batch_token"])
    with pytest.raises(RuntimeError):
        service.start(session_token=session["session_token"], batch_token=prepared["batch_token"])

    assert factory_calls == 1


def test_skipped_intake_is_counted_authoritatively_and_finish_materializes_once(tmp_path: Path) -> None:
    from wh_local.data_collection.plugin_onebound_capture import (
        PluginOneBoundCaptureDependencies,
        PluginOneBoundCaptureService,
    )

    class SkippingDrafts(_Drafts):
        def intake_shop_candidate(self, **kwargs):
            self.intakes.append(kwargs)
            return {"action": "skipped", "draft": {"id": 7, "status": "processing"}}

    queue = DataCollectionPluginQueue(tmp_path / "runtime.sqlite3")
    session = queue.create_session(actor_id="actor-1", workspace_id="workspace-1")
    drafts = SkippingDrafts()
    service = PluginOneBoundCaptureService(PluginOneBoundCaptureDependencies(
        plugin_queue=queue,
        provider_config_resolver=lambda _actor: {
            "api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com",
        },
        provider_factory=lambda _config: _Provider(),
        budget=_Budget(),
        draft_writer=drafts,
    ))
    prepared = service.prepare(
        session_token=session["session_token"],
        page_url="https://detail.1688.com/offer/12345678.html",
        source_urls=["https://detail.1688.com/offer/12345678.html"],
    )
    service.start(session_token=session["session_token"], batch_token=prepared["batch_token"])
    item = service.item(
        session_token=session["session_token"],
        batch_token=prepared["batch_token"],
        source_url="https://detail.1688.com/offer/12345678.html",
    )
    first = service.finish(
        session_token=session["session_token"], batch_token=prepared["batch_token"], cancelled=False,
    )
    replay = service.finish(
        session_token=session["session_token"], batch_token=prepared["batch_token"], cancelled=False,
    )

    assert item["outcome"] == "skipped"
    assert first["skipped_count"] == 1
    assert replay == first
    assert drafts.materialized == []


def test_start_installs_one_provider_guard_for_the_entire_batch(tmp_path: Path) -> None:
    from wh_local.data_collection.plugin_onebound_capture import (
        PluginOneBoundCaptureDependencies,
        PluginOneBoundCaptureService,
    )

    class GuardedProvider(_Provider):
        def __init__(self) -> None:
            super().__init__()
            self.guards = 0

        def install_api_call_guard(self, _guard):
            self.guards += 1

    queue = DataCollectionPluginQueue(tmp_path / "runtime.sqlite3")
    session = queue.create_session(actor_id="actor-1", workspace_id="workspace-1")
    provider = GuardedProvider()
    service = PluginOneBoundCaptureService(PluginOneBoundCaptureDependencies(
        plugin_queue=queue,
        provider_config_resolver=lambda _actor: {
            "api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com",
        },
        provider_factory=lambda _config: provider,
        budget=_Budget(),
        draft_writer=_Drafts(),
    ))
    prepared = service.prepare(
        session_token=session["session_token"],
        page_url="https://detail.1688.com/offer/12345678.html",
        source_urls=["https://detail.1688.com/offer/12345678.html"],
    )

    service.start(session_token=session["session_token"], batch_token=prepared["batch_token"])
    service.start(session_token=session["session_token"], batch_token=prepared["batch_token"])

    assert provider.guards == 1


def test_ttl_finalizer_closes_before_waiting_for_an_inflight_item(tmp_path: Path) -> None:
    from wh_local.data_collection.plugin_onebound_capture import (
        PluginOneBoundCaptureDependencies,
        PluginOneBoundCaptureService,
    )

    class BlockingProvider(_Provider):
        def __init__(self) -> None:
            super().__init__()
            self.entered = Event()
            self.release = Event()

        def get_item_detail(self, offer_id: str):
            self.entered.set()
            assert self.release.wait(timeout=3)
            return super().get_item_detail(offer_id)

    class SignallingDrafts(_Drafts):
        def __init__(self) -> None:
            super().__init__()
            self.done = Event()

        def materialize_until_idle(self, *, workspace_id: str):
            result = super().materialize_until_idle(workspace_id=workspace_id)
            self.done.set()
            return result

    queue = DataCollectionPluginQueue(tmp_path / "runtime.sqlite3")
    session = queue.create_session(actor_id="actor-1", workspace_id="workspace-1")
    provider = BlockingProvider()
    drafts = SignallingDrafts()
    service = PluginOneBoundCaptureService(
        PluginOneBoundCaptureDependencies(
            plugin_queue=queue,
            provider_config_resolver=lambda _actor: {
                "api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com",
            },
            provider_factory=lambda _config: provider,
            budget=_Budget(),
            draft_writer=drafts,
        ),
        ttl_seconds=0.01,
    )
    prepared = service.prepare(
        session_token=session["session_token"],
        page_url="https://detail.1688.com/offer/12345678.html",
        source_urls=["https://detail.1688.com/offer/12345678.html"],
    )
    service.start(session_token=session["session_token"], batch_token=prepared["batch_token"])

    with ThreadPoolExecutor(max_workers=1) as pool:
        item = pool.submit(
            service.item, session_token=session["session_token"], batch_token=prepared["batch_token"],
            source_url="https://detail.1688.com/offer/12345678.html",
        )
        assert provider.entered.wait(timeout=1)
        import time
        time.sleep(0.03)
        with pytest.raises(RuntimeError):
            service.item(
                session_token=session["session_token"], batch_token=prepared["batch_token"],
                source_url="https://detail.1688.com/offer/12345678.html",
            )
        provider.release.set()
        assert item.result(timeout=3)["outcome"] == "created"

    assert drafts.done.wait(timeout=2)
    assert drafts.materialized == ["workspace-1"]


def test_cancel_and_ttl_leave_unstarted_urls_as_unprocessed_not_skipped(tmp_path: Path) -> None:
    from wh_local.data_collection.plugin_onebound_capture import (
        PluginOneBoundCaptureDependencies,
        PluginOneBoundCaptureService,
    )

    queue = DataCollectionPluginQueue(tmp_path / "runtime.sqlite3")
    session = queue.create_session(actor_id="actor-1", workspace_id="workspace-1")
    service = PluginOneBoundCaptureService(PluginOneBoundCaptureDependencies(
        plugin_queue=queue,
        provider_config_resolver=lambda _actor: {
            "api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com",
        },
        provider_factory=lambda _config: _Provider(),
        budget=_Budget(),
        draft_writer=_Drafts(),
    ))
    prepared = service.prepare(
        session_token=session["session_token"],
        page_url="https://detail.1688.com/offer/12345678.html",
        source_urls=[
            "https://detail.1688.com/offer/12345678.html",
            "https://detail.1688.com/offer/87654321.html",
        ],
    )

    summary = service.finish(
        session_token=session["session_token"], batch_token=prepared["batch_token"], cancelled=True,
    )

    assert summary["cancelled"] is True
    assert summary["skipped_count"] == 0
    assert summary["unprocessed_count"] == 2


def test_prepare_normalizes_then_limits_to_eighty_unique_offers(tmp_path: Path) -> None:
    client, session, _provider, _drafts = _client(tmp_path)
    urls = ["https://detail.1688.com/offer/10000000.html"] * 81
    urls.extend(f"https://detail.1688.com/offer/{10000001 + index}.html" for index in range(80))

    response = client.post("/plugin/product-capture/onebound-batches/prepare", json={
        "session_token": session["session_token"],
        "page_url": urls[0],
        "source_urls": urls,
    })

    assert response.status_code == 200
    assert response.json()["total_count"] == 80
    assert response.json()["pending_count"] == 80


def test_repository_source_type_lookup_ignores_newer_other_source_draft(tmp_path: Path) -> None:
    from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
    from wh_local.modules.product_processing.infrastructure.database import create_database
    from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository
    from wh_local.modules.product_processing.service import ProductProcessingService

    service = ProductProcessingService(
        ProductProcessingRepository(create_database("sqlite:///:memory:")),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    onebound, _ = service.create_draft(
        {
            "candidate_id": "1688:12345678",
            "source_type": "onebound_api",
            "source_url": "https://detail.1688.com/offer/12345678.html",
            "title": "OneBound draft",
        },
        workspace_id="workspace-1",
        allow_duplicate_candidate=True,
    )
    service.create_draft(
        {
            "candidate_id": "1688:12345678",
            "source_type": "plugin_capture",
            "source_url": "https://detail.1688.com/offer/12345678.html",
            "title": "Newer non-OneBound draft",
        },
        workspace_id="workspace-1",
        allow_duplicate_candidate=True,
    )

    exact = service.repository.draft_by_candidate(
        "1688:12345678", "workspace-1", source_type="onebound_api"
    )

    assert exact is not None
    assert exact["id"] == onebound["id"]


def test_detail_calls_across_two_batches_never_exceed_three_inflight(tmp_path: Path) -> None:
    from wh_local.data_collection.plugin_onebound_capture import (
        PluginOneBoundCaptureDependencies,
        PluginOneBoundCaptureService,
    )

    class PeakProvider(_Provider):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.peak = 0
            self.lock = Lock()
            self.first_three = Event()
            self.release = Event()

        def get_item_detail(self, offer_id: str):
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
                if self.active >= 3:
                    self.first_three.set()
            assert self.release.wait(timeout=3)
            with self.lock:
                self.active -= 1
            return super().get_item_detail(offer_id)

    queue = DataCollectionPluginQueue(tmp_path / "runtime.sqlite3")
    session = queue.create_session(actor_id="actor-1", workspace_id="workspace-1")
    provider = PeakProvider()
    service = PluginOneBoundCaptureService(PluginOneBoundCaptureDependencies(
        plugin_queue=queue,
        provider_config_resolver=lambda _actor: {
            "api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com",
        },
        provider_factory=lambda _config: provider,
        budget=_Budget(),
        draft_writer=_Drafts(),
    ))
    urls_one = [f"https://detail.1688.com/offer/{10000000 + index}.html" for index in range(3)]
    urls_two = [f"https://detail.1688.com/offer/{20000000 + index}.html" for index in range(3)]
    batches = [
        service.prepare(session_token=session["session_token"], page_url=urls[0], source_urls=urls)
        for urls in (urls_one, urls_two)
    ]
    for batch in batches:
        service.start(session_token=session["session_token"], batch_token=batch["batch_token"])

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [
            pool.submit(
                service.item,
                session_token=session["session_token"],
                batch_token=batch["batch_token"],
                source_url=url,
            )
            for batch, urls in zip(batches, (urls_one, urls_two))
            for url in urls
        ]
        assert provider.first_three.wait(timeout=1)
        provider.release.set()
        for future in futures:
            assert future.result(timeout=3)["outcome"] == "created"

    assert provider.peak <= 3


def test_finish_route_returns_before_slow_or_failed_materialization_and_schedules_once(tmp_path: Path) -> None:
    from wh_local.data_collection.plugin_onebound_capture import (
        PluginOneBoundCaptureDependencies,
        register_plugin_onebound_capture_routes,
    )

    class FailingMaterializationDrafts(_Drafts):
        def materialize_until_idle(self, *, workspace_id: str):
            raise RuntimeError("slow media failure")

    queue = DataCollectionPluginQueue(tmp_path / "runtime.sqlite3")
    session = queue.create_session(actor_id="actor-1", workspace_id="workspace-1")
    drafts = FailingMaterializationDrafts()
    router = APIRouter()
    service = register_plugin_onebound_capture_routes(router, PluginOneBoundCaptureDependencies(
        plugin_queue=queue,
        provider_config_resolver=lambda _actor: {
            "api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com",
        },
        provider_factory=lambda _config: _Provider(),
        budget=_Budget(),
        draft_writer=drafts,
    ))
    prepared = service.prepare(
        session_token=session["session_token"],
        page_url="https://detail.1688.com/offer/12345678.html",
        source_urls=["https://detail.1688.com/offer/12345678.html"],
    )
    service.start(session_token=session["session_token"], batch_token=prepared["batch_token"])
    service.item(
        session_token=session["session_token"], batch_token=prepared["batch_token"],
        source_url="https://detail.1688.com/offer/12345678.html",
    )
    endpoint = next(route.endpoint for route in router.routes if route.path.endswith("/finish"))
    tasks = BackgroundTasks()
    payload = {
        "session_token": session["session_token"],
        "batch_token": prepared["batch_token"],
        "cancelled": False,
    }

    summary = endpoint(tasks, payload)
    replay = endpoint(BackgroundTasks(), payload)

    assert summary == replay
    assert summary["created_count"] == 1
    assert len(tasks.tasks) == 1
    tasks.tasks[0].func(*tasks.tasks[0].args, **tasks.tasks[0].kwargs)


def test_persistent_batch_uses_public_batch_id_and_desktop_reads_it(tmp_path: Path) -> None:
    from wh_local.data_collection.plugin_onebound_capture import (
        PluginOneBoundCaptureDependencies,
        register_plugin_onebound_capture_routes,
    )
    from wh_local.data_collection.service import DailySelectionActor

    database = tmp_path / "runtime.sqlite3"
    queue = DataCollectionPluginQueue(database)
    session = queue.create_session(actor_id="actor-1", workspace_id="workspace-1")
    drafts = _Drafts()
    router = APIRouter()
    register_plugin_onebound_capture_routes(router, PluginOneBoundCaptureDependencies(
        plugin_queue=queue,
        provider_config_resolver=lambda _actor: {
            "api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com",
        },
        provider_factory=lambda _config: _Provider(), budget=_Budget(), draft_writer=drafts,
        database_path=str(database),
        resolve_actor=lambda: {"actor_id": "actor-1", "workspace_id": "workspace-1"},
    ))
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    prepared = client.post("/plugin/product-capture/onebound-batches/prepare", json={
        "session_token": session["session_token"],
        "page_url": "https://detail.1688.com/offer/12345678.html",
        "source_urls": ["https://detail.1688.com/offer/12345678.html"],
    }).json()
    token = {"session_token": session["session_token"], "batch_token": prepared["batch_token"]}
    client.post("/plugin/product-capture/onebound-batches/start", json=token)
    client.post("/plugin/product-capture/onebound-batches/item", json={
        **token, "source_url": "https://detail.1688.com/offer/12345678.html",
    })
    client.post("/plugin/product-capture/onebound-batches/finish", json={**token, "cancelled": False})

    listed = client.get("/desktop/data-collection/plugin-onebound-batches")
    detail = client.get(f"/desktop/data-collection/plugin-onebound-batches/{prepared['batch_id']}")
    items = client.get(f"/desktop/data-collection/plugin-onebound-batches/{prepared['batch_id']}/items")

    assert listed.status_code == detail.status_code == items.status_code == 200
    assert detail.json()["batch"]["batch_id"] == prepared["batch_id"]
    assert items.json()["items"][0]["status"] == "succeeded"
    assert drafts.intakes[0]["batch_id"] == prepared["batch_id"]


def test_desktop_start_button_executes_a_prepared_batch_through_onebound(tmp_path: Path) -> None:
    provider = _Provider()
    budget = _Budget()
    client, service, session, drafts = _persistent_retry_client(
        tmp_path, provider=provider, budget=budget,
        resolve_config=lambda _actor: {"api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com"},
    )
    prepared = service.prepare(
        session_token=session["session_token"], page_url="https://s.1688.com/selloffer/offer_search.htm",
        source_urls=[
            "https://detail.1688.com/offer/12345678.html",
            "https://detail.1688.com/offer/87654321.html",
        ],
    )

    response = client.post(f"/desktop/data-collection/plugin-onebound-batches/{prepared['batch_id']}/start")

    assert response.status_code == 202
    batch = client.get(f"/desktop/data-collection/plugin-onebound-batches/{prepared['batch_id']}").json()["batch"]
    assert batch["status"] == "completed"
    assert batch["created_count"] == 2
    assert provider.calls == 2
    assert len(budget.calls) == 2
    assert {entry["batch_id"] for entry in drafts.intakes} == {prepared["batch_id"]}


def test_desktop_start_dispatches_three_onebound_items_concurrently(tmp_path: Path) -> None:
    class ConcurrentProvider(_Provider):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.max_active = 0
            self.lock = Lock()
            self.release = Event()

        def get_item_detail(self, offer_id: str):
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                if self.active >= 3:
                    self.release.set()
            self.release.wait(timeout=1)
            try:
                return super().get_item_detail(offer_id)
            finally:
                with self.lock:
                    self.active -= 1

    provider = ConcurrentProvider()
    client, service, session, _drafts = _persistent_retry_client(
        tmp_path, provider=provider, budget=_Budget(),
        resolve_config=lambda _actor: {"api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com"},
    )
    prepared = service.prepare(
        session_token=session["session_token"], page_url="https://s.1688.com/selloffer/offer_search.htm",
        source_urls=[
            "https://detail.1688.com/offer/12345678.html",
            "https://detail.1688.com/offer/22345678.html",
            "https://detail.1688.com/offer/32345678.html",
        ],
    )

    response = client.post(f"/desktop/data-collection/plugin-onebound-batches/{prepared['batch_id']}/start")

    assert response.status_code == 202
    assert provider.calls == 3
    assert provider.max_active == 3


def test_desktop_start_failure_keeps_batch_prepared_and_allows_retry(tmp_path: Path) -> None:
    provider = _Provider()
    budget = _Budget()
    available = {"value": False}

    def resolve_config(_actor):
        if not available["value"]:
            raise RuntimeError("temporary credential grant unavailable")
        return {"api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com"}

    client, service, session, _drafts = _persistent_retry_client(
        tmp_path, provider=provider, budget=budget, resolve_config=resolve_config,
    )
    prepared = service.prepare(
        session_token=session["session_token"], page_url="https://s.1688.com/selloffer/offer_search.htm",
        source_urls=["https://detail.1688.com/offer/12345678.html"],
    )

    first = client.post(f"/desktop/data-collection/plugin-onebound-batches/{prepared['batch_id']}/start")
    failed = client.get(f"/desktop/data-collection/plugin-onebound-batches/{prepared['batch_id']}").json()["batch"]
    assert first.status_code == 202
    assert failed["status"] == "prepared"
    assert failed["error_code"] == "start_failed"
    assert failed["unprocessed_count"] == 1

    available["value"] = True
    second = client.post(f"/desktop/data-collection/plugin-onebound-batches/{prepared['batch_id']}/start")
    recovered = client.get(f"/desktop/data-collection/plugin-onebound-batches/{prepared['batch_id']}").json()["batch"]
    assert second.status_code == 202
    assert recovered["status"] == "completed"
    assert recovered["created_count"] == 1
    assert provider.calls == 1


def test_desktop_start_restores_a_persisted_prepared_batch_after_service_restart(tmp_path: Path) -> None:
    from wh_local.data_collection.plugin_onebound_capture import (
        PluginOneBoundCaptureDependencies,
        PluginOneBoundCaptureService,
        register_plugin_onebound_capture_routes,
    )
    from wh_local.data_collection.service import DailySelectionActor

    database = tmp_path / "restart.sqlite3"
    queue = DataCollectionPluginQueue(database)
    session = queue.create_session(actor_id="actor-1", workspace_id="workspace-1")
    drafts = _Drafts()
    provider = _Provider()
    dependencies = PluginOneBoundCaptureDependencies(
        plugin_queue=queue,
        provider_config_resolver=lambda _actor: {"api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com"},
        provider_factory=lambda _config: provider,
        budget=_Budget(),
        draft_writer=drafts,
        database_path=str(database),
        resolve_actor=lambda: DailySelectionActor(actor_id="actor-1", workspace_id="workspace-1"),
    )
    first_service = PluginOneBoundCaptureService(dependencies)
    prepared = first_service.prepare(
        session_token=session["session_token"], page_url="https://s.1688.com/selloffer/offer_search.htm",
        source_urls=["https://detail.1688.com/offer/12345678.html"],
    )

    router = APIRouter()
    register_plugin_onebound_capture_routes(router, dependencies)
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).post(
        f"/desktop/data-collection/plugin-onebound-batches/{prepared['batch_id']}/start"
    )

    assert response.status_code == 202
    restored = PluginOneBoundCaptureService(dependencies)._repository.get(
        workspace_id="workspace-1", batch_id=prepared["batch_id"]
    )
    assert restored is not None
    assert restored["status"] == "completed"
    assert provider.calls == 1


def test_retry_failed_validates_parent_and_returns_child_before_background_execution(tmp_path: Path) -> None:
    # The HTTP contract is intentionally checked before provider work: a UI can
    # navigate to the child immediately while the server owns execution.
    client, session, _provider, _drafts = _client(tmp_path)
    missing = client.post("/desktop/data-collection/plugin-onebound-batches/missing/retry-failed")
    assert missing.status_code == 404


def test_persistent_prepare_start_and_item_keep_live_aggregate_counts(tmp_path: Path) -> None:
    from wh_local.data_collection.plugin_onebound_capture import (
        PluginOneBoundCaptureDependencies,
        PluginOneBoundCaptureService,
    )

    database = tmp_path / "runtime.sqlite3"
    queue = DataCollectionPluginQueue(database)
    session = queue.create_session(actor_id="actor-1", workspace_id="workspace-1")
    drafts = _Drafts()
    drafts.by_candidate["1688:12345678"] = {
        "id": 1, "status": "draft", "source_type": "onebound_api",
    }
    service = PluginOneBoundCaptureService(PluginOneBoundCaptureDependencies(
        plugin_queue=queue,
        provider_config_resolver=lambda _actor: {"api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com"},
        provider_factory=lambda _config: _Provider(), budget=_Budget(), draft_writer=drafts,
        database_path=str(database),
    ))
    prepared = service.prepare(
        session_token=session["session_token"],
        page_url="https://detail.1688.com/offer/12345678.html",
        source_urls=[
            "https://detail.1688.com/offer/12345678.html",
            "https://detail.1688.com/offer/87654321.html",
        ],
    )

    before_start = service._repository.get(workspace_id="workspace-1", batch_id=prepared["batch_id"])
    assert before_start is not None
    assert {key: before_start[key] for key in ("total_count", "skipped_count", "unprocessed_count")} == {
        "total_count": 2, "skipped_count": 1, "unprocessed_count": 1,
    }
    assert {(item["offer_id"], item["status"]) for item in service._repository.items(
        workspace_id="workspace-1", batch_id=prepared["batch_id"]
    )} == {("12345678", "skipped"), ("87654321", "pending")}

    service.start(session_token=session["session_token"], batch_token=prepared["batch_token"])
    after_start = service._repository.get(workspace_id="workspace-1", batch_id=prepared["batch_id"])
    assert after_start is not None
    assert {key: after_start[key] for key in ("total_count", "skipped_count", "unprocessed_count")} == {
        "total_count": 2, "skipped_count": 1, "unprocessed_count": 1,
    }

    service.item(
        session_token=session["session_token"], batch_token=prepared["batch_token"],
        source_url="https://detail.1688.com/offer/87654321.html",
    )
    live = service._repository.get(workspace_id="workspace-1", batch_id=prepared["batch_id"])
    item = service._repository.items(workspace_id="workspace-1", batch_id=prepared["batch_id"])[1]
    assert live is not None
    assert {key: live[key] for key in ("total_count", "created_count", "skipped_count", "unprocessed_count")} == {
        "total_count": 2, "created_count": 1, "skipped_count": 1, "unprocessed_count": 0,
    }
    assert item["attempts"] == 1
    assert item["error_message"] == "商品已写入草稿"
    assert item["source_title"] == "Capture item"


def test_legacy_backfill_aggregates_one_plugin_batch_and_excludes_daily_and_shop(tmp_path: Path) -> None:
    from wh_local.data_collection.plugin_onebound_capture_repository import PluginOneBoundCaptureRepository
    from wh_local.db import connect, init_db

    database = tmp_path / "legacy.sqlite3"
    init_db(database)
    with connect(database) as conn:
        rows = [
            ("1688:11111111", "https://detail.1688.com/offer/11111111.html", "legacy-run", "Legacy One"),
            ("1688:22222222", "https://detail.1688.com/offer/22222222.html", "legacy-run", "Legacy Two"),
            ("1688:33333333", "https://detail.1688.com/offer/33333333.html", "daily-run", "Daily"),
            ("1688:44444444", "https://detail.1688.com/offer/44444444.html", "shop-run", "Shop"),
        ]
        conn.executemany(
            """INSERT INTO product_processing_drafts
                (workspace_id, source_type, candidate_id, source_ref, selection_run_id, title, created_at, updated_at)
                VALUES ('default', 'onebound_api', ?, ?, ?, ?, datetime('now'), datetime('now'))""",
            rows,
        )
        conn.execute(
            """INSERT INTO daily_selection_runs
                (workspace_id, run_id, status, created_at, updated_at)
                VALUES ('default', 'daily-run', 'completed', datetime('now'), datetime('now'))"""
        )
        conn.execute(
            """INSERT INTO shop_collection_batches (batch_id, workspace_id, actor_id, shop_sid)
                VALUES ('shop-run', 'default', 'actor-1', 'shop-1')"""
        )

    repository = PluginOneBoundCaptureRepository(database)
    batch = repository.get(workspace_id="default", batch_id="legacy-run")
    assert batch is not None
    assert batch["status"] == "completed"
    assert batch["total_count"] == batch["created_count"] == 2
    legacy_items = repository.items(workspace_id="default", batch_id="legacy-run")
    assert {item["offer_id"] for item in legacy_items} == {
        "11111111", "22222222",
    }
    assert {item["source_title"] for item in legacy_items} == {"Legacy One", "Legacy Two"}
    assert all(item["draft_id"] is not None for item in legacy_items)
    assert repository.get(workspace_id="default", batch_id="daily-run") is None
    assert repository.get(workspace_id="default", batch_id="shop-run") is None

    again = PluginOneBoundCaptureRepository(database).get(workspace_id="default", batch_id="legacy-run")
    assert again is not None
    assert again["total_count"] == again["created_count"] == 2


def test_legacy_backfill_keeps_identical_selection_ids_in_separate_workspaces(tmp_path: Path) -> None:
    from wh_local.data_collection.plugin_onebound_capture_repository import PluginOneBoundCaptureRepository
    from wh_local.db import connect, init_db

    database = tmp_path / "legacy-workspaces.sqlite3"
    init_db(database)
    with connect(database) as conn:
        conn.executemany(
            """INSERT INTO product_processing_drafts
                (workspace_id, source_type, candidate_id, source_ref, selection_run_id, created_at, updated_at)
                VALUES (?, 'onebound_api', ?, ?, 'same-run', datetime('now'), datetime('now'))""",
            [
                ("workspace-a", "1688:11111111", "https://detail.1688.com/offer/11111111.html"),
                ("workspace-b", "1688:22222222", "https://detail.1688.com/offer/22222222.html"),
            ],
        )

    repository = PluginOneBoundCaptureRepository(database)
    first = repository.list(workspace_id="workspace-a", limit=10, offset=0)
    second = repository.list(workspace_id="workspace-b", limit=10, offset=0)

    assert len(first) == len(second) == 1
    assert first[0]["batch_id"] != second[0]["batch_id"]
    assert repository.items(workspace_id="workspace-a", batch_id=first[0]["batch_id"])[0]["offer_id"] == "11111111"
    assert repository.items(workspace_id="workspace-b", batch_id=second[0]["batch_id"])[0]["offer_id"] == "22222222"


def test_old_008_marker_receives_009_item_attempts_upgrade(tmp_path: Path) -> None:
    from wh_local.data_collection.plugin_onebound_capture_repository import PluginOneBoundCaptureRepository
    from wh_local.db import connect, init_db

    database = tmp_path / "old-008.sqlite3"
    init_db(database)
    with connect(database) as conn:
        conn.execute("DROP TABLE plugin_onebound_capture_items")
        conn.execute("""CREATE TABLE plugin_onebound_capture_items (
            batch_id TEXT NOT NULL, offer_id TEXT NOT NULL, source_url TEXT NOT NULL,
            source_title TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
            outcome TEXT NOT NULL DEFAULT '', draft_id INTEGER, error_code TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')), PRIMARY KEY (batch_id, offer_id))""")
        conn.execute("DELETE FROM schema_migrations WHERE migration_id='data_collection:009_plugin_onebound_capture_item_attempts'")

    repository = PluginOneBoundCaptureRepository(database)
    repository.create(
        batch_id="old-batch", actor_id="actor-1", workspace_id="default",
        items=({"offer_id": "12345678", "source_url": "https://detail.1688.com/offer/12345678.html"},),
    )
    repository.update_item("old-batch", "12345678", status="running", increment_attempt=True)

    assert repository.items(workspace_id="default", batch_id="old-batch")[0]["attempts"] == 1


def test_old_008_marker_receives_010_persistent_batch_columns_upgrade(tmp_path: Path) -> None:
    from wh_local.data_collection.plugin_onebound_capture_repository import PluginOneBoundCaptureRepository
    from wh_local.db import connect, init_db

    database = tmp_path / "old-008-runtime-columns.sqlite3"
    init_db(database)
    with connect(database) as conn:
        conn.execute("DROP TABLE plugin_onebound_capture_items")
        conn.execute("DROP TABLE plugin_onebound_capture_batches")
        conn.execute("""CREATE TABLE plugin_onebound_capture_batches (
            batch_id TEXT PRIMARY KEY, parent_batch_id TEXT NOT NULL DEFAULT '',
            workspace_id TEXT NOT NULL, actor_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'prepared', cancelled INTEGER NOT NULL DEFAULT 0,
            created_count INTEGER NOT NULL DEFAULT 0, refreshed_count INTEGER NOT NULL DEFAULT 0,
            skipped_count INTEGER NOT NULL DEFAULT 0, failed_count INTEGER NOT NULL DEFAULT 0,
            unprocessed_count INTEGER NOT NULL DEFAULT 0, error_code TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')), completed_at TEXT)""")
        conn.execute("""CREATE TABLE plugin_onebound_capture_items (
            batch_id TEXT NOT NULL, offer_id TEXT NOT NULL, source_url TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', outcome TEXT NOT NULL DEFAULT '',
            draft_id INTEGER, error_code TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            attempts INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (batch_id, offer_id))""")
        conn.execute(
            "DELETE FROM schema_migrations WHERE migration_id='data_collection:010_plugin_onebound_capture_persistent_columns'"
        )

    init_db(database)
    repository = PluginOneBoundCaptureRepository(database)
    repository.create(
        batch_id="upgraded-batch", actor_id="actor-1", workspace_id="default",
        page_url="https://s.1688.com/selloffer/offer_search.htm",
        items=({
            "offer_id": "1056665846829",
            "source_url": "https://detail.1688.com/offer/1056665846829.html",
            "source_title": "Upgraded offer",
        },),
    )

    batch = repository.get(workspace_id="default", batch_id="upgraded-batch")
    item = repository.items(workspace_id="default", batch_id="upgraded-batch")[0]
    assert batch is not None
    assert batch["page_url"] == "https://s.1688.com/selloffer/offer_search.htm"
    assert batch["total_count"] == 1
    assert batch["error_message"] == ""
    assert item["source_title"] == "Upgraded offer"
    assert item["error_message"] == ""


def _persistent_retry_client(tmp_path: Path, *, provider: _Provider, budget: _Budget, resolve_config):
    from wh_local.data_collection.plugin_onebound_capture import (
        PluginOneBoundCaptureDependencies,
        register_plugin_onebound_capture_routes,
    )
    from wh_local.data_collection.service import DailySelectionActor

    database = tmp_path / "retry.sqlite3"
    queue = DataCollectionPluginQueue(database)
    session = queue.create_session(actor_id="actor-1", workspace_id="workspace-1")
    drafts = _Drafts()
    router = APIRouter()
    service = register_plugin_onebound_capture_routes(router, PluginOneBoundCaptureDependencies(
        plugin_queue=queue, provider_config_resolver=resolve_config, provider_factory=lambda _config: provider,
        budget=budget, draft_writer=drafts, database_path=str(database),
        resolve_actor=lambda: DailySelectionActor(actor_id="actor-1", workspace_id="workspace-1"),
    ))
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), service, session, drafts


def test_retry_failed_rejects_a_parent_without_failed_items(tmp_path: Path) -> None:
    client, service, session, _drafts = _persistent_retry_client(
        tmp_path, provider=_Provider(), budget=_Budget(),
        resolve_config=lambda _actor: {"api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com"},
    )
    prepared = service.prepare(
        session_token=session["session_token"], page_url="https://detail.1688.com/offer/12345678.html",
        source_urls=["https://detail.1688.com/offer/12345678.html"],
    )
    service.finish(session_token=session["session_token"], batch_token=prepared["batch_token"], cancelled=True)

    response = client.post(f"/desktop/data-collection/plugin-onebound-batches/{prepared['batch_id']}/retry-failed")

    assert response.status_code == 409


def test_retry_failed_returns_child_then_runs_onebound_under_shared_budget(tmp_path: Path) -> None:
    class FailFirstProvider(_Provider):
        def get_item_detail(self, offer_id: str):
            self.calls += 1
            if self.calls == 1:
                return type("Failed", (), {"ok": False})()
            return _Provider.get_item_detail(self, offer_id)

    provider = FailFirstProvider()
    budget = _Budget()
    client, service, session, drafts = _persistent_retry_client(
        tmp_path, provider=provider, budget=budget,
        resolve_config=lambda _actor: {"api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com"},
    )
    prepared = service.prepare(
        session_token=session["session_token"], page_url="https://detail.1688.com/offer/12345678.html",
        source_urls=["https://detail.1688.com/offer/12345678.html"],
    )
    service.start(session_token=session["session_token"], batch_token=prepared["batch_token"])
    assert service.item(
        session_token=session["session_token"], batch_token=prepared["batch_token"],
        source_url="https://detail.1688.com/offer/12345678.html",
    )["outcome"] == "failed"
    service.finish(session_token=session["session_token"], batch_token=prepared["batch_token"], cancelled=False)

    response = client.post(f"/desktop/data-collection/plugin-onebound-batches/{prepared['batch_id']}/retry-failed")

    assert response.status_code == 202
    child_id = response.json()["batch"]["batch_id"]
    child = client.get(f"/desktop/data-collection/plugin-onebound-batches/{child_id}").json()["batch"]
    assert child["parent_batch_id"] == prepared["batch_id"]
    assert child["status"] == "completed"
    assert child["created_count"] == 1
    assert provider.calls >= 2
    assert len(budget.calls) >= 2
    assert drafts.intakes[-1]["batch_id"] == child_id


def test_retry_start_failure_finishes_child_with_diagnostic(tmp_path: Path) -> None:
    class FailingProvider(_Provider):
        def get_item_detail(self, _offer_id: str):
            self.calls += 1
            return type("Failed", (), {"ok": False})()

    failing = {"enabled": False}

    def config(_actor):
        if failing["enabled"]:
            raise RuntimeError("provider unavailable")
        return {"api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com"}

    client, service, session, _drafts = _persistent_retry_client(
        tmp_path, provider=FailingProvider(), budget=_Budget(), resolve_config=config,
    )
    prepared = service.prepare(
        session_token=session["session_token"], page_url="https://detail.1688.com/offer/12345678.html",
        source_urls=["https://detail.1688.com/offer/12345678.html"],
    )
    service.start(session_token=session["session_token"], batch_token=prepared["batch_token"])
    service.item(
        session_token=session["session_token"], batch_token=prepared["batch_token"],
        source_url="https://detail.1688.com/offer/12345678.html",
    )
    service.finish(session_token=session["session_token"], batch_token=prepared["batch_token"], cancelled=False)
    failing["enabled"] = True

    response = client.post(f"/desktop/data-collection/plugin-onebound-batches/{prepared['batch_id']}/retry-failed")

    assert response.status_code == 202
    child = client.get(
        f"/desktop/data-collection/plugin-onebound-batches/{response.json()['batch']['batch_id']}"
    ).json()["batch"]
    assert child["status"] == "failed"
    assert child["error_code"] == "retry_execution_failed"
    assert child["error_message"] == "服务器重试执行失败"
    assert child["unprocessed_count"] == 1
    assert child["completed_at"]


def test_persistent_workspace_isolation_and_schema_omit_plugin_secrets(tmp_path: Path) -> None:
    from wh_local.data_collection.plugin_onebound_capture import (
        PluginOneBoundCaptureDependencies,
        PluginOneBoundCaptureService,
    )
    from wh_local.db import connect

    database = tmp_path / "runtime.sqlite3"
    queue = DataCollectionPluginQueue(database)
    first = queue.create_session(actor_id="actor-1", workspace_id="workspace-1")
    second = queue.create_session(actor_id="actor-2", workspace_id="workspace-2")
    service = PluginOneBoundCaptureService(PluginOneBoundCaptureDependencies(
        plugin_queue=queue,
        provider_config_resolver=lambda _actor: {"api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com"},
        provider_factory=lambda _config: _Provider(), budget=_Budget(), draft_writer=_Drafts(),
        database_path=str(database),
    ))
    prepared = service.prepare(
        session_token=first["session_token"], page_url="https://detail.1688.com/offer/12345678.html",
        source_urls=["https://detail.1688.com/offer/12345678.html"],
    )

    assert service._repository.get(workspace_id="workspace-2", batch_id=prepared["batch_id"]) is None
    assert service._repository.items(workspace_id="workspace-2", batch_id=prepared["batch_id"]) == ()
    with pytest.raises(LookupError):
        service.start(session_token=second["session_token"], batch_token=prepared["batch_token"])
    with connect(database) as conn:
        columns = {
            row["name"]
            for table in ("plugin_onebound_capture_batches", "plugin_onebound_capture_items")
            for row in conn.execute(f"PRAGMA table_info({table})")
        }
    assert not columns & {"batch_token", "session_token", "api_key", "api_secret", "access_token", "credential"}


def test_persistent_cancel_and_ttl_mark_pending_items_unprocessed_with_completion_time(tmp_path: Path) -> None:
    from wh_local.data_collection.plugin_onebound_capture import (
        PluginOneBoundCaptureDependencies,
        PluginOneBoundCaptureService,
    )

    database = tmp_path / "runtime.sqlite3"
    queue = DataCollectionPluginQueue(database)
    session = queue.create_session(actor_id="actor-1", workspace_id="workspace-1")
    service = PluginOneBoundCaptureService(PluginOneBoundCaptureDependencies(
        plugin_queue=queue,
        provider_config_resolver=lambda _actor: {"api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com"},
        provider_factory=lambda _config: _Provider(), budget=_Budget(), draft_writer=_Drafts(),
        database_path=str(database),
    ))
    cancelled = service.prepare(
        session_token=session["session_token"], page_url="https://detail.1688.com/offer/12345678.html",
        source_urls=["https://detail.1688.com/offer/12345678.html"],
    )
    service.finish(session_token=session["session_token"], batch_token=cancelled["batch_token"], cancelled=True)
    expired = service.prepare(
        session_token=session["session_token"], page_url="https://detail.1688.com/offer/87654321.html",
        source_urls=["https://detail.1688.com/offer/87654321.html"],
    )
    service._expire_batch(expired["batch_token"])

    cancelled_row = service._repository.get(workspace_id="workspace-1", batch_id=cancelled["batch_id"])
    expired_row = service._repository.get(workspace_id="workspace-1", batch_id=expired["batch_id"])
    assert cancelled_row is not None and expired_row is not None
    assert (cancelled_row["status"], cancelled_row["unprocessed_count"], bool(cancelled_row["completed_at"])) == ("cancelled", 1, True)
    assert (expired_row["status"], expired_row["unprocessed_count"], bool(expired_row["completed_at"])) == ("expired", 1, True)
    assert service._repository.items(workspace_id="workspace-1", batch_id=cancelled["batch_id"])[0]["status"] == "unprocessed"


def test_capture_schema_rejects_invalid_status_and_negative_counts(tmp_path: Path) -> None:
    from wh_local.data_collection.plugin_onebound_capture_repository import PluginOneBoundCaptureRepository
    from wh_local.db import connect

    database = tmp_path / "schema.sqlite3"
    PluginOneBoundCaptureRepository(database)
    with connect(database) as conn:
        with pytest.raises(Exception, match="CHECK constraint failed"):
            conn.execute(
                """INSERT INTO plugin_onebound_capture_batches
                    (batch_id, workspace_id, actor_id, status, total_count)
                    VALUES ('invalid', 'default', 'actor-1', 'unknown', -1)"""
            )


def test_repository_reconciles_only_stale_active_batches_after_restart(tmp_path: Path) -> None:
    from wh_local.data_collection.plugin_onebound_capture_repository import PluginOneBoundCaptureRepository
    from wh_local.db import connect

    database = tmp_path / "restart.sqlite3"
    repository = PluginOneBoundCaptureRepository(database)
    for batch_id in ("stale", "recent"):
        repository.create(
            batch_id=batch_id, actor_id="actor-1", workspace_id="default",
            items=({"offer_id": f"{batch_id}12345678", "source_url": "https://detail.1688.com/offer/12345678.html"},),
        )
    with connect(database) as conn:
        conn.execute("UPDATE plugin_onebound_capture_batches SET status='running', updated_at='2000-01-01 00:00:00' WHERE batch_id='stale'")
        conn.execute("UPDATE plugin_onebound_capture_items SET status='running' WHERE batch_id='stale'")
        conn.execute("UPDATE plugin_onebound_capture_batches SET status='queued', updated_at=datetime('now') WHERE batch_id='recent'")

    recovered = PluginOneBoundCaptureRepository(database)
    stale = recovered.get(workspace_id="default", batch_id="stale")
    recent = recovered.get(workspace_id="default", batch_id="recent")

    assert stale is not None and recent is not None
    assert (stale["status"], stale["cancelled"], stale["unprocessed_count"], bool(stale["completed_at"])) == ("expired", 0, 1, True)
    assert recovered.items(workspace_id="default", batch_id="stale")[0]["status"] == "unprocessed"
    assert recent["status"] == "queued"

    with connect(database) as conn:
        conn.execute("UPDATE plugin_onebound_capture_batches SET updated_at='2000-01-01 00:00:00' WHERE batch_id='recent'")
    lazily_expired = recovered.get(workspace_id="default", batch_id="recent")
    assert lazily_expired is not None
    assert (lazily_expired["status"], lazily_expired["unprocessed_count"], bool(lazily_expired["completed_at"])) == (
        "expired", 1, True,
    )


def test_retry_capacity_limit_is_reported_as_conflict_not_server_error(tmp_path: Path) -> None:
    client, service, session, _drafts = _persistent_retry_client(
        tmp_path, provider=_Provider(), budget=_Budget(),
        resolve_config=lambda _actor: {"api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com"},
    )
    parent = service.prepare(
        session_token=session["session_token"], page_url="https://detail.1688.com/offer/12345678.html",
        source_urls=["https://detail.1688.com/offer/12345678.html"],
    )
    service.finish(session_token=session["session_token"], batch_token=parent["batch_token"], cancelled=True)
    service._repository.update_item(parent["batch_id"], "12345678", status="failed", outcome="failed")
    service._repository.set_status(parent["batch_id"], "partial", summary={"failed_count": 1})
    for offer_id in ("87654321", "76543210"):
        service.prepare(
            session_token=session["session_token"], page_url=f"https://detail.1688.com/offer/{offer_id}.html",
            source_urls=[f"https://detail.1688.com/offer/{offer_id}.html"],
        )

    response = TestClient(client.app, raise_server_exceptions=False).post(
        f"/desktop/data-collection/plugin-onebound-batches/{parent['batch_id']}/retry-failed"
    )

    assert response.status_code == 409


def test_retry_api_rejects_nonterminal_parent_even_when_failed_items_exist(tmp_path: Path) -> None:
    client, service, _session, _drafts = _persistent_retry_client(
        tmp_path, provider=_Provider(), budget=_Budget(),
        resolve_config=lambda _actor: {"api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com"},
    )
    service._repository.create(
        batch_id="running-parent", actor_id="actor-1", workspace_id="workspace-1",
        items=({"offer_id": "12345678", "source_url": "https://detail.1688.com/offer/12345678.html", "status": "failed", "outcome": "failed"},),
    )
    service._repository.set_status("running-parent", "running", summary={"failed_count": 1})

    response = client.post("/desktop/data-collection/plugin-onebound-batches/running-parent/retry-failed")

    assert response.status_code == 409


def test_prepare_removes_failed_persistence_attempts_from_active_capacity(tmp_path: Path) -> None:
    from wh_local.data_collection.plugin_onebound_capture import (
        PluginOneBoundCaptureDependencies,
        PluginOneBoundCaptureService,
    )

    class FailingRepository:
        def __init__(self) -> None:
            self.calls = 0

        def create(self, **_kwargs) -> None:
            self.calls += 1
            raise RuntimeError("disk unavailable")

    queue = DataCollectionPluginQueue(tmp_path / "runtime.sqlite3")
    session = queue.create_session(actor_id="actor-1", workspace_id="workspace-1")
    service = PluginOneBoundCaptureService(PluginOneBoundCaptureDependencies(
        plugin_queue=queue,
        provider_config_resolver=lambda _actor: {"api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com"},
        provider_factory=lambda _config: _Provider(), budget=_Budget(), draft_writer=_Drafts(),
    ))
    failed_repository = FailingRepository()
    service._repository = failed_repository

    for _ in range(3):
        with pytest.raises(RuntimeError, match="disk unavailable"):
            service.prepare(
                session_token=session["session_token"], page_url="https://detail.1688.com/offer/12345678.html",
                source_urls=["https://detail.1688.com/offer/12345678.html"],
            )

    assert failed_repository.calls == 3


def test_009_marker_recovery_skips_duplicate_alter_when_attempts_column_exists(tmp_path: Path) -> None:
    from wh_local.db import connect, init_db

    database = tmp_path / "009-recovery.sqlite3"
    init_db(database)
    with connect(database) as conn:
        conn.execute("DELETE FROM schema_migrations WHERE migration_id='data_collection:009_plugin_onebound_capture_item_attempts'")

    init_db(database)
    init_db(database)

    with connect(database) as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(plugin_onebound_capture_items)")}
        marker = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE migration_id='data_collection:009_plugin_onebound_capture_item_attempts'"
        ).fetchone()
    assert "attempts" in columns
    assert marker is not None


def _persist_failed_retry_parent(service) -> str:
    service._repository.create(
        batch_id="retry-parent", actor_id="actor-1", workspace_id="workspace-1",
        items=({"offer_id": "12345678", "source_url": "https://detail.1688.com/offer/12345678.html", "status": "failed", "outcome": "failed"},),
    )
    service._repository.set_status("retry-parent", "partial", summary={"failed_count": 1})
    return "retry-parent"


def test_retry_api_reuses_existing_child_without_second_provider_call(tmp_path: Path) -> None:
    provider = _Provider()
    budget = _Budget()
    client, service, _session, _drafts = _persistent_retry_client(
        tmp_path, provider=provider, budget=budget,
        resolve_config=lambda _actor: {"api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com"},
    )
    parent_id = _persist_failed_retry_parent(service)

    first = client.post(f"/desktop/data-collection/plugin-onebound-batches/{parent_id}/retry-failed")
    second = client.post(f"/desktop/data-collection/plugin-onebound-batches/{parent_id}/retry-failed")

    assert first.status_code == second.status_code == 202
    assert first.json()["batch"]["batch_id"] == second.json()["batch"]["batch_id"]
    assert provider.calls == 1
    assert len(budget.calls) == 1


def test_concurrent_retry_preparation_creates_exactly_one_child(tmp_path: Path) -> None:
    client, service, _session, _drafts = _persistent_retry_client(
        tmp_path, provider=_Provider(), budget=_Budget(),
        resolve_config=lambda _actor: {"api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com"},
    )
    _persist_failed_retry_parent(service)

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(
            lambda _index: service.prepare_retry_child(
                actor_id="actor-1", workspace_id="workspace-1", batch_id="retry-parent"
            ),
            range(6),
        ))

    assert {result["batch"]["batch_id"] for result in results}.__len__() == 1
    assert sum(bool(result["execute"]) for result in results) == 1


def test_retry_replaces_failed_start_child_after_provider_configuration_recovers(tmp_path: Path) -> None:
    available = {"value": False}

    def config(_actor):
        if not available["value"]:
            raise RuntimeError("provider configuration unavailable")
        return {"api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com"}

    provider = _Provider()
    client, service, _session, _drafts = _persistent_retry_client(
        tmp_path, provider=provider, budget=_Budget(), resolve_config=config,
    )
    parent_id = _persist_failed_retry_parent(service)

    first = client.post(f"/desktop/data-collection/plugin-onebound-batches/{parent_id}/retry-failed")
    available["value"] = True
    second = client.post(f"/desktop/data-collection/plugin-onebound-batches/{parent_id}/retry-failed")

    first_id = first.json()["batch"]["batch_id"]
    second_id = second.json()["batch"]["batch_id"]
    assert first.status_code == second.status_code == 202
    assert first_id != second_id
    assert client.get(f"/desktop/data-collection/plugin-onebound-batches/{first_id}").json()["batch"]["status"] == "failed"
    repaired = client.get(f"/desktop/data-collection/plugin-onebound-batches/{second_id}").json()["batch"]
    assert repaired["status"] == "completed"
    assert provider.calls == 1


def test_retry_replaces_expired_orphan_child(tmp_path: Path) -> None:
    provider = _Provider()
    client, service, _session, _drafts = _persistent_retry_client(
        tmp_path, provider=provider, budget=_Budget(),
        resolve_config=lambda _actor: {"api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com"},
    )
    parent_id = _persist_failed_retry_parent(service)
    service._repository.create(
        batch_id="expired-child", parent_batch_id=parent_id, actor_id="actor-1", workspace_id="workspace-1",
        items=({"offer_id": "12345678", "source_url": "https://detail.1688.com/offer/12345678.html"},),
    )
    service._repository.set_status("expired-child", "expired", summary={"unprocessed_count": 1})

    response = client.post(f"/desktop/data-collection/plugin-onebound-batches/{parent_id}/retry-failed")

    assert response.status_code == 202
    replacement_id = response.json()["batch"]["batch_id"]
    assert replacement_id != "expired-child"
    assert client.get(f"/desktop/data-collection/plugin-onebound-batches/{replacement_id}").json()["batch"]["status"] == "completed"
    assert provider.calls == 1


def test_retry_replaces_stale_prepared_child_without_an_in_memory_token(tmp_path: Path) -> None:
    from wh_local.db import connect

    provider = _Provider()
    client, service, _session, _drafts = _persistent_retry_client(
        tmp_path, provider=provider, budget=_Budget(),
        resolve_config=lambda _actor: {"api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com"},
    )
    parent_id = _persist_failed_retry_parent(service)
    service._repository.create(
        batch_id="stale-prepared-child", parent_batch_id=parent_id, actor_id="actor-1", workspace_id="workspace-1",
        items=({"offer_id": "12345678", "source_url": "https://detail.1688.com/offer/12345678.html"},),
    )
    with connect(service._repository.database_path) as conn:
        conn.execute("UPDATE plugin_onebound_capture_batches SET updated_at='2000-01-01 00:00:00' WHERE batch_id='stale-prepared-child'")

    response = client.post(f"/desktop/data-collection/plugin-onebound-batches/{parent_id}/retry-failed")

    assert response.status_code == 202
    assert response.json()["batch"]["batch_id"] != "stale-prepared-child"
    assert provider.calls == 1
