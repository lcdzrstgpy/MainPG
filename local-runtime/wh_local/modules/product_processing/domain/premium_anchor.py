"""营销图「锚点出图 + 纯合成兜底」。

2026-11-23 多轮实测：让模型「重画」复杂机械产品必熔（铆钉/齿/杠杆编造）。
改为两档：

- **锚点出图**：先把原图主体抠出来（rembg，本地），钉在中性底上当唯一参考，
  命令模型「产品已定稿，只许创作环境（台面/光影/背景）」——产品是锚点不是作业。
- **纯合成兜底**：AI 还画坏时，直接把抠好的产品合成到程序生成的渐变台面上
  （compose_on_background：等比/接触阴影/曝光匹配），产品像素永远真实，
  保底产出一张可用图。

两条路都只动环境，产品几何一像素不改。
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from .background_composite import _cutout, _load_product, _subject_mask

_ANCHOR_BG = (26, 28, 32)  # 中性深灰：不给模型任何环境暗示，避免锚点底色干扰


def _naive_white_cutout(product: Image.Image) -> Image.Image | None:
    """白底/浅底产品的简易抠图（rembg 不可用时的兜底）：从四角泛洪标背景。"""
    rgb = product.convert("RGB")
    width, height = rgb.size
    corners = [(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)]
    sample = [rgb.getpixel(corner) for corner in corners]
    bg = tuple(sum(channel) // len(channel) for channel in zip(*sample))
    if min(bg) < 170:  # 背景不够浅，简易方法不可靠
        return None
    flood = rgb.copy()
    marker = (255, 0, 255)
    for corner in corners:
        if rgb.getpixel(corner) != marker:
            ImageDraw.floodfill(flood, corner, marker, thresh=48)
    mask = Image.new("L", flood.size, 255)
    mask.putdata([0 if pixel == marker else 255 for pixel in flood.getdata()])
    # 收边 + 羽化
    mask = mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1.6))
    rgba = rgb.convert("RGBA")
    rgba.putalpha(mask)
    bbox = mask.point(lambda value: 255 if value >= 128 else 0).getbbox()
    return rgba.crop(bbox) if bbox else None


def cutout_rgba(product_bytes: bytes) -> bytes | None:
    """原图主体抠出（RGBA PNG）：优先 rembg，白底图退化到泛洪抠图。"""
    product = _load_product(product_bytes)
    if product is None:
        return None
    cutout = None
    mask = _subject_mask(product)
    if mask is not None:
        cutout = _cutout(product, mask.convert("L"))
    if cutout is None:
        cutout = _naive_white_cutout(product)
    if cutout is None:
        return None
    buffer = io.BytesIO()
    cutout.save(buffer, format="PNG")
    return buffer.getvalue()


def anchor_image(product_bytes: bytes, *, side: int = 1024) -> bytes | None:
    """产品抠图钉在中性深灰底上（PNG）——给模型的「唯一参考」。"""
    cutout_bytes = cutout_rgba(product_bytes)
    if cutout_bytes is None:
        return None
    cutout = Image.open(io.BytesIO(cutout_bytes)).convert("RGBA")
    canvas = Image.new("RGBA", (side, side), (*_ANCHOR_BG, 255))
    scale = side * 0.78 / max(cutout.size)
    target = (max(1, round(cutout.width * scale)), max(1, round(cutout.height * scale)))
    resized = cutout.resize(target, Image.LANCZOS)
    canvas.alpha_composite(resized, ((side - target[0]) // 2, (side - target[1]) // 2))
    buffer = io.BytesIO()
    canvas.convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def _gradient_background(side: int, workdir: Path) -> Path:
    """程序生成的深色台面渐变底（上暗下亮的台面感 + 柔和暗角）。"""
    image = Image.new("RGB", (side, side), (18, 19, 22))
    draw = ImageDraw.Draw(image)
    horizon = int(side * 0.62)
    for y in range(side):
        # 台面（下 38%）比背景亮一点，交界柔和
        if y < horizon:
            t = y / max(1, horizon)
            value = int(16 + 10 * t)
        else:
            t = (y - horizon) / max(1, side - horizon)
            value = int(52 - 18 * t)
        draw.line([(0, y), (side, y)], fill=(value, value + 2, value + 6))
    # 暗角
    vignette = Image.new("L", (side, side), 0)
    vdraw = ImageDraw.Draw(vignette)
    margin = int(side * 0.06)
    vdraw.ellipse([-margin, -margin, side + margin, side + margin], fill=90)
    vignette = vignette.filter(ImageFilter.GaussianBlur(side * 0.12))
    dark = Image.new("RGB", (side, side), (0, 0, 0))
    image = Image.composite(image, dark, vignette.point(lambda v: min(255, v + 120)))
    path = workdir / "marketing_fallback_bg.png"
    image.save(path, format="PNG")
    return path


def fallback_composite(
    product_bytes: bytes,
    *,
    side: int = 1600,
    workdir: Path | None = None,
) -> bytes | None:
    """AI 出图失败的保底：产品抠图 + 程序台面 + 接触阴影（JPEG bytes）。

    自包含实现（不依赖 rembg 的 compose_on_background）：任何情况下都尝试给出
    一张可用图——产品像素来自原图，永不失真。
    """
    cutout_bytes = cutout_rgba(product_bytes)
    if cutout_bytes is None:
        return None
    cutout = Image.open(io.BytesIO(cutout_bytes)).convert("RGBA")
    target_dir = workdir or Path.cwd() / "outputs" / "wh-local" / "product-processing-assets"
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        background_path = _gradient_background(side, target_dir)
        canvas = Image.open(background_path).convert("RGB")
        scale = side * 0.72 / max(cutout.size)
        target = (max(1, round(cutout.width * scale)), max(1, round(cutout.height * scale)))
        resized = cutout.resize(target, Image.LANCZOS)
        x = (side - target[0]) // 2
        y = side - target[1] - int(side * 0.14)
        # 椭圆接触阴影
        shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(shadow)
        cx, base_y = x + target[0] // 2, y + target[1] - int(side * 0.012)
        sdraw.ellipse(
            [cx - int(target[0] * 0.42), base_y - int(side * 0.022),
             cx + int(target[0] * 0.42), base_y + int(side * 0.022)],
            fill=(0, 0, 0, 120),
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(side * 0.012))
        canvas = canvas.convert("RGBA")
        canvas.alpha_composite(shadow)
        canvas.alpha_composite(resized, (x, y))
        buffer = io.BytesIO()
        canvas.convert("RGB").save(buffer, format="JPEG", quality=92, subsampling=0)
        return buffer.getvalue()
    except Exception:  # noqa: BLE001
        return None
