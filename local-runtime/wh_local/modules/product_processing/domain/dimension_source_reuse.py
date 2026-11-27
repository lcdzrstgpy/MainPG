"""原链接尺寸图复用：只保留商品像素，文字、尺寸线、单位、背景全部丢弃后重画。

复用原则：
1. 原图只作为「商品长什么样」的像素来源，不保留任何原有标注；
2. 本地 rembg 抠图 + 纯白底 + 1:1 归一化，再由尺寸卡渲染器印我们的英寸表；
3. 抠图不可信（前景占比异常、蒙版噪声）时直接放弃，回退到 AI 生成图。
"""

from __future__ import annotations

import io
import os
import re
import sys
import threading
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

_SUBJECT_FRAME_RATIO = 0.7
_MIN_FOREGROUND_RATIO = 0.02
_MAX_FOREGROUND_RATIO = 0.98
_MIN_FOREGROUND_EXTENT = 0.2

# rembg 每次 remove() 不传 session 时会内部新建 onnx 会话并重新加载分割权重（数百 MB、
# 数秒），尺寸卡批量烧图等于每张都重建一次：CPU 打满、内存翻倍、进程被系统杀掉。
# 这里与 combo_kit.autosegment 一致，进程内按模型只建一次会话，并用信号量把推理串行化，
# 避免「预取线程 2 路 + 渲染线程 3 路」同时各跑一份抠图。
_SESSIONS: dict[str, Any] = {}
_SESSION_ERRORS: dict[str, str] = {}
_SESSION_LOCK = threading.Lock()
_MASK_SEMAPHORE = threading.BoundedSemaphore(1)

# 随包分发的分割权重目录（wh_local/models/rembg/models/<name>/<name>.onnx）。
# 目录存在时把它设为 rembg 的数据根，避免客户机首次出图去 GitHub 下载权重。
# 默认用 u2netp（4.4MB）：尺寸图复用与白底主图都够用，不再依赖 176MB 的 u2net。
_DEFAULT_MASK_MODEL = "u2netp"

_TOKEN_RE = re.compile(
    r"尺寸|长|宽|高|大小|规格|厘米|毫米|"
    r"size|dimension|length|width|height|inch|inches|\bcm\b|\bmm\b",
    re.I,
)
_NUMBER_UNIT_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:cm|mm|inch|inches|in|厘米|毫米)",
    re.I,
)


def dimension_text_score(lines: list[str]) -> tuple[int, int]:
    """返回 (带单位的数字个数, 尺寸词个数)，用于判断这张图是不是尺寸图。"""

    text = " ".join(str(line or "") for line in lines)
    units = len(_NUMBER_UNIT_RE.findall(text))
    tokens = len(_TOKEN_RE.findall(text))
    return units, tokens


def is_dimension_image(lines: list[str] | None) -> bool:
    if not lines:
        return False
    units, tokens = dimension_text_score(lines)
    # 尺寸图至少要有一组「数字+单位」；两组以上基本可以确认。
    return units >= 2 or (units >= 1 and tokens >= 3)


def find_dimension_source_index(
    candidates: list[bytes],
    *,
    recognize=None,
) -> int | None:
    """在候选原图中找尺寸图，返回下标；没有则 None。

    ``recognize`` 仅供测试注入；默认使用本地 RapidOCR。候选图按调用方给的优先级排列，
    先命中的优先，避免为了找图把整批原图都 OCR 一遍。
    """

    if recognize is None:
        from ..infrastructure.ocr_gate import recognize_text  # noqa: PLC0415

        recognize = recognize_text
    for index, content in enumerate(candidates):
        lines = recognize(content)
        if is_dimension_image(lines):
            return index
    return None


