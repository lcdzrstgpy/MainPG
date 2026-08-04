"""Persistence-facing, read-only browser sourcing workflow."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from ..contracts import PluginCommandRequest, PriceVerificationActor, PriceVerificationContractError
from ..quote_normalizer import QuoteItem
from ..repository import PluginCommandRecord, PriceVerificationNotFound, PriceVerificationRepository, SourcingRunRecord
from ..plugin.service import PluginBridgeService
from .normalizer import normalize_source_candidates
from .ranking import rank_source_candidates
from .task_builder import build_source_browser_image_search_payload


class SourcingService:
    """Queue and materialize only read-only source browser discovery results."""

    def __init__(self, *, repository: PriceVerificationRepository, plugin_bridge: PluginBridgeService) -> None:
        if not isinstance(repository, PriceVerificationRepository):
            raise TypeError("repository must be PriceVerificationRepository")
        if not isinstance(plugin_bridge, PluginBridgeService):
            raise TypeError("plugin_bridge must be PluginBridgeService")
        self._repository = repository
        self._plugin_bridge = plugin_bridge

    def queue_browser_search(
        self,
        actor: PriceVerificationActor,
        *,
        session_id: str,
        quote_run_id: str,
        idempotency_key: str,
        max_quotes: int = 50,
    ) -> PluginCommandRecord:
        """Queue one bounded image search command for complete saved quotes."""
        actor = _actor(actor)
        _owned_session(self._plugin_bridge, actor, session_id)
        run = self._repository.get_quote_run(workspace_id=actor.workspace_id, run_id=quote_run_id)
        browser_payload = build_source_browser_image_search_payload(run.items, max_quotes=max_quotes)
        payload = {"quote_run_id": run.run_id, "source_mode": "browser_image_search", **browser_payload.to_payload()}
        return self._repository.create_command(
            workspace_id=actor.workspace_id,
            session_id=session_id,
            request=PluginCommandRequest(
                command_type="source_browser_image_search", payload=payload, idempotency_key=idempotency_key
            ),
        )

    def materialize_browser_result(
        self,
        actor: PriceVerificationActor,
        command: PluginCommandRecord,
        *,
        quote_run_id: str | None = None,
    ) -> SourcingRunRecord:
        """Persist completed candidates together with each task's terminal state.

        An item marker deliberately persists failures and empty results, so a
        later retry can target only unfinished quotes without discarding a
        concurrent successful candidate.
        """
        actor = _actor(actor)
        if not isinstance(command, PluginCommandRecord):
            raise TypeError("command must be PluginCommandRecord")
        persisted = self._repository.get_command(workspace_id=actor.workspace_id, command_id=command.command_id)
        if persisted.command_type != "source_browser_image_search":
            raise PriceVerificationContractError("command must be a source browser image search")
        if persisted.status != "succeeded":
            raise ValueError("source command must have succeeded before materialization")
        saved_run_id = persisted.payload.get("quote_run_id")
        resolved_quote_run_id = quote_run_id or (saved_run_id if isinstance(saved_run_id, str) else "")
        if not resolved_quote_run_id:
            raise PriceVerificationContractError("quote_run_id is required")
        quotes = self._repository.get_quote_run(workspace_id=actor.workspace_id, run_id=resolved_quote_run_id).items
        preview = build_source_preview(quotes, persisted.result)
        snapshots: list[dict[str, Any]] = []
        for item in preview["items"]:
            quote_key = str(item["quote_key"])
            snapshots.append({
                "record_type": "source_item", "quote_key": quote_key,
                "candidate_key": f"__source_item__:{quote_key}",
                "status": item["source_search_status"], "error": item.get("source_search_error", ""),
            })
            for candidate in _all_item_candidates(item):
                snapshots.append({"record_type": "candidate", **candidate})
        return self._repository.create_sourcing_run(
            workspace_id=actor.workspace_id,
            quote_run_id=resolved_quote_run_id,
            candidates=snapshots,
            source_mode="browser_image_search",
            status="partial" if preview["counts"]["failed_quotes"] else "succeeded",
            task_count=len(preview["items"]),
        )

    def preview(self, actor: PriceVerificationActor, sourcing_run_id: str) -> dict[str, Any]:
        """Recreate a source preview solely from workspace-owned snapshots."""
        actor = _actor(actor)
        run = self._repository.get_sourcing_run(workspace_id=actor.workspace_id, run_id=sourcing_run_id)
        quotes = self._repository.get_quote_run(workspace_id=actor.workspace_id, run_id=run.quote_run_id).items
        source_items: dict[str, dict[str, Any]] = {}
        for snapshot in run.candidates:
            quote_key = _text(snapshot.get("quote_key"))
            if not quote_key:
                continue
            item = source_items.setdefault(quote_key, {"quote_key": quote_key, "status": "succeeded", "candidates": []})
            if snapshot.get("record_type") == "source_item":
                item["status"] = _text(snapshot.get("status")) or "succeeded"
                item["error"] = _text(snapshot.get("error"))
            elif snapshot.get("record_type") == "candidate":
                item["candidates"].append(snapshot)
        return build_source_preview(quotes, {"items": list(source_items.values())})

    def retry_failed_items(
        self,
        actor: PriceVerificationActor,
        *,
        sourcing_run_id: str,
        session_id: str,
        idempotency_key: str,
        max_quotes: int = 50,
    ) -> PluginCommandRecord:
        """Queue only failed source tasks; recommendations and reviews remain saved."""
        actor = _actor(actor)
        _owned_session(self._plugin_bridge, actor, session_id)
        run = self._repository.get_sourcing_run(workspace_id=actor.workspace_id, run_id=sourcing_run_id)
        current = self.preview(actor, sourcing_run_id)
        retry_keys = set(current["retry_quote_keys"])
        if not retry_keys:
            raise ValueError("no failed source items to retry")
        quote_run = self._repository.get_quote_run(workspace_id=actor.workspace_id, run_id=run.quote_run_id)
        retry_quotes = [quote for quote in quote_run.items if _quote_key(quote) in retry_keys]
        browser_payload = build_source_browser_image_search_payload(retry_quotes, max_quotes=max_quotes)
        payload = {
            "quote_run_id": quote_run.run_id,
            "retry_of_sourcing_run_id": run.run_id,
            "source_mode": "browser_image_search",
            **browser_payload.to_payload(),
        }
        return self._repository.create_command(
            workspace_id=actor.workspace_id,
            session_id=session_id,
            request=PluginCommandRequest(
                command_type="source_browser_image_search", payload=payload, idempotency_key=idempotency_key
            ),
        )


def build_source_preview(
    quotes: Sequence[QuoteItem | Mapping[str, Any]], source_result: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Merge quote evidence and an already-captured browser result without I/O."""
    result_by_key = _result_items_by_quote_key(source_result)
    items: list[dict[str, Any]] = []
    review_candidates: list[dict[str, Any]] = []
    sku_targets: list[dict[str, Any]] = []
    for quote in quotes:
        quote_key = _quote_key(quote)
        result = result_by_key.get(quote_key) or result_by_key.get(_quote_skc(quote))
        status = _text(result.get("status")) if result else ("pending" if source_result is None else "failed")
        status = status or "succeeded"
        raw_candidates = result.get("candidates", []) if result else []
        normalized = normalize_source_candidates(quote, raw_candidates, quote_key=quote_key)
        normalized = list(rank_source_candidates(normalized))
        recommended = [candidate for candidate in normalized if candidate["source_decision"] == "recommended"]
        review = [candidate for candidate in normalized if candidate["source_decision"] == "review"]
        validation = [candidate for candidate in normalized if candidate["source_decision"] == "sku_validation"]
        item_decision, item_status = _item_decision(status, normalized, recommended, review, validation)
        item = {
            "quote_key": quote_key,
            "skc_id": _quote_skc(quote),
            "sku_id": _quote_sku(quote),
            "product_title": _quote_value(quote, "product_title"),
            "source_search_status": item_status,
            "source_search_error": _text(result.get("error")) if result else "",
            "source_decision": item_decision,
            "candidates": recommended,
            "source_review_candidates": review,
            "source_sku_validation_targets": [_sku_validation_target(quote, candidate) for candidate in validation],
            "all_candidates": normalized,
        }
        items.append(item)
        review_candidates.extend(review)
        sku_targets.extend(item["source_sku_validation_targets"])
    counts = _counts(items)
    return {
        "items": items,
        "counts": counts,
        "employee_action_summary": _employee_action_summary(counts),
        "source_review_candidates": review_candidates,
        "source_sku_validation_targets": sku_targets,
        "retry_quote_keys": [item["quote_key"] for item in items if item["source_decision"] == "failed"],
    }


