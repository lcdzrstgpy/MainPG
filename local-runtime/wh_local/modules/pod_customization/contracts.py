from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator


SUPPORTED_PATTERN_COUNTS = (20, 40, 100)
MIN_STYLE_COUNT = 1
MAX_STYLE_COUNT = 200
PromptVersion = Literal["v1"]


def grid_call_count(pattern_count: int) -> int:
    if pattern_count not in SUPPORTED_PATTERN_COUNTS:
        raise ValueError("count must be one of 20, 40, or 100")
    return pattern_count // 4


def style_grid_call_count(style_count: int) -> int:
    """Each new POD style owns exactly one 2×2 image-generation request."""
    if isinstance(style_count, bool) or not isinstance(style_count, int):
        raise ValueError("count must be an integer")
    if not MIN_STYLE_COUNT <= style_count <= MAX_STYLE_COUNT:
        raise ValueError("count must be between 1 and 200")
    return style_count


class NormalizedPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class NormalizedRect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def validate_bounds(self) -> "NormalizedRect":
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("mask must stay inside the normalized canvas")
        return self


class Calibration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mask: NormalizedRect
    anchor: NormalizedPoint


class BusinessFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_name: str = ""
    product_category: str = ""
    target_market: str = ""
    target_audience: str = ""
    core_selling_points: list[str] = Field(default_factory=list)
    design_theme: str = ""
    style_planning: str = ""
    style_keywords: list[str] = Field(default_factory=list)
    color_preferences: list[str] = Field(default_factory=list)
    excluded_elements: list[str] = Field(default_factory=list)


class ListingSku(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)

    name: str = Field(strict=True, min_length=1, max_length=120)
    length_cm: float = Field(strict=True, gt=0)
    width_cm: float = Field(strict=True, gt=0)
    height_cm: float = Field(strict=True, gt=0)
    weight_g: float = Field(strict=True, gt=0)


class ListingFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)

    title_mode: Literal["long", "short"] = "long"
    declared_price: float = Field(strict=True, gt=0)
    suggested_price_usd: float = Field(strict=True, gt=0)
    category_name: str = Field(min_length=1, max_length=120)
    skus: list[ListingSku] = Field(
        min_length=1,
        max_length=100,
    )
    # 第 4 张图「规格卡」；可选字段（Agent C 的集成点）。注意 SpecCardConfig 自带
    # config，父模型的 str_strip_whitespace 不会传播到嵌套模型 —— 单元格文本逐字保留。
    spec_card: SpecCardConfig | None = None


class BatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: str = Field(min_length=1, max_length=64)
    count: int = Field(strict=True, ge=MIN_STYLE_COUNT, le=MAX_STYLE_COUNT)
    prompt_version: PromptVersion = "v1"
    business_fields: BusinessFields = Field(default_factory=BusinessFields)
    listing_fields: ListingFields
    creative_prompt: str = Field(default="", max_length=4000)
    title: str = Field(default="", max_length=120)

    @model_validator(mode="after")
    def validate_product_category(self) -> "BatchCreate":
        if not self.business_fields.product_category.strip():
            raise ValueError("business_fields.product_category is required")
        return self


class DirectListingTrialCreate(BaseModel):
    """One synchronous, reference-locked 2x2 listing image trial."""

    model_config = ConfigDict(extra="forbid")

    template_id: str = Field(min_length=1, max_length=64)
    business_fields: BusinessFields = Field(default_factory=BusinessFields)
    creative_prompt: str = Field(default="", max_length=4000)


class CalibrationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calibration: Calibration


class SceneOptimizationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instruction: str = Field(default="", max_length=1000)


class RegenerateItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    creative_prompt: str = Field(default="", max_length=4000)


