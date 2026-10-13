"""复刻识图主流程的 HTTP 层测试：分析、确认与带分析的建批次。

全程使用假豆包分析与假计费，不触发任何真实付费调用。
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from wh_local.modules.pod_customization.billing_contract import PodExecutionGrant
from wh_local.modules.pod_customization.contracts import COMPOSITION_PANEL_KEYS
from wh_local.modules.pod_customization.replica_analysis_runtime import (
    ReplicaAnalysisResult,
    build_composition_plan,
)
from wh_local.modules.pod_customization.replica_context import (
    ReplicaPlanMissingError,
    load_frozen_plan,
)
from wh_local.modules.pod_customization.prompts import DEFAULT_COMPOSITION_ID
from wh_local.modules.pod_customization.replica_specs import (
    CompositionPlan,
    REPLICA_ANALYSIS_SCHEMA_VERSION,
    SOURCE_FACT_FIELDS,
    TARGET_FACT_FIELDS,
    normalize_source_analysis,
    normalize_target_analysis,
)
from wh_local.modules.pod_customization.router import create_router
from wh_local.modules.pod_customization.service import PodCustomizationService

API = "/api/pod-customization"
HEADERS = {"Authorization": "Bearer dev-admin-token"}

_SOURCE_VALUES: dict[str, object] = {
    "source_product_name": "原素材帆布袋",
    "artwork_motifs": ["玫瑰"],
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


def _raw(values: dict[str, object], fields: tuple[str, ...], confidence: float = 0.9) -> dict[str, object]:
    return {
        name: {"value": values[name], "confidence": confidence, "source": "model"} for name in fields
    }


class NeverCalledRuntime:
    def submit(self, *_args, **_kwargs):
        raise AssertionError("AI image runtime must not run in analysis-only tests")


class Billing:
    def __init__(self) -> None:
        self.freezes: list[object] = []

    def freeze(self, _actor, plan):
        self.freezes.append(plan)
        return PodExecutionGrant("freeze-1", 1, "2099-01-01T00:00:00Z", {"ark": "test-ark-key"})

    def settle(self, _actor, _grant, _plan, _outcomes) -> None:
        return None

    def regrant(self, actor, _freeze_id):
        return self.freeze(actor, None)


class AnalysisRuntime:
    """假豆包：按设置返回成功/失败的分析，并实现构图参数选择。"""

    def __init__(self, *, fail: bool = False, target_confidence: float = 0.9) -> None:
        self.calls = 0
        self.composition_calls = 0
        self.fail = fail
        self.target_confidence = target_confidence

    def analyze_assets(
        self, source_asset, target_asset, *, grant, call_id, call_ids=None, on_start=None, on_outcome=None
    ) -> ReplicaAnalysisResult:
        self.calls += 1
        if self.fail:
            raise RuntimeError("analysis provider exploded")
        if on_outcome is not None:
            on_outcome(call_id, "success")
        raw_source = _raw(_SOURCE_VALUES, SOURCE_FACT_FIELDS)
        raw_target = _raw(_TARGET_VALUES, TARGET_FACT_FIELDS, confidence=self.target_confidence)
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
    ) -> CompositionPlan:
        self.composition_calls += 1
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


def _create_body(source_id: str, target_id: str, *, analysis_id: str = "", client_request_id: str) -> dict:
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


def test_analyze_returns_facts_and_reuses_successful_analysis(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, service, _billing = _client(tmp_path, runtime)
    source_id = _upload(client, "source", "#dd0000")
    target_id = _upload(client, "target", "#00aa00")

    payload = _analyze(client, source_id, target_id)

    assert payload["status"] == "succeeded"
    assert payload["target"]["product_type"]["value"] == "nail_tip_set"
    assert payload["source"]["artwork_motifs"]["value"] == ["玫瑰"]
    assert payload["source_needs_confirmation"] is False
    assert {"multi_piece", "gel_material", "macro_detail"} <= set(payload["candidate_capabilities"])
    assert "hand_worn" not in payload["candidate_capabilities"]

    # 相同资产与版本：直接复用成功分析，不再调用 provider。
    again = _analyze(client, source_id, target_id)
    assert again["analysis_id"] == payload["analysis_id"]
    assert runtime.calls == 1


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

    # 角色错误：把目标图当样图。
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


def test_analyze_failure_is_persisted_and_can_be_retried(tmp_path: Path) -> None:
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
            "SELECT status, error_code FROM pod_customization_replica_analyses"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["error_code"] == "RuntimeError"

    # 修好后重新分析：失败记录不阻塞。
    runtime.fail = False
    payload = _analyze(client, source_id, target_id)
    assert payload["status"] == "succeeded"


def test_create_batch_with_analysis_freezes_plan(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, service, _billing = _client(tmp_path, runtime)
    source_id = _upload(client, "source", "#dd0000")
    target_id = _upload(client, "target", "#00aa00")
    analysis = _analyze(client, source_id, target_id)

    response = client.post(
        f"{API}/replica/batches",
        headers=HEADERS,
        json=_create_body(source_id, target_id, analysis_id=analysis["analysis_id"], client_request_id="req-plan"),
    )
    assert response.status_code == 200, response.text

    with service.repository._connect() as connection:
        row = connection.execute(
            "SELECT analysis_id, replica_plan_json, capability_registry_version, rule_version "
            "FROM pod_customization_replica_targets"
        ).fetchone()
    assert row["analysis_id"] == analysis["analysis_id"]
    plan = json.loads(row["replica_plan_json"])
    assert plan["capability_set"]["capabilities"] == analysis["candidate_capabilities"]
    assert row["capability_registry_version"]
    assert row["rule_version"] == plan["rule_set"]["rule_version"]
    assert any("只能复制原素材图" in item for item in plan["rule_set"]["hard_constraints"])


def test_create_batch_without_analysis_keeps_legacy_behaviour(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, service, _billing = _client(tmp_path, runtime)
    source_id = _upload(client, "source", "#dd0000")
    target_id = _upload(client, "target", "#00aa00")

    response = client.post(
        f"{API}/replica/batches",
        headers=HEADERS,
        json=_create_body(source_id, target_id, client_request_id="req-legacy"),
    )
    assert response.status_code == 200, response.text
    with service.repository._connect() as connection:
        row = connection.execute(
            "SELECT analysis_id, replica_plan_json FROM pod_customization_replica_targets"
        ).fetchone()
    assert row["analysis_id"] == ""
    assert row["replica_plan_json"] == ""


def test_create_batch_rejects_foreign_analysis(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, service, _billing = _client(tmp_path, runtime)
    source_id = _upload(client, "source", "#dd0000")
    target_id = _upload(client, "target", "#00aa00")
    service.repository.save_replica_analysis(
        analysis_id="foreign-analysis",
        workspace_id="default",
        owner_user_id="someone-else",
        source_asset_id=source_id,
        target_asset_id=target_id,
        analysis_version=REPLICA_ANALYSIS_SCHEMA_VERSION,
        status="succeeded",
    )

    response = client.post(
        f"{API}/replica/batches",
        headers=HEADERS,
        json=_create_body(source_id, target_id, analysis_id="foreign-analysis", client_request_id="req-foreign"),
    )
    assert response.status_code == 404


def test_create_batch_rejects_low_confidence_analysis(tmp_path: Path) -> None:
    runtime = AnalysisRuntime(target_confidence=0.2)
    client, _service, _billing = _client(tmp_path, runtime)
    source_id = _upload(client, "source", "#dd0000")
    target_id = _upload(client, "target", "#00aa00")
    analysis = _analyze(client, source_id, target_id)
    assert analysis["target_needs_confirmation"] is True

    response = client.post(
        f"{API}/replica/batches",
        headers=HEADERS,
        json=_create_body(source_id, target_id, analysis_id=analysis["analysis_id"], client_request_id="req-lowconf"),
    )
    assert response.status_code == 400


def test_create_batch_rejects_analysis_target_mismatch(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, _service, _billing = _client(tmp_path, runtime)
    source_id = _upload(client, "source", "#dd0000")
    target_a = _upload(client, "target", "#00aa00")
    target_b = _upload(client, "target", "#0000cc")
    analysis = _analyze(client, source_id, target_a)

    response = client.post(
        f"{API}/replica/batches",
        headers=HEADERS,
        json=_create_body(source_id, target_b, analysis_id=analysis["analysis_id"], client_request_id="req-mismatch"),
    )
    assert response.status_code == 409


# --- Task 4：构图模板接入与 CompositionPlan 冻结 ---------------------------------


def _create_composition(service: PodCustomizationService, composition_id: str, version: str) -> None:
    panels = {
        key: {"zh": f"{key} 中文指令", "en": f"{key} english instruction"}
        for key in COMPOSITION_PANEL_KEYS
    }
    service.repository.create_composition(
        workspace_id="default",
        owner_user_id="local-demo-admin",
        composition_id=composition_id,
        name=composition_id,
        raw_input=composition_id,
        panels=panels,
        model="doubao-fake",
        prompt_version=version,
    )


def _stored_plan(service: PodCustomizationService, batch_id: str) -> dict:
    with service.repository._connect() as connection:
        row = connection.execute(
            "SELECT replica_plan_json FROM pod_customization_replica_targets WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()
    return json.loads(row["replica_plan_json"])


def _create_with_analysis(client: TestClient, service, runtime, *, composition_id: str = "", request_id: str) -> dict:
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


def test_frozen_plan_carries_default_composition_and_versions(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, service, _billing = _client(tmp_path, runtime)
    batch = _create_with_analysis(client, service, runtime, request_id="req-default-comp")

    plan = _stored_plan(service, batch["id"])
    assert plan["composition_plan"]["template_id"] == DEFAULT_COMPOSITION_ID
    slots = plan["composition_plan"]["slots"]
    assert [slot["slot_id"] for slot in slots] == list(COMPOSITION_PANEL_KEYS)
    assert plan["capability_set"]["registry_version"]
    assert plan["rule_set"]["rule_version"]
    assert runtime.composition_calls == 1

    # 冻结计划可原样读回（重试/恢复只读快照，不重新推导）。
    stored_batch = service.repository.get_batch(batch["id"], "default", "local-demo-admin")
    frozen = load_frozen_plan(stored_batch, 1)
    assert frozen.composition_plan is not None
    assert frozen.composition_plan.template_id == DEFAULT_COMPOSITION_ID
    assert frozen.generation_strategy == "independent_panels"
    assert all(not slot.hand_worn for slot in frozen.composition_plan.slots)


def test_create_batch_uses_selected_active_template(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, service, _billing = _client(tmp_path, runtime)
    _create_composition(service, "comp-active", "pod-composition-v9")

    batch = _create_with_analysis(
        client, service, runtime, composition_id="comp-active", request_id="req-active-comp"
    )

    plan = _stored_plan(service, batch["id"])
    assert plan["composition_plan"]["template_id"] == "comp-active"
    assert plan["composition_plan"]["template_version"] == "pod-composition-v9"
    assert plan["composition_template_version"] == "pod-composition-v9"
    # 模板四格指令作为 shot 缺省文本被冻结。
    assert plan["composition_plan"]["slots"][0]["shot"] == "panel_1 english instruction"


def test_create_batch_rejects_unknown_or_inactive_template(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, service, _billing = _client(tmp_path, runtime)
    _create_composition(service, "comp-old", "v1")
    _create_composition(service, "comp-new", "v2")  # 新建即生效，comp-old 变为停用

    source_id = _upload(client, "source", "#dd0000")
    target_id = _upload(client, "target", "#00aa00")
    analysis = _analyze(client, source_id, target_id)
    body = _create_body(
        source_id, target_id, analysis_id=analysis["analysis_id"], client_request_id="req-inactive"
    )
    body["composition_id"] = "comp-old"
    inactive = client.post(f"{API}/replica/batches", headers=HEADERS, json=body)
    assert inactive.status_code == 409

    body["composition_id"] = "definitely-missing"
    body["client_request_id"] = "req-missing"
    missing = client.post(f"{API}/replica/batches", headers=HEADERS, json=body)
    assert missing.status_code == 404


def test_load_frozen_plan_rejects_legacy_batch_without_plan(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, service, _billing = _client(tmp_path, runtime)
    source_id = _upload(client, "source", "#dd0000")
    target_id = _upload(client, "target", "#00aa00")
    batch = client.post(
        f"{API}/replica/batches",
        headers=HEADERS,
        json=_create_body(source_id, target_id, client_request_id="req-no-plan"),
    )
    assert batch.status_code == 200

    stored = service.repository.get_batch(batch.json()["id"], "default", "local-demo-admin")
    with pytest.raises(ReplicaPlanMissingError):
        load_frozen_plan(stored, 1)


def test_multi_piece_plan_requires_full_component_panel(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, service, _billing = _client(tmp_path, runtime)
    batch = _create_with_analysis(client, service, runtime, request_id="req-multi-piece")

    plan = _stored_plan(service, batch["id"])
    assert "multi_piece" in plan["capability_set"]["capabilities"]
    assert any(slot["show_all_components"] for slot in plan["composition_plan"]["slots"])
    # 未选择手部展示：任何槽位都不得出现手部。
    assert all(not slot["hand_worn"] for slot in plan["composition_plan"]["slots"])
    assert plan["generation_strategy"] == "independent_panels"

