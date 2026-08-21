from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
from typing import Any

import pytest

from wh_local.data_collection.provider import HttpResponse, OneBound1688Provider
from wh_local.data_collection.shop_parsing import (
    extract_1688_offer_id,
    extract_1688_shop_sid,
    normalize_shop_page,
    validate_shop_sid,
)


def _provider(transport: object) -> OneBound1688Provider:
    return OneBound1688Provider(
        {
            "api_key": "test-key",
            "api_secret": "test-secret",
            "base_url": "https://api.example.test/1688",
        },
        transport=transport,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    ("value", "offer_id"),
    (
        ("123456", "123456"),
        (" https://detail.1688.com/offer/123456.html?spm=a ", "123456"),
        ("https://detail.1688.com/123456.html", "123456"),
        ("https://m.1688.com/offer/123456.html", "123456"),
        ("https://detail.1688.com/offer_detail.htm?offerId=123456", "123456"),
        (
            "  https://shop.1688.com/page.htm?spm=a&offerId-1071342679320&scene=search  ",
            "1071342679320",
        ),
    ),
)
def test_extract_1688_offer_id_accepts_plain_ids_and_canonical_1688_urls(value: str, offer_id: str) -> None:
    assert extract_1688_offer_id(value) == offer_id


@pytest.mark.parametrize("value", ("", "abc", "https://example.test/offer/123.html", "https://detail.1688.com/offer/nope.html"))
def test_extract_1688_offer_id_rejects_unusable_input(value: str) -> None:
    with pytest.raises(ValueError, match="1688 offer"):
        extract_1688_offer_id(value)


@pytest.mark.parametrize(
    ("value", "sid"),
    (
        ("https://b2b-22165305319342b.1688.com/", "b2b-22165305319342b"),
        ("https://shop1234567890123.1688.com/page/offerlist.htm", "shop1234567890123"),
        (
            "https://winport.m.1688.com/page/index.html?memberId=b2b-22165305319342b",
            "b2b-22165305319342b",
        ),
        ("https://m.1688.com/winport/b2b-22165305319342b.html", "b2b-22165305319342b"),
    ),
)
def test_extract_1688_shop_sid_accepts_real_shop_home_urls(value: str, sid: str) -> None:
    assert extract_1688_shop_sid(value) == sid


@pytest.mark.parametrize(
    "value",
    (
        "https://detail.1688.com/offer/123456.html",
        "https://www.1688.com/",
        "https://example.test/b2b-shop",
    ),
)
def test_extract_1688_shop_sid_rejects_non_shop_urls(value: str) -> None:
    with pytest.raises(ValueError, match="shop"):
        extract_1688_shop_sid(value)


@pytest.mark.parametrize("value", ("shop-123", " shop_abc ", 123456))
def test_validate_shop_sid_returns_a_trimmed_nonempty_identifier(value: object) -> None:
    assert validate_shop_sid(value) == str(value).strip()


@pytest.mark.parametrize("value", (None, "", "\t", True, {"sid": "shop-1"}))
def test_validate_shop_sid_rejects_empty_or_ambiguous_identifiers(value: object) -> None:
    with pytest.raises(ValueError, match="shop sid"):
        validate_shop_sid(value)


def test_normalize_shop_page_collects_only_upstream_offer_ids_and_reports_missing_items() -> None:
    page = normalize_shop_page(
        {
            "data": {
                "items": [
                    {"num_iid": "1001"},
                    {"offer_id": 1002},
                    {"detail_url": "https://detail.1688.com/offer/1003.html"},
                    {"title": "upstream omitted its id"},
                ]
            }
        },
        evidence=None,
    )

    assert page.offer_ids == ("1001", "1002", "1003")
    assert page.missing_offer_count == 1
    assert page.evidence.operation == "item_search_shop"


def test_normalize_shop_page_uses_onebound_total_results_page_size_and_current_page() -> None:
    page = normalize_shop_page(
        {
            "items": {
                "item": [{"num_iid": "1001"}],
                "total_results": "250",
                "page_size": "20",
                "page": "3",
            }
        },
        evidence=None,
    )

    assert page.total_pages == 13
    assert page.has_next is True


def test_normalize_shop_page_accepts_a_root_level_current_page_with_a_data_items_wrapper() -> None:
    page = normalize_shop_page(
        {
            "page": 1,
            "data": {
                "items": {
                    "item": [{"num_iid": "1001"}],
                    "total_results": 40,
                    "page_size": 20,
                }
            },
        },
        evidence=None,
    )

    assert page.total_pages == 2
    assert page.has_next is True