def clean_product_canvas(
    content: bytes, *, side: int = 800, model_name: str = _DEFAULT_MASK_MODEL
) -> bytes | None:
    """抠出商品主体并归一化到纯白 1:1 画布；不可信时返回 None。"""

    try:
        opened = Image.open(io.BytesIO(content))
        # 详情图多为 4096² JPEG：先让 libjpeg 按 1/2~1/8 抽样解码，再转 RGB，
        # 避免为了 1280 的缩略图先把整张大图（约 50MB/张）读进内存。
        try:
            opened.draft("RGB", (1280, 1280))
        except Exception:
            pass
        image = opened.convert("RGB")
    except Exception:
        return None
    # 抠图前先限制长边：输出只有 800px，2000px 详情图直接跑 rembg 纯属浪费。
    if max(image.size) > 1280:
        image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
    mask = _subject_mask(image, model_name=model_name)
    if mask is None:
        return None
    mask = mask.convert("L")
    width, height = image.size
    total = max(1, width * height)
    histogram = mask.histogram()
    foreground = sum(histogram[128:])
    ratio = foreground / total
    if not (_MIN_FOREGROUND_RATIO <= ratio <= _MAX_FOREGROUND_RATIO):
        return None
    binary = mask.point(lambda value: 255 if value >= 128 else 0)
    bbox = binary.getbbox()
    if not bbox:
        return None
    left, top, right, bottom = bbox
    bbox_area = max(1, (right - left) * (bottom - top))
    if foreground / bbox_area < _MIN_FOREGROUND_EXTENT:
        # 前景散成噪声或细条：分割不可信，不拿它去污染成品图。
        return None

    canvas = Image.new("RGB", (width, height), (255, 255, 255))
    canvas.paste(image, (0, 0), mask)
    pad_x = round((right - left) * 0.1)
    pad_y = round((bottom - top) * 0.1)
    subject = canvas.crop(
        (
            max(0, left - pad_x),
            max(0, top - pad_y),
            min(width, right + pad_x),
            min(height, bottom + pad_y),
        )
    )
    subject_w, subject_h = subject.size
    if subject_w <= 0 or subject_h <= 0:
        return None
    frame_side = max(1, round(max(subject_w, subject_h) / _SUBJECT_FRAME_RATIO))
    frame = Image.new("RGB", (frame_side, frame_side), (255, 255, 255))
    frame.paste(subject, ((frame_side - subject_w) // 2, (frame_side - subject_h) // 2))
    if frame.size != (side, side):
        frame = ImageOps.fit(frame, (side, side), method=Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    frame.save(buffer, format="JPEG", quality=93, subsampling=0)
    return buffer.getvalue()


def subject_bbox_ratios(
    content: bytes, *, model_name: str = _DEFAULT_MASK_MODEL
) -> tuple[float, float, float, float] | None:
    """用本地抠图蒙版估算商品在画面里的归一化包围盒（左, 上, 右, 下）。

    用于信息图决定 2×2 小卡放左边还是右边：商品在左就放右，反之放左，
    避免卡片压住商品。抠图不可信时返回 None，由调用方回退到默认位置。
    """

    try:
        opened = Image.open(io.BytesIO(content))
        try:
            opened.draft("RGB", (1280, 1280))
        except Exception:
            pass
        image = opened.convert("RGB")
    except Exception:
        return None
    if max(image.size) > 1280:
        image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
    mask = _subject_mask(image, model_name=model_name)
    if mask is None:
        return None
    binary = mask.convert("L").point(lambda value: 255 if value >= 128 else 0)
    bbox = binary.getbbox()
    if not bbox:
        return None
    width, height = image.size
    left, top, right, bottom = bbox
    if max(1, (right - left) * (bottom - top)) / max(1, width * height) < 0.02:
        return None
    return (
        max(0.0, left / width),
        max(0.0, top / height),
        min(1.0, right / width),
        min(1.0, bottom / height),
    )


def _subject_mask(image: Image.Image, *, model_name: str = _DEFAULT_MASK_MODEL) -> Any:
    """本地 rembg 前景蒙版；未安装/权重不可用/推理失败一律返回 None。"""

    try:
        from rembg import remove  # type: ignore
    except Exception:
        return None
    session = _mask_session(model_name)
    if session is None:
        return None
    try:
        with _MASK_SEMAPHORE:
            result = remove(image, session=session, only_mask=True)
    except Exception:
        return None
    if isinstance(result, Image.Image):
        return result
    try:
        return Image.open(io.BytesIO(result))
    except Exception:
        return None


def _bundled_rembg_home() -> Path | None:
    """随包分发的 rembg 数据根；不存在时返回 None（退回 rembg 默认位置）。"""
    candidates: list[Path] = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(Path(bundle_root) / "wh_local" / "models" / "rembg")
    candidates.append(Path(__file__).resolve().parents[3] / "models" / "rembg")
    for candidate in candidates:
        if (candidate / "models").is_dir():
            return candidate
    return None


def _mask_session(model_name: str = _DEFAULT_MASK_MODEL) -> Any | None:
    """懒加载并复用 rembg 会话：权重只加载一次，失败结果也缓存，避免每张图重试。"""

    name = str(model_name or _DEFAULT_MASK_MODEL).strip() or _DEFAULT_MASK_MODEL
    if name in _SESSIONS or name in _SESSION_ERRORS:
        return _SESSIONS.get(name)
    with _SESSION_LOCK:
        if name not in _SESSIONS and name not in _SESSION_ERRORS:
            try:
                bundled = _bundled_rembg_home()
                if bundled is not None:
                    # 只在不覆盖用户既有配置时接管，避免影响本机已有的 ~/.u2net。
                    os.environ.setdefault("REMBG_HOME", str(bundled))
                from rembg import new_session  # type: ignore

                _SESSIONS[name] = new_session(name)
            except Exception as exc:  # 权重缺失/依赖不全：后续直接走回退，不再重试
                _SESSION_ERRORS[name] = f"{exc.__class__.__name__}: {str(exc)[:160]}"
    return _SESSIONS.get(name)

