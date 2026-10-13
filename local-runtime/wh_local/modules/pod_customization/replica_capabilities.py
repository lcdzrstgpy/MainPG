"""复刻能力注册表与事实 → 能力匹配。

能力是系统支持的**通用处理/展示动作**，不是产品类别名称（设计 §3.3）。
本模块把「标准化事实 + 用户展示偏好」确定性地映射为一个 ``CapabilitySet``：
纯函数、无 IO、无随机，同一输入必然得到同一输出。

豆包返回的建议标签不是系统能力：无法映射的标签只进入 ``unknown_capability``，
可映射但不被事实/偏好支持的标签一律丢弃，绝不直接写入能力数组。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .replica_specs import CapabilitySet, PresentationPreferences, TargetProductSpec

# 能力注册表版本：任何能力定义增删或语义调整都必须递增。
CAPABILITY_REGISTRY_VERSION = "replica-capabilities-v1"

# 初始能力集合（设计 §3.3），顺序即优先级：越靠前越"强"，冲突时保留靠前者。
CAPABILITY_IDS: tuple[str, ...] = (
    "surface_wrap",
    "flat_print",
    "local_print",
    "multi_piece",
    "hand_worn",
    "body_worn",
    "wall_mounted",
    "standalone_display",
    "macro_detail",
    "white_background",
    "fabric_material",
    "paper_material",
    "plastic_material",
    "metal_material",
    "gel_material",
)

# 互斥能力：同一产品不会同时具备两者。
_INCOMPATIBLE_PAIRS: tuple[tuple[str, str], ...] = (
    ("surface_wrap", "flat_print"),
    ("surface_wrap", "local_print"),
    ("hand_worn", "wall_mounted"),
    ("body_worn", "wall_mounted"),
    ("hand_worn", "body_worn"),
)


@dataclass(frozen=True)
class CapabilityDefinition:
    """单个能力的定义：触发条件、生成约束与适用边界。"""

    capability_id: str
    capability_type: str
    required_facts: tuple[str, ...]
    optional_facts: tuple[str, ...]
    activation_conditions: tuple[str, ...]
    generated_constraints: tuple[str, ...]
    forbidden_conditions: tuple[str, ...]
    compatible_templates: tuple[str, ...]
    incompatible_capabilities: tuple[str, ...]
    generation_strategy: str
    prompt_slots: tuple[str, ...]
    version: str


def _defn(
    capability_id: str,
    capability_type: str,
    *,
    required_facts: tuple[str, ...],
    optional_facts: tuple[str, ...],
    activation_conditions: tuple[str, ...],
    generated_constraints: tuple[str, ...],
    forbidden_conditions: tuple[str, ...],
    compatible_templates: tuple[str, ...],
    incompatible_capabilities: tuple[str, ...],
    generation_strategy: str,
    prompt_slots: tuple[str, ...],
) -> CapabilityDefinition:
    return CapabilityDefinition(
        capability_id=capability_id,
        capability_type=capability_type,
        required_facts=required_facts,
        optional_facts=optional_facts,
        activation_conditions=activation_conditions,
        generated_constraints=generated_constraints,
        forbidden_conditions=forbidden_conditions,
        compatible_templates=compatible_templates,
        incompatible_capabilities=incompatible_capabilities,
        generation_strategy=generation_strategy,
        prompt_slots=prompt_slots,
        version=CAPABILITY_REGISTRY_VERSION,
    )


CAPABILITY_REGISTRY: Mapping[str, CapabilityDefinition] = MappingProxyType(
    {
        "surface_wrap": _defn(
            "surface_wrap",
            "印刷能力",
            required_facts=("printable_regions", "surface_texture"),
            optional_facts=("material",),
            activation_conditions=("可印区域为曲面/环绕表面",),
            generated_constraints=("图案随曲面包裹，保持透视与缩放一致",),
            forbidden_conditions=("不得把曲面图案平铺到正反面两处",),
            compatible_templates=("ecommerce_standard_v1",),
            incompatible_capabilities=("flat_print", "local_print"),
            generation_strategy="grid",
            prompt_slots=("printable_regions",),
        ),
        "flat_print": _defn(
            "flat_print",
            "印刷能力",
            required_facts=("printable_regions",),
            optional_facts=("material", "surface_texture"),
            activation_conditions=("可印区域覆盖整个平面",),
            generated_constraints=("图案覆盖整块平面，边缘到边缘对齐",),
            forbidden_conditions=("不得越过平面边界或变形拉伸",),
            compatible_templates=("ecommerce_standard_v1",),
            incompatible_capabilities=("surface_wrap",),
            generation_strategy="grid",
            prompt_slots=("printable_regions",),
        ),
        "local_print": _defn(
            "local_print",
            "印刷能力",
            required_facts=("printable_regions", "non_printable_regions"),
            optional_facts=("material",),
            activation_conditions=("存在明确的局部可印区域",),
            generated_constraints=("图案只覆盖声明的可印区域",),
            forbidden_conditions=("不能覆盖缝线、五金、内衬和其他不可印区域",),
            compatible_templates=("ecommerce_standard_v1",),
            incompatible_capabilities=("surface_wrap",),
            generation_strategy="grid",
            prompt_slots=("printable_regions", "non_printable_regions"),
        ),
        "multi_piece": _defn(
            "multi_piece",
            "结构要求",
            required_facts=("component_count",),
            optional_facts=("component_structure",),
            activation_conditions=("部件数量大于 1",),
            generated_constraints=("至少一个画面展示全部部件，不得省略任一部件",),
            forbidden_conditions=("不得只展示部分部件",),
            compatible_templates=("ecommerce_multi_piece_v1", "ecommerce_standard_v1"),
            incompatible_capabilities=(),
            generation_strategy="independent_panels",
            prompt_slots=("all_components",),
        ),
        "hand_worn": _defn(
            "hand_worn",
            "展示能力",
            required_facts=("supported_usage_modes",),
            optional_facts=("product_orientation",),
            activation_conditions=("产品支持手部使用，且用户/模板选择手部展示",),
            generated_constraints=("体现正确的手部关系与佩戴关系",),
            forbidden_conditions=("偏好未选中手部展示时不得出现手部",),
            compatible_templates=("ecommerce_wearable_v1",),
            incompatible_capabilities=("wall_mounted", "body_worn"),
            generation_strategy="independent_panels",
            prompt_slots=("worn_context",),
        ),
        "body_worn": _defn(
            "body_worn",
            "展示能力",
            required_facts=("supported_usage_modes",),
            optional_facts=("product_orientation",),
            activation_conditions=("产品支持人体穿着，且用户/模板选择人体展示",),
            generated_constraints=("体现正确的穿着关系与自然垂坠",),
            forbidden_conditions=("偏好未选中人体展示时不得出现人体模特",),
            compatible_templates=("ecommerce_wearable_v1",),
            incompatible_capabilities=("wall_mounted", "hand_worn"),
            generation_strategy="independent_panels",
            prompt_slots=("worn_context",),
        ),
        "wall_mounted": _defn(
            "wall_mounted",
            "展示能力",
            required_facts=("supported_usage_modes",),
            optional_facts=("product_orientation",),
            activation_conditions=("产品支持墙面悬挂，且用户/模板选择墙面展示",),
            generated_constraints=("产品处于墙面悬挂展示状态",),
            forbidden_conditions=("偏好未选中墙面展示时不得出现墙面场景",),
            compatible_templates=("ecommerce_standard_v1",),
            incompatible_capabilities=("hand_worn", "body_worn"),
            generation_strategy="independent_panels",
            prompt_slots=("wall_context",),
        ),
        "standalone_display": _defn(
            "standalone_display",
            "展示能力",
            required_facts=("product_orientation",),
            optional_facts=("occlusion_risk",),
            activation_conditions=("产品可独立摆放（非纯佩戴类产品）",),
            generated_constraints=("产品完整可辨、无遮挡地独立摆放",),
            forbidden_conditions=("不得让产品被道具遮挡主体",),
            compatible_templates=("ecommerce_standard_v1",),
            incompatible_capabilities=(),
            generation_strategy="grid",
            prompt_slots=("display_context",),
        ),
        "macro_detail": _defn(
            "macro_detail",
            "展示能力",
            required_facts=("surface_texture",),
            optional_facts=("material", "component_structure"),
            activation_conditions=("小件或多部件产品，需要微距呈现材质与图案",),
            generated_constraints=("保留材质光泽、边缘与表面质感",),
            forbidden_conditions=("微距画面不得改变图案或材质",),
            compatible_templates=("ecommerce_multi_piece_v1", "ecommerce_standard_v1"),
            incompatible_capabilities=(),
            generation_strategy="independent_panels",
            prompt_slots=("macro_context",),
        ),
        "white_background": _defn(
            "white_background",
            "展示能力",
            required_facts=(),
            optional_facts=("product_orientation",),
            activation_conditions=("用户/模板要求提供白底素材",),
            generated_constraints=("提供干净中性纯白底、无道具的完整产品素材图",),
            forbidden_conditions=("白底素材图不得出现场景道具",),
            compatible_templates=("ecommerce_standard_v1",),
            incompatible_capabilities=(),
            generation_strategy="grid",
            prompt_slots=("white_background",),
        ),
        "fabric_material": _defn(
            "fabric_material",
            "材质能力",
            required_facts=("material",),
            optional_facts=("surface_texture",),
            activation_conditions=("材质为布料",),
            generated_constraints=("保留织纹、缝线与自然垂坠",),
            forbidden_conditions=("不得呈现为光滑塑料或喷绘表面",),
            compatible_templates=("ecommerce_standard_v1",),
            incompatible_capabilities=(),
            generation_strategy="grid",
            prompt_slots=("material",),
        ),
        "paper_material": _defn(
            "paper_material",
            "材质能力",
            required_facts=("material",),
            optional_facts=("surface_texture",),
            activation_conditions=("材质为纸张/卡纸",),
            generated_constraints=("保留纸张纤维与平整边缘",),
            forbidden_conditions=("不得呈现为布料或塑料质感",),
            compatible_templates=("ecommerce_standard_v1",),
            incompatible_capabilities=(),
            generation_strategy="grid",
            prompt_slots=("material",),
        ),
        "plastic_material": _defn(
            "plastic_material",
            "材质能力",
            required_facts=("material",),
            optional_facts=("surface_texture",),
            activation_conditions=("材质为塑料/亚克力",),
            generated_constraints=("保留塑料光泽与硬边轮廓",),
            forbidden_conditions=("不得呈现为布料质感",),
            compatible_templates=("ecommerce_standard_v1",),
            incompatible_capabilities=(),
            generation_strategy="grid",
            prompt_slots=("material",),
        ),
        "metal_material": _defn(
            "metal_material",
            "材质能力",
            required_facts=("material",),
            optional_facts=("surface_texture",),
            activation_conditions=("材质为金属",),
            generated_constraints=("保留金属反光与拉丝质感",),
            forbidden_conditions=("不得呈现为哑光布料质感",),
            compatible_templates=("ecommerce_standard_v1",),
            incompatible_capabilities=(),
            generation_strategy="grid",
            prompt_slots=("material",),
        ),
        "gel_material": _defn(
            "gel_material",
            "材质能力",
            required_facts=("material",),
            optional_facts=("surface_texture",),
            activation_conditions=("材质为凝胶/甲油胶",),
            generated_constraints=("保留凝胶光泽、边缘与表面质感",),
            forbidden_conditions=("不得呈现为哑光布面或塑料壳",),
            compatible_templates=("ecommerce_multi_piece_v1",),
            incompatible_capabilities=(),
            generation_strategy="independent_panels",
            prompt_slots=("material",),
        ),
    }
)

if tuple(CAPABILITY_REGISTRY) != CAPABILITY_IDS:  # pragma: no cover - 注册表自检
    raise AssertionError("能力注册表与 CAPABILITY_IDS 不一致")

# 建议标签 → 能力 ID 的标准化别名表（键为去掉分隔符后的小写形式）。
_SUGGESTION_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "print": "local_print",
        "printing": "local_print",
        "localprint": "local_print",
        "flat": "flat_print",
        "flatprint": "flat_print",
        "alloverprint": "flat_print",
        "wrap": "surface_wrap",
        "wrapped": "surface_wrap",
        "surfacewrap": "surface_wrap",
        "multipart": "multi_piece",
        "multipartset": "multi_piece",
        "multipiece": "multi_piece",
        "set": "multi_piece",
        "worn": "hand_worn",
        "wearable": "hand_worn",
        "hand": "hand_worn",
        "handworn": "hand_worn",
        "apparel": "body_worn",
        "bodyworn": "body_worn",
        "wall": "wall_mounted",
        "hanging": "wall_mounted",
        "wallmounted": "wall_mounted",
        "standalone": "standalone_display",
        "standalonedisplay": "standalone_display",
        "macro": "macro_detail",
        "closeup": "macro_detail",
        "macrodetail": "macro_detail",
        "white": "white_background",
        "whitebackground": "white_background",
        "fabric": "fabric_material",
        "paper": "paper_material",
        "plastic": "plastic_material",
        "metal": "metal_material",
        "gel": "gel_material",
    }
)

# 材质 token → 材质能力（按 token 精确匹配，避免 "pu" 之类的子串误伤）。
_MATERIAL_ALIASES: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset({"gel", "uvgel", "acrylicgel", "凝胶", "甲油胶", "美甲胶"}), "gel_material"),
    (
        frozenset({"fabric", "canvas", "cotton", "linen", "polyester", "nylon", "cloth", "textile", "布", "棉", "帆布", "绒"}),
        "fabric_material",
    ),
    (frozenset({"paper", "card", "cardboard", "kraft", "纸", "卡纸", "海报纸"}), "paper_material"),
    (frozenset({"plastic", "pvc", "abs", "acrylic", "pc", "tpu", "塑料", "亚克力"}), "plastic_material"),
    (frozenset({"metal", "steel", "aluminum", "alloy", "brass", "zinc", "金", "金属", "合金", "银"}), "metal_material"),
)

# 可印区域语义 token。
_FULL_SURFACE_TOKENS = frozenset(
    {"full", "fullsurface", "fullbody", "entire", "whole", "allover", "all", "全覆盖", "整个"}
)
_WRAP_TOKENS = frozenset({"wrap", "wrapped", "curved", "cylindrical", "round", "3d", "曲面", "环绕"})

# 使用方式 token → 展示能力 ID。
_USAGE_MODE_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "handworn": "hand_worn",
        "hand": "hand_worn",
        "wornonhand": "hand_worn",
        "手部佩戴": "hand_worn",
        "bodyworn": "body_worn",
        "worn": "body_worn",
        "wearable": "body_worn",
        "穿着": "body_worn",
        "wallmounted": "wall_mounted",
        "wall": "wall_mounted",
        "hanging": "wall_mounted",
        "墙面": "wall_mounted",
        "standalone": "standalone_display",
        "standing": "standalone_display",
        "desktop": "standalone_display",
        "摆放": "standalone_display",
    }
)

# 展示能力 → 用户偏好开关。
_PREFERENCE_GATES: Mapping[str, str] = MappingProxyType(
    {
        "hand_worn": "use_hand_model",
        "body_worn": "use_body_model",
        "wall_mounted": "use_wall_display",
    }
)

_TOKEN_SPLIT = re.compile(r"[^0-9a-zA-Z\u4e00-\u9fff]+")


def _tokenize(value: object) -> tuple[str, ...]:
    text = str(value or "").strip().lower().replace(" ", "_")
    return tuple(token for token in _TOKEN_SPLIT.split(text) if token)


def _material_capability(material: str) -> str | None:
    tokens = set(_tokenize(material))
    for aliases, capability in _MATERIAL_ALIASES:
        if tokens & aliases:
            return capability
    return None


def _print_capability(regions: tuple[str, ...]) -> str | None:
    if not regions:
        return None
    tokens: set[str] = set()
    for region in regions:
        tokens |= set(_tokenize(region))
    if tokens & _WRAP_TOKENS:
        return "surface_wrap"
    if tokens & _FULL_SURFACE_TOKENS:
        return "flat_print"
    return "local_print"


def _usage_modes(modes: tuple[str, ...]) -> set[str]:
    capabilities: set[str] = set()
    for mode in modes:
        tokens = _tokenize(mode)
        joined = "".join(tokens)
        if joined in _USAGE_MODE_ALIASES:
            capabilities.add(_USAGE_MODE_ALIASES[joined])
            continue
        for token in tokens:
            if token in _USAGE_MODE_ALIASES:
                capabilities.add(_USAGE_MODE_ALIASES[token])
    return capabilities


def _order(capabilities: set[str]) -> tuple[str, ...]:
    """按 ``CAPABILITY_IDS`` 优先级排序并消解互斥。"""
    ordered: list[str] = []
    for candidate in CAPABILITY_IDS:
        if candidate not in capabilities:
            continue
        if any((candidate, kept) in _INCOMPATIBLE_PAIRS or (kept, candidate) in _INCOMPATIBLE_PAIRS for kept in ordered):
            continue
        ordered.append(candidate)
    return tuple(ordered)


def match_capabilities(target: TargetProductSpec, preferences: PresentationPreferences) -> CapabilitySet:
    """由标准化事实与用户偏好确定性推导能力组合。"""
    derived: set[str] = set()

    component_count = int(target.component_count.value or 0)
    material = str(target.material.value or "")
    regions = tuple(str(item) for item in (target.printable_regions.value or []))
    modes = tuple(str(item) for item in (target.supported_usage_modes.value or []))

    material_capability = _material_capability(material)
    if material_capability:
        derived.add(material_capability)

    print_capability = _print_capability(regions)
    if print_capability:
        derived.add(print_capability)

    if component_count > 1:
        derived.add("multi_piece")

    # 小件 / 多部件 / 硬质小材质需要微距呈现。
    if component_count > 1 or material_capability in {"gel_material", "metal_material"}:
        derived.add("macro_detail")

    supported_modes = _usage_modes(modes)
    if supported_modes and supported_modes <= {"hand_worn", "body_worn"}:
        pass  # 纯佩戴类产品不自动给独立摆放
    else:
        derived.add("standalone_display")

    for capability, preference_field in _PREFERENCE_GATES.items():
        if capability in supported_modes and bool(getattr(preferences, preference_field)):
            derived.add(capability)

    if preferences.use_white_background:
        derived.add("white_background")

    # 模型建议标签：只做注册表校验；无法映射的进 unknown_capability。
    unknown: list[str] = []
    for suggestion in target.suggested_capabilities:
        normalized = _normalize_suggestion(suggestion)
        if normalized is None or normalized not in CAPABILITY_REGISTRY:
            unknown.append(str(suggestion))
            continue
        # 可映射但未被事实/偏好支持的标签一律丢弃，绝不直接成为能力。
        continue

    return CapabilitySet(
        capabilities=_order(derived),
        unknown_capability=tuple(unknown),
        registry_version=CAPABILITY_REGISTRY_VERSION,
    )


def _normalize_suggestion(suggestion: str) -> str | None:
    joined = "".join(_tokenize(suggestion))
    if not joined:
        return None
    if joined in CAPABILITY_REGISTRY:
        return joined
    return _SUGGESTION_ALIASES.get(joined)


def capability_definition(capability_id: str) -> CapabilityDefinition:
    """取能力定义；未注册的能力 ID 直接抛错（防止自由文本混入）。"""
    try:
        return CAPABILITY_REGISTRY[capability_id]
    except KeyError as exc:  # pragma: no cover - 调用方保证合法
        raise KeyError(f"未注册的能力：{capability_id}") from exc
