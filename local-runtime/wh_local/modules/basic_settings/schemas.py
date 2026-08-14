from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class TextAiConfig(BaseModel):
    """文本 AI 配置，对应旧工作台“文本 AI”区域。"""

    base_url: str = ""
    model: str = ""
    api_key: str | None = None
    clear_api_key: bool = False


class ImageConfig(BaseModel):
    """主图生图和备用图生图共用结构。"""

    base_url: str = ""
    model: str = ""
    reference_model: str = ""
    api_key: str | None = None
    clear_api_key: bool = False


class CosConfig(BaseModel):
    """腾讯云 COS 配置，密钥字段保存时会进入 secret_values。"""

    bucket: str = ""
    region: str = "ap-guangzhou"
    secret_id: str | None = None
    secret_key: str | None = None
    clear_secret_id: bool = False
    clear_secret_key: bool = False


class RuntimeLimits(BaseModel):
    """运行限制，控制文本/图片任务并发、限流和失败重试策略。"""

    text_workers: int = Field(default=30, ge=1, le=60)
    image_workers: int = Field(default=15, ge=1, le=100)
    text_request_limit: int = Field(default=30, ge=1, le=100)
    image_request_limit: int = Field(default=15, ge=1, le=100)
    image_retry_attempts: int = Field(default=3, ge=1, le=5)
    image_provider_strategy: str = "primary_first"
    provider_backup_share_percent: int = Field(default=0, ge=0, le=90)
    image_stop_after_billable_failure: bool = True

    @field_validator("image_provider_strategy")
    @classmethod
    def validate_provider_strategy(cls, value: str) -> str:
        normalized = (value or "balanced").strip()
        if normalized not in {"balanced", "primary_first", "backup_first", "cost_first"}:
            raise ValueError("image_provider_strategy is invalid")
        return normalized


class UpdateConfig(BaseModel):
    cos_prefix: str = "temu-y2-control"
    public_base_url: str = ""


class SystemConfigUpdate(BaseModel):
    """系统配置页面一次保存提交的完整表单。"""

    ai: TextAiConfig = Field(default_factory=TextAiConfig)
    image: ImageConfig = Field(default_factory=ImageConfig)
    backup_image: ImageConfig = Field(default_factory=ImageConfig)
    cos: CosConfig = Field(default_factory=CosConfig)
    limits: RuntimeLimits = Field(default_factory=RuntimeLimits)
    updates: UpdateConfig = Field(default_factory=UpdateConfig)
