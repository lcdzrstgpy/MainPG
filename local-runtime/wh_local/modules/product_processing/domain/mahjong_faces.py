"""标准麻将牌面的程序化生成（本地绘制，不依赖任何素材）。

为什么需要它：
- 从电商实拍图里抠牌面，分辨率只有一两百像素，放大到渲染尺寸必然发糊，
  还会把水印、邻牌残边当成花纹抠出来（实测抠出过 "POKER" 字样）。
- 标准牌只有 34 种（筒/条/万 各 1-9、字牌 7 张、花牌 8 张），它们本来就是
  规则图形 + 规范字形，程序画比抠图更准、更清、可无限放大。

输出格式刻意与图案库一致：RGBA PNG，alpha 即「墨迹」，RGB 即墨色。
画布固定 600×840（= 牌面 30:42 的比例），所有牌共用同一画布，
这样渲染时每张牌的字号/留白完全一致，不会一筒大、九筒小。
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from PIL import Image, ImageDraw, ImageFont

# 牌面比例：麻将牌 30×42mm，画布按同比例固定，保证所有牌的留白一致。
CANVAS: tuple[int, int] = (600, 840)
_SUPERSAMPLE = 3

INK_NAVY = (28, 44, 76, 255)
INK_RED = (178, 36, 42, 255)
INK_GREEN = (22, 102, 70, 255)
INK_BLUE = (30, 74, 146, 255)

_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simsun.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
)

_CN_NUMERALS = "一二三四五六七八九"

# 每张牌的圆点/竹条圆心（画布归一化坐标）。数量少时排布更大、更居中。
_LAYOUTS: dict[int, tuple[tuple[float, float], ...]] = {
    1: ((0.5, 0.5),),
    2: ((0.5, 0.26), (0.5, 0.74)),
    3: ((0.5, 0.17), (0.27, 0.72), (0.73, 0.72)),
    4: ((0.29, 0.30), (0.71, 0.30), (0.29, 0.70), (0.71, 0.70)),
    5: ((0.27, 0.23), (0.73, 0.23), (0.5, 0.5), (0.27, 0.77), (0.73, 0.77)),
    6: ((0.30, 0.19), (0.70, 0.19), (0.30, 0.50), (0.70, 0.50), (0.30, 0.81), (0.70, 0.81)),
    7: (
        (0.24, 0.14),
        (0.5, 0.14),
        (0.76, 0.14),
        (0.28, 0.52),
        (0.72, 0.52),
        (0.28, 0.84),
        (0.72, 0.84),
    ),
    8: (
        (0.32, 0.12),
        (0.68, 0.12),
        (0.32, 0.37),
        (0.68, 0.37),
        (0.32, 0.63),
        (0.68, 0.63),
        (0.32, 0.88),
        (0.68, 0.88),
    ),
    9: (
        (0.22, 0.18),
        (0.5, 0.18),
        (0.78, 0.18),
        (0.22, 0.5),
        (0.5, 0.5),
        (0.78, 0.5),
        (0.22, 0.82),
        (0.5, 0.82),
        (0.78, 0.82),
    ),
}

# 点阵图案的半径（占画布宽度的比例）：数量越多越小。
_RADIUS = {1: 0.30, 2: 0.205, 3: 0.185, 4: 0.175, 5: 0.145, 6: 0.135, 7: 0.115, 8: 0.11, 9: 0.102}


@dataclass(frozen=True)
class FaceSpec:
    """一张标准牌面的描述。"""

    face_id: str
    label: str  # 中文名，日志/备注用
    kind: str  # dot / bamboo / myriad / honour / blank


def _standard_specs() -> tuple[FaceSpec, ...]:
    specs: list[FaceSpec] = []
    for number in range(1, 10):
        specs.append(FaceSpec(f"{number}筒", f"{number}筒", "dot"))
    for number in range(1, 10):
        specs.append(FaceSpec(f"{number}条", f"{number}条", "bamboo"))
    for number in range(1, 10):
        specs.append(FaceSpec(f"{number}万", f"{number}万", "myriad"))
    for name in ("東", "南", "西", "北", "中", "發"):
        specs.append(FaceSpec(name, name, "honour"))
    specs.append(FaceSpec("白", "白板", "blank"))
    return tuple(specs)


STANDARD_FACES: tuple[FaceSpec, ...] = _standard_specs()
_FACE_BY_ID = {spec.face_id: spec for spec in STANDARD_FACES}

# 四宫格默认摆出的手牌：字牌 + 各花色各一张，一眼能看出「一套牌」。
DEFAULT_HAND: tuple[str, ...] = ("中", "發", "白", "5筒", "7条", "9万")


def _font(size: int) -> ImageFont.FreeTypeFont | None:
    for candidate in _FONT_CANDIDATES:
        try:
            if Path(candidate).is_file():
                return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return None


def _draw_dot(draw: ImageDraw.ImageDraw, cx: float, cy: float, radius: float, color) -> None:
    """筒子：外圈 + 内圈 + 实心芯，远看就是麻将的圆饼。"""
    outer = radius
    draw.ellipse(
        (cx - outer, cy - outer, cx + outer, cy + outer),
        outline=color,
        width=max(2, int(radius * 0.24)),
    )
    middle = radius * 0.56
    draw.ellipse(
        (cx - middle, cy - middle, cx + middle, cy + middle),
        outline=color,
        width=max(2, int(radius * 0.13)),
    )
    core = radius * 0.20
    draw.ellipse((cx - core, cy - core, cx + core, cy + core), fill=color)


def _draw_bamboo(draw: ImageDraw.ImageDraw, cx: float, cy: float, radius: float, color) -> None:
    """条子：竖着的三段竹节，段间留缝。"""
    width = radius * 0.86
    height = radius * 2.5
    segment = height / 3.0
    gap = segment * 0.14
    for index in range(3):
        top = cy - height / 2 + index * segment
        draw.rounded_rectangle(
            (cx - width / 2, top, cx + width / 2, top + segment - gap),
            radius=width * 0.28,
            fill=color,
        )


def _draw_honour(draw: ImageDraw.ImageDraw, face_id: str, width: int, height: int) -> bool:
    """字牌：一个居中的大字。"""
    font = _font(int(height * 0.62))
    if font is None:
        return False
    color = INK_RED if face_id == "中" else INK_GREEN if face_id == "發" else INK_NAVY
    box = draw.textbbox((0, 0), face_id, font=font)
    text_w, text_h = box[2] - box[0], box[3] - box[1]
    draw.text(
        ((width - text_w) / 2 - box[0], (height - text_h) / 2 - box[1]),
        face_id,
        font=font,
        fill=color,
    )
    return True


def _draw_myriad(
    draw: ImageDraw.ImageDraw, number: int, width: int, height: int
) -> bool:
    """万子：上面中文数字，下面「萬」。"""
    numeral = _font(int(height * 0.30))
    myriad = _font(int(height * 0.34))
    if numeral is None or myriad is None:
        return False
    top_text = _CN_NUMERALS[number - 1]
    bottom_text = "萬"
    top_box = draw.textbbox((0, 0), top_text, font=numeral)
    bottom_box = draw.textbbox((0, 0), bottom_text, font=myriad)
    top_w = top_box[2] - top_box[0]
    bottom_w = bottom_box[2] - bottom_box[0]
    draw.text(
        ((width - top_w) / 2 - top_box[0], height * 0.20 - top_box[1]),
        top_text,
        font=numeral,
        fill=INK_NAVY,
    )
    draw.text(
        ((width - bottom_w) / 2 - bottom_box[0], height * 0.52 - bottom_box[1]),
        bottom_text,
        font=myriad,
        fill=INK_RED,
    )
    return True


def _draw_blank(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    """白板：蓝色双线方框（真牌是压印方框，不上色会看不出是白板）。"""
    inset_x, inset_y = width * 0.18, height * 0.16
    draw.rounded_rectangle(
        (inset_x, inset_y, width - inset_x, height - inset_y),
        radius=width * 0.04,
        outline=INK_BLUE,
        width=max(3, int(width * 0.016)),
    )
    inner_x, inner_y = width * 0.235, height * 0.215
    draw.rounded_rectangle(
        (inner_x, inner_y, width - inner_x, height - inner_y),
        radius=width * 0.03,
        outline=INK_BLUE,
        width=max(2, int(width * 0.008)),
    )


def render_face(face_id: str) -> bytes:
    """把一张标准牌面画成 RGBA PNG（背景透明，墨迹在 RGB，不透明度在 alpha）。"""

    spec = _FACE_BY_ID.get(face_id)
    if spec is None:
        raise KeyError(f"unknown mahjong face: {face_id}")
    width, height = CANVAS
    image = Image.new("RGBA", (width * _SUPERSAMPLE, height * _SUPERSAMPLE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    scale = _SUPERSAMPLE
    draw_w, draw_h = width * scale, height * scale

    if spec.kind in {"dot", "bamboo"}:
        number = int(spec.face_id[0])
        radius = _RADIUS[number] * draw_w
        centers = _LAYOUTS[number]
        # 蓝绿交替最接近真牌；正中间那一颗用红，只有 1/5/9 有正中心。
        middle_index = centers.index((0.5, 0.5)) if (0.5, 0.5) in centers else -1
        for index, (nx, ny) in enumerate(centers):
            if index == middle_index:
                color = INK_RED
            else:
                color = INK_BLUE if index % 2 == 0 else INK_GREEN
            if spec.kind == "dot":
                _draw_dot(draw, nx * draw_w, ny * draw_h, radius, color)
            else:
                _draw_bamboo(draw, nx * draw_w, ny * draw_h, radius, color)
    elif spec.kind == "myriad":
        if not _draw_myriad(draw, int(spec.face_id[0]), draw_w, draw_h):
            raise RuntimeError("需要支持中文的字体才能生成万子牌面")
    elif spec.kind == "honour":
        if not _draw_honour(draw, spec.face_id, draw_w, draw_h):
            raise RuntimeError("需要支持中文的字体才能生成字牌牌面")
    else:
        _draw_blank(draw, draw_w, draw_h)

    image = image.resize((width, height), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def standard_hand(ids: Sequence[str] | None = None) -> list[tuple[str, bytes]]:
    """按给定顺序生成若干张标准牌面；默认引用 DEFAULT_HAND。"""

    chosen = list(ids or DEFAULT_HAND)
    return [(face_id, render_face(face_id)) for face_id in chosen]


def write_standard_faces(directory: Path, ids: Iterable[str] | None = None) -> list[Path]:
    """把标准牌面落成 PNG 文件（渲染脚本只接受文件路径）。"""

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for face_id, content in standard_hand(list(ids) if ids else None):
        path = directory / f"{face_id}.png"
        if not path.is_file():
            path.write_bytes(content)
        written.append(path.resolve())
    return written


if __name__ == "__main__":  # 手动检查：导出整副牌面并拼成一张预览
    import sys

    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("_tmp_render/faces")
    files = write_standard_faces(out_dir, [spec.face_id for spec in STANDARD_FACES])
    columns = 9
    cell = (150, 210)
    rows = (len(files) + columns - 1) // columns
    sheet = Image.new("RGB", (cell[0] * columns, cell[1] * rows), (238, 238, 234))
    for index, path in enumerate(files):
        with Image.open(path) as face:
            tile = face.convert("RGBA").resize(cell, Image.LANCZOS)
        sheet.paste(tile, ((index % columns) * cell[0], (index // columns) * cell[1]), tile)
    sheet.save(out_dir / "sheet.png")
    print(f"wrote {len(files)} faces -> {out_dir} (sheet.png)")
