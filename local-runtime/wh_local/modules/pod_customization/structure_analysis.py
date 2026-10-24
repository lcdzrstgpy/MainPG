"""全定制模板的「结构事实表」：多视图 → 严格 JSON → 提示词硬约束。

背景：透明亚克力、玻璃、亮面、细结构产品在单张 3/4 视角下几乎没有几何证据，
生图模型只能按先验补全（层数、瓣数、板厚、连接方式），于是必然畸变。

这里把上传的结构视图集转成一份**可校验的部件事实**（而不是再塞给生图模型
几张图）：文字约束不会被画布注意力稀释，能逐条写进提示词，也能被人工修正。
调用本身走 ``PodReplicaAnalysisRuntime`` 的 Ark 视觉 lane（``pod.title`` 零计费）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from .title_runtime import _SYSTEM_SAFETY_CONTRACT

# 提示词 / 输出契约版本：改动任一处措辞或字段集合都必须递增。
STRUCTURE_ANALYSIS_VERSION = "pod-structure-v1"

# 视图角色。``dimension`` 只用于识图（读尺寸），不参与生图，避免把尺寸线、
# 箭头、彩色背景带进成品图。
VIEW_ROLES = ("primary", "side", "top", "dimension")
# 下发给生图模型的结构视图（顺序即参考图顺序，主图永远第一）。
GENERATION_VIEW_ROLES = ("primary", "side", "top")
MAX_GENERATION_VIEWS = 3

# 结构事实表状态：none（无视图）/ ready / needs_confirmation（低置信度待确认）/ failed
STRUCTURE_STATUS_NONE = "none"
STRUCTURE_STATUS_READY = "ready"
STRUCTURE_STATUS_NEEDS_CONFIRMATION = "needs_confirmation"
STRUCTURE_STATUS_FAILED = "failed"
STRUCTURE_STATUSES = (
    STRUCTURE_STATUS_NONE,
    STRUCTURE_STATUS_READY,
    STRUCTURE_STATUS_NEEDS_CONFIRMATION,
    STRUCTURE_STATUS_FAILED,
)

# 低于该置信度的字段直接丢弃：宁可不写，也不让模型把猜的数字写死成硬约束。
CONFIDENCE_DROP_THRESHOLD = 0.5
# 处于 [DROP, CONFIRM) 之间的字段保留，但整份事实表标记为「待人工确认」。
CONFIDENCE_CONFIRM_THRESHOLD = 0.7

_INT_FIELDS = ("part_count", "tiers", "units_per_tier")
_FLOAT_FIELDS = ("height_mm", "thickness_mm")
_TEXT_FIELDS = ("product_type", "material", "transparency", "surface_finish", "symmetry", "connections")
_CONFIDENCE_KEYS = (
    "product_type",
    "part_count",
    "tiers",
    "units_per_tier",
    "height_mm",
    "thickness_mm",
    "material",
    "transparency",
    "surface_finish",
    "symmetry",
    "connections",
)
_TRANSPARENCY_VALUES = ("none", "low", "medium", "high")

_TRANSPARENT_MATERIAL_TOKENS = (
    "acrylic", "plexiglass", "perspex", "polycarbonate", "glass", "clear", "transparent",
    "translucent", "crystal", "petg", "pvc", "resin",
    "亚克力", "玻璃", "透明", "水晶", "树脂",
)

STRUCTURE_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "pod_product_structure",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "product_type",
                "part_count",
                "tiers",
                "units_per_tier",
                "height_mm",
                "thickness_mm",
                "material",
                "transparency",
                "surface_finish",
                "symmetry",
                "connections",
                "printable_regions",
                "confidence",
            ],
            "properties": {
                "product_type": {"type": "string"},
                "part_count": {"type": "integer", "minimum": 0, "maximum": 500},
                "tiers": {"type": "integer", "minimum": 0, "maximum": 500},
                "units_per_tier": {"type": "integer", "minimum": 0, "maximum": 2000},
                "height_mm": {"type": "number", "minimum": 0, "maximum": 100000},
                "thickness_mm": {"type": "number", "minimum": 0, "maximum": 100000},
                "material": {"type": "string", "maxLength": 120},
                "transparency": {"type": "string", "maxLength": 20},
                "surface_finish": {"type": "string", "maxLength": 120},
                "symmetry": {"type": "string", "maxLength": 120},
                "connections": {"type": "string", "maxLength": 200},
                "printable_regions": {
                    "type": "array",
                    "maxItems": 12,
                    "items": {"type": "string", "maxLength": 120},
                },
                "confidence": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(_CONFIDENCE_KEYS),
                    "properties": {
                        key: {"type": "number", "minimum": 0, "maximum": 1}
                        for key in _CONFIDENCE_KEYS
                    },
                },
            },
        },
    },
}


def build_structure_messages(views: Sequence[Mapping[str, str]]) -> list[dict[str, Any]]:
    """构造识图消息：按上传顺序附图（主图在前），再给严格 JSON 契约。

    ``views`` 每项至少含 ``role`` 与 ``data_url``；顺序即提示词里编号的顺序，
    调用方必须保证主图（``primary``）排第一。
    """
    content: list[dict[str, Any]] = [
        {"type": "image_url", "image_url": {"url": str(view["data_url"])}}
        for view in views
    ]
    content.append(
        {
            "type": "text",
            "text": json.dumps(
                {
                    "task": "Describe the physical structure of the ONE product shown in the attached reference views.",
                    "image_roles": [
                        f"Image {index} = {str(view.get('role') or '').upper()}"
                        for index, view in enumerate(views, start=1)
                    ],
                    "rules": [
                        "All images show the same single product from different angles; never describe them as different products.",
                        "Count parts literally: number of separate pieces, vertical tiers/layers, and repeated units per tier. Count what is visible, do not estimate a 'typical' version.",
                        "Read dimensions ONLY when a measurement sheet is attached; report millimetres exactly as printed. If no measurement is visible, return 0 for that field and give it a low confidence.",
                        "Measurement lines, arrows, numbers and coloured backdrops are annotations, not product parts; never treat them as structure.",
                        "Describe transparency honestly: none / low / medium / high. 'Clear acrylic' or 'glass' is high transparency, frosted or milky material is low.",
                        "Never invent brand, model, price, function or accessories that are not visible.",
                        "Give every confidence value in 0..1; when a field cannot be seen, use a low value instead of guessing.",
                        "Return exactly one JSON object, no markdown and no extra keys.",
                    ],
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        }
    )
    return [
        {"role": "system", "content": _SYSTEM_SAFETY_CONTRACT},
        {"role": "user", "content": content},
    ]


def parse_structure_response(content: str) -> dict[str, Any]:
    """解析并规范化结构事实表；低置信度字段直接丢弃。

    抛 ``ValueError`` 表示输出违反契约（由 runtime 触发一次契约修复重试）。
    """
    try:
        payload = json.loads(str(content or ""))
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("structure analysis response did not contain strict JSON") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("structure analysis response must be a JSON object")

    raw_confidence = payload.get("confidence")
    confidence: dict[str, float] = {}
    if isinstance(raw_confidence, Mapping):
        for key in _CONFIDENCE_KEYS:
            confidence[key] = _clamp_confidence(raw_confidence.get(key))

    facts: dict[str, Any] = {"confidence": confidence}
    for key in _INT_FIELDS:
        value = _bounded_int(payload.get(key), limit=2000)
        if value and confidence.get(key, 0.0) >= CONFIDENCE_DROP_THRESHOLD:
            facts[key] = value
    for key in _FLOAT_FIELDS:
        value = _bounded_float(payload.get(key))
        if value and confidence.get(key, 0.0) >= CONFIDENCE_DROP_THRESHOLD:
            facts[key] = value
    for key in _TEXT_FIELDS:
        value = _clean_text(payload.get(key), limit=200)
        if not value:
            continue
        if confidence.get(key, 0.0) < CONFIDENCE_DROP_THRESHOLD:
            continue
        if key == "transparency":
            value = value.casefold()
            if value not in _TRANSPARENCY_VALUES:
                continue
        facts[key] = value

    regions = payload.get("printable_regions")
    if isinstance(regions, Sequence) and not isinstance(regions, (str, bytes)):
        cleaned = [_clean_text(item, limit=120) for item in regions]
        printable = [item for item in cleaned if item][:12]
        if printable:
            facts["printable_regions"] = printable
    if not facts.keys() - {"confidence"}:
        raise ValueError("structure analysis returned no usable fact")
    return facts


def needs_confirmation(facts: Mapping[str, Any] | None) -> bool:
    """是否存在需要人工确认的低置信度字段。"""
    if not isinstance(facts, Mapping):
        return False
    confidence = facts.get("confidence")
    if not isinstance(confidence, Mapping):
        return False
    return any(
        _clamp_confidence(confidence.get(key)) < CONFIDENCE_CONFIRM_THRESHOLD
        for key in _CONFIDENCE_KEYS
        if key in confidence
    )


def is_transparent(facts: Mapping[str, Any] | None) -> bool:
    if not isinstance(facts, Mapping):
        return False
    if str(facts.get("transparency") or "").casefold() in {"medium", "high"}:
        return True
    material = str(facts.get("material") or "").casefold()
    return any(token in material for token in _TRANSPARENT_MATERIAL_TOKENS)


def load_structure_facts(raw: Any) -> dict[str, Any] | None:
    """把库里的 ``structure_facts_json`` 还原成事实表；坏数据一律当空。"""
    if isinstance(raw, Mapping):
        return dict(raw)
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None
    return dict(payload) if isinstance(payload, Mapping) else None


def render_structure_facts_block(facts: Mapping[str, Any] | None) -> str:
    """把事实表渲染成 PART 1 的字面事实块；没有可用事实时返回空串。"""
    if not isinstance(facts, Mapping):
        return ""
    lines: list[str] = []
    if facts.get("part_count"):
        lines.append(f"- Separate parts: exactly {int(facts['part_count'])}.")
    if facts.get("tiers"):
        lines.append(f"- Vertical tiers: exactly {int(facts['tiers'])}.")
    if facts.get("units_per_tier"):
        lines.append(f"- Repeated units on each tier: exactly {int(facts['units_per_tier'])}.")
    if facts.get("height_mm"):
        lines.append(f"- Overall height: {_format_number(facts['height_mm'])} mm.")
    if facts.get("thickness_mm"):
        lines.append(f"- Material thickness: {_format_number(facts['thickness_mm'])} mm.")
    material_bits = [
        str(facts.get(key) or "").strip()
        for key in ("material", "transparency", "surface_finish")
    ]
    material_line = ", ".join(bit for bit in material_bits if bit)
    if material_line:
        lines.append(f"- Material: {material_line}.")
    if facts.get("symmetry"):
        lines.append(f"- Symmetry: {facts['symmetry']}.")
    if facts.get("connections"):
        lines.append(f"- Connections between parts: {facts['connections']}.")
    regions = facts.get("printable_regions")
    if isinstance(regions, Sequence) and not isinstance(regions, (str, bytes)) and regions:
        lines.append(f"- Printable surfaces: {', '.join(str(item) for item in regions)}.")
    if not lines:
        return ""
    return (
        "STRUCTURE FACTS — extracted from the uploaded multi-view reference set. Every line is a literal, "
        "measurable fact, not a suggestion:\n" + "\n".join(lines)
    )


def render_part_count_lock(facts: Mapping[str, Any] | None) -> str:
    """部件数量/尺寸锁定：只在事实表确实给出数字时渲染。"""
    if not isinstance(facts, Mapping):
        return ""
    counts: list[str] = []
    if facts.get("tiers"):
        counts.append(f"exactly {int(facts['tiers'])} vertical tiers")
    if facts.get("units_per_tier"):
        counts.append(f"exactly {int(facts['units_per_tier'])} repeated units on each tier")
    if facts.get("part_count"):
        counts.append(f"exactly {int(facts['part_count'])} separate parts")
    sizes: list[str] = []
    if facts.get("height_mm"):
        sizes.append(f"{_format_number(facts['height_mm'])} mm tall")
    if facts.get("thickness_mm"):
        sizes.append(f"{_format_number(facts['thickness_mm'])} mm thick")
    if not counts and not sizes:
        return ""
    requirement = " and ".join(counts + sizes)
    return (
        "PART COUNT & SIZE LOCK — count literally in every panel: this product has "
        f"{requirement}. Do not simplify, merge, duplicate, add or remove any part, and do not "
        "re-proportion the product. A panel with a different tier count, unit count, thickness or "
        "silhouette is a FAILED result, even if the artwork looks better."
    )


def render_transparent_material_lock(facts: Mapping[str, Any] | None) -> str:
    """透明/亮面材质锁：只在事实表判定为透明材质时渲染。"""
    if not is_transparent(facts):
        return ""
    return (
        "TRANSPARENT MATERIAL LOCK — this product is genuinely see-through, and every panel must keep it so:\n"
        "- Keep the body transparent: the background stays visible through it, edges stay crisp and polished, "
        "sheet thickness is visible, and bright specular highlights plus subtle edge refraction read as real "
        "clear material.\n"
        "- Never turn it into frosted, milky, white, opaque plastic, painted metal or tinted glass; never add a "
        "colour tint the reference does not show.\n"
        "- Never invent side walls, back panels, bases, lids, brackets or supports. Only the parts in STRUCTURE "
        "FACTS exist.\n"
        "- Separate the product from its background with edge highlights, a soft contact shadow and slight "
        "refraction — never by making it opaque or darker."
    )


def render_reference_views_block(roles: Sequence[str]) -> str:
    """多视图契约：只在确实下发了 2 张以上参考图时渲染。"""
    attached = [str(role) for role in roles if str(role) in GENERATION_VIEW_ROLES]
    if len(attached) < 2:
        return ""
    descriptions = {
        "primary": "PRIMARY white-background product photo — the single structural baseline",
        "side": "SIDE elevation of the same product",
        "top": "TOP elevation of the same product",
    }
    lines = [
        f"- Image {index} = {descriptions.get(role, role)}."
        for index, role in enumerate(attached, start=1)
    ]
    return (
        "REFERENCE VIEWS CONTRACT — the attached images are ONE orthographic reference set for ONE single "
        "product, not artwork to copy:\n"
        + "\n".join(lines)
        + "\n- Use the extra views ONLY to resolve geometry the primary image cannot show: part count, tier "
        "count, repeated units, thickness, connections and symmetry.\n"
        "- Take STRUCTURE ONLY from these images. Never copy their background, backdrop colour, props, "
        "measurement lines, arrows, numbers, labels or watermarks; annotations are not part of the product and "
        "must never appear in the result.\n"
        "- If two views seem to disagree, the PRIMARY image wins. Never average them, never merge parts from "
        "different views and never build a hybrid product.\n"
        "- All four panels must show the same product reconstructed from this set. A panel with a different "
        "silhouette, part count or proportion is a FAILED result."
    )


def _bounded_int(value: Any, *, limit: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(int(limit), number))


def _bounded_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number:  # NaN
        return 0.0
    return max(0.0, min(100000.0, round(number, 2)))


def _clamp_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number:
        return 0.0
    return max(0.0, min(1.0, number))


def _clean_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _format_number(value: Any) -> str:
    number = _bounded_float(value)
    return str(int(number)) if float(number).is_integer() else f"{number:g}"
