"""Validated Pydantic collection criteria for the daily-selection workflow."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, ValidationError, field_validator, model_validator


class DailySelectionCriteriaError(ValueError):
    """Raised when a collection request is incomplete or inconsistent."""


_HTTP_URL = TypeAdapter(HttpUrl)

# 面向用户的中文字段名：校验失败时按字段给出可读提示，不外露 Pydantic 的英文原文。
_FIELD_LABELS: dict[str, str] = {
    "keywords": "采集关键词",
    "collection_mode": "采集方式",
    "collection_platform": "采集平台",
    "selection_scope": "选品范围",
    "reference_image_url": "参考图 URL",
    "category": "选品方向",
    "min_price": "最低价",
    "max_price": "最高价",
    "min_moq": "最小起订量",
    "min_sku_count": "SKU 数量下限",
    "max_sku_count": "SKU 数量上限",
    "min_sku_price": "SKU 最低价",
    "max_sku_price": "SKU 最高价",
    "min_sku_stock": "SKU 最低库存",
    "max_sku_stock": "SKU 最高库存",
    "target_count": "采集数量",
    "max_api_calls": "接口调用上限",
    "detail_count": "详情覆盖数量",
    "exclude_risks": "风险过滤",
    "site": "站点",
    "max_parallel_collect": "采集并行数",
}


def _label_for(field_name: str) -> str:
    return _FIELD_LABELS.get(field_name, field_name)


def _field_label(location: object) -> str:
    """取 Pydantic loc 里最后一个字段名并转成中文；模型级校验没有 loc，用通用名。"""

    if isinstance(location, (list, tuple)):
        for part in reversed(location):
            if isinstance(part, str):
                return _label_for(part)
    return "采集参数"


def _describe_error(item: object) -> str:
    """把单条 Pydantic 错误转成中文；自定义校验器的中文说明原样透出。"""

    if not isinstance(item, dict):
        return "采集参数不正确。"
    label = _field_label(item.get("loc"))
    error_type = str(item.get("type") or "")
    message = str(item.get("msg") or "").strip()
    context: dict[str, object] = item.get("ctx") if isinstance(item.get("ctx"), dict) else {}
    if error_type == "value_error":
        # 校验器抛出的就是我们自己的中文说明，Pydantic 会统一加 "Value error, " 前缀。
        if message.startswith("Value error, "):
            message = message[len("Value error, ") :]
        return message or f"{label}不正确。"
    if error_type == "greater_than_equal":
        return f"{label}不能小于 {context.get('ge')}。"
    if error_type == "less_than_equal":
        return f"{label}不能大于 {context.get('le')}。"
    if error_type == "greater_than":
        return f"{label}必须大于 {context.get('gt')}。"
    if error_type == "less_than":
        return f"{label}必须小于 {context.get('lt')}。"
    if error_type == "literal_error":
        expected = context.get("expected")
        return f"{label}只能是 {expected}。" if expected else f"{label}取值不合法。"
    if error_type == "missing":
        return f"缺少必填参数：{label}。"
    if error_type == "extra_forbidden":
        return f"存在不支持的参数：{label}。"
    return f"{label}不正确。"


def criteria_error_message(error: ValidationError) -> str:
    """把 Pydantic 的 ValidationError 汇总成一行可直接展示给用户的中文提示。"""

    messages: list[str] = []
    for item in error.errors():
        text = _describe_error(item)
        if text and text not in messages:
            messages.append(text)
    return "；".join(messages) or "采集参数不正确，请检查后重试。"


def _normalized_keywords(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        value = (value,)
    if not isinstance(value, (list, tuple)):
        raise DailySelectionCriteriaError("采集关键词必须是文本列表。")
    normalized: list[str] = []
    for keyword in value:
        if not isinstance(keyword, str):
            raise DailySelectionCriteriaError("采集关键词只能包含文本。")
        candidate = " ".join(keyword.split())
        if candidate and candidate not in normalized:
            normalized.append(candidate)
    return tuple(normalized)


def _decimal(value: object, label: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise DailySelectionCriteriaError(f"{label}必须是数字。")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise DailySelectionCriteriaError(f"{label}必须是数字。") from error
    if not result.is_finite():
        raise DailySelectionCriteriaError(f"{label}必须是有限数字。")
    if result < 0:
        raise DailySelectionCriteriaError(f"{label}不能为负数。")
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
    target_count: int = Field(default=30, ge=1, le=100)
    max_api_calls: int = Field(default=0, ge=0, description="兼容旧客户端字段；不再限制万邦调用次数")
    detail_count: int = Field(default=50, ge=1, le=190, description="启用 SKU 筛选时的详情覆盖上限；未启用时只拉取前 target_count 个候选的详情")
    exclude_risks: bool = True
    site: Literal["US", "CO", "EC"] = "US"
    max_parallel_collect: int = Field(default=8, ge=1, le=10, description="采集并行数，1=串行")

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except ValidationError as error:
            raise DailySelectionCriteriaError(criteria_error_message(error)) from error

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
            raise DailySelectionCriteriaError("参考图 URL 必须是文本。")
        try:
            return str(_HTTP_URL.validate_python(value.strip()))
        except ValidationError as error:
            raise DailySelectionCriteriaError(
                "参考图 URL 必须是有效的 http 或 https 地址。"
            ) from error
        raise AssertionError("unreachable")

    @field_validator("min_price", "max_price", "min_sku_price", "max_sku_price", mode="before")
    @classmethod
    def _decimal_price(cls, value: object, info: Any) -> Decimal | None:
        return _decimal(value, _label_for(info.field_name))

    @field_validator(
        "min_moq", "target_count", "max_api_calls", "detail_count",
        "min_sku_count", "max_sku_count", "min_sku_stock", "max_sku_stock",
        mode="before",
    )
    @classmethod
    def _strict_integers(cls, value: object, info: Any) -> object:
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise DailySelectionCriteriaError(f"{_label_for(info.field_name)}必须是整数。")
        return value

    @field_validator("exclude_risks", mode="before")
    @classmethod
    def _strict_boolean(cls, value: object) -> bool:
        if not isinstance(value, bool):
            raise DailySelectionCriteriaError("风险过滤必须是布尔值。")
        return value

    @model_validator(mode="after")
    def _consistent_request(self) -> "DailySelectionCriteria":
        if self.collection_mode == "keyword":
            if not 1 <= len(self.keywords) <= 5:
                raise DailySelectionCriteriaError("关键词采集需要 1–5 个关键词。")
            if self.reference_image_url is not None:
                raise DailySelectionCriteriaError("关键词采集不能同时提供参考图。")
        elif self.reference_image_url is None:
            raise DailySelectionCriteriaError("图片采集需要提供参考图 URL。")
        if self.min_price is not None and self.max_price is not None and self.min_price > self.max_price:
            raise DailySelectionCriteriaError("最低价不能高于最高价。")
        if self.min_moq is not None and self.min_moq < 1:
            raise DailySelectionCriteriaError("最小起订量必须是正整数。")
        if self.min_sku_count is not None and self.max_sku_count is not None and self.min_sku_count > self.max_sku_count:
            raise DailySelectionCriteriaError("SKU 数量下限不能高于上限。")
        if self.min_sku_count is not None and self.min_sku_count < 1:
            raise DailySelectionCriteriaError("SKU 数量下限必须是正整数。")
        if self.min_sku_price is not None and self.max_sku_price is not None and self.min_sku_price > self.max_sku_price:
            raise DailySelectionCriteriaError("SKU 最低价不能高于最高价。")
        if self.min_sku_stock is not None and self.max_sku_stock is not None and self.min_sku_stock > self.max_sku_stock:
            raise DailySelectionCriteriaError("SKU 最低库存不能高于最高库存。")
        if self.min_sku_stock is not None and self.min_sku_stock < 1:
            raise DailySelectionCriteriaError("SKU 最低库存必须是正整数。")
        return self

    @property
    def keyword_tags(self) -> tuple[str, ...]:
        """Keywords are descriptive tags in image mode, never a second query."""
        return self.keywords if self.collection_mode == "image" else ()


def validate_criteria(payload: object) -> DailySelectionCriteria:
    """构造并校验采集参数；校验失败时抛带中文说明的 DailySelectionCriteriaError。

    与 ``DailySelectionCriteria.model_validate`` 的区别：后者直接抛 Pydantic 的
    ``ValidationError``（英文原文），会经异步任务状态原样透给用户；这里统一转成
    一行中文提示，同步 422 与异步任务失败两条路都能直接展示。
    """

    try:
        return DailySelectionCriteria.model_validate(payload)
    except ValidationError as error:
        raise DailySelectionCriteriaError(criteria_error_message(error)) from error
