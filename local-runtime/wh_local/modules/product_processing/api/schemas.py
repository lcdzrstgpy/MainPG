from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..domain.models import DailySelectionHandoffEnvelope, DailySelectionRun, SiteCode, TargetLanguage


class DraftCreateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_type: str = "manual"
    source_ref: str = ""
    candidate_id: str | None = None
    skc: str | None = None
    sku: str | None = None
    product_name: str = ""
    title: str = ""
    description: str = ""
    image_url: str = ""
    main_image_url: str = ""
    cost: float | None = None
    declared_price: float | None = None


class DraftUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_ref: str | None = None
    skc: str | None = None
    sku: str | None = None
    product_name: str | None = None
    title: str | None = None
    description: str | None = None
    image_url: str | None = None
    main_image_url: str | None = None
    image_path: str | None = None
    cost: float | None = None
    declared_price: float | None = None
    status: str | None = None
    sku_name_edits: dict[str, str] | None = None
    sku_name_deletes: list[str] | None = None


class DraftDeleteRequest(BaseModel):
    draft_ids: list[int] = Field(default_factory=list)
    delete_all: bool = False

    @field_validator("draft_ids")
    @classmethod
    def positive_ids(cls, value: list[int]) -> list[int]:
        return list(dict.fromkeys(item for item in value if item > 0))


class DraftProcessRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str = "产品处理任务-草稿池商品"
    draft_ids: list[int]
    max_products: int = Field(default=0, ge=0, le=1000)
    async_mode: bool = True
    preflight_only: bool = False
    category_preflight_only: bool = False
    force_new_task: bool = False
    plugin_session_id: int | None = None
    title_optimize: bool = True
    description: bool = True
    size: bool = True
    grid_image: bool = True
    detail_image: bool = True
    product_video_template: bool = False
    cos_upload: bool = True
    strict_external: bool = False
    qualification_mode: bool | str = False
    ai_media_opt_in: bool = True
    image_rewrite: bool = True
    preserve_source_images: bool = True
    source_image_to_library: bool | None = None
    target_site: SiteCode = "US"
    target_language: TargetLanguage = "en"

    @field_validator("draft_ids")
    @classmethod
    def require_drafts(cls, value: list[int]) -> list[int]:
        normalized = list(dict.fromkeys(item for item in value if item > 0))
        if not normalized:
            raise ValueError("draft_ids is required")
        return normalized


class RetryTaskRequest(BaseModel):
    plugin_session_id: int | None = None


class PromptUpdateRequest(BaseModel):
    prompts: dict[str, str] = Field(default_factory=dict)


class DailySelectionIntakeRequest(DailySelectionRun):
    pass


class DailySelectionHandoffRequest(BaseModel):
    handoffs: list[DailySelectionHandoffEnvelope] = Field(min_length=1)


class DownloadRequest(BaseModel):
    task_id: int
    kind: str


def extras(model: BaseModel) -> dict[str, Any]:
    return dict(model.model_extra or {})
