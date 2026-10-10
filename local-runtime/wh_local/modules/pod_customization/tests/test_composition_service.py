"""构图/视角定制：服务层「新增模板 / 手动编辑 / 生效 / 重命名 / 删除」与提示词注入测试。

全程使用假 billing coordinator 与假 runtime，不触发任何真实付费调用。
"""

from __future__ import annotations

import pathlib

import pytest

from wh_local.modules.pod_customization.billing_contract import PodExecutionGrant
from wh_local.modules.pod_customization.composition_runtime import PodCompositionResult
from wh_local.modules.pod_customization.contracts import (
    BusinessFields,
    CompositionPanels,
    CompositionPanelsZh,
    CompositionRenameRequest,
    CompositionRequest,
    CompositionUpdateRequest,
)
from wh_local.modules.pod_customization.prompts import DEFAULT_COMPOSITION_ID, build_direct_listing_prompt
from wh_local.modules.pod_customization.repository import PodRepositoryError
from wh_local.modules.pod_customization.service import PodCustomizationService
from wh_local.session import Actor


def _actor() -> Actor:
    return Actor(id="designer-1", username="designer", role="admin", workspace_id="workspace-a")


class _Coordinator:
    def __init__(self) -> None:
        self.freezes: list[object] = []
        self.settlements: list[tuple[object, tuple]] = []

    def freeze(self, _actor, plan):
        self.freezes.append(plan)
        return PodExecutionGrant("freeze-1", 1, "2099-01-01T00:00:00Z", {"ark": "test-ark-key"})

    def settle(self, _actor, _grant, plan, outcomes):
        self.settlements.append((plan, tuple(outcomes)))


_PANELS = {
    "panel_1": {"zh": "主图：桌面俯拍平铺", "en": "flat-lay hero on a wooden table"},
    "panel_2": {"zh": "细节图 A：图案微距", "en": "macro close-up of the surface artwork"},
    "panel_3": {"zh": "细节图 B：侧面结构", "en": "three-quarter structural detail"},
    "panel_4": {"zh": "素材图：正面纯白底", "en": "front view on a plain neutral background"},
}
_EN = {key: value["en"] for key, value in _PANELS.items()}


class _CompositionRuntime:
    def __init__(self) -> None:
        self.requests: list[object] = []
        self.localize_requests: list[object] = []

    def generate_composition(self, request, *, grant, call_id, call_ids=None, on_start=None, on_outcome=None):
        self.requests.append(request)
        if on_start is not None:
            on_start(call_id)
        if on_outcome is not None:
            on_outcome(call_id, "success")
        return PodCompositionResult(
            panels=CompositionPanels(**{k: dict(v) for k, v in _PANELS.items()}),
            attempt_count=1,
            model="doubao-test",
            prompt_version="pod-composition-v2",
        )

    def localize_composition(self, request, *, grant, call_id, call_ids=None, on_start=None, on_outcome=None):
        self.localize_requests.append(request)
        if on_start is not None:
            on_start(call_id)
        if on_outcome is not None:
            on_outcome(call_id, "success")
        # zh 原样回填用户编辑内容，en 由「模型」重新给出。
        panels = {key: {"zh": request.panels[key], "en": _EN[key]} for key in _PANELS}
        return PodCompositionResult(
            panels=CompositionPanels(**panels),
            attempt_count=1,
            model="doubao-test",
            prompt_version="pod-composition-v2",
        )


def _service(tmp_path: pathlib.Path, runtime: _CompositionRuntime, coordinator: _Coordinator) -> PodCustomizationService:
    return PodCustomizationService(
        tmp_path / "workbench.sqlite3",
        tmp_path / "pod-assets",
        object(),
        composition_runtime=runtime,
        billing_coordinator=coordinator,
        start_workers=False,
    )


def _edited_panels() -> CompositionPanelsZh:
    return CompositionPanelsZh(
        panel_1="主图：大理石台面平铺",
        panel_2="细节图 A：图案微距",
        panel_3="细节图 B：侧面结构",
        panel_4="素材图：正面纯白底",
    )


