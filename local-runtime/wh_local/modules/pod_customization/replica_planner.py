"""确定性规则推导、默认展示槽位与计划就绪校验（设计 §3.4 / §5）。

本模块不调用任何模型：规则、展示要求与槽位完全由「标准化事实 + 能力 + 用户偏好」
推导得出。同一份输入与同一版本必然得到同一份规则集合。
"""

from __future__ import annotations

from .replica_capabilities import CAPABILITY_REGISTRY, CAPABILITY_REGISTRY_VERSION
from .replica_specs import (
    CompositionPlan,
    CompositionSlot,
    PresentationPreferences,
    ReplicaPlanNotReadyError,
    ReplicaRule,
    ReplicaRuleSet,
    SourceArtworkSpec,
    TargetProductSpec,
)

RULE_VERSION = "replica-rules-v1"

# 硬约束（设计 §3.4）：违反后结果不可接受。
HARD_CONSTRAINTS: tuple[str, ...] = (
    "只能复制原素材图 A 的图案内容（主体、配色、排布关系）。",
    "不能复制 A 的背景、人物、家具、道具、标签和水印。",
    "不能新增图案元素、颜色、文字、Logo 或贴纸。",
    "必须保留目标产品 B 的轮廓、结构、材质和部件数量。",
    "图案只能出现在 B 声明的可印区域。",
    "图案自带文字可以保留，但不得新增任何文字。",
)
# 图案文字规则：允许保留、禁止新增（与 test_replica_prompts 的语义一致）。
ALLOW_PATTERN_TEXT_CONSTRAINT = "Text that is already part of the pattern itself may stay; never add new text."

# 固定四格槽位角色（与全定制构图模板的四格语义对齐）。
DEFAULT_SLOT_ROLES: tuple[tuple[str, str], ...] = (
    ("panel_1", "primary_image"),
    ("panel_2", "pattern_detail"),
    ("panel_3", "structure_or_usage"),
    ("panel_4", "material_image"),
)
# 允许承载"佩戴/使用"关系的槽位。
WORN_SLOT_ID = "panel_3"
# 承载白底素材的槽位。
WHITE_BACKGROUND_SLOT_ID = "panel_4"

_MATERIAL_CONSTRAINT_TEXT = {
    "fabric_material": "保留织纹、缝线与自然垂坠，不得呈现为光滑塑料或喷绘表面。",
    "paper_material": "保留纸张纤维与平整边缘，不得呈现为布料或塑料质感。",
    "plastic_material": "保留塑料光泽与硬边轮廓，不得呈现为布料质感。",
    "metal_material": "保留金属反光与拉丝质感，不得呈现为哑光布料质感。",
    "gel_material": "保留凝胶光泽、边缘与表面质感，不得呈现为哑光布面或塑料壳。",
}


def _rule(
    rule_id: str,
    *,
    priority: int,
    trigger: str,
    effect: str,
    generation_constraints: tuple[str, ...] = (),
    source_reason: str = "",
) -> ReplicaRule:
    return ReplicaRule(
        rule_id=rule_id,
        rule_version=RULE_VERSION,
        priority=priority,
        trigger=trigger,
        effect=effect,
        generation_constraints=generation_constraints,
        source_reason=source_reason,
    )


def _print_region_text(target: TargetProductSpec) -> tuple[str, tuple[str, ...]]:
    regions = [str(item) for item in (target.printable_regions.value or []) if str(item).strip()]
    forbidden = [str(item) for item in (target.non_printable_regions.value or []) if str(item).strip()]
    printable = "、".join(regions) or "未声明"
    text = f"图案只能出现在可印区域（{printable}）"
    if forbidden:
        text += f"，不得覆盖不可印区域（{'、'.join(forbidden)}）"
    text += "。"
    return text, tuple([text])