class BatchRetryFailedCreate(BaseModel):
    """Selected failed POD styles for one all-or-nothing retry submission."""

    model_config = ConfigDict(extra="forbid")

    image_style_indices: list[StrictInt] = Field(default_factory=list, max_length=200)
    title_style_indices: list[StrictInt] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def validate_selection(self) -> "BatchRetryFailedCreate":
        image_indices = self.image_style_indices
        title_indices = self.title_style_indices
        if not image_indices and not title_indices:
            raise ValueError("at least one failed style must be selected")
        if any(isinstance(index, bool) or not 1 <= index <= MAX_STYLE_COUNT for index in (*image_indices, *title_indices)):
            raise ValueError("style index must be between 1 and 200")
        if len(set(image_indices)) != len(image_indices):
            raise ValueError("image_style_indices must not contain duplicates")
        if len(set(title_indices)) != len(title_indices):
            raise ValueError("title_style_indices must not contain duplicates")
        if set(image_indices).intersection(title_indices):
            raise ValueError("a style cannot be retried as both image and title")
        return self


class ManualTitleUpdate(BaseModel):
    """A user-entered listing title that bypasses AI copy validation."""

    model_config = ConfigDict(extra="forbid")

    title: str

    @model_validator(mode="after")
    def strip_title(self) -> "ManualTitleUpdate":
        self.title = self.title.strip()
        if not self.title:
            raise ValueError("title is required")
        return self


class ExportSelectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected: StrictBool


# --- 第 4 张图「规格卡」（方案 docs/superpowers/specs/2026-09-10-pod-spec-card-plan.md §4/§10.4） ---
#
# 配置随批次冻结在 listing_fields_json.spec_card（零 DB 迁移）。
# 渲染由 spec_card.py 负责：用户填什么就印什么 —— 不翻译、不做单位换算、不做变量替换。
# 这里的字符串取值必须与 spec_card.STYLES / spec_card.CORNERS 保持一致。

SPEC_CARD_STYLES = ("light", "dark")
SpecCardStyle = Literal["light", "dark"]
SPEC_CARD_CORNERS = ("bottom-right", "bottom-left", "top-right", "top-left")
SpecCardCorner = Literal["bottom-right", "bottom-left", "top-right", "top-left"]
SPEC_CARD_MAX_ROWS = 12
SPEC_CARD_MAX_COLUMNS = 6
SPEC_CARD_MAX_CELL_LENGTH = 120

_SPEC_CARD_GRID_ERROR = "规格卡表格结构不正确"
_SPEC_CARD_CELL_ERROR = "规格卡表格单元格必须是文本"


def validate_spec_card_cells(
    cells: Sequence[Sequence[str]] | None,
    *,
    require_content: bool = True,
) -> tuple[tuple[str, ...], ...]:
    """校验并规范化规格卡的 m×n 单元格；非法时抛 ValueError（中文消息，与页面提示一致）。

    规则（§10.2）：行 1–8（**空行不计**）、列 1–3、每个单元格 ≤120 个字符。
    单元格文本不做任何加工（不 strip、不转换），只做长度与结构校验。
    """

    rows = _coerce_spec_card_rows(cells)
    content_rows = [row for row in rows if any(cell.strip() for cell in row)]
    if len(content_rows) > SPEC_CARD_MAX_ROWS:
        raise ValueError(f"规格卡最多 {SPEC_CARD_MAX_ROWS} 行")
    columns = max((_spec_card_row_columns(row) for row in content_rows), default=0)
    if columns > SPEC_CARD_MAX_COLUMNS:
        raise ValueError(f"规格卡最多 {SPEC_CARD_MAX_COLUMNS} 列")
    for row in rows:
        for cell in row:
            if len(cell) > SPEC_CARD_MAX_CELL_LENGTH:
                raise ValueError(f"规格卡每格最多 {SPEC_CARD_MAX_CELL_LENGTH} 个字符")
    if require_content and not content_rows:
        raise ValueError("规格卡至少需要一个非空单元格")
    return rows


def validate_spec_card(
    config: "SpecCardConfig | Mapping[str, Any]",
    *,
    require_content: bool = True,
) -> SpecCardConfig:
    """提交前 / worker 用的整卡校验入口：返回校验通过的配置，非法则抛 ValueError。"""

    model = config if isinstance(config, SpecCardConfig) else SpecCardConfig.from_mapping(config)
    validate_spec_card_cells(model.cells, require_content=require_content)
    return model


