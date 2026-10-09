from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator


SUPPORTED_PATTERN_COUNTS = (20, 40, 100)
MIN_STYLE_COUNT = 1
MAX_STYLE_COUNT = 200
# 半定制：4 格 = 4 款，发起数量必须是 4 的倍数；交付单元 = 单张图案。
MIN_SEMI_ITEM_COUNT = 4
MAX_SEMI_ITEM_COUNT = 200
SEMI_PATTERN_ROLES = ("pattern_1", "pattern_2", "pattern_3", "pattern_4")
PromptVersion = Literal["v1"]

# 批次模式：全定制 full / 半定制 semi / 爆款复刻 replica。历史批次 mode 默认 full。
PodBatchMode = Literal["full", "semi", "replica"]
POD_BATCH_MODES = ("full", "semi", "replica")
# 复刻：一张样图 + 1–200 张目标白底图，每张对应一款。
MIN_REPLICA_TARGETS = 1
MAX_REPLICA_TARGETS = 200
REPLICA_IMAGE_ROLES = ("source", "target")
ReplicaImageRole = Literal["source", "target"]


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

    product_name: Annotated[str, Field(max_length=500)] = ""
    product_category: Annotated[str, Field(max_length=500)] = ""
    target_market: Annotated[str, Field(max_length=500)] = ""
    target_audience: Annotated[str, Field(max_length=500)] = ""
    core_selling_points: list[Annotated[str, Field(max_length=200)]] = Field(
        default_factory=list, max_length=100
    )
    design_theme: Annotated[str, Field(max_length=500)] = ""
    style_keywords: list[Annotated[str, Field(max_length=200)]] = Field(
        default_factory=list, max_length=100
    )
    color_preferences: list[Annotated[str, Field(max_length=200)]] = Field(
        default_factory=list, max_length=100
    )
    excluded_elements: list[Annotated[str, Field(max_length=200)]] = Field(
        default_factory=list, max_length=100
    )
    # 选填：用户手写的上架文案限制（例如「标题不要出现刺绣」「明确带上 2D Flat」）。
    # 只作用于标题/英文标题/描述，不进入图片提示词。
    copy_restrictions: Annotated[str, Field(max_length=2000)] = ""


# --- 智能前置层：模糊输入 → 结构化业务字段 ---
# 方案见 docs/superpowers/specs/2026-09-12-pod-brief-preprocessing-design.md。
BRIEF_INPUT_MIN_LENGTH = 1
BRIEF_INPUT_MAX_LENGTH = 500
# 元素关键词必须多写：元素池越大，跨款图案差异化越明显（后端按款式随机分配主打/辅主/点缀）。
BRIEF_STYLE_KEYWORDS_MIN_ITEMS = 40
# 偏好配色要尽量多：每款的强调色从配色表里轮换抽取，颜色越多跨款差异越大。
BRIEF_COLOR_PREFERENCES_MIN_ITEMS = 10
# 禁用元素要尽量多，且必须覆盖侵权类与危险类（防止商品/店铺被封）。
BRIEF_EXCLUDED_ELEMENTS_MIN_ITEMS = 10


class BriefFieldRequest(BaseModel):
    """用户在前置层里写下的一句模糊主题/需求。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    brief: str = Field(min_length=BRIEF_INPUT_MIN_LENGTH, max_length=BRIEF_INPUT_MAX_LENGTH)
    locale: str = Field(default="zh-CN", max_length=16)


class BriefFieldResponse(BaseModel):
    """AI 转换结果：字段与 BusinessFields 一一对应，前端直接写回表单。"""

    model_config = ConfigDict(extra="forbid")

    brief_id: str
    prompt_version: str
    model: str
    fields: BusinessFields


# --- 构图/视角定制：一段大白话 → 四格画面指令（每账号+工作区只保留最新一份）---
# 四格角色固定（由我们确定，用户只决定每格怎么拍）：
#   panel_1 = 主图；panel_2 = 细节图 A；panel_3 = 细节图 B；panel_4 = 素材图。
# 每格存两份文本：zh 供前端展示与手动编辑，en 由后台转写并注入生图提示词。
COMPOSITION_PANEL_KEYS = ("panel_1", "panel_2", "panel_3", "panel_4")
COMPOSITION_PANEL_SLOTS = {
    "panel_1": "主图",
    "panel_2": "细节图 A",
    "panel_3": "细节图 B",
    "panel_4": "素材图",
}
COMPOSITION_INPUT_MIN_LENGTH = 1
COMPOSITION_INPUT_MAX_LENGTH = 500
COMPOSITION_PANEL_MAX_LENGTH = 500


class CompositionRequest(BaseModel):
    """用户对四张图视角/构图的一段大白话描述，由后端 LLM 解析成四格画面指令。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    brief: str = Field(min_length=COMPOSITION_INPUT_MIN_LENGTH, max_length=COMPOSITION_INPUT_MAX_LENGTH)
    locale: str = Field(default="zh-CN", max_length=16)


