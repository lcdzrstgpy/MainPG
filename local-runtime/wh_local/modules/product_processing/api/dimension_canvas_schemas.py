from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _StrictRequest(BaseModel):
    # In particular, reject client-supplied workspace_id. Workspace identity is
    # derived only from the authenticated/header server context.
    model_config = ConfigDict(extra="forbid")


class ImportPreviewItemRequest(_StrictRequest):
    task_id: int = Field(gt=0)
    task_item_id: int = Field(gt=0)


class ImportTaskRequest(_StrictRequest):
    task_id: int = Field(gt=0)
    task_item_ids: list[int] = Field(min_length=1, max_length=1000)
    existing_dimension_actions: dict[str, Literal["keep", "remake", "skip"]] = Field(default_factory=dict)

    @field_validator("task_item_ids")
    @classmethod
    def unique_positive_ids(cls, value: list[int]) -> list[int]:
        normalized = list(dict.fromkeys(int(item) for item in value if int(item) > 0))
        if not normalized:
            raise ValueError("task_item_ids is required")
        return normalized


class SaveDimensionItemRequest(_StrictRequest):
    expected_revision: int = Field(ge=0)
    selected_source_asset_id: str | None = None
    target_slot_id: str | None = None
    physical_dimensions: dict[str, Any] | None = None
    annotations: list[dict[str, Any]] | None = None
    canvas_settings: dict[str, Any] | None = None


class CompleteDimensionItemRequest(_StrictRequest):
    expected_revision: int | None = Field(default=None, ge=0)