def spec_card_is_configured(config: "SpecCardConfig | Mapping[str, Any] | None") -> bool:
    """§10.4 判定口径：只要存在任意非空单元格就算已配置（不校验行数、不要求填满）。"""

    if config is None:
        return False
    if isinstance(config, Mapping):
        try:
            config = SpecCardConfig.from_mapping(config)
        except ValueError:
            return False
    if not isinstance(config, SpecCardConfig):
        return False
    return any(cell.strip() for row in config.cells for cell in row)


class SpecCardConfig(BaseModel):
    """第 4 张图规格卡配置（随批次快照冻结；P1 的 per_style 不属于本模型）。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    style: str = "light"
    corner: str = "bottom-right"
    cells: tuple[tuple[str, ...], ...] = ()

    @model_validator(mode="after")
    def validate_spec_card(self) -> "SpecCardConfig":
        if self.style not in SPEC_CARD_STYLES:
            raise ValueError(f"规格卡风格必须是 {' 或 '.join(SPEC_CARD_STYLES)}")
        if self.corner not in SPEC_CARD_CORNERS:
            raise ValueError("规格卡位置必须是右下、左下、右上、左上之一")
        # 空表是合法状态（未配置），由 spec_card_is_configured / 提交拦截处理，这里只校验上限。
        self.cells = validate_spec_card_cells(self.cells, require_content=False)
        return self

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "SpecCardConfig":
        """从冻结快照字典构造；缺失字段用默认值，未知键忽略（向前兼容 P1 的 per_style）。"""

        if payload is None:
            payload = {}
        if not isinstance(payload, Mapping):
            raise ValueError("规格卡配置结构不正确")
        return cls(
            enabled=payload.get("enabled", True),
            style=payload.get("style", "light"),
            corner=payload.get("corner", "bottom-right"),
            cells=validate_spec_card_cells(payload.get("cells"), require_content=False),
        )


class SpecCardRequestBase(BaseModel):
    """规格卡接口请求体：``cells`` 刻意宽松（Any）。

    表格结构/上限/风格/位置的错误统一交给 ``validate_spec_card`` 抛中文 ValueError，
    路由据此返回 400，而不是让 pydantic 先给出 422 英文结构错误。
    """

    model_config = ConfigDict(extra="forbid")

    cells: Any = None
    style: str = "light"
    corner: str = "bottom-right"

    def config_mapping(self) -> dict[str, Any]:
        return {"cells": self.cells, "style": self.style, "corner": self.corner}


class SpecCardPreviewRequest(SpecCardRequestBase):
    """同源预览请求（方案 §7）；``base_template_id`` 有值时用该模板当底图。"""

    base_template_id: str = ""


class SpecCardReprintRequest(SpecCardRequestBase):
    """终态全批重印请求（方案 §8）；``style_index`` 有值时只重印该款。"""

    style_index: StrictInt | None = None


def _coerce_spec_card_rows(cells: Sequence[Sequence[str]] | None) -> tuple[tuple[str, ...], ...]:
    if cells is None:
        return ()
    if isinstance(cells, (str, bytes, bytearray)) or not isinstance(cells, Sequence):
        raise ValueError(_SPEC_CARD_GRID_ERROR)
    rows: list[tuple[str, ...]] = []
    for row in cells:
        if isinstance(row, (str, bytes, bytearray)) or not isinstance(row, Sequence):
            raise ValueError(_SPEC_CARD_GRID_ERROR)
        normalized: list[str] = []
        for cell in row:
            if cell is None:
                normalized.append("")
            elif isinstance(cell, str):
                normalized.append(cell)
            else:
                raise ValueError(_SPEC_CARD_CELL_ERROR)
        rows.append(tuple(normalized))
    return tuple(rows)


def _spec_card_row_columns(row: Sequence[str]) -> int:
    """该行占用的列数 = 最后一个非空单元格之后不再计数（尾部空单元格不占列）。"""

    for index in range(len(row) - 1, -1, -1):
        if row[index].strip():
            return index + 1
    return 0
