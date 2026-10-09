"""Taobao/Tmall whole-shop collection via the shared OneBound provider.

Mirrors the 1688 shop-collection contract: seed item (or numeric shop id) ->
resolve shop_id + seller_id -> paginate ``item_search_shop`` -> enrich each
item with ``item_get``. No real network access is performed.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from wh_local.data_collection.provider import HttpResponse, OneBoundProvider
from wh_local.data_collection.service import DailySelectionActor
from wh_local.data_collection.shop_parsing import (
    detect_shop_platform,
    extract_taobao_item_id,
    extract_taobao_shop_id,
)
from wh_local.data_collection.shop_repository import ShopCollectionRepository
from wh_local.data_collection.shop_service import _parse_source_input
from wh_local.data_collection.shop_worker import (
    ShopCollectionWorker,
    _seller_info_identity,
    _taobao_shop_identity,
)
from wh_local.db import init_db


def _taobao_provider(transport: object) -> OneBoundProvider:
    return OneBoundProvider(
        {
            "api_key": "test-key",
            "api_secret": "test-secret",
            "base_url": "https://api.example.test/taobao",
        },
        transport=transport,  # type: ignore[arg-type]
    )


def test_search_shop_sends_shop_id_and_seller_id_for_taobao() -> None:
    calls: list[dict[str, Any]] = []

    class Transport:
        def request(self, method: str, url: str, **kwargs: Any) -> HttpResponse:
            calls.append({"method": method, "url": url, **kwargs})
            return HttpResponse(status=200, body=json.dumps({"code": 200, "items": {"item": []}}).encode())

    result = _taobao_provider(Transport()).search_shop("440688975", 2, seller_id="4273827547")

    assert result.ok
    assert calls[0]["url"] == "https://api.example.test/taobao/item_search_shop_pro/"
    assert calls[0]["params"] == {
        "key": "test-key", "secret": "test-secret",
        "shop_id": "440688975", "seller_id": "4273827547", "page": 2,
    }
    assert result.audit.operation == "item_search_shop_pro"
    assert result.audit.provider == "onebound-taobao"
    assert "test-key" not in str(result.audit.model_dump())


def test_search_shop_rejects_missing_taobao_seller_id() -> None:
    class Transport:
        def request(self, *args: object, **kwargs: object) -> HttpResponse:
            raise AssertionError("unexpected request")

    result = _taobao_provider(Transport()).search_shop("440688975", 1)  # seller_id missing

    assert result.error is not None
    assert result.error.code == "invalid_request"


def test_seller_info_resolves_seller_from_shop_id() -> None:
    calls: list[str] = []

    class Transport:
        def request(self, method: str, url: str, **kwargs: Any) -> HttpResponse:
            calls.append(url)
            return HttpResponse(
                status=200,
                body=json.dumps({
                    "items": {"shop_id": "440688975", "seller_id": "4273827547", "nick": "VOLARE旗舰店"},
                    "code": 200,
                }).encode(),
            )

    result = _taobao_provider(Transport()).get_seller_info("440688975")

    assert result.ok
    assert calls == ["https://api.example.test/taobao/seller_info/"]
    assert result.audit.operation == "seller_info"
    assert _seller_info_identity(result.response) == {
        "seller_id": "4273827547", "shop_id": "440688975", "nick": "VOLARE旗舰店",
    }


def test_item_get_identity_extracts_taobao_shop_id_and_seller_id() -> None:
    identity = _taobao_shop_identity({
        "items": {"item": {
            "num_iid": "883692104104",
            "title": "VOLARE5号排球",
            "seller_id": "4273827547",
            "shop_id": "440688975",
            "seller_info": {"shop_name": "VOLARE旗舰店", "nick": "VOLARE旗舰店"},
        }}
    })

    assert identity == {
        "shop_id": "440688975", "seller_id": "4273827547", "shop_name": "VOLARE旗舰店",
    }


def test_detect_shop_platform_distinguishes_hosts() -> None:
    assert detect_shop_platform("https://item.taobao.com/item.htm?id=883692104104") == "taobao"
    assert detect_shop_platform("https://detail.tmall.com/item.htm?id=883692104104") == "taobao"
    assert detect_shop_platform("https://volare.tmall.com/category.htm?spm=1") == "taobao"
    assert detect_shop_platform("https://detail.1688.com/offer/1.html") == "1688"


def test_extract_taobao_item_id_accepts_item_and_tmall_links_with_noisy_query() -> None:
    link = (
        "https://detail.tmall.com/item.htm?id=883692104104&mi_id=deXpQ9JoI94&skuId=5891153667531"
        "&spm=a21bo.jianhua%2Fa.201876.d3.1d212a89Jrtg3R"
    )

    assert extract_taobao_item_id(link) == "883692104104"
    assert extract_taobao_item_id("https://item.taobao.com/item.htm?id=9143766258256") == "9143766258256"
    assert extract_taobao_item_id("9143766258256") == "9143766258256"


def test_extract_taobao_shop_id_accepts_numeric_shop_urls_and_rejects_nick_subdomains() -> None:
    assert extract_taobao_shop_id("https://shop440688975.taobao.com/") == "440688975"
    assert extract_taobao_shop_id("https://shop.m.taobao.com/shop/shop_index.htm?shop_id=440688975") == "440688975"
    assert extract_taobao_shop_id("440688975") == "440688975"
    with pytest.raises(ValueError):
        # 昵称子域（如 volare.tmall.com）无法直接解析出数字 shop_id。
        extract_taobao_shop_id("https://volare.tmall.com/category.htm?spm=1")


def test_parse_taobao_source_input_variants() -> None:
    assert _parse_source_input("https://item.taobao.com/item.htm?id=883692104104", "taobao") == (
        "pending:883692104104", "883692104104", "https://item.taobao.com/item.htm?id=883692104104"
    )
    assert _parse_source_input("https://shop440688975.taobao.com/", "taobao") == (
        "440688975", "", "https://shop440688975.taobao.com/"
    )
    assert _parse_source_input("shopid:440688975", "taobao") == ("440688975", "", "")
    assert _parse_source_input("id:883692104104", "taobao") == ("pending:883692104104", "883692104104", "")
    assert _parse_source_input("883692104104", "taobao") == ("pending:883692104104", "883692104104", "")
    with pytest.raises(ValueError):
        # 昵称子域店铺首页无法识别，应提示换商品链接。
        _parse_source_input("https://volare.tmall.com/category.htm", "taobao")


# ---------------------------------------------------------------------------
# Worker-level taobao batch: seed -> resolve -> list -> enrich.
# ---------------------------------------------------------------------------


class Result:
    def __init__(self, response: dict, error: object | None = None) -> None:
        self.response = response
        self.error = error

    @property
    def ok(self) -> bool:
        return self.error is None


class TaobaoProvider:
    def __init__(self) -> None:
        self.shop_calls: list[tuple[int, str | None]] = []
        self.detail_calls: list[str] = []
        self.lock = threading.Lock()

    def search_shop(self, shop_sid: str, page: int, *, seller_id: str | None = None) -> Result:
        self.shop_calls.append((page, seller_id))
        return Result({
            "items": {"item": [{"num_iid": "883692104104", "title": "VOLARE5号排球",
                                "detail_url": "https://item.taobao.com/item.htm?id=883692104104"}]},
            "total_results": 1, "page_size": 20, "page": page,
        })

    def get_item_detail(self, offer_id: str) -> Result:
        self.detail_calls.append(offer_id)
        if offer_id == "883692104104" and len(self.detail_calls) == 1:
            return Result({"items": {"item": {
                "num_iid": offer_id, "title": "VOLARE5号排球",
                "seller_id": "4273827547", "shop_id": "440688975",
                "seller_info": {"shop_name": "VOLARE旗舰店"},
            }}})
        return Result({"items": {"item": {"num_iid": offer_id, "title": "VOLARE5号排球"}}})


def _repository(tmp_path: Path) -> ShopCollectionRepository:
    database = tmp_path / "runtime.sqlite3"
    init_db(database)
    return ShopCollectionRepository(database)


def _detail(item: Any, result: Result) -> dict:
    return {
        "candidate_id": f"taobao:{item.offer_id}",
        "offer_id": item.offer_id,
        "source_platform": "taobao",
        "source_url": "https://item.taobao.com/item.htm?id=" + item.offer_id,
        "source_title": result.response["items"]["item"]["title"],
    }


def test_taobao_batch_resolves_shop_from_seed_and_lists_with_seller_id(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.create_batch(
        batch_id="batch-tb", workspace_id="default", actor_id="actor-a",
        platform="taobao", shop_sid="pending:883692104104", seed_offer_id="883692104104",
    )
    provider = TaobaoProvider()
    seen_configs: list[dict] = []

    def factory(config: dict) -> TaobaoProvider:
        seen_configs.append(dict(config))
        return provider

    worker = ShopCollectionWorker(
        repository=repository,
        # 解析器默认返回 1688 配置；worker 必须按批次平台转换成淘宝 base_url。
        provider_config_resolver=lambda actor: {
            "api_key": "k", "api_secret": "s", "base_url": "https://api-gw.onebound.cn/1688",
        },
        provider_factory=factory,
        intake_shop_candidate=lambda **payload: {"action": "created", "draft": {}},
        detail_normalizer=_detail,
    )
    worker.process_batch("batch-tb")
    batch = repository.get_batch(workspace_id="default", batch_id="batch-tb")

    assert batch.platform == "taobao"
    assert seen_configs[0]["base_url"] == "https://api-gw.onebound.cn/taobao"
    assert batch.shop_sid == "440688975"
    assert batch.seller_id == "4273827547"
    assert batch.shop_name == "VOLARE旗舰店"
    assert batch.status == "completed"
    assert provider.shop_calls == [(1, "4273827547")]
    assert provider.detail_calls == ["883692104104"]
