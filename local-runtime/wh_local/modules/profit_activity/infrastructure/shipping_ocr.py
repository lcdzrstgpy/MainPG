"""重量/长宽高截图 OCR（本地 RapidOCR，纯算法，无 AI、不消耗积分）。

与 ``product_processing`` 的质检门不同：重量/尺寸截图里的数字与单位字号小、
排布密，需要更高分辨率，且必须返回**全部**文本行交给解析器，而不是只挑中文
或显著大字。因此这里独立维护一个惰性单例引擎，不复用质检门的下采样与筛选。

引擎不可用（未安装/模型缺失）时抛 ``ShippingOcrUnavailable``，由接口层转 503。
"""

from __future__ import annotations

import io
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterable


class ShippingOcrUnavailable(RuntimeError):
    """RapidOCR 引擎不可用（未安装、模型缺失或依赖异常）。"""


_ENGINE: Any = None
_ENGINE_ERROR: str | None = None
_ENGINE_LOCK = threading.Lock()

# 尺寸表格小字需要更高分辨率；并发推理上限保守取 2，避免抢满 CPU。
_INFERENCE_SLOT = threading.BoundedSemaphore(2)

_DEFAULT_MAX_EDGE = 1024
# 多尺度：小字（如「15.9inch」）在基准缩放下可能漏检或误读，再跑一遍放大图后合并。
_DEFAULT_UPSCALE = 1.6
# RapidOCR 内部把长边压到 2000 以内，放大超过它不会带来更多细节。
_MAX_VARIANT_EDGE = 2000


def _max_edge() -> int:
    try:
        value = int(os.environ.get("WH_PROFIT_OCR_MAX_EDGE", str(_DEFAULT_MAX_EDGE)).strip())
    except (TypeError, ValueError):
        return _DEFAULT_MAX_EDGE
    return max(256, min(value, 2048))


def _upscale_factor() -> float:
    """放大倍率；<=1 表示关闭多尺度（只跑基准图）。"""
    try:
        value = float(os.environ.get("WH_PROFIT_OCR_UPSCALE", str(_DEFAULT_UPSCALE)).strip())
    except (TypeError, ValueError):
        return _DEFAULT_UPSCALE
    return max(1.0, min(value, 3.0))


# 旋转变体（竖排文字）的置信度门槛：高于基准，抑制整图旋转带来的误检。
_DEFAULT_ROTATE_MIN_SCORE = 0.7


def _rotation_angles() -> tuple[int, ...]:
    """额外尝试的逆时针旋转角度；默认 90°/270°，用于覆盖竖排（旋转）文字。

    RapidOCR 内部分类器只处理 0°/180°，横竖颠倒的文字必须显式旋转后再识别。
    可通过 WH_PROFIT_OCR_ROTATE 关闭（置空）或调整（如 "90"）。
    """
    raw = os.environ.get("WH_PROFIT_OCR_ROTATE", "90,270")
    angles: list[int] = []
    for part in raw.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            angle = int(part) % 360
        except ValueError:
            continue
        if angle in (90, 180, 270) and angle not in angles:
            angles.append(angle)
    return tuple(angles)


def _rotated_min_score() -> float:
    """旋转变体的置信度门槛（默认 0.7）。"""
    try:
        value = float(os.environ.get("WH_PROFIT_OCR_ROTATE_MIN_SCORE", str(_DEFAULT_ROTATE_MIN_SCORE)).strip())
    except (TypeError, ValueError):
        return _DEFAULT_ROTATE_MIN_SCORE
    return max(0.5, min(value, 0.99))


# 「数字 + 长度/重量单位」即说明截图里已有可用的尺寸信息；达到阈值就没必要再跑
# 旋转变体（竖排文字通常只出现在缺少横向标注的少数图上）。阈值走 env 可调。
_DEFAULT_ROTATE_HINT_THRESHOLD = 2
_DIMENSION_HINT = re.compile(
    r"\d\s*(?:cm|mm|inch|in|英寸|厘米|毫米|kg|公斤|斤|克)", re.IGNORECASE
)

# 旋转变体的总时间预算（秒）：无文字大图每组变体都要数秒，超出预算即放弃剩余方向，
# 避免个别图片把接口拖到一分钟以上。0 表示不限制。
_DEFAULT_ROTATE_BUDGET = 10.0