class CompositionPanel(BaseModel):
    """单格画面指令：中文供展示/编辑，英文由后台转写后注入提示词。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    zh: str = Field(min_length=1, max_length=COMPOSITION_PANEL_MAX_LENGTH)
    en: str = Field(min_length=1, max_length=COMPOSITION_PANEL_MAX_LENGTH)


class CompositionPanels(BaseModel):
    """四格画面指令；键固定对应固定的四格角色。"""

    model_config = ConfigDict(extra="forbid")

    panel_1: CompositionPanel
    panel_2: CompositionPanel
    panel_3: CompositionPanel
    panel_4: CompositionPanel


class CompositionPanelsZh(BaseModel):
    """四格中文画面指令（用户手动编辑后的提交形状）。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    panel_1: str = Field(min_length=1, max_length=COMPOSITION_PANEL_MAX_LENGTH)
    panel_2: str = Field(min_length=1, max_length=COMPOSITION_PANEL_MAX_LENGTH)
    panel_3: str = Field(min_length=1, max_length=COMPOSITION_PANEL_MAX_LENGTH)
    panel_4: str = Field(min_length=1, max_length=COMPOSITION_PANEL_MAX_LENGTH)


class CompositionUpdateRequest(BaseModel):
    """保存用户手动编辑后的四格中文指令；后台据此重新转写英文。"""

    model_config = ConfigDict(extra="forbid")

    panels: CompositionPanelsZh


COMPOSITION_NAME_MAX_LENGTH = 60


