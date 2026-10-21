"""原链接尺寸图复用：只保留商品像素，文字、尺寸线、单位、背景全部丢弃后重画。

复用原则：
1. 原图只作为「商品长什么样」的像素来源，不保留任何原有标注；
2. 本地 rembg 抠图 + 纯白底 + 1:1 归一化，再由尺寸卡渲染器印我们的英寸表；
3. 抠图不可信（前景占比异常、蒙版噪声）时直接放弃，回退到 AI 生成图。
"""

from __future__ import annotations

import io
import re
from typing import Any

from PIL import Image, ImageOps

_SUBJECT_FRAME_RATIO = 0.7
_MIN_FOREGROUND_RATIO = 0.02
_MAX_FOREGROUND_RATIO = 0.98
_MIN_FOREGROUND_EXTENT = 0.2

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


def clean_product_canvas(content: bytes, *, side: int = 800) -> bytes | None:
    """抠出商品主体并归一化到纯白 1:1 画布；不可信时返回 None。"""

    try:
        image = Image.open(io.BytesIO(content)).convert("RGB")
    except Exception:
        return None
    # 抠图前先限制长边：输出只有 800px，2000px 详情图直接跑 rembg 纯属浪费。
    if max(image.size) > 1280:
        image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
    mask = _subject_mask(image)
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


def _subject_mask(image: Image.Image) -> Any:
    """本地 rembg 前景蒙版；未安装/权重不可用/推理失败一律返回 None。"""

    try:
        from rembg import remove  # type: ignore
    except Exception:
        return None
    try:
        result = remove(image, only_mask=True)
    except Exception:
        return None
    if isinstance(result, Image.Image):
        return result
    try:
        return Image.open(io.BytesIO(result))
    except Exception:
        return None
