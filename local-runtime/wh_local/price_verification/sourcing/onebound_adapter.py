"""Workspace-isolated, read-only bridge to an injected OneBound provider.

This module deliberately depends on the existing data-collection provider only
through the factory supplied by its caller.  It owns neither credentials nor a
network transport, and persists only redacted provider evidence.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import re
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from ..contracts import PriceVerificationActor, redact_sensitive
from ..repository import PriceVerificationRepository
from .contracts import SourceSearchTask


_PROVIDER_NAME = "onebound-1688"
_PROVIDER_FINGERPRINT = hashlib.sha256(_PROVIDER_NAME.encode("utf-8")).hexdigest()
_MAX_CALLS_PER_TASK = 6  # provider image-search sequence plus one detail lookup
_DEFAULT_CALL_LIMIT = 60
_OFFER_ID = re.compile(r"(?:offer/|offerId=|offer_id=)(\d{3,})", flags=re.IGNORECASE)
_SHANGHAI = ZoneInfo("Asia/Shanghai")


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
        *,
        call_limit: int = _DEFAULT_CALL_LIMIT,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(repository, PriceVerificationRepository):
            raise TypeError("repository must be PriceVerificationRepository")
        if not callable(provider_factory):
            raise TypeError("provider_factory must be callable")
        if isinstance(call_limit, bool) or not isinstance(call_limit, int) or call_limit < _MAX_CALLS_PER_TASK:
            raise ValueError(f"call_limit must be an integer of at least {_MAX_CALLS_PER_TASK}")
        self._repository = repository
        self._provider_factory = provider_factory
        self._call_limit = call_limit
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def search_by_image(
        self, actor: PriceVerificationActor, tasks: Sequence[SourceSearchTask]
    ) -> dict[str, Any]:
        """Run each task independently so one provider failure remains retriable.

        The reservation is intentionally per task.  Slots reserved for the
        provider's worst allowed sequence are released after its redacted audit
        records reveal how many upstream calls actually occurred.
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
        except Exception:
            return _result_for_items([_failed_item(task, "provider request failed") for task in task_list])
        items: list[dict[str, Any]] = []
        for task in task_list:
            reservation_date = self._reserve(actor.workspace_id, _MAX_CALLS_PER_TASK)
            if reservation_date is None:
                items.append(_failed_item(task, "daily provider budget is exhausted"))
                continue
            item, audit_count = self._search_task(provider, task)
            self._settle(actor.workspace_id, reservation_date, _MAX_CALLS_PER_TASK, audit_count)
            items.append(item)
        return _result_for_items(items)

    def _search_task(self, provider: _OneBoundProvider, task: SourceSearchTask) -> tuple[dict[str, Any], int]:
        evidence: list[dict[str, Any]] = []
        try:
            searched = provider.search_by_image(_ImageSearchCriteria(task.main_image_url))
            evidence.extend(_redacted_audits(searched))
            if not _result_ok(searched):
                return _failed_item(task, "provider request failed", evidence), len(evidence)

            raw_candidates = _search_items(_response(searched))
            candidates: list[dict[str, Any]] = []
            # A detail request closes the evidence of the best provider result;
            # one per task keeps the six-slot budget reservation enforceable.
            for raw_candidate in raw_candidates[:1]:
                offer_id = _offer_id(raw_candidate)
                detailed = dict(raw_candidate)
                if offer_id:
                    detail = provider.get_item_detail(offer_id)
                    evidence.extend(_redacted_audits(detail))
                    if not _result_ok(detail):
                        return _failed_item(task, "provider request failed", evidence), len(evidence)
                    detailed = {**raw_candidate, **_detail_item(_response(detail))}
                candidates.append(_safe_candidate(detailed, evidence))
            return {
                "task_key": task.task_key,
                "skc_id": task.skc_id,
                "source_quote_keys": list(task.source_quote_keys),
                "status": "succeeded",
                "error": "",
                "candidates": candidates,
                "evidence": evidence,
            }, len(evidence)
        except Exception:
            # Provider exceptions are intentionally opaque: they can contain
            # credentials or request URLs and must never cross this boundary.
            return _failed_item(task, "provider request failed", evidence), len(evidence)

    def _reserve(self, workspace_id: str, calls: int) -> str | None:
        now = _shanghai_date(self._clock())
        with self._repository._connect() as connection:  # adapter owns only its module table rows
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT call_limit, used_count FROM price_verification_provider_budgets
                WHERE workspace_id = ? AND credential_fingerprint = ? AND shanghai_date = ?""",
                (workspace_id, _PROVIDER_FINGERPRINT, now),
            ).fetchone()
            limit, used = (self._call_limit, 0) if row is None else (min(int(row["call_limit"]), self._call_limit), int(row["used_count"]))
            granted = used + calls <= limit
            if row is None:
                connection.execute(
                    """INSERT INTO price_verification_provider_budgets
                    (workspace_id, credential_fingerprint, shanghai_date, call_limit, used_count, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (workspace_id, _PROVIDER_FINGERPRINT, now, limit, used + calls if granted else used, _timestamp()),
                )
            elif granted:
                connection.execute(
                    """UPDATE price_verification_provider_budgets
                    SET call_limit = ?, used_count = ?, updated_at = ?
                    WHERE workspace_id = ? AND credential_fingerprint = ? AND shanghai_date = ?""",
                    (limit, used + calls, _timestamp(), workspace_id, _PROVIDER_FINGERPRINT, now),
                )
            connection.commit()
        return now if granted else None

    def _settle(
        self, workspace_id: str, reservation_date: str, reserved_calls: int, actual_calls: int
    ) -> None:
        unused = max(reserved_calls - actual_calls, 0)
        if not unused:
            return
        with self._repository._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT used_count FROM price_verification_provider_budgets
                WHERE workspace_id = ? AND credential_fingerprint = ? AND shanghai_date = ?""",
                (workspace_id, _PROVIDER_FINGERPRINT, reservation_date),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise RuntimeError("provider budget reservation disappeared")
            connection.execute(
                """UPDATE price_verification_provider_budgets
                SET used_count = ?, updated_at = ?
                WHERE workspace_id = ? AND credential_fingerprint = ? AND shanghai_date = ?""",
                (
                    max(int(row["used_count"]) - unused, 0),
                    _timestamp(),
                    workspace_id,
                    _PROVIDER_FINGERPRINT,
                    reservation_date,
                ),
            )
            connection.commit()


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
        values = container.get("items") or container.get("item_list")
        if isinstance(values, list):
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


def _safe_candidate(candidate: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return provider fields only after recursive credential redaction."""
    return redact_sensitive({**dict(candidate), "provider_evidence": list(evidence)})


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _optional_text(value: object) -> str | None:
    text = _text(value)
    return text or None


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _shanghai_date(value: datetime) -> str:
    instant = value if value.tzinfo is not None else value.replace(tzinfo=_SHANGHAI)
    return instant.astimezone(_SHANGHAI).date().isoformat()
