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
from wh_local.modules.pod_customization import service as pod_service_module
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


def _create_composition(
    service: PodCustomizationService,
    composition_id: str,
    version: str,
    *,
    scope: str = "replica",
) -> None:
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
        scope=scope,
    )


def _stored_plan(service: PodCustomizationService, batch_id: str) -> dict:
    with service.repository._connect() as connection:
        row = connection.execute(
            "SELECT replica_plan_json FROM pod_customization_replica_targets WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()
    return json.loads(row["replica_plan_json"])


def _create_with_analysis(
    client: TestClient, service, runtime, *, composition_id: str = "", request_id: str
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
    # 冻结真实最终 Prompt（四段式），不再写空对象。
    assert plan["compiled_prompt"].startswith("=== PART 1")
    assert "PART 4" in plan["compiled_prompt"]
    assert plan["prompt_compiler_version"]
    # 生图模型版本同样随计划冻结（事后可归因），不得为空。
    assert plan["image_model_version"]
    assert runtime.composition_calls == 1

    # 冻结计划可原样读回（重试/恢复只读快照，不重新推导）。
    stored_batch = service.repository.get_batch(batch["id"], "default", "local-demo-admin")
    frozen = load_frozen_plan(stored_batch, 1)
    assert frozen.composition_plan is not None
    assert frozen.composition_plan.template_id == DEFAULT_COMPOSITION_ID
    # 生成策略不再由服务端按能力自动切换：未显式指定时统一按成本最低的 grid。
    assert frozen.generation_strategy == "grid"
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


def test_replica_ignores_full_custom_composition_pool(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, service, _billing = _client(tmp_path, runtime)
    # 全定制池里有一份「生效」模板，但复刻池为空。
    _create_composition(service, "comp-full", "v-full", scope="full")

    batch = _create_with_analysis(client, service, runtime, request_id="req-scope-isolation")
    plan = _stored_plan(service, batch["id"])
    # 复刻回退系统默认机位，绝不借用全定制的产品化模板。
    assert plan["composition_plan"]["template_id"] == DEFAULT_COMPOSITION_ID

    # 也不能显式把全定制的模板挂到复刻批次上。
    source_id = _upload(client, "source", "#dd0000")
    target_id = _upload(client, "target", "#00aa00")
    analysis = _analyze(client, source_id, target_id)
    body = _create_body(
        source_id, target_id, analysis_id=analysis["analysis_id"], client_request_id="req-cross"
    )
    body["composition_id"] = "comp-full"
    rejected = client.post(f"{API}/replica/batches", headers=HEADERS, json=body)
    assert rejected.status_code == 409


def test_fission_composition_pool_is_independent_from_full_and_replica(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, service, _billing = _client(tmp_path, runtime)
    _create_composition(service, "comp-fission", "v-fission", scope="fission")

    fission = client.get(f"{API}/compositions?scope=fission", headers=HEADERS).json()
    full = client.get(f"{API}/compositions?scope=full", headers=HEADERS).json()
    replica = client.get(f"{API}/compositions?scope=replica", headers=HEADERS).json()

    assert "comp-fission" in {item["composition_id"] for item in fission["templates"]}
    assert "comp-fission" not in {item["composition_id"] for item in full["templates"]}
    assert "comp-fission" not in {item["composition_id"] for item in replica["templates"]}


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
    # 高清直出暂时下线：生成策略统一为 grid（一款一图后本地拆四格）。
    assert plan["generation_strategy"] == "grid"


# --- 计划指纹复用：已复刻产品不再重复调豆包 -------------------------------------


def _stored_fingerprints(service: PodCustomizationService, batch_id: str) -> list[str]:
    with service.repository._connect() as connection:
        rows = connection.execute(
            "SELECT plan_fingerprint FROM pod_customization_replica_targets WHERE batch_id = ?",
            (batch_id,),
        ).fetchall()
    return [row["plan_fingerprint"] for row in rows]


def test_resubmitting_same_input_reuses_frozen_plan_without_doubao(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, service, _billing = _client(tmp_path, runtime)
    source_id = _upload(client, "source", "#dd0000")
    target_id = _upload(client, "target", "#00aa00")
    analysis = _analyze(client, source_id, target_id)

    first = client.post(
        f"{API}/replica/batches",
        headers=HEADERS,
        json=_create_body(source_id, target_id, analysis_id=analysis["analysis_id"], client_request_id="req-first"),
    )
    assert first.status_code == 200, first.text
    assert runtime.composition_calls == 1

    second = client.post(
        f"{API}/replica/batches",
        headers=HEADERS,
        json=_create_body(source_id, target_id, analysis_id=analysis["analysis_id"], client_request_id="req-second"),
    )
    assert second.status_code == 200, second.text
    # 已复刻产品再次提交：直接复用冻结计划，不再调豆包。
    assert runtime.composition_calls == 1

    plan_first = _stored_plan(service, first.json()["id"])
    plan_second = _stored_plan(service, second.json()["id"])
    assert plan_second["composition_plan"] == plan_first["composition_plan"]
    assert plan_second["compiled_prompt"] == plan_first["compiled_prompt"]
    # 复用不等于丢记录：生图模型版本仍随本次提交刷新冻结。
    assert plan_second["image_model_version"]
    assert _stored_fingerprints(service, second.json()["id"]) == _stored_fingerprints(
        service, first.json()["id"]
    )


def test_switching_generation_strategy_reuses_plan_without_doubao(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """换生成方式（普通生成 ↔ 高清直出）不该重调豆包。

    豆包的构图调用入参里没有生成方式，它只影响本地编译的 Prompt：因此同一输入换生成
    方式仍命中同一份冻结计划，只本地重编译 Prompt（零豆包调用）。

    高清直出暂时下线（``REPLICA_PANEL_STRATEGY_ENABLED=False``），这里显式打开开关，
    保住该保证的回归覆盖。
    """
    monkeypatch.setattr(pod_service_module, "REPLICA_PANEL_STRATEGY_ENABLED", True)
    runtime = AnalysisRuntime()
    client, service, _billing = _client(tmp_path, runtime)
    source_id = _upload(client, "source", "#dd0000")
    target_id = _upload(client, "target", "#00aa00")
    analysis = _analyze(client, source_id, target_id)

    def submit(request_id: str, strategy: str) -> dict:
        body = _create_body(
            source_id, target_id, analysis_id=analysis["analysis_id"], client_request_id=request_id
        )
        body["generation_strategy"] = strategy
        response = client.post(f"{API}/replica/batches", headers=HEADERS, json=body)
        assert response.status_code == 200, response.text
        return response.json()

    grid = submit("req-strategy-grid", "grid")
    assert runtime.composition_calls == 1

    panels = submit("req-strategy-panels", "independent_panels")
    # 同输入换生成方式：命中同一份冻结计划，不再调豆包。
    assert runtime.composition_calls == 1
    assert _stored_fingerprints(service, panels["id"]) == _stored_fingerprints(service, grid["id"])

    grid_plan = _stored_plan(service, grid["id"])
    panel_plan = _stored_plan(service, panels["id"])
    # 构图参数原样复用；生成方式与本地编译的 Prompt 按本次选择刷新。
    assert panel_plan["composition_plan"] == grid_plan["composition_plan"]
    assert grid_plan["generation_strategy"] == "grid"
    assert panel_plan["generation_strategy"] == "independent_panels"
    assert panel_plan["compiled_prompt"] != grid_plan["compiled_prompt"]


def test_changed_facts_preferences_or_template_fetch_new_plan(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, service, _billing = _client(tmp_path, runtime)
    source_id = _upload(client, "source", "#dd0000")
    target_id = _upload(client, "target", "#00aa00")
    analysis = _analyze(client, source_id, target_id)

    def submit(request_id: str, *, target_extra: dict | None = None, composition_id: str = "") -> dict:
        body = _create_body(
            source_id, target_id, analysis_id=analysis["analysis_id"], client_request_id=request_id
        )
        if target_extra:
            body["targets"][0].update(target_extra)
        if composition_id:
            body["composition_id"] = composition_id
        response = client.post(f"{API}/replica/batches", headers=HEADERS, json=body)
        assert response.status_code == 200, response.text
        return response.json()

    submit("req-base")
    assert runtime.composition_calls == 1
    submit("req-base-again")
    assert runtime.composition_calls == 1  # 同输入：命中指纹，不重推

    # 改展示偏好 → 新指纹、新计划、重新调豆包。
    preferred = submit("req-pref", target_extra={"presentation_preferences": {"use_white_background": True}})
    assert runtime.composition_calls == 2
    # 改事实 → 新指纹、新计划。
    submit("req-facts", target_extra={"fact_overrides": {"product_name": "珍珠美甲套装"}})
    assert runtime.composition_calls == 3

    # 换构图模板 → 新指纹、新计划。
    _create_composition(service, "comp-alt", "pod-composition-v9")
    templated = submit("req-template", composition_id="comp-alt")
    assert runtime.composition_calls == 4
    submit("req-template-again", composition_id="comp-alt")
    assert runtime.composition_calls == 4

    # 每种输入各冻结自己的计划，互不串用。
    assert _stored_plan(service, preferred["id"])["composition_plan"]["template_id"] == DEFAULT_COMPOSITION_ID
    assert _stored_plan(service, templated["id"])["composition_plan"]["template_id"] == "comp-alt"


def test_same_input_twice_in_one_batch_calls_doubao_once(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, service, _billing = _client(tmp_path, runtime)
    source_id = _upload(client, "source", "#dd0000")
    target_id = _upload(client, "target", "#00aa00")
    analysis = _analyze(client, source_id, target_id)
    target = {
        "target_asset_id": target_id,
        "product_name": "美甲套装",
        "listing_fields": _listing_fields(),
        "analysis_id": analysis["analysis_id"],
    }
    response = client.post(
        f"{API}/replica/batches",
        headers=HEADERS,
        json={
            "client_request_id": "req-dup-targets",
            "source_asset_id": source_id,
            "targets": [target, dict(target)],
        },
    )
    assert response.status_code == 200, response.text
    # 同批次同输入只推一次，另一款直接复用。
    assert runtime.composition_calls == 1
    fingerprints = _stored_fingerprints(service, response.json()["id"])
    assert len(fingerprints) == 2
    assert len(set(fingerprints)) == 1


def test_frozen_plan_is_never_reused_across_users(tmp_path: Path) -> None:
    runtime = AnalysisRuntime()
    client, service, _billing = _client(tmp_path, runtime)
    source_id = _upload(client, "source", "#dd0000")
    target_id = _upload(client, "target", "#00aa00")
    analysis = _analyze(client, source_id, target_id)
    response = client.post(
        f"{API}/replica/batches",
        headers=HEADERS,
        json=_create_body(source_id, target_id, analysis_id=analysis["analysis_id"], client_request_id="req-owner"),
    )
    assert response.status_code == 200, response.text
    (fingerprint,) = _stored_fingerprints(service, response.json()["id"])
    assert fingerprint

    assert service.repository.find_frozen_replica_plan(
        "default", "local-demo-admin", fingerprint
    ) is not None
    # 他人 / 别的工作区绝不复用这份计划；空指纹一律不命中。
    assert service.repository.find_frozen_replica_plan("default", "someone-else", fingerprint) is None
    assert service.repository.find_frozen_replica_plan("other-workspace", "local-demo-admin", fingerprint) is None
    assert service.repository.find_frozen_replica_plan("default", "local-demo-admin", "") is None

