"""精选处理的排版层：把 AI 照片层 + 英文文案合成为 1:1 营销图。

双层合成的「第二层」。照片层由 Seedream 生成（提示词里禁模型渲染文字），
这里用 PIL **确定性**画标题/副文案/装饰 —— 像素级干净，永远不会拼错。

两版式（一期）：
- ``headline_hero``（大字卖点）：顶部居中大标题 + 橙色强调条 + 副文案。
- ``action_proof``（场景动作）：左上标题块 + 渐变遮罩 + 副文案，照片是使用动作。

画布 1200×1200（1:1）。上传链路会再压到 800×800，排版在 1200 上做留足精度。
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageDraw, ImageFilter, ImageFont


CANVAS_SIZE = 1200

# 文案禁止的绝对化用语（营销合规：广告法/平台规则）。命中即删词，不拦整条。
BANNED_WORDS = (
    "best",
    "no.1",
    "no1",
    "number one",
    "#1",
    "top rated",
    "world's",
    "guaranteed",
    "100%",
    "perfect",
    "amazing",
    "unbeatable",
)

_FONT_CANDIDATES_BOLD = (
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\msyhbd.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)
_FONT_CANDIDATES_REGULAR = (
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\msyh.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


@dataclass
class MarketingCopy:
    """排版层的文案。headline 允许用换行控制断行（1~2 行最佳）。"""

    headline: str
    subline: str = ""


def filter_copy(text: str) -> str:
    """去掉绝对化用语（保留其余内容；大小写不敏感）。"""
    result = str(text or "")
    lowered = result.lower()
    for word in BANNED_WORDS:
        index = lowered.find(word)
        while index >= 0:
            result = result[:index] + result[index + len(word) :]
            lowered = result.lower()
            index = lowered.find(word)
    # 词被删掉后可能留下双空格 / 以标点开头
    result = " ".join(result.split())
    return result.strip(" ,.;:")


def _font(size: int, *, bold: bool) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = _FONT_CANDIDATES_BOLD if bold else _FONT_CANDIDATES_REGULAR
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # 旧版 Pillow 无 size 参数
        return ImageFont.load_default()


def _square_photo(photo: bytes, size: int) -> Image.Image:
    """居中裁成正方形并缩放到画布尺寸。"""
    image = Image.open(BytesIO(photo)).convert("RGB")
    width, height = image.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    image = image.crop((left, top, left + side, top + side))
    return image.resize((size, size), Image.LANCZOS)


def _scrim(image: Image.Image, *, top_ratio: float = 0.42) -> Image.Image:
    """顶部渐变遮罩：保证白字在任何照片上都读得清。"""
    overlay = Image.new("L", image.size, 0)
    draw = ImageDraw.Draw(overlay)
    height = int(image.size[1] * top_ratio)
    for y in range(height):
        # 顶 0 不透明（黑遮罩 ~150）→ 渐隐到 0
        alpha = int(150 * (1 - y / max(1, height)) ** 1.4)
        draw.line([(0, y), (image.size[0], y)], fill=alpha)
    return _apply_scrim(image, overlay)


def _apply_scrim(image: Image.Image, alpha_mask: Image.Image) -> Image.Image:
    black = Image.new("RGB", image.size, (8, 10, 13))
    base = image.copy()
    base.paste(black, (0, 0), alpha_mask)
    return base


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    """按像素宽度折行；尊重显式换行。"""
    lines: list[str] = []
    for paragraph in str(text or "").split("\n"):
        current = ""
        for word in paragraph.split():
            candidate = f"{current} {word}".strip()
            if draw.textlength(candidate, font=font) <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines


def _fit_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    base_size: int,
    bold: bool,
    max_width: int,
    max_lines: int = 2,
    min_size: int = 30,
) -> tuple[list[str], ImageFont.ImageFont]:
    """自动缩字号让文案在 max_lines 行内放下——不截断任何单词。"""
    size = base_size
    while size > min_size:
        font = _font(size, bold=bold)
        lines = _wrap(draw, text, font, max_width)
        if len(lines) <= max_lines:
            return lines, font
        size = int(size * 0.88)
    font = _font(min_size, bold=bold)
    return _wrap(draw, text, font, max_width)[:max_lines], font


def _draw_headline_hero(image: Image.Image, copy: MarketingCopy) -> Image.Image:
    """大字卖点：顶部居中大标题 + 橙色强调条 + 副文案。"""
    draw = ImageDraw.Draw(image)
    width = image.size[0]
    headline_lines, headline_font = _fit_lines(
        draw, copy.headline, base_size=int(width * 0.072), bold=True,
        max_width=int(width * 0.86), max_lines=2,
    )
    sub_lines, sub_font = _fit_lines(
        draw, copy.subline, base_size=int(width * 0.036), bold=True,
        max_width=int(width * 0.82), max_lines=2, min_size=22,
    )

    y = int(width * 0.075)
    for line in headline_lines:
        line_width = draw.textlength(line, font=headline_font)
        x = (width - line_width) // 2
        # 轻投影保证任何背景下可读
        draw.text((x + 3, y + 3), line, font=headline_font, fill=(24, 26, 30))
        draw.text((x, y), line, font=headline_font, fill=(255, 255, 255))
        y += int(width * 0.088)

    # 橙色强调条
    bar_width = int(width * 0.18)
    bar_y = y + int(width * 0.012)
    draw.rounded_rectangle(
        [(width - bar_width) // 2, bar_y, (width + bar_width) // 2, bar_y + int(width * 0.012)],
        radius=int(width * 0.006),
        fill=(255, 122, 47),
    )
    y = bar_y + int(width * 0.034)

    for line in sub_lines:
        line_width = draw.textlength(line, font=sub_font)
        x = (width - line_width) // 2
        draw.text((x + 2, y + 2), line, font=sub_font, fill=(24, 26, 30))
        draw.text((x, y), line, font=sub_font, fill=(235, 238, 242))
        y += int(width * 0.05)
    return image


def _draw_action_proof(image: Image.Image, copy: MarketingCopy) -> Image.Image:
    """场景动作：左上标题块 + 副文案（照片本身在下方/右侧）。"""
    draw = ImageDraw.Draw(image)
    width = image.size[0]
    x0 = int(width * 0.07)
    headline_lines, headline_font = _fit_lines(
        draw, copy.headline, base_size=int(width * 0.058), bold=True,
        max_width=int(width * 0.8), max_lines=2,
    )
    sub_lines, sub_font = _fit_lines(
        draw, copy.subline, base_size=int(width * 0.032), bold=True,
        max_width=int(width * 0.74), max_lines=2, min_size=22,
    )

    y = int(width * 0.07)
    for line in headline_lines:
        draw.text((x0 + 3, y + 3), line, font=headline_font, fill=(24, 26, 30))
        draw.text((x0, y), line, font=headline_font, fill=(255, 255, 255))
        y += int(width * 0.072)

    bar_y = y + int(width * 0.008)
    draw.rectangle([(x0, bar_y), (x0 + int(width * 0.09), bar_y + int(width * 0.009))], fill=(255, 122, 47))
    y = bar_y + int(width * 0.026)

    for line in sub_lines:
        draw.text((x0 + 2, y + 2), line, font=sub_font, fill=(24, 26, 30))
        draw.text((x0, y), line, font=sub_font, fill=(235, 238, 242))
        y += int(width * 0.044)
    return image


_TEMPLATES = {
    "headline_hero": _draw_headline_hero,
    "action_proof": _draw_action_proof,
}

TEMPLATES = tuple(_TEMPLATES)


def compose_marketing_image(
    photo: bytes,
    template: str,
    copy: MarketingCopy,
    *,
    canvas_size: int = CANVAS_SIZE,
) -> bytes:
    """照片层 + 文案 → 1:1 营销图（JPEG bytes）。"""
    drawer = _TEMPLATES.get(str(template or ""))
    if drawer is None:
        raise ValueError(f"未知版式：{template}（可选：{'、'.join(TEMPLATES)}）")
    image = _square_photo(photo, canvas_size)
    image = _scrim(image)
    copy = MarketingCopy(headline=filter_copy(copy.headline), subline=filter_copy(copy.subline))
    if not copy.headline:
        raise ValueError("文案标题为空")
    image = drawer(image, copy)
    image = image.filter(ImageFilter.UnsharpMask(radius=1.2, percent=60, threshold=3))
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()
