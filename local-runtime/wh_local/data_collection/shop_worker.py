"""Recoverable background executor for OneBound whole-shop collection."""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .contracts import DailySelectionCandidate
from .normalizer import normalize_detail_response
from .service import DailySelectionActor
from .shop_repository import (
    InvalidShopBatchTransition,
    ShopBatchLease,
    ShopCollectionRepository,
    ShopLeaseLost,
)


logger = logging.getLogger("wh_local.data_collection.shop_worker")
_TRANSIENT_ERRORS = frozenset({"timeout", "rate_limited", "upstream_failed", "network_error"})
# 单店铺采集上限：万邦 item_search_shop(_pro) 对部分店铺深页数据不可靠
# （error_code=5000/4010），按用户约定单店最多采集该数量，达到后结束翻页。
_MAX_SHOP_ITEMS = 120


class _WorkerStopping(RuntimeError):
    pass


class _WorkerPaused(RuntimeError):
    pass


class _StaleItemLease(RuntimeError):
    pass


class ShopCollectionWorker:
    """Runs durable batches with one global three-slot detail pool."""

    DETAIL_CONCURRENCY = 3
    BATCH_LEASE_SECONDS = 120
    ITEM_LEASE_SECONDS = 120

    def __init__(
        self,
        *,
        repository: ShopCollectionRepository,
        provider_config_resolver: Callable[[DailySelectionActor], Mapping[str, Any]],
        provider_factory: Callable[[Mapping[str, Any]], Any],
        intake_shop_candidate: Callable[..., Mapping[str, Any]],
        page_normalizer: Callable[..., Any] | None = None,
        detail_normalizer: Callable[[Any, Any], Mapping[str, Any] | DailySelectionCandidate] | None = None,
        retry_delay_seconds: float = 0.05,
        unfinished_poll_seconds: float = 0.25,
        budget: Any | None = None,
        max_api_calls: int = 0,
    ) -> None:
        self.repository = repository
        self._provider_config_resolver = provider_config_resolver
        self._provider_factory = provider_factory
        self._intake = intake_shop_candidate
        self._page_normalizer = page_normalizer or _default_page_normalizer
        self._detail_normalizer = detail_normalizer or _default_detail_normalizer
        self._retry_delay_seconds = retry_delay_seconds
        if unfinished_poll_seconds <= 0:
            raise ValueError("unfinished_poll_seconds must be positive")
        self._unfinished_poll_seconds = unfinished_poll_seconds
        self._owner = f"shop-worker-{uuid.uuid4().hex}"
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._seed_details: dict[tuple[str, str], Any] = {}
        self._seed_lock = threading.Lock()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="shop-collection-worker", daemon=True)
        self._thread.start()
        self.notify()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join()

    def notify(self) -> None:
        self._wake.set()

    def retry_failed(self, *, workspace_id: str, batch_id: str) -> Any:
        batch = self.repository.get_batch(workspace_id=workspace_id, batch_id=batch_id)
        if batch.status not in {"partial", "failed"}:
            raise ValueError("only failed or partial batches can be retried")
        reset = self.repository.reset_failed_items(batch_id=batch_id)
        if batch.status == "failed" and reset == 0 and not batch.listing_complete:
            # Listing failures resume from the persisted page checkpoint.
            pass
        self.repository.transition_batch(batch_id, "queued")
        self.notify()
        return self.repository.get_batch(workspace_id=workspace_id, batch_id=batch_id)

    def process_batch(self, batch_id: str, *, lease: ShopBatchLease | None = None) -> None:
        active_lease = lease or self.repository.claim_batch(
            batch_id=batch_id, owner=self._owner, lease_seconds=self.BATCH_LEASE_SECONDS
        )
        if active_lease is None:
            return
        try:
            batch = self.repository.get_batch_internal(batch_id)
            if self._stop.is_set() or self._apply_control_state(batch, active_lease):
                return
            actor = DailySelectionActor(actor_id=batch.actor_id, workspace_id=batch.workspace_id)
            if batch.platform == "taobao":
                from .service import _platform_config

                config = _platform_config(self._provider_config_resolver(actor), "taobao")
            else:
                config = self._provider_config_resolver(actor)
            provider = self._provider_factory(config)
            if batch.status == "queued":
                batch = self._transition(active_lease, "resolving", {"queued"})
            if batch.status == "resolving":
                batch = self._resolve_shop(provider, batch, active_lease)
                if self._stop.is_set():
                    return
                batch = self._transition(active_lease, "listing", {"resolving"})
            if batch.status == "listing":
                batch = self._list_shop(provider, batch, active_lease)
                if self._stop.is_set() or self._apply_control_state(batch, active_lease):
                    return
                batch = self._transition(active_lease, "enriching", {"listing"})
            if batch.status == "enriching":
                self._enrich(provider, batch, active_lease)
        except (ShopLeaseLost, _WorkerStopping):
            return
        except _WorkerPaused:
            current = self.repository.get_batch_internal(batch_id)
            self._apply_control_state(current, active_lease)
        except InvalidShopBatchTransition:
            current = self.repository.get_batch_internal(batch_id)
            self._apply_control_state(current, active_lease)
        except Exception as error:
            logger.warning("shop collection batch %s failed: %s", batch_id, type(error).__name__)
            current = self.repository.get_batch_internal(batch_id)
            if current.status in {"pausing", "cancelling"}:
                self._apply_control_state(current, active_lease)
                return
            if current.status not in {"cancelled", "completed", "partial", "failed", "paused"}:
                try:
                    self.repository.transition_batch(
                        batch_id, "failed", expected_statuses={current.status}, owner=active_lease.lease_owner,
                        lease_token=active_lease.lease_token, error_code=_error_code(error),
                        error_message=_error_message(error),
                    )
                except (InvalidShopBatchTransition, ShopLeaseLost):
                    pass
        finally:
            self.repository.release_batch_lease(
                batch_id=batch_id, owner=active_lease.lease_owner, lease_token=active_lease.lease_token
            )

    def _resolve_shop(self, provider: Any, batch: Any, lease: ShopBatchLease) -> Any:
        if batch.platform == "taobao":
            return self._resolve_taobao_shop(provider, batch, lease)
        if not batch.seed_offer_id:
            return batch
        self._raise_if_paused(batch.batch_id)
        result = self._call_detail(provider, batch.seed_offer_id)
        self._raise_if_stopping()
        self._raise_if_paused(batch.batch_id)
        if not _result_ok(result):
            raise RuntimeError(_result_error_message(result))
        seller = _seller_info(_result_response(result))
        sid = str(seller.get("sid") or "").strip()
        if not sid:
            raise ValueError("item detail did not include a shop SID")
        try:
            from .shop_parsing import validate_shop_sid

            sid = validate_shop_sid(sid)
        except ImportError:
            if not sid:
                raise ValueError("invalid shop SID")
        with self._seed_lock:
            self._seed_details[(batch.batch_id, batch.seed_offer_id)] = result
        self._renew(lease)
        return self.repository.resolve_shop_identity(
            batch_id=batch.batch_id,
            shop_sid=sid,
            shop_name=str(seller.get("shop_name") or seller.get("nick") or ""),
        )

    def _resolve_taobao_shop(self, provider: Any, batch: Any, lease: ShopBatchLease) -> Any:
        """Resolve a Taobao/Tmall shop identity to shop_id + seller_id.

        ``item_get`` usually returns item-level ``shop_id``/``seller_id``, but
        misses either on some products. Fallbacks: seller_info.shop_id and the
        shop home URL (``seller_info.zhuy``), then ``seller_info(shop_id)`` to
        recover the seller user id when the detail response lacks it.
        """
        if batch.seed_offer_id:
            self._raise_if_paused(batch.batch_id)
            result = self._call_detail(provider, batch.seed_offer_id)
            self._raise_if_stopping()
            self._raise_if_paused(batch.batch_id)
            if not _result_ok(result):
                code = _result_error_code(result)
                upstream = _result_upstream_code(result)
                raise ValueError(
                    f"万邦未返回该商品数据（error_code={upstream or code}）。该商品可能属于万邦受限类目"
                    "（药品/农产品/五金/天猫国际/百亿补贴等，文档注明部分商品获取不到），"
                    "请换一家店的商品链接重试"
                )
            identity = _taobao_shop_identity(_result_response(result))
            shop_id = identity.get("shop_id")
            if not shop_id:
                raise ValueError(_taobao_identity_failure_message(_result_response(result)))
            seller_id = identity.get("seller_id")
            shop_name = identity.get("shop_name", "")
            if not seller_id:
                # item_get 未返回 seller_id：用 shop_id 调 seller_info 补齐。
                info_result = provider.get_seller_info(shop_id)
                self._raise_if_stopping()
                self._raise_if_paused(batch.batch_id)
                if not _result_ok(info_result):
                    raise RuntimeError(_result_error_message(info_result))
                info = _seller_info_identity(_result_response(info_result))
                seller_id = info.get("seller_id") or ""
                shop_name = shop_name or info.get("shop_name", "") or info.get("nick", "")
                if not seller_id:
                    raise ValueError(
                        "未能解析该店铺的卖家信息（seller_id），请换一个店铺或商品链接重试"
                    )
            with self._seed_lock:
                self._seed_details[(batch.batch_id, batch.seed_offer_id)] = result
            self._renew(lease)
            return self.repository.resolve_shop_identity(
                batch_id=batch.batch_id,
                shop_sid=shop_id,
                shop_name=shop_name,
                seller_id=seller_id,
            )
        self._raise_if_paused(batch.batch_id)
        result = provider.get_seller_info(batch.shop_sid)
        self._raise_if_stopping()
        self._raise_if_paused(batch.batch_id)
        if not _result_ok(result):
            raise RuntimeError(_result_error_message(result))
        identity = _seller_info_identity(_result_response(result))
        seller_id = identity.get("seller_id")
        if not seller_id:
            raise ValueError("未能解析该店铺的卖家信息（seller_id），请换一个店铺或商品链接重试")
        self._renew(lease)
        return self.repository.resolve_shop_identity(
            batch_id=batch.batch_id,
            shop_sid=batch.shop_sid,
            shop_name=identity.get("shop_name", "") or identity.get("nick", ""),
            seller_id=seller_id,
        )

    def _list_shop(self, provider: Any, batch: Any, lease: ShopBatchLease) -> Any:
        if batch.listing_complete:
            return batch
        page = batch.next_page
        stalled_pages = 0
        while page <= min(batch.max_pages, 100):
            self._raise_if_stopping()
            self._renew(lease)
            current = self.repository.get_batch_internal(batch.batch_id)
            if self._apply_control_state(current, lease):
                return self.repository.get_batch_internal(batch.batch_id)
            self._raise_if_paused(batch.batch_id)
            if current.discovered_count >= _MAX_SHOP_ITEMS:
                # 单店铺采集上限（万邦深页数据不可靠/按需截断）：达到后不再翻页。
                break
            if current.platform == "taobao":
                result = provider.search_shop(current.shop_sid, page, seller_id=current.seller_id)
            else:
                result = provider.search_shop(current.shop_sid, page)
            self._raise_if_stopping()
            self._raise_if_paused(batch.batch_id)
            if not _result_ok(result):
                if _listing_no_data(result):
                    # 万邦对该店的数据到此为止（error_code=4010 “不存在相应的数据
                    # 信息”、2000 无结果，或原因文本含“不存在/无数据”）：视为列表
                    # 结束而不是整批失败，已发现的商品继续走 enriching。
                    logger.info(
                        "shop collection batch %s listing ended at page %s: upstream no-data (%s)",
                        batch.batch_id, page, _result_upstream_code(result) or _result_error_code(result),
                    )
                    self._renew(lease)
                    self.repository.record_shop_page(
                        batch_id=batch.batch_id, page=page, items=(), has_next=False, missing_id_count=0,
                    )
                    break
                raise RuntimeError(_shop_listing_error_message(result))
            response = _result_response(result)
            normalized = self._page_normalizer(response, getattr(result, "audit", None))
            values = _page_values(normalized)
            missing_count = _non_negative_int(_value(normalized, "missing_offer_count", 0))
            has_next = _page_has_next(normalized, response=response, current_page=page)
            total_pages = _value(normalized, "total_pages", None)
            if isinstance(total_pages, int):
                has_next = page < min(total_pages, batch.max_pages, 100)
            if page >= min(batch.max_pages, 100):
                has_next = False
            if not values and page == 1:
                # 第 1 页就为空：万邦未收录该店商品，或接口/权限异常；给出可诊断原因。
                # 1688 分支同样要有守卫：裸店铺主页提出的 Web shop ID 不是 OneBound
                # 的 seller_nick（b2b-…），列表会静默返回 0，不能伪装成“成功”。
                if batch.platform == "taobao":
                    hint = (
                        "该店铺商品列表为空（第 1 页 0 条）：万邦可能未收录这家店的商品，"
                        "或商品属于受限类目（平台补贴/官方直营等），"
                        "或 item_search_shop_pro 权限/卖家身份（shop_id+seller_id）不正确"
                    )
                else:
                    hint = (
                        "该店铺商品列表为空（第 1 页 0 条）：万邦可能未收录这家店的商品，"
                        "或商品属于受限类目（平台补贴/官方直营等），"
                        "或店铺 SID 不是 OneBound 可用的卖家标识（seller_nick，b2b-… 形式）"
                    )
                raise RuntimeError(f"{hint}。可换一家店重试，或联系万邦确认该店数据支持情况")
            if current.discovered_count + len(values) >= _MAX_SHOP_ITEMS:
                # 本页触及单店铺采集上限：结束翻页（等价于 has_next=False）。
                has_next = False
            self._renew(lease)
            page_result = self.repository.record_shop_page(
                batch_id=batch.batch_id,
                page=page,
                items=values,
                has_next=has_next,
                missing_id_count=missing_count,
            )
            if page_result["created"] == 0:
                # 本页没有新增任何新商品（万邦分页循环/内容重复/空页）：
                # 连续 2 页无新增即判定分页停滞，提前结束列表，避免空转。
                stalled_pages += 1
                if stalled_pages >= 2:
                    logger.info(
                        "shop collection batch %s listing stalled at page %s (no new items for %s pages)",
                        batch.batch_id, page, stalled_pages,
                    )
                    self.repository.record_shop_page(
                        batch_id=batch.batch_id, page=page, items=(), has_next=False, missing_id_count=0,
                    )
                    break
            else:
                stalled_pages = 0
            if not has_next:
                break
            page += 1
        return self.repository.get_batch_internal(batch.batch_id)

    def _enrich(self, provider: Any, batch: Any, lease: ShopBatchLease) -> None:
        while True:
            self._raise_if_stopping()
            self._renew(lease)
            current = self.repository.get_batch_internal(batch.batch_id)
            if self._apply_control_state(current, lease):
                return
            claimed = self.repository.claim_pending_items(
                batch_id=batch.batch_id,
                owner=self._owner,
                limit=self.DETAIL_CONCURRENCY,
                lease_seconds=self.ITEM_LEASE_SECONDS,
            )
            if not claimed:
                if self.repository.has_unfinished_items(batch_id=batch.batch_id):
                    if self._stop.wait(self._unfinished_poll_seconds):
                        raise _WorkerStopping()
                    continue
                final = self.repository.get_batch_internal(batch.batch_id)
                status = "partial" if final.failed_count else "completed"
                self._transition(lease, status, {"enriching"})
                return
            with ThreadPoolExecutor(max_workers=self.DETAIL_CONCURRENCY, thread_name_prefix="shop-detail") as pool:
                futures = {
                    pool.submit(self._enrich_one, provider, batch, item, lease): item
                    for item in claimed
                }
                for future in as_completed(futures):
                    item = futures[future]
                    try:
                        candidate, action = future.result()
                    except _WorkerPaused:
                        self.repository.release_item(
                            batch_id=batch.batch_id, item_id=item.item_id,
                            owner=item.lease_owner, lease_token=item.lease_token,
                        )
                    except _WorkerStopping:
                        self.repository.release_item(
                            batch_id=batch.batch_id, item_id=item.item_id,
                            owner=item.lease_owner, lease_token=item.lease_token,
                        )
                    except _StaleItemLease:
                        pass
                    except Exception as error:
                        try:
                            self.repository.fail_item(
                                batch_id=batch.batch_id, item_id=item.item_id,
                                owner=item.lease_owner, lease_token=item.lease_token,
                                error_code=_error_code(error), error_message=_error_message(error),
                            )
                        except ShopLeaseLost:
                            pass
                    else:
                        if self._is_paused(batch.batch_id):
                            self.repository.release_item(
                                batch_id=batch.batch_id, item_id=item.item_id,
                                owner=item.lease_owner, lease_token=item.lease_token,
                            )
                        else:
                            try:
                                self.repository.complete_item(
                                    batch_id=batch.batch_id, item_id=item.item_id,
                                    owner=item.lease_owner, lease_token=item.lease_token,
                                    intake_action=action, candidate=candidate,
                                )
                            except ShopLeaseLost:
                                pass

    def _enrich_one(
        self, provider: Any, batch: Any, item: Any, lease: ShopBatchLease | None = None
    ) -> tuple[Mapping[str, Any], str]:
        with self._seed_lock:
            result = self._seed_details.pop((batch.batch_id, item.offer_id), None)
        if result is None:
            self._raise_if_paused(batch.batch_id)
            result = self._call_detail(provider, item.offer_id)
        if not _result_ok(result):
            raise RuntimeError(_result_error_message(result))
        self._raise_if_stopping()
        self._raise_if_paused(batch.batch_id)
        normalized = self._detail_normalizer(item, result)
        if isinstance(normalized, DailySelectionCandidate):
            candidate = normalized.model_dump(mode="json")
        elif isinstance(normalized, Mapping):
            candidate = dict(normalized)
        else:
            raise TypeError("detail normalizer returned an invalid candidate")
        self._raise_if_stopping()
        self._raise_if_paused(batch.batch_id)
        if not self.repository.renew_item_lease(
            batch_id=batch.batch_id, item_id=item.item_id, owner=item.lease_owner,
            lease_token=item.lease_token, lease_seconds=self.ITEM_LEASE_SECONDS,
        ):
            raise _StaleItemLease("item lease expired or was reclaimed before intake")
        intake = self._intake(
            batch_id=batch.batch_id,
            workspace_id=batch.workspace_id,
            candidate=candidate,
            collection_channel="shop_collection",
            **(
                {
                    "shop_fence": {
                        "batch_id": batch.batch_id,
                        "batch_lease_owner": lease.lease_owner,
                        "batch_lease_token": lease.lease_token,
                        "item_id": item.item_id,
                        "item_lease_owner": item.lease_owner,
                        "item_lease_token": item.lease_token,
                        "offer_id": item.offer_id,
                    }
                }
                if lease is not None
                else {}
            ),
        )
        action = str(intake.get("action") or "")
        if action not in {"created", "refreshed", "skipped"}:
            raise ValueError("product intake returned an invalid action")
        return candidate, action

    def _call_detail(self, provider: Any, offer_id: str) -> Any:
        result = None
        for attempt in range(3):
            result = provider.get_item_detail(offer_id)
            if _result_ok(result):
                return result
            code = _result_error_code(result)
            if code not in _TRANSIENT_ERRORS or attempt == 2:
                return result
            if self._retry_delay_seconds:
                if self._stop.wait(self._retry_delay_seconds * (attempt + 1)):
                    raise _WorkerStopping()
        return result

    def _apply_control_state(self, batch: Any, lease: ShopBatchLease) -> bool:
        if batch.status == "pausing":
            self._transition(lease, "paused", {"pausing"})
            return True
        if batch.status == "cancelling":
            self.repository.cancel_pending_items(batch_id=batch.batch_id)
            self._transition(lease, "cancelled", {"cancelling"})
            return True
        return batch.status in {"paused", "cancelled", "completed", "partial", "failed"}

    def _transition(self, lease: ShopBatchLease, status: str, expected: set[str]) -> Any:
        return self.repository.transition_batch(
            lease.batch_id, status, expected_statuses=expected, owner=lease.lease_owner,
            lease_token=lease.lease_token,
        )

    def _renew(self, lease: ShopBatchLease) -> None:
        if not self.repository.renew_batch_lease(
            batch_id=lease.batch_id, owner=lease.lease_owner, lease_token=lease.lease_token,
            lease_seconds=self.BATCH_LEASE_SECONDS,
        ):
            raise ShopLeaseLost("batch lease expired or was reclaimed")

    def _raise_if_stopping(self) -> None:
        if self._stop.is_set():
            raise _WorkerStopping()

    def _raise_if_paused(self, batch_id: str) -> None:
        if self._is_paused(batch_id):
            raise _WorkerPaused()

    def _is_paused(self, batch_id: str) -> bool:
        return self.repository.get_batch_internal(batch_id).status in {"pausing", "paused"}

    def _run(self) -> None:
        # 在后台线程里先恢复上次异常退出遗留的过期租约，避免在 lifespan
        # 启动阶段同步执行数据库写操作（该同步动作会被杀软行为引擎盯上）。
        try:
            self.repository.recover_interrupted_work()
        except Exception:
            logger.exception("shop worker recover_interrupted_work failed")
        while not self._stop.is_set():
            lease = self.repository.claim_next_runnable_batch(
                owner=self._owner, lease_seconds=self.BATCH_LEASE_SECONDS
            )
            if lease is None:
                self._wake.wait(timeout=2)
                self._wake.clear()
                continue
            self.process_batch(lease.batch_id, lease=lease)


