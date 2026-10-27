"""复刻主流程 HTTP 层测试：方案产出、建批次冻结 v4 计划、构图模板收窄与旧批次守卫。

全程使用假豆包方案与假计费，不触发任何真实付费调用。
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from wh_local.modules.pod_customization.billing_contract import (
    POD_BILLING_PROFILE_REPLICA_HD,
    PodExecutionGrant,
)
from wh_local.modules.pod_customization.contracts import COMPOSITION_PANEL_KEYS
from wh_local.modules.pod_customization.replica_analysis_runtime import (
    MAIN_PROMPT_MODEL,
    MAIN_PROMPT_VERSION,
    ReplicaMainPromptResult,
)
from wh_local.modules.pod_customization.replica_context import load_frozen_plan
from wh_local.modules.pod_customization.prompts import (
    DEFAULT_COMPOSITION_ID,
    DEFAULT_COMPOSITION_PANELS,
)
from wh_local.modules.pod_customization.replica_specs import REPLICA_PLAN_VERSION
from wh_local.modules.pod_customization.repository import PodRepositoryError
from wh_local.modules.pod_customization.router import create_router
from wh_local.modules.pod_customization.service import PodCustomizationService

API = "/api/pod-customization"
HEADERS = {"Authorization": "Bearer dev-admin-token"}
_MAIN_PROMPT = (
    "Use reference image 1 as the exact target product and reference image 2 only for the printed "
    "pattern. Place the full product on a warm wooden desk with a directional window light."
)


class NeverCalledRuntime:
    def submit(self, *_args, **_kwargs):
        raise AssertionError("AI image runtime must not run in analysis-only tests")


class Billing:
    def __init__(self) -> None:
        self.freezes: list[object] = []

    def freeze(self, _actor, plan):
        self.freezes.append(plan)
        return PodExecutionGrant(
            plan.idempotency_key, 1, "2099-01-01T00:00:00Z",
            {"ark": "test-ark-key", "wuyin": "test-wuyin-key"},
        )

    def settle(self, _actor, _grant, _plan, _outcomes) -> None:
        return None

    def regrant(self, actor, _freeze_id):
        return self.freeze(actor, None)


class AnalysisRuntime:
    """假豆包：一次调用返回主图提示词。"""

    def __init__(self, *, fail: bool = False, main_prompt: str = _MAIN_PROMPT) -> None:
        self.calls = 0
        self.fail = fail
        self.main_prompt = main_prompt

    def generate_main_prompt(
        self, source_asset, target_asset, *, grant, call_id, call_ids=None, on_start=None, on_outcome=None
    ) -> ReplicaMainPromptResult:
        self.calls += 1
        if self.fail:
            raise RuntimeError("analysis provider exploded")
        if on_outcome is not None:
            on_outcome(call_id, "success")
        return ReplicaMainPromptResult(
            main_prompt=self.main_prompt,
            model=MAIN_PROMPT_MODEL,
            prompt_version=MAIN_PROMPT_VERSION,
            attempt_count=1,
        )


def _png(color: str = "#f5f2ea") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(output, "PNG")
    return output.getvalue()


def _client(
    tmp_path: Path, analysis_runtime: AnalysisRuntime
) -> tuple[TestClient, PodCustomizationService, Billing]:
    billing = Billing()
    app = FastAPI()
    router = create_router(
        tmp_path / "workbench.sqlite3",
        tmp_path / "pod-assets",
        NeverCalledRuntime(),
        analysis_runtime=analysis_runtime,
        billing_coordinator=billing,
        start_workers=False,
    )
    app.include_router(router)
    return TestClient(app), router.pod_customization_service, billing


def _upload(client: TestClient, role: str, color: str) -> str:
    response = client.post(
        f"{API}/replica/images",
        headers=HEADERS,
        files={"file": (f"{role}.png", _png(color), "image/png")},
        data={"role": role},
    )
    assert response.status_code == 200, response.text
    return response.json()["asset_id"]


def _analyze(client: TestClient, source_id: str, target_id: str) -> dict:
    response = client.post(
        f"{API}/replica/analyze",
        headers=HEADERS,
        json={"source_asset_id": source_id, "target_asset_id": target_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _listing_fields() -> dict:
    return {
        "suggested_price_usd": 19.99,
        "category_name": "美妆 > 美甲",
        "skus": [{"name": "5片", "declared_price": 6.0, "weight_g": 300.0}],
    }


def _create_body(
    source_id: str, target_id: str, *, analysis_id: str = "", client_request_id: str
) -> dict:
    target: dict = {
        "target_asset_id": target_id,
        "product_name": "美甲套装",
        "listing_fields": _listing_fields(),
    }
    if analysis_id:
        target["analysis_id"] = analysis_id
    return {
        "client_request_id": client_request_id,
        "source_asset_id": source_id,
        "targets": [target],
    }


def _stored_plan(service: PodCustomizationService, batch_id: str) -> dict:
    with service.repository._connect() as connection:
        row = connection.execute(
            "SELECT replica_plan_json, prompt_snapshot, compiler_version FROM "
            "pod_customization_replica_targets WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()
    return {**json.loads(row["replica_plan_json"]), "_prompt_snapshot": row["prompt_snapshot"],
            "_compiler_version": row["compiler_version"]}


def _create_composition(
    service: PodCustomizationService,
    composition_id: str,
    version: str,
    *,
    scope: str = "replica",
    panels: dict | None = None,
) -> None:
    payload = panels or {
        key: {"zh": f"{key} 中文指令", "en": f"{key} english instruction"}
        for key in COMPOSITION_PANEL_KEYS
    }
    service.repository.create_composition(
        workspace_id="default",
        owner_user_id="local-demo-admin",
        composition_id=composition_id,
        name=composition_id,
        raw_input=composition_id,
        panels=payload,
        model="doubao-fake",
        prompt_version=version,
        scope=scope,
    )


def _create_with_plan(
    client: TestClient, *, composition_id: str = "", request_id: str
) -> dict:
    source_id = _upload(client, "source", "#dd0000")
    target_id = _upload(client, "target", "#00aa00")
    analysis = _analyze(client, source_id, target_id)
    body = _create_body(
        source_id, target_id, analysis_id=analysis["analysis_id"], client_request_id=request_id
    )
    if composition_id:
        body["composition_id"] = composition_id
    response = client.post(f"{API}/replica/batches", headers=HEADERS, json=body)
    assert response.status_code == 200, response.text
    return response.json()


# --- 方案产出 -----------------------------------------------------------------


def test_analyze_returns_plan_and_reuses_it(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, _service, _billing = _client(tmp_path, runtime)
    source_id = _upload(client, "source", "#dd0000")
    target_id = _upload(client, "target", "#00aa00")

    payload = _analyze(client, source_id, target_id)

    assert set(payload) == {
        "analysis_id",
        "status",
        "source_asset_id",
        "target_asset_id",
        "prompt_version",
        "model",
        "cached",
    }
    assert payload["status"] == "succeeded"
    assert payload["prompt_version"] == MAIN_PROMPT_VERSION
    assert payload["cached"] is False

    again = _analyze(client, source_id, target_id)
    assert again["analysis_id"] == payload["analysis_id"]
    assert again["cached"] is True
    assert runtime.calls == 1


def test_analyze_reuses_a_preexisting_plan_by_unique_key(tmp_path: Path) -> None:
    """缓存按 (A, B, 版本, 模型, 顺序) 命中，不依赖本次新生成的 plan_id。"""

    runtime = AnalysisRuntime()
    client, service, _billing = _client(tmp_path, runtime)
    source_id = _upload(client, "source", "#dd0000")
    target_id = _upload(client, "target", "#00aa00")
    service.repository.create_replica_transfer_plan(
        plan_id="canonical-plan",
        workspace_id="default",
        owner_user_id="local-demo-admin",
        source_asset_id=source_id,
        target_asset_id=target_id,
        prompt_version=MAIN_PROMPT_VERSION,
        model=MAIN_PROMPT_MODEL,
        reference_order="B,A",
        main_prompt="already generated prompt",
    )

    payload = _analyze(client, source_id, target_id)

    assert payload["analysis_id"] == "canonical-plan"
    assert payload["cached"] is True
    assert runtime.calls == 0


def test_analyze_requires_permission_and_valid_roles(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, _service, _billing = _client(tmp_path, runtime)
    source_id = _upload(client, "source", "#dd0000")
    target_id = _upload(client, "target", "#00aa00")

    unauthenticated = client.post(
        f"{API}/replica/analyze",
        json={"source_asset_id": source_id, "target_asset_id": target_id},
    )
    assert unauthenticated.status_code in {401, 403}

    wrong_role = client.post(
        f"{API}/replica/analyze",
        headers=HEADERS,
        json={"source_asset_id": target_id, "target_asset_id": target_id},
    )
    assert wrong_role.status_code == 400

    missing = client.post(
        f"{API}/replica/analyze",
        headers=HEADERS,
        json={"source_asset_id": "nope", "target_asset_id": target_id},
    )
    assert missing.status_code == 404


def test_failed_analysis_is_not_cached_and_can_be_retried(tmp_path: Path) -> None:
    runtime = AnalysisRuntime(fail=True)
    client, service, _billing = _client(tmp_path, runtime)
    source_id = _upload(client, "source", "#dd0000")
    target_id = _upload(client, "target", "#00aa00")

    failed = client.post(
        f"{API}/replica/analyze",
        headers=HEADERS,
        json={"source_asset_id": source_id, "target_asset_id": target_id},
    )
    assert failed.status_code == 503

    with service.repository._connect() as connection:
        rows = connection.execute(
            "SELECT plan_id FROM pod_customization_replica_transfer_plans"
        ).fetchall()
    assert rows == []

    runtime.fail = False
    payload = _analyze(client, source_id, target_id)
    assert payload["status"] == "succeeded"
    assert runtime.calls == 2


# --- 建批次与计划冻结 -----------------------------------------------------------


def test_create_batch_freezes_v4_plan_and_billing_shape(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, service, billing = _client(tmp_path, runtime)
    batch = _create_with_plan(client, request_id="req-plan")

    plan = _stored_plan(service, batch["id"])
    assert plan["plan_version"] == REPLICA_PLAN_VERSION
    assert plan["main_prompt"] == _MAIN_PROMPT
    assert plan["prompt_version"] == MAIN_PROMPT_VERSION
    assert plan["reference_order"] == "B,A"
    assert plan["generation_strategy"] == "independent_panels"
    assert plan["composition_template_id"] == DEFAULT_COMPOSITION_ID
    assert plan["image_model_version"]
    assert set(plan["derived_panels"]) == {"panel_2", "panel_3", "panel_4"}
    assert plan["_prompt_snapshot"] == _MAIN_PROMPT
    assert plan["_compiler_version"] == MAIN_PROMPT_VERSION
    # 建批次不再产出任何豆包调用。
    assert runtime.calls == 1

    # 计费：一款 = 1 link，预留 4 面板 × 2 次尝试，画像为复刻高清档。
    frozen = billing.freezes[-1]
    assert frozen.billing_profile == POD_BILLING_PROFILE_REPLICA_HD
    assert frozen.product_batch_freeze_payload()["link_count"] == 1
    assert sum(call.feature == "pod.image" for call in frozen.calls) == 8
    # 只有「方案分析」与「建批次」两次冻结，不再冻结任何复刻构图调用。
    freeze_keys = [plan.idempotency_key for plan in billing.freezes]
    assert len(freeze_keys) == 2
    assert not any("replica-composition" in key for key in freeze_keys)

    stored = service.repository.get_batch(batch["id"], "default", "local-demo-admin")
    frozen_plan = load_frozen_plan(stored, 1)
    assert frozen_plan.main_prompt == _MAIN_PROMPT
    assert frozen_plan.generation_strategy == "independent_panels"


def test_create_batch_requires_a_plan_for_every_target(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, service, _billing = _client(tmp_path, runtime)
    source_id = _upload(client, "source", "#dd0000")
    target_id = _upload(client, "target", "#00aa00")

    missing = client.post(
        f"{API}/replica/batches",
        headers=HEADERS,
        json=_create_body(source_id, target_id, client_request_id="req-no-plan"),
    )
    assert missing.status_code == 409

    unknown = client.post(
        f"{API}/replica/batches",
        headers=HEADERS,
        json=_create_body(
            source_id, target_id, analysis_id="does-not-exist", client_request_id="req-unknown"
        ),
    )
    assert unknown.status_code == 409


def test_create_batch_rejects_mismatched_assets(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, _service, _billing = _client(tmp_path, runtime)
    source_id = _upload(client, "source", "#dd0000")
    target_a = _upload(client, "target", "#00aa00")
    target_b = _upload(client, "target", "#0000cc")
    analysis = _analyze(client, source_id, target_a)

    response = client.post(
        f"{API}/replica/batches",
        headers=HEADERS,
        json=_create_body(
            source_id, target_b, analysis_id=analysis["analysis_id"], client_request_id="req-mismatch"
        ),
    )
    assert response.status_code == 409


def test_plan_is_not_shared_across_users(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, service, _billing = _client(tmp_path, runtime)
    source_id = _upload(client, "source", "#dd0000")
    target_id = _upload(client, "target", "#00aa00")
    analysis = _analyze(client, source_id, target_id)

    with pytest.raises(PodRepositoryError):
        service.repository.get_replica_transfer_plan(
            analysis["analysis_id"], "default", "someone-else"
        )


# --- 构图模板：只作用于派生三格 ------------------------------------------------


def test_default_template_covers_only_derived_panels(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, service, _billing = _client(tmp_path, runtime)
    batch = _create_with_plan(client, request_id="req-default-template")

    plan = _stored_plan(service, batch["id"])
    assert plan["composition_template_id"] == DEFAULT_COMPOSITION_ID
    # 没有复刻模板时用系统内置的默认三格拍法；panel_1 不参与派生。
    assert plan["derived_panels"]["panel_2"] == DEFAULT_COMPOSITION_PANELS["panel_2"]["en"]
    assert all(plan["derived_panels"][key] for key in ("panel_2", "panel_3", "panel_4"))
    assert "panel_1" not in plan["derived_panels"]


def test_selected_template_supplies_derived_directions_only(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, service, _billing = _client(tmp_path, runtime)
    _create_composition(service, "comp-active", "pod-composition-v9")

    batch = _create_with_plan(
        client, composition_id="comp-active", request_id="req-active-template"
    )

    plan = _stored_plan(service, batch["id"])
    assert plan["composition_template_id"] == "comp-active"
    assert plan["composition_template_version"] == "pod-composition-v9"
    assert plan["derived_panels"] == {
        "panel_2": "panel_2 english instruction",
        "panel_3": "panel_3 english instruction",
        "panel_4": "panel_4 english instruction",
    }


def test_rejects_unknown_inactive_or_cross_scope_template(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, service, _billing = _client(tmp_path, runtime)
    _create_composition(service, "comp-old", "v1")
    _create_composition(service, "comp-new", "v2")  # 新建即生效，comp-old 变为停用
    _create_composition(service, "comp-full", "v-full", scope="full")

    source_id = _upload(client, "source", "#dd0000")
    target_id = _upload(client, "target", "#00aa00")
    analysis = _analyze(client, source_id, target_id)

    def submit(composition_id: str, request_id: str) -> int:
        body = _create_body(
            source_id, target_id, analysis_id=analysis["analysis_id"], client_request_id=request_id
        )
        body["composition_id"] = composition_id
        return client.post(f"{API}/replica/batches", headers=HEADERS, json=body).status_code

    assert submit("comp-old", "req-inactive") == 409
    assert submit("definitely-missing", "req-missing") == 404
    assert submit("comp-full", "req-cross-scope") == 409


def test_template_missing_a_derived_slot_is_rejected(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, service, _billing = _client(tmp_path, runtime)
    incomplete = {
        key: {"zh": f"{key} 中文指令", "en": "" if key == "panel_3" else f"{key} english instruction"}
        for key in COMPOSITION_PANEL_KEYS
    }
    _create_composition(service, "comp-incomplete", "v1", panels=incomplete)

    source_id = _upload(client, "source", "#dd0000")
    target_id = _upload(client, "target", "#00aa00")
    analysis = _analyze(client, source_id, target_id)
    body = _create_body(
        source_id, target_id, analysis_id=analysis["analysis_id"], client_request_id="req-incomplete"
    )
    body["composition_id"] = "comp-incomplete"

    response = client.post(f"{API}/replica/batches", headers=HEADERS, json=body)
    assert response.status_code == 409
    assert "panel_3" in response.json()["detail"]


def test_replica_pool_does_not_borrow_full_custom_template(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, service, _billing = _client(tmp_path, runtime)
    _create_composition(service, "comp-full", "v-full", scope="full")

    batch = _create_with_plan(client, request_id="req-scope-isolation")

    plan = _stored_plan(service, batch["id"])
    assert plan["composition_template_id"] == DEFAULT_COMPOSITION_ID


# --- 旧批次只读 -----------------------------------------------------------------


def _make_legacy(tmp_path: Path, client: TestClient, service: PodCustomizationService) -> str:
    batch = _create_with_plan(client, request_id="req-legacy")
    with service.repository._connect() as connection:
        connection.execute(
            "UPDATE pod_customization_replica_targets SET replica_plan_json = ?, "
            "prompt_snapshot = '' WHERE batch_id = ?",
            (json.dumps({"plan_version": "1"}), batch["id"]),
        )
    return batch["id"]


def test_legacy_batch_rejects_style_and_batch_retry(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, service, billing = _client(tmp_path, runtime)
    batch_id = _make_legacy(tmp_path, client, service)
    freezes_before = len(billing.freezes)

    style = client.post(
        f"{API}/batches/{batch_id}/styles/1/regenerate",
        headers=HEADERS,
        json={},
    )
    assert style.status_code == 409

    batch_retry = client.post(
        f"{API}/batches/{batch_id}/retry-failed",
        headers=HEADERS,
        json={"image_style_indices": [1], "title_style_indices": []},
    )
    assert batch_retry.status_code == 409
    # 守卫在冻结之前生效：不产生新的计费冻结，也不会进入生图。
    assert len(billing.freezes) == freezes_before


def test_legacy_batch_rejects_resume(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, service, billing = _client(tmp_path, runtime)
    batch_id = _make_legacy(tmp_path, client, service)
    freezes_before = len(billing.freezes)
    service.repository.request_pause(batch_id)
    service.repository.mark_batch_paused(batch_id, "已暂停")

    resumed = client.post(f"{API}/batches/{batch_id}/resume", headers=HEADERS)
    assert resumed.status_code == 409
    assert len(billing.freezes) == freezes_before


def test_legacy_plan_is_reported_as_legacy(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, service, _billing = _client(tmp_path, runtime)
    batch_id = _make_legacy(tmp_path, client, service)
    stored = service.repository.get_batch(batch_id, "default", "local-demo-admin")

    from wh_local.modules.pod_customization.replica_context import LegacyReplicaPlanError

    with pytest.raises(LegacyReplicaPlanError):
        load_frozen_plan(stored, 1)
