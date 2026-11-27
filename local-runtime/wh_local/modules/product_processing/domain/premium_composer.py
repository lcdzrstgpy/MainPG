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


# 分区版式：整页底色 = 主图自身的模糊提亮版（照片背景自然延伸，不露方块接缝），
# 清晰照片是圆角卡片。值 = (照片顶部, 照片边长) 占画布比例。
ZONE_SPECS: dict[str, tuple[float, float]] = {
    "material_strip": (0.22, 0.55),
    "use_cases": (0.21, 0.45),
}

# 「上图下信息带」版式：照片铺满上部，文案/标签放进底部白色信息带。
# 顶部最多裁 18%（商品上方通常只有空墙），不足的高度从底部补，保证商品上下都不切。
# 成图终检发现商品被裁时，调用方会传 ``contain=True`` 改为整图完整缩放。
BAND_TOP_CROP = 0.18
ICON_GRID_BAND = 0.20
FEATURE_STRIP_BAND = 0.22

# 分区版式的浅色卡片：白底 + 浅暖灰描边 + 深色字，避免黑色块压得又重又土。
_LIGHT_CARD_FILL = (255, 255, 255)
_LIGHT_CARD_EDGE = (229, 223, 213)
_LIGHT_TEXT = (28, 30, 34)


def _square(source: Image.Image) -> Image.Image:
    """居中裁成正方形（生图层是 1:1，这里只是防御）。"""

    if source.width == source.height:
        return source
    side = min(source.size)
    sx = (source.width - side) // 2
    sy = (source.height - side) // 2
    return source.crop((sx, sy, sx + side, sy + side))


def _cover_resize(source: Image.Image, width: int, height: int) -> Image.Image:
    """等比放大到铺满目标框，再居中裁切（不拉伸）。"""

    scale = max(width / max(1, source.width), height / max(1, source.height))
    resized = source.resize(
        (max(width, int(round(source.width * scale))), max(height, int(round(source.height * scale)))),
        Image.LANCZOS,
    )
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def _contain_band_photo(source: Image.Image, width: int, height: int) -> Image.Image:
    """商品太高、怎么裁都会切时：整图完整缩放，两侧用同源模糊底柔边融入。"""

    background = _cover_resize(source, width, height).filter(
        ImageFilter.GaussianBlur(max(6, int(width * 0.035)))
    )
    side = min(width, height)
    foreground = source.resize((side, side), Image.LANCZOS)
    left = (width - side) // 2
    top = (height - side) // 2
    # 两侧各 6% 宽度做透明渐变，照片与模糊底之间不留硬边。
    feather = max(8, int(side * 0.06))
    mask = Image.new("L", (side, side), 255)
    pixels = mask.load()
    for x in range(feather):
        alpha = int(255 * (x / float(feather)))
        for y in range(side):
            if pixels[x, y] > alpha:
                pixels[x, y] = alpha
            if pixels[side - 1 - x, y] > alpha:
                pixels[side - 1 - x, y] = alpha
    background.paste(foreground, (left, top), mask)
    return background


def crop_band_photo(
    photo: bytes, band_ratio: float, width: int, *, contain: bool = False
) -> Image.Image:
    """把 1:1 主图裁成「上图 + 下信息带」版式需要的高度。

    ``contain=False``（默认）：只裁商品上方的空白（顶部最多 18%），剩余高度
    从底部补足——直接居中裁切会把商品底边切掉（生图里商品通常压得比较低）。
    ``contain=True``：成图终检发现商品被裁时的兜底，整图完整缩放 + 同源模糊底
    柔边（宁可有侧边虚化，也不切商品）。

    返回宽度 = ``width``、高度 = ``width * (1 - band_ratio)`` 的照片。
    """

    source = _square(Image.open(BytesIO(photo)).convert("RGB"))
    band = max(0.0, min(0.5, float(band_ratio)))
    height = max(1, int(round(width * (1 - band))))
    if contain:
        return _contain_band_photo(source, width, height)
    top = min(BAND_TOP_CROP, band)
    bottom = max(0.0, band - top)
    src_w, src_h = source.size
    source = source.crop(
        (0, int(round(src_h * top)), src_w, int(round(src_h * (1 - bottom))))
    )
    return source.resize((width, height), Image.LANCZOS)


def band_photo_bytes(
    photo: bytes, band_ratio: float, width: int, *, contain: bool = False
) -> bytes:
    """``crop_band_photo`` 的 JPEG 字节版（HTML 排版直接内嵌 data URL）。"""

    image = crop_band_photo(photo, band_ratio, width, contain=contain)
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def content_bottom_ratio(photo: bytes) -> float:
    """估算商品在画面中的底边位置（0-1）。

    只识别「明显是商品」的像素：深色（亮度 < 0.60）或高饱和（> 0.38）。
    浅色商品配浅底时识别不到，返回 1.0 表示「不限制」——宁可不管，也不误裁。
    用途：信息带高度自适应，避免把商品底边切掉。
    """

    with Image.open(BytesIO(photo)) as image:
        small = image.convert("RGB").resize((160, 160), Image.LANCZOS)
    pixels = small.load()

    def is_content(r: int, g: int, b: int) -> bool:
        high = max(r, g, b)
        low = min(r, g, b)
        lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255
        sat = 0.0 if high == 0 else (high - low) / high
        return lum < 0.60 or sat > 0.38

    for y in range(159, 8, -1):
        hits = 0
        total = 0
        for x in range(16, 145, 3):
            total += 1
            if is_content(*pixels[x, y]):
                hits += 1
        if total and hits / total >= 0.04:
            return y / 159
    return 1.0


