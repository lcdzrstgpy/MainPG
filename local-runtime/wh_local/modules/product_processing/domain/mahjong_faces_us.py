"""美式麻将牌面（按某套 166 张商品牌的设计规则程序化重绘）。

为什么要单独一个模块：中式标准牌（``mahjong_faces``）和这套美式牌的设计语言
完全不同，共用一套画法必然"像但不是这个商品"。这里只复刻实测到的规则：

- 每张牌**左上角都有红色小数字编号**（美式便于认牌的特征）；
- 筒子是"花瓣圆盘"（外圈一圈小圆点 + 内外双层环 + 实心芯），用绿/黑/红三色；
- 条子是绿色竹节，顶部带红编号；
- 万子用楷体红字「一萬…九萬」，左上红编号；
- 风牌：白板=黑回纹框，東南西北=红 E/S/W/N + 黑字，中=红中；
- JOKER 这类插画牌不重绘，由抠图 + 增强提供（见 ``tools`` 里的抠图流程）。

画布与中式那套一致（600×840 = 牌面 30:42），保证贴图尺寸可以互换。
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

CANVAS: tuple[int, int] = (600, 840)
_SUPERSAMPLE = 3

RED = (188, 38, 44, 255)
GREEN = (26, 106, 62, 255)
BLACK = (34, 34, 36, 255)

_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\simkai.ttf",
    r"C:\Windows\Fonts\STKAITI.TTF",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\msyhbd.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
)

_CN_NUMERALS = "一二三四五六七八九"
_WAN_GLYPHS = "一二三四伍六七八九"
_WIND_LETTERS = {"東": "E", "南": "S", "西": "W", "北": "N"}

# 圆盘中心（归一化）；沿用总表里的排布：奇数居中、偶数对称。
_LAYOUTS: dict[int, tuple[tuple[float, float], ...]] = {
    1: ((0.5, 0.52),),
    2: ((0.5, 0.30), (0.5, 0.74)),
    3: ((0.30, 0.27), (0.52, 0.53), (0.72, 0.79)),
    4: ((0.30, 0.31), (0.70, 0.31), (0.30, 0.73), (0.70, 0.73)),
    5: ((0.27, 0.26), (0.73, 0.26), (0.5, 0.52), (0.27, 0.78), (0.73, 0.78)),
    6: ((0.30, 0.22), (0.70, 0.22), (0.30, 0.52), (0.70, 0.52), (0.30, 0.82), (0.70, 0.82)),
    7: (
        (0.26, 0.17),
        (0.5, 0.17),
        (0.74, 0.17),
        (0.28, 0.55),
        (0.72, 0.55),
        (0.28, 0.85),
        (0.72, 0.85),
    ),
    8: (
        (0.32, 0.15),
        (0.68, 0.15),
        (0.32, 0.38),
        (0.68, 0.38),
        (0.32, 0.62),
        (0.68, 0.62),
        (0.32, 0.85),
        (0.68, 0.85),
    ),
    9: (
        (0.22, 0.20),
        (0.5, 0.20),
        (0.78, 0.20),
        (0.22, 0.52),
        (0.5, 0.52),
        (0.78, 0.52),
        (0.22, 0.84),
        (0.5, 0.84),
        (0.78, 0.84),
    ),
}

_RADIUS = {1: 0.27, 2: 0.185, 3: 0.17, 4: 0.16, 5: 0.148, 6: 0.125, 7: 0.108, 8: 0.104, 9: 0.098}

# 这套牌的点色顺序：绿、黑、红，按位置循环。
_DOT_COLORS = (GREEN, BLACK, RED)


@dataclass(frozen=True)
class FaceSpec:
    face_id: str
    kind: str  # dot / bamboo / myriad / wind / blank / red / joker


def _specs() -> tuple[FaceSpec, ...]:
    items: list[FaceSpec] = []
    for number in range(1, 10):
        items.append(FaceSpec(f"{number}筒", "dot"))
    for number in range(1, 10):
        items.append(FaceSpec(f"{number}条", "bamboo"))
    for number in range(1, 10):
        items.append(FaceSpec(f"{number}万", "myriad"))
    for wind in ("東", "南", "西", "北"):
        items.append(FaceSpec(wind, "wind"))
    items.append(FaceSpec("白", "blank"))
    items.append(FaceSpec("中", "red"))
    items.append(FaceSpec("JOKER", "joker"))
    return tuple(items)


STANDARD_FACES: tuple[FaceSpec, ...] = _specs()
_SPEC_BY_ID = {spec.face_id: spec for spec in STANDARD_FACES}

# 样张默认摆这套：JOKER + 各花色一张，一眼看出是"这套美式牌"。
DEFAULT_HAND: tuple[str, ...] = ("JOKER", "5筒", "7条", "9万", "東", "中")


def _font(size: int) -> ImageFont.FreeTypeFont | None:
    for candidate in _FONT_CANDIDATES:
        try:
            if Path(candidate).is_file():
                return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return None


def _draw_petal_disc(draw: ImageDraw.ImageDraw, cx: float, cy: float, radius: float, color) -> None:
    """花瓣圆盘：外圈一圈小圆点 + 内外双层环 + 实心芯。"""

    petals = 12
    petal_r = radius * 0.17
    for index in range(petals):
        angle = 2.0 * math.pi * index / petals
        px = cx + radius * 0.84 * math.cos(angle)
        py = cy + radius * 0.84 * math.sin(angle)
        draw.ellipse((px - petal_r, py - petal_r, px + petal_r, py + petal_r), fill=color)
    ring = radius * 0.66
    draw.ellipse(
        (cx - ring, cy - ring, cx + ring, cy + ring),
        outline=color,
        width=max(2, int(radius * 0.16)),
    )
    core = radius * 0.26
    draw.ellipse((cx - core, cy - core, cx + core, cy + core), fill=color)


def _draw_bamboo(draw: ImageDraw.ImageDraw, cx: float, cy: float, radius: float, color) -> None:
    width = radius * 0.78
    height = radius * 2.45
    segment = height / 3.0
    gap = segment * 0.16
    for index in range(3):
        top = cy - height / 2 + index * segment
        draw.rounded_rectangle(
            (cx - width / 2, top, cx + width / 2, top + segment - gap),
            radius=width * 0.3,
            fill=color,
        )


def _draw_index(draw: ImageDraw.ImageDraw, text: str, width: int, height: int, color=RED) -> None:
    """左上角编号：这套牌最显眼的识别特征。"""
    font = _font(int(height * 0.105))
    if font is None:
        return
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(
        (width * 0.055 - box[0], height * 0.035 - box[1]),
        text,
        font=font,
        fill=color,
    )


def _draw_blank(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    """白板：黑色回纹方框（这套牌的白板不是空白）。"""

    inset_x, inset_y = width * 0.16, height * 0.14
    outer = (inset_x, inset_y, width - inset_x, height - inset_y)
    draw.rectangle(outer, outline=BLACK, width=max(4, int(width * 0.018)))
    inner_in = width * 0.045
    draw.rectangle(
        (outer[0] + inner_in, outer[1] + inner_in, outer[2] - inner_in, outer[3] - inner_in),
        outline=BLACK,
        width=max(2, int(width * 0.008)),
    )


def render_face(face_id: str) -> bytes:
    """画一张美式牌面（RGBA，透明底，墨迹在 RGB）。"""

    spec = _SPEC_BY_ID.get(face_id)
    if spec is None:
        raise KeyError(f"unknown US mahjong face: {face_id}")
    width, height = CANVAS
    scale = _SUPERSAMPLE
    image = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw_w, draw_h = width * scale, height * scale

    if spec.kind in {"dot", "bamboo"}:
        number = int(spec.face_id[0])
        radius = _RADIUS[number] * draw_w
        for index, (nx, ny) in enumerate(_LAYOUTS[number]):
            if spec.kind == "bamboo":
                # 这套牌的条子基本是绿色，只有 1 条是红色（鸟牌）。
                color = RED if number == 1 else GREEN
                _draw_bamboo(draw, nx * draw_w, ny * draw_h, radius, color)
            else:
                color = _DOT_COLORS[index % len(_DOT_COLORS)]
                _draw_petal_disc(draw, nx * draw_w, ny * draw_h, radius, color)
        _draw_index(draw, str(number), draw_w, draw_h)
    elif spec.kind == "myriad":
        number = int(spec.face_id[0])
        glyph = _font(int(draw_h * 0.28))
        suffix = _font(int(draw_h * 0.30))
        if glyph is None or suffix is None:
            raise RuntimeError("需要中文字体才能生成万子牌面")
        text = _WAN_GLYPHS[number - 1]
        box = draw.textbbox((0, 0), text, font=glyph)
        box2 = draw.textbbox((0, 0), "萬", font=suffix)
        draw.text(
            ((draw_w - (box[2] - box[0])) / 2 - box[0], draw_h * 0.26 - box[1]),
            text,
            font=glyph,
            fill=RED,
        )
        draw.text(
            ((draw_w - (box2[2] - box2[0])) / 2 - box2[0], draw_h * 0.54 - box2[1]),
            "萬",
            font=suffix,
            fill=RED,
        )
        _draw_index(draw, str(number), draw_w, draw_h)
    elif spec.kind == "wind":
        big = _font(int(draw_h * 0.46))
        if big is None:
            raise RuntimeError("需要中文字体才能生成风牌牌面")
        box = draw.textbbox((0, 0), spec.face_id, font=big)
        draw.text(
            ((draw_w - (box[2] - box[0])) / 2 - box[0], draw_h * 0.5 - box[1]),
            spec.face_id,
            font=big,
            fill=BLACK,
        )
        _draw_index(draw, _WIND_LETTERS[spec.face_id], draw_w, draw_h)
    elif spec.kind == "red":
        big = _font(int(draw_h * 0.52))
        if big is None:
            raise RuntimeError("需要中文字体才能生成红中牌面")
        box = draw.textbbox((0, 0), "中", font=big)
        draw.text(
            ((draw_w - (box[2] - box[0])) / 2 - box[0], draw_h * 0.5 - box[1]),
            "中",
            font=big,
            fill=RED,
        )
    elif spec.kind == "blank":
        _draw_blank(draw, draw_w, draw_h)
    else:  # joker：插画牌不重绘，等调用方用抠图资产覆盖
        _draw_index(draw, "JOKER", draw_w, draw_h)

    image = image.resize((width, height), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def write_faces(
    directory: Path,
    ids: Sequence[str] | None = None,
    *,
    art_dir: Path | None = None,
) -> list[Path]:
    """生成/复制牌面贴图。

    ``art_dir`` 里若已有同名 PNG（抠图或 AI 生成的插画牌面），直接拿来用——
    这类图案程序画不出来，必须走资产；其余牌面仍由程序绘制，保证字数和字纹正确。
    """

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for face_id in list(ids or DEFAULT_HAND):
        path = directory / f"{face_id}.png"
        art = (Path(art_dir) / f"{face_id}.png") if art_dir else None
        if art is not None and art.is_file():
            if not path.is_file() or art.stat().st_mtime > path.stat().st_mtime:
                path.write_bytes(art.read_bytes())
            written.append(path.resolve())
            continue
        if not path.is_file():
            path.write_bytes(render_face(face_id))
        written.append(path.resolve())
    return written