def _item_decision(status: str, normalized: Sequence[Mapping[str, Any]], recommended: Sequence[Mapping[str, Any]], review: Sequence[Mapping[str, Any]], validation: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    if recommended:
        return "recommended", "succeeded" if status == "succeeded" else "succeeded_partial"
    if validation:
        return "sku_validation", "needs_sku_validation"
    if review:
        return "review", "needs_review"
    if normalized:
        return "no_reliable_source", "no_reliable_source"
    if status in {"failed", "error", "cancelled", "timeout"}:
        return "failed", "failed"
    if status in {"pending", "queued", "running", "leased"}:
        return "pending", status
    return "no_results", "no_results"


def _result_items_by_quote_key(source_result: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    output: dict[str, Mapping[str, Any]] = {}
    if not isinstance(source_result, Mapping):
        return output
    entries = source_result.get("items")
    if not isinstance(entries, list):
        return output
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        keys = [_text(entry.get("quote_key")), _text(entry.get("task_key")), _text(entry.get("skc_id"))]
        source_keys = entry.get("source_quote_keys")
        if isinstance(source_keys, list):
            keys.extend(_text(key) for key in source_keys)
        for key in keys:
            if key and key not in output:
                output[key] = entry
    return output


def _counts(items: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    decisions = Counter(_text(item.get("source_decision")) for item in items)
    candidates = sum(len(item.get("candidates", [])) for item in items)
    return {
        "quotes": len(items), "processed_quotes": len(items) - decisions["pending"],
        "recommended_quotes": decisions["recommended"], "candidate_count": candidates,
        "review_source_quotes": decisions["review"], "sku_validation_quotes": decisions["sku_validation"],
        "no_reliable_source_quotes": decisions["no_reliable_source"], "no_result_quotes": decisions["no_results"],
        "failed_quotes": decisions["failed"], "pending_quotes": decisions["pending"],
    }


def _employee_action_summary(counts: Mapping[str, int]) -> dict[str, Any]:
    if counts["recommended_quotes"]:
        action = "confirm_recommended_sources"
    elif counts["sku_validation_quotes"]:
        action = "validate_sku_details"
    elif counts["review_source_quotes"]:
        action = "review_source_candidates"
    elif counts["failed_quotes"]:
        action = "retry_failed_items"
    elif counts["no_reliable_source_quotes"] or counts["no_result_quotes"]:
        action = "manual_source_search"
    else:
        action = "wait_for_source_search"
    return {"next_action": action, "actionable_quotes": counts["recommended_quotes"] + counts["sku_validation_quotes"] + counts["review_source_quotes"]}


def _sku_validation_target(quote: QuoteItem | Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {"quote_key": _quote_key(quote), "skc_id": _quote_skc(quote), "sku_id": _quote_sku(quote), "offer_id": candidate.get("offer_id", ""), "source_url": candidate.get("source_url", ""), "source_title": candidate.get("source_title", ""), "validation_reason": candidate.get("source_decision_reason", "")}


def _all_item_candidates(item: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    values = item.get("all_candidates")
    return values if isinstance(values, list) else []


def _actor(value: PriceVerificationActor) -> PriceVerificationActor:
    if not isinstance(value, PriceVerificationActor):
        raise TypeError("actor must be PriceVerificationActor")
    return value


def _owned_session(bridge: PluginBridgeService, actor: PriceVerificationActor, session_id: str) -> None:
    if not isinstance(session_id, str) or not session_id.strip():
        raise PriceVerificationContractError("session_id is required")
    if session_id not in {session.session_id for session in bridge.list_sessions(actor)}:
        raise PriceVerificationNotFound("resource not found")


def _quote_key(quote: QuoteItem | Mapping[str, Any]) -> str:
    if isinstance(quote, Mapping) and _text(quote.get("quote_key")):
        return _text(quote.get("quote_key"))
    skc, sku = _quote_skc(quote), _quote_sku(quote)
    return f"{skc}:{sku}" if skc and sku else skc or sku


def _quote_skc(quote: QuoteItem | Mapping[str, Any]) -> str:
    return _quote_value(quote, "skc_id")


def _quote_sku(quote: QuoteItem | Mapping[str, Any]) -> str:
    return _quote_value(quote, "sku_id")


def _quote_value(quote: QuoteItem | Mapping[str, Any], name: str) -> str:
    return _text(getattr(quote, name, "") if isinstance(quote, QuoteItem) else quote.get(name))


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
