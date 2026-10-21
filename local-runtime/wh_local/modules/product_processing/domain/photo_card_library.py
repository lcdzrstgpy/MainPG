"""Five-layer image prompt card library and assembler.

Layers, in required assembly order:

1. FRAME  - composition skeleton (camera, scale, composition)
2. LIGHT  - light direction, shadow, contrast
3. TONE   - saturation, brightness, color temperature
4. DETAIL - material rendering and micro texture
5. STYLE  - global mood and finish

Each layer has a small set of cards.  A category has a default card per layer.
The assembler takes the category plus optional per-layer overrides and returns
one compact single-line prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .category_image_presets import CATEGORY_IMAGE_PROMPTS
from .image_style_presets import IMAGE_STYLE_PRESETS

LAYER_KEYS: tuple[str, ...] = ("frame", "light", "tone", "detail", "style")


@dataclass(frozen=True, slots=True)
class PhotoCard:
    id: str
    label_zh: str
    prompt_en: str


FRAME_CARDS: dict[str, PhotoCard] = {
    "frame_hero": PhotoCard(
        "frame_hero",
        "英雄生活场景",
        "50mm at 30-45 degrees, product fills 52-65% of frame, centered hero composition",
    ),
    "frame_macro": PhotoCard(
        "frame_macro",
        "微距细节",
        "90mm macro, detail fills 60-75% of frame, close detail crop",
    ),
    "frame_environment": PhotoCard(
        "frame_environment",
        "环境远景",
        "24mm wide environmental view, product occupies 18-38% of frame, three-layer composition",
    ),
    "frame_flatlay": PhotoCard(
        "frame_flatlay",
        "平铺网格",
        "true 90-degree top-down, product set occupies 55-68% of frame, strict knolling grid",
    ),
    "frame_flatlay_detail": PhotoCard(
        "frame_flatlay_detail",
        "俯拍特写",
        "true 90-degree top-down close crop, one component fills 45-60% of frame, strict overhead alignment, no perspective distortion",
    ),
    "frame_low_angle": PhotoCard(
        "frame_low_angle",
        "低角度",
        "35mm low-angle view, product fills 52-65% of frame, asymmetric balance",
    ),
    "frame_portrait": PhotoCard(
        "frame_portrait",
        "人像压缩",
        "85mm compressed perspective, product occupies 52-65% of frame, rule-of-thirds placement",
    ),
}

LIGHT_CARDS: dict[str, PhotoCard] = {
    "light_soft_home": PhotoCard(
        "light_soft_home",
        "柔和家居",
        "soft window light from one side, white bounce fill, grounded contact shadow, balanced medium contrast",
    ),
    "light_clean_bright": PhotoCard(
        "light_clean_bright",
        "干净明亮",
        "even overhead light, controlled specular highlights, grounded contact shadow, balanced medium contrast",
    ),
    "light_editorial_sun": PhotoCard(
        "light_editorial_sun",
        "编辑日光",
        "low warm sunlight, white bounce fill, soft directional drop shadow, editorial contrast with clean highlight rolloff",
    ),
    "light_workshop_hard": PhotoCard(
        "light_workshop_hard",
        "工坊硬光",
        "hard raking work light, negative fill on the shadow side, crisp hard shadow, high contrast with deep falloff",
    ),
    "light_overcast": PhotoCard(
        "light_overcast",
        "阴天户外",
        "bright overcast daylight, negative fill on the shadow side, soft directional drop shadow, high contrast with deep falloff",
    ),
    "light_warm_evening": PhotoCard(
        "light_warm_evening",
        "暖夜实践",
        "warm tungsten practical lamp light, neutral white light without colored gels, long soft shadow, high contrast",
    ),
    "light_vanity": PhotoCard(
        "light_vanity",
        "临床梳妆",
        "bright even vanity light, controlled specular highlights, soft directional drop shadow, low-contrast tonal range",
    ),
    "light_car_sun": PhotoCard(
        "light_car_sun",
        "车内硬阳",
        "hard sun through glass with soft fill, white bounce fill, tight-gap contact shadow, high contrast",
    ),
    "light_dappled": PhotoCard(
        "light_dappled",
        "斑驳园艺",
        "dappled leaf-shadow daylight, white bounce fill, dappled leaf shadow, balanced medium contrast",
    ),
    "light_studio_hero": PhotoCard(
        "light_studio_hero",
        "影棚英雄",
        "soft studio sweep key with gentle falloff, controlled specular highlights, grounded contact shadow, balanced medium contrast",
    ),
    "light_warm_sport": PhotoCard(
        "light_warm_sport",
        "暖阳运动",
        "low warm sunlight, negative fill on the shadow side, crisp hard shadow, high contrast with deep falloff",
    ),
    "light_window_editorial": PhotoCard(
        "light_window_editorial",
        "窗光编辑",
        "soft window light from one side, white bounce fill, soft drop shadow, editorial contrast",
    ),
}

TONE_CARDS: dict[str, PhotoCard] = {
    "tone_natural": PhotoCard(
        "tone_natural",
        "自然平衡",
        "natural true color, balanced exposure, 4800K warm daylight",
    ),
    "tone_bright_airy": PhotoCard(
        "tone_bright_airy",
        "明亮通透",
        "muted natural color, bright airy exposure, 5200K cool daylight",
    ),
    "tone_warm_moody": PhotoCard(
        "tone_warm_moody",
        "暖调氛围",
        "rich color, low-key exposure, 2700K warm tone",
    ),
    "tone_clinical": PhotoCard(
        "tone_clinical",
        "临床干净",
        "natural true color, bright even exposure, 5000K neutral",
    ),
    "tone_industrial": PhotoCard(
        "tone_industrial",
        "工业硬朗",
        "muted color, balanced exposure, 5000K cool",
    ),
    "tone_fresh_outdoor": PhotoCard(
        "tone_fresh_outdoor",
        "清新户外",
        "rich color, bright exposure, 5600K daylight",
    ),
    "tone_editorial_muted": PhotoCard(
        "tone_editorial_muted",
        "编辑低饱和",
        "desaturated background, product color accent, balanced exposure, 5200K cool",
    ),
    "tone_warm_outdoor": PhotoCard(
        "tone_warm_outdoor",
        "暖阳户外",
        "rich color, bright exposure, 4800K warm daylight",
    ),
}

DETAIL_CARDS: dict[str, PhotoCard] = {
    "detail_ceramic_fabric": PhotoCard(
        "detail_ceramic_fabric",
        "陶瓷织物",
        "ceramic glaze micro-shine, visible weave and fold memory",
    ),
    "detail_soft_goods": PhotoCard(
        "detail_soft_goods",
        "软质服装",
        "weave and fold memory, leather grain and creases",
    ),
    "detail_paper_plastic": PhotoCard(
        "detail_paper_plastic",
        "纸张塑料",
        "individual paper edges, subtle plastic mold seams",
    ),
    "detail_rubber_webbing": PhotoCard(
        "detail_rubber_webbing",
        "橡胶织带",
        "textured rubber grip, visible weave and fold memory",
    ),
    "detail_plastic_leather": PhotoCard(
        "detail_plastic_leather",
        "塑料皮革",
        "subtle plastic mold seams, natural leather grain and creases",
    ),
    "detail_metal_wood": PhotoCard(
        "detail_metal_wood",
        "金属木纹",
        "anisotropic metal highlights, visible wood grain",
    ),
    "detail_matte_plastic": PhotoCard(
        "detail_matte_plastic",
        "哑光塑料",
        "matte surface with soft sheen, subtle plastic mold seams",
    ),
    "detail_metal_ceramic": PhotoCard(
        "detail_metal_ceramic",
        "金属陶瓷",
        "anisotropic metal highlights, ceramic glaze micro-shine",
    ),
    "detail_paper_felt": PhotoCard(
        "detail_paper_felt",
        "纸张毛毡",
        "individual paper edges, visible weave and fold memory",
    ),
    "detail_soil_terracotta": PhotoCard(
        "detail_soil_terracotta",
        "土壤陶土",
        "moist soil and porous terracotta, ceramic glaze micro-shine",
    ),
}

STYLE_CARDS: dict[str, PhotoCard] = {
    style_id: PhotoCard(style_id, preset.label_zh, preset.prompt_en)
    for style_id, preset in IMAGE_STYLE_PRESETS.items()
    if style_id != "auto" and preset.prompt_en
}

CARD_LIBRARIES: dict[str, dict[str, PhotoCard]] = {
    "frame": FRAME_CARDS,
    "light": LIGHT_CARDS,
    "tone": TONE_CARDS,
    "detail": DETAIL_CARDS,
    "style": STYLE_CARDS,
}


@dataclass(frozen=True, slots=True)
class AuxCard:
    """Per-panel display element: scene/surface or companion prop."""

    id: str
    label_zh: str
    prompt_en: str
    kind: str  # scene | prop


AUX_CARDS: dict[str, AuxCard] = {
    # Scenes / surfaces
    "scene_white_sweep": AuxCard(
        "scene_white_sweep", "纯白无缝", "pure white seamless studio sweep with no scene", "scene"
    ),
    "scene_light_oak": AuxCard(
        "scene_light_oak", "浅橡木桌面", "light oak desk surface with a soft plaster wall", "scene"
    ),
    "scene_butcher_block": AuxCard(
        "scene_butcher_block", "厨房木台面", "butcher-block kitchen counter with a warm plaster wall", "scene"
    ),
    "scene_linen": AuxCard(
        "scene_linen", "亚麻台面", "washed linen surface with a soft neutral backdrop", "scene"
    ),
    "scene_marble": AuxCard(
        "scene_marble", "白色大理石", "white marble vanity surface with a pale wall", "scene"
    ),
    "scene_dark_marble": AuxCard(
        "scene_dark_marble", "深色大理石", "dark marble cafe table with window-light falloff", "scene"
    ),
    "scene_felt": AuxCard(
        "scene_felt", "深色毛毡", "deep felt surface with a clean neutral backdrop", "scene"
    ),
    "scene_concrete": AuxCard(
        "scene_concrete", "水泥工坊", "concrete workshop floor with an industrial backdrop", "scene"
    ),
    "scene_plaster": AuxCard(
        "scene_plaster", "灰泥墙台", "pale plaster wall and a simple stone ledge", "scene"
    ),
    "scene_stone": AuxCard(
        "scene_stone", "浅色石台", "light stone ledge with an open background", "scene"
    ),
    "scene_terracotta": AuxCard(
        "scene_terracotta", "陶土花台", "terracotta potting surface with loose soil", "scene"
    ),
    "scene_rug": AuxCard(
        "scene_rug", "羊毛地毯", "wool rug with soft floor-level texture", "scene"
    ),
    "scene_rubber": AuxCard(
        "scene_rubber", "健身房橡胶地", "dark rubber gym floor with hard overhead light", "scene"
    ),
    "scene_bedroom_mirror": AuxCard(
        "scene_bedroom_mirror",
        "卧室镜前",
        "bedroom mirror corner with an oak floor and linen curtain",
        "scene",
    ),
    "scene_entryway_console": AuxCard(
        "scene_entryway_console",
        "玄关台",
        "entryway console with a pale plaster wall",
        "scene",
    ),
    "scene_workbench": AuxCard(
        "scene_workbench", "工坊工作台", "oiled wood workbench with a pegboard wall", "scene"
    ),
    "scene_vanity": AuxCard(
        "scene_vanity", "梳妆台", "white marble vanity with a brushed-steel tray", "scene"
    ),
    "scene_potting_bench": AuxCard(
        "scene_potting_bench", "育苗台", "weathered potting bench with terracotta pots", "scene"
    ),
    "scene_gaming_table": AuxCard(
        "scene_gaming_table", "游戏茶几", "walnut coffee table with a warm lamp pool", "scene"
    ),
    # Companion props
    # 只保留“大块、简单、易识别”的道具：尺子/笔/刷子这类细长精细物件在生图里
    # 极易畸变或与商品粘连，直接不提供，从源头避免。
    "prop_notebook": AuxCard("prop_notebook", "笔记本", "one closed notebook", "prop"),
    "prop_ceramic_bowl": AuxCard("prop_ceramic_bowl", "陶瓷碗", "one ceramic bowl", "prop"),
    "prop_linen_towel": AuxCard("prop_linen_towel", "亚麻巾", "one folded linen towel", "prop"),
    "prop_pet_blanket": AuxCard("prop_pet_blanket", "宠物毯", "one folded pet blanket", "prop"),
    "prop_steel_bowl": AuxCard("prop_steel_bowl", "钢碗", "one steel bowl", "prop"),
    "prop_cosmetic_jar": AuxCard("prop_cosmetic_jar", "面霜罐", "one cosmetic jar", "prop"),
    "prop_plant_pot": AuxCard("prop_plant_pot", "花盆", "one small plant pot", "prop"),
    "prop_woven_basket": AuxCard("prop_woven_basket", "编织篮", "one woven basket", "prop"),
    "prop_wooden_block": AuxCard("prop_wooden_block", "木块", "one neutral wooden block", "prop"),
}

PANEL_ROLES: tuple[str, ...] = ("hero", "alternate", "lifestyle", "detail")
PANEL_ROLE_LABELS: dict[str, str] = {
    "hero": "主图/白底英雄",
    "alternate": "45° 完整角度",
    "lifestyle": "生活场景",
    "detail": "微距细节",
}

# Category-level per-panel defaults: scene + 1-2 companion props for panels 2/3.
CATEGORY_AUX_DEFAULTS: dict[str, dict[str, Any]] = {
    "home_kitchen": {
        "scenes": ("scene_butcher_block", "scene_linen", "scene_light_oak"),
        "props": ("prop_ceramic_bowl", "prop_linen_towel"),
    },
    "apparel_accessories": {
        "scenes": ("scene_bedroom_mirror", "scene_entryway_console", "scene_stone"),
        "props": ("prop_linen_towel",),
    },
    "pet_supplies": {
        "scenes": ("scene_rug", "scene_entryway_console", "scene_light_oak"),
        "props": ("prop_pet_blanket", "prop_steel_bowl"),
    },
    "office_stationery": {
        "scenes": ("scene_light_oak", "scene_felt", "scene_plaster"),
        "props": ("prop_notebook",),
    },
    "sports_outdoor": {
        "scenes": ("scene_rubber", "scene_stone", "scene_light_oak"),
        "props": ("prop_linen_towel",),
    },
    "auto_accessories": {
        "scenes": ("scene_concrete", "scene_light_oak", "scene_felt"),
        "props": ("prop_notebook",),
    },
    "hand_tools": {
        "scenes": ("scene_workbench", "scene_concrete", "scene_light_oak"),
        "props": ("prop_wooden_block",),
    },
    "passive_tech": {
        "scenes": ("scene_light_oak", "scene_felt", "scene_marble"),
        "props": ("prop_notebook",),
    },
    "beauty_tools": {
        "scenes": ("scene_vanity", "scene_marble", "scene_plaster"),
        "props": ("prop_cosmetic_jar",),
    },
    "adult_games": {
        "scenes": ("scene_gaming_table", "scene_felt", "scene_light_oak"),
        # 桌游卡片/棋子本身细节已足够，不再叠加精细道具。
        "props": (),
    },
    "garden_tools": {
        "scenes": ("scene_potting_bench", "scene_terracotta", "scene_stone"),
        "props": ("prop_plant_pot", "prop_woven_basket"),
    },
    "general": {
        "scenes": ("scene_plaster", "scene_light_oak", "scene_stone"),
        "props": ("prop_wooden_block",),
    },
}


CATEGORY_DEFAULT_PACKS: dict[str, dict[str, str]] = {
    "home_kitchen": {
        "frame": "frame_hero",
        "light": "light_soft_home",
        "tone": "tone_natural",
        "detail": "detail_ceramic_fabric",
        "style": "calm_domestic",
    },
    "apparel_accessories": {
        "frame": "frame_portrait",
        "light": "light_window_editorial",
        "tone": "tone_editorial_muted",
        "detail": "detail_soft_goods",
        "style": "editorial_minimal",
    },
    "pet_supplies": {
        "frame": "frame_hero",
        "light": "light_soft_home",
        "tone": "tone_natural",
        "detail": "detail_rubber_webbing",
        "style": "calm_domestic",
    },
    "office_stationery": {
        "frame": "frame_flatlay",
        "light": "light_clean_bright",
        "tone": "tone_bright_airy",
        "detail": "detail_paper_plastic",
        "style": "bright_minimal",
    },
    "sports_outdoor": {
        "frame": "frame_environment",
        "light": "light_warm_sport",
        "tone": "tone_warm_outdoor",
        "detail": "detail_rubber_webbing",
        "style": "fresh_outdoor",
    },
    "auto_accessories": {
        "frame": "frame_environment",
        "light": "light_car_sun",
        "tone": "tone_industrial",
        "detail": "detail_plastic_leather",
        "style": "editorial_minimal",
    },
    "hand_tools": {
        "frame": "frame_macro",
        "light": "light_workshop_hard",
        "tone": "tone_industrial",
        "detail": "detail_metal_wood",
        "style": "industrial",
    },
    "passive_tech": {
        "frame": "frame_macro",
        "light": "light_clean_bright",
        "tone": "tone_bright_airy",
        "detail": "detail_matte_plastic",
        "style": "editorial_minimal",
    },
    "beauty_tools": {
        "frame": "frame_macro",
        "light": "light_vanity",
        "tone": "tone_clinical",
        "detail": "detail_metal_ceramic",
        "style": "clinical_clean",
    },
    "adult_games": {
        "frame": "frame_hero",
        "light": "light_warm_evening",
        "tone": "tone_warm_moody",
        "detail": "detail_paper_felt",
        "style": "warm_evening",
    },
    "garden_tools": {
        "frame": "frame_hero",
        "light": "light_dappled",
        "tone": "tone_fresh_outdoor",
        "detail": "detail_soil_terracotta",
        "style": "fresh_outdoor",
    },
    "general": {
        "frame": "frame_hero",
        "light": "light_soft_home",
        "tone": "tone_natural",
        "detail": "detail_matte_plastic",
        "style": "editorial_minimal",
    },
}


def _split_category_base(prompt: str) -> tuple[str, str]:
    """Split a v16 category prompt into head (Scene/Focus) and tail (Show/Avoid).

    The old inline ``Light:`` sentence is removed because the LIGHT layer owns it.
    """

    head, rest = prompt.split("Light:", 1)
    _, tail = rest.split("Show:", 1)
    return head.strip(), f"Show:{tail}".strip()


CATEGORY_BASE_PROMPTS: dict[str, tuple[str, str]] = {
    category_id: _split_category_base(prompt)
    for category_id, prompt in CATEGORY_IMAGE_PROMPTS.items()
}

# 四个小白模板的商业意图：不是摄影参数，而是“买家一眼要看到什么”。
# 放在 Focus 之后、每格卡片之前，避免四个模板只换光影却仍然同质化。
PRESET_INTENTS: dict[str, str] = {
    "auto": "",
    "hero_proof": (
        "BUYER PROOF: one dominant subject; the main selling point reads at a glance "
        "through size, quantity or key structure; crisp edges, clean silhouette, "
        "no props, no clutter."
    ),
    # 生图里的手指是最常见畸变源；「真实使用」改为用真实空间与摆放位置表达用途，
    # 不出现任何手部，避免多指/粘连。
    "lifestyle_use": (
        "BUYER PROOF: show the use story with the product already placed where it is "
        "used - believable lived-in room, real scale cues, one clear in-use arrangement; "
        "no hands, no fingers, no people, no face."
    ),
    "macro_craft": (
        "BUYER PROOF: price is justified by material and construction; one panel "
        "crops tight to 65-85% with grain, seam, stitch or hardware under hard raking "
        "light, while the other panels keep the whole build readable; no hands."
    ),
    "knolling_set": (
        "BUYER PROOF: prove the complete set - every component and its exact quantity "
        "laid out in use order, true 90-degree top-down, no missing pieces, no hands."
    ),
}


def _resolve_preset(preset_id: Any) -> tuple[str, str]:
    selected = str(preset_id or "auto").strip().lower()
    if selected not in PRESET_INTENTS:
        selected = "auto"
    return selected, PRESET_INTENTS[selected]


@dataclass(frozen=True, slots=True)
class PromptAssembly:
    prompt: str
    word_count: int
    category_id: str
    selections: dict[str, str]
    used_defaults: tuple[str, ...] = ()
    over_budget: bool = False
    preset_id: str = "auto"


def _resolve_category(category_id: str) -> str:
    normalized = str(category_id or "").strip().lower()
    return normalized if normalized in CATEGORY_BASE_PROMPTS else "general"


def _resolve_selection(
    category_id: str,
    layer: str,
    value: Any,
) -> tuple[str, bool]:
    selected = str(value or "").strip().lower()
    library = CARD_LIBRARIES[layer]
    if selected in library:
        return selected, False
    return CATEGORY_DEFAULT_PACKS[category_id][layer], True


def assemble_image_prompt(
    category_id: str,
    selections: dict[str, Any] | None = None,
    preset_id: str = "auto",
) -> PromptAssembly:
    """Assemble one category prompt from the five layers.

    ``selections`` may contain ``frame/light/tone/detail/style`` ids.  Missing,
    ``auto`` or invalid values fall back to the category's default pack.
    ``preset_id`` adds the four simple-template commercial intents.
    """

    resolved_category = _resolve_category(category_id)
    resolved_preset, intent = _resolve_preset(preset_id)
    head, tail = CATEGORY_BASE_PROMPTS[resolved_category]
    effective: dict[str, str] = {}
    used_defaults: list[str] = []
    for layer in LAYER_KEYS:
        value = (selections or {}).get(layer)
        card_id, defaulted = _resolve_selection(resolved_category, layer, value)
        effective[layer] = card_id
        if defaulted:
            used_defaults.append(layer)

    frame = FRAME_CARDS[effective["frame"]].prompt_en
    light = LIGHT_CARDS[effective["light"]].prompt_en
    tone = TONE_CARDS[effective["tone"]].prompt_en
    detail = DETAIL_CARDS[effective["detail"]].prompt_en
    style = STYLE_CARDS[effective["style"]].prompt_en

    parts = [head]
    if intent:
        parts.append(intent)
    parts.extend(
        [
            f"Compose: {frame}.",
            f"Light: {light}.",
            f"Color: {tone}.",
            f"Detail: {detail}.",
            f"Style: {style}.",
            tail,
        ]
    )
    prompt = " ".join(parts).strip()
    word_count = len(prompt.split())
    return PromptAssembly(
        prompt=prompt,
        word_count=word_count,
        category_id=resolved_category,
        selections=effective,
        used_defaults=tuple(used_defaults),
        over_budget=word_count > 110,
        preset_id=resolved_preset,
    )


@dataclass(frozen=True, slots=True)
class PanelAssembly:
    prompt: str
    word_count: int
    category_id: str
    panels: tuple[str, ...]
    selections: dict[str, Any]
    used_defaults: tuple[str, ...] = ()
    over_budget: bool = False
    preset_id: str = "auto"


PANEL_LAYER_DEFAULTS: dict[str, dict[str, str]] = {
    "hero": {"frame": "frame_hero", "light": "light_studio_hero"},
    "alternate": {"frame": "frame_low_angle"},
    "lifestyle": {"frame": "frame_environment"},
    "detail": {"frame": "frame_macro"},
}


def _category_label_and_focus(category_id: str) -> tuple[str, str]:
    head, _tail = CATEGORY_BASE_PROMPTS[category_id]
    label, _separator, rest = head.partition(":")
    if "Focus:" in rest:
        focus = rest.split("Focus:", 1)[1].strip()
    elif "Route:" in rest:
        focus = rest.split("Route:", 1)[1].strip()
    else:
        focus = "Keep the exact product identity complete and prominent."
    return label.strip(), focus


def _safe_scene(value: Any) -> str:
    selected = str(value or "").strip().lower()
    card = AUX_CARDS.get(selected)
    return selected if card is not None and card.kind == "scene" else ""


def _safe_props(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    picked: list[str] = []
    for raw in value:
        card_id = str(raw or "").strip().lower()
        card = AUX_CARDS.get(card_id)
        if card is None or card.kind != "prop" or card_id in picked:
            continue
        picked.append(card_id)
        if len(picked) >= 3:
            break
    return picked


def assemble_panel_image_prompt(
    category_id: str,
    panels: list[Any] | tuple[Any, ...] | None = None,
    global_selection: dict[str, Any] | None = None,
    preset_id: str = "auto",
) -> PanelAssembly:
    """Assemble a four-panel prompt with per-panel cards and aux elements.

    Each panel can override the five photographic layers and add one scene card
    plus up to three companion prop cards.  ``auto``/missing values inherit the
    batch-level ``global_selection`` and then the category default pack.
    ``preset_id`` adds the four simple-template commercial intents.
    """

    resolved_category = _resolve_category(category_id)
    resolved_preset, intent = _resolve_preset(preset_id)
    label, focus = _category_label_and_focus(resolved_category)
    _head, tail = CATEGORY_BASE_PROMPTS[resolved_category]
    aux_defaults = CATEGORY_AUX_DEFAULTS[resolved_category]
    default_scenes = list(aux_defaults.get("scenes") or ("scene_plaster",))
    default_props = list(aux_defaults.get("props") or ())

    base_layers: dict[str, str] = {}
    for layer in LAYER_KEYS:
        base_value = (
            (global_selection or {}).get(layer)
            if isinstance(global_selection, dict)
            else "auto"
        )
        base_id, _defaulted = _resolve_selection(
            resolved_category, layer, base_value
        )
        base_layers[layer] = base_id
    base_look = (
        f"GLOBAL LOOK: Light: {LIGHT_CARDS[base_layers['light']].prompt_en}. "
        f"Color: {TONE_CARDS[base_layers['tone']].prompt_en}. "
        f"Detail: {DETAIL_CARDS[base_layers['detail']].prompt_en}. "
        f"Style: {STYLE_CARDS[base_layers['style']].prompt_en}."
    )

    raw_panels = list(panels or [])
    effective_panels: list[str] = []
    selection_summary: dict[str, Any] = {}
    used_defaults: list[str] = []

    for index, role in enumerate(PANEL_ROLES):
        raw = raw_panels[index] if index < len(raw_panels) else {}
        raw = dict(raw) if hasattr(raw, "items") else {}
        white_hero = bool(raw.get("white_hero", index == 0))

        defaults_for_role = PANEL_LAYER_DEFAULTS.get(role, {})
        resolved_layers: dict[str, str] = {}
        for layer in LAYER_KEYS:
            raw_value = str(raw.get(layer) or "auto").strip().lower()
            explicit = raw_value in CARD_LIBRARIES[layer]
            role_default = defaults_for_role.get(layer)
            if layer == "frame" and role_default:
                preferred = raw_value if explicit else role_default
            elif white_hero and index == 0 and layer == "light" and not explicit:
                preferred = "light_studio_hero"
            else:
                preferred = raw_value if explicit else base_layers[layer]
                if preferred not in CARD_LIBRARIES[layer]:
                    preferred = role_default or "auto"
            card_id, defaulted = _resolve_selection(
                resolved_category, layer, preferred
            )
            resolved_layers[layer] = card_id
            if defaulted:
                used_defaults.append(f"panel{index + 1}:{layer}")

        if white_hero and index == 0:
            scene_id = "scene_white_sweep"
            prop_ids: list[str] = []
        else:
            raw_scene = raw.get("scene")
            scene_id = (
                _safe_scene(raw_scene)
                if raw_scene not in (None, "", "auto")
                else (default_scenes[index - 1] if 1 <= index < len(default_scenes) + 1 else default_scenes[0])
            )
            if not scene_id:
                scene_id = default_scenes[0] if default_scenes else "scene_plaster"
            raw_props = raw.get("props")
            if raw_props is None:
                prop_ids = (
                    default_props[:1]
                    if index == 1
                    else default_props[:2]
                    if index == 2
                    else []
                )
            else:
                prop_ids = _safe_props(raw_props)

        scene_text = AUX_CARDS[scene_id].prompt_en
        props_text = (
            ", ".join(AUX_CARDS[prop_id].prompt_en for prop_id in prop_ids)
            if prop_ids
            else "no companion props"
        )
        panel_parts = [
            f"PANEL {index + 1} ({PANEL_ROLE_LABELS[role]}): "
            f"Scene: {scene_text}. Props: {props_text}. "
            f"Compose: {FRAME_CARDS[resolved_layers['frame']].prompt_en}."
        ]
        for layer, layer_label, cards in (
            ("light", "Light", LIGHT_CARDS),
            ("tone", "Color", TONE_CARDS),
            ("detail", "Detail", DETAIL_CARDS),
            ("style", "Style", STYLE_CARDS),
        ):
            if resolved_layers[layer] != base_layers[layer]:
                panel_parts.append(
                    f"{layer_label}: {cards[resolved_layers[layer]].prompt_en}."
                )
        panel_prompt = " ".join(panel_parts)
        effective_panels.append(panel_prompt)
        selection_summary[f"panel{index + 1}"] = {
            "role": role,
            "scene": scene_id,
            "props": prop_ids,
            "white_hero": white_hero,
            **resolved_layers,
        }

    prompt_parts = [f"{label}: Focus: {focus}."]
    if intent:
        prompt_parts.append(intent)
    prompt_parts.extend(
        [
            base_look,
            " ".join(effective_panels),
            "Companion props are display-only and never included with the product; "
            "use at most one simple large prop, never rulers, pens or fine tools.",
            tail,
        ]
    )
    prompt = " ".join(part for part in prompt_parts if part).strip()
    word_count = len(prompt.split())
    return PanelAssembly(
        prompt=prompt,
        word_count=word_count,
        category_id=resolved_category,
        panels=tuple(effective_panels),
        selections=selection_summary,
        used_defaults=tuple(used_defaults),
        over_budget=word_count > 340,
        preset_id=resolved_preset,
    )


def render_panel_plan_override(assembly: PanelAssembly) -> str:
    """把「导演模式」逐格卡片渲染成一段高优先级的逐格改写。

    背景：2K 四宫格模板自带写死的逐格区域规划（机位、道具数量、台面），用户逐格选的
    场景/道具只会落在末尾的“建议”里，模型基本不读。这里在运行时契约之后追加一段
    显式声明优先级的短文本，让用户的选择真正生效。
    """

    panels = [str(panel).strip() for panel in assembly.panels if str(panel).strip()]
    if not panels:
        return ""
    lines = [
        "USER PANEL PLAN OVERRIDE (the operator set these per-panel cards explicitly; where the "
        "template's per-panel plan or [Scene Scenario] differs for Panels 2-4, THIS BLOCK WINS for "
        "scene, surface, props, camera and lighting):",
        *[f"- {panel}" for panel in panels],
        "- Panel 1 stays the pure-white marketplace hero. All layout, divider, fidelity, geometry, "
        "safe-margin and zero-added-text rules are unchanged.",
    ]
    return "\n".join(lines)