def test_normalize_shop_page_stops_at_the_final_page_or_an_empty_page() -> None:
    final_page = normalize_shop_page(
        {
            "items": {
                "item": [{"num_iid": "1001"}],
                "total_results": 41,
                "page_size": 20,
                "current_page": 3,
            }
        },
        evidence=None,
    )
    empty_page = normalize_shop_page(
        {"items": {"item": [], "total_results": 41, "page_size": 20, "page": 1}}, evidence=None
    )

    assert final_page.total_pages == 3
    assert final_page.has_next is False
    assert empty_page.total_pages == 3
    assert empty_page.has_next is False


def test_normalize_shop_page_clamps_total_pages_and_falls_back_safely_for_malformed_pagination() -> None:
    capped = normalize_shop_page(
        {
            "items": {
                "item": [{"num_iid": "1001"}],
                "total_results": 100_000,
                "page_size": 1,
                "page": 100,
            }
        },
        evidence=None,
    )
    malformed = normalize_shop_page(
        {
            "data": {
                "items": {
                    "item": [{"num_iid": "1002"}],
                    "total_results": "not-a-number",
                    "page_size": 0,
                    "page": "also-bad",
                }
            }
        },
        evidence=None,
    )

    assert capped.total_pages == 100
    assert capped.has_next is False
    assert malformed.total_pages is None
    assert malformed.has_next is True


def test_search_shop_calls_documented_operation_with_pagination_and_redacts_credentials() -> None:
    calls: list[dict[str, Any]] = []

    class Transport:
        def request(self, method: str, url: str, **kwargs: Any) -> HttpResponse:
            calls.append({"method": method, "url": url, **kwargs})
            return HttpResponse(
                status=200,
                body=json.dumps({"code": 200, "items": {"item": [{"num_iid": "1001"}]}}).encode(),
            )

    result = _provider(Transport()).search_shop("shop-123", 2)

    assert result.ok
    assert calls == [
        {
            "method": "GET",
            "url": "https://api.example.test/1688/item_search_shop/",
            "params": {"key": "test-key", "secret": "test-secret", "seller_nick": "shop-123", "page": 2},
            "body": None,
            "headers": None,
            "timeout": 15.0,
        }
    ]
    assert result.audit.operation == "item_search_shop"
    assert result.audit.request_summary == {
        "http_method": "GET",
        "operation": "item_search_shop",
        "seller_nick_present": True,
        "page": 2,
    }
    assert "test-key" not in str(result.response)
    assert "test-secret" not in str(result.audit.model_dump())


def test_search_shop_retries_an_empty_upstream_page_once() -> None:
    class Transport:
        calls = 0

        def request(self, *args: object, **kwargs: object) -> HttpResponse:
            self.calls += 1
            body = {"code": 2000} if self.calls == 1 else {"code": 200, "items": {"item": []}}
            return HttpResponse(status=200, body=json.dumps(body).encode())

    transport = Transport()
    result = _provider(transport).search_shop("shop-123", 1)

    assert result.ok
    assert transport.calls == 2
    assert [audit.response_summary["outcome"] for audit in result.audits] == ["no_results", "success"]


@pytest.mark.parametrize("seller_nick, page", (("", 1), ("shop-1", 0), ("shop-1", 101), ("shop-1", True)))
def test_search_shop_rejects_invalid_inputs_without_a_network_call(seller_nick: object, page: object) -> None:
    class Transport:
        def request(self, *args: object, **kwargs: object) -> HttpResponse:
            raise AssertionError("unexpected request")

    result = _provider(Transport()).search_shop(seller_nick, page)  # type: ignore[arg-type]

    assert result.error is not None
    assert result.error.code == "invalid_request"


def test_item_get_concurrency_is_process_wide_across_provider_instances() -> None:
    class ConcurrentTransport:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0
            self.lock = threading.Lock()

        def request(self, *args: object, **kwargs: object) -> HttpResponse:
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            time.sleep(0.02)
            with self.lock:
                self.active -= 1
            return HttpResponse(status=200, body=b'{"code": 200, "item": {"num_iid": "1"}}')

    transport = ConcurrentTransport()
    providers = [_provider(transport), _provider(transport)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda index: providers[index % 2].get_item_detail(str(index)), range(8)))

    assert all(result.ok for result in results)
    assert transport.max_active == 3
