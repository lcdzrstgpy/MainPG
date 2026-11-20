"""麻将专业模式：把牌面图案扣成资产，AI 只出空白牌，再把图案贴回去。

为什么单独成模块：
- 麻将的“字/花纹”就是商品本体，交给生图模型重画必然造字、错纹；
- 正确做法是把图案当资产（RGBA + 归一化 bbox），空白牌体只负责材质、倒角、光影；
- 回贴时用 multiply 混合，让图案继承空白牌的光影，避免“贴纸感”。

处理顺序（也是耗时从大到小的顺序）：
1. 四边形检测 + 透视矫正（每条边一次 warp，成本固定）；
2. 图案转正：小角度倾斜用对称性搜索找水平位；
3. 对称补齐：只有左右/上下/中心对称且缺失是“一整块”时才补，非对称字形（如「七」）不补；
4. 清晰化：UnsharpMask 只作用在颜色通道，alpha 保持原样；
5. 落库：按 SKU/品类缓存，同一花纹只存一份。

本模块只做本地像素运算：不调用 provider、不写远程 URL。
"""

from __future__ import annotations

import hashlib
import io
import json
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageChops, ImageFilter

# 麻将专属改写：**保留原始实景与多牌构图**，只把牌面留白。
#
# 为什么不再用「白底单牌」提示词：麻将的卖点是台面、暖光、成堆/成排的牌与手部使用，
# 一旦换成纯灰底单牌，画面和原始轮播完全是两个商品。现在改成在普通四宫格提示词
# 末尾追加这条最高优先级规则：场景/道具/机位/光影全部不动，只有牌的脸留白，
# 真实字纹由本地图案库按透视贴回（见 ``paste_patterns_in_scene``）。
MAHJONG_BLANK_FACE_RULE = """MAHJONG FACE OVERRIDE (highest priority; applies to every panel and every tile):
- Keep the scene exactly as planned above: same surface, background, props, stacks, hands, depth of field, light direction and camera.
- Keep every mahjong tile at its real size, position, tilt, thickness and bevel. Never reduce the shot to a single tile and never replace the scene with a plain studio background.
- ONLY the tile FRONT FACES change: each visible face is left plain and unmarked - no character, numeral, letter, symbol, flower, dot pattern, engraving or printed motif on any face.
- Do not invent tile face artwork, do not copy the reference faces, do not add any sticker or label on the tiles.
- Everything else (quantity of tiles, arrangement, material, colour of the tile body) stays faithful to the reference."""


_TILE_FACE_SIZE = (300, 400)
_MIN_FACE_AREA_RATIO = 0.04
_MIN_PATTERN_AREA_RATIO = 0.0015
_MAX_PATTERN_AREA_RATIO = 0.90
_PATTERN_MARGIN_RATIO = 0.12

# 实景贴图：牌在场景里更小、更斜，检测阈值比「空白牌单拍」宽松一档。
_SCENE_TILE_MIN_AREA = 0.004
_SCENE_TILE_MAX_AREA = 0.12
_SCENE_TILE_ASPECT = (0.30, 1.25)
_MAX_TILE_SIDE_RATIO = 1.9
_QUAD_INSET = 0.06

# 图案库抠图的质量闸：真实牌面是「浅底 + 少量刻纹」，
# 深色/大面积墨块基本都是照片背景或拼贴素材，直接判废，避免把垃圾贴到牌上。
# 阈值取「尽量多抠、接受残缺」：先保证有花纹可用，再由 confidence 排序取优。
_MIN_INK_RATIO = 0.015
_MAX_INK_RATIO = 0.45
_MIN_INK_CONTRAST = 16.0
_MIN_FACE_LUMA = 105.0
_MAX_BBOX_WIDTH = 0.95
_MAX_BBOX_HEIGHT = 0.97
# 贴回时统一按「牌面短边的 66%」缩放：不依赖每张图各自的 bbox，
# 否则不同来源的图案在同一张牌上会一大一小。
_FACE_PATTERN_SCALE = 0.66

# 转正/补齐的保守阈值：宁可留着轻微歪斜，也不能把非对称字形补成“假字”。
_MAX_DESKEW_DEG = 12.0
_DESKEW_STEP_DEG = 1.0
_DESKEW_MIN_GAIN = 0.02
_COMPLETE_SYMMETRY_GATE = 0.55
_COMPLETE_TARGET_SCORE = 0.85
_COMPLETE_MAX_MISSING = 0.25
_COMPLETE_MIN_BLOB_SHARE = 0.55
_SYMMETRY_NAMES = ("vertical", "horizontal", "radial")


@dataclass(frozen=True)
class PatternAsset:
    """一个可回贴的牌面图案资产。

    ``bbox`` 是图案在牌面内框里的归一化位置/尺寸，回贴时按它还原“实际大小比例”；
    旋转/补齐只改 ``content``，bbox 由调用方按内容尺寸等比修正。
    """

    content: bytes  # RGBA PNG，已裁到图案 bbox
    bbox: tuple[float, float, float, float]  # 相对牌面内框的归一化 x/y/w/h
    face_aspect: float  # 牌面宽高比
    signature: str
    source_index: int
    blank_face: bytes  # 同一牌面抹掉图案后的空白牌（本地兜底用）
    label: str = ""
    rotation_deg: float = 0.0
    flip_h: bool = False
    symmetry_type: str = "none"
    symmetry_score: float = 0.0
    completion_ratio: float = 0.0
    confidence: float = 0.0


