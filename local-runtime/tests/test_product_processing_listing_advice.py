from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wh_local.customer.contracts import CustomerAuthResult
from wh_local.customer.local_session import LocalSessionService
from wh_local.modules.product_processing.api import router as product_processing_router
from wh_local.modules.product_processing.api.router import create_product_processing_router
from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository
from wh_local.modules.product_processing.listing_advice import (
    build_listing_advice_messages,
    deterministic_listing_advice,
    merge_ai_listing_advice,
    prepare_listing_context,
    rank_listing_rules,
)
from wh_local.modules.product_processing.service import ProductProcessingService
from wh_local.session import Actor, actor_from_authorization


def test_handcrafted_gourd_matches_handmade_table_row() -> None:
    rules = rank_listing_rules(
        "Handcrafted Hollow Natural Gourd, Keychain or Car Hanging Pendant",
        "Natural wood with a thick twisted rope edge.",
        "其他",
    )

    assert rules[0].number == 12
    advice = deterministic_listing_advice(
        prepare_listing_context(
            "Handcrafted Hollow Natural Gourd, Keychain or Car Hanging Pendant",
            "Natural wood with a thick twisted rope edge.",
            "其他",
        )
    )
    assert advice["level"] == "B-可做"
    assert "Handmade Products" in advice["recommended_category"]


def test_high_risk_terms_are_exposed_in_rule_fallback() -> None:
    context = prepare_listing_context(
        "Kids rechargeable Bluetooth toy",
        "A wireless electronic toy for children",
        "玩具与游戏",
    )

    advice = deterministic_listing_advice(context)

    assert advice["matched_rule_number"] == 22
    assert advice["level"] == "D-高门槛"
    assert "儿童/婴幼儿用途" in advice["warning"]
    assert "带电、无线或电池" in advice["warning"]


def test_ai_can_explain_but_cannot_lower_table_risk_level() -> None:
    context = prepare_listing_context("Bluetooth speaker", "rechargeable battery", "电子")
    candidate_number = context["candidates"][0].number
    response = merge_ai_listing_advice(
        context,
        json.dumps(
            {
                "matched_rule_number": candidate_number,
                "level": "A-优先",
                "reason": "包含蓝牙和电池功能，按电子类规则判断。",
                "warning": "先核对无线、电气和电池合规资料。",
                "required_documents": ["FCC", "UN38.3/MSDS", "extra"],
            },
            ensure_ascii=False,
        ),
    )

    assert response["level"] == "D-高门槛"
    assert response["required_documents"] == ["FCC", "UN38.3/MSDS"]


def test_ai_cannot_select_rule_outside_server_candidates() -> None:
    context = prepare_listing_context("Handmade wood pendant", "natural craft", "")

    with pytest.raises(ValueError, match="outside the candidates"):
        merge_ai_listing_advice(
            context,
            '{"matched_rule_number": 27, "reason": "x", "warning": "y", "required_documents": []}',
        )


def test_prompt_contains_only_candidate_rules_and_disclaimer_constraint() -> None:
    context = prepare_listing_context("Pet leash", "nylon dog lead", "宠物用品")
    messages = build_listing_advice_messages(context)
    payload = json.loads(messages[1]["content"])

    assert len(payload["candidate_rules"]) == 3
    assert payload["candidate_rules"][0]["number"] == 2
    assert "不得降低" in messages[0]["content"]


