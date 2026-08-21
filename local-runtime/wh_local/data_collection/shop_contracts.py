"""Public contracts for durable OneBound whole-shop collection."""

from __future__ import annotations

from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field


ShopBatchStatus = Literal[
    "queued", "resolving", "listing", "enriching", "pausing", "paused",
    "cancelling", "cancelled", "completed", "partial", "failed",
]
ShopItemStatus = Literal["pending", "running", "succeeded", "failed", "cancelled"]
ShopIntakeAction = Literal["none", "created", "refreshed", "skipped"]

ACTIVE_BATCH_STATUSES = frozenset(
    {"queued", "resolving", "listing", "enriching", "pausing", "paused", "cancelling"}
)
TERMINAL_BATCH_STATUSES = frozenset({"cancelled", "completed", "partial", "failed"})


class ShopBatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    batch_id: str
    workspace_id: str
    actor_id: str
    shop_sid: str
    seed_offer_id: str = ""
    shop_url: str = ""
    shop_name: str = ""
    status: ShopBatchStatus
    next_page: int = 1
    pages_fetched: int = 0
    max_pages: int = 100
    listing_complete: bool = False
    discovered_count: int = 0
    duplicate_count: int = 0
    missing_id_count: int = 0
    succeeded_count: int = 0
    failed_count: int = 0
    created_count: int = 0
    refreshed_count: int = 0
    skipped_count: int = 0
    error_code: str = ""
    error_message: str = ""
    created_at: str
    updated_at: str
    started_at: str | None = None
    completed_at: str | None = None


class ShopBatchItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: str
    batch_id: str
    workspace_id: str
    offer_id: str
    source_url: str = ""
    source_title: str = ""
    detail_status: ShopItemStatus
    intake_action: ShopIntakeAction = "none"
    attempts: int = 0
    error_code: str = ""
    error_message: str = ""
    candidate: Mapping[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str
    completed_at: str | None = None


class ShopBatchPage(BaseModel):
    items: tuple[ShopBatch, ...]
    total: int


class ShopBatchItemPage(BaseModel):
    items: tuple[ShopBatchItem, ...]
    total: int
