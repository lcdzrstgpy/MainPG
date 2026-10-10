"""重量/长宽高文本解析（纯算法，无 AI）。

用于「单品利润」的重量卡点：用户上传或粘贴物流/包装截图后，用本地 OCR 取回
文本行，再在这里做纯规则解析，得到实际重量与长宽高，并据此计算抛重。

抛重（体积重）公式：长(cm) × 宽(cm) × 高(cm) ÷ 7000 = 抛重(kg)。
计费重量默认取 max(实际重量, 抛重)；「按实际重量计算（不算抛重）」勾选后只用实际重量。

本模块只做确定性文本解析，不含任何网络/AI 调用，便于单测覆盖。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, replace
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
# 「m(?!l|g)」避免把体积单位 ml/mg（如「1500ml」）当成「米」，其余长度单位不受影响。
_LEN_UNITS = r"cm|厘米|公分|mm|毫米|m(?!l|g)|米|inch|in|英寸|″|\"|”"
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

# 独立的数字（不含单位捕获），用于表格型行的列拆分。
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

# 数字是否被重量单位修饰（如「314g」里的 g），用于把重量列从维度列里剔除。
_WEIGHT_SUFFIX_RE = re.compile(r"\s*(" + _WT_UNITS + r")")

# 表头里的重量单位，如「重量(g)」「重量（g)」「重量 kg」。
_WEIGHT_HEADER_RE = re.compile(r"(?:重量|weight)\s*[（(\[]?\s*([a-z]+)")

# 「数字 + 长度单位」，如「46cm」「18.11in」，用于收集纯尺寸标注里的边长。
_LENGTH_VALUE_RE = re.compile(_NUM + r"\s*(" + _LEN_UNITS + r")")

# 双单位标注「46cm/18.11in」是同一个长度的两种写法，折叠为单一厘米值，避免被当成两个尺寸。
_DUAL_UNIT_CM_FIRST_RE = re.compile(
    _NUM + r"\s*(?:cm|厘米|公分)\s*/\s*" + _NUM + r"\s*(?:inch|inches|in|英寸|″|\"|”)"
)
_DUAL_UNIT_IN_FIRST_RE = re.compile(
    _NUM + r"\s*(?:inch|inches|in|英寸|″|\"|”)\s*/\s*" + _NUM + r"\s*(?:cm|厘米|公分)"
)

# 双单位标注的一致性校验：「46cm/18.11in」是同一长度的公制/英制两种写法，
# 换算后应基本相等；差太多说明卖家写错或 OCR 误读，需要提示用户核对。
_CM_UNITS = {"cm", "厘米", "公分"}
_IMPERIAL_UNITS = {"inch", "in", "英寸", "″", '"', "”"}
_DUAL_UNIT_PAIR_RE = re.compile(
    _NUM + r"\s*(" + _LEN_UNITS + r")\s*/\s*" + _NUM + r"\s*(" + _LEN_UNITS + r")"
)
# 两侧都按人工量取并四舍五入，容差取「1cm 或 4% 中较大者」，避免把正常取整当成错误。
_DUAL_UNIT_TOLERANCE_CM = 1.0
_DUAL_UNIT_TOLERANCE_RATIO = 0.04

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


def _collapse_dual_units(text: str) -> str:
    """把「46cm/18.11in」这类同一长度的双单位标注折叠为单一厘米值（取 cm 一侧）。

    尺寸标注图常同时给出公制与英制，若不折叠会被当成两个独立边长。
    """
    text = _DUAL_UNIT_CM_FIRST_RE.sub(r"\1cm", text)
    return _DUAL_UNIT_IN_FIRST_RE.sub(r"\2cm", text)


def _basic_normalize(text: str) -> str:
    """统一大小写、全角字符与各种乘号（不折叠双单位标注）。"""
    return _to_halfwidth(str(text)).translate(_MULTIPLY_SIGNS).lower()


def normalize_text(text: str) -> str:
    """统一大小写、全角字符与各种乘号，并折叠双单位标注，供解析函数使用。"""
    return _collapse_dual_units(_basic_normalize(text))


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
    label: str = ""
    matched_lines: list[str] = field(default_factory=list)
    # 低置信提示（如双单位标注换算不一致），供前端高亮提醒用户核对。
    warnings: list[str] = field(default_factory=list)

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


def _dims_from_match(match: re.Match[str]) -> tuple[float, float, float]:
    n1, u1, n2, u2, n3, u3 = match.groups()
    units = _unit_series(u1, u2, u3)
    return (
        to_cm(float(n1), units[0]),
        to_cm(float(n2), units[1]),
        to_cm(float(n3), units[2]),
    )


def _parse_triple(text: str) -> tuple[float, float, float] | None:
    match = _TRIPLE_RE.search(text)
    return _dims_from_match(match) if match else None


def _parse_all_triples(text: str) -> list[tuple[float, float, float]]:
    """取出一行里的全部三连维度（如尺码列表「19.69*13.78*13.78英寸 23.62*15.75*15.75英寸」有两组）。"""
    return [_dims_from_match(match) for match in _TRIPLE_RE.finditer(text)]


def _parse_labeled_dims(text: str) -> tuple[float, float, float] | None:
    match = _LABELED_DIM_RE.search(text)
    return _dims_from_match(match) if match else None


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


def _detect_header_weight_unit(texts: Iterable[str]) -> str | None:
    """从表头行（如「尺寸 长(cm) 宽(cm) 高(cm) 体积(cm3) 重量(g)」）取重量单位。"""
    for text in texts:
        match = _WEIGHT_HEADER_RE.search(text)
        if match and match.group(1) in _WEIGHT_TO_KG:
            return match.group(1)
    return None


def _guess_weight_unit(value: float) -> str:
    """表头没写重量单位时的兜底：件重尺表格多是 g；小于 10 的数值多半是 kg。"""
    return "g" if value >= 10 else "kg"


def _row_label(text: str) -> str:
    """取行首商品名（第一个数字之前的内容）。"""
    match = _NUMBER_RE.search(text)
    fragment = text[: match.start()] if match else ""
    return fragment.strip(" \t-—:|·、,，.。/\\")


def _match_volume_triple(values: list[float]) -> tuple[tuple[float, float, float], float | None] | None:
    """在「长 宽 高 体积 [重量]」序列里，用「长×宽×高 ≈ 体积」定位维度三连。

    例：``[23, 11.5, 27, 7141.5, 314]`` → 23×11.5×27 = 7141.5，命中，重量取 314。
    """
    for index in range(len(values) - 3):
        a, b, c = values[index], values[index + 1], values[index + 2]
        volume = values[index + 3]
        if a <= 0 or b <= 0 or c <= 0 or volume <= 0:
            continue
        if abs(a * b * c - volume) <= max(0.5, volume * 0.03):
            weight = values[index + 4] if index + 4 < len(values) else None
            return (a, b, c), weight
    return None


def _parse_row(text: str, *, header_weight_unit: str | None, divisor: float) -> ShippingMetrics | None:
    """解析单行（可能是「名称 长 宽 高 体积 重量」的表格行，也可能是带标签的一行）。"""
    dims = _parse_triple(text) or _parse_labeled_dims(text)
    weight = _parse_weight(text)
    if dims is None or weight is None:
        # 收集未被重量单位修饰的数字，用于表格型行的列拆分。
        plain: list[float] = []
        for match in _NUMBER_RE.finditer(text):
            if _WEIGHT_SUFFIX_RE.match(text, match.end()):
                continue
            plain.append(float(match.group()))
        if dims is None and len(plain) >= 3:
            matched = _match_volume_triple(plain)
            if matched is not None:
                dims = matched[0]
                if weight is None and matched[1] is not None:
                    unit = header_weight_unit or _guess_weight_unit(matched[1])
                    weight = to_kg(matched[1], unit)
    if dims is None and weight is None:
        return None
    return ShippingMetrics(
        actual_weight_kg=weight,
        length_cm=dims[0] if dims else None,
        width_cm=dims[1] if dims else None,
        height_cm=dims[2] if dims else None,
        divisor=divisor,
        label=_row_label(text),
        matched_lines=[text],
    )


def _parse_row_variants(text: str, *, header_weight_unit: str | None, divisor: float) -> list[ShippingMetrics]:
    """解析一行，可能产出多个候选。

    一行里列出多组尺码时（如「19.69*13.78*13.78英寸 23.62*15.75*15.75英寸」），
    每组各出一个候选，供用户点选；否则退回单条解析。
    """
    triples = _parse_all_triples(text)
    if triples:
        weight = _parse_weight(text)
        label = _row_label(text)
        return [
            ShippingMetrics(
                actual_weight_kg=weight,
                length_cm=dim[0],
                width_cm=dim[1],
                height_cm=dim[2],
                divisor=divisor,
                label=label,
                matched_lines=[text],
            )
            for dim in triples
        ]
    single = _parse_row(text, header_weight_unit=header_weight_unit, divisor=divisor)
    return [single] if single is not None else []


def parse_shipping_rows(rows: Iterable[str], *, divisor: float = DEFAULT_VOLUMETRIC_DIVISOR) -> list[ShippingMetrics]:
    """按「一行一个商品/一条记录」解析合并后的 OCR 行。

    每行独立产出结果（含商品名 label），一行多组尺码时产出多个候选，供前端选择；
    行间不再互相拼字段。
    """
    prepared = [normalize_text(raw) for raw in rows]
    header_unit = _detect_header_weight_unit(prepared)
    results: list[ShippingMetrics] = []
    for text in prepared:
        if not text.strip():
            continue
        results.extend(_parse_row_variants(text, header_weight_unit=header_unit, divisor=divisor))
    return results


def _length_values(text: str) -> list[float]:
    """按出现顺序取出一行里所有「数字 + 长度单位」的厘米值。"""
    values: list[float] = []
    for match in _LENGTH_VALUE_RE.finditer(text):
        value = to_cm(float(match.group(1)), match.group(2))
        if value > 0:
            values.append(value)
    return values


def _length_value_counts(rows: Iterable[str]) -> list[tuple[float, int]]:
    """统计纯尺寸标注里的边长（去重后保留首现顺序与出现次数）。

    只处理「没有显式结构化维度/重量」的行，避免与表格行解析重复计数。
    """
    counts: dict[float, int] = {}
    order: list[float] = []
    for raw in rows:
        text = normalize_text(raw)
        if not text.strip():
            continue
        if (
            _parse_triple(text) is not None
            or _parse_labeled_dims(text) is not None
            or _parse_weight(text) is not None
        ):
            continue
        for match in _LENGTH_VALUE_RE.finditer(text):
            value = round(to_cm(float(match.group(1)), match.group(2)), 1)
            if value <= 0:
                continue
            if value not in counts:
                counts[value] = 0
                order.append(value)
            counts[value] += 1
    return [(value, counts[value]) for value in order]


def _dimension_metrics(length: float, width: float, height: float, *, divisor: float) -> ShippingMetrics:
    return ShippingMetrics(length_cm=length, width_cm=width, height_cm=height, divisor=divisor)


def _dimension_candidates(rows: Iterable[str], *, divisor: float) -> list[ShippingMetrics]:
    """从纯尺寸标注图推断多组「长×宽×高」供用户点选。

    标注图常一次给出多种配置（如展开前/展开后）：出现 ≥2 次的边长是各配置共用的，
    只出现 1 次的是随配置变化的那条边；据此组合出多组尺寸。
    """
    stats = _length_value_counts(rows)
    if len(stats) < 3:
        return []
    if len(stats) == 3:
        values = [value for value, _ in stats]
        return [_dimension_metrics(values[0], values[1], values[2], divisor=divisor)]
    common = [value for value, count in stats if count >= 2]
    variants = [value for value, count in stats if count == 1]
    if len(common) != 2 or not variants:
        return []
    # 小于最短共用边的值多是扩展量/辅助标注，不是整条边，过滤掉。
    shortest_common = min(common)
    groups = [
        _dimension_metrics(common[0], common[1], variant, divisor=divisor)
        for variant in variants
        if variant >= shortest_common
    ]
    groups.sort(key=lambda item: item.volumetric_weight_kg or 0.0, reverse=True)
    return groups


def _table_column_candidates(rows: Iterable[str], *, divisor: float) -> list[ShippingMetrics]:
    """识别「属性行 × 尺码列」的尺码表，按列（每个尺码）组合出「长×宽×高」。

    尺码表截图形如每行一个属性、每列一个尺码（S/M/L/XL），属性值逐列对齐：
    例如 Caliber / Height / Bowl diameter 三行各给出 4 个尺寸，第 i 列即第 i 个尺码。
    取列数一致且行数最多的一组属性行，用前 3 行按列组合成候选。
    """
    value_rows: list[list[float]] = []
    for raw in rows:
        text = normalize_text(raw)
        if not text.strip():
            continue
        # 已有结构化三连/带标签维度或重量的行交给行解析，不参与表格列组合。
        if (
            _parse_triple(text) is not None
            or _parse_labeled_dims(text) is not None
            or _parse_weight(text) is not None
        ):
            continue
        values = _length_values(text)
        if len(values) >= 2:
            value_rows.append(values)
    if len(value_rows) < 3:
        return []
    # 属性行的列数应一致；取「同列数行数最多」的那组（并列时取列数多的）。
    by_columns: dict[int, list[list[float]]] = {}
    for values in value_rows:
        by_columns.setdefault(len(values), []).append(values)
    _, group = max(by_columns.items(), key=lambda item: (len(item[1]), item[0]))
    if len(group) < 3:
        return []
    first, second, third = group[0], group[1], group[2]
    return [
        _dimension_metrics(first[index], second[index], third[index], divisor=divisor)
        for index in range(min(len(first), len(second), len(third)))
    ]


def _dual_unit_issues(rows: Iterable[str]) -> dict[float, str]:
    """找出「同一长度的双单位标注」里两个单位换算不一致的项。

    返回 {折叠后保留的厘米值: 提示文案}；一致的标注视为可信，不返回。
    只认「公制 cm + 英制 inch」这种真正的双单位写法，避免把无关的「a/b」误判。
    """
    issues: dict[float, str] = {}
    for raw in rows:
        text = _basic_normalize(raw)
        for match in _DUAL_UNIT_PAIR_RE.finditer(text):
            n1, u1, n2, u2 = match.groups()
            u1, u2 = u1 or "", u2 or ""
            if (u1 in _CM_UNITS and u2 in _IMPERIAL_UNITS):
                cm_first, cm_second = to_cm(float(n1), u1), to_cm(float(n2), u2)
                kept = cm_first
            elif (u2 in _CM_UNITS and u1 in _IMPERIAL_UNITS):
                cm_first, cm_second = to_cm(float(n1), u1), to_cm(float(n2), u2)
                kept = cm_second
            else:
                continue
            if cm_first <= 0 or cm_second <= 0:
                continue
            tolerance = max(
                _DUAL_UNIT_TOLERANCE_CM,
                max(cm_first, cm_second) * _DUAL_UNIT_TOLERANCE_RATIO,
            )
            if abs(cm_first - cm_second) <= tolerance:
                continue
            issues[round(kept, 6)] = f"{n1}{u1}/{n2}{u2} 两个单位不一致"
    return issues


def _with_dual_unit_warnings(
    metrics: Iterable[ShippingMetrics], issues: dict[float, str]
) -> list[ShippingMetrics]:
    """给尺寸来自「双单位不一致」标注的候选附上提示，供前端高亮。"""
    result: list[ShippingMetrics] = []
    for item in metrics:
        notes: list[str] = []
        for dim in (item.length_cm, item.width_cm, item.height_cm):
            if dim is None:
                continue
            message = issues.get(round(dim, 6))
            if message and message not in notes:
                notes.append(message)
        result.append(replace(item, warnings=notes) if notes else item)
    return result


def resolve_shipping_metrics(
    rows: Iterable[str], *, divisor: float = DEFAULT_VOLUMETRIC_DIVISOR
) -> tuple[ShippingMetrics, list[ShippingMetrics]]:
    """得到「最佳结果 + 候选列表」。

    优先取「维度 + 重量都完整」的行（典型件重尺表格，一行一个商品）；
    没有完整行时，退回跨行聚合（重量与尺寸各在一行，如「实际重量：1.2kg」+「尺寸：30*20*10cm」）。
    """
    materialized = [str(raw) for raw in rows]
    best, candidates = _resolve_candidates(materialized, divisor=divisor)
    # 双单位标注换算不一致时，对应的候选标上低置信提示，让用户核对。
    issues = _dual_unit_issues(materialized)
    if issues and candidates:
        candidates = _with_dual_unit_warnings(candidates, issues)
        best = candidates[0]
    return best, candidates


def _resolve_candidates(
    materialized: list[str], *, divisor: float
) -> tuple[ShippingMetrics, list[ShippingMetrics]]:
    parsed = parse_shipping_rows(materialized, divisor=divisor)
    complete = [
        item for item in parsed if item.length_cm is not None and item.actual_weight_kg is not None
    ]
    if complete:
        return complete[0], complete
    # 一行里列出多组尺寸（如尺码列表）时，各组都是可选候选，直接让用户点选。
    dimension_only = [item for item in parsed if item.length_cm is not None]
    if len(dimension_only) > 1:
        return dimension_only[0], dimension_only
    # 尺码表（每行一个属性、每列一个尺码）按列组合出各尺码的尺寸候选。
    table_groups = _table_column_candidates(materialized, divisor=divisor)
    if table_groups:
        return table_groups[0], table_groups
    dimension_groups = _dimension_candidates(materialized, divisor=divisor)
    if dimension_groups:
        return dimension_groups[0], dimension_groups
    aggregate = parse_shipping_metrics(materialized, divisor=divisor)
    if aggregate.actual_weight_kg is None and aggregate.length_cm is None:
        return aggregate, []
    return aggregate, [aggregate]