def test_generate_creates_active_template_and_stays_free(tmp_path: pathlib.Path) -> None:
    coordinator = _Coordinator()
    service = _service(tmp_path, _CompositionRuntime(), coordinator)
    actor = _actor()

    saved = service.generate_composition(actor, CompositionRequest(brief="四张图想这样拍"))

    assert saved["panels"]["panel_1"]["zh"] == _PANELS["panel_1"]["zh"]
    assert saved["panels"]["panel_1"]["en"] == _PANELS["panel_1"]["en"]
    assert saved["is_active"] is True
    assert saved["name"].startswith("四张图")
    assert service.get_active_composition(actor)["composition_id"] == saved["composition_id"]
    assert service.list_compositions(actor)["total"] == 2  # 内置默认模板 + 本次新增

    # 免费的关键：冻结计划只含 pod.title（服务端纯 title scope 显式零计费）。
    assert len(coordinator.freezes) == 1
    assert {call.feature for call in coordinator.freezes[0].calls} == {"pod.title"}
    assert len(coordinator.settlements) == 1


def test_generate_twice_accumulates_templates_and_newest_is_active(tmp_path: pathlib.Path) -> None:
    service = _service(tmp_path, _CompositionRuntime(), _Coordinator())
    actor = _actor()

    first = service.generate_composition(actor, CompositionRequest(brief="第一份描述"))
    second = service.generate_composition(actor, CompositionRequest(brief="第二份描述"))

    listing = service.list_compositions(actor)
    assert listing["total"] == 3  # 内置默认模板 + 两份用户模板
    assert listing["templates"][0]["is_builtin"] is True
    users = [item for item in listing["templates"] if not item["is_builtin"]]
    assert {item["composition_id"] for item in users} == {
        first["composition_id"], second["composition_id"],
    }
    # 生效的排最前，且只有新生成的那份是生效的。
    assert users[0]["composition_id"] == second["composition_id"]
    active_ids = [item["composition_id"] for item in users if item["is_active"]]
    assert active_ids == [second["composition_id"]]
    assert service.get_active_composition(actor)["composition_id"] == second["composition_id"]


def test_save_composition_updates_same_record_and_re_localizes(tmp_path: pathlib.Path) -> None:
    runtime = _CompositionRuntime()
    service = _service(tmp_path, runtime, _Coordinator())
    actor = _actor()
    created = service.generate_composition(actor, CompositionRequest(brief="原始描述"))

    saved = service.save_composition(
        actor, created["composition_id"], CompositionUpdateRequest(panels=_edited_panels())
    )

    # 改原记录，不新增。
    assert saved["composition_id"] == created["composition_id"]
    assert service.list_compositions(actor)["total"] == 2  # 内置默认模板 + 这一份
    assert saved["panels"]["panel_1"]["zh"] == "主图：大理石台面平铺"
    assert saved["panels"]["panel_1"]["en"] == _PANELS["panel_1"]["en"]
    # 原话与名字都保留。
    assert saved["raw_input"] == "原始描述"
    assert saved["name"] == created["name"]
    assert len(runtime.localize_requests) == 1


def test_activate_rename_and_delete_composition(tmp_path: pathlib.Path) -> None:
    service = _service(tmp_path, _CompositionRuntime(), _Coordinator())
    actor = _actor()
    first = service.generate_composition(actor, CompositionRequest(brief="甲"))
    second = service.generate_composition(actor, CompositionRequest(brief="乙"))

    switched = service.activate_composition(actor, first["composition_id"])
    assert switched["is_active"] is True
    assert service.get_active_composition(actor)["composition_id"] == first["composition_id"]

    renamed = service.rename_composition(
        actor, first["composition_id"], CompositionRenameRequest(name="甲方案")
    )
    assert renamed["name"] == "甲方案"

    assert service.delete_composition(actor, second["composition_id"]) == {"deleted": True}
    assert service.list_compositions(actor)["total"] == 2  # 内置默认模板 + 剩下的一份


def test_save_or_delete_missing_composition_returns_404(tmp_path: pathlib.Path) -> None:
    service = _service(tmp_path, _CompositionRuntime(), _Coordinator())
    actor = _actor()

    with pytest.raises(PodRepositoryError) as save_error:
        service.save_composition(actor, "missing", CompositionUpdateRequest(panels=_edited_panels()))
    assert save_error.value.status_code == 404

    with pytest.raises(PodRepositoryError) as delete_error:
        service.delete_composition(actor, "missing")
    assert delete_error.value.status_code == 404