def _rotate_hint_threshold() -> int:
    """已有多少条「数字+单位」才跳过旋转变体（默认 2）。"""
    try:
        value = int(os.environ.get("WH_PROFIT_OCR_ROTATE_HINT", str(_DEFAULT_ROTATE_HINT_THRESHOLD)).strip())
    except (TypeError, ValueError):
        return _DEFAULT_ROTATE_HINT_THRESHOLD
    return max(0, value)


def _rotate_budget() -> float:
    """旋转变体的时间预算（秒，默认 10，0 为不限）。"""
    try:
        value = float(os.environ.get("WH_PROFIT_OCR_ROTATE_BUDGET", str(_DEFAULT_ROTATE_BUDGET)).strip())
    except (TypeError, ValueError):
        return _DEFAULT_ROTATE_BUDGET
    return max(0.0, min(value, 120.0))


def dimension_hint_count(variants: Iterable[tuple[float, int, list[tuple[float, float, float, str, float]]]]) -> int:
    """统计各变体里「数字+单位」文本条数，用于决定是否需要旋转变体。"""
    count = 0
    for _scale, _rotation, items in variants:
        for item in items:
            if len(item) >= 4 and _DIMENSION_HINT.search(str(item[3])):
                count += 1
    return count


def needs_rotation(variants: Iterable[tuple[float, int, list[tuple[float, float, float, str, float]]]]) -> bool:
    """基准 + 放大结果里「数字+单位」不足阈值时，才值得再试旋转变体。"""
    return dimension_hint_count(variants) < _rotate_hint_threshold()


def _ensure_onnx_dll_searchable() -> None:
    """PyInstaller 打包后 onnxruntime 的 DLL 在 _internal\\onnxruntime\\capi 子目录，
    Python 3.8+ 不再把任意目录加入 DLL 搜索路径，需显式 add_dll_directory；
    源码运行及已可加载场景直接跳过。
    """
    if not getattr(sys, "frozen", False):
        return
    try:
        import importlib.util

        spec = importlib.util.find_spec("onnxruntime")
        if spec and spec.submodule_search_locations:
            capi = Path(next(iter(spec.submodule_search_locations))) / "capi"
            if capi.is_dir():
                os.add_dll_directory(str(capi))
    except Exception:  # 目录不存在或已加载过等，均不阻断
        pass


def _get_engine() -> Any:
    """惰性加载 RapidOCR 引擎（进程内单例，线程安全）。"""
    global _ENGINE, _ENGINE_ERROR
    if _ENGINE is None and _ENGINE_ERROR is None:
        with _ENGINE_LOCK:
            if _ENGINE is None and _ENGINE_ERROR is None:
                try:
                    _ensure_onnx_dll_searchable()
                    from rapidocr_onnxruntime import RapidOCR  # type: ignore

                    _ENGINE = RapidOCR()
                except Exception as exc:  # 未安装/模型下载失败/依赖缺失
                    _ENGINE_ERROR = f"{exc.__class__.__name__}: {str(exc)[:160]}"
    return _ENGINE


def shipping_ocr_available() -> dict[str, Any]:
    """OCR 引擎就绪状态（供接口/诊断复用）。"""
    engine = _get_engine()
    if engine is None:
        return {"ready": False, "reason": _ENGINE_ERROR or "rapidocr not available"}
    return {"ready": True, "backend": "rapidocr_onnxruntime"}


# 同一视觉行的判定：与行内锚点中心 y 的偏差不超过「行高 × 该比例」。
_ROW_MERGE_RATIO = 0.6
_MIN_ROW_TOLERANCE = 6.0


def _ocr_items(engine: Any, array: Any, min_score: float = 0.5) -> list[tuple[float, float, float, str, float]]:
    """对一张已解码的图像数组跑 OCR → [(中心 y, 中心 x, 框高, 文本, 置信度)]。

    ``min_score`` 以下的结果直接丢弃（旋转变体用更高门槛过滤误检）。
    """
    with _INFERENCE_SLOT:
        result, _elapsed = engine(array)
    items: list[tuple[float, float, float, str, float]] = []
    for line in result or []:
        if not isinstance(line, (list, tuple)) or len(line) < 2:
            continue
        text = str(line[1] or "").strip()
        if not text:
            continue
        score = float(line[2]) if len(line) > 2 and isinstance(line[2], (int, float)) else 1.0
        if score < min_score:
            continue
        box = line[0] if line else None
        try:
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            center_x = (min(xs) + max(xs)) / 2
            center_y = (min(ys) + max(ys)) / 2
            height = max(1.0, max(ys) - min(ys))
        except (TypeError, ValueError, IndexError):
            center_x = center_y = 0.0
            height = 1.0
        items.append((center_y, center_x, height, text, score))
    return items


