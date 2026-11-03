"""变种规格轴拆分：把「颜色=蓝色 S」拆成「颜色 + 尺码」。

1688 上不少供货商把尺码写进颜色选项里（``蓝色 S`` / ``蓝色M码`` / ``红色-2XL``），
如果原样导出，店小秘 / Temu 只会得到一条 ``Color: 蓝色 S`` 且没有 Size 轴，
导入后无法按尺码拆分。这里在读取与导出两层统一拆开，草稿池展示、SKU 名称匹配、
导出表格共用同一套规则。

只拆「颜色轴 + 明确的尺码尾缀」；已有尺码轴、或尾部不像尺码（如 ``Floral``）时不拆。
"""

from __future__ import annotations

import re

COLOR_AXIS_NAMES = frozenset(
    {"颜色", "颜色分类", "颜色名称", "色号", "色彩", "主色", "color", "colour", "colors"}
)
SIZE_AXIS_NAMES = frozenset({"尺码", "尺寸", "码数", "大小", "规格", "size", "sizes"})
SPLIT_SIZE_AXIS_NAME = "尺码"

_SIZE_TOKEN = (
    r"xxs|xs|s|m|l|xl|xxl|xxxl|xxxxl|[2-5]xl|均码|均色|free\s*size|one\s*size|os|"
    r"\d{2,3}\s*(?:cm|码)?|大号|中号|小号"
)
# 尾部尺码：颜色 + 可选分隔符 + 尺码（可带「码/号」后缀）。
_TAIL_SIZE_RE = re.compile(
    rf"^(?P<color>.*?)(?P<sep>[\s\-_/·,，（）()]+)?(?P<size>{_SIZE_TOKEN})(?:码|号)?$",
    re.IGNORECASE,
)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def axis_key(name: str) -> str:
    """规格轴名归一化：去空白、转小写（中英文都适用）。"""

    return re.sub(r"\s+", "", str(name or "")).strip().lower()


def split_color_size(value: str) -> tuple[str, str] | None:
    """``蓝色 S`` → ``("蓝色", "S")``；不像「颜色+尺码」时返回 None。"""

    text = str(value or "").strip()
    if not text:
        return None
    match = _TAIL_SIZE_RE.match(text)
    if not match:
        return None
    color = (match.group("color") or "").strip(" \t-—–/·,，（）()")
    size = (match.group("size") or "").strip()
    if len(color) < 2 or not size:
        return None
    # 没有分隔符时只对中文颜色名生效，避免把 "Floral" 拆成 "Flora"+"L"。
    if not match.group("sep") and not _CJK_RE.search(color):
        return None
    if re.fullmatch(r"[A-Za-z0-9]+", size):
        size = size.upper()
    return color, size


def normalize_variant_attributes(attributes: object) -> dict[str, str]:
    """拆开「颜色+尺码」合并值；已有尺码轴或无法识别时原样返回新字典。"""

    if not isinstance(attributes, dict):
        return {}
    if not attributes:
        return {}
    existing = {axis_key(key) for key in attributes}
    if existing & SIZE_AXIS_NAMES:
        return {str(key): str(value) for key, value in attributes.items()}

    result: dict[str, str] = {}
    for key, value in attributes.items():
        result[str(key)] = value
        if axis_key(key) not in COLOR_AXIS_NAMES:
            continue
        split = split_color_size(str(value))
        if split:
            result[str(key)] = split[0]
            result[SPLIT_SIZE_AXIS_NAME] = split[1]
    return result