class MahjongPatternLibrary:
    """按 SKU/品类持久化的图案库：PNG 存图 + manifest.json 存元数据。

    同一花纹按 signature 去重，跨商品复用；缓存命中时整条链路不再重复抠图。
    """

    _MANIFEST = "manifest.json"

    def __init__(self, root: Path | str, *, max_assets: int = 48) -> None:
        self.root = Path(root)
        self.max_assets = max(1, int(max_assets))

    def _key_dir(self, cache_key: str) -> Path:
        key = str(cache_key or "default").strip() or "default"
        slug = re.sub(r"[^0-9A-Za-z_-]+", "-", key)[:32].strip("-") or "default"
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        return self.root / f"{slug}-{digest}"

    def load(self, cache_key: str) -> list[PatternAsset]:
        directory = self._key_dir(cache_key)
        manifest_path = directory / self._MANIFEST
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        items = payload.get("assets") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return []
        assets: list[PatternAsset] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                content = (directory / str(item["pattern"])).read_bytes()
                blank = (directory / str(item["blank"])).read_bytes()
                bbox = tuple(float(value) for value in item["bbox"])
            except (OSError, KeyError, TypeError, ValueError):
                continue
            if len(bbox) != 4:
                continue
            assets.append(
                PatternAsset(
                    content=content,
                    bbox=(bbox[0], bbox[1], bbox[2], bbox[3]),
                    face_aspect=float(item.get("face_aspect") or 0.75),
                    signature=str(item.get("signature") or ""),
                    source_index=int(item.get("source_index") or 0),
                    blank_face=blank,
                    label=str(item.get("label") or ""),
                    rotation_deg=float(item.get("rotation_deg") or 0.0),
                    flip_h=bool(item.get("flip_h")),
                    symmetry_type=str(item.get("symmetry_type") or "none"),
                    symmetry_score=float(item.get("symmetry_score") or 0.0),
                    completion_ratio=float(item.get("completion_ratio") or 0.0),
                    confidence=float(item.get("confidence") or 0.0),
                )
            )
        return assets

    def save(self, cache_key: str, assets: Sequence[PatternAsset]) -> None:
        directory = self._key_dir(cache_key)
        directory.mkdir(parents=True, exist_ok=True)
        entries: list[dict[str, object]] = []
        for asset in assets[: self.max_assets]:
            name = re.sub(r"[^0-9A-Za-z]", "", asset.signature)[:32] or "pattern"
            pattern_name = f"{name}.png"
            blank_name = f"{name}-blank.png"
            (directory / pattern_name).write_bytes(asset.content)
            (directory / blank_name).write_bytes(asset.blank_face)
            entries.append(
                {
                    "signature": asset.signature,
                    "pattern": pattern_name,
                    "blank": blank_name,
                    "bbox": list(asset.bbox),
                    "face_aspect": asset.face_aspect,
                    "source_index": asset.source_index,
                    "label": asset.label,
                    "rotation_deg": asset.rotation_deg,
                    "flip_h": asset.flip_h,
                    "symmetry_type": asset.symmetry_type,
                    "symmetry_score": asset.symmetry_score,
                    "completion_ratio": asset.completion_ratio,
                    "confidence": asset.confidence,
                }
            )
        temp = directory / f"{self._MANIFEST}.tmp"
        temp.write_text(
            json.dumps({"version": 1, "assets": entries}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp.replace(directory / self._MANIFEST)

    def extend(self, cache_key: str, assets: Sequence[PatternAsset]) -> list[PatternAsset]:
        """把新资产并进库（按 signature 去重），返回合并后的列表。"""
        merged = self.load(cache_key)
        seen = {asset.signature for asset in merged}
        added = False
        for asset in assets:
            if not asset.signature or asset.signature in seen:
                continue
            seen.add(asset.signature)
            merged.append(asset)
            added = True
            if len(merged) >= self.max_assets:
                break
        if added or not (self._key_dir(cache_key) / self._MANIFEST).exists():
            self.save(cache_key, merged)
        return merged


def _cv2():
    try:
        import cv2  # type: ignore
    except Exception:
        return None
    return cv2


def _decode(content: bytes):
    cv2 = _cv2()
    if cv2 is None:
        return None
    import numpy as np

    array = np.frombuffer(content, dtype=np.uint8)
    return cv2.imdecode(array, cv2.IMREAD_COLOR)


def _decode_rgba(content: bytes) -> Image.Image | None:
    try:
        return Image.open(io.BytesIO(content)).convert("RGBA")
    except (OSError, ValueError):
        return None


def _encode_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _order_points(points):
    import numpy as np

    pts = np.asarray(points, dtype="float32").reshape(4, 2)
    sums = pts.sum(axis=1)
    diffs = np.diff(pts, axis=1).reshape(-1)
    return np.array(
        [
            pts[np.argmin(sums)],
            pts[np.argmin(diffs)],
            pts[np.argmax(sums)],
            pts[np.argmax(diffs)],
        ],
        dtype="float32",
    )


def detect_face_quads(
    content: bytes,
    *,
    min_area_ratio: float = _MIN_FACE_AREA_RATIO,
    max_area_ratio: float = 1.0,
    aspect_range: tuple[float, float] = (0.45, 1.05),
) -> list[list[tuple[float, float]]]:
    """返回牌面四边形（按面积从大到小）；cv2 不可用或无结果时返回空。

    低对比白牌在强阈值下会丢边，因此用多档 Canny 合并结果，再按中心/尺寸去重。
    实景里的牌更小更斜，调用方（``detect_tile_quads``）会放宽面积与长宽比。
    """
    cv2 = _cv2()
    if cv2 is None:
        return []
    image = _decode(content)
    if image is None:
        return []
    import numpy as np

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    total = max(1, image.shape[0] * image.shape[1])
    low_aspect, high_aspect = aspect_range
    # 象牙白牌拍在白底上对比度很低，固定阈值经常一条边都出不来；
    # 再补一档按全图中位数自适应的阈值（经典 0.66/1.33 系数）。
    median = float(cv2.medianBlur(gray, 5).mean())
    adaptive_low = int(max(5.0, 0.66 * median))
    adaptive_high = int(min(255.0, 1.33 * median))
    thresholds = [(12, 36), (25, 75), (40, 120)]
    if adaptive_high > adaptive_low + 10:
        thresholds.append((adaptive_low, adaptive_high))
    found: list[tuple[float, float, float, float, float, list[tuple[float, float]]]] = []
    for low, high in thresholds:
        edges = cv2.Canny(gray, low, high)
        edges = cv2.dilate(edges, None, iterations=1)
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < total * min_area_ratio or area > total * max_area_ratio:
                continue
            perimeter = cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
            if len(approx) < 4 or len(approx) > 8:
                continue
            # 牌是圆角矩形：严格四点近似经常退化失败，这里改用最小外接矩形，
            # 再用「轮廓面积 / 外接矩形面积」校验它确实是一块矩形面。
            rect = cv2.minAreaRect(contour)
            (rect_w, rect_h) = rect[1]
            rect_area = float(rect_w) * float(rect_h)
            if min(rect_w, rect_h) < 20 or rect_area <= 0:
                continue
            # 一张牌是 3:4 左右；「一排/一堵牌墙」会被描成长条（长边/短边 3-5 倍），
            # 直接把长条判掉，否则图案会被拉成一整条贴在墙上。
            if max(rect_w, rect_h) / max(1.0, min(rect_w, rect_h)) > _MAX_TILE_SIDE_RATIO:
                continue
            if float(area) / rect_area < 0.72:
                continue
            box = cv2.boxPoints(rect)
            x, y, w, h = cv2.boundingRect(np.asarray(box, dtype="float32"))
            ratio = w / max(h, 1)
            if not low_aspect <= ratio <= high_aspect:
                continue
            cx, cy = x + w / 2, y + h / 2
            duplicate = any(
                abs(cx - other_cx) < max(w, other_w) * 0.15
                and abs(cy - other_cy) < max(h, other_h) * 0.15
                and abs(w - other_w) < max(w, other_w) * 0.15
                and abs(h - other_h) < max(h, other_h) * 0.15
                for _a, other_cx, other_cy, other_w, other_h, _q in found
            )
            if duplicate:
                continue
            ordered = _order_points(box)
            found.append(
                (
                    float(area),
                    cx,
                    cy,
                    float(w),
                    float(h),
                    [(float(px), float(py)) for px, py in ordered],
                )
            )
    found.sort(key=lambda item: item[0], reverse=True)
    return [item[5] for item in found]


def rectify_face(content: bytes, quad: Sequence[tuple[float, float]]) -> bytes | None:
    """把牌面四边形透视矫正到标准矩形。"""
    cv2 = _cv2()
    if cv2 is None:
        return None
    image = _decode(content)
    if image is None:
        return None
    import numpy as np

    width, height = _TILE_FACE_SIZE
    source = np.asarray(quad, dtype="float32")
    target = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
        dtype="float32",
    )
    matrix = cv2.getPerspectiveTransform(source, target)
    face = cv2.warpPerspective(image, matrix, (width, height), flags=cv2.INTER_CUBIC)
    ok, encoded = cv2.imencode(".png", face)
    return encoded.tobytes() if ok else None


def _ink_mask(image, margin_ratio: float = _PATTERN_MARGIN_RATIO):
    """用内框四周窄环估牌面基色，返回内框区域的 0/255 图案掩膜。"""
    cv2 = _cv2()
    if cv2 is None:
        return None
    import numpy as np

    height, width = image.shape[:2]
    margin_x = max(2, int(width * margin_ratio))
    margin_y = max(2, int(height * margin_ratio))
    inner = image[margin_y : height - margin_y, margin_x : width - margin_x]
    if inner.size == 0:
        return None
    ring = np.concatenate(
        [
            inner[:8].reshape(-1, 3),
            inner[-8:].reshape(-1, 3),
            inner[:, :8].reshape(-1, 3),
            inner[:, -8:].reshape(-1, 3),
        ]
    )
    base = np.median(ring, axis=0)
    lab = cv2.cvtColor(inner, cv2.COLOR_BGR2LAB).astype(np.float32)
    base_lab = cv2.cvtColor(
        base.reshape(1, 1, 3).astype(np.uint8), cv2.COLOR_BGR2LAB
    )[0, 0].astype(np.float32)
    diff = np.linalg.norm(lab - base_lab, axis=2)
    diff = np.clip(diff / max(float(diff.max()), 1e-6) * 255, 0, 255).astype(np.uint8)
    _, mask = cv2.threshold(diff, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    return mask, (margin_x, margin_y), base


def _base_luma(base) -> float:
    """牌面基色亮度（BGR，0-255）；用来判断这是不是一块浅色牌面。"""
    if base is None:
        return 0.0
    blue, green, red = (float(value) for value in base[:3])
    return 0.114 * blue + 0.587 * green + 0.299 * red


def _mirror(alpha, kind: str):
    if kind == "horizontal":
        return alpha[::-1, :]
    if kind == "radial":
        return alpha[::-1, ::-1]
    return alpha[:, ::-1]


def _symmetry_scores(alpha) -> dict[str, float]:
    import numpy as np

    mask = alpha.astype(np.float32) / 255.0
    denom = max(float(mask.sum()), 1e-6)
    return {
        kind: max(0.0, 1.0 - float(np.abs(mask - _mirror(mask, kind)).sum()) / denom)
        for kind in _SYMMETRY_NAMES
    }


def symmetry_scores(content: bytes) -> dict[str, float]:
    """图案 alpha 的三种镜像对称度（0~1），供上层筛查复用。"""
    image = _decode_rgba(content)
    if image is None:
        return {kind: 0.0 for kind in _SYMMETRY_NAMES}
    import numpy as np

    return _symmetry_scores(np.asarray(image.getchannel("A")))


def _trim_transparent(image: Image.Image, *, padding: int = 2) -> Image.Image:
    """裁掉旋转后新增的透明边，保证 bbox 与内容一致。"""
    alpha = image.getchannel("A")
    bbox = alpha.getbbox()
    if not bbox:
        return image
    left, top, right, bottom = bbox
    return image.crop(
        (
            max(0, left - padding),
            max(0, top - padding),
            min(image.width, right + padding),
            min(image.height, bottom + padding),
        )
    )


def _rotate_rgba(image: Image.Image, angle: float) -> Image.Image:
    """绕中心旋转并保留透明通道；角度很小时用双三次插值保证清晰度。"""
    return image.rotate(
        angle,
        resample=Image.Resampling.BICUBIC,
        expand=True,
        fillcolor=(0, 0, 0, 0),
    )


def _rect_tilt(points) -> float:
    """把 minAreaRect 的角度折成 (-45, 45] 的小倾角。"""
    cv2 = _cv2()
    rect = cv2.minAreaRect(points)
    angle = float(rect[2])
    width, height = rect[1]
    if width < height:  # OpenCV 会把长边记为 height，需要补 90° 再折叠
        angle += 90.0
    while angle > 45.0:
        angle -= 90.0
    while angle <= -45.0:
        angle += 90.0
    return angle


def normalize_pattern_orientation(content: bytes) -> tuple[bytes, float]:
    """把图案资产转正：按最小外接矩形估小倾角，超阈值就旋转回水平。

    只处理 ``±_MAX_DESKEW_DEG`` 内的倾斜；大角度倾斜说明这张牌整体没摆正，
    交给 ``refine_face_by_symmetry`` 在牌面层修，不在这里硬转。
    """
    cv2 = _cv2()
    image = _decode_rgba(content)
    if cv2 is None or image is None:
        return content, 0.0
    import numpy as np

    alpha = np.asarray(image.getchannel("A"))
    points = cv2.findNonZero((alpha >= 64).astype(np.uint8))
    if points is None:
        return content, 0.0
    tilt = _rect_tilt(points)
    if not 0.5 <= abs(tilt) <= _MAX_DESKEW_DEG:
        return content, 0.0
    rotated = _trim_transparent(_rotate_rgba(image, tilt))
    return _encode_png(rotated), round(tilt, 2)


def sharpen_pattern(
    content: bytes,
    *,
    radius: float = 1.5,
    percent: int = 130,
    threshold: int = 3,
) -> bytes:
    """只锐化颜色通道：alpha 保持原样，避免把图案边缘磨出一圈灰边。"""
    image = _decode_rgba(content)
    if image is None:
        return content
    alpha = image.getchannel("A")
    rgb = image.convert("RGB").filter(
        ImageFilter.UnsharpMask(radius=radius, percent=percent, threshold=threshold)
    )
    result = rgb.convert("RGBA")
    result.putalpha(alpha)
    return _encode_png(result)


def refine_face_by_symmetry(
    face_content: bytes,
    *,
    max_angle: float = _MAX_DESKEW_DEG,
    step: float = _DESKEW_STEP_DEG,
) -> tuple[bytes, float]:
    """在牌面上找图案的水平位：小角度搜索让左右镜像重合度最高的角度。

    先缩到 160 宽再搜，25 个候选角度的总耗时远低于一次生图，属于可接受的
    本地成本；收益是图案不会带着 3°~8° 的歪斜被贴回去。
    """
    cv2 = _cv2()
    image = _decode(face_content)
    if cv2 is None or image is None:
        return face_content, 0.0
    import numpy as np

    extracted = _ink_mask(image)
    if extracted is None:
        return face_content, 0.0
    mask, _offset, _base = extracted
    scale = 160.0 / max(1, mask.shape[1])
    small = cv2.resize(
        mask, (160, max(16, int(mask.shape[0] * scale))), interpolation=cv2.INTER_AREA
    )

    def score(candidate) -> float:
        return _symmetry_scores(candidate)["vertical"]

    baseline = score(small)
    best_angle, best_score = 0.0, baseline
    angle = -max_angle
    while angle <= max_angle + 1e-6:
        if abs(angle) >= step:
            matrix = cv2.getRotationMatrix2D(
                (small.shape[1] / 2, small.shape[0] / 2), angle, 1.0
            )
            rotated = cv2.warpAffine(
                small,
                matrix,
                (small.shape[1], small.shape[0]),
                flags=cv2.INTER_LINEAR,
                borderValue=0,
            )
            value = score(rotated)
            if value > best_score:
                best_angle, best_score = float(angle), value
        angle += step
    if best_score - baseline < _DESKEW_MIN_GAIN:
        return face_content, 0.0

    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), best_angle, 1.0)
    rotated = cv2.warpAffine(
        image,
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    ok, encoded = cv2.imencode(".png", rotated)
    return (encoded.tobytes() if ok else face_content), round(best_angle, 2)


def _largest_blob_share(binary) -> float:
    """最大连通块占全部前景像素的比例：整块遮挡≈1，字形本身不对称会摊薄。"""
    cv2 = _cv2()
    import numpy as np

    binary = (binary > 0).astype(np.uint8)
    total = float(binary.sum())
    if total <= 0:
        return 0.0
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(binary, 8)
    if count <= 1:
        return 0.0
    largest = max(int(stats[index, cv2.CC_STAT_AREA]) for index in range(1, count))
    return largest / total


def complete_pattern_by_symmetry(
    content: bytes,
    *,
    max_missing_ratio: float = _COMPLETE_MAX_MISSING,
) -> tuple[bytes, str, float, float]:
    """斜视/侧视导致花纹不全时，用镜像补全缺失部分。

    返回 ``(content, symmetry_type, symmetry_score, completion_ratio)``。
    只有同时满足下面三条才补，否则原样返回（非对称字形不补）：
    - 镜像对称度 ≥ 0.55（说明本来该对称）；
    - 缺失量 ≤ 25%；
    - 缺失残差集中在单个连通块（≥55%），即“被挡住一块”而不是“字本来就长这样”。
    """
    cv2 = _cv2()
    image = _decode_rgba(content)
    if cv2 is None or image is None:
        return content, "none", 0.0, 0.0
    import numpy as np

    alpha = np.asarray(image.getchannel("A"))
    scores = _symmetry_scores(alpha)
    kind = max(scores, key=lambda item: scores[item])
    score = scores[kind]
    if score < _COMPLETE_SYMMETRY_GATE:
        return content, kind, round(score, 4), 0.0

    present = alpha >= 96
    mirror_present = _mirror(present, kind)
    missing = mirror_present & ~present
    missing_pixels = int(np.count_nonzero(missing))
    missing_ratio = missing_pixels / max(1, int(np.count_nonzero(present | mirror_present)))
    if missing_ratio <= 0 or missing_ratio > max_missing_ratio:
        return content, kind, round(score, 4), 0.0
    if _largest_blob_share(missing) < _COMPLETE_MIN_BLOB_SHARE:
        # 缺失散落在整个字形上：说明本来就不对称（如「七」「九」），不能补。
        return content, kind, round(score, 4), 0.0

    mirrored_alpha = _mirror(alpha, kind)
    mirrored_rgb = np.asarray(
        image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if kind == "vertical"
        else (
            image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
            if kind == "horizontal"
            else image.transpose(Image.Transpose.ROTATE_180)
        )
    )

    binary = (alpha >= 64).astype(np.uint8)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(binary)
    if contours:
        cv2.drawContours(filled, contours, -1, 1, -1)
    allowed = (filled > 0) | (_mirror(filled, kind) > 0)
    fill = allowed & (mirrored_alpha >= 96) & (alpha < 96)

    completed_alpha = alpha.copy()
    completed_alpha[fill] = mirrored_alpha[fill]
    completed_alpha = cv2.morphologyEx(
        completed_alpha, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)
    )
    rgba = np.asarray(image).copy()
    rgba[..., 3] = completed_alpha
    rgba[fill, :3] = mirrored_rgb[fill, :3]

    candidate = _trim_transparent(Image.fromarray(rgba))
    after = _symmetry_scores(np.asarray(candidate.getchannel("A")))[kind]
    if after < _COMPLETE_TARGET_SCORE or after - score < 0.03:
        return content, kind, round(score, 4), 0.0
    completion = float(np.count_nonzero(fill)) / max(
        1.0, float(np.count_nonzero(completed_alpha >= 96))
    )
    return _encode_png(candidate), kind, round(after, 4), round(completion, 4)