def _map_to_base(
    center_x: float,
    center_y: float,
    height: float,
    scale: float,
    rotation: int,
    base_w: int,
    base_h: int,
) -> tuple[float, float, float]:
    """把某变体（缩放 + 逆时针旋转）的框中心/框高映射回**基准图**坐标 → (x, y, 框高)。

    旋转变体是在基准图（长边已限制）上整体转 90°/180°/270° 后识别的，
    因此先按缩放还原，再按旋转角度做逆变换。仅处理 90 的整数倍，无插值误差。
    """
    scale = scale or 1.0
    cx, cy, h = center_x / scale, center_y / scale, height / scale
    if rotation == 90:
        return base_w - cy, cx, h
    if rotation == 180:
        return base_w - cx, base_h - cy, h
    if rotation == 270:
        return cy, base_h - cx, h
    return cx, cy, h


def merge_ocr_variants(
    variants: Iterable[tuple[float, int, list[tuple[float, float, float, str, float]]]],
    base_size: tuple[int, int] = (0, 0),
) -> list[tuple[float, float, float, str]]:
    """合并多尺度/多方向 OCR 结果 → [(中心 y, 中心 x, 框高, 文本)]（坐标还原到基准图）。

    ``variants`` 每项为 (缩放系数, 逆时针旋转角度, 该变体下的 items)，坐标先按
    ``_map_to_base`` 映射回基准图。**同一文本 + 位置几乎重合**的多来源重复检测
    只保留置信度更高的一次；位置不同的相同文本（如尺寸图里重复出现的共用边
    「46cm」）则各自保留，避免破坏「重复出现即为共用边」的判断。
    """
    base_w, base_h = base_size
    accepted: list[list[Any]] = []
    for scale, rotation, items in variants:
        for center_y, center_x, height, text, score in items:
            key = " ".join(text.split())
            if not key:
                continue
            center_x, center_y, height = _map_to_base(
                center_x, center_y, height, scale, rotation, base_w, base_h
            )
            tolerance = max(6.0, height * 0.6)
            duplicate = next(
                (
                    cell
                    for cell in accepted
                    if cell[3] == key
                    and abs(cell[0] - center_y) <= tolerance
                    and abs(cell[1] - center_x) <= tolerance
                ),
                None,
            )
            if duplicate is None:
                accepted.append([center_y, center_x, height, key, score])
            elif score > duplicate[4]:
                duplicate[0], duplicate[1], duplicate[2], duplicate[4] = center_y, center_x, height, score
    accepted.sort(key=lambda cell: (cell[0], cell[1]))
    return [(cell[0], cell[1], cell[2], cell[3]) for cell in accepted]


