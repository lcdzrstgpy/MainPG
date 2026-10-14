"""爆款复刻领域模型：A/B 事实、能力、规则、构图计划与 ``ReplicaPlan``。

设计见 ``docs/superpowers/specs/2026-10-13-pod-replica-final-architecture-design.md``。

本模块只放 Pydantic 模型与纯标准化函数：不含 IO、不调用 provider、不写库。
标准化函数是「原始豆包 JSON → 可复现事实对象」的唯一入口，保证
「同一份输入 + 同一版本 → 同一份事实对象」；能力匹配、规则推导与构图选择
分别在 ``replica_capabilities`` 与 ``replica_planner`` 中实现。
"""

from __future__ import annotations

from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

# 分析 schema 版本：事实字段结构一变就递增，随分析结果一起冻结入库，用于事后归因。
REPLICA_ANALYSIS_SCHEMA_VERSION = "replica-analysis-v1"
# 低于该置信度的字段需要用户确认（needs_confirmation），不得直接进入硬约束。
FACT_CONFIDENCE_THRESHOLD = 0.6

# 事实来源：模型识别 / 用户覆盖 / 系统补全。
FactSource = Literal["model", "user", "system"]

# 生成策略见设计 §8：grid = 一次 2×2；independent_panels = 四次单面板。
GenerationStrategy = Literal["grid", "independent_panels"]
GENERATION_STRATEGIES: tuple[str, ...] = ("grid", "independent_panels")