def _expand_bbox(
    bbox: tuple[float, float, float, float],
    old_size: tuple[int, int],
    new_size: tuple[int, int],
) -> tuple[float, float, float, float]:
    """旋转/补齐后按内容尺寸等比修正 bbox，保持中心不变。"""
    old_w, old_h = max(1, old_size[0]), max(1, old_size[1])
    new_w, new_h = max(1, new_size[0]), max(1, new_size[1])
    center_x = bbox[0] + bbox[2] / 2
    center_y = bbox[1] + bbox[3] / 2
    width = min(1.0, bbox[2] * new_w / old_w)
    height = min(1.0, bbox[3] * new_h / old_h)
    left = min(max(0.0, center_x - width / 2), 1.0 - width)
    top = min(max(0.0, center_y - height / 2), 1.0 - height)
    return (left, top, width, height)


def extract_pattern(
    face_content: bytes,
    *,
    source_index: int = 0,
    normalize: bool = True,
    complete: bool = True,
    sharpen: bool = True,
) -> PatternAsset | None:
    """从已矫正的牌面提取图案：转正 → 对称补齐 → 清晰化 → 归一化 bbox。"""
    cv2 = _cv2()
    if cv2 is None:
        return None
    image = _decode(face_content)
    if image is None:
        return None
    import numpy as np

    height, width = image.shape[:2]
    extracted = _ink_mask(image)
    if extracted is None:
        return None
    mask, (margin_x, margin_y), base = extracted
    if mask.size == 0:
        return None
    # 质量闸 1：真实牌面是「浅底 + 少量刻纹」。深底（照片背景）或墨块占比过高
    # （拼贴/手部/整块照片）都直接判废，否则会把垃圾当花纹贴到牌上。
    if _base_luma(base) < _MIN_FACE_LUMA:
        return None
    ink_ratio = float((mask > 0).mean())
    if not _MIN_INK_RATIO <= ink_ratio <= _MAX_INK_RATIO:
        return None
    # 质量闸 1b：刻纹必须和牌面基色有明显色差，否则只是一块浅色阴影
    # （贴上去等于什么都没画，还会拉低整批图案的可用性）。
    ink_colors = image[margin_y : height - margin_y, margin_x : width - margin_x][mask > 0]
    if ink_colors.size and float(
        np.linalg.norm(ink_colors.reshape(-1, 3).mean(axis=0) - base)
    ) < _MIN_INK_CONTRAST:
        return None

    full_alpha = np.zeros((height, width), np.uint8)
    full_alpha[margin_y : height - margin_y, margin_x : width - margin_x] = mask
    binary = (full_alpha >= 128).astype(np.uint8)
    coords = cv2.findNonZero(binary)
    if coords is None:
        return None
    x, y, w, h = cv2.boundingRect(coords)
    inner_area = max(1, mask.shape[0] * mask.shape[1])
    ratio = (w * h) / inner_area
    if not _MIN_PATTERN_AREA_RATIO <= ratio <= _MAX_PATTERN_AREA_RATIO:
        return None
    # 质量闸 2：图案几乎占满整块牌面 → 说明这一块根本不是「牌面 + 刻纹」，
    # 而是整张照片被当成了一张牌。反过来，细长条（高或宽 <10%）基本是水印/横幅。
    if (
        w / max(1, mask.shape[1]) > _MAX_BBOX_WIDTH
        or h / max(1, mask.shape[0]) > _MAX_BBOX_HEIGHT
        or w / max(1, mask.shape[1]) < 0.10
        or h / max(1, mask.shape[0]) < 0.10
    ):
        return None

    # 羽化只作用在图案边缘，避免把牌面基色一起贴上去。
    alpha = cv2.GaussianBlur(full_alpha, (3, 3), 0)
    rgba = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
    rgba[:, :, 3] = alpha
    pattern = Image.fromarray(cv2.cvtColor(rgba, cv2.COLOR_BGRA2RGBA))
    cropped = pattern.crop((x, y, x + w, y + h))
    bbox = (
        max(0.0, (x - margin_x) / max(1, width - 2 * margin_x)),
        max(0.0, (y - margin_y) / max(1, height - 2 * margin_y)),
        min(1.0, w / max(1, width - 2 * margin_x)),
        min(1.0, h / max(1, height - 2 * margin_y)),
    )

    # 空白牌：用同一块牌面把图案区域 inpaint 掉，保留材质、倒角与光影。
    blank = cv2.inpaint(image, binary * 255, 3, cv2.INPAINT_TELEA)
    ok, blank_encoded = cv2.imencode(".png", blank)
    if not ok:
        return None

    content = _encode_png(cropped)
    old_size = cropped.size
    rotation_deg = 0.0
    if normalize:
        content, rotation_deg = normalize_pattern_orientation(content)
    symmetry_type, symmetry_score, completion_ratio = "none", 0.0, 0.0
    if complete:
        content, symmetry_type, symmetry_score, completion_ratio = (
            complete_pattern_by_symmetry(content)
        )
    if sharpen:
        content = sharpen_pattern(content)

    completed = _decode_rgba(content)
    if completed is None:
        return None
    if normalize or complete:
        bbox = _expand_bbox(bbox, old_size, completed.size)
    if symmetry_type == "none":
        best_kind, best_score = max(
            _symmetry_scores(np.asarray(completed.getchannel("A"))).items(),
            key=lambda item: item[1],
        )
        symmetry_type, symmetry_score = best_kind, round(best_score, 4)

    # 置信度只用于排序/兜底取舍：对称度高、补得少、面积合理的优先贴。
    confidence = 0.45 + 0.35 * symmetry_score - 0.4 * completion_ratio
    if 0.01 <= ratio <= 0.5:
        confidence += 0.1
    if sharpen:
        confidence += 0.03

    signature = hashlib.sha256(
        np.asarray(
            completed.resize((32, 32), Image.Resampling.LANCZOS).getchannel("A")
        ).tobytes()
    ).hexdigest()[:24]
    return PatternAsset(
        content=content,
        bbox=bbox,
        face_aspect=width / max(1, height),
        signature=signature,
        source_index=source_index,
        blank_face=blank_encoded.tobytes(),
        rotation_deg=rotation_deg,
        symmetry_type=symmetry_type,
        symmetry_score=symmetry_score,
        completion_ratio=completion_ratio,
        confidence=round(min(1.0, max(0.0, confidence)), 4),
    )


