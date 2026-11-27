"""原图版式蓝图（reference layout blueprint）。

精选处理不再只盯主图：把商品的原始轮播图 + 详情图逐张交给视觉模型，只抽取
「版式结构 + 画面质量 + 品牌元素」，再用这些蓝图驱动我们自己的生图与排版：

* 只借鉴结构、背景氛围、信息密度——**决不复用原图像素、文案和 logo**；
* 蓝图聚类后决定该商品使用哪种骨架（S1-S4）以及生图时的风格片段；
* 带 logo / 水印 / 认证标 / 尺寸表 / 大段文字的图标记为「不可复用」。

视觉模型返回 JSON；解析失败返回 ``None``，分析失败绝不能阻断出图。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Sequence

# 版式几何 → 本地排版骨架。只映射到已有骨架，不引入新的排版函数。
SKELETON_BY_LAYOUT: dict[str, str] = {
    "hero_band_bottom": "S1",
    "hero_split_side": "S2",
    "full_bleed_overlay": "S3",
    "card_centered": "S4",
    "grid_collage": "S4",
    "detail_macro": "S4",
    "flat_lay": "S4",
    "dimension_table": "S4",
}

LAYOUTS: tuple[str, ...] = tuple(SKELETON_BY_LAYOUT) + ("other",)
IMAGE_TYPES: tuple[str, ...] = (
    "main",
    "lifestyle",
    "detail",
    "infographic",
    "dimension",
    "packaging",
    "other",
)
PHOTO_ZONES: tuple[str, ...] = ("top", "bottom", "left", "right", "center", "full", "none")
TEXT_ZONES: tuple[str, ...] = ("top", "bottom", "left", "right", "center", "corner")
CARD_SHAPES: tuple[str, ...] = ("none", "rect", "rounded", "circle", "pill")

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
_ASCII_TEXT = re.compile(r"[^A-Za-z0-9 ,.'\-]")
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_WORD = re.compile(r"[a-z0-9]+")
_TRAILING_CONNECTORS = {"with", "and", "on", "in", "of", "the", "a", "an", "plus", "for"}

_SYSTEM = (
    "You are a senior e-commerce listing image art director. You analyse ONE reference "
    "listing image and describe only its LAYOUT and visual structure, so a brand-new "
    "photo of a similar product can be composed in a similarly effective style. "
    "Never ask to reuse the reference photo itself, its text, its logo or its brand."
)

_USER = """Reference image #{index} (source: {kind}) of product: "{title}".

Describe the layout as ONE JSON object with exactly these keys:
- "image_type": one of {image_types}
- "layout": one of {layouts}
- "photo_zone": where the product photo sits: one of {photo_zones}
- "text_zones": array of where printed text sits, each one of {text_zones}
- "item_count": how many separate info items (bullets/cards/circles) are printed, 0-12
- "card_shape": one of {card_shapes}
- "background": short generic English phrase for surface + colour + light (no brand names, max 12 words)
- "accent_color": dominant accent colour as "#rrggbb", or "" if none
- "quality_score": 0-10, how strong this layout is as a selling image
- "reusable": true only if the layout geometry is worth borrowing (clear zones, clean background)
- "covers_product": true if the product is clearly visible and not cropped away
- "brand_elements": array of logos / watermarks / certification badges / size charts / long claim text found, [] if none
- "notes": one short English sentence about what makes this layout work