def resolve_band_ratio(photo: bytes, desired: float) -> float:
    """把期望信息带高度限制在商品底边之上；商品底边过低时自动缩小信息带。

    下限 26%：再小就放不下 84px 标题 + 36px 标签（大字号规格）；商品底边压得
    很低时宁可切掉几像素底边，也不能把字缩回不可读。
    """

    band = max(0.26, min(0.38, float(desired)))
    bottom = content_bottom_ratio(photo)
    if bottom >= 0.999:
        return band
    allowed = 1.0 - bottom - 0.015
    return max(0.26, min(band, allowed))


def content_bbox_ratios(photo: bytes) -> tuple[float, float, float, float] | None:
    """估算商品在画面中的包围盒（left, top, right, bottom，0-1）。

    与 ``content_bottom_ratio`` 同一套「深色/高饱和即商品」启发式。识别不到
    （浅色商品配浅底）返回 None，调用方按居中处理。用途：S2/S4 的照片裁切
    位置按商品真实中心对齐，而不是一律居中。
    """

    with Image.open(BytesIO(photo)) as image:
        small = image.convert("RGB").resize((160, 160), Image.LANCZOS)
    pixels = small.load()

    def is_content(r: int, g: int, b: int) -> bool:
        high = max(r, g, b)
        low = min(r, g, b)
        lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255
        sat = 0.0 if high == 0 else (high - low) / high
        return lum < 0.60 or sat > 0.38

    left, top, right, bottom = 160, 160, -1, -1
    hits = 0
    for y in range(8, 152, 2):
        for x in range(8, 152, 2):
            if not is_content(*pixels[x, y]):
                continue
            hits += 1
            left = min(left, x)
            top = min(top, y)
            right = max(right, x)
            bottom = max(bottom, y)
    if hits < 20 or right <= left or bottom <= top:
        return None
    return (left / 159, top / 159, right / 159, bottom / 159)


def band_photo_bytes_safe(
    photo: bytes, desired: float, width: int, *, contain: bool = False
) -> tuple[bytes, float]:
    """返回 (照片字节, 实际信息带比例)，保证排版用的高度和裁切结果一致。"""

    band = resolve_band_ratio(photo, desired)
    return band_photo_bytes(photo, band, width, contain=contain), band



def _soft_page(source: Image.Image, canvas: int) -> Image.Image:
    """整页底：主图自身高斯模糊 + 大幅提白，形成柔雾浅色底。

    比「取边缘中位色纯色铺底」可靠得多——照片本身有渐变和窗光时，
    纯色底一定会露出方块边界；模糊底永远与照片同源、无缝衔接。
    """

    page = _square(source).resize((canvas, canvas), Image.LANCZOS)
    page = page.filter(ImageFilter.GaussianBlur(max(8, int(canvas * 0.038))))
    return Image.blend(page, Image.new("RGB", (canvas, canvas), (255, 255, 255)), 0.80)


