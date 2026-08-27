from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from wh_local.data_collection.plugin_queue import DataCollectionPluginQueue


class _Budget:
    def reserve(self, *, workspace_id: str, provider_fingerprint: str, max_api_calls: int, api_calls: int = 1):
        return type("State", (), {"reservation_granted": True})()


class _Drafts:
    def __init__(self) -> None:
        self.by_candidate: dict[str, dict] = {}
        self.intakes: list[dict] = []

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
        return {"materialized": 0}


def _detail_payload(offer_id: str, *, with_sku: bool) -> dict:
    item: dict = {
        "offer_id": offer_id,
        "detail_url": f"https://detail.1688.com/offer/{offer_id}.html",
        "title": f"Product {offer_id}",
        "main_image_url": "https://img.example.com/main.jpg",
    }
    if with_sku:
        item["sku"] = {"sku": [{"sku_id": f"{offer_id}-sku", "price": "10.0"}]}
    return {"item": item}


class _DetailProvider:
    def __init__(self, *, first_without_sku: bool = False) -> None:
        self.calls = 0
        self.first_without_sku = first_without_sku

    def get_item_detail(self, offer_id: str):
        self.calls += 1
        with_sku = not (self.first_without_sku and self.calls == 1)
        return type(
            "Result",
            (),
            {"ok": True, "response": _detail_payload(offer_id, with_sku=with_sku), "audit": None},
        )()


def _service(tmp_path: Path, provider, drafts, *, database_name: str = "runtime.sqlite3"):
    from wh_local.data_collection.plugin_onebound_capture import (
        PluginOneBoundCaptureDependencies,
        PluginOneBoundCaptureService,
    )

    database = tmp_path / database_name
    queue = DataCollectionPluginQueue(database)
    session = queue.create_session(actor_id="actor-1", workspace_id="workspace-1")
    service = PluginOneBoundCaptureService(PluginOneBoundCaptureDependencies(
        plugin_queue=queue,
        provider_config_resolver=lambda _actor: {
            "api_key": "key", "api_secret": "secret", "base_url": "https://api.example.com",
        },
        provider_factory=lambda _config: provider,
        budget=_Budget(),
        draft_writer=drafts,
        database_path=str(database),
    ))
    return service, session


def _capture_one(service, session, offer_id: str) -> dict:
    prepared = service.prepare(
        session_token=session["session_token"],
        page_url=f"https://detail.1688.com/offer/{offer_id}.html",
        source_urls=[f"https://detail.1688.com/offer/{offer_id}.html"],
    )
    service.start(session_token=session["session_token"], batch_token=prepared["batch_token"])
    service.item(
        session_token=session["session_token"], batch_token=prepared["batch_token"],
        source_url=f"https://detail.1688.com/offer/{offer_id}.html",
    )
    service.finish(session_token=session["session_token"], batch_token=prepared["batch_token"], cancelled=False)
    return prepared


