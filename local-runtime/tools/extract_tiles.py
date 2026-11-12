"""从任意麻将商品图里提取「牌面资产 + 牌型参数」。

设计目标：**不针对任何具体牌面/品牌/颜色**，只依赖图片本身的结构特征。

图分三类，各自用不同策略（自动判别，不写死）：

1. 平铺网格图（全套牌面整齐排列）
   → 投影自相关估周期 → 行列数自适配 → 等分切出每张牌面。
   完全不假设行列数，也不假设牌体颜色，只要求「网格规整 + 每格有内容」。

2. 散落堆叠图（牌叠在一起、有遮挡）
   → 不做牌面分割（实测传统 CV 在此类图上不可靠），
   只采样牌体颜色、材质、牌面底色。

3. 单牌特写
   → 用于校验/补充几何。

牌体颜色不写死：取「牌面之外、饱和度最高」的像素主色，因此蓝色亮片、
金色亮片、墨绿条纹、象牙白都能自动适配。

依赖仅 OpenCV / numpy / Pillow，不加载模型，普通办公本单图 <0.3s。

用法::

    python tools/extract_tiles.py --draft 271 --out _tmp_render/profile_271
    python tools/extract_tiles.py --images a.jpg,b.jpg --out out_dir
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

_DB = Path("outputs/wh-local/workbench.sqlite3")


@dataclass
class GridResult:
    columns: int
    rows: int
    cell_w: float
    cell_h: float
    origin: tuple[int, int]
    faces: list[np.ndarray]
    content_ratio: float


# 牌面短边的标称尺寸（mm）。牌面比例来自图片实测，具体毫米数不影响比例；
# 给一个接近实物短边的值，渲染时灯光/景深/倒角的相对关系才对。
_NOMINAL_SHORT_MM = 30.0
# 俯拍图看不出厚度，按「厚牌」惯例取宽度的这个比例。
_THICKNESS_OVER_WIDTH = 0.62
# 「高清牌面」的最短边下限：低于这个尺寸放到单牌大图上必然发虚。
_HARVEST_MIN_SHORT = 110


def _rgb_hex(color: tuple[int, int, int]) -> str:
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(v))) for v in color)


@dataclass
class TileProfile:
    source: dict[str, str] = field(default_factory=dict)
    image_kind: str = "unknown"
    face_aspect: float | None = None
    face_pixels: tuple[float, float] | None = None
    grid: dict | None = None
    body_color_rgb: tuple[int, int, int] | None = None
    face_base_rgb: tuple[int, int, int] | None = None
    finish: str | None = None
    grain: float | None = None
    ink_colors: list[tuple[int, int, int]] = field(default_factory=list)
    faces: list[np.ndarray] = field(default_factory=list)
    hi_faces: list[np.ndarray] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "image_kind": self.image_kind,
            "face_aspect": None if self.face_aspect is None else round(self.face_aspect, 3),
            "face_pixels": None if self.face_pixels is None else [round(v, 1) for v in self.face_pixels],
            "grid": self.grid,
            "body_color_rgb": None if self.body_color_rgb is None else list(self.body_color_rgb),
            "face_base_rgb": None if self.face_base_rgb is None else list(self.face_base_rgb),
            "finish": self.finish,
            "grain": None if self.grain is None else round(self.grain, 1),
            "ink_colors": [list(color) for color in self.ink_colors],
            "face_count": len(self.faces),
            "hi_face_count": len(self.hi_faces),
        }

    def to_params(self) -> dict:
        """转成 render_tile.py 能直接吃的牌型参数（尺寸/倒角/分层/配色）。

        牌面比例来自图片实测（单张牌面的像素宽高比），这是唯一能保证「视觉上长宽
        一致」的量。厚度与面层比例看不出（商品图都是俯拍），只能按品类惯例给，
        并且写进 param_source 让上层知道这两项是估计值。
        """

        aspect = self.face_aspect or 0.714
        width = _NOMINAL_SHORT_MM
        length = round(width / max(aspect, 1e-6), 1)
        thickness = round(width * _THICKNESS_OVER_WIDTH, 1)
        finish = "glitter" if self.finish == "glitter" else "gloss"
        face_color = _rgb_hex(self.face_base_rgb or (241, 239, 232))
        body_color = _rgb_hex(self.body_color_rgb or self.face_base_rgb or (233, 227, 209))
        # 量不到侧壁（商品图全是俯拍/全景，牌体的侧壁只有几像素、且常被邻牌或促销
        # 横幅盖住），所以分层比例按品类惯例：亮片牌一般是「白面层 + 彩色背层」。
        # 需要精确分层时，得补一张侧面/截面特写。
        face_ratio = 0.42 if finish == "glitter" else 0.0
        layers = [
            {"name": "face", "thickness_ratio": face_ratio, "color": face_color, "finish": "gloss"},
            {
                "name": "body",
                "thickness_ratio": round(1.0 - face_ratio, 2),
                "color": body_color,
                "finish": finish,
            },
        ]
        return {
            "source": self.source,
            "size_mm": [width, length, thickness],
            "bevel_mm": round(width * 0.05, 1),
            "layers": layers,
            # 牌面图案已按整张牌面裁好，cover=1 表示原样铺满牌面（不再缩放/内缩）。
            "face": {"cover": 1.0},
            "param_source": {
                "size_mm": "face_aspect 实测 + 短边 %gmm 标称" % _NOMINAL_SHORT_MM,
                "thickness": "估计值（宽 × %.2f，商品图无侧视）" % _THICKNESS_OVER_WIDTH,
                "layers": "品类惯例（未取到侧壁截面）",
            },
        }


def ink_mask(image: np.ndarray) -> np.ndarray:
    """牌面上的「内容」：有颜色或明显偏暗。纯白牌面与纯白背景都会被排除。"""

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    saturation = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)[..., 1]
    return ((saturation > 35) | (gray < 205)).astype(np.uint8)


def _board_bbox(ink: np.ndarray) -> tuple[int, int, int, int] | None:
    mask = cv2.morphologyEx(ink * 255, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    if count <= 1:
        return None
    best = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    if stats[best, cv2.CC_STAT_AREA] < ink.size * 0.05:
        return None
    points = cv2.findNonZero((labels == best).astype(np.uint8))
    return cv2.boundingRect(points) if points is not None else None


def _period_count(profile: np.ndarray, span: int) -> int:
    """用自相关估「牌」的重复周期，返回该方向上的格数。

    直接对投影做自相关会被牌面内部的花纹周期带偏（实测把 11 列算成 30 列），
    所以先把投影做一次大窗口平滑，抹掉花纹级别的起伏，只保留「一张牌一块」的结构。
    """

    signal = profile.astype(np.float32) - float(profile.mean())
    if not np.isfinite(signal).all() or float(signal.std()) < 1e-6:
        return 1
    window = max(5, int(span / 30) | 1)
    smooth = np.convolve(signal, np.ones(window, np.float32) / window, mode="same")
    corr = np.correlate(smooth, smooth, mode="full")[len(smooth) - 1 :]
    low = max(35, int(span / 40))
    high = max(low + 1, int(span / 2.2))
    if corr[low:high].size == 0:
        return 1
    period = int(low + np.argmax(corr[low:high]))
    if period <= low:
        return 1
    count = int(round(span / period))
    # 周期必须能整除得比较干净，否则说明估歪了。
    residual = abs(span / count - period) / period
    if residual > 0.18:
        return 1
    return max(1, count)


def _edges_are_seams(ink: np.ndarray, x: int, y: int, w: int, h: int, count: int, axis: str) -> bool:
    """校验等分边界是否真的落在牌与牌之间的白缝上。

    很多商品图是「分组排列」（如 1-9 条排一段、再空一格排 1-9 筒），等分会直接
    切在牌中间。若边界处内容占比过高，说明这张图不适合整幅等分，必须拒绝，
    否则会切出一堆半张牌——比不切更糟。
    """

    span = w if axis == "x" else h
    for index in range(1, count):
        position = int(round((x if axis == "x" else y) + index * span / count))
        if axis == "x":
            band = ink[y : y + h, max(0, position - 2) : position + 3]
        else:
            band = ink[max(0, position - 2) : position + 3, x : x + w]
        if band.size and float(band.mean()) > 0.22:
            return False
    return True


def _faces_have_margins(ink: np.ndarray, cells: list[tuple[int, int, int, int]]) -> bool:
    """校验每个格子「四周留白、内容居中」。

    真牌面一定有一圈素色留白；若等分切在牌中间，花纹会顶到格子边缘。
    这比检查边界是否为白缝更稳（牌间缝可能只有 1px）。
    """

    passed = 0
    checked = 0
    for left, top, right, bottom in cells:
        patch = ink[top:bottom, left:right]
        if patch.size == 0:
            continue
        height, width = patch.shape
        margin = max(2, int(min(height, width) * 0.10))
        if height <= margin * 2 or width <= margin * 2:
            continue
        checked += 1
        border = np.concatenate(
            [
                patch[:margin].ravel(),
                patch[-margin:].ravel(),
                patch[:, :margin].ravel(),
                patch[:, -margin:].ravel(),
            ]
        )
        center = patch[margin:-margin, margin:-margin]
        if center.size and float(border.mean()) < float(center.mean()) * 0.55:
            passed += 1
    return checked > 0 and passed / checked >= 0.80


def _segments(profile: np.ndarray, min_len: int, relative: float = 0.10, floor: float = 0.004) -> list[tuple[int, int]]:
    """把一维投影切成「有内容的连续段」。

    牌与牌之间是白缝（内容率接近 0），所以段 = 单张牌。这比等分通用得多：
    分组排列（一段 1-9 条、空一格再 1-9 筒）、不等距、缺张的情况都能自然处理。
    """

    if profile.size == 0:
        return []
    threshold = max(floor, float(profile.max()) * relative)
    segments: list[tuple[int, int]] = []
    start = None
    for index, value in enumerate(profile):
        if value > threshold:
            if start is None:
                start = index
        elif start is not None:
            if index - start >= min_len:
                segments.append((start, index))
            start = None
    if start is not None and len(profile) - start >= min_len:
        segments.append((start, len(profile)))
    return segments


def detect_grid(image: np.ndarray) -> GridResult | None:
    """检测可否切牌面：先试「等分网格」，再试「白缝分段」。

    两条路径互补：
    - 等分：适合整幅严格等距的平铺图（每行牌数相同、牌间缝均匀）；
    - 分段：适合分组排列（1-9 条一段、空一格再 1-9 筒），允许不等距。
    两条都会做结构校验，校验不过就返回 None——宁可不切，也不输出半张牌。
    """

    ink = ink_mask(image)
    bbox = _board_bbox(ink)
    if bbox is None:
        return None
    x, y, w, h = bbox
    if w < 120 or h < 120:
        return None
    roi = ink[y : y + h, x : x + w]
    return (
        _grid_by_white_blocks(image, ink)
        or _grid_equal_split(image, ink, roi, x, y, w, h)
        or _grid_by_seams(image, ink, roi, x, y)
    )


# 白牌的「白」不止一种：纯白底商品图的牌面往往是 248，牌间缝是 210 左右；
# 深色/彩色底的商品图牌面又是 250+。给三档，由严到松试，先能切出规整牌面的那一档算数。
WHITE_LEVELS = ((244, 20), (232, 28), (190, 32))


def white_blocks(image: np.ndarray, level: int = 2) -> tuple[np.ndarray, list[tuple[int, int, int, int]]]:
    """牌面白块：白/近白区域先做孔洞填充，让「被花纹割碎」的牌面重新成为实心块。

    牌内的花纹、描边、印章都被白面包住，填孔后并成一块；牌与牌之间的缝通向
    图外背景，不会被误填。返回 (mask, boxes)，boxes 是 (left, top, width, height)。
    """

    min_value, max_sat = WHITE_LEVELS[max(0, min(level, len(WHITE_LEVELS) - 1))]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    white = ((hsv[..., 1] < max_sat) & (hsv[..., 2] > min_value)).astype(np.uint8)
    white = cv2.morphologyEx(white, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    height, width = white.shape
    flood = white.copy()
    scratch = np.zeros((height + 2, width + 2), np.uint8)
    for seed in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
        if flood[seed[1], seed[0]] == 0:
            cv2.floodFill(flood, scratch, seed, 2)
    filled = ((flood != 2) | (white > 0)).astype(np.uint8)
    filled = cv2.morphologyEx(filled, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(filled, 8)
    boxes = [tuple(int(v) for v in stats[index][:4]) for index in range(1, count)]
    return filled, boxes


def _group_span(values: list[float], tolerance: float) -> list[list[int]]:
    """把一维坐标按间隙分组，用于从块中心反推「几行几列」。"""

    order = sorted(range(len(values)), key=lambda index: values[index])
    groups: list[list[int]] = []
    for index in order:
        if groups and values[index] - values[groups[-1][-1]] <= tolerance:
            groups[-1].append(index)
        else:
            groups.append([index])
    return groups


def _layout_from_boxes(boxes: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int, float]:
    """从白块坐标反推行列结构（用于把「分组排列」也表达成网格）。"""

    widths = [box[2] for box in boxes]
    heights = [box[3] for box in boxes]
    median_w, median_h = float(np.median(widths)), float(np.median(heights))
    centers_y = [box[1] + box[3] / 2 for box in boxes]
    centers_x = [box[0] + box[2] / 2 for box in boxes]
    rows = _group_span(centers_y, median_h * 0.55)
    per_row = []
    for row in rows:
        columns = _group_span([centers_x[index] for index in row], median_w * 0.55)
        per_row.append(len(columns))
    max_columns = max(per_row) if per_row else 0
    filled = sum(per_row)
    coverage = filled / max(len(rows) * max_columns, 1)
    return len(rows), max_columns, int(median_w), int(median_h), coverage


def _grid_by_white_blocks(image: np.ndarray, ink: np.ndarray) -> GridResult | None:
    """按「白色牌面块」切分：适合彩色背景（粉色/木色台面）上的平铺图。

    牌面是白或近白，背景是有色的台面/托盘，色差明显。白底图（背景也是白）
    会全部粘成一块，面积超限后自动拒绝，交给更严的白阈值或等分/分段路径处理。

    这条路径天然支持「分组排列」：每行牌数可以不同（如 6/6/4/4/3），
    不要求等距、不要求整齐，只要求每张牌面各自成块。
    """

    best: GridResult | None = None
    for level in range(len(WHITE_LEVELS)):
        result = _grid_from_white_level(image, level)
        if result is None:
            continue
        # 同一张图在不同白阈值下能切出的牌数不同：要牌最多的那一档
        # （严格阈值会把相邻两张牌并成一块，少切一半）。
        if best is None or (len(result.faces), result.content_ratio) > (len(best.faces), best.content_ratio):
            best = result
    return best


def _grid_from_white_level(image: np.ndarray, level: int) -> GridResult | None:
    white, boxes = white_blocks(image, level)
    total = float(image.shape[0] * image.shape[1])
    faces: list[np.ndarray] = []
    kept: list[tuple[int, int, int, int]] = []
    for left, top, width, height in boxes:
        area = width * height
        ratio = area / total
        # 单张牌面约占整图的 0.1%~6%；超出说明粘连成片，不适合这条路。
        if not 0.0008 <= ratio <= 0.06:
            continue
        if width < 30 or height < 30:
            continue
        if not 0.42 <= min(width, height) / max(width, height) <= 0.95:
            continue
        if white[top : top + height, left : left + width].mean() < 0.80:
            continue
        faces.append(image[top : top + height, left : left + width].copy())
        kept.append((left, top, width, height))
    if len(faces) < 8:
        return None
    widths = [box[2] for box in kept]
    heights = [box[3] for box in kept]
    median_w, median_h = float(np.median(widths)), float(np.median(heights))
    consistent = sum(
        1
        for width, height in zip(widths, heights)
        if abs(width - median_w) / median_w < 0.18 and abs(height - median_h) / median_h < 0.18
    )
    if consistent / len(faces) < 0.90:
        return None
    rows, columns, cell_w, cell_h, coverage = _layout_from_boxes(kept)
    return GridResult(
        columns=columns,
        rows=rows,
        cell_w=float(cell_w),
        cell_h=float(cell_h),
        origin=(kept[0][0], kept[0][1]),
        faces=faces,
        content_ratio=coverage,
    )


def _grid_equal_split(
    image: np.ndarray,
    ink: np.ndarray,
    roi: np.ndarray,
    x: int,
    y: int,
    w: int,
    h: int,
) -> GridResult | None:
    columns = _period_count(roi.mean(axis=0), w)
    rows = _period_count(roi.mean(axis=1), h)
    if columns < 3 or rows < 2:
        return None
    cell_w, cell_h = w / columns, h / rows
    if cell_w < 24 or cell_h < 24:
        return None
    if not 0.35 <= min(cell_w, cell_h) / max(cell_w, cell_h) <= 0.98:
        return None
    cells = [
        (
            int(round(x + col_index * cell_w)),
            int(round(y + row_index * cell_h)),
            int(round(x + (col_index + 1) * cell_w)),
            int(round(y + (row_index + 1) * cell_h)),
        )
        for row_index in range(rows)
        for col_index in range(columns)
    ]
    if not _faces_have_margins(ink, cells):
        return None
    faces: list[np.ndarray] = []
    for left, top, right, bottom in cells:
        cell_ink = ink[top:bottom, left:right]
        if cell_ink.size == 0 or cell_ink.mean() < 0.015:
            continue
        faces.append(image[top:bottom, left:right].copy())
    if len(faces) < 0.6 * len(cells):
        return None
    return GridResult(
        columns=columns,
        rows=rows,
        cell_w=cell_w,
        cell_h=cell_h,
        origin=(x, y),
        faces=faces,
        content_ratio=len(faces) / max(len(cells), 1),
    )


def _grid_by_seams(
    image: np.ndarray,
    ink: np.ndarray,
    roi: np.ndarray,
    x: int,
    y: int,
) -> GridResult | None:
    """按白缝逐行/逐段切牌，允许不等距与分组排列。"""

    row_bands = _segments(roi.mean(axis=1), min_len=max(20, roi.shape[0] // 30))
    if not row_bands:
        return None
    faces: list[np.ndarray] = []
    widths: list[int] = []
    heights: list[int] = []
    per_band: list[int] = []
    for top, bottom in row_bands:
        band = roi[top:bottom]
        band_height = bottom - top
        if band_height < 24:
            continue
        found = 0
        for left, right in _segments(band.mean(axis=0), min_len=max(12, band_height // 6)):
            band_width = right - left
            if band_width < 12:
                continue
            if not 0.42 <= min(band_width, band_height) / max(band_width, band_height) <= 0.95:
                continue
            cell = (x + left, y + top, x + right, y + bottom)
            if not _faces_have_margins(ink, [cell]):
                continue
            faces.append(image[cell[1] : cell[3], cell[0] : cell[2]].copy())
            widths.append(band_width)
            heights.append(band_height)
            found += 1
        if found:
            per_band.append(found)

    if len(faces) < 6:
        return None
    median_w, median_h = float(np.median(widths)), float(np.median(heights))
    consistent = sum(
        1
        for width, height in zip(widths, heights)
        if abs(width - median_w) / median_w < 0.18 and abs(height - median_h) / median_h < 0.18
    )
    if consistent / len(faces) < 0.90:
        return None
    # 每「行」的牌数必须接近，否则说明段分割把相邻牌并成了一段。
    if len(per_band) < 2:
        return None
    counts = np.array(per_band, dtype=np.float32)
    if float(counts.std() / max(counts.mean(), 1e-6)) > 0.22:
        return None
    return GridResult(
        columns=int(round(float(counts.mean()))),
        rows=len(per_band),
        cell_w=median_w,
        cell_h=median_h,
        origin=(x, y),
        faces=faces,
        content_ratio=len(faces) / max(len(per_band) * int(round(float(counts.mean()))), 1),
    )


def _dominant_colors(pixels: np.ndarray, top: int = 3) -> list[tuple[int, int, int]]:
    if pixels.size == 0:
        return []
    if len(pixels) > 20000:
        picks = np.random.default_rng(0).choice(len(pixels), 20000, replace=False)
        pixels = pixels[picks]
    data = pixels.reshape(-1, 3).astype(np.float32)
    k = max(1, min(top, len(data)))
    _, labels, centers = cv2.kmeans(
        data,
        k,
        None,
        (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0),
        3,
        cv2.KMEANS_PP_CENTERS,
    )
    counts = np.bincount(labels.reshape(-1), minlength=k)
    order = np.argsort(-counts)
    return [tuple(int(v) for v in centers[index]) for index in order]


def _body_color_from_faces(faces: list[np.ndarray]) -> tuple[int, int, int] | None:
    """从平铺图里找「整格单一高饱和色」的格子——那是牌背/纯色牌，即牌体本色。

    散落图上取色不可靠：牌体只从侧面露出窄条，而牌面花纹往往是更大的色块
    （实测把蓝色亮片牌判成深绿）。平铺图里牌背是整格纯色，最可靠。
    """

    for face in faces:
        if face.size == 0:
            continue
        hsv = cv2.cvtColor(face, cv2.COLOR_BGR2HSV)
        saturation = hsv[..., 1].astype(np.float32)
        value = hsv[..., 2].astype(np.float32)
        # 整格都偏饱和、且颜色分布均匀 → 纯色牌背。
        if float(saturation.mean()) > 70 and float(saturation.std()) < 45 and float(np.median(value)) > 70:
            rgb = cv2.cvtColor(face, cv2.COLOR_BGR2RGB).reshape(-1, 3)
            return tuple(int(v) for v in np.median(rgb, axis=0))
    return None


def sample_scatter(image: np.ndarray) -> dict:
    """散落图只做采样：牌体色、牌面底色、材质颗粒度、墨色。"""

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    total = float(image.shape[0] * image.shape[1])
    height, width = image.shape[:2]

    # 牌面白块（形状正常、尺寸像一张牌），排除背景白、排除粘连成片的大块。
    # 三档白阈值里取「能找出最多牌面」的那一档：纯白底图要严、彩色底图要松。
    white = np.zeros(image.shape[:2], np.uint8)
    face_boxes: list[tuple[int, int, int, int]] = []
    for level in range(len(WHITE_LEVELS)):
        level_mask, level_boxes = white_blocks(image, level)
        level_faces = [
            (left, top, block_w, block_h)
            for left, top, block_w, block_h in level_boxes
            if 0.0002 <= (block_w * block_h) / total <= 0.25
            and block_w >= 24
            and block_h >= 24
            and 0.42 <= min(block_w, block_h) / max(block_w, block_h) <= 0.95
            and level_mask[top : top + block_h, left : left + block_w].mean() >= 0.75
        ]
        if len(level_faces) > len(face_boxes):
            white, face_boxes = level_mask, level_faces

    def near_face(box: tuple[int, int, int, int]) -> bool:
        """牌体只会紧挨着牌面出现；背景/横幅不和牌面成对。"""

        if not face_boxes:
            return True
        left, top, block_w, block_h = box
        for face_left, face_top, face_w, face_h in face_boxes:
            gap_x = max(0, max(face_left, left) - min(face_left + face_w, left + block_w))
            gap_y = max(0, max(face_top, top) - min(face_top + face_h, top + block_h))
            limit = max(face_w, face_h) * 0.9
            if gap_x <= limit and gap_y <= limit:
                return True
        return False

    # 牌体色：高饱和的实心圆角块，且必须「不贴图边 + 紧挨牌面 + 尺寸像一张牌」。
    # 这三条把木纹台面、促销横幅、药丸按钮排除在外——它们要么贴边，要么和牌不等大。
    body_raw = ((hsv[..., 1] > 90) & (hsv[..., 2] > 90)).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(body_raw, 8)
    edge = max(2, int(min(height, width) * 0.01))
    face_w = float(np.median([box[2] for box in face_boxes])) if face_boxes else None
    face_h = float(np.median([box[3] for box in face_boxes])) if face_boxes else None
    body_indexes: list[int] = []
    for index in range(1, count):
        left, top, block_w, block_h, area = stats[index]
        if area < max(800.0, total * 0.0008) or area / total > 0.30:
            continue
        if area / float(block_w * block_h) < 0.82:
            continue
        if not 0.20 <= min(block_w, block_h) / max(block_w, block_h) <= 1.0:
            continue
        if left <= edge or top <= edge or left + block_w >= width - edge or top + block_h >= height - edge:
            continue
        if face_w and face_h:
            if not (0.7 * face_w <= block_w <= 1.7 * face_w and 0.7 * face_h <= block_h <= 1.7 * face_h):
                continue
        if not near_face((left, top, block_w, block_h)):
            continue
        body_indexes.append(int(index))

    body_colors: list[tuple[int, int, int]] = []
    if body_indexes:
        pixels = np.concatenate([rgb[labels == index] for index in body_indexes])
        if pixels.size:
            body_colors = _dominant_colors(pixels, 2)

    # 牌面底色：牌面白块里的低饱和高亮像素。
    face_base = None
    base = (hsv[..., 1] < 25) & (hsv[..., 2] > 215)
    if face_boxes:
        patch = np.zeros(base.shape, bool)
        for left, top, block_w, block_h in face_boxes:
            patch[top : top + block_h, left : left + block_w] = True
        base &= patch
    if base.sum() > 500:
        face_base = tuple(int(v) for v in np.median(rgb[base], axis=0))

    # 墨色：只在牌面上找（否则木纹台面会被当成「墨」）。
    surface = np.zeros(base.shape, bool)
    for left, top, block_w, block_h in face_boxes:
        surface[top : top + block_h, left : left + block_w] = True
    ink = ((hsv[..., 1] > 60) & (hsv[..., 2] > 60) & (hsv[..., 2] < 235)) | (hsv[..., 2] < 120)
    ink &= surface
    ink_colors = _dominant_colors(rgb[ink], 3) if ink.sum() > 500 else []

    # 材质颗粒：优先量牌体（亮片在牌体上最明显），没有牌体才退回牌面。
    grain = None
    if body_indexes:
        grain_mask = np.isin(labels, body_indexes)
    else:
        grain_mask = surface
    if grain_mask.sum() > total * 0.01:
        patch = cv2.Laplacian(gray, cv2.CV_32F)
        grain = float(np.abs(patch[grain_mask]).mean())

    return {
        "body_colors": body_colors,
        "face_base": face_base,
        "ink_colors": ink_colors,
        "grain": grain,
    }


def _agreeing_color(candidates: list[tuple[int, int, int]], min_votes: int = 2) -> tuple[int, int, int] | None:
    """多张图各自给出的牌体色必须互相印证，单张图里的偶发色块（横幅、花纹）不算数。"""

    if not candidates:
        return None
    best: list[tuple[int, int, int]] | None = None
    for anchor in candidates:
        group = [color for color in candidates if max(abs(a - b) for a, b in zip(anchor, color)) <= 30]
        if best is None or len(group) > len(best):
            best = group
    if best is None or len(best) < min_votes:
        return None
    return tuple(int(v) for v in np.median(np.array(best, dtype=np.float32), axis=0))


def load_draft_images(draft_id: int, limit: int = 30) -> list[Path]:
    con = sqlite3.connect(str(_DB))
    cur = con.cursor()
    cur.execute(
        "select local_path from product_processing_source_images"
        " where product_draft_id = ? order by id limit ?",
        (draft_id, limit),
    )
    paths = [Path(row[0]) for row in cur.fetchall() if row[0]]
    con.close()
    return [path for path in paths if path.is_file()]


def build_profile(paths: list[Path], out_dir: Path) -> TileProfile:
    """遍历全部图：优先找可用的平铺网格拿牌面，散落图负责采样。"""

    profile = TileProfile()
    best: tuple[float, GridResult, Path] | None = None
    samples: list[dict] = []
    harvested: list[np.ndarray] = []

    for path in paths:
        image = cv2.imread(str(path))
        if image is None:
            continue
        if max(image.shape[:2]) > 2400:
            scale = 2400 / max(image.shape[:2])
            image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        grid = detect_grid(image)
        if grid is not None:
            # 牌面越多、每格越大越好。
            score = len(grid.faces) * float(np.sqrt(grid.cell_w * grid.cell_h))
            if best is None or score > best[0]:
                best = (score, grid, path)
        else:
            samples.append({"path": path, **sample_scatter(image)})
        harvested.extend(harvest_faces(image, min_short=_HARVEST_MIN_SHORT))

    if best is not None:
        _, grid, path = best
        profile.image_kind = "flat_lay_grid"
        profile.source["flat_lay"] = path.name
        profile.faces = grid.faces
        profile.face_aspect = min(grid.cell_w, grid.cell_h) / max(grid.cell_w, grid.cell_h)
        profile.face_pixels = (grid.cell_w, grid.cell_h)
        profile.grid = {
            "columns": grid.columns,
            "rows": grid.rows,
            "faces": len(grid.faces),
            "content_ratio": round(grid.content_ratio, 3),
        }
        # 平铺图的单牌只有一百多像素，放到单牌大图会糊。特写/散落图里偶尔有完整
        # 且更大的牌面，挑出来专门给「单牌主图」用。
        cell_short = min(grid.cell_w, grid.cell_h)
        profile.hi_faces = [
            patch for patch in harvested if min(patch.shape[:2]) > cell_short * 1.3
        ]
    elif samples:
        profile.image_kind = "scatter"

    # 采样类信息优先取「有牌体」的图（散落图比平铺图更容易看到牌体侧壁）。
    # 但牌体本色最可靠的来源是平铺图里的纯色牌背，优先用它。
    body_color = _body_color_from_faces(profile.faces) if profile.faces else None
    body = _agreeing_color([tuple(item["body_colors"][0]) for item in samples if item["body_colors"]])
    if body_color is None and body is None and best is not None:
        extra = sample_scatter(cv2.imread(str(best[2]))).get("body_colors") or []
        body = tuple(extra[0]) if extra else None
    profile.body_color_rgb = tuple(body_color) if body_color else body
    face_base = next((item["face_base"] for item in samples if item["face_base"]), None)
    if face_base is None and best is not None:
        face_base = sample_scatter(cv2.imread(str(best[2]))).get("face_base")
    profile.face_base_rgb = tuple(face_base) if face_base else None
    # 通体米白/象牙白的牌没有「彩色牌体」，此时牌体色就是牌面底色。
    if profile.body_color_rgb is None and profile.face_base_rgb is not None:
        profile.body_color_rgb = profile.face_base_rgb
    ink = next((item["ink_colors"] for item in samples if item["ink_colors"]), [])
    if not ink and profile.faces:
        ink = sample_scatter(cv2.imread(str(best[2]))).get("ink_colors") or []
    profile.ink_colors = [tuple(color) for color in ink]

    grains = [item["grain"] for item in samples if item["grain"]]
    if grains:
        profile.grain = float(np.median(grains))
        # 亮片牌的颗粒能量明显高于纯色牌；阈值来自实测分布，不是针对某个品牌。
        profile.finish = "glitter" if profile.grain > 9.0 else "solid"
    else:
        profile.finish = "solid"
    profile.source["sampled"] = ",".join(item["path"].name[:8] for item in samples[:4])
    return profile


def _has_internal_seam(patch: np.ndarray) -> bool:
    """牌面内部是否有一条贯通的空行/空列——有就说明这张「牌」其实是多张并在一起。

    紧挨着摆的牌，白块会因为缝太细连成一整块；2×2 并块的长宽比和单张牌一模一样，
    只靠形状区分不出来，得看里面有没有牌与牌之间的白缝。
    """

    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    ink = ((hsv[..., 1] > 45) | (hsv[..., 2] < 190))
    height, width = ink.shape
    if height < 12 or width < 12:
        return True
    for row in range(int(height * 0.20), int(height * 0.80)):
        if float(ink[row].mean()) < 0.02:
            return True
    for column in range(int(width * 0.20), int(width * 0.80)):
        if float(ink[:, column].mean()) < 0.02:
            return True
    return False


def harvest_faces(image: np.ndarray, *, min_short: int = _HARVEST_MIN_SHORT) -> list[np.ndarray]:
    """从任意图里挑「完整且够大」的牌面，不要求成网格。

    平铺图的单牌往往只有一百多像素，放到单牌主图会糊；特写/散落图里偶尔有一张
    正对镜头的完整牌面，像素是平铺图的两三倍。这里只做形状、完整性与「只有一张牌」
    的把关：圆角矩形、占满外接框、离图边有余量、面上有花纹、内部没有贯通白缝。
    斜放/被遮挡/多张并块的都会被拒掉。
    """

    total = float(image.shape[0] * image.shape[1])
    height, width = image.shape[:2]
    found: list[np.ndarray] = []
    best_level = -1
    for level in range(len(WHITE_LEVELS)):
        white, boxes = white_blocks(image, level)
        crops: list[np.ndarray] = []
        for left, top, block_w, block_h in boxes:
            if not 0.0004 <= (block_w * block_h) / total <= 0.25:
                continue
            if min(block_w, block_h) < min_short:
                continue
            # 麻将是竖长条；横躺/斜放会被长宽比挡掉，避免把侧壁当成牌面。
            if not 0.55 <= min(block_w, block_h) / max(block_w, block_h) <= 0.85:
                continue
            if white[top : top + block_h, left : left + block_w].mean() < 0.90:
                continue
            if left < 3 or top < 3 or left + block_w > width - 3 or top + block_h > height - 3:
                continue
            patch = image[top : top + block_h, left : left + block_w]
            hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
            ink = float(((hsv[..., 1] > 45) | (hsv[..., 2] < 190)).mean())
            if not 0.04 <= ink <= 0.55:
                continue
            if _has_internal_seam(patch):
                continue
            crops.append(patch.copy())
        if len(crops) > len(found):
            found, best_level = crops, level
    if best_level < 0:
        return []
    found.sort(key=lambda patch: -patch.shape[0] * patch.shape[1])
    return found


def face_ink_rgba(face: np.ndarray, base_rgb: tuple[int, int, int] | None) -> np.ndarray:
    """把裁好的牌面转成「墨迹贴图」：牌面底色透明，花纹不透明。

    render_tile.py 拿 alpha 当刻纹遮罩。整张牌面照片直接贴上去会连白底一起变成
    墨，渲出来是贴纸；剥掉底色后，牌面底色由参数里的 face 层颜色提供。
    """

    if base_rgb is None:
        base = np.median(face.reshape(-1, 3), axis=0).astype(np.float32)
    else:
        base = np.array(base_rgb[::-1], np.float32)  # face 是 BGR
    observed = face.astype(np.float32)
    diff = np.abs(observed - base).max(axis=2)
    alpha = np.clip((diff - 8.0) / 26.0, 0.0, 1.0)
    # 笔画边缘的像素是「墨 + 白」的混合色，直接拿来用会在笔画外圈留一圈浅色白边。
    # 按 alpha 反解出真实墨色：observed = a·ink + (1-a)·base。
    safe = np.maximum(alpha, 0.18)[..., None]
    color = np.clip((observed - (1.0 - safe) * base) / safe, 0.0, 255.0)
    return np.dstack([color.astype(np.uint8), (alpha * 255).astype(np.uint8)])


def write_outputs(profile: TileProfile, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "profile.json").write_text(
        json.dumps(profile.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "tile_params.json").write_text(
        json.dumps(profile.to_params(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    faces_dir = out_dir / "faces"
    faces_dir.mkdir(exist_ok=True)
    ink_dir = out_dir / "faces_ink"
    ink_dir.mkdir(exist_ok=True)
    hi_dir = out_dir / "faces_hi_ink"
    hi_dir.mkdir(exist_ok=True)
    for stale in list(faces_dir.glob("*.png")) + list(ink_dir.glob("*.png")) + list(hi_dir.glob("*.png")):
        stale.unlink()
    for index, face in enumerate(profile.faces):
        cv2.imwrite(str(faces_dir / f"{index:02d}.png"), face)
        cv2.imwrite(str(ink_dir / f"{index:02d}.png"), face_ink_rgba(face, profile.face_base_rgb))
    for index, face in enumerate(profile.hi_faces):
        cv2.imwrite(str(hi_dir / f"{index:02d}.png"), face_ink_rgba(face, profile.face_base_rgb))
    if not profile.faces:
        stale_sheet = out_dir / "cards.png"
        if stale_sheet.exists():
            stale_sheet.unlink()
        return
    thumb_w = max(60, min(120, int(profile.face_pixels[0]) if profile.face_pixels else 100))
    thumb_h = int(thumb_w * (profile.face_pixels[1] / profile.face_pixels[0])) if profile.face_pixels else int(thumb_w * 1.4)
    columns = min(11, max(1, len(profile.faces)))
    rows = (len(profile.faces) + columns - 1) // columns
    sheet = Image.new("RGB", (thumb_w * columns, thumb_h * rows), (255, 255, 255))
    for index, face in enumerate(profile.faces):
        patch = Image.fromarray(cv2.cvtColor(face, cv2.COLOR_BGR2RGB)).resize((thumb_w - 3, thumb_h - 3), Image.LANCZOS)
        sheet.paste(patch, ((index % columns) * thumb_w + 1, (index // columns) * thumb_h + 1))
    sheet.save(out_dir / "cards.png")


def main() -> None:
    parser = argparse.ArgumentParser(description="通用麻将牌面提取器")
    parser.add_argument("--draft", type=int, default=0)
    parser.add_argument("--images", default="")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    if args.images:
        paths = [Path(item.strip()) for item in args.images.split(",") if item.strip()]
    elif args.draft:
        paths = load_draft_images(args.draft)
    else:
        raise SystemExit("需要 --draft 或 --images")

    profile = build_profile(paths, Path(args.out))
    write_outputs(profile, Path(args.out))
    print(json.dumps(profile.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
