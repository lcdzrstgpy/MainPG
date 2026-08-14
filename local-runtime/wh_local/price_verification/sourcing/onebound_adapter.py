"""Workspace-isolated, read-only bridge to an injected OneBound provider.

This module deliberately depends on the existing data-collection provider only
through the factory supplied by its caller.  It owns neither credentials nor a
network transport, and persists only redacted provider evidence.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
import ctypes
from dataclasses import dataclass
import os
import re
from typing import Any, Protocol

from ..contracts import PriceVerificationActor, redact_sensitive, redact_sensitive_text
from ..repository import PriceVerificationRepository
from ...data_collection.contracts import DailySelectionError
from .contracts import SourceSearchTask
from .identity import evaluate_product_evidence
from .image_similarity import IMAGE_DISPLAY_LIMIT, IMAGE_SEARCH_RECALL_LIMIT, verify_visual_candidates
from .title_translation import to_search_keywords, translate_title_to_chinese


_PROVIDER_NAME = "onebound-1688"
_MAX_PARALLEL_SEARCHES = 4
_OFFER_ID = re.compile(r"(?:offer/|offerId=|offer_id=)(\d{3,})", flags=re.IGNORECASE)
_LOW_MEMORY_BYTES = 8 * 1024**3
_LOW_CPU_COUNT = 4
_MAX_PARALLEL_SKCS = 2
_SERIAL_FALLBACK_OUTCOMES = frozenset({"rate_limited", "timeout"})


class _ProviderResult(Protocol):
    response: Mapping[str, Any]
    audits: Sequence[object]
    error: object | None


class _OneBoundProvider(Protocol):
    def search_by_image(self, criteria: object) -> _ProviderResult: ...

    def get_item_detail(self, offer_id: str) -> _ProviderResult: ...


@dataclass(frozen=True)
class _ImageSearchCriteria:
    """The small structural request the existing provider consumes for image search."""

    reference_image_url: str
    collection_mode: str = "image"
    target_count: int = 30
    keyword_tags: tuple[str, ...] = ()


class OneBoundSourceAdapter:
    """Execute bounded source image searches without coupling to collection internals."""

    def __init__(
        self,
        repository: PriceVerificationRepository,
        provider_factory: Callable[[], _OneBoundProvider],
    ) -> None:
        if not isinstance(repository, PriceVerificationRepository):
            raise TypeError("repository must be PriceVerificationRepository")
        if not callable(provider_factory):
            raise TypeError("provider_factory must be callable")
        self._repository = repository
        self._provider_factory = provider_factory

    def search_by_image(self, actor: PriceVerificationActor, tasks: Sequence[SourceSearchTask], *, keyword_search: bool = True) -> dict[str, Any]:
        """Run each task independently so one provider failure remains retriable.

        There is no daily call budget: every task always executes against the
        provider, and provider-side failures surface per task so a single
        upstream hiccup never blocks the rest of the batch.

        ``keyword_search`` additionally searches the translated title.  The
        title request can only corroborate an offer returned by image search;
        it must never turn a text hit into a visual-match candidate.
        """
        if not isinstance(actor, PriceVerificationActor):
            raise TypeError("actor must be PriceVerificationActor")
        if isinstance(tasks, (str, bytes)):
            raise TypeError("tasks must be a sequence of SourceSearchTask")
        task_list = tuple(tasks)
        if any(not isinstance(task, SourceSearchTask) for task in task_list):
            raise TypeError("tasks must contain SourceSearchTask values")

<<<<<<< HEAD
        if not task_list:
            return _result_for_items(())

        # One provider instance is created per task: the underlying transport
        # is not required to be thread-safe. Executor.map preserves input
        # order, while each task already converts its own provider exception
        # into an isolated failed item.
        with ThreadPoolExecutor(
            max_workers=min(_MAX_PARALLEL_SEARCHES, len(task_list)),
            thread_name_prefix="onebound-skc",
        ) as executor:
            items = list(
                executor.map(
                    lambda task: self._search_task_with_provider(
                        task,
                        keyword_search=keyword_search,
                    ),
                    task_list,
                )
            )
        return _result_for_items(items)

    def _search_task_with_provider(
=======
        items: list[dict[str, Any]] = []
        parallelism = min(_recommended_skc_parallelism(), len(task_list))
        offset = 0
        while offset < len(task_list):
            batch = task_list[offset : offset + parallelism]
            if len(batch) == 1:
                batch_items = [
                    self._search_task_with_new_provider(batch[0], keyword_search=keyword_search)
                ]
            else:
                # Each SKC owns a provider instance. Sharing one transport
                # across worker threads would make request state and retries
                # unnecessarily coupled.
                with ThreadPoolExecutor(max_workers=len(batch)) as executor:
                    futures = [
                        executor.submit(
                            self._search_task_with_new_provider,
                            task,
                            keyword_search=keyword_search,
                        )
                        for task in batch
                    ]
                    # Resolve in submission order so the response remains in
                    # the same SKC order as the request.
                    batch_items = [future.result() for future in futures]
            items.extend(batch_items)
            offset += len(batch)
            if parallelism > 1 and _requires_serial_fallback(batch_items):
                parallelism = 1
        return _result_for_items(items)

    def _search_task_with_new_provider(
>>>>>>> team/codex/price-verification-sourcing-dev-20260811
        self,
        task: SourceSearchTask,
        *,
        keyword_search: bool,
    ) -> dict[str, Any]:
        try:
            provider = self._provider_factory()
        except Exception as error:
            return _failed_item(task, _provider_error_message(error))
<<<<<<< HEAD
        item, _ = self._search_task(
            provider,
            task,
            keyword_search=keyword_search,
        )
=======
        item, _ = self._search_task(provider, task, keyword_search=keyword_search)
>>>>>>> team/codex/price-verification-sourcing-dev-20260811
        return item

    def _search_task(self, provider: _OneBoundProvider, task: SourceSearchTask, *, keyword_search: bool = False) -> tuple[dict[str, Any], int]:
        evidence: list[dict[str, Any]] = []
        try:
            # Channel A: pure image search. This is the only channel allowed
            # to produce visual-match candidates.
            image_raw: list[Mapping[str, Any]] = []
            image_ok = False
            image_error = ""
            reference_content: bytes | None = None
            try:
                criteria = _ImageSearchCriteria(
                    task.main_image_url,
                    target_count=max(IMAGE_SEARCH_RECALL_LIMIT, int(task.max_candidates or 0)),
                )
                search_with_reference = getattr(provider, "search_by_image_with_reference", None)
                if callable(search_with_reference):
                    searched, reference_content = search_with_reference(criteria)
                    if reference_content is not None and not isinstance(reference_content, bytes):
                        raise TypeError("provider reference content must be bytes")
                else:
                    searched = provider.search_by_image(criteria)
                evidence.extend(_redacted_audits(searched))
                if _result_ok(searched):
                    image_ok = True
                    image_raw = _search_items(_response(searched))
                else:
                    image_error = _provider_result_error(searched)
            except Exception as error:
                image_error = _provider_error_message(error)

            # Channel B: translated-title keyword search. OneBound's image
            # endpoint does not accept title text, so this only corroborates
            # image hits and never contributes standalone candidates.
            keyword_raw: list[Mapping[str, Any]] = []
            keyword_ok = False
            keyword_error = ""
            translated_title = translate_title_to_chinese(task.product_title) if keyword_search else ""
            keywords = to_search_keywords(translated_title)
            # Only run the keyword channel when the title translated into
            # Chinese; a raw-English fallback would search 1688 with the wrong
            # language and return noise.
            if keywords and _contains_cjk(keywords):
                try:
                    keyword_hits = provider.search_keyword(
                        DailySelectionCriteria(
                            collection_mode="keyword",
                            keywords=(keywords,),
                            target_count=max(int(task.max_candidates or 0), 1),
                        )
                    )
                    evidence.extend(_redacted_audits(keyword_hits))
                    if _result_ok(keyword_hits):
                        keyword_ok = True
                        keyword_raw = _search_items(_response(keyword_hits))
                    else:
                        keyword_error = _provider_result_error(keyword_hits)
                except Exception as error:
                    keyword_error = _provider_error_message(error)

            merged = _merge_channels(
                image_raw,
                keyword_raw,
                max(IMAGE_SEARCH_RECALL_LIMIT, int(task.max_candidates or 0)),
            )
            # Never discard an image hit from English-vs-Chinese title evidence
            # before visual verification.  Marketing attributes such as
            # "cooling" are too brittle to be hard category gates and could
            # otherwise reduce a valid 60-result image recall to zero.
            merged = [
                _with_title_evidence(task, candidate)
                for candidate in merged
            ]
            if reference_content is None:
                verified, visual_audit = verify_visual_candidates(task.main_image_url, merged)
            else:
                verified, visual_audit = verify_visual_candidates(
                    task.main_image_url,
                    merged,
                    reference_content=reference_content,
                )
            visual_audit["title_evidence"] = {
                status: sum(
                    candidate.get("title_evidence_status") == status
                    for candidate in merged
                )
                for status in ("compatible", "missing", "conflict")
            }
            selected_keys = {_offer_id(candidate) or _candidate_image_url(candidate) for candidate in verified}
            if len(verified) < IMAGE_DISPLAY_LIMIT:
                for candidate in merged:
                    key = _offer_id(candidate) or _candidate_image_url(candidate)
                    if not key or key in selected_keys:
                        continue
                    fallback = dict(candidate)
                    fallback["image_similarity_score"] = None
                    fallback["image_similarity_method"] = "onebound-order-fallback"
                    fallback["image_similarity_verified"] = False
                    fallback["image_similarity_fallback"] = True
                    fallback["image_similarity_selected"] = True
                    fallback["image_similarity_fallback_reason"] = "image_unavailable_or_category_fallback"
                    verified.append(fallback)
                    selected_keys.add(key)
                    visual_audit["fallback_count"] = int(visual_audit.get("fallback_count") or 0) + 1
                    if len(verified) >= IMAGE_DISPLAY_LIMIT:
                        break
            verified.sort(
                key=lambda candidate: (
                    bool(candidate.get("image_similarity_fallback")),
                    -float(candidate.get("image_similarity_score") or 0),
                    _title_evidence_rank(candidate.get("title_evidence_status")),
                    not bool(candidate.get("title_search_confirmed")),
                    int(candidate.get("image_search_rank") or 10**9),
                )
            )
            merged = verified[:max(int(task.max_candidates or 0), 1)]
            if not merged:
                # A keyword hit is not evidence of a visual match. If image
                # search failed, make the SKC retriable instead of showing text
                # matches as candidates.
                if not image_ok:
                    return _failed_item(task, image_error or _GENERIC_PROVIDER_ERROR, evidence), len(evidence)
                return {
                    "task_key": task.task_key,
                    "skc_id": task.skc_id,
                    "source_quote_keys": list(task.source_quote_keys),
                    "status": "succeeded",
                    "error": "",
                    "candidates": [],
                    "evidence": evidence,
                    "visual_verification": visual_audit,
                }, len(evidence)

            candidates: list[dict[str, Any]] = []
            # Only the first (most relevant) candidate gets a detail lookup; the
            # rest keep the search payload the provider already returned.
            for index, raw_candidate in enumerate(merged):
                offer_id = _offer_id(raw_candidate)
                detailed = dict(raw_candidate)
                if index == 0 and offer_id and _needs_detail_lookup(raw_candidate):
                    detail = provider.get_item_detail(offer_id)
                    evidence.extend(_redacted_audits(detail))
                    # A failed detail lookup must not discard the results: keep
                    # the search payload and skip the enrichment.
                    if _result_ok(detail):
                        detailed = {**raw_candidate, **_detail_item(_response(detail))}
                        # Keep the exact thumbnail that passed local visual
                        # verification paired with this offer/link.  Detail
                        # payloads may carry a different gallery image.
                        verified_image_url = _candidate_image_url(raw_candidate)
                        if verified_image_url:
                            detailed["main_image_url"] = verified_image_url
                candidates.append(_safe_candidate(detailed, evidence, channel="image"))
            return {
                "task_key": task.task_key,
                "skc_id": task.skc_id,
                "source_quote_keys": list(task.source_quote_keys),
                "status": "succeeded",
                "error": "",
                "candidates": candidates,
                "evidence": evidence,
                "visual_verification": visual_audit,
            }, len(evidence)
        except Exception as error:
            # Provider exceptions are intentionally opaque: they can contain
            # credentials or request URLs.  Only the normalized safe diagnostic
            # is allowed to cross this boundary.
            return _failed_item(task, _provider_error_message(error), evidence), len(evidence)


def _failed_item(
    task: SourceSearchTask, error: str, evidence: Sequence[Mapping[str, Any]] = ()
) -> dict[str, Any]:
    return {
        "task_key": task.task_key,
        "skc_id": task.skc_id,
        "source_quote_keys": list(task.source_quote_keys),
        "status": "failed",
        "error": error,
        "candidates": [],
        "evidence": list(evidence),
    }


def _result_for_items(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    failed = [item for item in items if item["status"] == "failed"]
    if items and len(failed) == len(items):
        status = "failed"
    elif failed:
        status = "partial"
    else:
        status = "succeeded"
    return {
        "status": status,
        "all_failed": bool(items) and len(failed) == len(items),
        "failed_skc_ids": [item["skc_id"] for item in failed],
        "items": list(items),
        "counts": {
            "total_skc": len(items),
            "completed_skc": len(items),
            "succeeded_skc": len(items) - len(failed),
            "failed_skc": len(failed),
            "processed_quotes": sum(len(item["source_quote_keys"]) for item in items),
            "failed_quotes": sum(
                len(item["source_quote_keys"])
                for item in items
                if item["status"] == "failed"
            ),
            "candidate_count": sum(len(item["candidates"]) for item in items),
        },
    }


def _recommended_skc_parallelism() -> int:
    """Use two SKC workers only when both CPU and memory are sufficient."""
    cpu_count = os.cpu_count() or 1
    total_memory = _total_physical_memory_bytes()
    if cpu_count <= _LOW_CPU_COUNT or total_memory is None or total_memory < _LOW_MEMORY_BYTES:
        return 1
    return _MAX_PARALLEL_SKCS


def _total_physical_memory_bytes() -> int | None:
    try:
        if os.name == "nt":
            class _MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            state = _MemoryStatusEx()
            state.dwLength = ctypes.sizeof(state)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):  # type: ignore[attr-defined]
                return int(state.ullTotalPhys)
            return None
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        page_count = int(os.sysconf("SC_PHYS_PAGES"))
        return page_size * page_count if page_size > 0 and page_count > 0 else None
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _requires_serial_fallback(items: Sequence[Mapping[str, Any]]) -> bool:
    for item in items:
        evidence = item.get("evidence")
        if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)):
            continue
        for audit in evidence:
            if not isinstance(audit, Mapping):
                continue
            summary = audit.get("response_summary")
            if isinstance(summary, Mapping) and _text(summary.get("outcome")).casefold() in _SERIAL_FALLBACK_OUTCOMES:
                return True
    return False


def _result_ok(result: object) -> bool:
    return result is not None and getattr(result, "error", None) is None


_GENERIC_PROVIDER_ERROR = "OneBound 图搜请求失败，请稍后重试"


def _provider_result_error(result: object) -> str:
    """Return a safe diagnostic from a provider result without exposing secrets."""
    error = getattr(result, "error", None)
    return _provider_error_message(error) if error is not None else _GENERIC_PROVIDER_ERROR


def _provider_error_message(error: object) -> str:
    """Normalize expected local/OB failures into actionable, non-sensitive text."""
    if isinstance(error, DailySelectionError):
        return _message_for_provider_error(error.code, error.message)

    # The provider configuration resolver raises FastAPI's HTTPException.  Its
    # detail is deliberately built from CollectCredentialsError and therefore is
    # the only exception text safe enough to inspect here.
    detail = getattr(error, "detail", None)
    if isinstance(detail, str) and detail.strip():
        return _message_for_provider_error("credential", detail)

    return _GENERIC_PROVIDER_ERROR


def _message_for_provider_error(code: object, message: object) -> str:
    normalized_code = _text(code).casefold()
    safe_message = redact_sensitive_text(_text(message))[:300]
    normalized_message = safe_message.casefold()

    if "not registered" in normalized_message:
        return "当前账号未开通 OneBound 图搜权限，请在服务端注册账号后重试"
    if "credentials are not configured" in normalized_message:
        return "服务端未配置 OneBound 图搜凭据"
    if "cannot reach collect-key" in normalized_message:
        return "无法连接图搜凭据服务，请检查本机网络或服务地址"
    if "collect credential service" in normalized_message:
        return "图搜凭据服务配置异常"
    if normalized_code == "authentication_failed":
        return "OneBound 图搜鉴权失败，请检查服务端凭据配置"
    if normalized_code == "rate_limited":
        return "OneBound 图搜请求过于频繁，请稍后重试"
    if normalized_code == "quota_exhausted":
        return "OneBound 图搜额度不足，请检查服务端账户余额"
    if normalized_code == "invalid_request":
        return safe_message or "商品主图无法用于图搜"
    if normalized_code == "upstream_failed":
        return safe_message or "OneBound 图搜服务暂不可用，请稍后重试"
    return safe_message or _GENERIC_PROVIDER_ERROR


def _response(result: object) -> Mapping[str, Any]:
    value = getattr(result, "response", {})
    return value if isinstance(value, Mapping) else {}


def _redacted_audits(result: object) -> list[dict[str, Any]]:
    values = getattr(result, "audits", ())
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    return [_redacted_audit(audit) for audit in values]


def _redacted_audit(audit: object) -> dict[str, Any]:
    if isinstance(audit, Mapping):
        value = audit
        get = value.get
    else:
        get = lambda name, default=None: getattr(audit, name, default)
    return redact_sensitive({
        "provider": _text(get("provider")) or _PROVIDER_NAME,
        "operation": _text(get("operation")) or "unknown",
        "request_id": _optional_text(get("request_id")),
        "captured_at": _optional_text(get("captured_at")),
        "request_summary": get("request_summary") if isinstance(get("request_summary"), Mapping) else {},
        "response_summary": get("response_summary") if isinstance(get("response_summary"), Mapping) else {},
    })


def _search_items(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    for container in (payload, payload.get("data"), payload.get("result")):
        if not isinstance(container, Mapping):
            continue
        values = container.get("items") or container.get("item_list") or container.get("item")
        # OB item_search_img nests the array under {"items": {"item": [...]}} and
        # the provider parses the JSON array into a tuple.
        if isinstance(values, Mapping):
            values = values.get("item") or values.get("items") or values.get("item_list")
        if isinstance(values, (list, tuple)):
            return [value for value in values if isinstance(value, Mapping)]
    return []


def _detail_item(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    for value in (payload.get("item"), payload.get("data"), payload.get("result")):
        if isinstance(value, Mapping):
            return value
    return {}


def _offer_id(candidate: Mapping[str, Any]) -> str:
    for key in ("offer_id", "offerId", "num_iid", "item_id", "id"):
        value = _text(candidate.get(key))
        if value:
            return value
    for key in ("detail_url", "source_url", "url", "item_url"):
        match = _OFFER_ID.search(_text(candidate.get(key)))
        if match:
            return match.group(1)
    return ""


def _candidate_image_url(candidate: Mapping[str, Any]) -> str:
    for key in ("main_image_url", "image", "image_url", "pic_url", "pic", "picUrl"):
        value = _text(candidate.get(key))
        if value:
            return value
    return ""


def _needs_detail_lookup(candidate: Mapping[str, Any]) -> bool:
    """Avoid a redundant OneBound round-trip when search data is display-ready."""
    has_title = any(_text(candidate.get(key)) for key in ("title", "item_title", "subject", "name"))
    has_image = bool(_candidate_image_url(candidate))
    has_url = any(_text(candidate.get(key)) for key in ("detail_url", "source_url", "url", "item_url"))
    has_price = any(
        candidate.get(key) not in (None, "")
        for key in ("price", "promotion_price", "price_info", "original_price")
    )
    return not (has_title and has_image and has_url and has_price)


def _with_title_evidence(
    task: SourceSearchTask, candidate: Mapping[str, Any]
) -> dict[str, Any]:
    """Attach non-blocking title evidence for audit and tie-breaking only."""
    status, reasons = evaluate_product_evidence(
        {"product_title": task.product_title, "main_image_url": task.main_image_url},
        candidate,
    )
    return {
        **dict(candidate),
        "title_evidence_status": status,
        "title_evidence_reasons": list(reasons),
    }


def _title_evidence_rank(value: object) -> int:
    return {"compatible": 0, "missing": 1, "conflict": 2}.get(_text(value), 1)


def _contains_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _merge_channels(
    image_raw: Sequence[Mapping[str, Any]],
    keyword_raw: Sequence[Mapping[str, Any]],
    max_candidates: int,
) -> list[Mapping[str, Any]]:
    """Return only image-search offers, de-duplicated by offer ID.

    A title hit says nothing about whether the product looks the same. When both
    channels return one offer we keep the image hit only.
    """
    keyword_offer_ids = {
        _offer_id(candidate)
        for candidate in keyword_raw
        if _offer_id(candidate)
    }
    seen: set[str] = set()
    merged: list[Mapping[str, Any]] = []
    for index, candidate in enumerate(image_raw, start=1):
        offer_id = _offer_id(candidate) or _text(candidate.get("title") or candidate.get("item_title"))[:40]
        if offer_id:
            if offer_id in seen:
                continue
            seen.add(offer_id)
        item = dict(candidate)
        # This is the only rank we can safely preserve: the order returned by
        # OneBound's image endpoint. Do not interpret ``turn_head`` as an image
        # similarity score; the provider does not document it as one.
        item["image_search_rank"] = index
        merged.append(item)
        if len(merged) >= max_candidates:
            break
    return merged


def _safe_candidate(
    candidate: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]],
    *,
    channel: str = "",
) -> dict[str, Any]:
    """Return provider fields only after recursive credential redaction."""
    return redact_sensitive({
        **dict(candidate),
        "source_channel": channel or _text(candidate.get("source_channel")),
        "provider_evidence": list(evidence),
    })


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _optional_text(value: object) -> str | None:
    text = _text(value)
    return text or None
