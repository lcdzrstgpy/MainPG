"""Persistence-facing, read-only browser sourcing workflow."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from ..contracts import PluginCommandRequest, PriceVerificationActor, PriceVerificationContractError
from ..quote_normalizer import QuoteItem
from ..repository import (
    BatchSelectionRecord,
    PluginCommandRecord,
    PriceVerificationNotFound,
    PriceVerificationRepository,
    QuoteRunRecord,
    SkcSourceLinkRecord,
    SourcingRunRecord,
)
from ..plugin.service import PluginBridgeService
from ..plugin.shared_gateway import SharedPluginGateway
from .normalizer import canonical_source_url, normalize_source_candidates
from .profit_ranking import DEFAULT_CANDIDATE_LIMIT, DEFAULT_WEIGHT_KG, build_candidate_profit
from .ranking import rank_candidates_by_mode, rank_source_candidates
from .contracts import SourceBrowserImageSearchPayload, SourceSearchTask
from .task_builder import (
    build_batch_sourcing_payload,
    build_retained_source_browser_image_search_payload,
)


class QuoteDecisionRequiredError(ValueError):
    """Raised when sourcing is requested before a human has reviewed quotes."""


class NoRetainedQuotesError(ValueError):
    """Raised when the current human decisions retain no official links."""


class IncompleteRetainedQuotesError(ValueError):
    """Raised when retained links lack a URL, image, or selected price."""


class SourcingService:
    """Queue and materialize only read-only source browser discovery results."""

    def __init__(
        self,
        *,
        repository: PriceVerificationRepository,
        plugin_gateway: SharedPluginGateway | None = None,
        plugin_bridge: PluginBridgeService | None = None,
    ) -> None:
        if not isinstance(repository, PriceVerificationRepository):
            raise TypeError("repository must be PriceVerificationRepository")
        if plugin_gateway is None and not isinstance(plugin_bridge, PluginBridgeService):
            raise TypeError("plugin_gateway or plugin_bridge is required")
        if plugin_gateway is not None and not isinstance(plugin_gateway, SharedPluginGateway):
            raise TypeError("plugin_gateway must be SharedPluginGateway")
        self._repository = repository
        self._plugin_gateway = plugin_gateway
        self._plugin_bridge = plugin_bridge

    def search_batch_selections_by_image(
        self,
        actor: PriceVerificationActor,
        *,
        batch_id: str,
        provider_factory: Callable[[], Any],
        ranking_mode: str = "similarity",
        skc_ids: Sequence[str] | None = None,
        keyword_search: bool = False,
    ) -> dict[str, Any]:
        """Run the established OB 1688 image-search chain for retained SKCs.

        Each retained selection becomes one task whose main image is first
        downloaded and uploaded through the already-verified data-collection
        provider (upload_img), then matched via item_search_img.  Results are
        returned as a source preview grouped by SKC, honoring each selection's
        candidate cap, ranked by the requested mode, and topped with a profit
        preview for the best candidate against the Temu adjusted price.

        ``skc_ids`` restricts the search to the user-selected SKCs; when it is
        ``None`` every retained selection is searched (backward compatible).
        ``keyword_search`` optionally adds the translated-title keyword channel;
        it is off by default so the first run is pure image search.
        """
        from .onebound_adapter import OneBoundSourceAdapter

        actor = _actor(actor)
        selections = tuple(
            item
            for item in self._repository.list_batch_selections(
                workspace_id=actor.workspace_id, batch_id=batch_id
            )
            if item.status == "retained"
        )
        if skc_ids is not None:
            selected = {_text(skc) for skc in skc_ids if _text(skc)}
            selections = tuple(item for item in selections if item.skc_id in selected)
        if not selections:
            raise NoRetainedQuotesError("no retained SKC selections are available for sourcing")
        payload = build_batch_sourcing_payload(
            (_selection_sourcing_view(item) for item in selections)
        )
        tasks = payload.tasks
        if not tasks:
            raise NoRetainedQuotesError("no retained SKC selections are available for sourcing")
        adapter = OneBoundSourceAdapter(self._repository, provider_factory)
        result = adapter.search_by_image(actor, tasks, keyword_search=keyword_search)
        quotes = [task.to_payload() for task in tasks]
        preview = build_source_preview(quotes, result)
        return _apply_batch_ranking(
            preview,
            selections_by_skc={item.skc_id: item for item in selections},
            ranking_mode=ranking_mode,
        )

    def preview_candidate_profit(
        self,
        *,
        site: str,
        selling_price: object,
        price: object,
        moq: object = None,
        domestic_freight: object = None,
        weight_kg: object = DEFAULT_WEIGHT_KG,
    ) -> Mapping[str, Any]:
        """Recompute the profit preview for one candidate with adjustable weight."""
        site_code = _site_code(site)
        if not site_code:
            raise PriceVerificationContractError("site must be US, CO or EC")
        return build_candidate_profit(
            {
                "price": price,
                "promotion_price": None,
                "moq": moq,
                "domestic_freight": domestic_freight,
            },
            site=site_code,
            selling_price=selling_price,
            weight_kg=weight_kg,
        )

    def link_skc_source(
        self,
        actor: PriceVerificationActor,
        *,
        batch_id: str,
        skc_id: str,
        offer_id: str,
        source_url: str,
        source_title: str = "",
        main_image_url: str = "",
        price_cny: object = None,
        moq: object = None,
        domestic_freight_cny: object = None,
        source_decision: str = "",
        note: str = "",
    ) -> Mapping[str, Any]:
        """Link one 1688 offer to a retained Temu SKC (idempotent, one SKC to many offers).

        Only SKCs that were retained in the final review (and therefore written
        to the draft pool) may be linked; this keeps the dropshipping record
        closed: retain -> image-search -> link.
        """
        actor = _actor(actor)
        batch_id = _required_text(batch_id, "batch_id")
        skc_id = _required_text(skc_id, "skc_id")
        offer_id = _required_text(offer_id, "offer_id")
        if not re.fullmatch(r"\d{3,}", offer_id):
            raise PriceVerificationContractError("offer_id must be a 1688 offer id")
        source_url = canonical_source_url(source_url, offer_id=offer_id)
        if not source_url:
            raise PriceVerificationContractError("source_url must be a valid 1688 offer URL")
        selection = self._repository.get_batch_selection_by_skc(
            workspace_id=actor.workspace_id, batch_id=batch_id, skc_id=skc_id
        )
        if selection.status != "retained":
            raise PriceVerificationContractError(
                "only retained SKC selections can link 1688 sources"
            )
        record = self._repository.upsert_skc_source_link(
            workspace_id=actor.workspace_id,
            batch_id=batch_id,
            skc_id=skc_id,
            offer_id=offer_id,
            source_url=source_url,
            source_title=_text(source_title),
            main_image_url=_text(main_image_url),
            price_cny=_nullable_decimal_text(price_cny),
            moq=_nullable_decimal_text(moq),
            domestic_freight_cny=_nullable_decimal_text(domestic_freight_cny),
            source_decision=_text(source_decision),
            note=_text(note),
            now=_now_text(),
        )
        return _source_link_response(record, selection=selection)

    def list_skc_source_links(
        self,
        actor: PriceVerificationActor,
        *,
        batch_id: str,
        skc_id: str | None = None,
    ) -> tuple[Mapping[str, Any], ...]:
        actor = _actor(actor)
        links = self._repository.list_skc_source_links(
            workspace_id=actor.workspace_id,
            batch_id=batch_id,
            skc_id=_text(skc_id) or None,
        )
        if not links:
            return ()
        selections = {
            item.skc_id: item
            for item in self._repository.list_batch_selections(
                workspace_id=actor.workspace_id, batch_id=batch_id
            )
        }
        return tuple(
            _source_link_response(record, selection=selections.get(record.skc_id))
            for record in links
        )

    def remove_skc_source_link(
        self, actor: PriceVerificationActor, *, link_id: int
    ) -> Mapping[str, Any]:
        actor = _actor(actor)
        record = self._repository.soft_remove_skc_source_link(
            workspace_id=actor.workspace_id, link_id=int(link_id), now=_now_text()
        )
        return _source_link_response(record)

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
        run, browser_payload = self._retained_browser_payload(
            actor, quote_run_id=quote_run_id, max_quotes=max_quotes
        )
        frozen = [task.to_payload() for task in browser_payload.tasks]
        payload = {
            "quote_run_id": run.run_id,
            "source_mode": "browser_image_search",
            "source_quotes": frozen,
            **browser_payload.to_payload(),
        }
        if self._plugin_gateway is not None:
            return self._plugin_gateway.queue_command(
                actor,
                session_id=session_id,
                command_type="source_browser_image_search",
                payload=payload,
                idempotency_key=idempotency_key,
            )
        assert self._plugin_bridge is not None
        _owned_session(self._plugin_bridge, actor, session_id)
        return self._repository.create_command(
            workspace_id=actor.workspace_id,
            session_id=session_id,
            request=PluginCommandRequest(
                command_type="source_browser_image_search", payload=payload, idempotency_key=idempotency_key
            ),
        )

    def queue_batch_sourcing(
        self,
        actor: PriceVerificationActor,
        *,
        session_id: str,
        batch_id: str,
        idempotency_key: str,
    ) -> PluginCommandRecord:
        """Queue one bounded image-search command for retained SKC selections.

        Each retained SKC becomes one search task carrying its requested
        candidate cap.  The payload is tagged with a batch-scoped identifier
        because the second panel drives sourcing without a quote-run snapshot.
        """
        actor = _actor(actor)
        selections = tuple(
            item
            for item in self._repository.list_batch_selections(
                workspace_id=actor.workspace_id, batch_id=batch_id
            )
            if item.status == "retained"
        )
        if not selections:
            raise NoRetainedQuotesError("no retained SKC selections are available for sourcing")
        browser_payload = build_batch_sourcing_payload(
            (_selection_sourcing_view(item) for item in selections)
        )
        frozen = [task.to_payload() for task in browser_payload.tasks]
        payload = {
            "quote_run_id": f"batch-sourcing:{batch_id}",
            "source_mode": "browser_image_search",
            "source_quotes": frozen,
            **browser_payload.to_payload(),
        }
        if self._plugin_gateway is not None:
            return self._plugin_gateway.queue_command(
                actor,
                session_id=session_id,
                command_type="source_browser_image_search",
                payload=payload,
                idempotency_key=idempotency_key,
            )
        assert self._plugin_bridge is not None
        _owned_session(self._plugin_bridge, actor, session_id)
        return self._repository.create_command(
            workspace_id=actor.workspace_id,
            session_id=session_id,
            request=PluginCommandRequest(
                command_type="source_browser_image_search", payload=payload, idempotency_key=idempotency_key
            ),
        )

    def retained_search_tasks(
        self,
        actor: PriceVerificationActor,
        *,
        quote_run_id: str,
        max_quotes: int = 50,
    ) -> tuple[SourceSearchTask, ...]:
        """Return validated one-link-per-task inputs for the direct provider adapter."""
        actor = _actor(actor)
        _, browser_payload = self._retained_browser_payload(
            actor, quote_run_id=quote_run_id, max_quotes=max_quotes
        )
        return browser_payload.tasks

    def _retained_browser_payload(
        self,
        actor: PriceVerificationActor,
        *,
        quote_run_id: str,
        max_quotes: int,
    ) -> tuple[QuoteRunRecord, SourceBrowserImageSearchPayload]:
        run = self._repository.get_quote_run(workspace_id=actor.workspace_id, run_id=quote_run_id)
        decisions = self._repository.list_current_quote_decisions(
            workspace_id=actor.workspace_id, quote_run_id=quote_run_id
        )
        if not decisions:
            raise QuoteDecisionRequiredError("quote decisions are required before sourcing")
        retained_keys = {item.quote_key for item in decisions if item.decision == "retained"}
        if not retained_keys:
            raise NoRetainedQuotesError("no retained quotes are available for sourcing")
        retained = [_frozen_source_quote(item) for item in run.items if _quote_key(item) in retained_keys]
        incomplete = [item["quote_key"] for item in retained if not _complete_frozen_quote(item)]
        if incomplete:
            raise IncompleteRetainedQuotesError(
                "retained quotes are incomplete: " + ", ".join(incomplete)
            )
        browser_payload = build_retained_source_browser_image_search_payload(
            retained, max_quotes=max_quotes
        )
        return run, browser_payload

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
        persisted = (
            self._plugin_gateway.get_command(actor, command.command_id)
            if self._plugin_gateway is not None
            else self._repository.get_command(
                workspace_id=actor.workspace_id, command_id=command.command_id
            )
        )
        if persisted.command_type != "source_browser_image_search":
            raise PriceVerificationContractError("command must be a source browser image search")
        if persisted.status != "succeeded":
            raise ValueError("source command must have succeeded before materialization")
        saved_run_id = persisted.payload.get("quote_run_id")
        resolved_quote_run_id = quote_run_id or (saved_run_id if isinstance(saved_run_id, str) else "")
        if not resolved_quote_run_id:
            raise PriceVerificationContractError("quote_run_id is required")
        frozen_quotes = persisted.payload.get("source_quotes")
        if not isinstance(frozen_quotes, list) or not all(
            isinstance(item, Mapping) for item in frozen_quotes
        ):
            frozen_quotes = list(
                self._repository.get_quote_run(
                    workspace_id=actor.workspace_id, run_id=resolved_quote_run_id
                ).items
            )
        quotes = tuple(frozen_quotes)
        source_result: Mapping[str, Any] = persisted.result
        parent_run_id = _text(persisted.payload.get("retry_of_sourcing_run_id"))
        if parent_run_id:
            parent_run = self._repository.get_sourcing_run(
                workspace_id=actor.workspace_id, run_id=parent_run_id
            )
            if parent_run.quote_run_id != resolved_quote_run_id:
                raise PriceVerificationContractError("retry source run must use the same quote run")
            source_result = _merge_retry_source_result(self.preview(actor, parent_run_id), persisted.result)
        preview = build_source_preview(quotes, source_result)
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
            source_quotes=tuple(frozen_quotes),
        )

    def preview(self, actor: PriceVerificationActor, sourcing_run_id: str) -> dict[str, Any]:
        """Recreate a source preview solely from workspace-owned snapshots."""
        actor = _actor(actor)
        run = self._repository.get_sourcing_run(workspace_id=actor.workspace_id, run_id=sourcing_run_id)
        frozen_quotes = self._repository.list_sourcing_run_quotes(
            workspace_id=actor.workspace_id, sourcing_run_id=sourcing_run_id
        )
        quotes = (
            tuple(item.snapshot for item in frozen_quotes)
            if frozen_quotes
            else self._repository.get_quote_run(
                workspace_id=actor.workspace_id, run_id=run.quote_run_id
            ).items
        )
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
        run = self._repository.get_sourcing_run(workspace_id=actor.workspace_id, run_id=sourcing_run_id)
        current = self.preview(actor, sourcing_run_id)
        retry_keys = set(current["retry_quote_keys"])
        if not retry_keys:
            raise ValueError("no failed source items to retry")
        frozen_quotes = self._repository.list_sourcing_run_quotes(
            workspace_id=actor.workspace_id, sourcing_run_id=sourcing_run_id
        )
        retry_quotes = [
            dict(item.snapshot) for item in frozen_quotes if item.quote_key in retry_keys
        ]
        if not retry_quotes:
            quote_run = self._repository.get_quote_run(
                workspace_id=actor.workspace_id, run_id=run.quote_run_id
            )
            retry_quotes = [quote for quote in quote_run.items if _quote_key(quote) in retry_keys]
        browser_payload = build_retained_source_browser_image_search_payload(
            retry_quotes, max_quotes=max_quotes
        )
        source_quotes = [task.to_payload() for task in browser_payload.tasks]
        payload = {
            "quote_run_id": run.quote_run_id,
            "retry_of_sourcing_run_id": run.run_id,
            "source_mode": "browser_image_search",
            "source_quotes": source_quotes,
            **browser_payload.to_payload(),
        }
        if self._plugin_gateway is not None:
            return self._plugin_gateway.queue_command(
                actor,
                session_id=session_id,
                command_type="source_browser_image_search",
                payload=payload,
                idempotency_key=idempotency_key,
            )
        assert self._plugin_bridge is not None
        _owned_session(self._plugin_bridge, actor, session_id)
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
            "max_candidates": _quote_candidate_cap(quote),
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
        "skc_groups": _group_source_items_by_skc(items),
        "counts": counts,
        "employee_action_summary": _employee_action_summary(counts),
        "source_review_candidates": review_candidates,
        "source_sku_validation_targets": sku_targets,
        "retry_quote_keys": [item["quote_key"] for item in items if item["source_decision"] == "failed"],
    }


def _apply_batch_ranking(
    preview: dict[str, Any],
    *,
    selections_by_skc: Mapping[str, BatchSelectionRecord],
    ranking_mode: str,
) -> dict[str, Any]:
    """Reorder each SKC's candidates by the user-selected mode and attach the top profit.

    The first ranked candidate is priced against the Temu adjusted declared
    price so the employee immediately sees whether the most-similar or cheapest
    1688 match clears the profit thresholds.  The displayed list is capped at
    ``DEFAULT_CANDIDATE_LIMIT`` (3-5 links) per the sourcing convention.
    """
    mode = ranking_mode if ranking_mode in {"similarity", "price"} else "similarity"
    for item in preview.get("items", []):
        if not isinstance(item, dict):
            continue
        raw_candidates = item.get("all_candidates")
        all_candidates = (
            [candidate for candidate in raw_candidates if isinstance(candidate, Mapping)]
            if isinstance(raw_candidates, list)
            else []
        )
        ranked = rank_candidates_by_mode(all_candidates, mode=mode)
        selection = selections_by_skc.get(_text(item.get("skc_id")))
        site = _site_code(selection.site) if selection is not None else ""
        selling_price = _text(selection.adjusted_min) if selection is not None else ""
        ranked_copies = [dict(candidate) for candidate in ranked]
        for candidate in ranked_copies:
            candidate["profit"] = _candidate_profit(candidate, site, selling_price)
        item["all_candidates"] = ranked_copies
        keyword_count = sum(1 for candidate in ranked_copies if candidate.get("source_channel") == "keyword")
        item["keyword_count"] = keyword_count
        # The default display stays at 3-5 links; when the user opted into the
        # title-keyword channel, widen the list so those hits stay visible.
        display_limit = min(len(ranked_copies), DEFAULT_CANDIDATE_LIMIT + keyword_count * DEFAULT_CANDIDATE_LIMIT) if keyword_count else DEFAULT_CANDIDATE_LIMIT
        item["ranked_candidates"] = list(ranked_copies[:display_limit])
        item["candidates"] = [
            candidate
            for candidate in ranked_copies
            if candidate.get("source_decision") == "recommended"
        ]
        item["profit_context"] = {
            "site": site,
            "selling_price": selling_price,
            "weight_kg": str(DEFAULT_WEIGHT_KG),
        }
        item["top_profit"] = _top_candidate_profit(ranked_copies, selection)
    preview["ranking_mode"] = mode
    preview["candidate_limit"] = DEFAULT_CANDIDATE_LIMIT
    return preview


def _candidate_profit(
    candidate: Mapping[str, Any], site: str, selling_price: str
) -> Mapping[str, Any]:
    """Per-candidate profit preview against the Temu adjusted declared price."""
    if not site:
        return {"available": False, "reason": "missing_site"}
    if not selling_price:
        return {"available": False, "reason": "missing_selling_price"}
    return build_candidate_profit(candidate, site=site, selling_price=selling_price)


def _top_candidate_profit(
    ranked: Sequence[Mapping[str, Any]], selection: BatchSelectionRecord | None
) -> Mapping[str, Any]:
    top = ranked[0] if ranked else None
    if top is None:
        return {"available": False, "reason": "no_candidates"}
    if selection is None:
        return {"available": False, "reason": "missing_selection"}
    site = _site_code(selection.site)
    if not site:
        return {"available": False, "reason": "missing_site"}
    if not selection.adjusted_min:
        return {"available": False, "reason": "missing_selling_price"}
    return build_candidate_profit(top, site=site, selling_price=selection.adjusted_min)


def _site_code(value: object) -> str:
    """Map a stored site label (e.g. 美国站) to the profit engine's US/CO/EC code."""
    text = _text(value)
    upper = text.upper()
    if upper in {"US", "CO", "EC"}:
        return upper
    if "美国" in text:
        return "US"
    if "哥伦比亚" in text:
        return "CO"
    if "厄瓜多尔" in text:
        return "EC"
    return ""