def derive_rules(
    source: SourceArtworkSpec,
    target: TargetProductSpec,
    capabilities,
    preferences: PresentationPreferences,
) -> ReplicaRuleSet:
    """由事实、能力与用户偏好确定性推导规则集合。"""
    capability_ids = tuple(capabilities.capabilities)
    rules: list[ReplicaRule] = []
    display_requirements: list[str] = []
    generation_constraints: list[str] = []

    # 结构要求：multi_piece 至少一个画面展示全部部件。
    if "multi_piece" in capability_ids:
        requirement = "至少一个画面必须展示全部部件，不得省略任一部件。"
        display_requirements.append(requirement)
        constraint = "生成时必须完整呈现全部部件，不得主动省略或只画部分部件。"
        generation_constraints.append(constraint)
        rules.append(
            _rule(
                "replica_multi_piece",
                priority=80,
                trigger=f"component_count={int(target.component_count.value or 0)}>1",
                effect="启用 multi_piece，要求完整部件展示",
                generation_constraints=(constraint,),
                source_reason="部件数量大于 1，套装部件不得遗漏",
            )
        )

    # 印刷能力：把实际可印/不可印区域写成硬约束。
    for capability_id in ("flat_print", "local_print", "surface_wrap"):
        if capability_id in capability_ids:
            text, constraints = _print_region_text(target)
            generation_constraints.extend(constraints)
            rules.append(
                _rule(
                    f"replica_{capability_id}",
                    priority=70,
                    trigger=f"capability={capability_id}",
                    effect=text,
                    generation_constraints=constraints,
                    source_reason=f"印刷能力 {capability_id} 触发区域约束",
                )
            )

    # 材质能力：逐条生成材质保真约束。
    for capability_id, text in _MATERIAL_CONSTRAINT_TEXT.items():
        if capability_id in capability_ids:
            generation_constraints.append(text)
            rules.append(
                _rule(
                    f"replica_{capability_id}",
                    priority=50,
                    trigger=f"material capability={capability_id}",
                    effect=text,
                    generation_constraints=(text,),
                    source_reason=f"材质能力 {capability_id} 触发材质保真约束",
                )
            )

    # 展示类能力：只有在用户/模板选中后才进入能力集，因此可直接生成对应要求。
    if "hand_worn" in capability_ids:
        requirement = "对应画面必须体现手部佩戴关系。"
        display_requirements.append(requirement)
        constraint = "体现正确的手部关系与佩戴关系。"
        generation_constraints.append(constraint)
        rules.append(
            _rule(
                "replica_hand_worn",
                priority=60,
                trigger="supported_usage_modes 含手部使用且用户偏好开启手模",
                effect="本次构图选用手部佩戴展示",
                generation_constraints=(constraint,),
                source_reason="用户显式选择手部展示",
            )
        )
    if "body_worn" in capability_ids:
        requirement = "对应画面必须体现人体穿着关系。"
        display_requirements.append(requirement)
        constraint = "体现正确的穿着关系与自然垂坠。"
        generation_constraints.append(constraint)
        rules.append(
            _rule(
                "replica_body_worn",
                priority=60,
                trigger="supported_usage_modes 含人体穿着且用户偏好开启模特",
                effect="本次构图选用人体穿着展示",
                generation_constraints=(constraint,),
                source_reason="用户显式选择人体展示",
            )
        )
    if "wall_mounted" in capability_ids:
        requirement = "对应画面必须体现墙面悬挂展示。"
        display_requirements.append(requirement)
        constraint = "产品处于墙面悬挂展示状态。"
        generation_constraints.append(constraint)
        rules.append(
            _rule(
                "replica_wall_mounted",
                priority=60,
                trigger="supported_usage_modes 含墙面悬挂且用户偏好开启墙面展示",
                effect="本次构图选用墙面展示",
                generation_constraints=(constraint,),
                source_reason="用户显式选择墙面展示",
            )
        )
    if "macro_detail" in capability_ids:
        constraint = "保留材质光泽、边缘与表面质感（微距细节）。"
        generation_constraints.append(constraint)
        rules.append(
            _rule(
                "replica_macro_detail",
                priority=55,
                trigger="小件/多部件产品需要微距呈现",
                effect="本次构图包含材质或图案微距画面",
                generation_constraints=(constraint,),
                source_reason="能力集含 macro_detail",
            )
        )
    if "white_background" in capability_ids:
        constraint = "提供干净中性纯白底、无道具的完整产品素材图。"
        generation_constraints.append(constraint)
        rules.append(
            _rule(
                "replica_white_background",
                priority=40,
                trigger="用户偏好要求白底素材",
                effect="本次构图包含白底素材画面",
                generation_constraints=(constraint,),
                source_reason="用户显式选择白底素材",
            )
        )

    generation_constraints.append(ALLOW_PATTERN_TEXT_CONSTRAINT)
    rules.append(
        _rule(
            "replica_pattern_text",
            priority=90,
            trigger="固定硬约束",
            effect="图案自带文字可保留，禁止新增文字",
            generation_constraints=(ALLOW_PATTERN_TEXT_CONSTRAINT,),
            source_reason="复刻不新增文字",
        )
    )

    return ReplicaRuleSet(
        hard_constraints=tuple(HARD_CONSTRAINTS),
        display_requirements=tuple(display_requirements),
        generation_constraints=tuple(generation_constraints),
        rules=tuple(rules),
        rule_version=RULE_VERSION,
    )


