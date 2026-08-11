"""Workspace-isolated, read-only bridge to an injected OneBound provider.

This module deliberately depends on the existing data-collection provider only
through the factory supplied by its caller.  It owns neither credentials nor a
network transport, and persists only redacted provider evidence.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any, Protocol

from ..contracts import PriceVerificationActor, redact_sensitive, redact_sensitive_text
from ..repository import PriceVerificationRepository
from ...data_collection.criteria import DailySelectionCriteria
from ...data_collection.contracts import DailySelectionError
from .contracts import SourceSearchTask
from .title_translation import to_search_keywords, translate_title_to_chinese


_PROVIDER_NAME = "onebound-1688"
_OFFER_ID = re.compile(r"(?:offer/|offerId=|offer_id=)(\d{3,})", flags=re.IGNORECASE)


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

    def search_by_image(
        self, actor: PriceVerificationActor, tasks: Sequence[SourceSearchTask], *, keyword_search: bool = False
    ) -> dict[str, Any]:
        """Run each task independently so one provider failure remains retriable.

        There is no daily call budget: every task always executes against the
        provider, and provider-side failures surface per task so a single
        upstream hiccup never blocks the rest of the batch.

        ``keyword_search`` optionally adds the translated-title keyword channel;
        it is off by default so the first run is pure image search (with
        similarity scores) and the user can opt into title search afterwards.
        """
        if not isinstance(actor, PriceVerificationActor):
            raise TypeError("actor must be PriceVerificationActor")
        if isinstance(tasks, (str, bytes)):
            raise TypeError("tasks must be a sequence of SourceSearchTask")
        task_list = tuple(tasks)
        if any(not isinstance(task, SourceSearchTask) for task in task_list):
            raise TypeError("tasks must contain SourceSearchTask values")

        try:
            provider = self._provider_factory()
        except Exception as error:
            return _result_for_items(
                [_failed_item(task, _provider_error_message(error)) for task in task_list]
            )
        items: list[dict[str, Any]] = []
        for task in task_list:
            item, _ = self._search_task(provider, task, keyword_search=keyword_search)
            items.append(item)
        return _result_for_items(items)

    def _search_task(
        self, provider: _OneBoundProvider, task: SourceSearchTask, *, keyword_search: bool = False
    ) -> tuple[dict[str, Any], int]:
        evidence: list[dict[str, Any]] = []
        try:
            # Channel A: pure image search.  This is the established primary
            # channel; it carries the OB similarity score used for ranking.
            image_raw: list[Mapping[str, Any]] = []
            image_ok = False
            image_error = ""
            try:
                searched = provider.search_by_image(_ImageSearchCriteria(task.main_image_url))
                evidence.extend(_redacted_audits(searched))
                if _result_ok(searched):
                    image_ok = True
                    image_raw = _search_items(_response(searched))
                else:
                    image_error = _provider_result_error(searched)
            except Exception as error:
                image_error = _provider_error_message(error)

            # Channel B (optional): translated-title keyword search.  Only runs
            # when the user opts in, because the Temu title is translated to
            # Chinese and hits carry no similarity score.
            keyword_raw: list[Mapping[str, Any]] = []
            keyword_ok = False
            keyword_error = ""
            keywords = to_search_keywords(translate_title_to_chinese(task.product_title)) if keyword_search else ""
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

            merged = _merge_channels(image_raw, keyword_raw, max(int(task.max_candidates or 0), 1))
            if not merged:
                # A successful search with no hits is a valid empty result; only
                # fail when every channel errored out.
                if not image_ok and not keyword_ok:
                    return _failed_item(task, image_error or keyword_error or _GENERIC_PROVIDER_ERROR, evidence), len(evidence)
                return {
                    "task_key": task.task_key,
                    "skc_id": task.skc_id,
                    "source_quote_keys": list(task.source_quote_keys),
                    "status": "succeeded",
                    "error": "",
                    "candidates": [],
                    "evidence": evidence,
                }, len(evidence)

            candidates: list[dict[str, Any]] = []
            # Only the first (most relevant) candidate gets a detail lookup; the
            # rest keep the search payload the provider already returned.
            for index, (raw_candidate, channel) in enumerate(merged):
                offer_id = _offer_id(raw_candidate)
                detailed = dict(raw_candidate)
                if index == 0 and offer_id:
                    detail = provider.get_item_detail(offer_id)
                    evidence.extend(_redacted_audits(detail))
                    # A failed detail lookup must not discard the results: keep
                    # the search payload and skip the enrichment.
                    if _result_ok(detail):
                        detailed = {**raw_candidate, **_detail_item(_response(detail))}
                candidates.append(_safe_candidate(detailed, evidence, channel=channel))
            return {
                "task_key": task.task_key,
                "skc_id": task.skc_id,
                "source_quote_keys": list(task.source_quote_keys),
                "status": "succeeded",
                "error": "",
                "candidates": candidates,
                "evidence": evidence,
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
    return {
        "items": list(items),
        "counts": {
            "processed_quotes": sum(len(item["source_quote_keys"]) for item in items),
            "failed_quotes": sum(
                len(item["source_quote_keys"])
                for item in items
                if item["status"] == "failed"
            ),
            "candidate_count": sum(len(item["candidates"]) for item in items),
        },
    }


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


def _contains_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _merge_channels(
    image_raw: Sequence[Mapping[str, Any]],
    keyword_raw: Sequence[Mapping[str, Any]],
    max_candidates: int,
) -> list[tuple[Mapping[str, Any], str]]:
    """Merge image hits ahead of keyword hits, de-duplicated by offer.

    The image channel is the primary signal the user asked to keep first (it
    carries the OB similarity score); the translated-title keyword channel only
    fills in behind when the user opted into title search.  Each channel keeps
    its own cap so the keyword supplement is not silently cut off by the image
    channel filling the shared limit (e.g. five image hits + five keyword hits).
    """
    seen: set[str] = set()
    merged: list[tuple[Mapping[str, Any], str]] = []
    for raw, channel in ((image_raw, "image"), (keyword_raw, "keyword")):
        channel_count = 0
        for candidate in raw:
            offer_id = _offer_id(candidate) or _text(candidate.get("title") or candidate.get("item_title"))[:40]
            if offer_id:
                if offer_id in seen:
                    continue
                seen.add(offer_id)
            merged.append((candidate, channel))
            channel_count += 1
            if channel_count >= max_candidates:
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