def build_pattern_library(
    images: Sequence[bytes],
    *,
    max_patterns: int = 24,
    max_faces_per_image: int = 12,
    time_budget_s: float = 8.0,
) -> list[PatternAsset]:
    """从多张原图里扣出图案资产；按 alpha 指纹去重，保持首次出现顺序。

    ``time_budget_s`` 是硬上限：超出后直接返回已扣到的部分，剩下的交给下次
    或走本地空白牌兜底——抠图再准也不值得拖垮整批处理。
    """
    started = time.perf_counter()
    library: list[PatternAsset] = []
    seen: set[str] = set()
    for source_index, content in enumerate(images):
        # 电商原图里牌往往只占画面 1%-10%，且常被拍摄角度拉斜；
        # 用实景档阈值才扣得到，严格档几乎全军覆没（实测 8 张原图扣出 0 张）。
        quads = detect_face_quads(
            content,
            min_area_ratio=_SCENE_TILE_MIN_AREA,
            max_area_ratio=_SCENE_TILE_MAX_AREA,
            aspect_range=_SCENE_TILE_ASPECT,
        )[:max_faces_per_image]
        for quad in quads:
            if time.perf_counter() - started > time_budget_s:
                return library
            face = rectify_face(content, quad)
            if not face:
                continue
            refined, _angle = refine_face_by_symmetry(face)
            pattern = extract_pattern(refined, source_index=source_index)
            if pattern is None or pattern.signature in seen:
                continue
            seen.add(pattern.signature)
            library.append(pattern)
            if len(library) >= max_patterns:
                return library
    return library