def _default_page_normalizer(payload: Mapping[str, Any], evidence: Any = None) -> Any:
    from .shop_parsing import normalize_shop_page

    return normalize_shop_page(payload, evidence)


def _default_detail_normalizer(item: Any, result: Any) -> DailySelectionCandidate:
    return normalize_detail_response(
        _result_response(result), evidence=getattr(result, "audit", None)
    )


def _page_values(page: Any) -> tuple[Mapping[str, Any], ...]:
    raw = _value(page, "items", ())
    if not raw:
        raw = tuple({"offer_id": offer_id} for offer_id in _value(page, "offer_ids", ()))
    values: list[Mapping[str, Any]] = []
    for item in raw:
        if isinstance(item, Mapping):
            data = dict(item)
        elif hasattr(item, "model_dump"):
            data = item.model_dump(mode="python")
        else:
            continue
        data["offer_id"] = str(data.get("offer_id") or data.get("id") or data.get("num_iid") or "")
        data["source_url"] = str(data.get("source_url") or data.get("detail_url") or data.get("url") or "")
        values.append(data)
    return tuple(values)


def _page_has_next(page: Any, *, response: Mapping[str, Any], current_page: int) -> bool:
    explicit = _value(page, "has_next", None)
    if isinstance(explicit, bool):
        return explicit
    total_pages = _value(page, "total_pages", None)
    if isinstance(total_pages, int) and not isinstance(total_pages, bool):
        return current_page < min(max(0, total_pages), 100)
    container = response.get("items")
    if not isinstance(container, Mapping):
        data = response.get("data")
        container = data if isinstance(data, Mapping) else response
    total = _non_negative_int(container.get("total_results"))
    size = _non_negative_int(container.get("page_size"))
    if total and size:
        pages = min(100, (total + size - 1) // size)
        return current_page < pages
    # Without trustworthy pagination metadata, an empty page is terminal.
    return bool(_page_values(page))


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _value(value: Any, name: str, default: Any) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _result_ok(result: Any) -> bool:
    return bool(getattr(result, "ok", False))


def _result_response(result: Any) -> Mapping[str, Any]:
    value = getattr(result, "response", {})
    return value if isinstance(value, Mapping) else {}


def _result_error_code(result: Any) -> str:
    error = getattr(result, "error", None)
    return str(getattr(error, "code", "upstream_failed") or "upstream_failed")


def _result_upstream_code(result: Any) -> str:
    """Return the OneBound business error code preserved on the error context.

    ``provider._api_call`` records the upstream ``error_code`` (e.g. ``5000``)
    alongside the stable provider code so user-facing diagnostics can name the
    real OneBound failure instead of the generic ``upstream_failed``.
    """
    error = getattr(result, "error", None)
    context = getattr(error, "context", None)
    if not isinstance(context, Mapping):
        return ""
    value = str(context.get("upstream_code") or "").strip()
    return value if value and value not in {"0000", "0"} else ""


# 列表页的“无数据/数据不存在”类上游业务码：表示该店在万邦的数据到此为止。
_LISTING_TERMINATION_CODES = frozenset({"4010", "2000", "no_results"})
_LISTING_NO_DATA_MARKERS = ("不存在", "无数据", "没有数据", "没有找到", "未找到")


def _listing_no_data(result: Any) -> bool:
    """Return True when a listing call failed because the upstream has no more data.

    OneBound's ``item_search_shop`` / ``item_search_shop_pro`` only cover the
    first pages for some shops; deeper pages return ``error_code=4010``
    (不存在相应的数据信息) or ``2000`` (无结果) instead of page data.  These are
    end-of-list signals, not batch failures: the batch should finish listing
    with what was already discovered and continue to enrichment.
    """
    if _result_ok(result):
        return False
    error = getattr(result, "error", None)
    provider_code = str(getattr(error, "code", "") or "").casefold()
    if provider_code in _LISTING_TERMINATION_CODES:
        return True
    upstream = _result_upstream_code(result)
    if upstream.casefold() in _LISTING_TERMINATION_CODES:
        return True
    context = getattr(error, "context", None)
    if isinstance(context, Mapping):
        reason = str(context.get("upstream_reason") or "").strip()
        if reason and any(marker in reason for marker in _LISTING_NO_DATA_MARKERS):
            return True
    message = str(getattr(error, "message", "") or "")
    return any(marker in message for marker in _LISTING_NO_DATA_MARKERS)


def _result_error_message(result: Any) -> str:
    error = getattr(result, "error", None)
    message = str(getattr(error, "message", "OneBound request failed") or "OneBound request failed")
    upstream = _result_upstream_code(result)
    if upstream:
        return f"{message} (upstream error_code={upstream})"
    return message


def _shop_listing_error_message(result: Any) -> str:
    """Turn an upstream listing failure into an actionable Chinese message."""
    normalized_code = _result_error_code(result)
    response = _result_response(result)
    upstream_code = str(response.get("error_code") or "").strip()
    raw_error = str(response.get("error") or response.get("reason") or "")
    error = getattr(result, "error", None)
    if not upstream_code:
        # 错误上下文兜底（错误响应可能不在 response 里，而在 error.context）
        upstream_code = _result_upstream_code(result)
    if not raw_error:
        context = getattr(error, "context", None)
        if isinstance(context, Mapping):
            raw_error = str(context.get("upstream_reason") or "")
    if upstream_code == "4005" or "无权访问" in raw_error or "请开通接口" in raw_error:
        return (
            "淘宝店铺商品列表接口（taobao.item_search_shop）未开通"
            f"（error_code={upstream_code or normalized_code}，无权访问/请开通接口）。"
            "请联系万邦客服（错误信息附带的 QQ/微信）开通该接口后重试"
        )
    detail = f"（error_code={upstream_code}）" if upstream_code else ""
    suffix = str(raw_error)[:120] or "请稍后重试"
    return f"店铺商品列表获取失败{detail}：{suffix}"


def _seller_info(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    item = payload.get("item")
    if not isinstance(item, Mapping):
        data = payload.get("data")
        item = data if isinstance(data, Mapping) else payload
    seller = item.get("seller_info") if isinstance(item, Mapping) else None
    return seller if isinstance(seller, Mapping) else {}


def _taobao_shop_identity(payload: Mapping[str, Any]) -> Mapping[str, str]:
    """Extract shop_id / seller_id / shop_name from a Taobao item_get response.

    Fields move around depending on the product: item-level ``shop_id``/
    ``seller_id``, camel-case variants, or only ``seller_info`` (which carries
    ``shop_id`` and the shop home URL ``zhuy`` like ``https://shop{id}.taobao.com/``).
    OneBound may wrap the detail as ``{"item": {...}}`` or ``{"items": {"item": ...}}``.
    """
    item: Any = None
    for candidate in (payload.get("item"), payload.get("data")):
        if isinstance(candidate, Mapping):
            item = candidate
            break
    else:
        items = payload.get("items")
        nested = items.get("item") if isinstance(items, Mapping) else None
        item = nested if isinstance(nested, Mapping) else None
    if not isinstance(item, Mapping):
        return {}
    seller = item.get("seller_info") if isinstance(item.get("seller_info"), Mapping) else {}

    def first(*values: object) -> str:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                return str(int(value))
        return ""

    shop_id = first(
        item.get("shop_id"), item.get("shopId"), seller.get("shop_id"), seller.get("shopId")
    )
    if not shop_id:
        shop_id = _shop_id_from_shop_url(seller.get("zhuy") or seller.get("url"))
    seller_id = first(
        item.get("seller_id"), item.get("sellerId"), seller.get("seller_id"), seller.get("sellerId")
    )
    shop_name = first(seller.get("shop_name"), seller.get("nick"), item.get("nick"))
    return {"shop_id": shop_id, "seller_id": seller_id, "shop_name": shop_name}


def _shop_id_from_shop_url(value: object) -> str:
    """Extract the numeric shop id from a Taobao shop home URL if one is present."""
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        from .shop_parsing import extract_taobao_shop_id

        return extract_taobao_shop_id(value)
    except ValueError:
        return ""


def _taobao_identity_failure_message(payload: Mapping[str, Any]) -> str:
    """Self-diagnostic message embedding what the response actually contained."""
    try:
        top_keys = ", ".join(str(key) for key in list(payload.keys())[:8])
        item = payload.get("item")
        if not isinstance(item, Mapping):
            items = payload.get("items")
            item = items.get("item") if isinstance(items, Mapping) else None
        item_keys = ", ".join(str(key) for key in list(item.keys())[:10]) if isinstance(item, Mapping) else "无"
        code = str(payload.get("error_code") or "")
        reason = str(payload.get("reason") or "")
        error = str(payload.get("error") or "")
        return (
            "该商品详情未返回店铺信息（shop_id）。返回字段："
            f"顶层[{top_keys}] item[{item_keys}] error={error or '无'} code={code} reason={reason or '无'}；"
            "请换一个店内商品链接重试"
        )
    except Exception:
        return "该商品详情未返回店铺信息（shop_id），请换一个店内商品链接重试"


def _seller_info_identity(payload: Mapping[str, Any]) -> Mapping[str, str]:
    """Extract seller_id / nick from a Taobao ``seller_info`` response."""
    source = payload.get("items")
    if not isinstance(source, Mapping):
        data = payload.get("data")
        source = data if isinstance(data, Mapping) else payload
    if not isinstance(source, Mapping):
        return {}

    def first(*names: str) -> str:
        for name in names:
            value = source.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    return {
        "seller_id": first("seller_id"),
        "shop_id": first("shop_id"),
        "nick": first("nick", "shop_name"),
    }


def _error_code(error: Exception) -> str:
    return str(getattr(error, "code", "worker_failed") or "worker_failed")[:80]


def _error_message(error: Exception) -> str:
    text = str(error or "shop collection failed")
    lowered = text.casefold()
    if any(marker in lowered for marker in ("api_key", "api_secret", "secret=", "token=", "authorization")):
        return "shop collection request failed"
    return text[:500]
