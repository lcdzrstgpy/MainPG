"""把 AI 出图里的商品合成到用户上传的背景底板上。

为什么不直接把底图当参考图交给生图模型：模型看到「现成背景 + 商品」的参考组合时，
会重绘商品（比例、材质、印花都会漂），Temu 白底/场景图要求商品与实物一致。
所以这里走确定性像素路径：

1. rembg 取出商品前景蒙版（复用 ``dimension_source_reuse._subject_mask``）；
2. 底板按 1:1 铺满，商品等比缩放到画面 ~72% 高（给背景留出空间，比例才不像贴纸），
   落在画面下沿的「台面」上；
3. 加一层模糊椭圆接触阴影，避免商品像贴纸浮在背景上；
4. 蒙版边缘羽化，并按底板整体亮度做轻微曝光匹配（只动 6% 以内，
   不改色相、不改商品本身颜色，保证商品保真优先）。

失败（rembg 不可用、蒙版不可信、底板读不出来）一律返回 None，由调用方保留原图。
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from .dimension_source_reuse import _subject_mask

_SUBJECT_FILL_RATIO = 0.72
_BASELINE_RATIO = 0.88
_MIN_FOREGROUND_RATIO = 0.02
_MAX_FOREGROUND_RATIO = 0.98


def compose_on_background(
    product_bytes: bytes,
    background_path: Path | str,
    *,
    side: int | None = None,
) -> bytes | None:
    """返回合成后的 JPEG bytes；不可用时返回 None（调用方保留原图）。

    ``side`` 省略时跟随输入图边长：四宫格拆出来的主图是 800²、精品模式更高，
    这里跟着原图走，保证同一批轮播图的尺寸仍然一致（不额外放大或缩小成品）。
    """

    product = _load_product(product_bytes)
    if product is None:
        return None
    if side is None:
        side = min(product.size)
    if side <= 0:
        return None
    mask = _subject_mask(product)
    if mask is None:
        return None
    mask = mask.convert("L")
    foreground_ratio = sum(mask.histogram()[128:]) / max(1, product.size[0] * product.size[1])
    if not (_MIN_FOREGROUND_RATIO <= foreground_ratio <= _MAX_FOREGROUND_RATIO):
        return None

    cutout = _cutout(product, mask)
    if cutout is None:
        return None

    background = _load_background(background_path, side)
    if background is None:
        return None

    subject = _fit_subject(cutout, side)
    canvas = background.convert("RGB")
    _paste_with_shadow(canvas, subject, side)
    rgb = _match_exposure(canvas, product)

    buffer = io.BytesIO()
    rgb.save(buffer, format="JPEG", quality=92, subsampling=0)
    return buffer.getvalue()


def _load_product(content: bytes) -> Image.Image | None:
    try:
        opened = Image.open(io.BytesIO(content))
        try:
            opened.draft("RGB", (1600, 1600))
        except Exception:
            pass
        image = opened.convert("RGB")
    except Exception:
        return None
    if max(image.size) > 1600:
        image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    return image


def _load_background(path: Path | str, side: int) -> Image.Image | None:
    try:
        opened = Image.open(Path(path))
        opened.load()
        return _center_square(opened.convert("RGB"), side)
    except Exception:
        return None


def _center_square(image: Image.Image, side: int) -> Image.Image:
    width, height = image.size
    edge = min(width, height)
    left = (width - edge) // 2
    top = (height - edge) // 2
    cropped = image.crop((left, top, left + edge, top + edge))
    if cropped.size != (side, side):
        cropped = cropped.resize((side, side), Image.Resampling.LANCZOS)
    return cropped


def _cutout(product: Image.Image, mask: Image.Image) -> Image.Image | None:
    binary = mask.point(lambda value: 255 if value >= 128 else 0)
    bbox = binary.getbbox()
    if not bbox:
        return None
    # 边缘羽化：硬边蒙版直接贴会留下锯齿和「剪纸」边。
    feather = max(1, round(min(product.size) * 0.004))
    alpha = mask.filter(ImageFilter.GaussianBlur(feather))
    rgba = product.convert("RGBA")
    rgba.putalpha(alpha)
    return rgba.crop(bbox)


def _fit_subject(cutout: Image.Image, side: int) -> Image.Image:
    max_side = max(1, round(side * _SUBJECT_FILL_RATIO))
    width, height = cutout.size
    scale = min(max_side / max(1, width), max_side / max(1, height))
    return cutout.resize(
        (max(1, round(width * scale)), max(1, round(height * scale))),
        Image.Resampling.LANCZOS,
    )


def _paste_with_shadow(canvas: Image.Image, subject: Image.Image, side: int) -> None:
    width, height = subject.size
    baseline = round(side * _BASELINE_RATIO)
    left = (side - width) // 2
    top = max(0, baseline - height)

    shadow = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    draw = ImageDraw.Draw(shadow)
    # 椭圆形接触阴影：只铺在商品底部，宽度略大于商品，高度按商品高的 6% 控制。
    draw.ellipse(
        (
            left - round(width * 0.04),
            baseline - round(height * 0.03),
            left + width + round(width * 0.04),
            baseline + round(height * 0.035),
        ),
        fill=(0, 0, 0, 96),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(2, round(side * 0.012))))
    canvas.paste(shadow, (0, 0), shadow)

    canvas.paste(subject, (left, top), subject)


def _match_exposure(canvas: Image.Image, product: Image.Image) -> Image.Image:
    """按底板亮度轻微校正商品曝光，避免「亮商品贴暗背景」。"""

    background_luma = _mean_luma(canvas)
    product_luma = _mean_luma(product)
    if background_luma <= 0 or product_luma <= 0:
        return canvas
    factor = min(1.06, max(0.94, (background_luma + 18) / (product_luma + 18)))
    if abs(factor - 1.0) < 0.005:
        return canvas
    grey = Image.new("RGB", canvas.size, (round(255 * factor),) * 3)
    return ImageChops.multiply(canvas, grey)


def _mean_luma(image: Image.Image) -> float:
    histogram = image.convert("L").resize((32, 32), Image.Resampling.BOX).histogram()
    total = sum(histogram)
    if not total:
        return 0.0
    return sum(index * count for index, count in enumerate(histogram)) / total