def choose_distinct_patterns(
    patterns: Sequence[PatternAsset],
    count: int,
    *,
    seed: str = "",
) -> list[PatternAsset]:
    """确定性抽取不重复图案；不足时只返回现有的，由调用方决定是否回退。"""
    unique: list[PatternAsset] = []
    seen: set[str] = set()
    for pattern in sorted(patterns, key=lambda item: item.confidence, reverse=True):
        if pattern.signature in seen:
            continue
        seen.add(pattern.signature)
        unique.append(pattern)
    if len(unique) <= count:
        return unique
    picked = random.Random(seed or "mahjong").sample(unique, count)
    return sorted(picked, key=lambda item: unique.index(item))


def _target_rect(
    panel: Image.Image,
    pattern: PatternAsset,
) -> tuple[int, int, int, int]:
    """在空白牌面板里找牌面矩形；找不到时用居中的标准牌面矩形。"""
    quads = detect_face_quads(_encode_png(panel))
    if quads:
        xs = [point[0] for point in quads[0]]
        ys = [point[1] for point in quads[0]]
        left, top = int(min(xs)), int(min(ys))
        width, height = int(max(xs) - min(xs)), int(max(ys) - min(ys))
        if width > 40 and height > 40:
            return left, top, width, height

    height = int(panel.height * 0.76)
    width = int(height * pattern.face_aspect)
    if width > panel.width * 0.9:
        width = int(panel.width * 0.9)
        height = int(width / max(0.1, pattern.face_aspect))
    left = (panel.width - width) // 2
    top = (panel.height - height) // 2
    return left, top, width, height