def _paste_card(
    base: Image.Image, photo: Image.Image, box: tuple[int, int, int, int], radius: int
) -> None:
    """把照片以圆角卡片（带柔和投影）贴到整页底上。"""

    x0, y0, x1, y1 = box
    card_w, card_h = x1 - x0, y1 - y0
    tile = photo.resize((card_w, card_h), Image.LANCZOS)
    mask = Image.new("L", (card_w, card_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, card_w - 1, card_h - 1], radius=radius, fill=255
    )
    shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    offset = max(4, radius // 4)
    ImageDraw.Draw(shadow).rounded_rectangle(
        [x0 + offset, y0 + offset * 2, x1 + offset, y1 + offset * 2],
        radius=radius, fill=(38, 30, 20, 78),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(6, radius // 2)))
    composed = Image.alpha_composite(base.convert("RGBA"), shadow).convert("RGB")
    composed.paste(tile, (x0, y0), mask)
    base.paste(composed)


def _zoned_canvas(
    photo: bytes, canvas: int, *, top_ratio: float, size_ratio: float
) -> Image.Image:
    """模糊提亮整页底 + 居中圆角照片卡（正方形，与生图同比例，不裁商品）。"""

    source = _square(Image.open(BytesIO(photo)).convert("RGB"))
    base = _soft_page(source, canvas)
    side = int(canvas * size_ratio)
    left = (canvas - side) // 2
    top = int(canvas * top_ratio)
    _paste_card(base, source, (left, top, left + side, top + side), radius=int(canvas * 0.024))
    return base


def _draw_light_title(
    image: Image.Image,
    copy: MarketingCopy,
    *,
    align: str = "left",
    base_ratio: float = 0.056,
    max_lines: int = 2,
) -> Image.Image:
    """照片背景延伸底色上的标题：浅底用深字、深底用白字（分区版式专用）。"""

    width, height = image.size
    draw = ImageDraw.Draw(image)
    corner = image.getpixel((4, 4))
    luminance = 0.299 * corner[0] + 0.587 * corner[1] + 0.114 * corner[2]
    text_fill = _LIGHT_TEXT if luminance > 150 else (255, 255, 255)
    lines, font = _fit_lines(
        draw, copy.headline, base_size=int(width * base_ratio), bold=True,
        max_width=int(width * 0.88), max_lines=max_lines,
    )
    y = int(height * 0.05)
    for line in lines:
        line_width = draw.textlength(line, font=font)
        x = (width - line_width) // 2 if align == "center" else int(width * 0.06)
        draw.text((x, y), line, font=font, fill=text_fill)
        y += int(font.size * 1.08) if hasattr(font, "size") else int(height * 0.06)
    draw.rectangle(
        [
            (int(width * 0.06) if align != "center" else (width - int(width * 0.09)) // 2, y + 6),
            (
                (int(width * 0.06) if align != "center" else (width - int(width * 0.09)) // 2)
                + int(width * 0.09),
                y + 6 + int(height * 0.008),
            ),
        ],
        fill=(255, 122, 47),
    )
    return image


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


def _fit_single_line_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    base_size: int,
    max_width: int,
    bold: bool = True,
    min_size: int = 16,
) -> ImageFont.ImageFont:
    """单行标签按可用宽度缩字号：宁可小一点，也不能被画布边缘切掉。

    底部圆环/卡片标签原来用固定字号 + 居中绘制，长标签（如
    "Complete 3-Piece Set"）会溢出到画布外，看起来像「滑档」。
    """
    size = max(min_size, int(base_size))
    while size > min_size:
        font = _font(size, bold=bold)
        if draw.textlength(text, font=font) <= max_width:
            return font
        size = int(size * 0.92)
    return _font(min_size, bold=bold)


def _fit_label_to_cell(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    base_size: int,
    max_width: int,
    bold: bool = True,
    min_size: int = 16,
) -> tuple[str, ImageFont.ImageFont]:
    """标签先缩字号；缩到最小仍放不下才截断（保证绝不越界）。"""
    label = str(text or "").strip()
    font = _fit_single_line_font(
        draw, label, base_size=base_size, max_width=max_width, bold=bold, min_size=min_size
    )
    if draw.textlength(label, font=font) <= max_width:
        return label, font
    clipped = label
    while clipped and draw.textlength(clipped + "…", font=font) > max_width:
        clipped = clipped[:-1]
    return (clipped + "…") if clipped else label, font


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


def _draw_icon_grid(image: Image.Image, copy: MarketingCopy, *, light: bool = False) -> Image.Image:
    """功能图标网格：顶部标题 + 下方 2×3 图标短词卡片。"""
    width, height = image.size
    draw = ImageDraw.Draw(image)
    if light:
        _draw_light_title(image, copy, align="center", base_ratio=0.056)
    else:
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
    label_font = _font(int(width * (0.024 if light else 0.026)), bold=True)
    chip_w = int(width * 0.40)
    chip_h = int(height * (0.060 if light else 0.100))
    gap = int(height * (0.012 if light else 0.018))
    left = int(width * 0.055)
    top = int(height * (0.76 if light else 0.60))
    chip_fill = _LIGHT_CARD_FILL if light else _CHIP_FILL
    chip_edge = _LIGHT_CARD_EDGE if light else _CHIP_EDGE
    text_fill = _LIGHT_TEXT if light else (240, 242, 245, 255)

    def paint(painter: ImageDraw.ImageDraw) -> None:
        for index, label in enumerate(labels[:6]):
            col = index % 2
            row = index // 2
            cx0 = left + col * (chip_w + gap)
            cy0 = top + row * (chip_h + gap)
            painter.rounded_rectangle(
                [cx0, cy0, cx0 + chip_w, cy0 + chip_h],
                radius=int(chip_h * 0.32), fill=chip_fill, outline=chip_edge, width=1,
            )
            icon_size = int(chip_h * 0.62)
            _icon(painter, cx0 + int(chip_h * 0.19), cy0 + (chip_h - icon_size) // 2, icon_size, index)
            text = label[:26]
            painter.text(
                (cx0 + int(chip_h * 0.19) * 2 + icon_size + 6, cy0 + chip_h / 2),
                text, font=label_font, fill=text_fill, anchor="lm",
            )

    return _with_overlay(image, paint)


def _draw_material_strip(image: Image.Image, copy: MarketingCopy, *, light: bool = False) -> Image.Image:
    """材质/适用条：顶部标题 + 底部一排材质短词药丸。"""
    width, height = image.size
    if light:
        base = _draw_light_title(image, copy, align="left", base_ratio=0.058)
    else:
        base = _draw_action_proof(image, copy)
    draw = ImageDraw.Draw(base)
    labels = _bullets(copy, 4)
    if not labels:
        return base
    chip_font = _font(int(width * 0.028), bold=True)
    chip_h = int(height * 0.065)
    gaps = int(width * 0.014)
    total_w = width - int(width * 0.14)
    chip_w = int((total_w - gaps * (len(labels) - 1)) / len(labels))
    left = int(width * 0.07)
    top = int(height * (0.79 if light else 0.80))
    chip_fill = _LIGHT_CARD_FILL if light else _CHIP_FILL
    chip_edge = _LIGHT_CARD_EDGE if light else _CHIP_EDGE
    text_fill = _LIGHT_TEXT if light else (240, 242, 245, 255)

    def paint(painter: ImageDraw.ImageDraw) -> None:
        for index, label in enumerate(labels):
            x0 = left + index * (chip_w + gaps)
            painter.rounded_rectangle(
                [x0, top, x0 + chip_w, top + chip_h],
                radius=chip_h // 2, fill=chip_fill, outline=chip_edge, width=1,
            )
            painter.text(
                (x0 + chip_w / 2, top + chip_h / 2),
                label[:18], font=chip_font, fill=text_fill, anchor="mm",
            )

    return _with_overlay(base, paint)


def _draw_use_cases(image: Image.Image, copy: MarketingCopy, *, light: bool = False) -> Image.Image:
    """场景应用：标题 + 中间照片卡 + 下方四个应用短词卡片。"""
    width, height = image.size
    if light:
        base = _draw_light_title(image, copy, align="center", base_ratio=0.058)
    else:
        base = _draw_headline_hero(image, copy)
    labels = _bullets(copy, 4)
    if not labels:
        return base
    card_font = _font(int(width * 0.024), bold=True)
    card_w = int(width * 0.30)
    card_h = int(height * (0.073 if light else 0.092))
    positions = (
        [(0.05, 0.72), (0.65, 0.72), (0.05, 0.82), (0.65, 0.82)]
        if light
        else [(0.05, 0.55), (0.65, 0.55), (0.05, 0.72), (0.65, 0.72)]
    )
    card_fill = _LIGHT_CARD_FILL if light else _CHIP_FILL
    card_edge = _LIGHT_CARD_EDGE if light else _CHIP_EDGE
    text_fill = _LIGHT_TEXT if light else (240, 242, 245, 255)

    def paint(painter: ImageDraw.ImageDraw) -> None:
        for index, label in enumerate(labels):
            rx, ry = positions[index]
            x0, y0 = int(width * rx), int(height * ry)
            painter.rounded_rectangle(
                [x0, y0, x0 + card_w, y0 + card_h],
                radius=int(card_h * 0.3), fill=card_fill, outline=card_edge, width=1,
            )
            icon_size = int(card_h * 0.58)
            _icon(painter, x0 + int(card_h * 0.2), y0 + (card_h - icon_size) // 2, icon_size, index + 2)
            painter.text(
                (x0 + int(card_h * 0.2) * 2 + icon_size + 6, y0 + card_h / 2),
                label[:16], font=card_font, fill=text_fill, anchor="lm",
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

# 信息带版式的通用色：白带 + 浅暖灰卡片 + 深色字，与 HTML 版保持一致。
_BAND_EYEBROW = (180, 71, 10)
_BAND_CHIP_FILL = (251, 249, 245)
_BAND_CHIP_EDGE = (234, 227, 216)


def _draw_band_headline(
    draw: ImageDraw.ImageDraw,
    copy: MarketingCopy,
    *,
    canvas: int,
    top: int,
    base_ratio: float,
    left: int,
    max_width: int,
) -> int:
    """信息带里的标题 + 橙色强调条；最后一行用主色。返回强调条之后的 y。"""

    lines, font = _fit_lines(
        draw,
        copy.headline,
        base_size=int(canvas * base_ratio),
        bold=True,
        max_width=max_width,
        max_lines=2,
        min_size=int(canvas * 0.018),
    )
    line_height = int(font.size * 1.0) if hasattr(font, "size") else int(canvas * base_ratio)
    y = top
    for index, line in enumerate(lines):
        color = _ACCENT[:3] if index == len(lines) - 1 and len(lines) > 1 else _LIGHT_TEXT
        draw.text((left, y), line, font=font, fill=color)
        y += line_height
    bar = int(canvas * 0.004)
    draw.rounded_rectangle(
        [left, y + int(canvas * 0.008), left + int(canvas * 0.07), y + int(canvas * 0.008) + bar],
        radius=bar // 2,
        fill=(255, 122, 47),
    )
    return y + int(canvas * 0.008) + bar


def _compose_icon_grid_band(photo: bytes, copy: MarketingCopy, canvas: int) -> Image.Image:
    """功能图标网格：照片铺满上部 80%，底部白色信息带放标题 + 6 个短词卡。

    旧版把照片缩到 58% 当圆角卡片浮在整页中间，商品只剩画面两成多，
    四周全是空背景；现在照片直接铺满宽度、顶到画布上沿，信息放进底部白带。
    """

    image = Image.new("RGB", (canvas, canvas), (255, 255, 255))
    photo_image = crop_band_photo(photo, ICON_GRID_BAND, canvas)
    image.paste(photo_image, (0, 0))
    draw = ImageDraw.Draw(image)
    left = int(canvas * 0.04)
    right = canvas - left
    band_top = photo_image.height

    eyebrow_font = _font(int(canvas * 0.012), bold=True)
    draw.text(
        (left, band_top + int(canvas * 0.017)),
        "KEY FEATURES",
        font=eyebrow_font,
        fill=_BAND_EYEBROW,
    )
    accent_bottom = _draw_band_headline(
        draw,
        copy,
        canvas=canvas,
        top=band_top + int(canvas * 0.040),
        base_ratio=0.035,
        left=left,
        max_width=right - left,
    )

    labels = _bullets(copy, 6)
    chip_h = int(canvas * 0.038)
    gap = int(canvas * 0.0075)
    chips_top = max(accent_bottom + int(canvas * 0.012), canvas - int(canvas * 0.019) - chip_h)
    chip_w = (right - left - gap * (len(labels) - 1)) // max(1, len(labels))
    label_font_base = int(canvas * 0.0115)

    for index, label in enumerate(labels):
        x0 = left + index * (chip_w + gap)
        draw.rounded_rectangle(
            [x0, chips_top, x0 + chip_w, chips_top + chip_h],
            radius=int(chip_h * 0.28),
            fill=_BAND_CHIP_FILL,
            outline=_BAND_CHIP_EDGE,
            width=1,
        )
        icon_size = int(chip_h * 0.62)
        icon_x = x0 + int(chip_h * 0.19)
        _icon(draw, icon_x, chips_top + (chip_h - icon_size) // 2, icon_size, index)
        text_x = icon_x + icon_size + int(canvas * 0.007)
        text, text_font = _fit_label_to_cell(
            draw,
            label,
            base_size=label_font_base,
            max_width=x0 + chip_w - text_x - int(canvas * 0.008),
            min_size=int(canvas * 0.008),
        )
        draw.text(
            (text_x, chips_top + chip_h / 2),
            text,
            font=text_font,
            fill=_LIGHT_TEXT,
            anchor="lm",
        )
    return image


def compose_marketing_image(
    photo: bytes,
    template: str,
    copy: MarketingCopy,
    *,
    canvas_size: int = CANVAS_SIZE,
) -> bytes:
    """照片层 + 文案 → 1:1 营销图（JPEG bytes）。"""
    # side_rail 目前只有 HTML 版；排版引擎不可用时用信息量最接近的 icon_grid 兜底。
    if str(template or "") == "side_rail":
        template = "icon_grid"
    key = str(template or "")
    drawer = _TEMPLATES.get(key)
    if drawer is None:
        raise ValueError(f"未知版式：{template}（可选：{'、'.join(TEMPLATES)}）")
    copy = MarketingCopy(
        headline=filter_copy(copy.headline),
        subline=filter_copy(copy.subline),
        bullets=[filter_copy(item) for item in (copy.bullets or [])],
    )
    if not copy.headline:
        raise ValueError("文案标题为空")
    # 图标网格走「上图 + 底部信息带」，不再把照片缩成浮空小卡片。
    if key == "icon_grid":
        image = _compose_icon_grid_band(photo, copy, canvas_size)
    else:
        zone = ZONE_SPECS.get(key)
        if zone:
            image = _zoned_canvas(photo, canvas_size, top_ratio=zone[0], size_ratio=zone[1])
        else:
            image = _scrim(_square_photo(photo, canvas_size))
        image = drawer(image, copy, light=True) if zone else drawer(image, copy)
    image = image.filter(ImageFilter.UnsharpMask(radius=1.2, percent=60, threshold=3))
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def compose_feature_cards(
    hero_bytes: bytes,
    cards: list[tuple[bytes, str]],
    copy: MarketingCopy,
    *,
    canvas_size: int = CANVAS_SIZE,
) -> bytes:
    """「功能细节卡」版式：上方主图卡 + 标题，下方 2×2 功能细节小卡。"""
    canvas = canvas_size
    # 主图：照片背景延伸铺满整页，清晰商品柔边融入；细节卡放在下方白卡里。
    image = _zoned_canvas(hero_bytes, canvas, top_ratio=0.14, size_ratio=0.50)
    image = _draw_light_title(image, copy, align="left", base_ratio=0.052)

    # 卡片网格：行数按真实张数收（2 张=1 行、4 张=2×2），不给空格子留位。
    cards_top = int(canvas * 0.66)
    margin = int(canvas * 0.035)
    gap = int(canvas * 0.022)
    rows = max(1, -(-min(4, len(cards)) // 2))
    card_w = (canvas - margin * 2 - gap) // 2
    card_h = (canvas - cards_top - margin - gap * (rows - 1)) // rows
    label_font = _font(int(canvas * 0.026), bold=True)

    # 白卡在白色页底上只靠描边不够，先铺一层很淡的投影拉开层次。
    shadows = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadows)
    for index in range(min(4, len(cards))):
        col, row = index % 2, index // 2
        sx0 = margin + col * (card_w + gap)
        sy0 = cards_top + row * (card_h + gap)
        shadow_draw.rounded_rectangle(
            [sx0 + 4, sy0 + 10, sx0 + card_w + 4, sy0 + card_h + 10],
            radius=int(card_h * 0.09), fill=(66, 54, 38, 40),
        )
    shadows = shadows.filter(ImageFilter.GaussianBlur(12))
    image = Image.alpha_composite(image.convert("RGBA"), shadows).convert("RGB")

    def draw_card(card_bytes: bytes, label: str, x0: int, y0: int) -> None:
        radius = int(card_h * 0.09)
        tile = Image.new("RGB", (card_w, card_h), _LIGHT_CARD_FILL)
        photo = Image.open(BytesIO(card_bytes)).convert("RGB")
        photo_h = int(card_h * 0.72)
        scale = max(card_w / photo.width, photo_h / photo.height)
        photo = photo.resize((max(1, int(photo.width * scale)), max(1, int(photo.height * scale))), Image.LANCZOS)
        left_p = (photo.width - card_w) // 2
        top_p = (photo.height - photo_h) // 2
        tile.paste(photo.crop((left_p, top_p, left_p + card_w, top_p + photo_h)), (0, 0))
        tdraw = ImageDraw.Draw(tile)
        icon_size = int(card_h * 0.13)
        _icon(tdraw, int(card_w * 0.05), photo_h + (card_h - photo_h - icon_size) // 2, icon_size, hash(label) % 6)
        tdraw.text(
            (int(card_w * 0.05) * 2 + icon_size + 6, photo_h + (card_h - photo_h) // 2),
            label[:22], font=label_font, fill=_LIGHT_TEXT, anchor="lm",
        )
        # 圆角蒙版贴回，再补一圈浅暖灰描边（白卡在暖白页底上不描边会糊成一片）。
        mask = Image.new("L", (card_w, card_h), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, card_w, card_h], radius=radius, fill=255)
        image.paste(tile, (x0, y0), mask)
        outline = ImageDraw.Draw(image)
        outline.rounded_rectangle(
            [x0, y0, x0 + card_w, y0 + card_h],
            radius=radius, outline=_LIGHT_CARD_EDGE, width=2,
        )

    for index, (card_bytes, label) in enumerate(list(cards)[:4]):
        col, row = index % 2, index // 2
        x0 = margin + col * (card_w + gap)
        y0 = cards_top + row * (card_h + gap)
        draw_card(card_bytes, label, x0, y0)

    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def compose_callout_ring(
    hero_bytes: bytes,
    parts: list[dict],
    copy: MarketingCopy,
    *,
    canvas_size: int = CANVAS_SIZE,
) -> bytes:
    """圆环标注版式：hero 大图 + 编号点/引线 + 左上标题 + 四角圆形细节裁切。

    ``parts`` = [{"label": str, "x": 0-1000, "y": 0-1000}]，坐标由视觉模型给出；
    坐标缺失时退化为「只画编号列表，不画点和引线」；一个部位都没标出来时
    退化为居中大标题版式（避免只剩左上标题压在产品上，看起来像排版滑位）。
    """
    canvas = canvas_size
    hero = Image.open(BytesIO(hero_bytes)).convert("RGB")
    scale = max(canvas / hero.width, canvas / hero.height)
    hero = hero.resize((max(1, int(hero.width * scale)), max(1, int(hero.height * scale))), Image.LANCZOS)
    left = (hero.width - canvas) // 2
    top = (hero.height - canvas) // 2
    image = hero.crop((left, top, left + canvas, top + canvas))
    if not parts:
        image = _scrim(image, top_ratio=0.38)
        image = _draw_headline_hero(image, copy)
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=90)
        return buffer.getvalue()
    image = _scrim(image, top_ratio=0.22)

    draw = ImageDraw.Draw(image)
    headline_lines, headline_font = _fit_lines(
        draw, copy.headline, base_size=int(canvas * 0.052), bold=True,
        max_width=int(canvas * 0.58), max_lines=2,
    )
    y = int(canvas * 0.05)
    for line in headline_lines:
        draw.text((int(canvas * 0.05) + 3, y + 3), line, font=headline_font, fill=(24, 26, 30))
        draw.text((int(canvas * 0.05), y), line, font=headline_font, fill=(255, 255, 255))
        y += int(canvas * 0.062)
    draw.rectangle(
        [(int(canvas * 0.05), y + 4), (int(canvas * 0.05) + int(canvas * 0.09), y + int(canvas * 0.011))],
        fill=(255, 122, 47),
    )

    label_font = _font(int(canvas * 0.024), bold=True)
    num_font = _font(int(canvas * 0.026), bold=True)
    badge_r = int(canvas * 0.021)
    placed: list[tuple[int, int, int]] = []
    for index, part in enumerate(parts[:5], start=1):
        label = str(part.get("label") or "").strip()[:28] or f"Detail {index}"
        try:
            px = int(float(part.get("x", -1)) / 1000 * canvas)
            py = int(float(part.get("y", -1)) / 1000 * canvas)
        except Exception:  # noqa: BLE001
            px = py = -1

        if 0 <= px < canvas and 0 <= py < canvas:
            # 编号点 + 引线到右侧标注列
            anchor_x = canvas - int(canvas * 0.30)
            anchor_y = int(canvas * 0.20) + (index - 1) * int(canvas * 0.085)
            draw.line([(px, py), (anchor_x, anchor_y)], fill=(255, 122, 47, 255), width=3)
            draw.ellipse([px - badge_r, py - badge_r, px + badge_r, py + badge_r], fill=(255, 122, 47))
            draw.text((px, py), str(index), font=num_font, fill=(20, 20, 20), anchor="mm")
            placed.append((anchor_x, anchor_y, len(placed)))
            draw.ellipse([anchor_x - 6, anchor_y - 6, anchor_x + 6, anchor_y + 6], fill=(255, 122, 47))
            draw.text((anchor_x + int(canvas * 0.02), anchor_y), label, font=label_font, fill=(245, 246, 250), anchor="lm")
        else:
            row_y = int(canvas * 0.82) + (index - 1 % 3) * int(canvas * 0.03)
            draw.ellipse([int(canvas * 0.06), row_y - badge_r, int(canvas * 0.06) + badge_r * 2, row_y + badge_r], fill=(255, 122, 47))
            draw.text((int(canvas * 0.06) + badge_r, row_y), str(index), font=num_font, fill=(20, 20, 20), anchor="mm")
            draw.text((int(canvas * 0.06) + badge_r * 3, row_y), label, font=label_font, fill=(245, 246, 250), anchor="lm")

    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def compose_feature_strip(
    hero_bytes: bytes,
    cards: list[tuple[bytes, str]],
    copy: MarketingCopy,
    *,
    canvas_size: int = CANVAS_SIZE,
) -> bytes:
    """功能横条版式：照片铺满上部 78%，底部白色信息带放标题 + 4 个圆形细节。

    旧版把主图缩成 50% 的方块浮在柔和底页中间，四周大片空背景显得很空；
    现在照片铺满宽度、顶到画布上沿，标题与细节圆点都落在底部白带里。
    """
    canvas = canvas_size
    image = Image.new("RGB", (canvas, canvas), (255, 255, 255))
    photo = crop_band_photo(hero_bytes, FEATURE_STRIP_BAND, canvas)
    image.paste(photo, (0, 0))
    draw = ImageDraw.Draw(image)
    left = int(canvas * 0.04)
    right = canvas - left
    accent_bottom = _draw_band_headline(
        draw,
        copy,
        canvas=canvas,
        top=photo.height + int(canvas * 0.015),
        base_ratio=0.033,
        left=left,
        max_width=right - left,
    )

    # 底部 4 个圆形细节：圆点用真实裁切，标签按格子宽度缩字号。
    count = max(1, min(4, len(cards)))
    cell_w = canvas // 4
    circle_d = int(canvas * 0.078)
    label_base_size = int(canvas * 0.0145)
    label_bottom = canvas - int(canvas * 0.012)
    label_font = _font(label_base_size, bold=True)
    label_height = getattr(label_font, "size", label_base_size)
    circle_bottom = label_bottom - label_height - int(canvas * 0.010)
    circle_top = max(accent_bottom + int(canvas * 0.010), circle_bottom - circle_d)
    cy = circle_top + circle_d // 2

    for index, (card_bytes, label) in enumerate(cards[:count]):
        card = Image.open(BytesIO(card_bytes)).convert("RGB")
        scale_c = circle_d / min(card.size)
        card = card.resize(
            (
                max(circle_d, int(card.width * scale_c)),
                max(circle_d, int(card.height * scale_c)),
            ),
            Image.LANCZOS,
        )
        left_c = (card.width - circle_d) // 2
        top_c = (card.height - circle_d) // 2
        tile = card.crop((left_c, top_c, left_c + circle_d, top_c + circle_d))
        mask = Image.new("L", (circle_d, circle_d), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, circle_d, circle_d], fill=255)
        cx = index * cell_w + (cell_w - circle_d) // 2
        image.paste(tile, (cx, circle_top), mask)
        draw.ellipse(
            [cx - 3, circle_top - 3, cx + circle_d + 3, circle_top + circle_d + 3],
            outline=(255, 122, 47),
            width=4,
        )
        text, text_font = _fit_label_to_cell(
            draw,
            label,
            base_size=label_base_size,
            max_width=int(cell_w * 0.90),
            min_size=int(canvas * 0.010),
        )
        draw.text(
            (index * cell_w + cell_w // 2, circle_top + circle_d + int(canvas * 0.008)),
            text,
            font=text_font,
            fill=(28, 30, 34),
            anchor="mt",
        )

    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def compose_band_skeleton(
    photo: bytes,
    template: str,
    copy: MarketingCopy,
    details: list[tuple[bytes, str]] | None = None,
    callout_parts: list[dict] | None = None,
    *,
    canvas_size: int = CANVAS_SIZE,
    desired_band: float = 0.32,
    contain: bool = False,
) -> bytes:
    """PIL 版「S1 上下分栏」兜底：所有模板共用一条白色信息带。

    浏览器不可用时统一降级到这一种结构（HTML 全量支持 S1-S4）。信息带高度会按
    商品底边自动回收，避免把商品底边切掉。
    """

    canvas = int(canvas_size)
    band = resolve_band_ratio(photo, desired_band)
    image = Image.new("RGB", (canvas, canvas), (255, 255, 255))
    photo_image = crop_band_photo(photo, band, canvas, contain=contain)
    image.paste(photo_image, (0, 0))
    draw = ImageDraw.Draw(image)
    left = int(canvas * 0.035)
    right = canvas - left
    band_top = photo_image.height

    key = str(template or "")
    style = {
        "icon_grid": "chips",
        "side_rail": "chips",
        "material_strip": "pills",
        "use_cases": "cards",
        "feature_cards": "cards",
        "feature_strip": "circles",
        "callout_ring": "callouts",
    }.get(key, "none")

    if style != "none":
        draw.text(
            (left, band_top + int(canvas * 0.014)),
            "KEY FEATURES",
            font=_font(int(canvas * 0.012), bold=True),
            fill=(180, 71, 10),
        )
        title_top = band_top + int(canvas * 0.034)
    else:
        title_top = band_top + int(canvas * 0.030)

    accent_bottom = _draw_band_headline(
        draw,
        copy,
        canvas=canvas,
        top=title_top,
        base_ratio=0.031,
        left=left,
        max_width=right - left,
    )

    if style == "none":
        subline = str(copy.subline or "").strip()
        if subline:
            line, font = _fit_label_to_cell(
                draw,
                subline,
                base_size=int(canvas * 0.016),
                max_width=right - left,
                min_size=int(canvas * 0.012),
            )
            draw.text(
                (left, accent_bottom + int(canvas * 0.008)),
                line,
                font=font,
                fill=(88, 82, 74),
            )
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=90)
        return buffer.getvalue()

    content_top = max(
        accent_bottom + int(canvas * 0.010),
        canvas - int(canvas * 0.018) - int(canvas * 0.070),
    )
    inner = right - left
    labels = _bullets(copy, 4)
    if style in {"cards", "circles"} and details:
        labels = [label for _content, label in details][:4]

    if style in {"chips", "pills", "cards", "circles"}:
        count = max(1, min(4, len(labels)))
        gap = int(canvas * 0.012)
        cell_w = (inner - gap * (count - 1)) // count
        cell_h = int(canvas * 0.070)
        for index, label in enumerate(labels[:count]):
            x0 = left + index * (cell_w + gap)
            if style == "pills":
                draw.rounded_rectangle(
                    [x0, content_top, x0 + cell_w, content_top + cell_h],
                    radius=cell_h // 2,
                    fill=_BAND_CHIP_FILL,
                    outline=_BAND_CHIP_EDGE,
                    width=1,
                )
                text, text_font = _fit_label_to_cell(
                    draw,
                    label,
                    base_size=int(canvas * 0.015),
                    max_width=cell_w - int(canvas * 0.026),
                    min_size=int(canvas * 0.010),
                )
                draw.text(
                    (x0 + cell_w / 2, content_top + cell_h / 2),
                    text,
                    font=text_font,
                    fill=_LIGHT_TEXT,
                    anchor="mm",
                )
                continue
            draw.rounded_rectangle(
                [x0, content_top, x0 + cell_w, content_top + cell_h],
                radius=int(cell_h * 0.26),
                fill=_BAND_CHIP_FILL,
                outline=_BAND_CHIP_EDGE,
                width=1,
            )
            icon_size = int(cell_h * 0.52)
            if style == "circles" and details:
                content = details[index][0]
                card = Image.open(BytesIO(content)).convert("RGB")
                side = min(card.size)
                card = card.crop(
                    (
                        (card.width - side) // 2,
                        (card.height - side) // 2,
                        (card.width + side) // 2,
                        (card.height + side) // 2,
                    )
                ).resize((icon_size, icon_size), Image.LANCZOS)
                mask = Image.new("L", (icon_size, icon_size), 0)
                ImageDraw.Draw(mask).ellipse([0, 0, icon_size, icon_size], fill=255)
                image.paste(
                    card,
                    (x0 + int(cell_h * 0.16), content_top + (cell_h - icon_size) // 2),
                    mask,
                )
            elif style == "cards" and details:
                content = details[index][0]
                thumb_w = int(cell_h * 0.62)
                card = Image.open(BytesIO(content)).convert("RGB")
                scale = thumb_w / min(card.size)
                card = card.resize(
                    (
                        max(thumb_w, int(card.width * scale)),
                        max(thumb_w, int(card.height * scale)),
                    ),
                    Image.LANCZOS,
                )
                x_left = (card.width - thumb_w) // 2
                y_top = (card.height - thumb_w) // 2
                image.paste(
                    card.crop((x_left, y_top, x_left + thumb_w, y_top + thumb_w)),
                    (x0 + int(cell_h * 0.16), content_top + (cell_h - thumb_w) // 2),
                )
            else:
                _icon(
                    draw,
                    x0 + int(cell_h * 0.18),
                    content_top + (cell_h - icon_size) // 2,
                    icon_size,
                    index,
                )
            text_x = x0 + int(cell_h * 0.18) + icon_size + int(canvas * 0.008)
            text, text_font = _fit_label_to_cell(
                draw,
                label,
                base_size=int(canvas * 0.0125),
                max_width=x0 + cell_w - text_x - int(canvas * 0.008),
                min_size=int(canvas * 0.008),
            )
            draw.text(
                (text_x, content_top + cell_h / 2),
                text,
                font=text_font,
                fill=_LIGHT_TEXT,
                anchor="lm",
            )
    else:
        # 编号标注：信息带里画 2×2 图例（圆点编号 + 部位名）。
        parts = list(callout_parts or [])[:4] or [
            {"label": label} for label in labels[:4]
        ]
        cell_w = inner // 2
        row_h = int(canvas * 0.042)
        for index, part in enumerate(parts):
            col = index % 2
            row = index // 2
            x0 = left + col * cell_w
            y0 = content_top + row * row_h
            dot = int(canvas * 0.026)
            draw.ellipse([x0, y0, x0 + dot, y0 + dot], fill=(255, 106, 0))
            num_font = _font(int(canvas * 0.014), bold=True)
            draw.text(
                (x0 + dot / 2, y0 + dot / 2),
                str(index + 1),
                font=num_font,
                fill=(255, 255, 255),
                anchor="mm",
            )
            text, text_font = _fit_label_to_cell(
                draw,
                str(part.get("label") or ""),
                base_size=int(canvas * 0.015),
                max_width=cell_w - dot - int(canvas * 0.020),
                min_size=int(canvas * 0.010),
            )
            draw.text(
                (x0 + dot + int(canvas * 0.010), y0 + dot / 2),
                text,
                font=text_font,
                fill=_LIGHT_TEXT,
                anchor="lm",
            )

    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()