class CompositionRenameRequest(BaseModel):
    """给一份构图模板改名。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=COMPOSITION_NAME_MAX_LENGTH)


class CompositionResponse(BaseModel):
    """一份构图模板：raw_input 为原话，panels 为四格画面指令，is_active 标记是否生效。"""

    model_config = ConfigDict(extra="forbid")

    composition_id: str
    name: str
    raw_input: str
    panels: CompositionPanels
    model: str
    prompt_version: str
    is_active: bool
    # 系统内置「默认模板」标记：不可编辑/重命名/删除，只能设为生效。
    is_builtin: bool = False
    updated_at: str


class CompositionListResponse(BaseModel):
    """构图模板列表；生效的排最前。"""

    model_config = ConfigDict(extra="forbid")

    templates: list[CompositionResponse]
    total: int


class ListingSku(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)

    name: str = Field(strict=True, min_length=1, max_length=120)
    # 申报价改为每个 SKU 各一个；长/宽/高改由 listing_fields.spec_card 的尺寸详情表格承载。
    declared_price: float = Field(strict=True, gt=0)
    weight_g: float = Field(strict=True, gt=0)


class ListingFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)

    title_mode: Literal["long", "short"] = "long"
    suggested_price_usd: float = Field(strict=True, gt=0)
    category_name: str = Field(min_length=1, max_length=120)
    skus: list[ListingSku] = Field(
        min_length=1,
        max_length=100,
    )
    # 第 4 张图「规格卡」：表头 + 每个 SKU 一行的尺寸详情表格，同时是导出长/宽/高的取值来源。
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


class SemiBatchCreate(BaseModel):
    """半定制创建：纯提示词生成图案，不用模板、不用参考图、不用上架字段。

    4 格 = 4 款；``count`` 为交付图案张数，必须是 4 的倍数（4..200）。
    业务字段只消费图案相关项（主题风格 / 元素 / 配色 / 禁用元素），
    产品名、品类、市场、人群、卖点对纯图案生成无意义，故不做必填校验。
    """

    model_config = ConfigDict(extra="forbid")

    count: int = Field(strict=True, ge=MIN_SEMI_ITEM_COUNT, le=MAX_SEMI_ITEM_COUNT)
    prompt_version: PromptVersion = "v1"
    business_fields: BusinessFields = Field(default_factory=BusinessFields)
    creative_prompt: str = Field(default="", max_length=4000)
    title: str = Field(default="", max_length=120)

    @model_validator(mode="after")
    def validate_semi_batch(self) -> "SemiBatchCreate":
        if self.count % 4 != 0:
            raise ValueError("半定制数量必须是 4 的倍数")
        return self


# --- 爆款复刻：一张样图搬到 1–200 个目标产品，每款独立上架信息 ---


class ReplicaImageUploadResponse(BaseModel):
    """复刻独立图片上传结果：角色、尺寸与授权本地预览路径。"""

    model_config = ConfigDict(extra="forbid")

    asset_id: str
    role: ReplicaImageRole
    width: int
    height: int
    preview_url: str


class ReplicaTargetCreate(BaseModel):
    """复刻创建时的单项目标产品：一张白底图 + 商品名 + 复用现有上架字段。"""

    model_config = ConfigDict(extra="forbid")

    target_asset_id: str = Field(min_length=1, max_length=64)
    product_name: str = Field(min_length=1, max_length=500)
    listing_fields: ListingFields


class ReplicaBatchCreate(BaseModel):
    """复刻批次创建：款数由 ``targets`` 长度决定，不另设数量字段。

    ``listing_fields`` 复用现有 ``ListingFields``；``business_fields`` 由服务端从
    产品名与类目派生，其余可选业务字段保持空默认值，不做主题智能填写。
    """

    model_config = ConfigDict(extra="forbid")

    client_request_id: str = Field(min_length=1, max_length=200)
    source_asset_id: str = Field(min_length=1, max_length=64)
    title: str = Field(default="", max_length=120)
    creative_prompt: str = Field(default="", max_length=4000)
    targets: list[ReplicaTargetCreate] = Field(
        min_length=MIN_REPLICA_TARGETS, max_length=MAX_REPLICA_TARGETS
    )

    @model_validator(mode="after")
    def validate_replica_creative_prompt(self) -> "ReplicaBatchCreate":
        # 复刻不接受非空指示词：图案只来自样图，避免从 API 绕过图案锁定规则。
        if self.creative_prompt.strip():
            raise ValueError("复刻模式不接受指示词，图案只由样图决定")
        return self


class ReplicaBatch(TypedDict):
    """复刻批次公共响应：共有批次字段 + 来源样图 source + 有序目标 targets。"""

    id: str
    batch_id: str
    mode: str
    title: str
    status: str
    count: int
    source: dict[str, Any]
    targets: list[dict[str, Any]]


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

    title: Annotated[str, Field(max_length=300)]

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
# 渲染由 spec_card.py 负责；固定尺寸表在渲染/导出边界按 display_unit 换算，冻结数据仍保留厘米原值。
# 这里的字符串取值必须与 spec_card.STYLES / spec_card.CORNERS 保持一致。

from .spec_card_units import (
    SPEC_CARD_DISPLAY_UNIT_CM,
    validate_spec_card_display_unit,
)

SPEC_CARD_STYLES = ("light", "dark")
SpecCardStyle = Literal["light", "dark"]
SPEC_CARD_CORNERS = ("bottom-right", "bottom-left", "top-right", "top-left")
SpecCardCorner = Literal["bottom-right", "bottom-left", "top-right", "top-left"]
SPEC_CARD_MAX_ROWS = 101  # 尺寸详情表格行数 = 1 行表头 + 最多 100 个 SKU 行
SPEC_CARD_MAX_COLUMNS = 6
SPEC_CARD_MAX_CELL_LENGTH = 120
SPEC_CARD_MAX_TOTAL_CELLS = SPEC_CARD_MAX_ROWS * SPEC_CARD_MAX_COLUMNS

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
    display_unit: str = SPEC_CARD_DISPLAY_UNIT_CM
    cells: tuple[tuple[str, ...], ...] = ()

    @model_validator(mode="after")
    def validate_spec_card(self) -> "SpecCardConfig":
        if self.style not in SPEC_CARD_STYLES:
            raise ValueError(f"规格卡风格必须是 {' 或 '.join(SPEC_CARD_STYLES)}")
        if self.corner not in SPEC_CARD_CORNERS:
            raise ValueError("规格卡位置必须是右下、左下、右上、左上之一")
        self.display_unit = validate_spec_card_display_unit(self.display_unit)
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
            display_unit=payload.get("display_unit", SPEC_CARD_DISPLAY_UNIT_CM),
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
    display_unit: str = SPEC_CARD_DISPLAY_UNIT_CM
    # 是否把卡片印到第 4 张图上；关掉时素材图保持干净母版（长/宽/高数据仍必填）。
    enabled: bool = True

    def config_mapping(self) -> dict[str, Any]:
        return {
            "cells": self.cells,
            "style": self.style,
            "corner": self.corner,
            "display_unit": self.display_unit,
            "enabled": self.enabled,
        }


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
    if len(cells) > SPEC_CARD_MAX_ROWS:
        raise ValueError(f"规格卡最多 {SPEC_CARD_MAX_ROWS} 行")
    rows: list[tuple[str, ...]] = []
    total_cells = 0
    for row in cells:
        if isinstance(row, (str, bytes, bytearray)) or not isinstance(row, Sequence):
            raise ValueError(_SPEC_CARD_GRID_ERROR)
        if len(row) > SPEC_CARD_MAX_COLUMNS:
            raise ValueError(f"规格卡最多 {SPEC_CARD_MAX_COLUMNS} 列")
        total_cells += len(row)
        if total_cells > SPEC_CARD_MAX_TOTAL_CELLS:
            raise ValueError(f"规格卡最多 {SPEC_CARD_MAX_TOTAL_CELLS} 个单元格")
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