def test_listing_advice_endpoint_falls_back_to_table_rules_without_ai_session(tmp_path) -> None:
    database = create_database("sqlite:///:memory:")
    service = ProductProcessingService(
        ProductProcessingRepository(database),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    draft, _created = service.create_draft(
        {"source_type": "manual", "title": "Handcrafted wood pendant"},
        workspace_id="local",
    )
    task = service.repository.create_task(
        title="listing advice test",
        preflight_only=False,
        settings={},
        drafts=[draft],
        idempotency_key=None,
        workspace_id="local",
    )
    app = FastAPI()
    app.dependency_overrides[actor_from_authorization] = lambda: Actor(
        id="user", username="user", role="operator"
    )
    app.include_router(create_product_processing_router(service))

    try:
        response = TestClient(app).post(
            f"/product-processing/tasks/{task['id']}/preview/items/{draft['id']}/listing-advice",
            json={
                "title": "Handcrafted hollow natural gourd pendant",
                "description": "Natural wood keychain with rope edge",
                "category_path": "其他",
                "request_id": "test-request-001",
            },
        )

        assert response.status_code == 200
        assert response.json()["matched_rule_number"] == 12
        assert response.json()["source"] == "rules"
        assert "AI 会话暂不可用" in response.json()["notice"]
    finally:
        getattr(service, "_dimension_canvas_service").close()
        database.dispose()


def test_listing_advice_endpoint_reserves_ai_and_settles_success(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = create_database("sqlite:///:memory:")
    service = ProductProcessingService(
        ProductProcessingRepository(database),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    draft, _created = service.create_draft(
        {"source_type": "manual", "title": "Handcrafted wood pendant"},
        workspace_id="local",
    )
    task = service.repository.create_task(
        title="listing advice AI test",
        preflight_only=False,
        settings={"_billing": {"account_id": "user"}},
        drafts=[draft],
        idempotency_key=None,
        workspace_id="local",
    )

    class RecordingRemote:
        def __init__(self) -> None:
            self.events: list[tuple[str, str]] = []

        def reserve_ai_usage(self, token, payload):
            self.events.append(("reserve", token))
            assert payload["feature_key"] == "product_processing.text"
            return {
                "usage": {
                    "usage_id": "usage-1",
                    "status": "reserved",
                    "feature_key": "product_processing.text",
                }
            }

        def settle_ai_usage_success(self, token, usage_id, _payload):
            self.events.append(("succeed", f"{token}:{usage_id}"))
            return {"usage": {"usage_id": usage_id, "status": "succeeded"}}

        def settle_ai_usage_failure(self, token, usage_id, _payload):
            self.events.append(("fail", f"{token}:{usage_id}"))
            return {"usage": {"usage_id": usage_id, "status": "failed"}}

    class StubAiClient:
        def __init__(self, usage_kind: str) -> None:
            assert usage_kind == "text"

        def complete(self, messages):
            assert messages[0]["role"] == "system"
            return json.dumps(
                {
                    "matched_rule_number": 12,
                    "reason": "天然葫芦手工挂件，匹配普通手工品规则。",
                    "warning": "确认手工属性真实，避免品牌和儿童用途宣称。",
                    "required_documents": [],
                },
                ensure_ascii=False,
            )

    sessions = LocalSessionService()
    session = sessions.login_customer(
        CustomerAuthResult(customer_id="user", username="user", remote_token="remote-session")
    )
    remote = RecordingRemote()
    monkeypatch.setattr(product_processing_router, "DoubaoArkClient", StubAiClient)
    app = FastAPI()
    app.dependency_overrides[actor_from_authorization] = lambda: Actor(
        id="user", username="user", role="operator"
    )
    app.include_router(
        create_product_processing_router(
            service,
            customer_sessions=sessions,
            remote_customer_auth=remote,
        )
    )

    try:
        response = TestClient(app).post(
            f"/product-processing/tasks/{task['id']}/preview/items/{draft['id']}/listing-advice",
            headers={"Authorization": f"Bearer {session.token}"},
            json={
                "title": "Handcrafted hollow natural gourd pendant",
                "description": "Natural wood keychain with rope edge",
                "category_path": "其他",
                "request_id": "test-request-ai-001",
            },
        )

        assert response.status_code == 200
        assert response.json()["source"] == "ai+rules"
        assert response.json()["level"] == "B-可做"
        assert remote.events == [
            ("reserve", "remote-session"),
            ("succeed", "remote-session:usage-1"),
        ]
    finally:
        getattr(service, "_dimension_canvas_service").close()
        database.dispose()