def build_default_composition_slots(
    target: TargetProductSpec,
    capabilities,
    preferences: PresentationPreferences,
) -> tuple[CompositionSlot, ...]:
    """按能力与偏好生成默认四格槽位；能力标签不等于必拍画面，只有被选中的才落位。"""
    capability_ids = tuple(capabilities.capabilities)
    slots: list[CompositionSlot] = []
    for slot_id, role in DEFAULT_SLOT_ROLES:
        slot = CompositionSlot(slot_id=slot_id, role=role)
        if "multi_piece" in capability_ids and slot_id == "panel_1":
            slot.show_all_components = True
        if "macro_detail" in capability_ids and slot_id == "panel_2":
            slot.macro_detail = True
        if slot_id == WORN_SLOT_ID:
            if "hand_worn" in capability_ids:
                slot.hand_worn = True
            elif "body_worn" in capability_ids:
                slot.body_worn = True
            elif "wall_mounted" in capability_ids:
                slot.wall_mounted = True
        if "white_background" in capability_ids and slot_id == WHITE_BACKGROUND_SLOT_ID:
            slot.white_background = True
        slots.append(slot)
    return tuple(slots)


def detect_fact_conflicts(target: TargetProductSpec) -> tuple[str, ...]:
    """检出阻塞性事实冲突（不自动生成，要求用户修正）。"""
    conflicts: list[str] = []
    component_count = int(target.component_count.value or 0)
    if component_count > 1 and not str(target.component_structure.value or "").strip():
        conflicts.append("multi_piece_without_structure")
    regions = {str(item).strip().lower() for item in (target.printable_regions.value or []) if str(item).strip()}
    forbidden = {str(item).strip().lower() for item in (target.non_printable_regions.value or []) if str(item).strip()}
    if regions & forbidden:
        conflicts.append("print_region_overlap")
    return tuple(conflicts)


def validate_plan_ready(
    source: SourceArtworkSpec,
    target: TargetProductSpec,
    capabilities,
    preferences: PresentationPreferences,
) -> None:
    """提交门禁：低置信度或事实冲突时阻止提交，交由用户确认/修正。"""
    low_confidence = target.low_confidence_fields()
    if low_confidence:
        raise ReplicaPlanNotReadyError(
            "needs_confirmation",
            "以下目标产品事实置信度不足，需用户确认：" + "、".join(low_confidence),
        )
    conflicts = detect_fact_conflicts(target)
    if conflicts:
        raise ReplicaPlanNotReadyError(
            "fact_conflict",
            "目标产品事实存在冲突，需用户修正：" + "、".join(conflicts),
        )


def validate_composition_plan(plan: CompositionPlan, capabilities) -> None:
    """校验构图计划与能力集一致：未选中的展示能力不得出现在任何槽位。"""
    capability_ids = tuple(capabilities.capabilities)
    for slot in plan.slots:
        for flag, capability in (
            ("hand_worn", "hand_worn"),
            ("body_worn", "body_worn"),
            ("wall_mounted", "wall_mounted"),
            ("white_background", "white_background"),
        ):
            if getattr(slot, flag) and capability not in capability_ids:
                raise ReplicaPlanNotReadyError(
                    "composition_capability_mismatch",
                    f"槽位 {slot.slot_id} 使用了未选中的能力：{capability}",
                )
    if "multi_piece" in capability_ids and not any(slot.show_all_components for slot in plan.slots):
        raise ReplicaPlanNotReadyError(
            "missing_full_component_slot",
            "multi_piece 要求至少一个槽位展示全部部件",
        )


def registry_version() -> str:
    return CAPABILITY_REGISTRY_VERSION


def capability_known(capability_id: str) -> bool:
    return capability_id in CAPABILITY_REGISTRY