def paste_pattern(
    panel_content: bytes,
    pattern: PatternAsset,
    *,
    rotation_deg: float = 0.0,
    flip_h: bool = False,
) -> bytes:
    """把图案按归一化 bbox 贴到空白牌面板上，用 multiply 继承牌面光影。

    缩放保持图案自身宽高比（``contain`` 居中），不拉伸变形；额外旋转只用于
    同一批里换朝向，不改变图案本身的比例。
    """
    panel = Image.open(io.BytesIO(panel_content)).convert("RGB")
    left, top, width, height = _target_rect(panel, pattern)
    bx, by, bw, bh = pattern.bbox
    box_left = left + int(width * bx)
    box_top = top + int(height * by)
    box_width = max(1, int(width * bw))
    box_height = max(1, int(height * bh))

    pattern_image = _decode_rgba(pattern.content)
    if pattern_image is None:
        return panel_content
    if flip_h or pattern.flip_h:
        pattern_image = pattern_image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    total_rotation = float(pattern.rotation_deg) + float(rotation_deg)
    if abs(total_rotation) >= 0.5:
        pattern_image = _trim_transparent(_rotate_rgba(pattern_image, total_rotation))

    scale = min(box_width / pattern_image.width, box_height / pattern_image.height)
    target_width = max(1, int(pattern_image.width * scale))
    target_height = max(1, int(pattern_image.height * scale))
    pattern_image = pattern_image.resize(
        (target_width, target_height), Image.Resampling.LANCZOS
    )
    rect = (
        box_left + (box_width - target_width) // 2,
        box_top + (box_height - target_height) // 2,
        box_left + (box_width - target_width) // 2 + target_width,
        box_top + (box_height - target_height) // 2 + target_height,
    )
    alpha = pattern_image.getchannel("A")
    base = panel.crop(rect)
    multiplied = ImageChops.multiply(base, pattern_image.convert("RGB"))
    panel.paste(Image.composite(multiplied, base, alpha), (rect[0], rect[1]))
    return _encode_png(panel)


def detect_tile_quads(content: bytes, *, max_quads: int = 6) -> list[list[tuple[float, float]]]:
    """在实景面板里找牌面四边形（比空白牌单拍更宽松：牌更小、可能倾斜）。"""

    quads = detect_face_quads(
        content,
        min_area_ratio=_SCENE_TILE_MIN_AREA,
        max_area_ratio=_SCENE_TILE_MAX_AREA,
        aspect_range=_SCENE_TILE_ASPECT,
    )
    return quads[: max(1, int(max_quads))]


def detect_blank_face_quads(
    content: bytes, *, max_faces: int = 4
) -> list[list[tuple[float, float]]]:
    """找「空白牌面」：亮色、紧凑、不触边的连通块，按面积从大到小返回。

    为什么不再只靠 Canny 四边形：实景里牌面是很亮的一块平面，Canny 更容抓到
    整张图/大色块的边，抓不到一张张小牌。而 AI 被要求把牌面留白后，「亮 +
    紧凑 + 不触边」几乎等价于「一张牌」，比轮廓检测稳得多。
    """
    cv2 = _cv2()
    image = _decode(content)
    if cv2 is None or image is None:
        return []
    import numpy as np

    height, width = image.shape[:2]
    total = max(1, height * width)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bright = mask > 0
    dark = ~bright
    if bright.any() and dark.any() and gray[bright].mean() < gray[dark].mean():
        mask = cv2.bitwise_not(mask)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, 8)

    found: list[tuple[float, list[tuple[float, float]]]] = []
    for label in range(1, count):
        x, y, w, h, area = (int(value) for value in stats[label])
        if area < total * 0.004 or area > total * 0.30:
            continue
        # 触边的亮块基本都是背景墙面/台面，不是牌。
        if x <= 2 or y <= 2 or x + w >= width - 2 or y + h >= height - 2:
            continue
        if not 0.40 <= w / max(1, h) <= 1.30:
            continue
        if area / max(1.0, float(w * h)) < 0.55:
            continue
        ys, xs = np.where(labels[y : y + h, x : x + w] == label)
        if xs.size < 64:
            continue
        points = np.column_stack([xs + x, ys + y]).astype("int32")
        rect = cv2.minAreaRect(points)
        rect_w, rect_h = rect[1]
        if min(rect_w, rect_h) < 24:
            continue
        if not 0.40 <= rect_w / max(1.0, rect_h) <= 1.60:
            continue
        if area / max(1.0, float(rect_w * rect_h)) < 0.55:
            continue
        box = cv2.boxPoints(rect)
        ordered = _order_points(box)
        found.append(
            (float(area), [(float(px), float(py)) for px, py in ordered])
        )
    found.sort(key=lambda item: item[0], reverse=True)
    return [quad for _area, quad in found[: max(1, int(max_faces))]]


