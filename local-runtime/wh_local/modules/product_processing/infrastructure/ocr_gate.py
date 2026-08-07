"""图片中文质检门（OCR 后置质量门）。

对齐原项目 native_product_engine 的图片质量门（交接文档 §11.4）：生成完成后用本地 OCR
库（RapidOCR）检测图片是否含中文，检出即触发定向重绘——把中文替换成对应英文或删除。
OCR 是后置验证器，不是第一变换器（§15 确定性验证 → AI 修复 → 确定性复验）。

开关与配置：
- ``WH_PRODUCT_OCR_GATE=0`` 关闭质检（测试/演示用）；
- ``WH_PRODUCT_OCR_MAX_REPAIRS`` 控制最大重绘轮数（默认 2）。

OCR 库未安装或推理失败时返回 ``None``，表示"无法判断"，调用方跳过质检、不阻断流水线。
"""

from __future__ import annotations

import io
import os
import re
import threading
from typing import Any

# CJK 统一表意文字 + 扩展A（覆盖简体/繁体/日韩汉字）
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")

_ENGINE: Any = None
_ENGINE_LOCK = threading.Lock()
_ENGINE_ERROR: str | None = None


def ocr_gate_enabled() -> bool:
    return os.environ.get("WH_PRODUCT_OCR_GATE", "1").strip() not in {"0", "false", "no", "off"}


def max_repair_rounds() -> int:
    try:
        value = int(os.environ.get("WH_PRODUCT_OCR_MAX_REPAIRS", "2").strip())
    except (TypeError, ValueError):
        return 2
    return max(0, min(value, 4))


def _get_engine() -> Any:
    """惰性加载 RapidOCR 引擎（进程内单例，线程安全）。"""
    global _ENGINE, _ENGINE_ERROR
    if _ENGINE is None and _ENGINE_ERROR is None:
        with _ENGINE_LOCK:
            if _ENGINE is None and _ENGINE_ERROR is None:
                try:
                    from rapidocr_onnxruntime import RapidOCR  # type: ignore

                    _ENGINE = RapidOCR()
                except Exception as exc:  # 未安装/模型下载失败/依赖缺失
                    _ENGINE_ERROR = f"{exc.__class__.__name__}: {str(exc)[:160]}"
    return _ENGINE


def detect_chinese_text(content: bytes) -> list[str] | None:
    """返回图片中识别出的中文文本列表；无中文返回空列表；OCR 不可用返回 None。

    RapidOCR 结果每行为 ``[box, text, score]``；仅保留含汉字的文本行。
    """
    engine = _get_engine()
    if engine is None:
        return None
    try:
        import numpy as np  # type: ignore
        from PIL import Image  # type: ignore

        array = np.array(Image.open(io.BytesIO(content)).convert("RGB"))
        result, _elapse = engine(array)
    except Exception:
        return None
    texts: list[str] = []
    if not result:
        return texts
    for line in result:
        if not isinstance(line, (list, tuple)) or len(line) < 2:
            continue
        text = str(line[1] or "").strip()
        if text and _CJK_RE.search(text):
            texts.append(text)
    return texts


def ocr_diagnostics() -> dict[str, Any]:
    """OCR 工具链就绪状态（供引擎状态接口展示）。"""
    engine = _get_engine()
    if engine is None:
        return {"ready": False, "reason": _ENGINE_ERROR or "rapidocr not available"}
    return {"ready": True, "backend": "rapidocr_onnxruntime"}
