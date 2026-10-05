"""combo_kit 主体自动框选：本地分割出前景，再把轮廓简化成可拖动的多边形。

给用户一个「算法初步框选」的起点，替代前端写死的固定六边形，用户只需拖动
少量控制点微调即可。走本地 onnxruntime 推理（rembg + isnet-general-use），
不调外部 API、不扣积分。

模型权重首次运行会联网下载并缓存在用户目录；离线、权重下载失败、依赖缺失或
分割结果不可信（前景占比异常）时一律返回 None，由调用方回落到前端默认六边形，
不能让自动框选失败拖垮上传/编辑流程。
"""
from __future__ import annotations

import io
import threading
from typing import Any

# isnet-general-use：通用商品场景，边缘比 rembg 默认的 u2net 更稳。
MODEL_NAME = "isnet-general-use"
# 多边形点数上限：控制点太多拖不动，太少贴合不准。12 个对鼠标/笔袋这类
# 有机形状足够贴合，同时保持可拖拽。
MAX_POINTS = 12
# Douglas-Peucker 容差（相对轮廓周长），越大轮廓越粗。
SIMPLIFY_TOLERANCE = 0.012
# 分割可信区间：前景占比过小说明没找到主体，过大说明模型把整张图当成了前景。
MIN_FOREGROUND_RATIO = 0.02
MAX_FOREGROUND_RATIO = 0.98
# 前景在自己包围盒里的填充率下限。模型对渐变色噪声、纯色块这类没有主体的图并非
# 干净地返回空蒙版，而是吐出一堆细条/网格：总面积能过 MIN_FOREGROUND_RATIO，
# 但像素散得到处都是，简化后只剩贴着画布边的一根细条——比失败更糟，因为它会
# 当成「识别成功」写进库。实测真实商品图 >= 0.37，噪声图 <= 0.09，取 0.2 兜住两头。
MIN_FOREGROUND_EXTENT = 0.2

_session_lock = threading.Lock()
_session: Any = None


def segment_subject_polygon(image_path: str) -> list[list[float]] | None:
    """返回主体轮廓的归一化多边形 [[x, y], ...]（每点 0..1），失败返回 None。"""
    try:
        import cv2
        import numpy as np
        from PIL import Image
        from rembg import remove
    except Exception:
        return None
    try:
        with Image.open(image_path) as source:
            image = source.convert("RGB")
    except Exception:
        return None
    try:
        mask = _mask_array(remove(image, session=_get_session(), only_mask=True))
    except Exception:
        return None
    if mask is None:
        return None
    return _polygon_from_mask(mask, cv2, np)


def _get_session() -> Any:
    """懒加载并复用 rembg 会话：模型只加载一次，首次会下载权重。"""
    global _session
    with _session_lock:
        if _session is None:
            from rembg import new_session

            _session = new_session(MODEL_NAME)
        return _session


def _mask_array(result: Any):
    """把 rembg 的返回值统一成 2D 灰度 ndarray。"""
    import numpy as np
    from PIL import Image

    if isinstance(result, (bytes, bytearray)):
        result = Image.open(io.BytesIO(result))
    if hasattr(result, "convert"):
        return np.asarray(result.convert("L"))
    array = np.asarray(result)
    return array if array.ndim == 2 else None


def _polygon_from_mask(mask: Any, cv2: Any, np: Any) -> list[list[float]] | None:
    binary = np.where(mask > 127, 255, 0).astype(np.uint8)
    height, width = binary.shape[:2]
    if height < 8 or width < 8:
        return None
    kernel = np.ones((5, 5), np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    binary = _fill_holes(binary, cv2, np)
    area = int(np.count_nonzero(binary))
    ratio = float(area) / float(height * width)
    if not MIN_FOREGROUND_RATIO <= ratio <= MAX_FOREGROUND_RATIO:
        return None
    rows, cols = np.nonzero(binary)
    extent = float(area) / float(
        (cols.max() - cols.min() + 1) * (rows.max() - rows.min() + 1)
    )
    if extent < MIN_FOREGROUND_EXTENT:
        return None
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not len(contours):
        return None
    contour = max(contours, key=cv2.contourArea)
    approx = _simplify(contour, cv2)
    if approx is None:
        return None
    return [
        [
            max(0.0, min(1.0, round(float(point[0][0]) / width, 4))),
            max(0.0, min(1.0, round(float(point[0][1]) / height, 4))),
        ]
        for point in approx
    ]


def _fill_holes(binary: Any, cv2: Any, np: Any):
    """填掉主体内部的孔洞（例如鼠标滚轮、笔夹镂空），否则轮廓会被内孔切碎。

    先外扩 1 像素纯背景边框，保证泛洪起点 (0, 0) 一定落在背景上，
    否则主体贴边时会把整张图判成前景。
    """
    padded = cv2.copyMakeBorder(binary, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    rows, cols = padded.shape[:2]
    cv2.floodFill(padded, np.zeros((rows + 2, cols + 2), np.uint8), (0, 0), 255)
    holes = cv2.bitwise_not(padded)[1:-1, 1:-1]
    return cv2.bitwise_or(binary, holes)


def _simplify(contour: Any, cv2: Any):
    perimeter = float(cv2.arcLength(contour, True))
    if perimeter <= 0:
        return None
    tolerance = perimeter * SIMPLIFY_TOLERANCE
    approx = cv2.approxPolyDP(contour, tolerance, True)
    # 点数仍超上限时逐步放大容差，直到落到可拖拽的点数。
    while len(approx) > MAX_POINTS and tolerance < perimeter:
        tolerance *= 1.35
        approx = cv2.approxPolyDP(contour, tolerance, True)
    if len(approx) < 3:
        return None
    # 容差放大后，细长/弯曲的轮廓会被近似成自交折线（边互相穿过）：编辑器里看是
    # 打结的框，抠图也会切错。退回凸包——凸包一定不自交，形状略钝但紧贴主体，
    # 比交叉的框可用得多。
    if _self_intersects(approx):
        approx = cv2.approxPolyDP(cv2.convexHull(contour), tolerance, True)
    return approx if len(approx) >= 3 else None


def _self_intersects(approx: Any) -> bool:
    """多边形是否有非相邻边相交（自交）。点数有上限，两两比较成本可忽略。"""
    points = [(float(point[0][0]), float(point[0][1])) for point in approx]
    count = len(points)
    if count < 4:
        return False
    for i in range(count):
        a, b = points[i], points[(i + 1) % count]
        for j in range(i + 1, count):
            # 相邻边（含首尾相接的那对）共享端点，不算自交。
            if (i + 1) % count == j or (j + 1) % count == i:
                continue
            if _segments_cross(a, b, points[j], points[(j + 1) % count]):
                return True
    return False


def _segments_cross(a: tuple, b: tuple, c: tuple, d: tuple) -> bool:
    """两线段是否真正交叉（端点相触不算）。"""

    def side(o: tuple, p: tuple, q: tuple) -> float:
        return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])

    return (side(c, d, a) > 0) != (side(c, d, b) > 0) and (side(a, b, c) > 0) != (side(a, b, d) > 0)
