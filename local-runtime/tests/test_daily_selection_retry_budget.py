"""Regression coverage for provider retries during daily selection."""

from __future__ import annotations

import pytest

from wh_local.data_collection.budget import UnlimitedApiBudget
from wh_local.data_collection.collector import DailySelectionCollector
from wh_local.data_collection.contracts import ApiEvidence
from wh_local.data_collection.criteria import DailySelectionCriteria
from wh_local.data_collection.provider import ProviderCallResult


class RetryAuditProvider:
    credential_fingerprint = "0" * 64

    def search_keyword(self, criteria: DailySelectionCriteria) -> ProviderCallResult:
        return ProviderCallResult(
            response={"items": []},
            audits=(
                ApiEvidence(provider="fake", operation="item_search", request_id="attempt-1"),
                ApiEvidence(provider="fake", operation="item_search", request_id="attempt-2"),
            ),
        )

    def search_by_image(self, criteria: DailySelectionCriteria) -> ProviderCallResult:
        raise AssertionError("image search is not used by this test")

    def get_item_detail(self, offer_id: str) -> ProviderCallResult:
        raise AssertionError("an empty search has no detail calls")


@pytest.mark.parametrize("max_parallel_collect", [1, 8])
def test_retry_audits_do_not_fail_collection(max_parallel_collect: int) -> None:
    collector = DailySelectionCollector(
        workspace_id="workspace-1",
        provider=RetryAuditProvider(),
        budget=UnlimitedApiBudget(),
    )
    criteria = DailySelectionCriteria(
        keywords=("收纳盒",),
        selection_scope="exact",
        target_count=1,
        detail_count=1,
        max_parallel_collect=max_parallel_collect,
    )

    result = collector.collect(criteria)

    assert result.status == "empty"
    assert result.api_calls == 2
    assert len(result.query_attempts) == 1
    assert len(result.query_attempts[0].audits) == 2
