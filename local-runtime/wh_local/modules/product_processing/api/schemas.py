from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..domain.models import DailySelectionHandoffEnvelope, DailySelectionRun, SiteCode, TargetLanguage

PROCESSING_SCOPE_OPTIONS = {
    "title": "标题",
    "details": "详情",
    "product_dimensions": "产品尺寸",
    "four_grid": "四宫格",
    "detail_images": "详情图",
    "sku_images": "SKU 图",
    "qualification": "资质",
}


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
    # 旧版布尔开关（向后兼容）
    title_optimize: bool = True
    description: bool = True
    size: bool = True
    grid_image: bool = True
    detail_image: bool = True
    product_video_template: bool = False
    cos_upload: bool = True
    strict_external: bool = False
    skip_duplicates: bool = False
    ip_check: bool = True
    qualification_mode: bool | str = False
    ai_media_opt_in: bool = True
    image_rewrite: bool = True
    preserve_source_images: bool = True
    source_image_to_library: bool | None = None
    target_site: SiteCode = "US"
    target_language: TargetLanguage = "en"
    # 新版原型风格选项
    processing_scope: list[str] = Field(default_factory=list)
    include_product_video: bool = False
    max_parallel_drafts: int = Field(default=1, ge=1, le=20, description="最大并行处理数，1=串行，上限20")
    image_generation_count: int = Field(
        default=4,
        description="单次生图承载的独立图片数：1=单图×4，2=双图×2，4=四宫格×1",
    )

    @field_validator("draft_ids")
    @classmethod
    def require_drafts(cls, value: list[int]) -> list[int]:
        normalized = list(dict.fromkeys(item for item in value if item > 0))
        if not normalized:
            raise ValueError("draft_ids is required")
        return normalized

    @field_validator("processing_scope")
    @classmethod
    def valid_scope(cls, value: list[str]) -> list[str]:
        valid = set(PROCESSING_SCOPE_OPTIONS)
        invalid = set(value) - valid
        if invalid:
            raise ValueError(f"processing_scope 包含非法值: {sorted(invalid)}")
        return list(dict.fromkeys(value))

    @field_validator("image_generation_count")
    @classmethod
    def valid_image_generation_count(cls, value: int) -> int:
        if value not in {1, 2, 4}:
            raise ValueError("image_generation_count 必须是 1、2 或 4")
        return value

    @model_validator(mode="after")
    def sync_scope_and_legacy_options(self) -> "DraftProcessRequest":
        scope = set(self.processing_scope)
        legacy_scope = {
            "title": self.title_optimize,
            "details": self.description,
            "product_dimensions": self.size,
            "four_grid": self.grid_image,
            "detail_images": self.detail_image,
            "sku_images": self.image_rewrite,
            "qualification": bool(self.qualification_mode) if isinstance(self.qualification_mode, bool) else True,
        }
        if not scope:
            # 没有传 processing_scope 时，从旧布尔字段推导
            scope = {key for key, enabled in legacy_scope.items() if enabled}
            self.processing_scope = list(scope)
        else:
            # 传了 processing_scope 时，同步旧布尔字段，保持后端现有逻辑可用
            self.title_optimize = "title" in scope
            self.description = "details" in scope
            self.size = "product_dimensions" in scope
            self.grid_image = "four_grid" in scope
            self.detail_image = "detail_images" in scope
            self.image_rewrite = "sku_images" in scope
            if isinstance(self.qualification_mode, bool):
                self.qualification_mode = "strict" if (self.qualification_mode and "qualification" in scope) else "standard"
            elif "qualification" not in scope:
                self.qualification_mode = "standard"
            elif self.qualification_mode not in {"standard", "strict"}:
                self.qualification_mode = "standard"
        if self.include_product_video:
            self.product_video_template = True
        return self


class RetryTaskRequest(BaseModel):
    plugin_session_id: int | None = None
    draft_ids: list[int] = Field(default_factory=list)


class PreviewImageManifestInput(BaseModel):
    main_asset_id: str = ""
    carousel_asset_ids: list[str] = Field(default_factory=list)
    detail_asset_ids: list[str] = Field(default_factory=list)
    semantic_asset_ids: dict[str, str] = Field(default_factory=dict)


class PreviewDesiredState(BaseModel):
    title: str
    description: str
    core_fields: dict[str, Any] = Field(default_factory=dict)
    image_manifest_v2: PreviewImageManifestInput


class PreviewSaveItem(BaseModel):
    product_draft_id: int
    expected_preview_revision: int = Field(ge=0)
    expected_result_version: str = Field(default="", max_length=64)
    overrides: PreviewDesiredState


class PreviewSaveRequest(BaseModel):
    items: list[PreviewSaveItem] = Field(default_factory=list)


class PreviewFinalizeRequest(PreviewSaveRequest):
    pass


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
