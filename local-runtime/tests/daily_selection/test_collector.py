from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.modules.daily_selection.budget import SQLiteDailyApiBudget, credential_fingerprint  # noqa: E402
from wh_local.modules.daily_selection.collector import DailySelectionCollector  # noqa: E402
from wh_local.modules.daily_selection.contracts import ApiEvidence, DailySelectionError  # noqa: E402
from wh_local.modules.daily_selection.criteria import DailySelectionCriteria  # noqa: E402
from wh_local.modules.daily_selection.provider import ProviderCallResult  # noqa: E402


def audit(operation: str) -> ApiEvidence:
    return ApiEvidence(provider="fake-1688", operation=operation)


def result(items: list[Mapping[str, Any]] | None = None, *, error: DailySelectionError | None = None, audits: tuple[ApiEvidence, ...] | None = None) -> ProviderCallResult:
    records = audits or (audit("item_search"),)
    return ProviderCallResult(response={"data": {"items": list(items or [])}}, audits=records, error=error)


def item(offer_id: str, title: str | None = None) -> dict[str, str]:
    return {
        "num_iid": offer_id,
        "title": title or f"商品 {offer_id}",
        "detail_url": f"https://detail.1688.com/{offer_id}.html",
        "pic_url": f"https://images.example.test/{offer_id}.jpg",
    }


class FakeProvider:
    """A deterministic provider fake; no method performs network I/O."""

    credential_fingerprint = credential_fingerprint({"api_key": "fake-key", "secret": "fake-secret"})

    def __init__(
        self,
        *,
        keyword_results: list[ProviderCallResult] | None = None,
        image_result: ProviderCallResult | None = None,
        detail_results: Mapping[str, ProviderCallResult] | None = None,
    ) -> None:
        self.keyword_results = list(keyword_results or [])
        self.image_result = image_result or result([])
        self.detail_results = dict(detail_results or {})
        self.calls: list[tuple[str, str]] = []

    def search_keyword(self, criteria: DailySelectionCriteria) -> ProviderCallResult:
        self.calls.append(("search", " ".join(criteria.keywords)))
        return self.keyword_results.pop(0)

    def search_by_image(self, criteria: DailySelectionCriteria) -> ProviderCallResult:
        self.calls.extend((("upload", criteria.reference_image_url or ""), ("image_search", "uploaded-image")))
        return self.image_result

    def get_item_detail(self, offer_id: str) -> ProviderCallResult:
        self.calls.append(("detail", offer_id))
        return self.detail_results.get(offer_id, result([]))


def collector(tmp_path: Path, provider: FakeProvider) -> DailySelectionCollector:
    return DailySelectionCollector(
        workspace_id="workspace-a",
        provider=provider,
        budget=SQLiteDailyApiBudget(tmp_path / "budget.sqlite3"),
    )


def test_exact_keyword_collection_uses_only_user_keywords_and_audits_each_query(tmp_path: Path) -> None:
    provider = FakeProvider(keyword_results=[result([item("one")])], detail_results={"one": result([])})

    collected = collector(tmp_path, provider).collect(
        DailySelectionCriteria(keywords=["露营灯"], selection_scope="exact", detail_count=1)
    )

    assert provider.calls == [("search", "露营灯"), ("detail", "one")]
    assert [attempt.query for attempt in collected.query_attempts] == ["露营灯"]
    assert all(not attempt.expanded for attempt in collected.query_attempts)
    assert collected.status == "completed"
    assert collected.search_calls == 1
    assert collected.image_search_calls == 0
    assert collected.detail_calls == 1
    assert collected.api_calls == 2


def test_divergent_keyword_collection_uses_versioned_local_extensions_and_audits_them(tmp_path: Path) -> None:
    provider = FakeProvider(keyword_results=[result([item("one")]), result([item("two")])])

    collected = collector(tmp_path, provider).collect(
        DailySelectionCriteria(keywords=["露营灯"], selection_scope="divergent", detail_count=1)
    )

    assert provider.calls[0] == ("search", "露营灯")
    assert [call for call in provider.calls if call[0] == "search"] == [
        ("search", "露营灯"),
        ("search", "便携露营灯"),
    ]
    assert collected.expansion_rule_version == "local-v1"
    assert [attempt.query for attempt in collected.query_attempts][0] == "露营灯"
    assert any(attempt.expanded for attempt in collected.query_attempts)
    assert all(attempt.expansion_rule_version == "local-v1" for attempt in collected.query_attempts)


