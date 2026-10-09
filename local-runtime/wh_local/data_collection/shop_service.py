"""Application service for whole-shop batch commands and queries."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from .service import DailySelectionActor
from .shop_contracts import ShopBatch, ShopBatchItemPage, ShopBatchPage
from .shop_repository import InvalidShopBatchTransition, ShopCollectionRepository


class ShopCollectionInputError(ValueError):
    pass


class ShopCollectionConflict(ValueError):
    pass


class ShopCollectionProviderUnavailable(RuntimeError):
    pass


class ShopCollectionService:
    def __init__(
        self,
        *,
        repository: ShopCollectionRepository,
        provider_config_resolver: Callable[[DailySelectionActor], Mapping[str, Any]],
        worker: Any,
        batch_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.repository = repository
        self._provider_config_resolver = provider_config_resolver
        self._worker = worker
        self._batch_id_factory = batch_id_factory or (lambda: f"shop-{uuid.uuid4().hex}")

    def create_batch(
        self, *, actor: DailySelectionActor, source_input: str, platform: str = "1688"
    ) -> ShopBatch:
        if platform not in {"1688", "taobao"}:
            raise ShopCollectionInputError("采集平台仅支持 1688 或淘宝/天猫")
        # 粘贴商品/店铺链接时按链接自动识别平台，覆盖面板手选结果，
        # 避免「1688 面板 + 淘宝链接」这类误操作导致解析失败。
        resolved_platform = _resolve_platform(source_input, platform)
        shop_sid, seed_offer_id, shop_url = _parse_source_input(source_input, resolved_platform)
        try:
            config = self._provider_config_resolver(actor)
        except Exception as error:
            raise ShopCollectionProviderUnavailable("shop collection provider is unavailable") from error
        if not isinstance(config, Mapping) or config.get("enabled", True) is False:
            raise ShopCollectionProviderUnavailable("shop collection provider is unavailable")
        batch = self.repository.create_batch(
            batch_id=self._batch_id_factory(),
            workspace_id=actor.workspace_id,
            actor_id=actor.actor_id,
            platform=resolved_platform,
            shop_sid=shop_sid,
            shop_url=shop_url,
            seed_offer_id=seed_offer_id,
            max_pages=100,
        )
        self._worker.notify()
        return batch

    def list_batches(self, *, actor: DailySelectionActor, limit: int, offset: int) -> ShopBatchPage:
        return ShopBatchPage(
            items=self.repository.list_batches(workspace_id=actor.workspace_id, limit=limit, offset=offset),
            total=self.repository.count_batches(workspace_id=actor.workspace_id),
        )

    def get_batch(self, *, actor: DailySelectionActor, batch_id: str) -> ShopBatch:
        return self.repository.get_batch(workspace_id=actor.workspace_id, batch_id=batch_id)

    def list_items(self, *, actor: DailySelectionActor, batch_id: str, limit: int, offset: int) -> ShopBatchItemPage:
        return ShopBatchItemPage(
            items=self.repository.list_items(
                workspace_id=actor.workspace_id, batch_id=batch_id, limit=limit, offset=offset
            ),
            total=self.repository.count_items(workspace_id=actor.workspace_id, batch_id=batch_id),
        )

    def pause(self, *, actor: DailySelectionActor, batch_id: str) -> ShopBatch:
        batch = self.get_batch(actor=actor, batch_id=batch_id)
        allowed = {"queued", "resolving", "listing", "enriching"}
        if batch.status not in allowed:
            raise ShopCollectionConflict("batch cannot be paused in its current state")
        result = self._transition(batch_id, "pausing", allowed)
        self._worker.notify()
        return result

    def resume(self, *, actor: DailySelectionActor, batch_id: str) -> ShopBatch:
        batch = self.get_batch(actor=actor, batch_id=batch_id)
        if batch.status != "paused":
            raise ShopCollectionConflict("only paused batches can be resumed")
        result = self._transition(batch_id, "queued", {"paused"})
        self._worker.notify()
        return result

    def cancel(self, *, actor: DailySelectionActor, batch_id: str) -> ShopBatch:
        batch = self.get_batch(actor=actor, batch_id=batch_id)
        allowed = {"queued", "resolving", "listing", "enriching", "pausing", "paused"}
        if batch.status not in allowed:
            raise ShopCollectionConflict("batch cannot be cancelled in its current state")
        result = self._transition(batch_id, "cancelling", allowed)
        self._worker.notify()
        return result

    def retry_failed(self, *, actor: DailySelectionActor, batch_id: str) -> ShopBatch:
        batch = self.get_batch(actor=actor, batch_id=batch_id)
        if batch.status not in {"failed", "partial"}:
            raise ShopCollectionConflict("only failed or partial batches can be retried")
        self.repository.reset_failed_items(batch_id=batch_id)
        try:
            result = self.repository.transition_batch(
                batch_id, "queued", expected_statuses={"failed", "partial"}
            )
        except InvalidShopBatchTransition as error:
            raise ShopCollectionConflict(str(error)) from error
        self._worker.notify()
        return result

    def _transition(self, batch_id: str, status: str, expected: set[str]) -> ShopBatch:
        try:
            return self.repository.transition_batch(
                batch_id, status, expected_statuses=expected
            )
        except InvalidShopBatchTransition as error:
            raise ShopCollectionConflict(str(error)) from error


def _parse_source_input(source_input: str, platform: str = "1688") -> tuple[str, str, str]:
    if not isinstance(source_input, str) or not source_input.strip():
        raise ShopCollectionInputError("采集线索不能为空")
    value = source_input.strip()
    if len(value) > 4096:
        raise ShopCollectionInputError("采集线索过长，请只粘贴商品链接或商品 ID")
    if platform == "taobao":
        return _parse_taobao_source(value)
    return _parse_1688_source(value)


def _resolve_platform(source_input: str, selected: str) -> str:
    """URL 线索按域名自动识别平台；非 URL（数字、前缀）尊重手动选择。"""
    if "://" in str(source_input):
        try:
            from .shop_parsing import detect_shop_platform

            detected = detect_shop_platform(source_input)
            if detected in {"1688", "taobao"}:
                return detected
        except ImportError:
            pass
    return selected


def _parse_1688_source(value: str) -> tuple[str, str, str]:
    explicit_sid = re.fullmatch(r"sid:\s*([0-9]+)", value, re.IGNORECASE)
    if explicit_sid:
        return explicit_sid.group(1), "", ""
    if "://" in value:
        # 链接行为：交由 extract_* 解析；失败给中文提示。
        try:
            from .shop_parsing import extract_1688_offer_id, extract_1688_shop_sid

            offer_id = extract_1688_offer_id(value)
        except ImportError:
            offer_id = _fallback_offer_id(value)
        except ValueError:
            try:
                shop_sid = extract_1688_shop_sid(value)
            except ValueError:
                raise ShopCollectionInputError(
                    "无法识别该链接：不是 1688 商品/店铺链接，请粘贴 1688 商品链接、商品 ID 或店铺链接"
                ) from None
            return shop_sid, "", value
        return f"pending:{offer_id}", offer_id, value
    if re.fullmatch(r"[A-Za-z_@-][A-Za-z0-9_.@-]{2,127}", value):
        try:
            from .shop_parsing import validate_shop_sid

            return validate_shop_sid(value), "", ""
        except ImportError:
            return value, "", ""
    try:
        from .shop_parsing import extract_1688_offer_id

        offer_id = extract_1688_offer_id(value)
    except ValueError:
        raise ShopCollectionInputError(
            "无法识别该输入：请粘贴 1688 商品链接/商品 ID，或店铺 SID（sid:xxx）"
        ) from None
    return f"pending:{offer_id}", offer_id, ""


def _parse_taobao_source(value: str) -> tuple[str, str, str]:
    explicit_shop = re.fullmatch(r"(?:shopid|shop_id|sid):\s*([0-9]+)", value, re.IGNORECASE)
    if explicit_shop:
        return explicit_shop.group(1), "", ""
    explicit_item = re.fullmatch(r"(?:id|itemid|item_id|offerid):\s*([0-9]+)", value, re.IGNORECASE)
    if explicit_item:
        return f"pending:{explicit_item.group(1)}", explicit_item.group(1), ""
    if re.fullmatch(r"[0-9]{8,20}", value):
        # 纯数字无法区分商品 ID 与店铺 ID，默认按商品 ID（seed 定位店铺）。
        return f"pending:{value}", value, ""
    if "://" not in value:
        raise ShopCollectionInputError(
            "淘宝整店采集需要商品链接/商品 ID（或 shopid:xxx 形式的店铺 ID）"
        )
    try:
        from .shop_parsing import extract_taobao_item_id

        item_id = extract_taobao_item_id(value)
    except ValueError:
        try:
            from .shop_parsing import extract_taobao_shop_id

            shop_id = extract_taobao_shop_id(value)
        except ValueError:
            raise ShopCollectionInputError(
                "无法识别该链接：不是淘宝/天猫商品或店铺链接。店铺首页（如 volare.tmall.com）"
                "无法直接定位店铺，请粘贴该店内任一商品链接"
            ) from None
        return shop_id, "", value
    return f"pending:{item_id}", item_id, value


def _fallback_offer_id(value: str) -> str:
    compact = re.sub(r"\s+", "", value)
    if compact.isdigit() and 8 <= len(compact) <= 20:
        return compact
    if "1688.com" not in compact.casefold():
        raise ShopCollectionInputError("only a 1688 shop link, offer link, offer ID, or shop SID is supported")
    for pattern in (r"/offer/(\d{8,20})(?:\.html)?", r"offerId(?:=|-|%3D)(\d{8,20})"):
        match = re.search(pattern, compact, re.IGNORECASE)
        if match:
            return match.group(1)
    raise ShopCollectionInputError("the 1688 link does not contain an offer ID")