def test_list_compositions_always_lists_builtin_default_first(tmp_path: pathlib.Path) -> None:
    service = _service(tmp_path, _CompositionRuntime(), _Coordinator())
    actor = _actor()

    listing = service.list_compositions(actor)

    assert listing["total"] == 1
    seeded = listing["templates"][0]
    assert seeded["name"] == "默认模板"
    assert seeded["is_builtin"] is True
    assert seeded["is_active"] is True  # 没有用户模板生效时，回退默认模板
    assert seeded["panels"]["panel_1"]["zh"]


def test_builtin_default_falls_back_when_a_user_template_is_active(tmp_path: pathlib.Path) -> None:
    service = _service(tmp_path, _CompositionRuntime(), _Coordinator())
    actor = _actor()
    created = service.generate_composition(actor, CompositionRequest(brief="甲"))

    listing = service.list_compositions(actor)

    assert listing["total"] == 2
    assert listing["templates"][0]["is_builtin"] is True
    assert listing["templates"][0]["is_active"] is False
    assert listing["templates"][1]["composition_id"] == created["composition_id"]
    assert listing["templates"][1]["is_active"] is True


def test_activating_builtin_default_clears_the_user_active_template(tmp_path: pathlib.Path) -> None:
    service = _service(tmp_path, _CompositionRuntime(), _Coordinator())
    actor = _actor()
    created = service.generate_composition(actor, CompositionRequest(brief="甲"))

    activated = service.activate_composition(actor, DEFAULT_COMPOSITION_ID)

    assert activated["is_builtin"] is True and activated["is_active"] is True
    # 回退默认：没有用户模板生效，新建批次走默认机位。
    assert service.get_active_composition(actor) is None
    listing = service.list_compositions(actor)
    assert listing["templates"][0]["is_active"] is True
    assert all(not t["is_active"] for t in listing["templates"] if t["composition_id"] == created["composition_id"])


def test_build_direct_listing_prompt_injects_english_and_keeps_hard_constraints() -> None:
    fields = BusinessFields(product_name="Tote bag", product_category="bags")

    with_composition = build_direct_listing_prompt(fields, "", composition=_EN)

    # 用户指令以「最高优先级」的口径注入，并带上角色/位置说明。
    assert "USER-SPECIFIED SHOOTING DIRECTIONS" in with_composition
    assert (
        "Panel 1 (top-left — role: primary image; user direction): flat-lay hero on a wooden table"
        in with_composition
    )
    assert (
        "Panel 4 (bottom-right — role: material image; user direction): front view on a plain neutral background"
        in with_composition
    )
    # 四格趋同是实测暴露的致命失败模式：必须显式下「四格机位必须互不相同」的硬条款。
    assert "MANDATORY DIFFERENCE CHECK" in with_composition
    assert "Four identical viewpoints is a failed result" in with_composition
    # 「禁止沿用模板背景」必须配一条反向澄清，否则模型会把背景做成空白棚拍（竞品观感差距主因）。
    assert "It does NOT mean the background may be plain" in with_composition
    assert "Standalone scene props beside the product ARE required" in with_composition
    # 鲜艳度约束必须在：否则暗色板会出成灰冷发素的图。
    assert "COLOR IMPACT" in with_composition
    assert "never dull, grey or washed out" in with_composition
    # 面料真实感必须在：否则会出成"喷漆在光滑塑料壳上"的劣质包。
    assert "TEXTILE REALISM" in with_composition
    assert "spray-painted surface" in with_composition
    # 绗缝/填充是"结构"不是"装饰"，必须明确豁免——否则模型会把绗缝抹平成光滑壳（实测踩过）。
    assert "CARVE-OUT" in with_composition
    assert "do not flatten the quilted surface into a smooth shell" in with_composition
    # 硬约束仍然保留：同产品同图案、禁文字/品牌、内饰不印。
    assert "Keep the same exact product across all four panels" in with_composition
    assert "Do not invent another product" in with_composition
    assert "interior surface unprinted" in with_composition
    assert "Panel positions and roles stay fixed" in with_composition


def test_build_direct_listing_prompt_without_composition_uses_default_panel_roles() -> None:
    fields = BusinessFields(product_name="Tote bag", product_category="bags")

    prompt = build_direct_listing_prompt(fields, "")

    assert "Panel 1 — PRIMARY IMAGE (top-left)" in prompt
    assert "Panel 4 — MATERIAL IMAGE (bottom-right)" in prompt
    assert "Final check on the fixed order by position" in prompt
