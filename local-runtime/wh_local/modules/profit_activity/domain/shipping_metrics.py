"""重量/长宽高文本解析（纯算法，无 AI）。

用于「单品利润」的重量卡点：用户上传或粘贴物流/包装截图后，用本地 OCR 取回
文本行，再在这里做纯规则解析，得到实际重量与长宽高，并据此计算抛重。

抛重（体积重）公式：长(cm) × 宽(cm) × 高(cm) ÷ 7000 = 抛重(kg)。
计费重量默认取 max(实际重量, 抛重)；「按实际重量计算（不算抛重）」勾选后只用实际重量。

本模块只做确定性文本解析，不含任何网络/AI 调用，便于单测覆盖。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Iterable

# 抛重除数：国际快递常用 5000，这里是用户约定口径 7000。
DEFAULT_VOLUMETRIC_DIVISOR = 7000.0

# 单位换算到 cm。
_LENGTH_TO_CM: dict[str, float] = {
    "cm": 1.0,
    "厘米": 1.0,
    "公分": 1.0,
    "mm": 0.1,
    "毫米": 0.1,
    "m": 100.0,
    "米": 100.0,
    "inch": 2.54,
    "in": 2.54,
    "英寸": 2.54,
    '"': 2.54,
    "″": 2.54,
    "”": 2.54,
}

# 单位换算到 kg。
_WEIGHT_TO_KG: dict[str, float] = {
    "kg": 1.0,
    "kgs": 1.0,
    "千克": 1.0,
    "公斤": 1.0,
    "kilogram": 1.0,
    "kilograms": 1.0,
    "g": 0.001,
    "gram": 0.001,
    "grams": 0.001,
    "克": 0.001,
    "lb": 0.45359237,
    "lbs": 0.45359237,
    "磅": 0.45359237,
    "oz": 0.028349523125,
    "盎司": 0.028349523125,
}

_NUM = r"(\d+(?:\.\d+)?)"
_LEN_UNITS = r"cm|厘米|公分|mm|毫米|m|米|inch|in|英寸|″|\"|”"
_WT_UNITS = r"kg|kgs|千克|公斤|kilogram|kilograms|g|gram|grams|克|lb|lbs|磅|oz|盎司"
_LEN_U = r"(" + _LEN_UNITS + r")?"
_WT_U = r"(" + _WT_UNITS + r")?"

# 「30 x 20 x 10 cm」/「30*20*10cm」/「30×20×10」三连维度。
_TRIPLE_RE = re.compile(
    _NUM + r"\s*" + _LEN_U + r"\s*[x*]\s*"
    + _NUM + r"\s*" + _LEN_U + r"\s*[x*]\s*"
    + _NUM + r"\s*" + _LEN_U
)

# 「长 30 宽 20 高 10 cm」/「L:30 W:20 H:10cm」带标签维度。
_LABELED_DIM_RE = re.compile(
    r"(?:长|length|(?<![a-z])l)\s*[:=]?\s*" + _NUM + r"\s*" + _LEN_U
    + r"[\s,，、;；/]*"
    + r"(?:宽|width|(?<![a-z])w)\s*[:=]?\s*" + _NUM + r"\s*" + _LEN_U
    + r"[\s,，、;；/]*"
    + r"(?:高|height|(?<![a-z])h)\s*[:=]?\s*" + _NUM + r"\s*" + _LEN_U
)

# 重量：按标签优先级依次尝试，先命中先用。
_WEIGHT_LABEL_PATTERNS = [
    re.compile(r"(?:实际重量|实重|actual\s*weight)\s*[:=]?\s*" + _NUM + r"\s*" + _WT_U),
    re.compile(r"(?:毛重|gross\s*weight)\s*[:=]?\s*" + _NUM + r"\s*" + _WT_U),
    re.compile(r"(?:净重|net\s*weight)\s*[:=]?\s*" + _NUM + r"\s*" + _WT_U),
    re.compile(r"(?:计费重量|chargeable\s*weight)\s*[:=]?\s*" + _NUM + r"\s*" + _WT_U),
    re.compile(r"(?:重量|(?<![a-z])weight|(?<![a-z])wt)\s*[:=]?\s*" + _NUM + r"\s*" + _WT_U),
]

# 兜底：任意「数字 + 重量单位」，如「0.45kg」。必须带单位，避免把价格等数字误当重量。
_WEIGHT_ANY_RE = re.compile(_NUM + r"\s*(" + _WT_UNITS + r")")

_MULTIPLY_SIGNS = str.maketrans({"×": "x", "✕": "x", "✖": "x", "⨯": "x", "✗": "x", "＊": "*"})


def _to_halfwidth(text: str) -> str:
    """全角数字/字母/标点转半角，便于统一正则（如「：」→「:」、「３０」→「30」）。"""
    chars: list[str] = []
    for char in text:
        code = ord(char)
        if code == 0x3000:
            code = 0x20
        elif 0xFF01 <= code <= 0xFF5E:
            code -= 0xFEE0
        chars.append(chr(code))
    return "".join(chars)


def normalize_text(text: str) -> str:
    """统一大小写、全角字符与各种乘号，供解析函数使用。"""
    return _to_halfwidth(str(text)).translate(_MULTIPLY_SIGNS).lower()


def to_cm(value: float, unit: str | None) -> float:
    factor = _LENGTH_TO_CM.get((unit or "cm").strip().lower(), 1.0)
    return round(float(value) * factor, 6)


def to_kg(value: float, unit: str | None) -> float:
    factor = _WEIGHT_TO_KG.get((unit or "kg").strip().lower(), 1.0)
    return round(float(value) * factor, 6)


def volumetric_weight(length_cm: float, width_cm: float, height_cm: float, *, divisor: float = DEFAULT_VOLUMETRIC_DIVISOR) -> float:
    """抛重（体积重）= 长×宽×高÷7000，单位 kg。"""
    if divisor <= 0:
        raise ValueError("divisor must be positive")
    return round(float(length_cm) * float(width_cm) * float(height_cm) / float(divisor), 6)


def billable_weight(actual_weight_kg: float, volumetric_weight_kg: float | None, *, use_actual_only: bool = False) -> float:
    """计费重量：默认取 max(实际重量, 抛重)；勾选「按实际重量」时只用实际重量。"""
    if use_actual_only or volumetric_weight_kg is None:
        return round(float(actual_weight_kg), 6)
    return round(max(float(actual_weight_kg), float(volumetric_weight_kg)), 6)


@dataclass(frozen=True)
class ShippingMetrics:
    """一次文本解析的结果（未做勾选，勾选语义由调用方按 use_actual_only 计算）。"""

    actual_weight_kg: float | None = None
    length_cm: float | None = None
    width_cm: float | None = None
    height_cm: float | None = None
    divisor: float = DEFAULT_VOLUMETRIC_DIVISOR
    matched_lines: list[str] = field(default_factory=list)

    @property
    def volumetric_weight_kg(self) -> float | None:
        if self.length_cm is None or self.width_cm is None or self.height_cm is None:
            return None
        return volumetric_weight(self.length_cm, self.width_cm, self.height_cm, divisor=self.divisor)

    @property
    def billable_weight_kg(self) -> float | None:
        """默认口径下的计费重量（max(实际, 抛重)）；缺实际重量时回退抛重。"""
        volumetric = self.volumetric_weight_kg
        if self.actual_weight_kg is None:
            return volumetric
        return billable_weight(self.actual_weight_kg, volumetric)

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["volumetric_weight_kg"] = self.volumetric_weight_kg
        payload["billable_weight_kg"] = self.billable_weight_kg
        return payload


def _unit_series(*units: str | None) -> list[str]:
    """三轴单位缺省补齐：某一轴没写单位时，跟随整组里最后出现的单位，否则按 cm。"""
    fallback = "cm"
    for unit in reversed(units):
        if unit:
            fallback = unit
            break
    return [unit or fallback for unit in units]


def _parse_triple(text: str) -> tuple[float, float, float] | None:
    match = _TRIPLE_RE.search(text)
    if not match:
        return None
    n1, u1, n2, u2, n3, u3 = match.groups()
    units = _unit_series(u1, u2, u3)
    return (
        to_cm(float(n1), units[0]),
        to_cm(float(n2), units[1]),
        to_cm(float(n3), units[2]),
    )


def _parse_labeled_dims(text: str) -> tuple[float, float, float] | None:
    match = _LABELED_DIM_RE.search(text)
    if not match:
        return None
    n1, u1, n2, u2, n3, u3 = match.groups()
    units = _unit_series(u1, u2, u3)
    return (
        to_cm(float(n1), units[0]),
        to_cm(float(n2), units[1]),
        to_cm(float(n3), units[2]),
    )


def _parse_weight(text: str) -> float | None:
    for pattern in _WEIGHT_LABEL_PATTERNS:
        match = pattern.search(text)
        if match:
            return to_kg(float(match.group(1)), match.group(2))
    match = _WEIGHT_ANY_RE.search(text)
    if match:
        return to_kg(float(match.group(1)), match.group(2))
    return None


def parse_shipping_metrics(lines: Iterable[str], *, divisor: float = DEFAULT_VOLUMETRIC_DIVISOR) -> ShippingMetrics:
    """解析 OCR 文本行，逐行提取实际重量与长宽高（首个命中优先）。"""
    length = width = height = None
    actual: float | None = None
    matched: list[str] = []
    for raw in lines:
        text = normalize_text(raw)
        if not text.strip():
            continue
        if length is None:
            dims = _parse_triple(text) or _parse_labeled_dims(text)
            if dims is not None:
                length, width, height = dims
                matched.append(str(raw))
        if actual is None:
            weight = _parse_weight(text)
            if weight is not None:
                actual = weight
                matched.append(str(raw))
    return ShippingMetrics(
        actual_weight_kg=actual,
        length_cm=length,
        width_cm=width,
        height_cm=height,
        divisor=divisor,
        matched_lines=matched,
    )