Rules:
- Geometry only. Do not transcribe any text from the image.
- reusable must be false when the image is mostly a logo, a size chart or a text wall.
- Return ONLY the JSON object, no markdown fence, no commentary."""


@dataclass(frozen=True)
class LayoutBlueprint:
    """一张原图的版式蓝图（不含原图内容，仅结构描述）。"""

    layout: str = "other"
    image_type: str = "other"
    photo_zone: str = "center"
    text_zones: tuple[str, ...] = ()
    item_count: int = 0
    card_shape: str = "none"
    background: str = ""
    accent_color: str = ""
    quality_score: float = 0.0
    reusable: bool = False
    covers_product: bool = True
    brand_elements: tuple[str, ...] = ()
    notes: str = ""

    @property
    def skeleton(self) -> str:
        return SKELETON_BY_LAYOUT.get(self.layout, "")

    @property
    def signature(self) -> str:
        """同款版式的聚类键：几何 + 信息密度，忽略背景色等细节。"""
        return "|".join(
            (self.layout, self.photo_zone, self.card_shape, str(min(self.item_count, 4)))
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "layout": self.layout,
            "image_type": self.image_type,
            "photo_zone": self.photo_zone,
            "text_zones": list(self.text_zones),
            "item_count": self.item_count,
            "card_shape": self.card_shape,
            "background": self.background,
            "accent_color": self.accent_color,
            "quality_score": self.quality_score,
            "reusable": self.reusable,
            "covers_product": self.covers_product,
            "brand_elements": list(self.brand_elements),
            "notes": self.notes,
            "skeleton": self.skeleton,
            "signature": self.signature,
        }


def build_layout_messages(
    data_url: str,
    *,
    title: str = "",
    kind: str = "gallery",
    index: int = 1,
) -> list[dict[str, Any]]:
    """构造单张原图的版式分析消息。"""

    prompt = _USER.format(
        index=max(1, int(index)),
        kind="detail" if str(kind or "").lower() == "detail" else "gallery",
        title=(str(title or "").strip() or "the product")[:200],
        image_types=" | ".join(IMAGE_TYPES),
        layouts=" | ".join(LAYOUTS),
        photo_zones=" | ".join(PHOTO_ZONES),
        text_zones=" | ".join(TEXT_ZONES),
        card_shapes=" | ".join(CARD_SHAPES),
    )
    return [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": str(data_url or "")}},
                {"type": "text", "text": prompt},
            ],
        },
    ]


def _clean_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    text = _ASCII_TEXT.sub(" ", text)
    return " ".join(text.split())[:limit].strip()


def _choice(value: Any, allowed: Sequence[str], default: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "1"}:
            return True
        if text in {"false", "no", "0"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _string_list(value: Any, *, limit: int, item_limit: int, allowed: Sequence[str] = ()) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    items: list[str] = []
    for raw in value:
        text = (
            _choice(raw, allowed, "")
            if allowed
            else _clean_text(raw, item_limit)
        )
        if text and text not in items:
            items.append(text)
    return tuple(items[:limit])


def parse_layout_response(text: str) -> LayoutBlueprint | None:
    """解析视觉模型的 JSON；模型跑偏时返回 ``None``（调用方跳过这张图）。"""

    match = _JSON_OBJECT.search(str(text or ""))
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    accent = str(data.get("accent_color") or "").strip()
    score = max(0.0, min(10.0, _as_float(data.get("quality_score"), 0.0)))
    brand = _string_list(data.get("brand_elements"), limit=6, item_limit=40)
    return LayoutBlueprint(
        layout=_choice(data.get("layout"), LAYOUTS, "other"),
        image_type=_choice(data.get("image_type"), IMAGE_TYPES, "other"),
        photo_zone=_choice(data.get("photo_zone"), PHOTO_ZONES, "center"),
        text_zones=_string_list(
            data.get("text_zones"), limit=4, item_limit=12, allowed=TEXT_ZONES
        ),
        item_count=max(0, min(12, _as_int(data.get("item_count"), 0))),
        card_shape=_choice(data.get("card_shape"), CARD_SHAPES, "none"),
        background=_clean_text(data.get("background"), 90),
        accent_color=accent if _HEX_COLOR.match(accent) else "",
        quality_score=round(score, 1),
        reusable=_as_bool(data.get("reusable"), False),
        covers_product=_as_bool(data.get("covers_product"), True),
        brand_elements=brand,
        notes=_clean_text(data.get("notes"), 120),
    )


def usable_blueprints(blueprints: Sequence[LayoutBlueprint]) -> list[LayoutBlueprint]:
    """可借鉴的蓝图：结构可复用、商品完整、质量及格。"""

    return [
        item
        for item in blueprints
        if item.reusable and item.covers_product and item.quality_score >= 6.0
    ]


def plan_from_blueprints(
    blueprints: Sequence[LayoutBlueprint],
    *,
    fallback_order: Sequence[str],
    template_count: int = 4,
) -> dict[str, Any]:
    """把逐图蓝图汇总成该商品的排版计划（骨架顺序 + 生图风格片段）。

    结构顺序只决定「第几个版式用哪种骨架」，不会复刻原图；拿不到可用蓝图时
    完全退回 ``fallback_order``，行为与旧版本一致。
    """

    usable = usable_blueprints(blueprints)
    scores: dict[str, float] = {}
    for item in usable:
        skeleton = item.skeleton
        if not skeleton:
            continue
        bonus = 1.0 if item.card_shape in {"rect", "rounded"} else 0.0
        scores[skeleton] = scores.get(skeleton, 0.0) + item.quality_score + bonus

    order = [
        skeleton
        for skeleton, _score in sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
    ]
    for skeleton in fallback_order:
        if skeleton not in order:
            order.append(skeleton)
    for skeleton in ("S1", "S2", "S3", "S4"):
        if skeleton not in order:
            order.append(skeleton)
    order = order[: max(1, int(template_count or 4))]

    signature = ""
    if usable:
        counts: dict[str, int] = {}
        for item in usable:
            counts[item.signature] = counts.get(item.signature, 0) + 1
        signature = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[0][0]

    accent = ""
    for item in sorted(usable, key=lambda bp: -bp.quality_score):
        if item.accent_color:
            accent = item.accent_color
            break

    return {
        "skeleton_order": order,
        "preferred_skeleton": order[0] if order else "",
        "signature": signature,
        "prompt_fragment": style_fragment(usable),
        "accent_color": accent,
        "usable_count": len(usable),
        "analyzed_count": len(blueprints),
    }


def _safe_background(text: str, brand_elements: Sequence[str]) -> str:
    """背景描述进提示词前的清洗：去品牌词、去数字/百分比、去全大写商标词。"""

    brand_words = {
        word
        for item in brand_elements
        for word in _WORD.findall(str(item or "").lower())
    }
    words: list[str] = []
    for token in str(text or "").split():
        raw = token.strip(" ,.'-")
        if not raw:
            continue
        lower = raw.lower()
        if lower in brand_words:
            continue
        if any(char.isdigit() for char in raw) or "%" in raw:
            continue
        if raw.isupper() and len(raw) >= 3:
            continue
        words.append(raw)
    while words and words[-1].lower() in _TRAILING_CONNECTORS:
        words.pop()
    return " ".join(words)[:70].strip()


def style_fragment(usable: Sequence[LayoutBlueprint]) -> str:
    """生成提示词用的风格片段：只写背景氛围与卡片形状，不含任何原图文字。"""

    backgrounds: list[str] = []
    card_style = ""
    for item in sorted(usable, key=lambda bp: -bp.quality_score):
        safe_background = _safe_background(item.background, item.brand_elements)
        if safe_background and safe_background not in backgrounds:
            backgrounds.append(safe_background)
        if not card_style and item.card_shape in {"rounded", "circle", "pill"}:
            card_style = {
                "rounded": "rounded information cards",
                "circle": "circular detail badges",
                "pill": "pill-shaped feature tags",
            }[item.card_shape]
        if len(backgrounds) >= 2 and card_style:
            break

    parts: list[str] = []
    if backgrounds:
        parts.append(f"backdrop mood: {'; '.join(backgrounds[:2])}")
    if card_style:
        parts.append(card_style)
    if not parts:
        return ""
    text = (
        "Visual direction borrowed from proven listing layouts (structure only, "
        f"never copy the source photo, its text or its logo): {', '.join(parts)}."
    )
    if len(text) <= 260:
        return text
    # 按词边界截断，避免把最后一个形容词切成半截（提示词里出现 "... rounded infor"）。
    clipped = text[:260].rsplit(" ", 1)[0].rstrip(" ,;")
    return f"{clipped}."


def image_hash_distance(left: bytes, right: bytes, *, size: int = 8) -> int | None:
    """dHash 汉明距离（0 = 几乎同图）。用于确认成图没有直接复用原始摄影。

    解码失败返回 ``None``；营销图会叠字/换背景，正常距离远大于阈值，
    只有「几乎照搬原图」才会落到 0-6。
    """

    def _bits(content: bytes) -> list[int] | None:
        try:
            from io import BytesIO  # noqa: PLC0415

            from PIL import Image, ImageOps  # noqa: PLC0415

            with Image.open(BytesIO(content)) as opened:
                frame = ImageOps.exif_transpose(opened).convert("L")
                frame = frame.resize((size + 1, size), Image.Resampling.LANCZOS)
                pixels = list(frame.tobytes())
        except Exception:  # noqa: BLE001 - 哈希失败只表示无法判定
            return None
        bits: list[int] = []
        width = size + 1
        for row in range(size):
            base = row * width
            for column in range(size):
                bits.append(1 if pixels[base + column] > pixels[base + column + 1] else 0)
        return bits

    left_bits = _bits(bytes(left or b""))
    right_bits = _bits(bytes(right or b""))
    if left_bits is None or right_bits is None:
        return None
    return sum(1 for a, b in zip(left_bits, right_bits) if a != b)


def is_near_duplicate(left: bytes, right: bytes, *, max_distance: int = 6) -> bool:
    distance = image_hash_distance(left, right)
    return distance is not None and distance <= max_distance