def test_image_collection_uploads_before_image_search_and_keeps_reference_url_on_candidates(tmp_path: Path) -> None:
    image_audits = (audit("download_reference_image"), audit("upload_img"), audit("item_search_img"))
    provider = FakeProvider(image_result=result([item("image-one", "极简露营灯")], audits=image_audits))
    reference = "https://images.example.test/reference.jpg"

    collected = collector(tmp_path, provider).collect(
        DailySelectionCriteria(
            collection_mode="image",
            reference_image_url=reference,
            keywords=["露营风"],
            selection_scope="divergent",
            max_api_calls=3,
            detail_count=1,
        )
    )

    assert provider.calls[:2] == [("upload", reference), ("image_search", "uploaded-image")]
    assert collected.candidates[0].reference_image_url == reference
    assert collected.image_search_calls == 1
    assert collected.search_calls == 0
    assert collected.api_calls == 3
    assert collected.derived_image_terms == ("极简露营灯",)


def test_empty_search_returns_empty_with_one_counted_search(tmp_path: Path) -> None:
    provider = FakeProvider(keyword_results=[result([])])

    collected = collector(tmp_path, provider).collect(DailySelectionCriteria(keywords=["不存在"], detail_count=1))

    assert collected.status == "empty"
    assert collected.candidates == ()
    assert collected.errors == ()
    assert collected.search_calls == 1
    assert collected.detail_calls == 0
    assert collected.api_calls == 1


def test_partial_provider_failures_keep_successful_candidates_and_errors(tmp_path: Path) -> None:
    failure = DailySelectionError(code="upstream_failed", message="fake upstream failure")
    provider = FakeProvider(keyword_results=[result([item("one")]), result(error=failure)])

    collected = collector(tmp_path, provider).collect(
        DailySelectionCriteria(keywords=["露营灯"], selection_scope="divergent", detail_count=1)
    )

    assert collected.status == "partial"
    assert [candidate.offer_id for candidate in collected.candidates] == ["one"]
    assert collected.errors == (failure,)
    assert collected.search_calls == 2


def test_details_are_limited_to_deduplicated_top_candidates_and_failure_is_retained(tmp_path: Path) -> None:
    detail_failure = DailySelectionError(code="upstream_failed", message="detail unavailable")
    provider = FakeProvider(
        keyword_results=[result([item("one"), item("one"), item("two"), item("three")])],
        detail_results={
            "one": ProviderCallResult(response={"data": {"title": "详情一"}}, audits=(audit("item_get"),)),
            "two": result(error=detail_failure, audits=(audit("item_get"),)),
        },
    )

    collected = collector(tmp_path, provider).collect(DailySelectionCriteria(keywords=["露营灯"], detail_count=2))

    assert [call for call in provider.calls if call[0] == "detail"] == [("detail", "one"), ("detail", "two")]
    assert [candidate.offer_id for candidate in collected.candidates] == ["one", "two", "three"]
    assert collected.detail_errors == {"two": detail_failure}
    assert collected.status == "partial"
    assert collected.detail_calls == 2


def test_budget_exhaustion_before_image_operation_returns_failed_without_provider_call(tmp_path: Path) -> None:
    provider = FakeProvider(image_result=result([item("image-one")], audits=(audit("download_reference_image"), audit("upload_img"), audit("item_search_img"))))

    collected = collector(tmp_path, provider).collect(
        DailySelectionCriteria(
            collection_mode="image",
            reference_image_url="https://images.example.test/reference.jpg",
            max_api_calls=2,
            detail_count=1,
        )
    )

    assert collected.status == "failed"
    assert collected.errors[0].code == "budget_exhausted"
    assert provider.calls == []
    assert collected.api_calls == 0
