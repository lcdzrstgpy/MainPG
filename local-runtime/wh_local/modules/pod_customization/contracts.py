from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


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
