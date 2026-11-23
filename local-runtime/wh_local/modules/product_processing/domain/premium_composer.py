"""精选处理的排版层：把 AI 照片层 + 英文文案合成为 1:1 营销图。

双层合成的「第二层」。照片层由 Seedream 生成（提示词里禁模型渲染文字），
这里用 PIL **确定性**画标题/副文案/装饰 —— 像素级干净，永远不会拼错。

两版式（一期）：
- ``headline_hero``（大字卖点）：顶部居中大标题 + 橙色强调条 + 副文案。
- ``action_proof``（场景动作）：左上标题块 + 渐变遮罩 + 副文案，照片是使用动作。

画布 1600×1600（1:1，精品档）。上传链路会再压到 800×800，排版放大做留足精度。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO

from PIL import Image, ImageDraw, ImageFilter, ImageFont


CANVAS_SIZE = 1600

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
    """排版层的文案。headline 允许用换行控制断行（1~2 行最佳）。

    bullets 用于图标网格/材质条/场景应用：各 1~3 个短词（英文）。
    """

    headline: str
    subline: str = ""
    bullets: list[str] = field(default_factory=list)


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



# ── 通用绘制件：半透明圆角底 + 简单矢量图标（不依赖外部图标库） ──────────────

_CHIP_FILL = (16, 18, 22, 205)
_CHIP_EDGE = (255, 255, 255, 40)
_ACCENT = (255, 122, 47, 255)


def _with_overlay(image: Image.Image, painter) -> Image.Image:
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    painter(ImageDraw.Draw(overlay))
    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")


def _icon(draw: ImageDraw.ImageDraw, x: float, y: float, size: float, index: int) -> None:
    """在 (x,y) 画 size 见方的圆底图标；index 决定图形（check/shield/sparkle/droplet/gear/ruler）。"""
    draw.ellipse([x, y, x + size, y + size], fill=_ACCENT)
    color = (255, 255, 255, 255)
    cx, cy = x + size / 2, y + size / 2
    lw = max(2, int(size // 12))
    kind = index % 6
    if kind == 0:  # 对勾
        draw.line([(x + size * 0.28, cy), (cx - size * 0.02, y + size * 0.68)], fill=color, width=lw)
        draw.line([(cx - size * 0.02, y + size * 0.68), (x + size * 0.74, y + size * 0.34)], fill=color, width=lw)
    elif kind == 1:  # 盾形
        draw.polygon(
            [(cx, y + size * 0.24), (x + size * 0.74, y + size * 0.38), (x + size * 0.66, y + size * 0.7),
             (cx, y + size * 0.8), (x + size * 0.34, y + size * 0.7), (x + size * 0.26, y + size * 0.38)],
            outline=color, width=lw,
        )
    elif kind == 2:  # 星光
        draw.polygon(
            [(cx, y + size * 0.24), (cx + size * 0.08, cy - size * 0.06), (x + size * 0.74, cy),
             (cx + size * 0.08, cy + size * 0.06), (cx, y + size * 0.76), (cx - size * 0.08, cy + size * 0.06),
             (x + size * 0.26, cy), (cx - size * 0.08, cy - size * 0.06)],
            fill=color,
        )
    elif kind == 3:  # 水滴
        draw.ellipse([cx - size * 0.16, cy - size * 0.04, cx + size * 0.16, cy + size * 0.24], fill=color)
        draw.polygon([(cx, y + size * 0.28), (cx - size * 0.15, cy + size * 0.1), (cx + size * 0.15, cy + size * 0.1)], fill=color)
    elif kind == 4:  # 齿轮
        draw.ellipse([cx - size * 0.2, cy - size * 0.2, cx + size * 0.2, cy + size * 0.2], outline=color, width=lw)
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            draw.line([(cx + dx * size * 0.24, cy + dy * size * 0.24), (cx + dx * size * 0.36, cy + dy * size * 0.36)], fill=color, width=lw)
    else:  # 尺
        draw.rectangle([x + size * 0.26, cy - size * 0.1, x + size * 0.74, cy + size * 0.1], outline=color, width=lw)
        for step in range(1, 4):
            px = x + size * 0.26 + size * 0.48 * step / 4
            draw.line([(px, cy - size * 0.1), (px, cy - size * 0.01)], fill=color, width=lw)


def _bullets(copy: MarketingCopy, count: int) -> list[str]:
    """短词列表：不足用 subline 切分兜底；都为空则返回空表（版式退化为 3 个 chip）。"""
    items = [filter_copy(item).strip() for item in (copy.bullets or []) if str(item or "").strip()]
    items = [item for item in items if item][:count]
    if not items and copy.subline:
        words = filter_copy(copy.subline)
        items = [" ".join(words.split()[:2])][:count]
    return items


def _draw_icon_grid(image: Image.Image, copy: MarketingCopy) -> Image.Image:
    """功能图标网格：顶部标题 + 左下 2×3 图标短词卡片。"""
    width, height = image.size
    draw = ImageDraw.Draw(image)
    headline_lines, headline_font = _fit_lines(
        draw, copy.headline, base_size=int(width * 0.060), bold=True,
        max_width=int(width * 0.88), max_lines=2,
    )
    y = int(height * 0.06)
    for line in headline_lines:
        line_width = draw.textlength(line, font=headline_font)
        x = (width - line_width) // 2
        draw.text((x + 3, y + 3), line, font=headline_font, fill=(24, 26, 30))
        draw.text((x, y), line, font=headline_font, fill=(255, 255, 255))
        y += int(height * 0.072)

    labels = _bullets(copy, 6)
    if not labels:
        labels = ["Durable", "Precise", "Comfort Grip"]
    label_font = _font(int(width * 0.026), bold=True)
    chip_w = int(width * 0.40)
    chip_h = int(height * 0.100)
    gap = int(height * 0.018)
    left = int(width * 0.055)
    top = int(height * 0.60)
    rows = (len(labels) + 1) // 2

    def paint(painter: ImageDraw.ImageDraw) -> None:
        for index, label in enumerate(labels[:6]):
            col = index % 2
            row = index // 2
            cx0 = left + col * (chip_w + gap)
            cy0 = top + row * (chip_h + gap)
            painter.rounded_rectangle(
                [cx0, cy0, cx0 + chip_w, cy0 + chip_h],
                radius=int(chip_h * 0.32), fill=_CHIP_FILL, outline=_CHIP_EDGE, width=1,
            )
            icon_size = int(chip_h * 0.62)
            _icon(painter, cx0 + int(chip_h * 0.19), cy0 + (chip_h - icon_size) // 2, icon_size, index)
            text = label[:26]
            painter.text(
                (cx0 + int(chip_h * 0.19) * 2 + icon_size + 6, cy0 + chip_h / 2),
                text, font=label_font, fill=(240, 242, 245, 255), anchor="lm",
            )

    return _with_overlay(image, paint)


def _draw_material_strip(image: Image.Image, copy: MarketingCopy) -> Image.Image:
    """材质/适用条：顶部标题 + 底部一排材质短词药丸。"""
    width, height = image.size
    base = _draw_action_proof(image, copy)
    draw = ImageDraw.Draw(base)
    labels = _bullets(copy, 4)
    if not labels:
        return base
    chip_font = _font(int(width * 0.028), bold=True)
    chip_h = int(height * 0.072)
    gaps = int(width * 0.014)
    total_w = width - int(width * 0.14)
    chip_w = int((total_w - gaps * (len(labels) - 1)) / len(labels))
    left = int(width * 0.07)
    top = int(height * 0.82)

    def paint(painter: ImageDraw.ImageDraw) -> None:
        for index, label in enumerate(labels):
            x0 = left + index * (chip_w + gaps)
            painter.rounded_rectangle(
                [x0, top, x0 + chip_w, top + chip_h],
                radius=chip_h // 2, fill=_CHIP_FILL, outline=_CHIP_EDGE, width=1,
            )
            painter.text(
                (x0 + chip_w / 2, top + chip_h / 2),
                label[:18], font=chip_font, fill=(240, 242, 245, 255), anchor="mm",
            )

    return _with_overlay(base, paint)


def _draw_use_cases(image: Image.Image, copy: MarketingCopy) -> Image.Image:
    """场景应用：顶部标题 + 产品四周四个应用短词圆环。"""
    width, height = image.size
    base = _draw_headline_hero(image, copy)
    labels = _bullets(copy, 4)
    if not labels:
        return base
    card_font = _font(int(width * 0.024), bold=True)
    card_w = int(width * 0.30)
    card_h = int(height * 0.092)
    positions = [(0.05, 0.55), (0.65, 0.55), (0.05, 0.72), (0.65, 0.72)]

    def paint(painter: ImageDraw.ImageDraw) -> None:
        for index, label in enumerate(labels):
            rx, ry = positions[index]
            x0, y0 = int(width * rx), int(height * ry)
            painter.rounded_rectangle(
                [x0, y0, x0 + card_w, y0 + card_h],
                radius=int(card_h * 0.3), fill=_CHIP_FILL, outline=_CHIP_EDGE, width=1,
            )
            icon_size = int(card_h * 0.58)
            _icon(painter, x0 + int(card_h * 0.2), y0 + (card_h - icon_size) // 2, icon_size, index + 2)
            painter.text(
                (x0 + int(card_h * 0.2) * 2 + icon_size + 6, y0 + card_h / 2),
                label[:16], font=card_font, fill=(240, 242, 245, 255), anchor="lm",
            )

    return _with_overlay(base, paint)


_TEMPLATES = {
    "headline_hero": _draw_headline_hero,
    "action_proof": _draw_action_proof,
    "icon_grid": _draw_icon_grid,
    "material_strip": _draw_material_strip,
    "use_cases": _draw_use_cases,
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
    copy = MarketingCopy(
        headline=filter_copy(copy.headline),
        subline=filter_copy(copy.subline),
        bullets=[filter_copy(item) for item in (copy.bullets or [])],
    )
    if not copy.headline:
        raise ValueError("文案标题为空")
    image = drawer(image, copy)
    image = image.filter(ImageFilter.UnsharpMask(radius=1.2, percent=60, threshold=3))
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()