class ReplicaPlanNotReadyError(RuntimeError):
    """事实低置信度或冲突，或构图计划非法：必须阻止提交，由用户先确认。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code)
        super().__init__(str(message))


class FactValue(BaseModel):
    """单个事实字段：值 + 置信度 + 来源。

    ``source`` 只允许 model / user / system；``confidence`` 必须在 ``[0, 1]``，
    越界直接抛 ``ValidationError``。只有经过本模块 ``normalize_*`` 标准化、
    且置信度不低于 ``FACT_CONFIDENCE_THRESHOLD``（或已被用户确认）的字段，
    才允许进入规则推导与硬约束。

    刻意不使用 pydantic 参数化泛型（``FactValue[str]`` 会与本体成为不同类，
    导致赋值校验失败）；``value`` 的字段级类型由各 spec 的语义与标准化函数保证。
    """

    model_config = ConfigDict(extra="forbid")

    value: Any = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: FactSource = "model"

    def is_confident(self, threshold: float = FACT_CONFIDENCE_THRESHOLD) -> bool:
        return self.confidence >= threshold


# 各 spec 的 FactValue 字段名（显式列出，避免依赖 pydantic 内部字段 API）。
SOURCE_FACT_FIELDS: tuple[str, ...] = (
    "source_product_name",
    "artwork_motifs",
    "artwork_colors",
    "artwork_layout",
    "artwork_density",
    "artwork_repeat_pattern",
    "artwork_text",
    "artwork_print_region",
    "source_product_structure",
    "source_background_elements",
    "source_people_and_props",
    "source_watermarks_and_labels",
)
TARGET_FACT_FIELDS: tuple[str, ...] = (
    "product_name",
    "product_type",
    "component_count",
    "component_structure",
    "material",
    "surface_texture",
    "printable_regions",
    "non_printable_regions",
    "inner_outer_structure",
    "openings_and_hardware",
    "product_orientation",
    "supported_usage_modes",
    "occlusion_risk",
)


class SourceArtworkSpec(BaseModel):
    """原素材图 A 的事实。

    只描述「需要迁移的图案」与「必须丢弃的环境内容」：图案主体（motifs /
    colors / layout）、图案自带文字，以及需要排除的背景、人物、道具、水印。
    A 中的产品形状、背景、人物默认不迁移到目标产品。
    """

    model_config = ConfigDict(extra="forbid")

    source_asset_id: str = Field(min_length=1, max_length=200)
    source_product_name: FactValue
    artwork_motifs: FactValue
    artwork_colors: FactValue
    artwork_layout: FactValue
    artwork_density: FactValue
    artwork_repeat_pattern: FactValue
    # 图案自带的文字可保留；系统不得新增文字（硬约束在 rule_set 中给出）。
    artwork_text: FactValue
    artwork_print_region: FactValue
    source_product_structure: FactValue
    source_background_elements: FactValue
    source_people_and_props: FactValue
    source_watermarks_and_labels: FactValue
    artwork_extraction_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    analysis_model_version: str = Field(default="", max_length=200)
    analysis_schema_version: str = Field(default=REPLICA_ANALYSIS_SCHEMA_VERSION, max_length=100)

    def fact(self, name: str) -> FactValue:
        if name not in SOURCE_FACT_FIELDS:
            raise KeyError(f"未知的 A 事实字段：{name}")
        return getattr(self, name)

    def low_confidence_fields(self, threshold: float = FACT_CONFIDENCE_THRESHOLD) -> tuple[str, ...]:
        return tuple(
            name for name in SOURCE_FACT_FIELDS if not self.fact(name).is_confident(threshold)
        )

    @property
    def needs_confirmation(self) -> bool:
        return bool(self.low_confidence_fields())


class TargetProductSpec(BaseModel):
    """目标产品 B 的事实：结构、材质、部件、可印/不可印区域与支持的使用方式。"""

    model_config = ConfigDict(extra="forbid")

    target_asset_id: str = Field(min_length=1, max_length=200)
    product_name: FactValue
    product_type: FactValue
    component_count: FactValue
    component_structure: FactValue
    material: FactValue
    surface_texture: FactValue
    printable_regions: FactValue
    non_printable_regions: FactValue
    inner_outer_structure: FactValue
    openings_and_hardware: FactValue
    product_orientation: FactValue
    supported_usage_modes: FactValue
    occlusion_risk: FactValue
    product_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    # 模型给出的候选假设，仅作审计与用户提示，不进入硬约束。
    alternative_hypotheses: tuple[str, ...] = ()
    # 模型建议的能力标签：只能作为候选，必须经注册表校验后才可能生效。
    suggested_capabilities: tuple[str, ...] = ()
    analysis_model_version: str = Field(default="", max_length=200)
    analysis_schema_version: str = Field(default=REPLICA_ANALYSIS_SCHEMA_VERSION, max_length=100)

    def fact(self, name: str) -> FactValue:
        if name not in TARGET_FACT_FIELDS:
            raise KeyError(f"未知的 B 事实字段：{name}")
        return getattr(self, name)

    def low_confidence_fields(self, threshold: float = FACT_CONFIDENCE_THRESHOLD) -> tuple[str, ...]:
        return tuple(
            name for name in TARGET_FACT_FIELDS if not self.fact(name).is_confident(threshold)
        )

    @property
    def needs_confirmation(self) -> bool:
        return bool(self.low_confidence_fields())


class FactOverrides(BaseModel):
    """用户对 B 事实的显式修正；只允许覆盖事实，不得覆盖硬约束。"""

    model_config = ConfigDict(extra="forbid")

    product_name: str | None = Field(default=None, max_length=500)
    product_type: str | None = Field(default=None, max_length=200)
    component_count: int | None = Field(default=None, ge=0, le=1000)
    component_structure: str | None = Field(default=None, max_length=500)
    material: str | None = Field(default=None, max_length=200)
    surface_texture: str | None = Field(default=None, max_length=200)
    printable_regions: tuple[str, ...] | None = None
    non_printable_regions: tuple[str, ...] | None = None
    supported_usage_modes: tuple[str, ...] | None = None


class PresentationPreferences(BaseModel):
    """用户的展示偏好。能力标签 ≠ 必拍画面：展示类能力必须由这里显式选中。"""

    model_config = ConfigDict(extra="forbid")

    use_hand_model: bool = False
    use_body_model: bool = False
    use_wall_display: bool = False
    use_white_background: bool = False
    use_lifestyle_scene: bool = True


class CapabilitySet(BaseModel):
    """本次任务的能力组合。``unknown_capability`` 保存无法映射的模型建议标签。"""

    model_config = ConfigDict(extra="forbid")

    capabilities: tuple[str, ...] = ()
    unknown_capability: tuple[str, ...] = ()
    registry_version: str = Field(default="", max_length=100)


class ReplicaRule(BaseModel):
    """单条确定性规则。"""

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1, max_length=100)
    rule_version: str = Field(min_length=1, max_length=100)
    priority: int = Field(ge=0, le=1000)
    trigger: str = Field(min_length=1, max_length=500)
    effect: str = Field(min_length=1, max_length=500)
    generation_constraints: tuple[str, ...] = ()
    source_reason: str = Field(default="", max_length=500)


class ReplicaRuleSet(BaseModel):
    """规则集合：硬约束 / 展示要求 / 生成约束三类 + 逐条规则明细。"""

    model_config = ConfigDict(extra="forbid")

    hard_constraints: tuple[str, ...] = ()
    display_requirements: tuple[str, ...] = ()
    generation_constraints: tuple[str, ...] = ()
    rules: tuple[ReplicaRule, ...] = ()
    rule_version: str = Field(default="", max_length=100)


class CompositionSlot(BaseModel):
    """一个画面槽位（四格之一或独立面板之一）：角色 + 本次要体现的展示方式。"""

    model_config = ConfigDict(extra="forbid")

    slot_id: str = Field(min_length=1, max_length=50)
    role: str = Field(min_length=1, max_length=100)
    # 结构约束：该画面必须展示全部部件（multi_piece 至少一个槽位为 True）。
    show_all_components: bool = False
    hand_worn: bool = False
    body_worn: bool = False
    wall_mounted: bool = False
    macro_detail: bool = False
    white_background: bool = False
    shot: str = Field(default="", max_length=500)
    camera: str = Field(default="", max_length=200)
    lighting: str = Field(default="", max_length=200)
    background: str = Field(default="", max_length=300)


class CompositionPlan(BaseModel):
    """冻结的摄影构图计划：由全定制构图模板 + 豆包结构化参数 + 用户覆盖组成。"""

    model_config = ConfigDict(extra="forbid")

    template_id: str = Field(default="", max_length=100)
    template_version: str = Field(default="", max_length=100)
    slots: tuple[CompositionSlot, ...] = ()
    camera: str = Field(default="", max_length=200)
    lighting: str = Field(default="", max_length=200)
    background: str = Field(default="", max_length=300)
    composition_notes: str = Field(default="", max_length=1000)
    user_overrides: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_slots(self) -> "CompositionPlan":
        slot_ids = [slot.slot_id for slot in self.slots]
        if len(set(slot_ids)) != len(slot_ids):
            raise ValueError("构图槽位 slot_id 不能重复")
        return self


class ReplicaPlan(BaseModel):
    """冻结的复刻计划：事实 + 能力 + 规则 + 构图计划 + 编译后的 Prompt + 全部版本号。

    同一份分析、能力、规则、用户选择、模板和版本必须得到同一份 ``ReplicaPlan``；
    生成、重试、暂停恢复、进程重启都只复用本对象，不再重新推导。
    """

    model_config = ConfigDict(extra="forbid")

    plan_version: int = Field(default=1, ge=1)
    source_artwork_spec: SourceArtworkSpec
    target_product_spec: TargetProductSpec
    capability_set: CapabilitySet
    rule_set: ReplicaRuleSet
    composition_plan: CompositionPlan | None = None
    generation_strategy: GenerationStrategy = "grid"
    compiled_prompt: str = ""
    # 版本冻结：提交后不得随系统升级而改变。
    analysis_schema_version: str = Field(default=REPLICA_ANALYSIS_SCHEMA_VERSION, max_length=100)
    capability_registry_version: str = Field(default="", max_length=100)
    rule_version: str = Field(default="", max_length=100)
    composition_template_version: str = Field(default="", max_length=100)
    prompt_compiler_version: str = Field(default="", max_length=100)
    image_model_version: str = Field(default="", max_length=200)


# --- 原始分析 JSON 标准化 -------------------------------------------------------


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    return default


def _as_str_list(value: Any, default: Sequence[str] = ()) -> list[str]:
    if value is None:
        return list(default)
    if isinstance(value, str):
        token = value.strip()
        return [token] if token else list(default)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        items = [_as_str(item) for item in value]
        return [item for item in items if item]
    return list(default)


def _as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return default


def _read_fact(
    raw: Mapping[str, Any],
    key: str,
    *,
    kind: str,
    default: Any,
) -> FactValue:
    """读取一个事实字段，兼容 ``{value, confidence, source}`` 与裸值两种形状。

    缺失或显式为 None → 系统补默认值、置信度 0（必然进入 needs_confirmation）。
    越界置信度交给 ``FactValue`` 校验并抛出 ``ValidationError``。
    """
    if not isinstance(raw, Mapping) or raw.get(key) is None:
        return FactValue(value=default, confidence=0.0, source="system")
    entry = raw[key]
    if isinstance(entry, Mapping):
        value = entry.get("value", default)
        confidence = entry.get("confidence", 1.0)
        source = entry.get("source", "model")
    else:
        value = entry
        confidence = 1.0
        source = "model"
    if kind == "str":
        coerced = _as_str(value, default)
    elif kind == "str_list":
        coerced = _as_str_list(value, default)
    elif kind == "int":
        coerced = _as_int(value, default)
    else:  # pragma: no cover - 内部约定
        raise ValueError(f"未知的字段类型：{kind}")
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = 0.0
    return FactValue(value=coerced, confidence=confidence_value, source=source)


def _apply_override(fact: FactValue, value: Any) -> FactValue:
    """用户覆盖：值以用户为准，来源标 user、置信度 1.0。"""
    if value is None:
        return fact
    return FactValue(value=value, confidence=1.0, source="user")


def normalize_source_analysis(raw: Mapping[str, Any] | None, source_asset_id: str) -> SourceArtworkSpec:
    """把豆包对 A 的原始 JSON 标准化为 ``SourceArtworkSpec``。"""
    payload: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    return SourceArtworkSpec(
        source_asset_id=source_asset_id,
        source_product_name=_read_fact(payload, "source_product_name", kind="str", default=""),
        artwork_motifs=_read_fact(payload, "artwork_motifs", kind="str_list", default=[]),
        artwork_colors=_read_fact(payload, "artwork_colors", kind="str_list", default=[]),
        artwork_layout=_read_fact(payload, "artwork_layout", kind="str", default=""),
        artwork_density=_read_fact(payload, "artwork_density", kind="str", default=""),
        artwork_repeat_pattern=_read_fact(payload, "artwork_repeat_pattern", kind="str", default=""),
        artwork_text=_read_fact(payload, "artwork_text", kind="str", default=""),
        artwork_print_region=_read_fact(payload, "artwork_print_region", kind="str", default=""),
        source_product_structure=_read_fact(payload, "source_product_structure", kind="str", default=""),
        source_background_elements=_read_fact(payload, "source_background_elements", kind="str_list", default=[]),
        source_people_and_props=_read_fact(payload, "source_people_and_props", kind="str_list", default=[]),
        source_watermarks_and_labels=_read_fact(payload, "source_watermarks_and_labels", kind="str_list", default=[]),
        artwork_extraction_confidence=_clamp_confidence(payload.get("artwork_extraction_confidence")),
        analysis_model_version=_as_str(payload.get("analysis_model_version")),
        analysis_schema_version=_as_str(
            payload.get("analysis_schema_version"), REPLICA_ANALYSIS_SCHEMA_VERSION
        ),
    )


def normalize_target_analysis(
    raw: Mapping[str, Any] | None,
    target_asset_id: str,
    overrides: FactOverrides | None = None,
) -> TargetProductSpec:
    """把豆包对 B 的原始 JSON 标准化为 ``TargetProductSpec``，并应用用户覆盖项。"""
    payload: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    spec = TargetProductSpec(
        target_asset_id=target_asset_id,
        product_name=_read_fact(payload, "product_name", kind="str", default=""),
        product_type=_read_fact(payload, "product_type", kind="str", default=""),
        component_count=_read_fact(payload, "component_count", kind="int", default=1),
        component_structure=_read_fact(payload, "component_structure", kind="str", default=""),
        material=_read_fact(payload, "material", kind="str", default=""),
        surface_texture=_read_fact(payload, "surface_texture", kind="str", default=""),
        printable_regions=_read_fact(payload, "printable_regions", kind="str_list", default=[]),
        non_printable_regions=_read_fact(payload, "non_printable_regions", kind="str_list", default=[]),
        inner_outer_structure=_read_fact(payload, "inner_outer_structure", kind="str", default=""),
        openings_and_hardware=_read_fact(payload, "openings_and_hardware", kind="str_list", default=[]),
        product_orientation=_read_fact(payload, "product_orientation", kind="str", default=""),
        supported_usage_modes=_read_fact(payload, "supported_usage_modes", kind="str_list", default=[]),
        occlusion_risk=_read_fact(payload, "occlusion_risk", kind="str", default=""),
        product_confidence=_clamp_confidence(payload.get("product_confidence")),
        alternative_hypotheses=tuple(_as_str_list(payload.get("alternative_hypotheses"))),
        suggested_capabilities=tuple(_as_str_list(payload.get("suggested_capabilities"))),
        analysis_model_version=_as_str(payload.get("analysis_model_version")),
        analysis_schema_version=_as_str(
            payload.get("analysis_schema_version"), REPLICA_ANALYSIS_SCHEMA_VERSION
        ),
    )
    if overrides is not None:
        spec = _apply_target_overrides(spec, overrides)
    return spec


def _apply_target_overrides(spec: TargetProductSpec, overrides: FactOverrides) -> TargetProductSpec:
    updates = {
        "product_name": overrides.product_name,
        "product_type": overrides.product_type,
        "component_count": overrides.component_count,
        "component_structure": overrides.component_structure,
        "material": overrides.material,
        "surface_texture": overrides.surface_texture,
        "printable_regions": (
            list(overrides.printable_regions) if overrides.printable_regions is not None else None
        ),
        "non_printable_regions": (
            list(overrides.non_printable_regions) if overrides.non_printable_regions is not None else None
        ),
        "supported_usage_modes": (
            list(overrides.supported_usage_modes)
            if overrides.supported_usage_modes is not None
            else None
        ),
    }
    if not any(value is not None for value in updates.values()):
        return spec
    data = spec.model_dump()
    for name, value in updates.items():
        if value is None:
            continue
        data[name] = _apply_override(getattr(spec, name), value).model_dump()
    return TargetProductSpec.model_validate(data)


def _clamp_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, number))