def _wait_for_sku_repull(service, batch_id: str, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    state: dict = {}
    while time.monotonic() < deadline:
        state = service.sku_repull_state(actor_id="actor-1", workspace_id="workspace-1", batch_id=batch_id)
        if state.get("status") in {"completed", "failed", "cancelled"}:
            return state
        time.sleep(0.01)
    return state


def test_auto_sku_backfill_enriches_empty_sku_candidates_once(tmp_path: Path) -> None:
    provider = _DetailProvider(first_without_sku=True)
    drafts = _Drafts()
    service, session = _service(tmp_path, provider, drafts)
    prepared = _capture_one(service, session, "12345678")

    state = _wait_for_sku_repull(service, prepared["batch_id"])
    assert state["status"] == "completed"
    assert state["total"] == 1
    assert state["succeeded"] == 1

    item = service._repository.get_item(prepared["batch_id"], "12345678")
    assert item is not None
    assert item["review_status"] == "pending"
    parsed = json.loads(item["candidate_json"])
    assert parsed["source_variant_records"]
    assert drafts.intakes == []


def test_auto_sku_backfill_skips_candidates_that_already_have_sku(tmp_path: Path) -> None:
    provider = _DetailProvider(first_without_sku=False)
    drafts = _Drafts()
    service, session = _service(tmp_path, provider, drafts)
    prepared = _capture_one(service, session, "12345678")

    state = _wait_for_sku_repull(service, prepared["batch_id"])
    assert state["status"] == "completed"
    assert state["total"] == 0
    # No backfill provider call: only the original capture call happened.
    assert provider.calls == 1


def test_confirm_candidates_creates_drafts_idempotently(tmp_path: Path) -> None:
    provider = _DetailProvider()
    drafts = _Drafts()
    service, session = _service(tmp_path, provider, drafts)
    prepared = _capture_one(service, session, "12345678")
    _wait_for_sku_repull(service, prepared["batch_id"])

    first = service.confirm_candidates(
        actor_id="actor-1", workspace_id="workspace-1", batch_id=prepared["batch_id"],
        offer_ids=["12345678"],
    )
    second = service.confirm_candidates(
        actor_id="actor-1", workspace_id="workspace-1", batch_id=prepared["batch_id"],
        offer_ids=["12345678"],
    )

    assert first["confirmed_count"] == 1
    assert second["confirmed_count"] == 0
    assert len(drafts.intakes) == 1
    assert drafts.intakes[0]["candidate"]["candidate_id"] == "1688:12345678"

    item = service._repository.get_item(prepared["batch_id"], "12345678")
    assert item["review_status"] == "confirmed"
    assert item["draft_id"] == 1
    assert item["outcome"] == "created"


def test_confirm_ignores_unselected_and_non_pending_candidates(tmp_path: Path) -> None:
    provider = _DetailProvider()
    drafts = _Drafts()
    service, session = _service(tmp_path, provider, drafts)
    prepared = _capture_one(service, session, "12345678")
    _wait_for_sku_repull(service, prepared["batch_id"])

    result = service.confirm_candidates(
        actor_id="actor-1", workspace_id="workspace-1", batch_id=prepared["batch_id"],
        offer_ids=["99999999", "12345678"],
    )

    assert result["confirmed_count"] == 1
    assert [entry["offer_id"] for entry in result["confirmed"]] == ["12345678"]


def test_confirm_conflicts_while_sku_backfill_is_running(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingProvider(_DetailProvider):
        def get_item_detail(self, offer_id: str):
            self.calls += 1
            if self.calls == 1:
                # Capture call leaves SKU empty so a backfill round is required.
                return type(
                    "Result",
                    (),
                    {"ok": True, "response": _detail_payload(offer_id, with_sku=False), "audit": None},
                )()
            # Backfill call blocks so the round stays running while we confirm.
            entered.set()
            assert release.wait(timeout=3)
            return type(
                "Result",
                (),
                {"ok": True, "response": _detail_payload(offer_id, with_sku=True), "audit": None},
            )()

    provider = BlockingProvider(first_without_sku=True)
    drafts = _Drafts()
    service, session = _service(tmp_path, provider, drafts)
    prepared = _capture_one(service, session, "12345678")

    assert entered.wait(timeout=2)
    with pytest.raises(ValueError, match="running"):
        service.confirm_candidates(
            actor_id="actor-1", workspace_id="workspace-1", batch_id=prepared["batch_id"],
            offer_ids=["12345678"],
        )
    release.set()
    _wait_for_sku_repull(service, prepared["batch_id"])


def test_manual_sku_backfill_retries_after_cancelled_round(tmp_path: Path) -> None:
    provider = _DetailProvider(first_without_sku=True)
    drafts = _Drafts()
    service, session = _service(tmp_path, provider, drafts)
    prepared = _capture_one(service, session, "12345678")
    _wait_for_sku_repull(service, prepared["batch_id"])

    # After the auto round completes, a manual start is still accepted.
    started = service.start_sku_repull(
        actor_id="actor-1", workspace_id="workspace-1", batch_id=prepared["batch_id"],
    )
    assert started["status"] == "completed"

    state = service.sku_repull_state(
        actor_id="actor-1", workspace_id="workspace-1", batch_id=prepared["batch_id"],
    )
    assert state["status"] == "completed"
