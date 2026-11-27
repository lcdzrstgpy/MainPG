"""细节块的结构化描述与局部参考图裁剪。

背景：细节块原来只把营销短词（例如 ``Hardened Steel``）塞进提示词，模型既不
知道该拍哪个部位、也不知道该呈现什么机械状态，只能自由发挥——这是细节图
结构畸形的主因。这里把细节块升级为「部位 + 动作 + 构图 + 必须出现 + 禁止
出现」的结构化描述，并支持从主图裁出对应区域作为参考图，让模型照着真实
结构画而不是凭空想象。

本模块只放纯逻辑：不调用 provider、不写库、不读文件（裁剪输入来自内存）。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Mapping, Sequence

# 视觉模型返回的部位坐标基准：0-1000，左上角原点（与 marketing_gen 的标注一致）。
COORD_BASE = 1000.0
# 裁剪框边长（占主图比例）：太小拿不到上下文，太大等于没裁剪。
CROP_MIN_SIDE = 0.18
CROP_MAX_SIDE = 0.46
DEFAULT_CROP_SIDE = 0.30
# 裁剪输出上限；超过就等比缩小，避免把大图当参考图白白增加上传体积。
CROP_MAX_PIXELS = 1024

DEFAULT_FRAMING = (
    "extreme macro, three-quarter angle, the part fills about 75% of the frame, "
    "sharp focus across the whole part, clean dark studio background"
)

# 这些是「结构畸形」的典型表现，统一写成负向约束。
DEFAULT_FORBIDDEN: tuple[str, ...] = (
    "extra or missing parts",
    "merged or fused geometry",
    "melted or bent metal",
    "duplicated jaws or handles",
    "text",
    "logos",
    "watermark",
    "hands",
)


@dataclass(frozen=True)
class DetailSpec:
    """一个细节块的生成依据。``crop`` 为归一化 x0,y0,x1,y1（0..1）。"""

    feature_id: str
    part: str
    action: str = ""
    framing: str = DEFAULT_FRAMING
    must_show: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = DEFAULT_FORBIDDEN
    crop: tuple[float, float, float, float] | None = None

    @property
    def display_label(self) -> str:
        """版式里画在细节卡上的短标题（不是喂给模型的描述）。"""
        text = str(self.part or "").strip()
        if text.lower().startswith("the "):
            text = text[4:]
        if not text:
            return "Detail"
        return text[:1].upper() + text[1:]


def _slug(text: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(text or ""))
    return cleaned.strip("_")[:48] or "detail"


def spec_from_label(label: str, *, framing: str | None = None) -> DetailSpec:
    """兜底：拿不到部位坐标时，把营销短词当成部位名。

    仍然比旧实现好——提示词里会明确要求「只拍这个部位 + 列出禁止项」，
    但因为没有裁剪参考，质量不如 ``specs_from_callout_parts``。
    """
    part = str(label or "").strip() or "the working end"
    return DetailSpec(
        feature_id=_slug(part),
        part=part,
        framing=framing or DEFAULT_FRAMING,
    )


# 视觉模型没给出部位坐标时的兜底裁切：沿商品中轴自上而下取四段，
# 保证每张细节卡都有商品内容（信息图 2×2 场景卡与细节卡共用同一口径）。
FALLBACK_REGIONS: tuple[tuple[float, float, float, float], ...] = (
    (0.34, 0.02, 0.66, 0.27),
    (0.34, 0.27, 0.66, 0.52),
    (0.34, 0.52, 0.66, 0.77),
    (0.34, 0.77, 0.66, 0.98),
)


def _regions_from_bbox(
    bbox: Sequence[float], count: int
) -> list[tuple[float, float, float, float]]:
    """按商品包围盒的长轴切出 count 段（带少量重叠），保证每段都有商品。"""
    count = max(0, int(count))
    if count <= 0:
        return []
    left, top, right, bottom = (max(0.0, min(1.0, float(value))) for value in bbox)
    if right <= left or bottom <= top:
        return list(FALLBACK_REGIONS[:count])
    pad = 0.02
    left = max(0.0, left - pad)
    right = min(1.0, right + pad)
    top = max(0.0, top - pad)
    bottom = min(1.0, bottom + pad)
    width = max(0.02, right - left)
    height = max(0.02, bottom - top)
    regions: list[tuple[float, float, float, float]] = []
    if height >= width:
        step = height / count
        for index in range(count):
            y0 = top + index * step - step * 0.12
            y1 = top + (index + 1) * step + step * 0.12
            regions.append(
                (
                    round(left, 4),
                    round(max(0.0, y0), 4),
                    round(right, 4),
                    round(min(1.0, y1), 4),
                )
            )
    else:
        step = width / count
        for index in range(count):
            x0 = left + index * step - step * 0.12
            x1 = left + (index + 1) * step + step * 0.12
            regions.append(
                (
                    round(max(0.0, x0), 4),
                    round(top, 4),
                    round(min(1.0, x1), 4),
                    round(bottom, 4),
                )
            )
    return regions


def fallback_detail_specs(
    labels: Sequence[str],
    *,
    limit: int = 4,
    bbox: Sequence[float] | None = None,
) -> list[DetailSpec]:
    """部位坐标不可用时的兜底细节：真实商品区域裁切 + 文案短词做标签。

    ``bbox`` 是商品在画面中的归一化包围盒（左, 上, 右, 下）。给了就沿商品
    长轴切段（裁切跟着商品走，不再固定切整帧）；没给才用整帧中轴的固定区域。

    标签只用于版式展示；crop 模式不会把标签写进生图提示词，所以不存在
    「文案说 A、裁到 B」被模型放大的问题，最多是标签与画面不完全对应。
    """
    count = max(0, int(limit))
    regions = _regions_from_bbox(bbox, count) if bbox else list(FALLBACK_REGIONS[:count])
    specs: list[DetailSpec] = []
    for index, region in enumerate(regions[:count]):
        label = str(labels[index] if index < len(labels) else "").strip() or f"Detail {index + 1}"
        specs.append(
            DetailSpec(
                feature_id=_slug(label),
                part=label,
                must_show=(label,),
                crop=region,
            )
        )
    return specs


def _crop_around(x: float, y: float, *, side: float = DEFAULT_CROP_SIDE) -> tuple[float, float, float, float]:
    side = max(CROP_MIN_SIDE, min(CROP_MAX_SIDE, float(side)))
    half = side / 2.0
    # 0-1000 → 0..1，并把框夹在画面内（不越界）。
    cx = max(0.0, min(1.0, float(x) / COORD_BASE))
    cy = max(0.0, min(1.0, float(y) / COORD_BASE))
    x0 = max(0.0, min(1.0 - side, cx - half))
    y0 = max(0.0, min(1.0 - side, cy - half))
    return (round(x0, 4), round(y0, 4), round(x0 + side, 4), round(y0 + side, 4))


def _spec_for_part(label: str, crop: tuple[float, float, float, float] | None) -> DetailSpec:
    """部位名统一写成 ``the <part>``，保证提示词语法稳定、不因坐标是否可用而变。"""
    cleaned = str(label or "").strip()
    return DetailSpec(
        feature_id=_slug(cleaned),
        part=f"the {cleaned}",
        must_show=(cleaned,),
        crop=crop,
    )


def specs_from_callout_parts(
    parts: Sequence[Mapping[str, Any]],
    *,
    limit: int = 4,
    crop_side: float = DEFAULT_CROP_SIDE,
) -> list[DetailSpec]:
    """把视觉模型标出的部位（label + 0-1000 坐标）转成细节块描述。

    坐标非法的条目仍保留（降级为无裁剪），因为它们至少提供了真实部位名。
    """
    specs: list[DetailSpec] = []
    seen: set[str] = set()
    for item in parts:
        if len(specs) >= max(0, limit):
            break
        if not isinstance(item, Mapping):
            continue
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        try:
            x = float(item.get("x"))
            y = float(item.get("y"))
        except (TypeError, ValueError):
            specs.append(_spec_for_part(label, None))
            continue
        if not (0.0 <= x <= COORD_BASE and 0.0 <= y <= COORD_BASE):
            specs.append(_spec_for_part(label, None))
            continue
        specs.append(_spec_for_part(label, _crop_around(x, y, side=crop_side)))
    return specs


def build_detail_prompt_from_spec(spec: DetailSpec) -> str:
    """把结构化描述拼成一条短提示词（保持简短，长合同会分散模型注意力）。"""
    chunks = [
        f"Extreme macro photograph of {spec.part} on the exact product from the reference image.",
    ]
    action = str(spec.action or "").strip().rstrip(".")
    if action:
        chunks.append(f"{action}.")
    framing = str(spec.framing or "").strip().rstrip(".")
    if framing:
        chunks.append(f"Framing: {framing}.")
    if spec.must_show:
        chunks.append("Must show: " + ", ".join(str(item) for item in spec.must_show) + ".")
    forbidden = tuple(str(item) for item in spec.forbidden if str(item or "").strip())
    if forbidden:
        chunks.append("Do not show: " + ", ".join(forbidden) + ".")
    chunks.append("Photographic realism, consistent with the reference product.")
    return " ".join(chunks)


def build_detail_styled_prompt(label: str, lighting: str = "") -> str:
    """细节特写重绘提示词：同一部位的「同风格微距」，场景/光线与主图统一。

    调用方把**真实原图裁切**作为第一参考图：部位照着真图画，不会乱指；这里只负
    责换掉白底/灰底的供应商棚拍观感，统一成主图的场景氛围。明确禁止改设计、画字。
    """
    part = str(label or "product detail").strip()[:40]
    chunks = [
        f"Extreme close-up macro photograph of the {part} of the exact product "
        "shown in the reference images.",
    ]
    scene = str(lighting or "").strip().rstrip(".")
    if scene:
        chunks.append(f"Use the same scene, surface and lighting mood as: {scene}.")
    chunks.append(
        "Shallow depth of field, crisp focus on the part, rich material texture, "
        "photographic realism consistent with the reference product; keep the exact "
        "product design, colours and proportions. No text, no watermark, no logos."
    )
    return " ".join(chunks)


def detail_crop_usable(
    content: bytes,
    *,
    min_mean: float = 18.0,
    min_high: float = 60.0,
) -> bool:
    """细节卡裁切是否真的有内容：整体过黑/纯黑的裁切直接判废。

    深色营销背景下的「空卡」很常见：点位落在背景上，裁出来一片黑。这里用
    亮度均值 + 最亮值兜住；不做方差门槛，避免把纯色但明亮的商品表面误杀。
    """
    if not content:
        return False
    try:
        from io import BytesIO

        from PIL import Image, ImageStat

        with Image.open(BytesIO(content)) as opened:
            gray = opened.convert("L")
            mean = float(ImageStat.Stat(gray).mean[0])
            high = int(gray.getextrema()[1])
    except Exception:  # noqa: BLE001 - 读不出来就按可用，交给下游
        return True
    return mean >= min_mean and high >= min_high


def lift_detail_exposure(content: bytes, gamma: float = 1.35) -> bytes:
    """细节卡提亮：暗部 gamma 提升，保留高光，避免黑背景里看不清部件。"""
    if not content:
        return content
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(content)) as opened:
            image = opened.convert("RGB")
        lut = [min(255, int(round(255.0 * ((index / 255.0) ** (1.0 / gamma))))) for index in range(256)]
        image = image.point(lut * 3)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception:  # noqa: BLE001 - 提亮失败保留原图
        return content


def crop_reference_bytes(
    image_bytes: bytes,
    crop: tuple[float, float, float, float] | None,
    *,
    pad_ratio: float = 0.04,
) -> bytes:
    """从主图裁出部位区域，返回 PNG 字节。

    裁剪是「锦上添花」：任何异常都返回空 bytes，调用方回退到整图参考或重绘，
    绝不因为裁剪问题阻断出图。
    """
    if not image_bytes or not crop:
        return b""
    try:
        from PIL import Image

        with Image.open(BytesIO(image_bytes)) as source:
            image = source.convert("RGB")
        width, height = image.size
        if width < 16 or height < 16:
            return b""
        x0, y0, x1, y1 = (float(value) for value in crop)
        # 外扩一点，避免正好切掉部位边缘。
        pad_x = (x1 - x0) * float(pad_ratio)
        pad_y = (y1 - y0) * float(pad_ratio)
        box = (
            max(0, int(round((x0 - pad_x) * width))),
            max(0, int(round((y0 - pad_y) * height))),
            min(width, int(round((x1 + pad_x) * width))),
            min(height, int(round((y1 + pad_y) * height))),
        )
        if box[2] - box[0] < 8 or box[3] - box[1] < 8:
            return b""
        cropped = image.crop(box)
        longest = max(cropped.size)
        if longest > CROP_MAX_PIXELS:
            scale = CROP_MAX_PIXELS / float(longest)
            cropped = cropped.resize(
                (max(1, int(cropped.width * scale)), max(1, int(cropped.height * scale))),
                Image.LANCZOS,
            )
        buffer = BytesIO()
        cropped.save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception:  # noqa: BLE001 - 裁剪失败必须静默降级
        return b""


def png_data_url(content: bytes) -> str:
    """把 PNG 字节包成可下发/可内嵌的 data URL。"""
    if not content:
        return ""
    return "data:image/png;base64," + base64.b64encode(content).decode()


def crop_reference_data_url(
    image_bytes: bytes,
    crop: tuple[float, float, float, float] | None,
    *,
    pad_ratio: float = 0.04,
) -> str:
    """``crop_reference_bytes`` 的 data URL 包装（保留给需要 URL 的调用方）。"""
    return png_data_url(crop_reference_bytes(image_bytes, crop, pad_ratio=pad_ratio))
