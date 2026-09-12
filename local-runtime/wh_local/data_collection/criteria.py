"""Validated Pydantic collection criteria for the daily-selection workflow."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, ValidationError, field_validator, model_validator


class DailySelectionCriteriaError(ValueError):
    """Raised when a collection request is incomplete or inconsistent."""


_HTTP_URL = TypeAdapter(HttpUrl)

# 采集条件校验失败时给用户看的前缀，保证任何一条错误都是同一种句式。
_REASON_PREFIX = "采集条件有误："

# 字段名 -> 中文标签。pydantic 内置错误只给出英文字段名，需要翻译后才好读。
_FIELD_LABELS: dict[str, str] = {
    "keywords": "关键词",
    "collection_mode": "采集模式",
    "collection_platform": "采集平台",
    "selection_scope": "选品范围",
    "reference_image_url": "参考图地址",
    "category": "采集方向",
    "min_price": "最低价格",
    "max_price": "最高价格",
    "min_moq": "起订量",
    "min_sku_count": "SKU 规格数下限",
    "max_sku_count": "SKU 规格数上限",
    "min_sku_price": "SKU 最低价",
    "max_sku_price": "SKU 最高价",
    "min_sku_stock": "SKU 库存下限",
    "max_sku_stock": "SKU 库存上限",
    "target_count": "采集数量",
    "max_api_calls": "接口调用上限",
    "detail_count": "详情覆盖数量",
    "exclude_risks": "风险过滤开关",
    "site": "站点",
    "max_parallel_collect": "采集并行数",
}

# 本模块校验器直接抛出的固定英文文案 -> 中文。
_REASON_LABELS: dict[str, str] = {
    "keywords must be a sequence of strings": "关键词格式不正确",
    "keywords must contain strings": "关键词格式不正确",
    "keyword mode requires one to five normalized keywords": "关键词模式下必须填写 1~5 个关键词",
    "keyword mode cannot include reference_image_url": "关键词模式下不能同时提供参考图",
    "image mode requires reference_image_url": "图片模式必须提供参考图地址",
    "reference_image_url must be a URL string": "参考图地址格式不正确",
    "reference_image_url must be a valid http or https URL": "参考图必须是可公开访问的 http/https 链接",
    "exclude_risks must be a boolean": "风险过滤开关取值不正确",
    "min_price cannot be greater than max_price": "最低价格不能大于最高价格",
    "min_sku_price cannot be greater than max_sku_price": "SKU 最低价不能大于 SKU 最高价",
    "min_moq must be a positive integer": "起订量必须是正整数",
    "min_sku_count must be a positive integer": "SKU 规格数必须是正整数",
    "min_sku_count cannot be greater than max_sku_count": "SKU 规格数下限不能大于上限",
    "min_sku_stock must be a positive integer": "SKU 库存必须是正整数",
    "min_sku_stock cannot be greater than max_sku_stock": "SKU 库存下限不能大于上限",
}

# "<字段名> <后缀>" 形态的动态文案：后缀 -> 中文。
_REASON_SUFFIX_LABELS: dict[str, str] = {
    "must be a decimal number": "必须是数字",
    "must be finite": "必须是有效数字",
    "cannot be negative": "不能为负数",
    "must be an integer": "必须是整数",
    "must be a boolean": "取值不正确",
    "must be a URL string": "格式不正确",
}

# pydantic 内置错误文案 -> 中文模板，保留上游给出的取值范围。
_BUILTIN_REASON_LABELS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^Input should be a valid (.+)$"), "格式不正确"),
    (re.compile(r"^Input should be greater than or equal to (.+)$"), "不能小于 {0}"),
    (re.compile(r"^Input should be less than or equal to (.+)$"), "不能大于 {0}"),
    (re.compile(r"^Input should be greater than (.+)$"), "必须大于 {0}"),
    (re.compile(r"^Input should be less than (.+)$"), "必须小于 {0}"),
    (re.compile(r"^Input should be (.+)$"), "取值必须是 {0}"),
    (re.compile(r"^Field required$"), "缺少必填项"),
    (re.compile(r"^Extra inputs are not permitted$"), "包含不支持的字段"),
)


def _strip_value_error_prefix(text: str) -> str:
    """pydantic 会给校验器抛出的 ValueError 逐层加上 "Value error, " 前缀。"""
    result = text.strip()
    while result.startswith("Value error, "):
        result = result[len("Value error, "):].strip()
    return result


def _innermost_error(raw: str) -> tuple[str, str]:
    """取出 pydantic 渲染文本里最内层的 (字段名, 原因)。

    `model_validate` 会把 `__init__` 抛出的错误整体再包一层，外层消息里嵌着一段
    完整的渲染文本；只在检测到嵌套时才拆解，单层消息直接返回原始原因。
    """
    text = _strip_value_error_prefix(raw)
    if "validation error for" not in text:
        return "", text
    lines = text.splitlines()
    for index, line in enumerate(lines):
        candidate = line.strip()
        if " [type=" not in candidate:
            continue
        field_name = ""
        for previous in reversed(lines[:index]):
            label = previous.strip()
            if label and not label.startswith("1 validation error for") and " [type=" not in label:
                field_name = label
                break
        return field_name, _strip_value_error_prefix(candidate.split(" [type=", 1)[0])
    return "", text


def _localized_reason(field_name: str, reason: str) -> str:
    if not reason:
        return "参数格式不正确"
    exact = _REASON_LABELS.get(reason)
    if exact is not None:
        return exact
    for known_field, field_label in _FIELD_LABELS.items():
        for suffix, suffix_label in _REASON_SUFFIX_LABELS.items():
            if reason == f"{known_field} {suffix}":
                return f"{field_label}{suffix_label}"
    label = _FIELD_LABELS.get(field_name, "")
    for pattern, template in _BUILTIN_REASON_LABELS:
        matched = pattern.match(reason)
        if matched is None:
            continue
        detail = template.format(*matched.groups()) if matched.groups() else template
        return f"{label}{detail}" if label else detail
    return f"{label}{reason}" if label else reason


def localize_criteria_error(error: BaseException | str) -> str:
    """把可能被双层包装的 pydantic 校验错误压成一句可读中文。"""
    raw = str(error)
    if _REASON_PREFIX in raw:
        # 已经本地化过，只需去掉外层包装残留的换行与调试后缀。
        localized = raw[raw.index(_REASON_PREFIX):].strip()
        return localized.split("\n", 1)[0].split(" [type=", 1)[0].strip()
    field_name, reason = _innermost_error(raw)
    return f"{_REASON_PREFIX}{_localized_reason(field_name, reason)}"


def _normalized_keywords(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        value = (value,)
    if not isinstance(value, (list, tuple)):
        raise DailySelectionCriteriaError("keywords must be a sequence of strings")
    normalized: list[str] = []
    for keyword in value:
        if not isinstance(keyword, str):
            raise DailySelectionCriteriaError("keywords must contain strings")
        candidate = " ".join(keyword.split())
        if candidate and candidate not in normalized:
            normalized.append(candidate)
    return tuple(normalized)


def _decimal(value: object, field_name: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise DailySelectionCriteriaError(f"{field_name} must be a decimal number")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise DailySelectionCriteriaError(f"{field_name} must be a decimal number") from error
    if not result.is_finite():
        raise DailySelectionCriteriaError(f"{field_name} must be finite")
    if result < 0:
        raise DailySelectionCriteriaError(f"{field_name} cannot be negative")
    return result


class DailySelectionCriteria(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    keywords: tuple[str, ...] = ()
    collection_mode: Literal["keyword", "image"] = "keyword"
    collection_platform: Literal["1688", "taobao"] = "1688"
    selection_scope: Literal["exact", "divergent"] = "divergent"
    reference_image_url: str | None = None
    category: str = ""
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    min_moq: int | None = None
    min_sku_count: int | None = None
    max_sku_count: int | None = None
    min_sku_price: Decimal | None = None
    max_sku_price: Decimal | None = None
    min_sku_stock: int | None = None
    max_sku_stock: int | None = None
    target_count: int = Field(default=30, ge=1, le=200)
    max_api_calls: int = Field(default=0, ge=0, description="兼容旧客户端字段；不再限制万邦调用次数")
    detail_count: int = Field(default=50, ge=1, le=190, description="启用 SKU 筛选时的详情覆盖上限；未启用时只拉取前 target_count 个候选的详情")
    exclude_risks: bool = True
    site: Literal["US", "CO", "EC"] = "US"
    max_parallel_collect: int = Field(default=8, ge=1, le=10, description="采集并行数，1=串行")

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except ValidationError as error:
            raise DailySelectionCriteriaError(localize_criteria_error(error)) from error

    @classmethod
    def validated(cls, data: Any) -> "DailySelectionCriteria":
        """统一构造入口：无论 pydantic 包几层，都只向上抛一句可读中文。"""
        try:
            return cls.model_validate(data)
        except DailySelectionCriteriaError:
            raise
        except ValidationError as error:
            raise DailySelectionCriteriaError(localize_criteria_error(error)) from error

    @field_validator("keywords", mode="before")
    @classmethod
    def _normalize_keywords(cls, value: object) -> tuple[str, ...]:
        return _normalized_keywords(value)

    @field_validator("reference_image_url", mode="before")
    @classmethod
    def _valid_reference_url(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise DailySelectionCriteriaError("reference_image_url must be a URL string")
        try:
            return str(_HTTP_URL.validate_python(value.strip()))
        except ValidationError as error:
            raise DailySelectionCriteriaError(
                "reference_image_url must be a valid http or https URL"
            ) from error
        raise AssertionError("unreachable")

    @field_validator("min_price", "max_price", "min_sku_price", "max_sku_price", mode="before")
    @classmethod
    def _decimal_price(cls, value: object, info: Any) -> Decimal | None:
        return _decimal(value, info.field_name)

    @field_validator(
        "min_moq", "target_count", "max_api_calls", "detail_count",
        "min_sku_count", "max_sku_count", "min_sku_stock", "max_sku_stock",
        mode="before",
    )
    @classmethod
    def _strict_integers(cls, value: object, info: Any) -> object:
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise DailySelectionCriteriaError(f"{info.field_name} must be an integer")
        return value

    @field_validator("exclude_risks", mode="before")
    @classmethod
    def _strict_boolean(cls, value: object) -> bool:
        if not isinstance(value, bool):
            raise DailySelectionCriteriaError("exclude_risks must be a boolean")
        return value

    @model_validator(mode="after")
    def _consistent_request(self) -> "DailySelectionCriteria":
        if self.collection_mode == "keyword":
            if not 1 <= len(self.keywords) <= 5:
                raise DailySelectionCriteriaError(
                    "keyword mode requires one to five normalized keywords"
                )
            if self.reference_image_url is not None:
                raise DailySelectionCriteriaError(
                    "keyword mode cannot include reference_image_url"
                )
        elif self.reference_image_url is None:
            raise DailySelectionCriteriaError("image mode requires reference_image_url")
        if self.min_price is not None and self.max_price is not None and self.min_price > self.max_price:
            raise DailySelectionCriteriaError("min_price cannot be greater than max_price")
        if self.min_moq is not None and self.min_moq < 1:
            raise DailySelectionCriteriaError("min_moq must be a positive integer")
        if self.min_sku_count is not None and self.max_sku_count is not None and self.min_sku_count > self.max_sku_count:
            raise DailySelectionCriteriaError("min_sku_count cannot be greater than max_sku_count")
        if self.min_sku_count is not None and self.min_sku_count < 1:
            raise DailySelectionCriteriaError("min_sku_count must be a positive integer")
        if self.min_sku_price is not None and self.max_sku_price is not None and self.min_sku_price > self.max_sku_price:
            raise DailySelectionCriteriaError("min_sku_price cannot be greater than max_sku_price")
        if self.min_sku_stock is not None and self.max_sku_stock is not None and self.min_sku_stock > self.max_sku_stock:
            raise DailySelectionCriteriaError("min_sku_stock cannot be greater than max_sku_stock")
        if self.min_sku_stock is not None and self.min_sku_stock < 1:
            raise DailySelectionCriteriaError("min_sku_stock must be a positive integer")
        return self

    @property
    def keyword_tags(self) -> tuple[str, ...]:
        """Keywords are descriptive tags in image mode, never a second query."""
        return self.keywords if self.collection_mode == "image" else ()
