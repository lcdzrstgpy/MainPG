from __future__ import annotations

import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.modules.daily_selection.criteria import (  # noqa: E402
    DailySelectionCriteria,
    DailySelectionCriteriaError,
)


def test_keyword_request_normalizes_one_to_five_keywords() -> None:
    criteria = DailySelectionCriteria(keywords=["  露营灯  ", "折叠桌", "露营灯"])

    assert criteria.collection_mode == "keyword"
    assert criteria.keywords == ("露营灯", "折叠桌")


def test_keyword_request_requires_at_least_one_keyword() -> None:
    with pytest.raises(DailySelectionCriteriaError, match="keyword"):
        DailySelectionCriteria(keywords=[])


def test_image_request_requires_reference_image_url() -> None:
    with pytest.raises(DailySelectionCriteriaError, match="reference_image_url"):
        DailySelectionCriteria(collection_mode="image", keywords=["露营风"])


def test_image_request_treats_keywords_as_tags() -> None:
    criteria = DailySelectionCriteria(
        collection_mode="image",
        reference_image_url="https://images.example.com/tent.jpg",
        keywords=["  露营风  ", "极简"],
    )

    assert criteria.keywords == ("露营风", "极简")
    assert criteria.keyword_tags == ("露营风", "极简")


def test_image_request_rejects_invalid_reference_image_url() -> None:
    with pytest.raises(DailySelectionCriteriaError, match="valid http"):
        DailySelectionCriteria(collection_mode="image", reference_image_url="file:///tmp/tent.jpg")


def test_keyword_request_rejects_reference_image_url() -> None:
    with pytest.raises(DailySelectionCriteriaError, match="keyword mode"):
        DailySelectionCriteria(
            keywords=["帐篷"],
            reference_image_url="https://images.example.com/tent.jpg",
        )


def test_price_range_requires_minimum_not_greater_than_maximum() -> None:
    with pytest.raises(DailySelectionCriteriaError, match="min_price"):
        DailySelectionCriteria(keywords=["帐篷"], min_price=200, max_price=100)


@pytest.mark.parametrize("budget", [0, 61])
def test_api_budget_must_be_between_one_and_sixty(budget: int) -> None:
    with pytest.raises(DailySelectionCriteriaError, match="max_api_calls"):
        DailySelectionCriteria(keywords=["帐篷"], max_api_calls=budget)


def test_selection_scope_accepts_only_exact_or_divergent() -> None:
    with pytest.raises(DailySelectionCriteriaError, match="selection_scope"):
        DailySelectionCriteria(keywords=["帐篷"], selection_scope="similar")


def test_exclude_risks_requires_strings() -> None:
    with pytest.raises(DailySelectionCriteriaError, match="exclude_risks"):
        DailySelectionCriteria(keywords=["帐篷"], exclude_risks=["侵权", 1])
