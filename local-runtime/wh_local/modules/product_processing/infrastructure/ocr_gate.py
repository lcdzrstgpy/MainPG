"""图片中文质检门（OCR 后置质量门）。

对齐原项目 native_product_engine 的图片质量门（交接文档 §11.4）：生成完成后用本地 OCR
库（RapidOCR）检测图片是否含中文，检出即触发定向重绘——把中文替换成对应英文或删除。
OCR 是后置验证器，不是第一变换器（§15 确定性验证 → AI 修复 → 确定性复验）。

开关与配置：
- ``WH_PRODUCT_OCR_GATE=0`` 仅关闭兼容详情图质检；生产四宫格始终 fail-closed；
- ``WH_PRODUCT_OCR_MAX_REPAIRS`` 控制最大重绘轮数（默认 2：首轮失败后允许再重绘一次，
  覆盖「产品本体印刷中文/字符」等难以一次修净的场景）。

OCR 库未安装或推理失败时返回 ``None``，表示"无法判断"，调用方跳过质检、不阻断流水线。
"""

from __future__ import annotations

import io
import os
import re
import sys
import threading
from pathlib import Path
from typing import Any

# CJK 统一表意文字 + 扩展A（覆盖简体/繁体/日韩汉字）
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")

_ENGINE: Any = None
_ENGINE_LOCK = threading.Lock()
_ENGINE_ERROR: str | None = None


def _ocr_worker_limit() -> int:
    try:
        value = int(os.environ.get("WH_PRODUCT_OCR_WORKERS", "2").strip())
    except (TypeError, ValueError):
        return 2
    return max(1, min(value, 2))


_OCR_INFERENCE_SEMAPHORE = threading.BoundedSemaphore(_ocr_worker_limit())


def ocr_gate_enabled() -> bool:
    return os.environ.get("WH_PRODUCT_OCR_GATE", "1").strip() not in {"0", "false", "no", "off"}


def max_repair_rounds() -> int:
    try:
        value = int(os.environ.get("WH_PRODUCT_OCR_MAX_REPAIRS", "1").strip())
    except (TypeError, ValueError):
        return 1
    return max(0, min(value, 4))


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


def _ensure_onnx_dll_searchable() -> None:
    """PyInstaller 打包后 onnxruntime 的 DLL 在 _internal\\onnxruntime\\capi 子目录，
    Python 3.8+ 不再把任意目录加入 DLL 搜索路径，需显式 add_dll_directory，
    否则 import onnxruntime 报 'DLL load failed while importing onnxruntime_pybind11_state'。
    源码运行与已可加载场景无需处理（直接跳过）。
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


def inspect_visible_text(content: bytes) -> dict[str, list[str]] | None:
    """一次 OCR 同时返回中文和显著排版文字，避免四宫格重复推理。

    ``prominent`` 只命中足够大的字框，用来拦截模型生成的海报标题/卖点；
    商品本体上的细小型号、花纹或数字不会仅因被 OCR 识别就被删除。
    OCR 不可用返回 ``None``，由调用方按自身质量合同决定是否阻断。
    """
    engine = _get_engine()
    if engine is None:
        return None
    try:
        import numpy as np  # type: ignore
        from PIL import Image  # type: ignore

        with _OCR_INFERENCE_SEMAPHORE:
            opened = Image.open(io.BytesIO(content)).convert("RGB")
            width, height = opened.size
            array = np.array(opened)
            result, _elapse = engine(array)
    except Exception:
        return None
    chinese: list[str] = []
    prominent: list[str] = []
    if not result:
        return {"chinese": chinese, "prominent": prominent}
    for line in result:
        if not isinstance(line, (list, tuple)) or len(line) < 2:
            continue
        text = str(line[1] or "").strip()
        if not text:
            continue
        score = float(line[2]) if len(line) > 2 and isinstance(line[2], (int, float)) else 1.0
        if score < 0.45:
            continue
        if _CJK_RE.search(text):
            chinese.append(text)
        box = line[0] if line else None
        try:
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            box_width = max(xs) - min(xs)
            box_height = max(ys) - min(ys)
        except (TypeError, ValueError, IndexError):
            continue
        searchable = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "", text)
        width_ratio = box_width / max(width, 1)
        height_ratio = box_height / max(height, 1)
        # 显著文字只拦「跨面板海报横幅级」超大排版（高度≥8% 且宽度≥30%，或高度≥12%）。
        # 单面板内的产品印刷标记（牌面数字/字母、型号、花纹）宽度通常 < 25%，
        # 即便字大也不会命中，避免把产品本体设计误判为 AI 文字导致重绘死循环；
        # 中文仍由 chinese 硬拦截，不在此处放宽。
        if (
            len(searchable) >= 6 and height_ratio >= 0.08 and width_ratio >= 0.30
        ) or (
            len(searchable) >= 6 and height_ratio >= 0.12
        ):
            prominent.append(text)
    return {"chinese": chinese, "prominent": prominent}


def detect_chinese_text(content: bytes) -> list[str] | None:
    """返回图片中识别出的中文文本列表；无中文返回空列表；OCR 不可用返回 None。"""
    inspection = inspect_visible_text(content)
    return None if inspection is None else inspection["chinese"]


def ocr_diagnostics() -> dict[str, Any]:
    """OCR 工具链就绪状态（供引擎状态接口展示）。"""
    engine = _get_engine()
    if engine is None:
        return {"ready": False, "reason": _ENGINE_ERROR or "rapidocr not available"}
    return {"ready": True, "backend": "rapidocr_onnxruntime"}
