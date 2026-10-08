"""重量/长宽高截图 OCR（本地 RapidOCR，纯算法，无 AI、不消耗积分）。

与 ``product_processing`` 的质检门不同：重量/尺寸截图里的数字与单位字号小、
排布密，需要更高分辨率，且必须返回**全部**文本行交给解析器，而不是只挑中文
或显著大字。因此这里独立维护一个惰性单例引擎，不复用质检门的下采样与筛选。

引擎不可用（未安装/模型缺失）时抛 ``ShippingOcrUnavailable``，由接口层转 503。
"""

from __future__ import annotations

import io
import os
import sys
import threading
from pathlib import Path
from typing import Any


class ShippingOcrUnavailable(RuntimeError):
    """RapidOCR 引擎不可用（未安装、模型缺失或依赖异常）。"""


_ENGINE: Any = None
_ENGINE_ERROR: str | None = None
_ENGINE_LOCK = threading.Lock()

# 尺寸表格小字需要更高分辨率；并发推理上限保守取 2，避免抢满 CPU。
_INFERENCE_SLOT = threading.BoundedSemaphore(2)

_DEFAULT_MAX_EDGE = 1024


def _max_edge() -> int:
    try:
        value = int(os.environ.get("WH_PROFIT_OCR_MAX_EDGE", str(_DEFAULT_MAX_EDGE)).strip())
    except (TypeError, ValueError):
        return _DEFAULT_MAX_EDGE
    return max(256, min(value, 2048))


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


def extract_text_lines(content: bytes) -> list[str]:
    """对图片做 OCR，返回按阅读顺序（上→下、左→右）排列的文本行。

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
    try:
        with Image.open(io.BytesIO(content)) as opened:
            image = opened.convert("RGB")
            edge = max(image.size)
            limit = _max_edge()
            if edge > limit:
                scale = limit / edge
                image = image.resize(
                    (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                    Image.Resampling.LANCZOS,
                )
            with _INFERENCE_SLOT:
                result, _elapsed = engine(np.asarray(image))
    except Exception as exc:
        raise ValueError("image_unreadable") from exc

    positioned: list[tuple[float, float, str]] = []
    for line in result or []:
        if not isinstance(line, (list, tuple)) or len(line) < 2:
            continue
        text = str(line[1] or "").strip()
        if not text:
            continue
        score = float(line[2]) if len(line) > 2 and isinstance(line[2], (int, float)) else 1.0
        if score < 0.5:
            continue
        box = line[0] if line else None
        try:
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            center_x = (min(xs) + max(xs)) / 2
            center_y = (min(ys) + max(ys)) / 2
        except (TypeError, ValueError, IndexError):
            center_x = center_y = 0.0
        positioned.append((center_y, center_x, text))
    # 同一行内按 x 排序，行与行按 y 归并（12px 容差），保证「重量 1.2kg」这类
    # 标签与数值相邻的读序，便于后续正则解析。
    positioned.sort(key=lambda item: (round(item[0] / 12), item[1]))
    return [text for _center_y, _center_x, text in positioned]