def _group_source_items_by_skc(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Present completed sourcing evidence by SKC without coalescing search tasks."""
    grouped: dict[str, dict[str, Any]] = {}
    for item in items:
        skc_id = _text(item.get("skc_id")) or _text(item.get("quote_key"))
        group = grouped.setdefault(
            skc_id,
            {"skc_id": skc_id, "quote_keys": [], "sku_ids": [], "items": []},
        )
        quote_key = _text(item.get("quote_key"))
        sku_id = _text(item.get("sku_id"))
        if quote_key and quote_key not in group["quote_keys"]:
            group["quote_keys"].append(quote_key)
        if sku_id and sku_id not in group["sku_ids"]:
            group["sku_ids"].append(sku_id)
        group["items"].append(item)
    return list(grouped.values())


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


def _merge_retry_source_result(
    parent_preview: Mapping[str, Any], retry_result: Mapping[str, Any]
) -> dict[str, Any]:
    """Overlay returned retry items without losing terminal parent snapshots."""
    retry_by_key = _result_items_by_quote_key(retry_result)
    entries: list[dict[str, Any]] = []
    parent_items = parent_preview.get("items")
    if not isinstance(parent_items, list):
        parent_items = []
    for parent in parent_items:
        if not isinstance(parent, Mapping):
            continue
        quote_key = _text(parent.get("quote_key"))
        replacement = retry_by_key.get(quote_key) or retry_by_key.get(_text(parent.get("skc_id")))
        if replacement is not None:
            entries.append(dict(replacement))
            continue
        candidates = parent.get("all_candidates")
        entries.append({
            "quote_key": quote_key,
            "status": _text(parent.get("source_search_status")) or "succeeded",
            "error": _text(parent.get("source_search_error")),
            "candidates": list(candidates) if isinstance(candidates, list) else [],
        })
    return {"items": entries}


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


def _quote_candidate_cap(quote: QuoteItem | Mapping[str, Any]) -> int:
    raw = getattr(quote, "max_candidates", "") if isinstance(quote, QuoteItem) else quote.get("max_candidates")
    try:
        parsed = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 10
    return 10 if parsed < 1 or parsed > 100 else parsed


def _selection_sourcing_view(selection: BatchSelectionRecord) -> dict[str, Any]:
    return {
        "skc_id": selection.skc_id,
        "quote_keys": list(selection.quote_keys),
        "product_title": selection.product_title,
        "main_image_url": selection.main_image_url,
        "official_link_url": selection.official_link_url,
        "sku_prices": list(selection.sku_prices),
        "max_candidates": selection.max_candidates,
    }


def _frozen_source_quote(quote: QuoteItem | Mapping[str, Any]) -> dict[str, Any]:
    values = dict(quote) if isinstance(quote, Mapping) else {
        name: getattr(quote, name) for name in quote.__dataclass_fields__
    }
    selected_price = next(
        (
            values.get(name)
            for name in (
                "adjusted_declared_price_cny",
                "new_declared_price_cny",
                "original_declared_price_cny",
            )
            if values.get(name) not in (None, "")
        ),
        "",
    )
    return {
        **values,
        "quote_key": _quote_key(quote),
        "official_link_url": _text(values.get("official_link_url")),
        "main_image_url": _text(values.get("main_image_url")),
        "selected_price_cny": str(selected_price),
    }


def _complete_frozen_quote(quote: Mapping[str, Any]) -> bool:
    return all(
        _text(quote.get(name))
        for name in ("quote_key", "official_link_url", "main_image_url", "selected_price_cny")
    )


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _now_text() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _required_text(value: object, field_name: str) -> str:
    text = _text(value)
    if not text:
        raise PriceVerificationContractError(f"{field_name} is required")
    return text


def _nullable_decimal_text(value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number < 0:
        return None
    return str(number)


def _source_link_response(
    record: SkcSourceLinkRecord,
    *,
    selection: BatchSelectionRecord | None = None,
) -> Mapping[str, Any]:
    response: dict[str, Any] = {
        "id": record.id,
        "workspace_id": record.workspace_id,
        "batch_id": record.batch_id,
        "skc_id": record.skc_id,
        "offer_id": record.offer_id,
        "source_url": record.source_url,
        "source_title": record.source_title,
        "main_image_url": record.main_image_url,
        "price_cny": record.price_cny,
        "moq": record.moq,
        "domestic_freight_cny": record.domestic_freight_cny,
        "source_decision": record.source_decision,
        "note": record.note,
        "status": record.status,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }
    site = _site_code(selection.site) if selection is not None else ""
    selling_price = _text(selection.adjusted_min) if selection is not None else ""
    response["product_title"] = selection.product_title if selection is not None else ""
    response["site"] = site
    response["selling_price"] = selling_price
    response["profit"] = _link_profit(record, site, selling_price)
    return response


def _link_profit(
    record: SkcSourceLinkRecord, site: str, selling_price: str
) -> Mapping[str, Any]:
    """Profit for one linked 1688 source against the Temu adjusted price."""
    if not site:
        return {"available": False, "reason": "missing_site"}
    if not selling_price:
        return {"available": False, "reason": "missing_selling_price"}
    if not record.price_cny:
        return {"available": False, "reason": "missing_source_price"}
    return build_candidate_profit(
        {
            "price": record.price_cny,
            "promotion_price": None,
            "moq": record.moq,
            "domestic_freight": record.domestic_freight_cny,
        },
        site=site,
        selling_price=selling_price,
    )
