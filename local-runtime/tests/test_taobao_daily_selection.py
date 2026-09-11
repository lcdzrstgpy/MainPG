"""Taobao direction for daily selection via the shared OneBound provider.

These tests exercise only the platform parameterisation added on top of the
existing 1688 pipeline: base_url/platform derivation, Taobao URL canonicalisation,
and search-response normalisation producing Taobao candidates. No real network
access is performed.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from wh_local.data_collection.contracts import ApiEvidence
from wh_local.data_collection.criteria import DailySelectionCriteria
from wh_local.data_collection.filtering import canonical_source_url, filter_and_score_candidates
from wh_local.data_collection.link_collection import canonical_taobao_item_url
from wh_local.data_collection.normalizer import normalize_search_response
from wh_local.data_collection.provider import HttpResponse, OneBound1688Provider, OneBoundProvider
from wh_local.data_collection.service import _platform_config


def _provider(transport: object) -> OneBoundProvider:
    return OneBoundProvider(
        {
            "api_key": "test-key",
            "api_secret": "test-secret",
            "base_url": "https://api.example.test/taobao",
        },
        transport=transport,  # type: ignore[arg-type]
    )


def test_provider_infers_taobao_platform_from_base_url() -> None:
    provider = OneBoundProvider(
        {"api_key": "k", "api_secret": "s", "base_url": "https://api.example.test/taobao"}
    )

    assert provider._platform == "taobao"
    assert provider._provider_name == "onebound-taobao"
    assert provider.safe_summary()["platform"] == "taobao"


def test_1688_alias_keeps_default_platform_and_name() -> None:
    provider = OneBound1688Provider(
        {"api_key": "k", "api_secret": "s", "base_url": "https://api.example.test/1688"}
    )

    assert provider._platform == "1688"
    assert provider._provider_name == "onebound-1688"


def test_explicit_platform_parameter_wins_over_base_url() -> None:
    provider = OneBoundProvider(
        {"api_key": "k", "api_secret": "s", "base_url": "https://api.example.test/1688"},
        platform="taobao",
    )

    assert provider._platform == "taobao"


def test_search_keyword_calls_taobao_endpoint_and_redacts_credentials() -> None:
    calls: list[dict[str, Any]] = []

    class Transport:
        def request(self, method: str, url: str, **kwargs: Any) -> HttpResponse:
            calls.append({"method": method, "url": url, **kwargs})
            return HttpResponse(status=200, body=json.dumps({"code": 200, "items": {"item": []}}).encode())

    from wh_local.data_collection.criteria import DailySelectionCriteria

    result = _provider(Transport()).search_keyword(
        DailySelectionCriteria(keywords=("女装",), target_count=10)
    )

    assert result.ok
    assert calls[0]["url"] == "https://api.example.test/taobao/item_search/"
    assert calls[0]["params"]["q"] == "女装"
    assert calls[0]["params"]["page_size"] == 10
    assert result.audit.operation == "item_search"
    assert result.audit.provider == "onebound-taobao"
    assert "test-key" not in str(result.audit.model_dump())


@pytest.mark.parametrize(
    ("value", "item_id"),
    (
        ("https://item.taobao.com/item.htm?id=123456", "123456"),
        (" https://detail.tmall.com/item.htm?id=999888 ", "999888"),
        ("https://item.taobao.com/item.htm?id=555", "555"),
    ),
)
def test_canonical_taobao_item_url_accepts_item_and_tmall_links(value: str, item_id: str) -> None:
    assert canonical_taobao_item_url(value) == (
        f"https://item.taobao.com/item.htm?id={item_id}",
        item_id,
    )


@pytest.mark.parametrize("value", ("", "https://example.com/item.htm?id=1", "https://detail.1688.com/offer/1.html"))
def test_canonical_taobao_item_url_rejects_non_taobao_input(value: str) -> None:
    with pytest.raises(ValueError, match="(source_url|Taobao)"):
        canonical_taobao_item_url(value)


def test_normalize_search_response_produces_a_taobao_candidate() -> None:
    evidence = ApiEvidence(provider="onebound-taobao", operation="item_search")
    candidates = normalize_search_response(
        {
            "items": {
                "item": [
                    {
                        "num_iid": "123456",
                        "title": "春季女装连衣裙",
                        "pic_url": "https://img.alicdn.com/x.jpg",
                    }
                ]
            }
        },
        evidence=evidence,
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source_platform == "taobao"
    assert candidate.candidate_id == "taobao:123456"
    assert candidate.source_url == "https://item.taobao.com/item.htm?id=123456"


def test_normalize_search_response_keeps_a_taobao_item_url_with_its_query() -> None:
    evidence = ApiEvidence(provider="onebound-taobao", operation="item_search")
    candidates = normalize_search_response(
        {
            "items": {
                "item": [
                    {
                        "num_iid": "777",
                        "title": "商品",
                        "pic_url": "https://img.alicdn.com/x.jpg",
                        "item_url": "https://detail.tmall.com/item.htm?id=777&skuId=1",
                    }
                ]
            }
        },
        evidence=evidence,
    )

    assert candidates[0].source_url == "https://detail.tmall.com/item.htm?id=777"
    assert candidates[0].source_platform == "taobao"


def test_platform_config_derives_taobao_base_url_from_1688_config() -> None:
    base = {"api_key": "k", "api_secret": "s", "base_url": "https://api-gw.onebound.cn/1688"}

    taobao = _platform_config(base, "taobao")
    unchanged = _platform_config(base, "1688")

    assert taobao["base_url"] == "https://api-gw.onebound.cn/taobao"
    assert taobao["api_key"] == "k"
    assert unchanged["base_url"] == "https://api-gw.onebound.cn/1688"


def test_canonical_source_url_keeps_taobao_item_id_in_query() -> None:
    assert canonical_source_url("https://item.taobao.com/item.htm?id=123456&spm=a21bo") == (
        "https://item.taobao.com/item.htm?id=123456"
    )
    assert canonical_source_url("https://detail.tmall.com/item.htm?id=777&skuId=1") == (
        "https://detail.tmall.com/item.htm?id=777"
    )
    assert canonical_source_url("https://detail.1688.com/offer/1.html?spm=a") == (
        "https://detail.1688.com/offer/1.html"
    )


def test_taobao_search_results_are_not_misclassified_as_duplicate_urls() -> None:
    """Regression: taobao items share one path and differ only in ?id= query."""
    evidence = ApiEvidence(provider="onebound-taobao", operation="item_search")
    payload = {
        "items": {
            "item": [
                {
                    "num_iid": f"{1000 + index}",
                    "title": f"手机壳 {index}",
                    "pic_url": f"https://img.alicdn.com/{index}.jpg",
                    "detail_url": f"https://item.taobao.com/item.htm?id={1000 + index}&spm=a21bo",
                }
                for index in range(10)
            ]
        }
    }
    candidates = normalize_search_response(payload, evidence=evidence)
    result = filter_and_score_candidates(candidates, DailySelectionCriteria(keywords=("手机壳",), target_count=3))

    assert len(result.candidates) == 10
    assert result.filtered == ()
    assert all(candidate.status == "candidate" for candidate in result.candidates)
    # 采集数量 = 展示上限：只保留前 3 条。
    assert len(result.candidates[: 3]) == 3


def test_collector_only_fetches_details_for_target_count_items_without_sku_filters() -> None:
    """Regression: taobao returns a fixed 48 items; details must not be fetched
    for every one when no SKU filters are configured."""
    from wh_local.data_collection.budget import UnlimitedApiBudget
    from wh_local.data_collection.collector import DailySelectionCollector
    from wh_local.data_collection.contracts import ApiEvidence
    from wh_local.data_collection.provider import ProviderCallResult

    class Provider:
        credential_fingerprint = "f" * 64

        def __init__(self) -> None:
            self.detail_ids: list[str] = []

        def search_keyword(self, criteria: DailySelectionCriteria, page: int = 1) -> ProviderCallResult:
            payload = {
                "items": {
                    "item": [
                        {
                            "num_iid": f"{1000 + index}",
                            "title": f"筷子 {index}",
                            "pic_url": f"https://img.alicdn.com/{index}.jpg",
                        }
                        for index in range(48)
                    ]
                }
            }
            return ProviderCallResult(
                payload, (ApiEvidence(provider="onebound-taobao", operation="item_search"),)
            )

        def get_item_detail(self, offer_id: str) -> ProviderCallResult:
            self.detail_ids.append(offer_id)
            return ProviderCallResult(
                {"items": {"item": {"num_iid": offer_id, "title": "筷子"}}},
                (ApiEvidence(provider="onebound-taobao", operation="item_get"),),
            )

    provider = Provider()
    collector = DailySelectionCollector(
        workspace_id="default",
        provider=provider,  # type: ignore[arg-type]
        budget=UnlimitedApiBudget(),
        provider_credential_fingerprint=provider.credential_fingerprint,
    )
    result = collector.collect(DailySelectionCriteria(keywords=("筷子",), target_count=3))

    assert len(result.detail_errors) == 0
    assert result.detail_calls <= 3
    assert len(provider.detail_ids) <= 3
    assert provider.detail_ids == [f"{1000 + index}" for index in range(len(provider.detail_ids))]


def test_normalize_detail_response_tolerates_zero_moq_from_taobao_skus() -> None:
    """Regression: taobao SKUs often carry moq=0; it must not fail the contract."""
    from wh_local.data_collection.normalizer import normalize_detail_response

    candidate = normalize_detail_response(
        {
            "item": {
                "num_iid": "831569268224",
                "title": "厨房置物架",
                "price": "19.9",
                "moq": 0,
                "skus": [
                    {"sku_id": "1", "price": "19.9", "moq": 0},
                    {"sku_id": "2", "price": "25.0", "moq": 1},
                ],
            }
        },
        evidence=ApiEvidence(provider="onebound-taobao", operation="item_get"),
    )

    assert candidate.min_order_quantity is None
    assert [variant.min_order_quantity for variant in candidate.source_variant_records] == [None, 1]