def _inset_quad(quad: Sequence[tuple[float, float]], ratio: float = _QUAD_INSET):
    """把四边形各角朝中心收一点：落在牌的正脸上，避开倒角与侧面厚度。"""
    import numpy as np

    points = np.asarray(quad, dtype="float32")
    center = points.mean(axis=0, keepdims=True)
    return center + (points - center) * (1.0 - max(0.0, min(0.45, ratio)))


def _face_canvas(pattern: PatternAsset, size: tuple[int, int]) -> Image.Image | None:
    """把图案按**统一比例**居中放进牌面画布，供透视贴回使用。

    这里刻意不用 ``pattern.bbox``：不同来源的图案 bbox 差异极大（有的是整字，
    有的只有半个花），按各自的 bbox 贴会出现「同一张牌上有的巨大有的极小」。
    真实麻将的字纹都居中且约占牌面 60%-70%，统一比例反而更稳。
    """
    image = _decode_rgba(pattern.content)
    if image is None:
        return None
    width, height = max(8, int(size[0])), max(8, int(size[1]))
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    target = max(8.0, _FACE_PATTERN_SCALE * min(width, height))
    scale = target / max(1, max(image.width, image.height))
    target_width = max(1, min(width, int(round(image.width * scale))))
    target_height = max(1, min(height, int(round(image.height * scale))))
    resized = image.resize((target_width, target_height), Image.Resampling.LANCZOS)
    canvas.paste(
        resized,
        ((width - target_width) // 2, (height - target_height) // 2),
        resized,
    )
    return canvas


def _paste_into_quad(panel, pattern: PatternAsset, quad, *, rotation_deg: float = 0.0):
    """按四边形做透视变换，把图案 multiply 进牌面（继承光影，不产生贴纸边）。"""
    cv2 = _cv2()
    import numpy as np

    points = _inset_quad(quad)
    top = np.linalg.norm(points[1] - points[0])
    bottom = np.linalg.norm(points[2] - points[3])
    left = np.linalg.norm(points[3] - points[0])
    right = np.linalg.norm(points[2] - points[1])
    face_width = int(max(24, min(1600, round((top + bottom) / 2))))
    face_height = int(max(24, min(2000, round((left + right) / 2))))
    face = _face_canvas(pattern, (face_width, face_height))
    if face is None:
        return panel
    if abs(rotation_deg) >= 45:
        face = _trim_transparent(_rotate_rgba(face, rotation_deg))
        face = face.resize((face_width, face_height), Image.Resampling.LANCZOS)

    source = np.array(
        [[0, 0], [face_width - 1, 0], [face_width - 1, face_height - 1], [0, face_height - 1]],
        dtype="float32",
    )
    matrix = cv2.getPerspectiveTransform(source, points.astype("float32"))
    inverse = cv2.getPerspectiveTransform(points.astype("float32"), source)

    # 第一步：抹平旧牌面。AI 经常无视「留白」指令照样画出字纹，直接叠图案会变成
    # 「旧字 + 新纹」的重影。这里把该牌面矫正出来做一次大半径模糊（细笔画消失、
    # 光照与明暗保留），再投回原位，等于只擦掉字、不动光影。
    rectified = cv2.warpPerspective(
        panel, inverse, (face_width, face_height), flags=cv2.INTER_LINEAR
    )
    sigma = max(3.0, min(face_width, face_height) * 0.09)
    flattened = cv2.GaussianBlur(rectified, (0, 0), sigma)
    flattened_back = cv2.warpPerspective(
        flattened, matrix, (panel.shape[1], panel.shape[0]), flags=cv2.INTER_LINEAR
    )
    full_mask = np.full((face_height, face_width), 255, np.uint8)
    face_mask = cv2.warpPerspective(
        full_mask, matrix, (panel.shape[1], panel.shape[0]), flags=cv2.INTER_LINEAR
    )
    face_mask = cv2.erode(face_mask, np.ones((5, 5), np.uint8))
    face_mask = cv2.GaussianBlur(face_mask, (7, 7), 0)
    face_alpha = (face_mask.astype("float32") / 255.0)[..., None]
    base = (
        panel.astype("float32") * (1.0 - face_alpha)
        + flattened_back.astype("float32") * face_alpha
    ).astype("uint8")

    warped = cv2.warpPerspective(
        cv2.cvtColor(np.asarray(face), cv2.COLOR_RGBA2BGRA),
        matrix,
        (panel.shape[1], panel.shape[0]),
        flags=cv2.INTER_LINEAR,
        borderValue=(0, 0, 0, 0),
    )
    mask = cv2.erode(warped[:, :, 3], np.ones((3, 3), np.uint8))
    mask = cv2.GaussianBlur(mask, (5, 5), 0)
    alpha = (mask.astype("float32") / 255.0)[..., None]
    if float(alpha.max()) <= 0.0:
        return panel
    # 画布是 RGBA，通道顺序与面板一致（这里只做逐通道乘法，不涉及色彩空间）。
    multiplied = base.astype("float32") * (warped[:, :, :3].astype("float32") / 255.0)
    blended = base.astype("float32") * (1.0 - alpha) + multiplied * alpha
    return np.clip(blended, 0, 255).astype("uint8")


def paste_patterns_in_scene(
    panel_content: bytes,
    patterns: Sequence[PatternAsset],
    *,
    max_faces: int = 4,
) -> tuple[bytes, int]:
    """在实景面板里检测每张牌，按透视把图案贴到牌面上。

    返回 ``(新面板, 实际贴了几张牌)``。检测不到牌面时**原样返回**——绝不把整格
    换成灰底空白牌（那样场景、台面、光影全丢，和原始轮播完全是两个商品）。
    """
    cv2 = _cv2()
    if cv2 is None or not patterns:
        return panel_content, 0
    image = _decode(panel_content)
    if image is None:
        return panel_content, 0
    # 只用轮廓四边形：亮块检测会把「牌墙的侧面」也当成牌面（实测会把图案贴到
    # 一堵墙上），宁可不贴，也不能贴错位置。
    quads = detect_tile_quads(panel_content, max_quads=max_faces)
    if not quads:
        return panel_content, 0
    canvas = image
    pasted = 0
    for index, quad in enumerate(quads):
        pattern = patterns[index % len(patterns)]
        rotation = 0.0 if index < len(patterns) else 180.0
        canvas = _paste_into_quad(canvas, pattern, quad, rotation_deg=rotation)
        pasted += 1
    ok, encoded = cv2.imencode(".png", canvas)
    return (encoded.tobytes() if ok else panel_content), pasted


def local_blank_panel(blank_face: bytes, *, side: int = 1600) -> bytes:
    """用本地空白牌做一张居中面板（AI 不可用时的零调用兜底）。"""
    face = Image.open(io.BytesIO(blank_face)).convert("RGB")
    face_width, face_height = face.size
    target_height = int(side * 0.76)
    target_width = int(target_height * face_width / max(1, face_height))
    if target_width > side * 0.9:
        target_width = int(side * 0.9)
        target_height = int(target_width * face_height / max(1, face_width))
    face = face.resize((target_width, target_height), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (side, side), (246, 246, 244))
    canvas.paste(face, ((side - target_width) // 2, (side - target_height) // 2))
    return _encode_png(canvas)


def is_blank_face(panel_content: bytes) -> bool:
    """检查空白牌面板中央是否真的没有图案/文字（边缘密度门）。"""
    cv2 = _cv2()
    if cv2 is None:
        return True
    image = _decode(panel_content)
    if image is None:
        return False
    height, width = image.shape[:2]
    inner = image[int(height * 0.2) : int(height * 0.8), int(width * 0.2) : int(width * 0.8)]
    if inner.size == 0:
        return False
    gray = cv2.cvtColor(inner, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 40, 120)
    return float(edges.mean()) < 6.0


def _gray_world(image: Image.Image, *, strength: float = 0.5) -> Image.Image:
    """轻度灰世界白平衡：只把三通道均值拉近一半，避免把实物颜色洗掉。"""
    import numpy as np

    pixels = np.asarray(image, dtype=np.float32)
    means = pixels.reshape(-1, 3).mean(axis=0)
    target = float(means.mean())
    if target <= 1e-3:
        return image
    gains = 1.0 + (target / np.maximum(means, 1e-3) - 1.0) * max(0.0, min(1.0, strength))
    corrected = np.clip(pixels * gains.reshape(1, 1, 3), 0, 255)
    return Image.fromarray(corrected.astype(np.uint8))


def score_source_image(content: bytes) -> float:
    """原图「可直出」评分：主体占比高、边框干净、少促销横幅。

    麻将原图常见「实拍图 + 上下促销横幅」两种混排。直出时优先选主体大、
    边框没有高饱和横幅的那几张，避免把「支持定制 / 现货批发」带进主图。
    """
    cv2 = _cv2()
    image = _decode(content)
    if cv2 is None or image is None:
        return 0.0
    import numpy as np

    height, width = image.shape[:2]
    if width > 512:
        small = cv2.resize(image, (512, max(64, int(512 * height / max(1, width)))))
    else:
        small = image
    h, w = small.shape[:2]
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 160).astype("float32") / 255.0
    center = float(edges[int(h * 0.20) : int(h * 0.80), int(w * 0.20) : int(w * 0.80)].mean())
    border = float(
        np.concatenate(
            [
                edges[: max(1, int(h * 0.08))].ravel(),
                edges[int(h * 0.92) :].ravel(),
                edges[:, : max(1, int(w * 0.06))].ravel(),
                edges[:, int(w * 0.94) :].ravel(),
            ]
        ).mean()
    )
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    banner_saturation = float(
        np.concatenate(
            [
                hsv[: max(1, int(h * 0.10)), :, 1].ravel(),
                hsv[int(h * 0.90) :, :, 1].ravel(),
            ]
        ).mean()
    )
    resolution_bonus = min(1.0, (width * height) / (1200.0 * 1200.0))
    return round(
        center * 6.0 - border * 4.0 - (banner_saturation / 255.0) * 2.0 + resolution_bonus * 0.5,
        4,
    )


def _source_panel(
    content: bytes,
    *,
    side: int = 2048,
    fraction: float = 1.0,
    anchor: float = 0.5,
) -> bytes | None:
    """把一张原图裁成 1:1 方图：白平衡 + 轻微锐化 + 统一到 side。"""
    try:
        image = Image.open(io.BytesIO(content)).convert("RGB")
    except (OSError, ValueError):
        return None
    image = _gray_world(image)
    width, height = image.size
    edge = max(64, int(min(width, height) * max(0.3, min(1.0, fraction))))
    left = max(0, (width - edge) // 2)
    top = int(max(0.0, min(float(height - edge), (height - edge) * max(0.0, min(1.0, anchor)))))
    image = image.crop((left, top, left + edge, top + edge))
    image = image.resize((side, side), Image.Resampling.LANCZOS)
    image = image.filter(ImageFilter.UnsharpMask(radius=1.2, percent=110, threshold=3))
    return _encode_png(image)


def build_source_panels(
    images: Sequence[bytes],
    *,
    side: int = 2048,
    count: int = 4,
) -> list[bytes]:
    """麻将直出：从原图里挑最干净的几张，裁成 count 张 1:1 方图。

    不做 AI 重绘——牌面字纹就是商品本体，任何重绘/低分辨率贴图都会失真。
    数量不足时用同一张图的不同取景（整图 / 近景 / 特写）补位，保证 4 格完整。
    """
    ranked = sorted(images, key=score_source_image, reverse=True)
    if not ranked:
        return []
    plans = ((1.0, 0.50), (0.80, 0.42), (0.66, 0.58), (0.92, 0.50))
    panels: list[bytes] = []
    for index in range(max(1, int(count))):
        content = ranked[index % len(ranked)]
        fraction, anchor = plans[index % len(plans)]
        panel = _source_panel(content, side=side, fraction=fraction, anchor=anchor)
        if panel:
            panels.append(panel)
    return panels


def library_key(category: str, title: str, *, extra: str = "") -> str:
    """图案库缓存键：品类 + 标题里的稳定片段（同款麻将的不同 SKU 复用同一批花纹）。"""
    parts = [str(category or "").strip().lower(), str(title or "").strip()]
    if extra:
        parts.append(str(extra).strip())
    return "|".join(part for part in parts if part) or "default"


__all__ = [
    "MAHJONG_BLANK_FACE_RULE",
    "MahjongPatternLibrary",
    "PatternAsset",
    "build_pattern_library",
    "build_source_panels",
    "choose_distinct_patterns",
    "complete_pattern_by_symmetry",
    "detect_face_quads",
    "detect_tile_quads",
    "extract_pattern",
    "is_blank_face",
    "library_key",
    "local_blank_panel",
    "normalize_pattern_orientation",
    "paste_pattern",
    "paste_patterns_in_scene",
    "rectify_face",
    "refine_face_by_symmetry",
    "score_source_image",
    "sharpen_pattern",
    "symmetry_scores",
]