def _positioned_items(content: bytes) -> list[tuple[float, float, float, str]]:
    """多尺度 + 多方向 OCR → [(中心 y, 中心 x, 框高, 文本)]，按阅读顺序排列。

    1. 基准图（长边不超过 ``_max_edge``）跑一遍；
    2. 放大图再跑一遍，提升小字（如「15.9inch」）的召回（受 RapidOCR 内部 2000 长边上限约束）；
    3. 仅当上面两遍的「数字+单位」不足 ``_rotate_hint_threshold`` 条时，才把基准图转
       90°、270° 再跑，覆盖竖排（旋转）文字（RapidOCR 内部分类器只管 0°/180°）；
       某个方向在基准图上识别到内容才补该方向的放大图，整体受 ``_rotate_budget`` 限时；
    最后把各变体坐标映射回基准图并按位置去重合并。
    引擎不可用抛 ``ShippingOcrUnavailable``；图片无法解码抛 ``ValueError``。
    """
    engine = _get_engine()
    if engine is None:
        raise ShippingOcrUnavailable(_ENGINE_ERROR or "rapidocr not available")
    try:
        import numpy as np  # type: ignore
        from PIL import Image  # type: ignore
    except Exception as exc:
        raise ShippingOcrUnavailable(f"missing dependency: {exc.__class__.__name__}") from exc

    def fit(image: Any, limit: int) -> Any:
        """长边超过 limit 时等比缩小（不放大）。"""
        edge = max(image.size)
        if edge <= limit:
            return image
        ratio = limit / edge
        return image.resize(
            (max(1, round(image.width * ratio)), max(1, round(image.height * ratio))),
            Image.Resampling.LANCZOS,
        )

    try:
        with Image.open(io.BytesIO(content)) as opened:
            base = fit(opened.convert("RGB"), _max_edge())
    except Exception as exc:
        raise ValueError("image_unreadable") from exc

    base_size = base.size
    variants: list[tuple[float, int, list[tuple[float, float, float, str, float]]]] = [
        (1.0, 0, _ocr_items(engine, np.asarray(base)))
    ]

    # 放大图（提升小字召回）；同时作为旋转变体的放大版本。
    enlarged: Any = None
    enlarged_scale = 1.0
    target_edge = min(_MAX_VARIANT_EDGE, round(max(base_size) * _upscale_factor()))
    if target_edge > max(base_size):
        enlarged_scale = target_edge / max(base_size)
        enlarged = base.resize(
            (max(1, round(base_size[0] * enlarged_scale)), max(1, round(base_size[1] * enlarged_scale))),
            Image.Resampling.LANCZOS,
        )
        variants.append((enlarged_scale, 0, _ocr_items(engine, np.asarray(enlarged))))

    # 旋转变体（竖排文字）：RapidOCR 内部分类器只处理 0°/180°，需显式旋转。
    # 仅当基准/放大结果里「数字+单位」不足时才尝试（大多数截图不需要），且受时间
    # 预算约束；只有某个方向在基准图上识别到内容，才再补该方向的放大图。
    transposes = {
        90: Image.Transpose.ROTATE_90,
        180: Image.Transpose.ROTATE_180,
        270: Image.Transpose.ROTATE_270,
    }
    deadline = time.monotonic() + _rotate_budget()
    if needs_rotation(variants):
        rotated_min_score = _rotated_min_score()
        for angle in _rotation_angles():
            transpose = transposes.get(angle)
            if transpose is None:
                continue
            if _rotate_budget() > 0 and time.monotonic() >= deadline:
                break
            items = _ocr_items(engine, np.asarray(base.transpose(transpose)), min_score=rotated_min_score)
            if not items:
                continue
            variants.append((1.0, angle, items))
            if enlarged is not None and (_rotate_budget() == 0 or time.monotonic() < deadline):
                items_big = _ocr_items(
                    engine, np.asarray(enlarged.transpose(transpose)), min_score=rotated_min_score
                )
                if items_big:
                    variants.append((enlarged_scale, angle, items_big))

    return merge_ocr_variants(variants, base_size)


def extract_rows(content: bytes) -> list[str]:
    """对图片做 OCR，并把**同一视觉行**的多个单元格按 x 顺序合并成一行文本。

    表格截图（如「商品件重尺」）逐格识别后每格一行，直接解析会丢失行内关系；
    这里按框中心 y 归并、行内按 x 排序，得到「名称 长 宽 高 体积 重量」式的整行。
    """
    items = _positioned_items(content)
    rows: list[str] = []
    cluster: list[tuple[float, float, float, str]] = []
    anchor_y = 0.0
    row_height = 0.0
    for center_y, center_x, height, text in items:
        if cluster:
            tolerance = max(_MIN_ROW_TOLERANCE, max(row_height, height) * _ROW_MERGE_RATIO)
            if abs(center_y - anchor_y) <= tolerance:
                cluster.append((center_y, center_x, height, text))
                anchor_y = (anchor_y * (len(cluster) - 1) + center_y) / len(cluster)
                row_height = max(row_height, height)
                continue
            rows.append(" ".join(cell[3] for cell in sorted(cluster, key=lambda cell: cell[1])))
        cluster = [(center_y, center_x, height, text)]
        anchor_y = center_y
        row_height = height
    if cluster:
        rows.append(" ".join(cell[3] for cell in sorted(cluster, key=lambda cell: cell[1])))
    return [row.strip() for row in rows if row.strip()]
