"""Temu 商品发布配套：网关客户端缓存/错误/重试与 goods.add 请求体组装测试。"""

from __future__ import annotations

import json

from pathlib import Path
from typing import Any

import pytest
import requests

from wh_local.db import init_db
from wh_local.modules.temu_publish.client import (
    TemuCredentials,
    TemuGatewayError,
    TemuProductClient,
)
from wh_local.modules.temu_publish.schemas import (
    GoodsDraftInput,
    GoodsPropertyInput,
    SkcInput,
    SkuInput,
    SkuSpecInput,
    TemuSettingsUpdate,
)
from wh_local.modules.temu_publish.service import TemuPublishService


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200, headers: dict[str, str] | None = None) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: float) -> FakeResponse:
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if not self._responses:
            raise AssertionError("unexpected extra request")
        return self._responses.pop(0)


def _credentials() -> TemuCredentials:
    return TemuCredentials(client="jieye", token="yl-token", access_token="shop-token")


def _client(responses: list[FakeResponse], sleeps: list[float] | None = None) -> tuple[TemuProductClient, FakeSession]:
    session = FakeSession(responses)
    recorded = sleeps if sleeps is not None else []
    client = TemuProductClient(_credentials(), session=session, sleep=recorded.append)
    return client, session


def test_categories_are_cached_and_headers_are_set() -> None:
    payload = {"success": True, "errorCode": "1000000", "result": {"categoryDTOList": [{"catId": 1}]}}
    client, session = _client([FakeResponse(payload), FakeResponse(payload)])

    first = client.categories(0)
    second = client.categories(0)

    assert first == second == {"categoryDTOList": [{"catId": 1}]}
    # 第二次命中缓存，不再打网关
    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["headers"]["x-yl-client"] == "jieye"
    assert call["headers"]["x-yl-token"] == "yl-token"
    assert call["json"]["access_token"] == "shop-token"
    assert call["json"]["type"] == "bg.glo.goods.cats.get"
    assert call["json"]["request"] == {"parentCatId": 0}


def test_business_error_is_normalized_without_retry() -> None:
    payload = {
        "success": False,
        "errorCode": "7000006",
        "errorMsg": "access_token and app_key are not mapping.",
    }
    client, session = _client([FakeResponse(payload)])

    with pytest.raises(TemuGatewayError) as captured:
        client.token_info()

    assert captured.value.error_code == "7000006"
    assert "not mapping" in captured.value.message
    assert len(session.calls) == 1


def test_rate_limit_retries_once_then_succeeds() -> None:
    limited = FakeResponse(
        {"success": False, "errorCode": "RATE_LIMIT_EXCEEDED", "message": "too many"},
        status_code=429,
        headers={"Retry-After": "0"},
    )
    ok = FakeResponse({"success": True, "result": {"parentSpecDTOS": []}})
    sleeps: list[float] = []
    client, session = _client([limited, ok], sleeps)

    assert client.parent_specs() == {"parentSpecDTOS": []}
    assert len(session.calls) == 2
    assert sleeps and sleeps[0] <= 5


class StubCatalogClient:
    """只提供 attributes() 的假客户端，用于组装 payload 时校验必填属性。"""

    def __init__(self, required: bool = True, values: list[dict[str, Any]] | None = None) -> None:
        self._required = required
        self._values = values or [{"vid": 10, "value": "PU"}]
        #: 记录「被要求建兜底规格」的次数，用来断言不该建的时候没建
        self.spec_requests: list[tuple[int, str]] = []

    def attributes(self, cat_id: int, supported_type: int = 1) -> dict[str, Any]:
        return {
            "properties": [
                {
                    "pid": 1,
                    "name": "材质",
                    "refPid": 12,
                    "templatePid": 569789,
                    "valueUnit": [""],
                    "required": self._required,
                    "values": self._values,
                }
            ]
        }


    def ensure_default_spec(
        self, spec_name: str = "", catalog: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """无销售属性类目兜底用的真实规格（颜色/默认）。"""
        self.spec_requests.append((1001, spec_name or "默认"))
        return {
            "parentSpecId": 1001,
            "parentSpecName": "颜色",
            "specId": 20832,
            "specName": "默认",
        }


def _draft(properties: list[GoodsPropertyInput] | None = None) -> GoodsDraftInput:
    spec = SkuSpecInput(
        parent_spec_id=1001, parent_spec_name="颜色", spec_id=2001, spec_name="Red"
    )
    return GoodsDraftInput(
        category_ids=[18768, 19503, 19668, 19669],
        title="Cartoon cosmetic bag",
        carousel_image_urls=["https://img.example.com/main.jpg"],
        freight_template_id="HFT-19421251915858203874",
        properties=properties
        if properties is not None
        else [GoodsPropertyInput(pid=1, vid=10)],
        skcs=[
            SkcInput(
                ext_code="SKC-1",
                preview_image_urls=["https://img.example.com/skc.jpg"],
                main_specs=[spec],
                skus=[
                    SkuInput(
                        ext_code="SKU-1",
                        supplier_price=1299,
                        suggested_price=1999,
                        stock=20,
                        warehouse_id="WH-07346074419333874",
                        weight_mg=200000,
                        length_mm=200,
                        width_mm=150,
                        height_mm=30,
                        specs=[spec],
                    )
                ],
            )
        ],
    )


def test_build_payload_maps_category_skc_sku_and_warehouse(tmp_path: Path) -> None:
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=StubCatalogClient())

    result = service.build_goods_payload(_draft())

    payload = result["payload"]
    assert payload["cat1Id"] == 18768
    assert payload["cat4Id"] == 19669
    assert payload["cat5Id"] == 0
    assert payload["productSemiManagedReq"]["bindSiteIds"] == [100]
    skc = payload["productSkcReqs"][0]
    assert skc["extCode"] == "SKC-1"
    sku = skc["productSkuReqs"][0]
    assert sku["siteSupplierPrices"] == [{"siteId": 100, "supplierPrice": 1299}]
    assert sku["productSkuWhExtAttrReq"]["productSkuWeightReq"]["value"] == 200000
    assert sku["productSkuStockQuantityReq"]["warehouseStockQuantityReqs"][0]["warehouseId"] == "WH-07346074419333874"
    assert payload["productWarehouseRouteReq"]["targetRouteList"] == [
        {"warehouseId": "WH-07346074419333874", "siteIdList": [100]}
    ]
    # 属性名称/refPid/templatePid/取值文本由类目属性表自动补齐
    prop = payload["productPropertyReqs"][0]
    assert prop["propName"] == "材质"
    assert prop["refPid"] == 12
    assert prop["templatePid"] == 569789
    assert prop["propValue"] == "PU"
    assert result["summary"]["sku_count"] == 1


def test_build_payload_rejects_missing_required_property(tmp_path: Path) -> None:
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=StubCatalogClient())

    with pytest.raises(ValueError, match="缺少必填属性"):
        service.build_goods_payload(_draft(properties=[]))


def test_build_payload_rejects_invalid_property_value(tmp_path: Path) -> None:
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=StubCatalogClient())
    draft = _draft(properties=[GoodsPropertyInput(pid=1, vid=999)])

    with pytest.raises(ValueError, match="不在可选值内"):
        service.build_goods_payload(draft)


def test_build_payload_rejects_non_https_image(tmp_path: Path) -> None:
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=StubCatalogClient())
    draft = _draft()
    draft.carousel_image_urls = ["http://img.example.com/main.jpg"]

    with pytest.raises(ValueError, match="https"):
        service.build_goods_payload(draft)


def test_settings_are_encrypted_and_reloadable(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite3"
    init_db(db_path)
    service = TemuPublishService(db_path)

    service.save_settings(
        TemuSettingsUpdate(
            yl_client="jieye",
            yl_token="yl-secret-token",
            access_token="shop-secret-token",
        ),
        actor_id="tester",
    )

    credentials = service.store.load()
    assert credentials.complete()
    assert credentials.client == "jieye"
    assert credentials.token == "yl-secret-token"

    status = service.settings_status()
    assert status["configured"]["ready"] is True
    assert status["source"]["yl_token"] == "database"
    # 状态接口只给脱敏提示，不含原文
    assert "yl-secret-token" not in str(status)
    assert status["hints"]["yl_token"].endswith("oken")


class FakeAiService:
    def task_history(self, limit: int, workspace_id: str) -> dict[str, Any]:
        return {
            "tasks": [
                {
                    "task_id": 7,
                    "title": "AI 批次",
                    "status": "completed",
                    "success_count": 1,
                    "updated_at": "2026-10-24T10:00:00Z",
                }
            ]
        }

    def task_preview(self, task_id: int, **kwargs: Any) -> dict[str, Any]:
        return {
            "items": [
                {
                    "item_id": 11,
                    "product_draft_id": 21,
                    "status": "completed",
                    "exportable": True,
                    "excluded": False,
                    "title": "AI processed title",
                    "main_image": "/api/product-processing/media-assets/a/content",
                    "carousel_images": ["/api/product-processing/media-assets/b/content"],
                    "detail_images": ["https://cos.example.com/detail.jpg"],
                    "core_fields": {
                        "sku": "SKU-AI-1",
                        "declared_price": 12.99,
                        "stock": 30,
                        "weight_g": 250,
                        "length_cm": 20,
                        "width_cm": 15,
                        "height_cm": 3,
                    },
                }
            ]
        }


class FakePodService:
    def list_batches(self, actor: Any, limit: int = 20) -> dict[str, Any]:
        return {
            "batches": [
                {"id": "batch-1", "title": "POD 批次", "status": "completed"}
            ]
        }

    def get_batch(self, actor: Any, batch_id: str) -> dict[str, Any]:
        return {
            "title": "POD 批次",
            "updated_at": "2026-10-24T11:00:00Z",
            "style_titles": [{"style_index": 1, "title": "POD style title"}],
            "items": [
                {
                    "style_index": 1,
                    "status": "completed",
                    "public_url": "https://cos.example.com/pod.jpg",
                    "composite_preview_url": "/api/pod-customization/assets/x",
                }
            ],
        }


def test_draft_library_aggregates_ai_and_pod_entries() -> None:
    from wh_local.modules.temu_publish.library import collect_draft_library
    from wh_local.session import Actor

    result = collect_draft_library(
        product_processing=FakeAiService(),
        pod_service=FakePodService(),
        actor=Actor(id="u1", username="u1", role="admin", workspace_id="default"),
        limit=10,
    )

    assert result["counts"] == {"ai": 1, "pod": 1, "total": 2}
    ai = next(item for item in result["entries"] if item["source"] == "ai")
    assert ai["title"] == "AI processed title"
    assert ai["sku"] == "SKU-AI-1"
    assert ai["weight_g"] == 250
    assert ai["carousel_images"] == [
        "/api/product-processing/media-assets/b/content",
        "/api/product-processing/media-assets/a/content",
    ]
    assert ai["detail_images"] == ["https://cos.example.com/detail.jpg"]
    assert ai["images"] == ai["carousel_images"] + ai["detail_images"]
    pod = next(item for item in result["entries"] if item["source"] == "pod")
    assert pod["title"] == "POD style title"
    assert pod["images"][0] == "https://cos.example.com/pod.jpg"


def test_upload_url_validation_rejects_non_https_and_private_hosts() -> None:
    from wh_local.modules.temu_publish.service import _assert_public_https_url

    with pytest.raises(ValueError, match="https"):
        _assert_public_https_url("http://img.example.com/a.jpg")
    with pytest.raises(ValueError, match="内网|本地"):
        _assert_public_https_url("https://127.0.0.1/a.jpg")
    with pytest.raises(ValueError, match="本地"):
        _assert_public_https_url("https://localhost/a.jpg")


def test_router_settings_and_payload_without_network(tmp_path: Path) -> None:
    """路由层冒烟：保存凭证 → 状态可读 → 组装 payload（跳过类目校验，不触网）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from wh_local.modules.temu_publish.router import create_temu_publish_router
    from wh_local.session import Actor, actor_from_authorization

    db_path = tmp_path / "runtime.sqlite3"
    init_db(db_path)
    app = FastAPI()
    app.include_router(create_temu_publish_router(db_path))
    app.dependency_overrides[actor_from_authorization] = lambda: Actor(
        id="tester", username="tester", role="admin"
    )
    http = TestClient(app)

    assert http.get("/api/temu-publish/settings").json()["configured"]["ready"] is False

    saved = http.put(
        "/api/temu-publish/settings",
        json={
            "yl_client": "jieye",
            "yl_token": "yl-token",
            "access_token": "shop-token",
        },
    )
    assert saved.status_code == 200
    assert saved.json()["configured"]["ready"] is True

    spec = {
        "parent_spec_id": 1001,
        "parent_spec_name": "颜色",
        "spec_id": 2001,
        "spec_name": "Red",
    }
    draft = {
        "category_ids": [18768, 19503, 19668, 19669],
        "title": "Cartoon cosmetic bag",
        "carousel_image_urls": ["https://img.example.com/main.jpg"],
        "freight_template_id": "HFT-1",
        "validate_catalog": False,
        "skcs": [
            {
                "ext_code": "SKC-1",
                "preview_image_urls": ["https://img.example.com/skc.jpg"],
                "main_specs": [spec],
                "skus": [
                    {
                        "ext_code": "SKU-1",
                        "supplier_price": 1299,
                        "stock": 10,
                        "warehouse_id": "WH-1",
                        "weight_mg": 200000,
                        "length_mm": 200,
                        "width_mm": 150,
                        "height_mm": 30,
                        "specs": [spec],
                    }
                ],
            }
        ],
    }
    built = http.post("/api/temu-publish/payload/build", json=draft)
    assert built.status_code == 200, built.text
    body = built.json()
    assert body["payload"]["cat4Id"] == 19669
    assert body["summary"]["sku_count"] == 1


class PublishStubClient(StubCatalogClient):
    """在假类目客户端基础上补 add_goods，用来测提交链路。"""

    def __init__(self) -> None:
        super().__init__()
        self.submitted: list[dict[str, Any]] = []
        # 商品列表（bg.glo.goods.list.get）用的假数据，按页返回
        self.goods_pages: list[list[dict[str, Any]]] = []
        self.goods_list_calls: int = 0

    def add_goods(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.submitted.append(payload)
        return {"goodsId": "G-1", "goodsName": payload.get("productName")}

    def goods_list(self, **filters: Any) -> dict[str, Any]:
        page = int(filters.get("page") or 1)
        self.goods_list_calls += 1
        rows = self.goods_pages[page - 1] if 0 <= page - 1 < len(self.goods_pages) else []
        total = sum(len(items) for items in self.goods_pages)
        return {"data": rows, "totalCount": total}


def test_add_goods_never_retries(  ) -> None:
    """建品非幂等：上游 5xx / 网络异常时必须只提交一次，否则会重复上架。"""
    client, session = _client([FakeResponse({"success": False, "message": "boom"}, status_code=502)])
    with pytest.raises(TemuGatewayError):
        client.add_goods({"productName": "x"})
    assert len(session.calls) == 1, "建品接口不允许自动重试"
    body = session.calls[0]["json"]
    assert body["type"] == "bg.glo.goods.add"
    assert body["gateway"] == "PARTNER"
    assert body["access_token"] == "shop-token"


def test_add_goods_retries_nothing_on_network_error() -> None:
    """网络异常同样不重试（FakeSession 只给一个响应，若重试会抛 AssertionError）。"""

    class Flaky(FakeSession):
        def post(self, url, *, json, headers, timeout):  # type: ignore[override]
            self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
            raise requests.ConnectionError("network down")

    session = Flaky([])
    client = TemuProductClient(_credentials(), session=session, sleep=lambda _s: None)
    with pytest.raises(TemuGatewayError):
        client.add_goods({"productName": "x"})
    assert len(session.calls) == 1


def test_publish_goods_submits_built_payload(tmp_path: Path) -> None:
    client = PublishStubClient()
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=client)

    result = service.publish_goods(_draft())

    assert result["submitted"] is True
    assert result["api_type"] == "bg.glo.goods.add"
    assert result["result"]["goodsId"] == "G-1"
    assert len(client.submitted) == 1
    submitted = client.submitted[0]
    assert submitted["cat1Id"] == 18768
    assert submitted["productSkcReqs"][0]["extCode"] == "SKC-1"
    # 返回体里带上 summary/warnings，前端不用再算一遍
    assert result["summary"]["sku_count"] == 1
    assert isinstance(result["warnings"], list)


def test_publish_goods_blocks_when_payload_invalid(tmp_path: Path) -> None:
    """校验不过（缺必填属性）时绝不能提交，避免半成品直接发到店铺。"""
    client = PublishStubClient()
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=client)

    with pytest.raises(ValueError, match="缺少必填属性"):
        service.publish_goods(_draft(properties=[]))
    assert client.submitted == []


def test_pod_entries_collapse_variants_into_one_card_per_style() -> None:
    """一款 4 个变体只能出一张卡（原来出 4 张、且 id 完全相同）。"""

    class FakePodService:
        def list_batches(self, actor, limit=6):
            return {"batches": [{"id": "b1", "status": "completed"}]}

        def get_batch(self, actor, batch_id):
            items = []
            for variant in range(4):
                items.append(
                    {
                        "status": "completed",
                        "style_index": 1,
                        "variant_index": variant,
                        "export_selected": True,
                        "public_url": f"https://img.example.com/s1-v{variant}.jpg",
                        "composite_preview_url": None,
                        "pattern_preview_url": None,
                    }
                )
            items.append(
                {
                    "status": "completed",
                    "style_index": 2,
                    "variant_index": 0,
                    "export_selected": True,
                    "public_url": "https://img.example.com/s2-v0.jpg",
                    "composite_preview_url": None,
                    "pattern_preview_url": None,
                }
            )
            return {"title": "桌布", "items": items, "style_titles": []}

    from wh_local.modules.temu_publish.library import _collect_pod_entries

    class Actor:
        workspace_id = "local"
        id = "u1"

    entries = _collect_pod_entries(FakePodService(), Actor(), 6)

    assert len(entries) == 2, f"一款一张卡，应 2 条，实际 {len(entries)}"
    assert [entry["id"] for entry in entries] == ["pod:b1:1", "pod:b1:2"]
    first = entries[0]
    assert first["variant_count"] == 4
    # 4 个变体的图都合进同一张卡，供发布页挑选
    assert len(first["images"]) == 4
    assert first["images"][0].endswith("s1-v0.jpg")
    assert entries[1]["variant_count"] == 1


def test_build_payload_carries_english_i18n_title(tmp_path: Path) -> None:
    """文档 §7.4 的 goods.add 示例带 productI18nReqs；英文标题要自动带一份 en。"""
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=StubCatalogClient())

    payload = service.build_goods_payload(_draft())["payload"]

    assert payload["productI18nReqs"] == [
        {"language": "en", "productName": "Cartoon cosmetic bag"}
    ]


def test_build_payload_prefers_explicit_english_title(tmp_path: Path) -> None:
    """英文标题：填了就优先用它，中文产品标题也能发。"""
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=StubCatalogClient())
    draft = _draft()
    draft.title = "卡通化妆包"
    draft.title_en = "Cartoon cosmetic bag"

    payload = service.build_goods_payload(draft)["payload"]

    assert payload["productI18nReqs"] == [
        {"language": "en", "productName": "Cartoon cosmetic bag"}
    ]
    assert payload["productName"] == "卡通化妆包"


def test_build_payload_carries_outer_goods_url(tmp_path: Path) -> None:
    """站外产品链接落到 productWhExtAttrReq.outerGoodsUrl，默认空串。"""
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=StubCatalogClient())
    draft = _draft()
    assert service.build_goods_payload(draft)["payload"]["productWhExtAttrReq"]["outerGoodsUrl"] == ""

    draft.outer_goods_url = "https://detail.1688.com/offer/123.html"
    payload = service.build_goods_payload(draft)["payload"]
    assert payload["productWhExtAttrReq"]["outerGoodsUrl"] == "https://detail.1688.com/offer/123.html"


def test_build_payload_warns_when_title_is_chinese(tmp_path: Path) -> None:
    """中文标题不硬塞 en（会被判英文标题不合规），改成警告让人改。"""
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=StubCatalogClient())
    draft = _draft()
    draft.title = "卡通化妆包"

    result = service.build_goods_payload(draft)

    assert "productI18nReqs" not in result["payload"]
    assert any("英文标题" in item for item in result["warnings"])


def test_check_mandatory_passes_leaf_category_and_properties(tmp_path: Path) -> None:
    """发布前校验必须带叶子类目 + 站点 + 已填属性，且绝不能触发建品。"""

    class StubClient(StubCatalogClient):
        def __init__(self) -> None:
            super().__init__()
            self.mandatory_calls: list[tuple[int, list[int], list[dict]]] = []
            self.add_calls = 0

        def mandatory_config(self, leaf_cat_id, bind_site_ids, product_property_reqs=None):
            self.mandatory_calls.append((leaf_cat_id, list(bind_site_ids), list(product_property_reqs or [])))
            return {"mandatoryList": []}

        def add_goods(self, payload):
            self.add_calls += 1
            return {}

    client = StubClient()
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=client)

    result = service.check_mandatory(_draft())

    assert result["result"] == {"mandatoryList": []}
    assert len(client.mandatory_calls) == 1
    leaf, sites, props = client.mandatory_calls[0]
    assert leaf == 19669  # _draft() 的叶子类目
    assert sites == [100]
    assert props and props[0]["pid"] == 1
    assert client.add_calls == 0, "校验绝不能提交商品"


def test_check_mandatory_falls_back_when_gateway_rejects_catsmandatory(tmp_path: Path) -> None:
    """网关不支持 catsmandatory.get（实测 sign is invalid）→ 必须退回 attrs 必填校验。"""

    class StubClient(StubCatalogClient):
        def mandatory_config(self, leaf_cat_id, bind_site_ids, product_property_reqs=None):
            raise TemuGatewayError(
                "bg.glo.goods.catsmandatory.get",
                "sign is invalid, please check your sign calculation",
            )

    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=StubClient())

    # 属性全填 → 回退校验应报「没有缺的必填项」
    full = service.check_mandatory(_draft())
    assert "attrs.get" in full["source"]
    assert full["result"]["requiredMissing"] == []

    # 属性不填 → 组装阶段就会抛（和「生成发布请求体」一致），不会走到网关
    with pytest.raises(ValueError, match="缺少必填属性"):
        service.check_mandatory(_draft(properties=[]))


def test_spec_decl_uses_parent_spec_id_and_zero_vid(tmp_path: Path) -> None:
    """商品级销售属性声明：pid/refPid/templatePid/parentSpecId 全填**父规格 id**，vid=0。

    实测（2026-10-26 真实上架成功，productId 45629654961）：
    这里填属性 id（颜色=13）或属性值 vid（=514）都会被判「主销售属性不合法」。
    SKC/SKU 级规格反过来只发 4 个字段，多带 pid/vid 同样被拒。
    """
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=StubCatalogClient())

    payload = service.build_goods_payload(_draft())["payload"]

    decl = payload["productSpecPropertyReqs"][0]
    assert decl["parentSpecId"] == 1001
    assert decl["pid"] == 1001
    assert decl["refPid"] == 1001
    assert decl["templatePid"] == 1001
    assert decl["vid"] == 0
    assert decl["propName"] == "颜色"
    assert decl["parentSpecName"] == "颜色"
    assert decl["specId"] == 2001
    assert decl["specName"] == "Red"
    assert decl["propValue"] == "Red"
    for key in ("valueUnit", "valueGroupId", "valueGroupName"):
        assert key in decl

    # 主销售规格必须是全 0 的空对象
    assert payload["productSkcReqs"][0]["mainProductSkuSpecReqs"] == [
        {"specId": 0, "specName": "", "parentSpecId": 0, "parentSpecName": ""}
    ]
    # SKU 级规格只发 4 个字段
    sku_spec = payload["productSkcReqs"][0]["productSkuReqs"][0]["productSkuSpecReqs"][0]
    assert set(sku_spec) == {"parentSpecId", "parentSpecName", "specId", "specName"}


def test_sale_properties_are_not_duplicated_into_goods_properties(tmp_path: Path) -> None:
    """销售属性不能同时出现在 productPropertyReqs 里（实测会判父子关系/规格不合法）。"""

    class SaleCatalogClient(StubCatalogClient):
        def attributes(self, cat_id: int, supported_type: int = 1) -> dict[str, Any]:
            base = super().attributes(cat_id, supported_type)
            # 颜色是销售属性（isSale=true），父规格 1001 与 draft 的规格对得上
            base["properties"].append(
                {
                    "pid": 13,
                    "name": "颜色",
                    "refPid": 63,
                    "templatePid": 1560574,
                    "parentSpecId": 1001,
                    "isSale": True,
                    "required": True,
                    "values": [{"vid": 514, "value": "Red"}],
                }
            )
            return base

    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=SaleCatalogClient())

    payload = service.build_goods_payload(
        _draft(properties=[GoodsPropertyInput(pid=1, vid=10), GoodsPropertyInput(pid=13, vid=514)])
    )["payload"]

    pids = [int(item["pid"]) for item in payload["productPropertyReqs"]]
    assert pids == [1], f"销售属性 13 应被剔除，实际 {pids}"


def test_payload_carries_currency_and_origin_region2(tmp_path: Path) -> None:
    """币种必须 CNY、产地 region2Id 不能是 0（两项都被 Temu 真拒过）。"""
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=StubCatalogClient())

    payload = service.build_goods_payload(_draft())["payload"]

    sku = payload["productSkcReqs"][0]["productSkuReqs"][0]
    assert sku["currencyType"] == "CNY"
    assert payload["productWhExtAttrReq"]["productOrigin"]["region2Id"] != 0
    assert payload["productSecondHandReq"] == {"isSecondHand": False}
    assert payload["personalizationSwitch"] == 0
    # 素材图必传（缺了报 Material Image cannot be empty）
    assert payload["materialImgUrl"] == "https://img.example.com/main.jpg"


def test_detail_images_are_split_into_one_image_per_module(tmp_path: Path) -> None:
    """详情图一个模块只能放一张（实测报「每个图片模块只能有一张图片」）。"""
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=StubCatalogClient())
    draft = _draft().model_copy(
        update={"detail_image_urls": [
            "https://img.example.com/d1.jpg",
            "https://img.example.com/d2.jpg",
            "https://img.example.com/d3.jpg",
        ]}
    )

    payload = service.build_goods_payload(draft)["payload"]

    modules = payload["goodsLayerDecorationReqs"]
    assert len(modules) == 3, "每张详情图一个模块"
    for index, module in enumerate(modules, start=1):
        assert module["key"] == "DecImage"
        assert module["priority"] == index
        assert len(module["contentList"]) == 1, "一个模块只能一张图"


def _clear_temu_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """环境变量兜底会掩盖"这个账号没配"的判定，测试里先清掉。"""
    for name in ("WH_TEMU_YL_CLIENT", "WH_TEMU_YL_TOKEN", "WH_TEMU_ACCESS_TOKEN"):
        monkeypatch.delenv(name, raising=False)


def test_credentials_are_isolated_per_account(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """每个账号一份店铺凭证：A 配的 token 对 B 不可见，解绑也只影响自己。"""
    _clear_temu_env(monkeypatch)
    init_db(tmp_path / "runtime.sqlite3")
    root = TemuPublishService(tmp_path / "runtime.sqlite3", client=StubCatalogClient())
    alice = root.with_owner("cust_alice")
    bob = root.with_owner("cust_bob")

    alice.save_settings(
        TemuSettingsUpdate(yl_client="c", yl_token="t", access_token="alice-shop"), "cust_alice"
    )

    assert alice.settings_status()["configured"]["ready"] is True
    assert alice.settings_status()["owner"]["account_id"] == "cust_alice"
    assert alice.settings_status()["owner"]["personal"] is True

    # B 看不到 A 的店铺
    assert bob.settings_status()["configured"]["access_token"] is False
    assert bob.settings_status()["configured"]["ready"] is False

    # 解绑只删自己那份
    bob.clear_settings("cust_bob")
    assert alice.settings_status()["configured"]["access_token"] is True
    alice.clear_settings("cust_alice")
    assert alice.settings_status()["configured"]["access_token"] is False


def test_legacy_global_credentials_adopt_only_for_its_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """升级前的全局凭证只迁给当初保存它的人，别人不会平白继承（否则会发错店铺）。"""
    _clear_temu_env(monkeypatch)
    init_db(tmp_path / "runtime.sqlite3")
    # 没有 owner 的服务 = 历史全局作用域
    legacy = TemuPublishService(tmp_path / "runtime.sqlite3", client=StubCatalogClient())
    legacy.save_settings(
        TemuSettingsUpdate(yl_client="c", yl_token="t", access_token="shared"), "cust_owner"
    )

    owner = TemuPublishService(
        tmp_path / "runtime.sqlite3", client=StubCatalogClient(), owner_id="cust_owner"
    )
    assert owner.settings_status()["configured"]["ready"] is True
    assert owner.settings_status()["configured"]["access_token"] is True

    other = TemuPublishService(
        tmp_path / "runtime.sqlite3", client=StubCatalogClient(), owner_id="cust_other"
    )
    assert other.settings_status()["configured"]["access_token"] is False


def test_router_clear_settings_only_touches_current_account(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """DELETE /settings 解绑当前账号；另一个账号的凭证必须原样保留。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from wh_local.modules.temu_publish.router import create_temu_publish_router
    from wh_local.session import Actor, actor_from_authorization

    _clear_temu_env(monkeypatch)
    db_path = tmp_path / "runtime.sqlite3"
    init_db(db_path)
    app = FastAPI()
    app.include_router(create_temu_publish_router(db_path))
    current = {"actor": Actor(id="cust_alice", username="alice", role="admin")}
    app.dependency_overrides[actor_from_authorization] = lambda: current["actor"]
    http = TestClient(app)

    for account in ("cust_alice", "cust_bob"):
        current["actor"] = Actor(id=account, username=account, role="admin")
        http.put(
            "/api/temu-publish/settings",
            json={"yl_client": "c", "yl_token": "t", "access_token": f"{account}-shop"},
        )

    current["actor"] = Actor(id="cust_alice", username="alice", role="admin")
    cleared = http.delete("/api/temu-publish/settings")
    assert cleared.status_code == 200
    assert cleared.json()["configured"]["access_token"] is False

    current["actor"] = Actor(id="cust_bob", username="bob", role="admin")
    assert http.get("/api/temu-publish/settings").json()["configured"]["access_token"] is True


def test_bootstrap_legacy_credentials_claimed_once_by_first_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """系统初始化的历史凭证只被第一个账号认领；第二个人登进来不能白拿同一份 token。"""
    _clear_temu_env(monkeypatch)
    init_db(tmp_path / "runtime.sqlite3")
    legacy = TemuPublishService(tmp_path / "runtime.sqlite3", client=StubCatalogClient())
    legacy.save_settings(
        TemuSettingsUpdate(yl_client="c", yl_token="t", access_token="bootstrap-shop"),
        "system-bootstrap",
    )

    first = TemuPublishService(
        tmp_path / "runtime.sqlite3", client=StubCatalogClient(), owner_id="cust_first"
    )
    assert first.settings_status()["configured"]["access_token"] is True

    second = TemuPublishService(
        tmp_path / "runtime.sqlite3", client=StubCatalogClient(), owner_id="cust_second"
    )
    assert second.settings_status()["configured"]["access_token"] is False


def test_legacy_credentials_of_another_account_are_never_adopted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """别人配的全局凭证不许继承（防止 A 用 B 的 token 发到自己店铺）。"""
    _clear_temu_env(monkeypatch)
    init_db(tmp_path / "runtime.sqlite3")
    legacy = TemuPublishService(tmp_path / "runtime.sqlite3", client=StubCatalogClient())
    legacy.save_settings(
        TemuSettingsUpdate(yl_client="c", yl_token="t", access_token="other-shop"), "cust_other"
    )

    intruder = TemuPublishService(
        tmp_path / "runtime.sqlite3", client=StubCatalogClient(), owner_id="cust_intruder"
    )
    assert intruder.settings_status()["configured"]["access_token"] is False


def test_build_payload_carries_sensitive_and_personalization_flags(tmp_path: Path) -> None:
    """敏感货（按 SKU）和定制/POD（整款）都要真的进 payload，默认关闭。"""
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=StubCatalogClient())

    default_payload = service.build_goods_payload(_draft())["payload"]
    sku_req = default_payload["productSkcReqs"][0]["productSkuReqs"][0]
    assert sku_req["productSkuWhExtAttrReq"]["productSkuSensitiveAttrReq"]["isSensitive"] == 0
    assert default_payload["personalizationSwitch"] == 0

    draft = _draft()
    draft.personalization = True
    for skc in draft.skcs:
        for sku in skc.skus:
            sku.is_sensitive = True
    payload = service.build_goods_payload(draft)["payload"]
    assert payload["personalizationSwitch"] == 1
    sensitive = payload["productSkcReqs"][0]["productSkuReqs"][0]["productSkuWhExtAttrReq"][
        "productSkuSensitiveAttrReq"
    ]
    assert sensitive["isSensitive"] == 1


def test_pod_entries_carry_declared_price_and_variant_fields() -> None:
    """POD 导出表里有申报价/尺寸/重量，草稿库不能把它丢掉（用于发布要能带过去）。"""

    class FakePodService:
        def list_batches(self, actor, limit=6):
            return {"batches": [{"id": "b1", "status": "completed"}]}

        def get_batch(self, actor, batch_id):
            return {
                "title": "收纳篮",
                "items": [
                    {
                        "status": "completed",
                        "style_index": 1,
                        "variant_index": 0,
                        "export_selected": True,
                        "public_url": "https://img.example.com/s1.jpg",
                        "composite_preview_url": None,
                        "pattern_preview_url": None,
                        "listing_fields": {
                            "declared_price": 150,
                            "weight_g": 350,
                            "length_cm": 30,
                            "width_cm": 30,
                            "height_cm": 12,
                            "source_url": "https://detail.1688.com/offer/1.html",
                        },
                        "skus": [
                            {
                                "name": "苹果4",
                                "sku_code": "PP-000180",
                                "declared_price": 150,
                                "weight_g": 350,
                                "length_cm": 30,
                                "width_cm": 30,
                                "height_cm": 12,
                            }
                        ],
                    }
                ],
                "style_titles": [],
            }

    from wh_local.modules.temu_publish.library import _collect_pod_entries

    class Actor:
        workspace_id = "local"
        id = "u1"

    entry = _collect_pod_entries(FakePodService(), Actor(), 6)[0]

    assert entry["declared_price"] == 150
    assert entry["sku"] == "PP-000180"
    assert entry["weight_g"] == 350
    assert entry["length_cm"] == 30
    assert entry["height_cm"] == 12
    assert entry["spec_value_1"] == "苹果4"
    assert entry["source_url"] == "https://detail.1688.com/offer/1.html"


def test_draft_library_result_is_cached_and_refreshable(tmp_path: Path) -> None:
    """草稿库聚合结果缓存 60 秒：第二次请求不再重扫（跑 get_batch 次数可观测）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from wh_local.modules.temu_publish.router import create_temu_publish_router
    from wh_local.session import Actor, actor_from_authorization

    calls = {"n": 0}

    class CountingPod:
        def list_batches(self, actor, limit=6):
            return {"batches": [{"id": "b1", "status": "completed"}]}

        def get_batch(self, actor, batch_id):
            calls["n"] += 1
            return {
                "title": "批次",
                "items": [
                    {
                        "status": "completed",
                        "style_index": 1,
                        "public_url": "https://img.example.com/a.jpg",
                        "listing_fields": {"declared_price": 150},
                    }
                ],
                "style_titles": [],
            }

    db_path = tmp_path / "runtime.sqlite3"
    init_db(db_path)
    app = FastAPI()
    app.include_router(create_temu_publish_router(db_path, pod_service_getter=lambda: CountingPod()))
    app.dependency_overrides[actor_from_authorization] = lambda: Actor(
        id="cache-tester", username="t", role="admin"
    )
    http = TestClient(app)

    first = http.get("/api/temu-publish/draft-library?limit=10")
    assert first.status_code == 200
    assert first.json()["counts"]["pod"] == 1
    assert calls["n"] == 1

    # 命中缓存：不再重扫
    second = http.get("/api/temu-publish/draft-library?limit=10")
    assert second.status_code == 200
    assert calls["n"] == 1

    # 显式刷新才重扫
    http.get("/api/temu-publish/draft-library?limit=10&refresh=true")
    assert calls["n"] == 2


def test_declared_price_follows_export_rule() -> None:
    """草稿库的申报价必须和导出表同一条规则：显式价抬到下限，没有就 成本×4（下限 150）。"""
    from wh_local.modules.temu_publish.library import _declared_price_cny

    # 显式价低于下限 → 抬到 150；标记为「不是推算」
    assert _declared_price_cny(explicit=12.99) == (150.0, False)
    # 显式价高于下限 → 原样
    assert _declared_price_cny(explicit=200) == (200.0, False)
    # 没填过：成本 × 4，低于 150 也按 150
    assert _declared_price_cny(cost=1.38) == (150.0, True)
    assert _declared_price_cny(cost=40.1) == (160.4, True)
    # 字符串（带货币符号/逗号）也要能解析
    assert _declared_price_cny(explicit="￥1,299.00") == (1299.0, False)
    # 一点价格依据都没有 → 仍然给下限，标记推算
    assert _declared_price_cny() == (150.0, True)


def test_draft_library_ai_entry_has_declared_price_when_column_is_empty() -> None:
    """AI 草稿的 declared_price 列基本是空的，草稿库要按成本推算出来（否则界面全是「—」）。"""
    from wh_local.modules.temu_publish.library import collect_draft_library

    class NoPriceAi(FakeAiService):
        def task_preview(self, task_id: int, **kwargs: Any) -> dict[str, Any]:
            preview = super().task_preview(task_id, **kwargs)
            preview["items"][0]["core_fields"] = {"sku": "SKU-AI-1", "stock": 30}
            return preview

        def get_drafts(self, draft_ids, **kwargs):  # noqa: ANN001, ANN201
            return [
                {
                    "id": 21,
                    "cost": 1.38,
                    "declared_price": None,
                    "description": "",
                    "raw_payload": {},
                }
            ]

    class Actor:
        workspace_id = "local"
        id = "u1"

    entries = collect_draft_library(
        product_processing=NoPriceAi(), pod_service=None, actor=Actor(), limit=10
    )["entries"]

    ai = [item for item in entries if item["source"] == "ai"]
    assert ai, "AI 条目没取到"
    assert ai[0]["declared_price"] == 150.0
    assert ai[0]["declared_price_estimated"] is True


def test_detail_image_resize_unifies_width_and_keeps_ratio() -> None:
    """详情图统一宽度：1688 那种 1392 宽的横图要缩到 800，高度等比。"""
    import io as _io

    from PIL import Image

    from wh_local.modules.temu_publish.service import _resize_image_to_width

    buffer = _io.BytesIO()
    Image.new("RGB", (1392, 795), (240, 240, 240)).save(buffer, format="JPEG")
    resized, width, height = _resize_image_to_width(buffer.getvalue(), 800)

    assert width == 800
    assert height == round(795 * 800 / 1392)
    # 产出的确实是 JPEG，且尺寸与返回值一致
    reopened = Image.open(_io.BytesIO(resized))
    assert reopened.size == (800, height)


def test_detail_payload_uses_real_size_and_warns_on_mixed_widths() -> None:
    """详情图按真实宽高写入；宽度不一致要给出提示（Temu 商详要求等宽）。"""
    service = TemuPublishService(Path("runtime.sqlite3"), client=StubCatalogClient())
    draft = _draft()
    from wh_local.modules.temu_publish.schemas import DetailImageInput

    draft.detail_images = [
        DetailImageInput(url="https://img.kwcdn.com/a.jpg", width=800, height=1200),
        DetailImageInput(url="https://img.kwcdn.com/b.jpg", width=1392, height=795),
    ]

    built = service.build_goods_payload(draft)

    layers = built["payload"]["goodsLayerDecorationReqs"]
    assert layers[0]["contentList"][0] == {
        "imgUrl": "https://img.kwcdn.com/a.jpg",
        "width": 800,
        "height": 1200,
    }
    assert layers[1]["contentList"][0]["width"] == 1392
    assert any("详情图宽度不一致" in item for item in built["warnings"])


def test_spec_declaration_uses_category_pid_even_without_validation() -> None:
    """跳过必填校验也要取类目属性：否则规格声明的 pid 退回父规格 id，Temu 判「请重置规格模版」。"""

    class CatalogClient(StubCatalogClient):
        def attributes(self, cat_id: int) -> dict:
            return {
                "templateId": 1,
                "properties": [
                    {
                        "pid": 13,
                        "name": "颜色",
                        "refPid": 63,
                        "templatePid": 1783986,
                        "parentSpecId": 1001,
                        "isSale": True,
                        "values": [{"vid": 376, "specId": 2001, "value": "白色"}],
                    }
                ],
            }

    service = TemuPublishService(Path("runtime.sqlite3"), client=CatalogClient())
    draft = _draft()
    draft.validate_catalog = False

    built = service.build_goods_payload(draft)
    decl = built["payload"]["productSpecPropertyReqs"][0]

    assert decl["pid"] == 13, "pid 必须是类目属性 id，不能退回父规格 id"
    assert decl["refPid"] == 63
    assert decl["templatePid"] == 1783986
    assert decl["parentSpecId"] == 1001


def test_multiple_skcs_merged_into_one_skc_with_many_skus() -> None:
    """多颜色：Temu 半托管要的是「1 个 SKC + N 个 SKU」，不是 N 个 SKC（真机实测）。"""
    from wh_local.modules.temu_publish.schemas import SkuSpecInput

    service = TemuPublishService(Path("runtime.sqlite3"), client=StubCatalogClient())
    draft = _draft()
    base_skc = draft.skcs[0]
    base_sku = base_skc.skus[0]

    skcs = []
    for index, color in enumerate(("Red", "Blue", "Black")):
        sku = base_sku.model_copy(deep=True)
        sku.ext_code = f"SKU-{color}"
        sku.specs = [
            SkuSpecInput(
                parent_spec_id=101, parent_spec_name="Color", spec_id=1 + index, spec_name=color
            ),
            SkuSpecInput(
                parent_spec_id=102, parent_spec_name="Size", spec_id=2, spec_name="30cm"
            ),
        ]
        skc = base_skc.model_copy(deep=True)
        skc.ext_code = f"SKC-{color}"
        skc.skus = [sku]
        skc.main_specs = [sku.specs[0]]
        skcs.append(skc)
    draft.skcs = skcs

    payload = service.build_goods_payload(draft)["payload"]
    skc_reqs = payload["productSkcReqs"]

    assert len(skc_reqs) == 1, "多颜色必须合并成一个 SKC"
    assert len(skc_reqs[0]["productSkuReqs"]) == 3, "三个颜色 = 三个 SKU"
    assert skc_reqs[0]["mainProductSkuSpecReqs"] == [
        {"specId": 0, "specName": "", "parentSpecId": 0, "parentSpecName": ""}
    ], "主销售规格只能是全零空对象"


def test_unbinding_does_not_resurrect_claimed_legacy_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """解绑后不能再被「历史凭证迁移」搬回来：否则界面显示已解绑，实际 token 还在。"""
    _clear_temu_env(monkeypatch)
    init_db(tmp_path / "runtime.sqlite3")
    legacy = TemuPublishService(tmp_path / "runtime.sqlite3", client=StubCatalogClient())
    legacy.save_settings(
        TemuSettingsUpdate(yl_client="c", yl_token="t", access_token="bootstrap-shop"),
        "system-bootstrap",
    )

    owner = TemuPublishService(
        tmp_path / "runtime.sqlite3", client=StubCatalogClient(), owner_id="cust_first"
    )
    assert owner.settings_status()["configured"]["ready"] is True

    owner.clear_settings("cust_first")
    assert owner.settings_status()["configured"]["access_token"] is False
    # 再读一次仍然为空（以前的实现会在这里把历史行重新迁回来）
    assert owner.settings_status()["configured"]["ready"] is False


def test_create_spec_is_not_retried_so_specs_are_not_duplicated() -> None:
    """建规格是写接口：网关 5xx 时只能试一次，重试会在类目里建出重复规格。"""
    client, session = _client([FakeResponse({"success": False, "errorMsg": "boom"}, status_code=500)])

    with pytest.raises(TemuGatewayError):
        client.create_spec(1, "红色")

    assert len(session.calls) == 1


def test_create_spec_invalidates_parent_spec_cache() -> None:
    """新建规格后父规格缓存要失效，否则下拉里最长 6 小时看不到新规格。"""
    client, session = _client(
        [
            FakeResponse({"success": True, "result": {"parentSpecDTOS": [{"parentSpecId": 1}]}}),
            FakeResponse({"success": True, "result": {"specId": 9}}),
            FakeResponse(
                {
                    "success": True,
                    "result": {"parentSpecDTOS": [{"parentSpecId": 1}, {"parentSpecId": 2}]},
                }
            ),
        ]
    )
    assert len(client.parent_specs()["parentSpecDTOS"]) == 1
    client.create_spec(1, "雾霾蓝")
    # 缓存被清掉 → 再取父规格真的又打了一次网关，拿到含新规格的两条
    assert len(client.parent_specs()["parentSpecDTOS"]) == 2
    assert len(session.calls) == 3


def test_image_bytes_rejects_oversized_data_url() -> None:
    """data URL 也要有 20MB 上限：以前只有外链下载分支限制，超大 base64 能直接进内存。"""
    from wh_local.modules.temu_publish.service import _load_image_bytes

    oversized = "data:image/png;base64," + "A" * (28 * 1024 * 1024)
    with pytest.raises(ValueError, match="20MB"):
        _load_image_bytes(oversized)


def test_image_download_does_not_follow_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    """外链图片只校验了第一跳，302 到内网地址就能绕过限制：下载时禁止跟随跳转。"""
    from wh_local.modules import temu_publish as _pkg  # noqa: F401
    from wh_local.modules.temu_publish import service as svc

    class RedirectResponse:
        is_redirect = True
        is_permanent_redirect = False
        status_code = 302

        def raise_for_status(self) -> None:  # pragma: no cover - 不该被调用
            raise AssertionError("redirect 不该继续处理")

    # 这一条只测「跟不跟跳转」，域名解析/内网判定另有测试，这里直接放行
    monkeypatch.setattr(svc, "_assert_public_https_url", lambda url: None)
    monkeypatch.setattr(svc.requests, "get", lambda *a, **kw: RedirectResponse())
    with pytest.raises(ValueError, match="跳转"):
        svc._load_image_bytes("https://img.example.com/a.jpg")


def test_detail_images_without_dimensions_still_validated() -> None:
    """detail_images（带宽高那一支）之前绕过了 https 校验，http 地址能直接进 payload。"""
    from wh_local.modules.temu_publish.schemas import DetailImageInput

    service = TemuPublishService(Path("unused.sqlite3"), client=StubCatalogClient())
    draft = _draft().model_copy(
        update={"detail_images": [DetailImageInput(url="http://insecure.example.com/a.jpg")]}
    )
    with pytest.raises(ValueError, match="https"):
        service.build_goods_payload(draft)


class RecordingAddClient(StubCatalogClient):
    """记录 goods.add 调用次数；fail_times 控制前 N 次抛网关错误。"""

    def __init__(self, fail_times: int = 0) -> None:
        super().__init__()
        self.add_calls = 0
        self.fail_times = fail_times

    def add_goods(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.add_calls += 1
        if self.add_calls <= self.fail_times:
            raise TemuGatewayError("bg.glo.goods.add", "网关超时", error_code="NETWORK_ERROR")
        return {"goodsId": 123456789}


def test_publish_blocks_duplicate_submission_but_force_overrides(tmp_path: Path) -> None:
    """同一条草稿 24 小时内重复提交要被台账挡下（接口非幂等），force=True 才放行。"""
    from wh_local.modules.temu_publish.submissions import DuplicateSubmission

    init_db(tmp_path / "runtime.sqlite3")
    client = RecordingAddClient()
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=client, owner_id="cust_a")

    first = service.publish_goods(_draft())
    assert first["submitted"] is True
    assert client.add_calls == 1

    with pytest.raises(DuplicateSubmission) as blocked:
        service.publish_goods(_draft())
    assert client.add_calls == 1, "被挡下的提交不能真的打到网关"
    assert "重复提交" in str(blocked.value)
    assert blocked.value.previous_status == "success"

    # 用户确认要重复上架
    service.publish_goods(_draft(), force=True)
    assert client.add_calls == 2


def test_publish_failure_does_not_block_retry(tmp_path: Path) -> None:
    """失败（含超时）要回写台账为 failed，否则用户重试会被自己的失败记录挡住。"""
    init_db(tmp_path / "runtime.sqlite3")
    client = RecordingAddClient(fail_times=1)
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=client, owner_id="cust_a")

    with pytest.raises(TemuGatewayError):
        service.publish_goods(_draft())
    # 失败之后重试必须放行
    service.publish_goods(_draft())
    assert client.add_calls == 2


def test_duplicate_detection_is_per_account_and_fingerprint(tmp_path: Path) -> None:
    """台账按账号隔离；改价格就是另一件商品，不该被挡。"""
    init_db(tmp_path / "runtime.sqlite3")
    client = RecordingAddClient()
    root = TemuPublishService(tmp_path / "runtime.sqlite3", client=client)
    alice = root.with_owner("cust_alice")
    bob = root.with_owner("cust_bob")

    alice.publish_goods(_draft())
    bob.publish_goods(_draft())  # 另一个账号不受影响
    assert client.add_calls == 2

    from wh_local.modules.temu_publish.submissions import draft_fingerprint

    cheaper = _draft().model_copy(deep=True)
    cheaper.skcs[0].skus[0].supplier_price = 999
    assert draft_fingerprint(cheaper) != draft_fingerprint(_draft())


def test_gateway_auth_errors_carry_actionable_hint() -> None:
    """CLIENT_TOKEN_INVALID 要说清是「服务商网关凭据」而不是店铺 token，并给排查方向。"""
    from wh_local.modules.temu_publish.router import _gateway_message

    text = _gateway_message("CLIENT_TOKEN_INVALID", "client_id or token is invalid")
    assert text.startswith("Temu 拒绝了这次提交：client_id or token is invalid")
    assert "CLIENT_TOKEN_INVALID" in text
    # 文案里不能出现供应商名字（用户要求界面上不显示）
    assert "服务商网关" in text and "access_token" in text
    assert "盈銮" not in text

    net = _gateway_message("NETWORK_ERROR", "网关网络请求失败：SSLEOFError")
    assert "系统代理" in net and "WH_TEMU_PROXY" in net
    assert "盈銮" not in net

    # 没登记的错误码不加多余的话，保持上游原话
    plain = _gateway_message("SOME_NEW_CODE", "boom")
    assert plain == "Temu 拒绝了这次提交：boom（错误码 SOME_NEW_CODE）"


def test_gateway_error_6000002_points_at_the_real_cause() -> None:
    """6000002「货品类目属性更新」的提示不能再说「属性表过时」。

    2026-11-03 最终定位：桌布 / 化妆包这类没有销售属性的类目，真因是补的规格
    不是该类目的合法值（id 全 0 的占位、或值不在「颜色」预设值里），
    跟缓存新旧无关 —— 清了缓存重拉同一份属性表照样报。
    所以文案要引导「先看 Temu 点名了哪个属性」，而不是让用户去点「刷新类目树」白折腾。
    """
    from wh_local.modules.temu_publish.router import _CATEGORY_ATTRS_CHANGED, _gateway_message

    assert _CATEGORY_ATTRS_CHANGED == "6000002"
    text = _gateway_message("6000002", "货品类目属性更新，请刷新页面后重新创建商品")
    assert "Temu 拒绝了这次提交：货品类目属性更新" in text
    assert "6000002" in text
    assert "属性表已过时" not in text, "这个结论已经被证伪，别再误导排查"
    assert "重新选" in text
    assert "真实规格" in text


def test_unbinding_keeps_gateway_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """解绑只清店铺授权：服务商 client_id / token 是预置的，换店还要用，必须留着。"""
    _clear_temu_env(monkeypatch)
    init_db(tmp_path / "runtime.sqlite3")
    service = TemuPublishService(
        tmp_path / "runtime.sqlite3", client=StubCatalogClient(), owner_id="cust_alice"
    )
    service.save_settings(
        TemuSettingsUpdate(yl_client="yl-client", yl_token="yl-token", access_token="shop-a"),
        "cust_alice",
    )
    assert service.settings_status()["configured"]["ready"] is True

    status = service.clear_settings("cust_alice")["configured"]
    assert status["access_token"] is False
    assert status["yl_client"] is True and status["yl_token"] is True
    assert status["ready"] is False

    # 重新授权只补店铺 token 就够了，不用再让服务商发一遍 client/token
    service.save_settings(TemuSettingsUpdate(access_token="shop-b"), "cust_alice")
    assert service.settings_status()["configured"]["ready"] is True


def test_zero_stock_is_allowed_but_warned(tmp_path: Path) -> None:
    """库存 0 是合法输入（先建品后补库存），不能拦；但要在 warnings 里提醒不可售。"""
    from wh_local.modules.temu_publish.schemas import SkcInput, SkuInput, SkuSpecInput

    spec = SkuSpecInput(parent_spec_id=1001, parent_spec_name="颜色", spec_id=2001, spec_name="Red")

    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=StubCatalogClient())
    built = service.build_goods_payload(_draft().model_copy(deep=True, update={"skcs": [
        SkcInput(
            ext_code="SKC-1",
            preview_image_urls=["https://img.example.com/skc.jpg"],
            main_specs=[spec],
            skus=[SkuInput(
                ext_code="SKU-1",
                supplier_price=1299,
                stock=0,
                warehouse_id="WH-07346074419333874",
                weight_mg=200000,
                length_mm=200,
                width_mm=150,
                height_mm=30,
                specs=[spec],
            )],
        )
    ]}))

    sku = built["payload"]["productSkcReqs"][0]["productSkuReqs"][0]
    assert sku["productSkuStockQuantityReq"]["warehouseStockQuantityReqs"][0]["targetStockAvailable"] == 0
    assert any("库存为 0" in item for item in built["warnings"])


class LinkedCatalogClient:
    """attributes() 返回带父子联动的目录（供联动测试用）。"""

    def attributes(self, cat_id: int, supported_type: int = 1) -> dict[str, Any]:
        return _linked_catalog()


def _linked_catalog() -> dict[str, Any]:
    """父子联动样例：电池属性(2001) → 可充电电池(2002) / 不可充电电池(2003)。"""
    return {
        "properties": [
            {"pid": 2001, "name": "电池属性", "templatePid": 5001, "required": False,
             "values": [{"vid": 1, "value": "可充电电池"}, {"vid": 2, "value": "不可充电电池"}]},
            {"pid": 2002, "name": "可充电电池", "templatePid": 5002, "parentTemplatePid": 5001,
             "required": True, "values": [{"vid": 10, "value": "锂电池"}, {"vid": 11, "value": "镍系电池"}],
             "templatePropertyValueParentList": [{"parentVidList": [1], "vidList": [10, 11]}]},
            {"pid": 2003, "name": "不可充电电池", "templatePid": 5003, "parentTemplatePid": 5001,
             "required": True, "values": [{"vid": 20, "value": "AA电池"}],
             "templatePropertyValueParentList": [{"parentVidList": [2], "vidList": [20]}]},
            {"pid": 2004, "name": "材料", "templatePid": 5004, "required": False,
             "values": [{"vid": 30, "value": "塑料"}]},
        ]
    }


def test_linked_properties_follow_parent_selection() -> None:
    """Temu 属性联动：父级没选/取值不匹配时，子属性不该出现也不必填。"""
    from wh_local.modules.temu_publish.schemas import GoodsPropertyInput
    from wh_local.modules.temu_publish.service import _resolve_applicable_properties

    catalog = _linked_catalog()
    applicable, dropped = _resolve_applicable_properties(
        catalog, [GoodsPropertyInput(pid=2001, vid=1)]
    )
    # 适用集合按 (pid, refPid) 索引：同 pid 挂多条属性时只按 pid 会互相覆盖
    assert (2002, 0) in applicable and (2003, 0) not in applicable
    assert dropped == ["不可充电电池"]

    # 父级没选 → 两个子属性都不出现
    applicable, dropped = _resolve_applicable_properties(catalog, [])
    assert (2002, 0) not in applicable and (2003, 0) not in applicable
    assert set(dropped) == {"可充电电池", "不可充电电池"}
    # 无联动规则的属性永远在
    assert (2001, 0) in applicable and (2004, 0) in applicable


def test_same_pid_multiple_refpid_not_confused() -> None:
    """同一个 pid 挂多条属性时，联动判断不能互相顶掉。

    真实样例（女装连衣裙 39107）：面料纹理1 / 面料纹理2 都是 pid 2054、refPid 6926/6927；
    面料克重1 / 面料克重2 / 里料克重 都是 pid 2052、refPid 6930/6931/6934。
    只按 pid 索引时，选了「面料纹理1=光面」会让「面料纹理2」也被当成光面，
    面料克重2 被误判成必填 —— 而它父属性根本没选。
    """
    from wh_local.modules.temu_publish.schemas import GoodsPropertyInput
    from wh_local.modules.temu_publish.service import (
        _property_applicable,
        _resolve_applicable_properties,
    )

    catalog = {
        "properties": [
            {"pid": 2054, "refPid": 6926, "name": "面料纹理1", "templatePid": 1001,
             "required": True, "values": [{"vid": 161198, "value": "光面"}]},
            {"pid": 2054, "refPid": 6927, "name": "面料纹理2", "templatePid": 1002,
             "required": False, "values": [{"vid": 161198, "value": "光面"}]},
            {"pid": 2052, "refPid": 6930, "name": "面料克重1（g/m²)", "templatePid": 1003,
             "required": True, "values": [], "valueUnit": ["g/㎡"],
             "parentTemplatePid": 1001,
             "showCondition": [{"parentRefPid": 6926, "parentVids": [161198]}]},
            {"pid": 2052, "refPid": 6931, "name": "面料克重2（g/m²)", "templatePid": 1004,
             "required": True, "values": [], "valueUnit": ["g/㎡"],
             "parentTemplatePid": 1002,
             "showCondition": [{"parentRefPid": 6927, "parentVids": [161198]}]},
        ]
    }
    # 只选了「面料纹理1=光面」：面料克重1 适用，面料克重2 不适用
    applicable, dropped = _resolve_applicable_properties(
        catalog, [GoodsPropertyInput(pid=2054, ref_pid=6926, vid=161198)]
    )
    assert _property_applicable(applicable, 2052, 6930) is True
    assert _property_applicable(applicable, 2052, 6931) is False
    assert "面料克重2（g/m²)" in dropped


def test_goods_list_accepts_both_page_size_spellings_without_leaking() -> None:
    """分页参数两种写法都收，但请求体里只能出现 pageSize。

    调用方既有传 `pageSize` 的也有传 `page_size` 的；以前只 pop pageSize，
    `page_size` 会被「其余键原样透传」塞进请求体 —— 同一个请求里两个分页字段，
    网关要么当未知字段、要么直接判参数非法。
    """
    from wh_local.modules.temu_publish.client import TemuProductClient

    captured: dict[str, Any] = {}

    def fake_call(api_type: str, payload: dict[str, Any] | None = None, **_kwargs: Any) -> dict[str, Any]:
        captured["api"] = api_type
        captured["payload"] = payload
        return {"data": [], "totalCount": 0}

    client = TemuProductClient.__new__(TemuProductClient)
    client.call = fake_call  # type: ignore[method-assign]

    client.goods_list(page=2, page_size=50, skcExtCode="A1")
    payload = captured["payload"] or {}
    assert captured["api"] == "bg.glo.goods.list.get"
    assert payload["page"] == 2
    assert payload["pageSize"] == 50
    assert "page_size" not in payload, f"别名漏进请求体了：{payload}"
    assert payload["skcExtCode"] == "A1"

    # camelCase 写法同样生效
    client.goods_list(page=1, pageSize=10)
    assert (captured["payload"] or {})["pageSize"] == 10


def test_required_check_covers_every_property_with_same_pid() -> None:
    """必填校验要遍历属性表全表，不能遍历「按 pid 建的字典」。

    同 pid 多条时字典只剩最后一条：面料纹理1（required）被面料纹理2（选填）顶掉，
    「面料纹理1 必填但没填」就查不出来，一路走到网关才被拒。
    """
    from wh_local.modules.temu_publish.service import _validate_against_catalog

    catalog: dict[str, Any] = {
        "properties": [
            {"pid": 2054, "refPid": 6926, "name": "面料纹理1", "required": True,
             "values": [{"vid": 31, "value": "光面"}]},
            {"pid": 2054, "refPid": 6927, "name": "面料纹理2", "required": False,
             "values": [{"vid": 31, "value": "光面"}]},
        ]
    }
    # 什么都没填 → 必须点名「面料纹理1」
    draft = GoodsDraftInput.model_construct(properties=[])
    with pytest.raises(ValueError) as empty_exc:
        _validate_against_catalog(draft, catalog, [])
    assert "面料纹理1" in str(empty_exc.value)

    # 只填了面料纹理2（同 pid 的另一条）→ 面料纹理1 仍算没填
    draft = GoodsDraftInput.model_construct(
        properties=[GoodsPropertyInput(pid=2054, ref_pid=6927, vid=31)]
    )
    with pytest.raises(ValueError) as partial_exc:
        _validate_against_catalog(draft, catalog, [])
    assert "面料纹理1" in str(partial_exc.value)


def test_inapplicable_property_is_dropped_from_payload(tmp_path: Path) -> None:
    """填了不适用属性的值也不能进 payload，并给出 warning（Temu 会判属性不合法）。"""
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=LinkedCatalogClient())

    # 父级选「可充电」：2002 必填且适用，2003 不适用（即使填了也不能进 payload）
    built = service.build_goods_payload(
        _draft(
            properties=[
                GoodsPropertyInput(pid=2001, vid=1),
                GoodsPropertyInput(pid=2002, vid=10),
                GoodsPropertyInput(pid=2003, vid=20),
            ]
        )
    )
    pids = [item["pid"] for item in built["payload"]["productPropertyReqs"]]
    assert 2002 in pids and 2003 not in pids
    assert any("不适用" in item for item in built["warnings"])

    # 反向：父级选「不可充电」时，只填 2003 就行，不该再要求 2002
    ok = service.build_goods_payload(
        _draft(properties=[GoodsPropertyInput(pid=2001, vid=2), GoodsPropertyInput(pid=2003, vid=20)])
    )
    pids = [item["pid"] for item in ok["payload"]["productPropertyReqs"]]
    assert 2003 in pids and 2002 not in pids


class LookupClient(StubCatalogClient):
    """goods.add 抛网络错误；goods_list 按开关返回「店里已经有这件商品」。"""

    def __init__(self, *, found: bool) -> None:
        super().__init__()
        self.found = found
        self.add_calls = 0
        self.list_calls = 0

    def add_goods(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.add_calls += 1
        raise TemuGatewayError("bg.glo.goods.add", "网关超时", error_code="NETWORK_ERROR")

    def goods_list(self, **filters: Any) -> dict[str, Any]:
        self.list_calls += 1
        if not self.found:
            return {"data": [], "totalCount": 0}
        import time as _time

        return {
            "data": [
                {
                    "productId": 987654321,
                    "productName": "Cartoon cosmetic bag",
                    "extCode": filters.get("skcExtCode"),
                    "createdAt": int(_time.time() * 1000),
                    "skcSiteStatus": 0,
                }
            ],
            "totalCount": 1,
        }


def test_publish_timeout_is_confirmed_by_goods_lookup(tmp_path: Path) -> None:
    """网络中断但店铺里已经能查到这件商品 → 按「已创建」处理，别让用户重试。"""
    init_db(tmp_path / "runtime.sqlite3")
    client = LookupClient(found=True)
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=client, owner_id="cust_a")

    result = service.publish_goods(_draft())

    assert result["submitted"] is True
    assert result["confirmed_by_lookup"] is True
    assert result["matched_goods"]["product_id"] == "987654321"
    assert any("已经查到这件商品" in item for item in result["warnings"])
    assert client.list_calls >= 1

    # 台账记成成功 → 再发会被拦（避免重复上架）
    from wh_local.modules.temu_publish.submissions import DuplicateSubmission

    with pytest.raises(DuplicateSubmission):
        service.publish_goods(_draft())


def test_publish_network_error_without_match_still_fails(tmp_path: Path) -> None:
    """反查不到就不能当成功：照常抛错、台账记 failed，允许用户重试。"""
    init_db(tmp_path / "runtime.sqlite3")
    client = LookupClient(found=False)
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=client, owner_id="cust_a")

    with pytest.raises(TemuGatewayError):
        service.publish_goods(_draft())
    assert client.list_calls >= 1
    # 失败不挡重试（这次会再打一次，仍然抛网络错误）
    with pytest.raises(TemuGatewayError):
        service.publish_goods(_draft())
    assert client.add_calls == 2


class SearchClient(StubCatalogClient):
    """goods_list 返回一条商品；记录收到的过滤参数。"""

    def __init__(self) -> None:
        super().__init__()
        self.filters: list[dict[str, Any]] = []

    def goods_list(self, **filters: Any) -> dict[str, Any]:
        self.filters.append(filters)
        return {
            "data": [
                {
                    "productId": 55894695437,
                    "productName": "Sage tablecloth",
                    "extCode": "SKC-RED",
                    "mainImageUrl": "https://img.example.com/a.jpg",
                    "createdAt": 1793000000000,
                    "skcSiteStatus": 0,
                    "leafCat": {"catId": 10498, "catName": "桌布"},
                    "productSkuSummaries": [{"extCode": "SKU-1"}, {"extCode": "SKU-2"}],
                }
            ],
            "totalCount": 1,
        }


def test_search_goods_compacts_items_and_caches(tmp_path: Path) -> None:
    """店铺商品查询：按 SKC 货号过滤、压成前端字段，并走 180 秒短缓存。"""
    client = SearchClient()
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=client)

    first = service.search_goods(ext_code="SKC-RED")
    assert first["total"] == 1
    item = first["items"][0]
    assert item["product_id"] == "55894695437"
    assert item["skc_ext_code"] == "SKC-RED"
    assert item["category"] == "桌布"
    assert item["sku_codes"] == ["SKU-1", "SKU-2"]
    assert client.filters[0]["skcExtCode"] == "SKC-RED"

    service.search_goods(ext_code="SKC-RED")  # 命中缓存，不再打网关
    assert len(client.filters) == 1


def test_dimension_check_works_without_mall_id(tmp_path: Path) -> None:
    """尺寸图检测不再强制要 mallId（2026-11-05 实测：只传 imageUrl 就能提交）。

    以前强制要求 mallId，而 mallId 根本拿不到（`bg.mall.info.get` 在 scope 里
    但实际回「Client is not allowed」），等于这个接口永远用不起来。
    """
    captured: dict[str, Any] = {}

    class DimClient(SearchClient):
        def dimension_image_check(
            self, image_url: str, *, mall_id: str = "", cat_id: int | None = None, class_id: int | None = None
        ) -> dict[str, Any]:
            captured["image_url"] = image_url
            captured["mall_id"] = mall_id
            return {"errorCode": 0, "uniqueId": "123", "errorMsg": "success"}

    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=DimClient())
    result = service.dimension_check("https://img.example.com/a.jpg")
    assert result["uniqueId"] == "123"
    assert captured["mall_id"] == ""


def test_dimension_result_polls_with_unique_id_not_task_id() -> None:
    """轮询参数必须是 uniqueId。

    传 taskId 网关不报错，而是永远回「5000000 图片检测中，请重试」——
    这就是长期看不到结果的真因（同一张图两个名字各查一次对比确认）。
    """
    from wh_local.modules.temu_publish.client import TemuProductClient

    captured: dict[str, Any] = {}

    def fake_call(api_type: str, payload: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        captured["api"] = api_type
        captured["payload"] = payload
        captured["gateway"] = kwargs.get("gateway")
        return {"errorCode": 0}

    client = TemuProductClient.__new__(TemuProductClient)
    client.call = fake_call  # type: ignore[method-assign]

    client.dimension_image_check_result("999")
    assert captured["api"] == "bg.algo.dimension.image.check.result"
    assert captured["payload"] == {"uniqueId": "999"}
    assert captured["gateway"] == "CN"
    assert "taskId" not in (captured["payload"] or {})


def test_goods_detail_router_returns_payload(tmp_path: Path) -> None:
    """GET /goods/{id} 走通（用桩客户端，不触网）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from wh_local.modules.temu_publish.router import create_temu_publish_router
    from wh_local.session import Actor, actor_from_authorization

    class DetailClient(SearchClient):
        def goods_detail(self, product_id: int) -> dict[str, Any]:
            return {"productId": product_id, "productName": "Sage tablecloth"}

    db_path = tmp_path / "runtime.sqlite3"
    init_db(db_path)
    app = FastAPI()
    app.include_router(create_temu_publish_router(db_path))
    app.dependency_overrides[actor_from_authorization] = lambda: Actor(
        id="cust_a", username="a", role="admin"
    )
    http = TestClient(app)
    # 路由内部的 service 是懒建的，这里只验证路径存在且未登录/无凭证时会走统一错误处理
    response = http.get("/api/temu-publish/goods/55894695437")
    assert response.status_code in (200, 400, 422, 502, 503)


class LowStockClient(StubCatalogClient):
    """商品列表桩：两条 0 库存、一条有货；记录翻页参数。"""

    def __init__(self) -> None:
        super().__init__()
        self.pages: list[int] = []

    def goods_list(self, **filters: Any) -> dict[str, Any]:
        page = int(filters.get("page") or 1)
        self.pages.append(page)
        if page > 1:
            return {"totalCount": 3, "data": []}
        return {
            "totalCount": 3,
            "data": [
                {
                    "productId": 111,
                    "productName": "Zero stock A",
                    "extCode": "SKC-A",
                    "leafCat": {"catName": "桌布"},
                    "productSkuSummaries": [
                        {"productSkuId": 1, "extCode": "SKU-A1", "virtualStock": 0,
                         "productSkuSpecList": [{"specName": "Red"}]},
                        {"productSkuId": 2, "extCode": "SKU-A2", "virtualStock": 0},
                    ],
                },
                {
                    "productId": 222,
                    "productName": "Mixed stock B",
                    "extCode": "SKC-B",
                    "productSkuSummaries": [
                        {"productSkuId": 3, "extCode": "SKU-B1", "virtualStock": 0},
                        {"productSkuId": 4, "extCode": "SKU-B2", "virtualStock": 7},
                    ],
                },
                {
                    "productId": 333,
                    "productName": "In stock C",
                    "extCode": "SKC-C",
                    "productSkuSummaries": [
                        {"productSkuId": 5, "extCode": "SKU-C1", "virtualStock": 12},
                    ],
                },
            ],
        }


def test_low_stock_goods_lists_zero_stock_skus(tmp_path: Path) -> None:
    """待补库存清单：只列有 0 库存 SKU 的商品，带上每个 SKU 的库存与规格。"""
    client = LowStockClient()
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=client)

    result = service.low_stock_goods()

    assert result["total"] == 3
    assert [item["product_id"] for item in result["items"]] == ["111", "222"]
    assert result["zero_sku_count"] == 3  # A1 A2 B1
    # 改库存接口没开通，必须如实告诉前端「只能给清单」
    assert result["can_update_stock"] is False

    first = result["items"][0]
    assert first["category"] == "桌布"
    assert first["stock_total"] == 0
    assert first["skus"][0] == {
        "sku_id": "1",
        "ext_code": "SKU-A1",
        "stock": 0,
        "specs": "Red",
    }
    # 混合库存的商品：总库存照实统计，0 库存 SKU 只算那一个
    mixed = result["items"][1]
    assert mixed["stock_total"] == 7
    assert mixed["zero_stock_sku_count"] == 1


def test_low_stock_goods_stops_at_limit(tmp_path: Path) -> None:
    """limit=1 只取第一个就停，不要为了凑数把整店翻完。"""
    client = LowStockClient()
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=client)

    result = service.low_stock_goods(limit=1)

    assert len(result["items"]) == 1
    assert client.pages == [1]


def test_sku_stock_falls_back_when_virtual_stock_missing() -> None:
    """列表里没有 virtualStock 时退回 stock 字段；都没有按 0 算（不能当未知）。"""
    from wh_local.modules.temu_publish.service import _compact_goods_item

    item = _compact_goods_item(
        {
            "productId": 1,
            "productSkuSummaries": [
                {"productSkuId": 1, "extCode": "A", "stock": 9},
                {"productSkuId": 2, "extCode": "B"},
                {"productSkuId": 3, "extCode": "C", "virtualStock": None, "targetStockAvailable": 4},
            ],
        }
    )
    assert [sku["stock"] for sku in item["skus"]] == [9, 0, 4]
    assert item["stock_total"] == 13


def test_low_stock_route_is_not_shadowed_by_product_id(tmp_path: Path) -> None:
    """`/goods/low-stock` 必须排在 `/goods/{product_id}` 之前，否则会被当成 product_id → 422。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from wh_local.modules.temu_publish.router import create_temu_publish_router
    from wh_local.session import Actor, actor_from_authorization

    db_path = tmp_path / "runtime.sqlite3"
    init_db(db_path)
    app = FastAPI()
    app.include_router(create_temu_publish_router(db_path))
    app.dependency_overrides[actor_from_authorization] = lambda: Actor(
        id="cust_a", username="a", role="admin"
    )

    response = TestClient(app).get("/api/temu-publish/goods/low-stock")
    assert response.status_code != 422, response.text
    assert response.status_code in (200, 400, 502, 503)


class FakeShopProbe:
    """代替 TemuProductClient：只实现 token_info，token 以 bad 开头则报无效。"""

    def __init__(self, credentials: Any, timeout: int = 30, max_attempts: int = 1) -> None:
        self.credentials = credentials
        self.timeout = timeout
        self.max_attempts = max_attempts

    def token_info(self) -> dict[str, Any]:
        token = str(getattr(self.credentials, "access_token", ""))
        if token.startswith("bad"):
            raise TemuGatewayError(
                "bg.open.accesstoken.info.get.global",
                "client_id or token is invalid",
                error_code="CLIENT_TOKEN_INVALID",
            )
        return {"mallName": f"Shop-{token[-4:]}", "apiScopeList": []}


def _shop_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from wh_local.modules.temu_publish import service as temu_service

    monkeypatch.setattr(temu_service, "TemuProductClient", FakeShopProbe)
    init_db(tmp_path / "runtime.sqlite3")
    service = temu_service.TemuPublishService(
        tmp_path / "runtime.sqlite3", client=StubCatalogClient(), owner_id="cust_a"
    )
    service.save_settings(TemuSettingsUpdate(yl_client="yl-c", yl_token="yl-t"), "cust_a")
    return service


def test_add_shop_verifies_then_saves_and_can_switch_without_retyping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """多店铺：验证通过才保存；换店只要点一下，不用再贴 token。"""
    service = _shop_service(tmp_path, monkeypatch)

    first = service.add_shop("aaa-token-1111", "", "cust_a")
    assert first["verified"] is True
    assert first["mall_name"] == "Shop-1111"
    assert first["configured"]["access_token"] is True
    assert len(first["shops"]) == 1
    assert first["shops"][0]["active"] is True
    assert first["shops"][0]["masked_token"].endswith("1111")

    second = service.add_shop("bbb-token-2222", "", "cust_a")
    assert len(second["shops"]) == 2
    active = [shop for shop in second["shops"] if shop["active"]]
    assert len(active) == 1 and active[0]["mall_name"] == "Shop-2222"
    assert service.store.load().access_token == "bbb-token-2222"

    # 切回第一家：库里存的 token 直接拿来用，不再要求输入
    other = [shop for shop in second["shops"] if not shop["active"]][0]
    back = service.activate_shop(other["id"], "cust_a")
    assert [shop for shop in back["shops"] if shop["active"]][0]["mall_name"] == "Shop-1111"
    assert service.store.load().access_token == "aaa-token-1111"


def test_add_shop_rejects_invalid_token_without_saving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """token 验证不过就不落库：否则店铺列表里会留一条永远发不出去的坏记录。"""
    service = _shop_service(tmp_path, monkeypatch)

    with pytest.raises(TemuGatewayError):
        service.add_shop("bad-token-9999", "", "cust_a")

    status = service.settings_status()
    assert status["shops"] == []
    assert status["configured"]["access_token"] is False


def test_add_same_shop_twice_does_not_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一个 token 保存两次只留一条（店铺 id 由 token 哈希得出）。"""
    service = _shop_service(tmp_path, monkeypatch)

    service.add_shop("ccc-token-3333", "", "cust_a")
    again = service.add_shop("ccc-token-3333", "", "cust_a")

    assert len(again["shops"]) == 1
    assert again["shops"][0]["active"] is True


def test_remove_active_shop_stops_using_it_but_keeps_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """删掉当前店铺：立即停用（access_token 清空），其它店铺仍在列表里等切换。"""
    service = _shop_service(tmp_path, monkeypatch)
    service.add_shop("ddd-token-4444", "", "cust_a")
    second = service.add_shop("eee-token-5555", "", "cust_a")
    active = [shop for shop in second["shops"] if shop["active"]][0]

    after = service.remove_shop(active["id"], "cust_a")

    assert len(after["shops"]) == 1
    assert after["shops"][0]["mall_name"] == "Shop-4444"
    assert after["shops"][0]["active"] is False
    assert after["configured"]["access_token"] is False


def test_multi_pack_only_sent_when_more_than_one(tmp_path: Path) -> None:
    """多件装：>1 才发 productSkuMultiPackReq（结构与官方成功案例一致），1 不发。"""
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=StubCatalogClient())

    single = service.build_goods_payload(_draft())
    sku = single["payload"]["productSkcReqs"][0]["productSkuReqs"][0]
    assert "productSkuMultiPackReq" not in sku

    packed = _draft().model_copy(deep=True)
    packed.skcs[0].skus[0].pieces_per_pack = 12
    built = service.build_goods_payload(packed)
    sku = built["payload"]["productSkcReqs"][0]["productSkuReqs"][0]
    assert sku["productSkuMultiPackReq"] == {
        "pieceUnitCode": 1,
        "numberOfPieces": 1,
        "pieceNewUnitCode": 1,
        "numberOfPiecesNew": 12,
        "skuClassification": 1,
        "productSkuNetContentReq": {},
    }


class SizeChartClient(StubCatalogClient):
    """size_charts 按开关返回空/非空，记录调用次数（验证缓存）。

    `elements` 非空 = 这个类目属于服装/鞋类（需要尺码表）；
    空 = 宠物/家居这类（网关回「没有找到当前类目对应的尺码组」）。
    """

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        elements: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__()
        self.rows = rows or []
        self.elements = elements or []
        self.calls = 0

    def size_chart_meta(self, cat_id: int) -> dict[str, Any]:
        if not self.elements:
            raise TemuGatewayError(
                "bg.glo.goods.sizecharts.meta.get", "没有找到当前类目对应的尺码组"
            )
        return {
            "allowRange": False,
            "sizeSpecMeta": {
                "elementList": self.elements,
                "groupList": [{"id": 1, "name": "尺码", "unnecessary": False}],
            },
        }

    def size_chart_settings(self, cat_id: int) -> dict[str, Any]:
        return {"sizeList": ["S", "M", "L"], "mappingContent": {"records": [], "meta": {}}}

    def size_charts(self, cat_id: int, offset: int = 0, page_size: int = 20) -> dict[str, Any]:
        self.calls += 1
        return {
            "offset": offset,
            "pageSize": page_size,
            "totalCount": len(self.rows),
            "sizeSpecDataList": self.rows or None,
        }


def test_size_charts_reports_whether_category_needs_one(tmp_path: Path) -> None:
    """宠物/家居这类：网关回「没有找到尺码组」→ required=False，前端据此隐藏字段。"""
    empty = SizeChartClient()
    service = TemuPublishService(tmp_path / "a.sqlite3", client=empty)
    info = service.size_charts(1634)
    assert info["cat_id"] == 1634
    assert info["required"] is False
    assert info["has_size_chart"] is False
    assert info["count"] == 0

    # 同一类目第二次直接命中负缓存，不再打网关
    service.size_charts(1634)
    assert empty.calls == 0  # meta 就判定完了，根本没走到 size_charts

    with_rows = SizeChartClient(
        [{"businessId": 1}, {"businessId": 2}],
        elements=[{"id": 10002, "name": "胸围全围", "necessary": True}],
    )
    service2 = TemuPublishService(tmp_path / "b.sqlite3", client=with_rows)
    info2 = service2.size_charts(24376)
    assert info2["required"] is True
    assert info2["has_size_chart"] is True
    assert info2["count"] == 2
    # 结构未知：原样带回去，不猜字段名
    assert info2["raw"][0] == {"businessId": 1}


def test_size_charts_clothing_category_without_table(tmp_path: Path) -> None:
    """服装类目「需要尺码表」但店里一张都没建 —— 这正是新店的真实状态。

    以前只看 `sizecharts.get` 有没有返回，会把这种情况判成「不需要尺码表」，
    于是发布时不带 sizeTemplateIds，服装商品发出去缺尺码表。
    """
    client = SizeChartClient(
        rows=[],
        elements=[
            {"id": 10002, "name": "胸围全围", "necessary": True},
            {"id": 10010, "name": "裙长", "necessary": True},
            {"id": 10001, "name": "肩宽", "necessary": False},
        ],
    )
    service = TemuPublishService(tmp_path / "d.sqlite3", client=client)
    info = service.size_charts(39107)

    assert info["required"] is True
    assert info["has_size_chart"] is False
    assert info["required_elements"] == ["胸围全围", "裙长"]
    assert [item["name"] for item in info["elements"]] == ["胸围全围", "裙长", "肩宽"]
    # 可选尺码来自 settings，建表/选码时要用
    assert info["sizes"] == ["S", "M", "L"]
    # 建表接口参数结构还没打通，前端据此提示「去后台建」
    assert info["can_create"] is False


def test_size_charts_without_category_short_circuits(tmp_path: Path) -> None:
    """没选类目（cat_id=0）不该打网关。"""
    client = SizeChartClient([{"businessId": 1}])
    service = TemuPublishService(tmp_path / "c.sqlite3", client=client)
    assert service.size_charts(0)["has_size_chart"] is False
    assert client.calls == 0


def test_size_charts_route_exists(tmp_path: Path) -> None:
    """/categories/{id}/size-charts 路由存在（不是 404/422；无凭证时走统一错误处理）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from wh_local.modules.temu_publish.router import create_temu_publish_router
    from wh_local.session import Actor, actor_from_authorization

    db_path = tmp_path / "runtime.sqlite3"
    init_db(db_path)
    app = FastAPI()
    app.include_router(create_temu_publish_router(db_path))
    app.dependency_overrides[actor_from_authorization] = lambda: Actor(
        id="cust_a", username="a", role="admin"
    )
    response = TestClient(app).get("/api/temu-publish/categories/1634/size-charts")
    assert response.status_code != 404, response.text
    assert response.status_code in (200, 400, 503)


def test_property_payload_omits_empty_optional_fields(tmp_path: Path) -> None:
    """商品属性只发真实成功案例里有的 7 个字段。

    numberInputValue / valueGroupId / valueGroupName 在 2026-09-27 与 2026-10-26
    两次真实上架的 payload 里都不存在，空值一起发有可能被判「属性不合法」，
    所以只在真有值时带上。
    """
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=StubCatalogClient())

    payload = service.build_goods_payload(_draft())["payload"]
    item = payload["productPropertyReqs"][0]

    assert item == {
        "pid": 1,
        "refPid": 12,
        "templatePid": 569789,
        "vid": 10,
        "propName": "材质",
        "propValue": "PU",
        "valueUnit": "",
    }


def test_property_payload_keeps_number_value_when_present(tmp_path: Path) -> None:
    """数值型属性（单位 cm 等）必须带上 numberInputValue，否则 Temu 让「请输入数值」。"""
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=StubCatalogClient())
    draft = _draft(
        properties=[
            GoodsPropertyInput(
                pid=1, vid=0, prop_name="长度", prop_value="20", number_input_value="20", value_unit="cm"
            )
        ]
    )

    item = service.build_goods_payload(draft)["payload"]["productPropertyReqs"][0]

    assert item["numberInputValue"] == "20"
    assert item["valueUnit"] == "cm"
    assert "valueGroupId" not in item


def test_number_property_without_vid_is_not_rejected(tmp_path: Path) -> None:
    """数值型属性没有 vid（靠文本传值），不能按「vid 不在可选值内」拒掉。"""
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=StubCatalogClient())
    draft = _draft(
        properties=[GoodsPropertyInput(pid=1, vid=0, prop_name="材质", prop_value="20cm")]
    )

    payload = service.build_goods_payload(draft)["payload"]

    assert payload["productPropertyReqs"][0]["propValue"] == "20cm"


def test_property_without_vid_and_text_is_rejected(tmp_path: Path) -> None:
    """既没选值也没填文本：提前拦下来，别让 Temu 回一句看不懂的话。"""
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=StubCatalogClient())

    with pytest.raises(ValueError, match="既没有选值也没有填数值"):
        service.build_goods_payload(_draft(properties=[GoodsPropertyInput(pid=1, vid=0)]))


class NoSaleCatalogClient:
    """类目属性表里一个销售属性都没有的类目（如「桌布/亚麻布」）。"""

    #: 记录被要求创建的兜底规格，断言用
    created_specs: list[tuple[int, str]] = []

    def attributes(self, cat_id: int, supported_type: int = 1) -> dict[str, Any]:
        return {
            "properties": [
                {
                    "pid": 112,
                    "name": "形状",
                    "refPid": 1,
                    "templatePid": 569789,
                    "required": True,
                    "isSale": False,
                    "values": [{"vid": 2463, "value": "矩形"}],
                }
            ]
        }

    def ensure_default_spec(
        self, spec_name: str = "", catalog: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """真实兜底规格：颜色/默认（specId 非 0）。

        以前这里用的是 id 全 0 的「款式/默认」：Temu 的规格校验能过，但 goods.add
        会一直回 6000002「货品类目属性更新」，且跟填了哪个属性无关。
        换成真实 specId 之后同一个类目立刻发布成功（2026-11-03 实测 3 单）。
        """
        self.created_specs.append((1001, "默认"))
        return {
            "parentSpecId": 1001,
            "parentSpecName": "颜色",
            "specId": 20832,
            "specName": "默认",
        }


class NoSaleCatalogClientWithoutGateway(NoSaleCatalogClient):
    """连不上网关：兜底规格创建不出来，只能退回占位（并给出告警）。"""

    def ensure_default_spec(
        self, spec_name: str = "", catalog: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        raise RuntimeError("network unreachable")


class SaleCatalogClient(StubCatalogClient):
    """类目靠颜色/尺码区分变种（isSale=True）。"""

    def attributes(self, cat_id: int, supported_type: int = 1) -> dict[str, Any]:
        data = super().attributes(cat_id, supported_type)
        data["properties"][0]["isSale"] = True
        return data


def _single_sku_draft() -> GoodsDraftInput:
    """没有任何规格的单 SKU 草稿（类目只剩一个必填商品属性「形状」）。"""
    draft = _draft(properties=[GoodsPropertyInput(pid=112, vid=2463, prop_name="形状", prop_value="矩形")])
    skc = draft.skcs[0]
    return draft.model_copy(
        update={
            "skcs": [
                skc.model_copy(
                    update={
                        "main_specs": [],
                        "skus": [skc.skus[0].model_copy(update={"specs": []})],
                    }
                )
            ]
        }
    )


def test_single_sku_payload_for_category_without_sale_properties(tmp_path: Path) -> None:
    """没有销售属性的类目：自动补一条**真实**规格，否则 Temu 回「规格不能为空」。

    历史坑（务必保留这段注释）：以前补的是 id 全 0 的「款式/默认」，Temu 的规格校验
    全都通过、但 `goods.add` 永远回 `6000002 货品类目属性更新，请刷新页面后重新创建货品`。
    2026-11-03 用真实店铺把 12 个必填属性逐个单发，**每条都回 6000002**，说明跟属性无关；
    换成「颜色/默认」这种真实 specId 后，同一份属性表立刻发布成功（桌布 10498、桌旗 10497
    各验一单，productId 都拿到了）。所以这里必须断言 specId 非 0。
    """
    client = NoSaleCatalogClient()
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=client)

    built = service.build_goods_payload(_single_sku_draft())
    payload = built["payload"]

    decls = payload["productSpecPropertyReqs"]
    assert len(decls) == 1
    assert decls[0]["specId"] == 20832, "必须是真实 specId，0 会被 Temu 判「类目属性更新」"
    assert decls[0]["parentSpecId"] == 1001
    assert decls[0]["specName"] == "默认"
    # 名字不能空，否则 Temu 回「Reference Property Name / Base Property Value 不能为空」
    assert decls[0]["propName"] and decls[0]["specName"]
    # 确实去要了一条兜底规格（不是凭空写死）
    assert client.created_specs == [(1001, "默认")]

    skc = payload["productSkcReqs"][0]
    sku_specs = skc["productSkuReqs"][0]["productSkuSpecReqs"]
    assert sku_specs == [
        {"specId": 20832, "specName": "默认", "parentSpecId": 1001, "parentSpecName": "颜色"}
    ], "SKU 级规格列表不能为空（Temu 会报 Product SKU specification list cannot be empty）"

    # 要告诉用户我们替他补了规格，别让他以为商品真有这个属性
    assert any("没有销售属性" in w for w in built["warnings"])
    skc = payload["productSkcReqs"][0]
    assert skc["mainProductSkuSpecReqs"] == [
        {"specId": 0, "specName": "", "parentSpecId": 0, "parentSpecName": ""}
    ]
    # 单 SKU 也要把价格/库存/物流发全
    sku = skc["productSkuReqs"][0]
    assert sku["siteSupplierPrices"] == [{"siteId": 100, "supplierPrice": 1299}]
    assert sku["productSkuStockQuantityReq"]["warehouseStockQuantityReqs"][0]["targetStockAvailable"] == 20


def test_single_sku_falls_back_to_placeholder_when_gateway_unreachable(tmp_path: Path) -> None:
    """连不上网关时仍要能组装出请求体，但要明确告警「这样提交大概率失败」。"""
    service = TemuPublishService(
        tmp_path / "runtime.sqlite3", client=NoSaleCatalogClientWithoutGateway()
    )
    built = service.build_goods_payload(_single_sku_draft())
    payload = built["payload"]

    # 退回占位（id 全 0）也不是干脆不补 —— 不补的话 Temu 直接回「规格不能为空」
    decls = payload["productSpecPropertyReqs"]
    assert len(decls) == 1
    assert decls[0]["propName"] == "款式"
    assert decls[0]["specId"] == 0
    # 关键：必须告诉用户「这样大概率被拒」，而不是让他再猜几小时
    assert any("自动创建规格失败" in w for w in built["warnings"])
    assert any("6000002" in w or "货品类目属性更新" in w for w in built["warnings"])


def test_category_with_sale_properties_rejects_empty_specs(tmp_path: Path) -> None:
    """有销售属性的类目不能按单 SKU 发：提前报错，别让 Temu 回一句难懂的。

    并且**不能**去建兜底规格：那种情况下这个请求注定被本地拒掉，
    再向 Temu 发一次 spec.create 纯属浪费，还会往店铺规格库里塞用不上的值。
    """
    client = SaleCatalogClient()
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=client)

    with pytest.raises(ValueError, match="至少要给每个 SKU 填一个规格值"):
        service.build_goods_payload(_single_sku_draft())
    assert client.spec_requests == [], "有销售属性时不该调用 ensure_default_spec"


def test_multi_variant_still_requires_specs(tmp_path: Path) -> None:
    """有销售属性的类目照旧要规格（不能因为放宽 schema 就漏掉）。"""
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=SaleCatalogClient())

    payload = service.build_goods_payload(_draft())["payload"]

    assert payload["productSpecPropertyReqs"]
    assert payload["productSkcReqs"][0]["productSkuReqs"][0]["productSkuSpecReqs"]


class UnitCatalogClient(StubCatalogClient):
    """带单位的必填属性（厚度 mm / 成分比例 %）。"""

    def attributes(self, cat_id: int, supported_type: int = 1) -> dict[str, Any]:
        data = super().attributes(cat_id, supported_type)
        data["properties"][0]["valueUnit"] = ["mm"]
        data["properties"][0]["numberInputTitle"] = "厚度"
        return data


def test_unit_property_requires_numeric_value(tmp_path: Path) -> None:
    """带单位的属性光选下拉不够，Temu 要的是数值：提前拦下并说清要填什么。"""
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=UnitCatalogClient())

    with pytest.raises(ValueError, match="要填数值（单位 mm）"):
        service.build_goods_payload(_draft(properties=[GoodsPropertyInput(pid=1, vid=10)]))


def test_unit_property_with_value_passes(tmp_path: Path) -> None:
    """填了数值就正常发，并把单位写进 valueUnit。"""
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=UnitCatalogClient())
    draft = _draft(
        properties=[GoodsPropertyInput(pid=1, vid=10, prop_value="2.5", number_input_value="2.5")]
    )

    item = service.build_goods_payload(draft)["payload"]["productPropertyReqs"][0]

    assert item["propValue"] == "2.5"
    assert item["numberInputValue"] == "2.5"
    assert item["valueUnit"] == "mm"


def _library_payload(count: int = 75) -> dict[str, Any]:
    entries = []
    for index in range(count):
        entries.append(
            {
                "source": "ai" if index % 2 == 0 else "pod",
                "id": f"x:{index}",
                "title": f"Table cloth {index}" if index % 3 else f"Phone case {index}",
                "sku": f"SKU-{index:03d}",
                "category_name": "家居 > 桌布" if index % 3 == 0 else "",
                "updated_at": f"2026-10-2{index % 9} 10:00",
            }
        )
    return {
        "entries": entries,
        "warnings": ["示例告警"],
        "counts": {"ai": sum(1 for e in entries if e["source"] == "ai"), "pod": count // 2, "total": count},
    }


def test_draft_library_paginates_30_per_page() -> None:
    """默认 30 条/页，页码越界要收敛到最后一页，而不是返回空。"""
    from wh_local.modules.temu_publish.library import paginate_draft_library

    payload = _library_payload(75)

    first = paginate_draft_library(payload, page=1, page_size=30)
    assert len(first["entries"]) == 30
    assert first["total"] == 75
    assert first["pages"] == 3
    assert first["page"] == 1

    last = paginate_draft_library(payload, page=3, page_size=30)
    assert len(last["entries"]) == 15

    beyond = paginate_draft_library(payload, page=99, page_size=30)
    assert beyond["page"] == 3, "越界要夹到最后一页"
    assert len(beyond["entries"]) == 15


def test_draft_library_filters_by_source_and_query() -> None:
    from wh_local.modules.temu_publish.library import paginate_draft_library

    payload = _library_payload(75)

    ai_only = paginate_draft_library(payload, source="ai", page=1, page_size=30)
    assert all(item["source"] == "ai" for item in ai_only["entries"])
    assert ai_only["total"] == 38
    # counts 是全量的，筛选按钮上的数字不能跟着变
    assert ai_only["counts"]["total"] == 75

    searched = paginate_draft_library(payload, query="table cloth", page=1, page_size=30)
    assert searched["total"] > 0
    assert all("table cloth" in item["title"].lower() for item in searched["entries"])

    by_sku = paginate_draft_library(payload, query="sku-00", page=1, page_size=30)
    assert all(item["sku"].lower().startswith("sku-00") for item in by_sku["entries"])

    by_category = paginate_draft_library(payload, query="桌布", page=1, page_size=30)
    assert all("桌布" in item["category_name"] for item in by_category["entries"])


def test_draft_library_keeps_warnings_and_reports_page_meta() -> None:
    from wh_local.modules.temu_publish.library import paginate_draft_library

    result = paginate_draft_library(_library_payload(10), source="", query="", page=1, page_size=30)

    assert result["warnings"] == ["示例告警"]
    assert result["page_size"] == 30
    assert result["source"] == "all"
    assert result["query"] == ""


class _PodLibraryStub:
    """POD 批次桩：只实现草稿库用到的两个方法。"""

    def __init__(self, batch: dict[str, Any]) -> None:
        self._batch = batch
        self.detail_calls = 0

    def list_batches(self, actor: Any, limit: int = 6) -> dict[str, Any]:
        return {"batches": [{"id": self._batch["id"], "status": "completed", "title": self._batch["title"]}]}

    def get_batch(self, actor: Any, batch_id: str) -> dict[str, Any]:
        self.detail_calls += 1
        return self._batch


def _pod_batch(listing: dict[str, Any] | None = None) -> dict[str, Any]:
    """真实口径：listing_fields 在**批次顶层**，items 里没有这个键。"""
    return {
        "id": "b1",
        "title": "天然橡胶正方形防水桌布",
        "status": "completed",
        "updated_at": "2026-10-20T06:56:20",
        "listing_fields": listing
        if listing is not None
        else {
            "category_name": "家居 > 桌布",
            "skus": [{"name": "1", "declared_price": 150, "weight_g": 350}],
            "spec_card": {
                "display_unit": "in",
                "cells": [
                    ["SKU", "Length", "Width", "Height"],
                    ["1", "18", "10", "8"],
                ],
            },
        },
        "style_titles": [{"style_index": 1, "title": "Waterproof tablecloth"}],
        "items": [
            {
                "status": "completed",
                "style_index": 1,
                "variant_index": 1,
                "public_url": "https://img.example.com/a.png",
            }
        ],
    }


def test_pod_entry_reads_sku_weight_dimensions_from_listing_fields() -> None:
    """POD 生成结果没有 skus 字段：货号/重量/尺寸必须从 listing_fields 里取。

    这里踩过的坑：只读 item["skus"]，于是草稿库永远显示「缺 货号/重量/尺寸」。
    """
    from wh_local.modules.temu_publish.library import _collect_pod_entries
    from wh_local.session import Actor

    actor = Actor(id="cust_a", username="a", role="admin")
    entries = _collect_pod_entries(_PodLibraryStub(_pod_batch()), actor, 6)

    assert len(entries) == 1
    entry = entries[0]
    # 产品货号走导出表第 4 列那套规则，不用第 10 列的 "1"（跨批次会撞号）
    assert entry["sku"] == "POD-001"
    assert entry["weight_g"] == 350
    # 规格卡里的数字本来就是厘米，display_unit=in 只影响展示，不能换算
    assert (entry["length_cm"], entry["width_cm"], entry["height_cm"]) == ("18", "10", "8")
    assert entry["category_name"] == "家居 > 桌布"
    assert entry["declared_price"] == 150


def test_pod_entry_tolerates_missing_spec_card() -> None:
    """没有规格卡时不要抛异常，尺寸留空即可（界面会显示 — 并标缺项）。"""
    from wh_local.modules.temu_publish.library import _collect_pod_entries
    from wh_local.session import Actor

    actor = Actor(id="cust_a", username="a", role="admin")
    batch = _pod_batch(
        {
            "category_name": "家居 > 桌布",
            "skus": [{"name": "1", "declared_price": 150, "weight_g": 350}],
        }
    )
    entry = _collect_pod_entries(_PodLibraryStub(batch), actor, 6)[0]

    assert entry["sku"] == "POD-001"
    assert entry["weight_g"] == 350
    assert entry["length_cm"] is None


def test_bundled_gateway_fills_provider_credentials(tmp_path: Path, monkeypatch) -> None:
    """服务商凭据随包下发：用户装上就能用，不用手填。

    优先级：本人加密库 > 随包文件（local 覆盖 > 模板）> 环境变量。
    """
    from wh_local.modules.temu_publish import service as service_mod
    from wh_local.modules.temu_publish.service import TemuCredentialStore

    from wh_local.db import init_db

    db = tmp_path / "runtime.sqlite3"
    init_db(db)  # secret_values 表由全局建库负责

    bundle = tmp_path / "temu_gateway.json"
    bundle.write_text(
        json.dumps({"yl_client": "bundle-client", "yl_token": "bundle-token"}),
        encoding="utf-8",
    )
    local = tmp_path / "temu_gateway.local.json"
    monkeypatch.setattr(service_mod, "BUNDLED_GATEWAY_PATH", bundle)
    monkeypatch.setattr(service_mod, "BUNDLED_GATEWAY_LOCAL_PATH", local)
    for name in ("WH_TEMU_YL_CLIENT", "WH_TEMU_YL_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    store = TemuCredentialStore(db, owner_id="cust_a")
    loaded = store.load()
    assert (loaded.client, loaded.token) == ("bundle-client", "bundle-token")

    # local 覆盖优先
    local.write_text(
        json.dumps({"yl_client": "local-client"}), encoding="utf-8"
    )
    loaded = TemuCredentialStore(db, owner_id="cust_a").load()
    assert loaded.client == "local-client"
    assert loaded.token == "bundle-token", "local 缺的字段继续用模板里的"

    # 自己填过的优先于随包文件
    store.save(
        type("P", (), {
            "yl_client": "mine",
            "yl_token": "mine-token",
            "access_token": "",
            "clear_yl_client": False,
            "clear_yl_token": False,
            "clear_access_token": False,
        })(),
        actor_id="cust_a",
    )
    loaded = TemuCredentialStore(db, owner_id="cust_a").load()
    assert loaded.client == "mine"


def test_bundled_gateway_tolerates_missing_or_broken_file(tmp_path: Path, monkeypatch) -> None:
    """文件缺失/JSON 坏掉都按「没配」处理，绝不让设置页崩。"""
    from wh_local.modules.temu_publish import service as service_mod
    from wh_local.modules.temu_publish.service import TemuCredentialStore

    from wh_local.db import init_db

    db = tmp_path / "runtime.sqlite3"
    init_db(db)
    missing = tmp_path / "nope.json"
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(service_mod, "BUNDLED_GATEWAY_PATH", missing)
    monkeypatch.setattr(service_mod, "BUNDLED_GATEWAY_LOCAL_PATH", broken)
    for name in ("WH_TEMU_YL_CLIENT", "WH_TEMU_YL_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    loaded = TemuCredentialStore(db, owner_id="cust_a").load()
    assert loaded.client == "" and loaded.token == ""


def test_sync_provider_credentials_pulls_once_and_skips_when_present(tmp_path: Path, monkeypatch) -> None:
    """服务器下发：本地空时拉一次并落库；值没变时不再重复写库。

    （值变了要覆盖 —— 见 ``test_sync_provider_credentials_overwrites_when_server_rotates``）
    """
    from wh_local.modules.temu_publish import gateway_sync
    from wh_local.modules.temu_publish.service import TemuCredentialStore

    db = tmp_path / "runtime.sqlite3"
    init_db(db)
    gateway_sync.reset_backoff()
    calls: list[str] = []

    def fake_fetch(base_url: str, token: str, *, timeout: float = 4.0):
        calls.append(token)
        return {"yl_client": "srv-client", "yl_token": "srv-token"}

    monkeypatch.setattr(gateway_sync, "fetch_gateway_credentials", fake_fetch)

    assert gateway_sync.sync_provider_credentials(
        db, "cust_a", base_url="https://x/auth-api", token_resolver=lambda: "tok-1"
    )
    cred = TemuCredentialStore(db, owner_id="cust_a").load()
    assert (cred.client, cred.token) == ("srv-client", "srv-token")
    # 店铺 token 不能被动过
    assert cred.access_token == ""

    # 已有就不再拉
    assert not gateway_sync.sync_provider_credentials(
        db, "cust_a", base_url="https://x/auth-api", token_resolver=lambda: "tok-2"
    )
    assert calls == ["tok-1"]


def test_sync_provider_credentials_backs_off_after_failure(tmp_path: Path, monkeypatch) -> None:
    """服务器没配/网络不通时：不抛异常，且 60 秒内不重复打远端。"""
    from wh_local.modules.temu_publish import gateway_sync

    db = tmp_path / "runtime.sqlite3"
    init_db(db)
    gateway_sync.reset_backoff()
    calls: list[str] = []

    def failing(base_url: str, token: str, *, timeout: float = 4.0):
        calls.append(token)
        return None

    monkeypatch.setattr(gateway_sync, "fetch_gateway_credentials", failing)

    for _ in range(3):
        assert not gateway_sync.sync_provider_credentials(
            db, "cust_b", base_url="https://x/auth-api", token_resolver=lambda: "tok"
        )
    assert len(calls) == 1, "失败后应退避，不要每次进页面都打远端"
    gateway_sync.reset_backoff()


def test_sync_provider_credentials_needs_token(tmp_path: Path, monkeypatch) -> None:
    """没登录（拿不到远端 token）时直接跳过，不请求服务器。"""
    from wh_local.modules.temu_publish import gateway_sync

    db = tmp_path / "runtime.sqlite3"
    init_db(db)
    gateway_sync.reset_backoff()
    called: list[str] = []
    monkeypatch.setattr(
        gateway_sync,
        "fetch_gateway_credentials",
        lambda *a, **k: called.append("x") or {"yl_client": "c", "yl_token": "t"},
    )

    assert not gateway_sync.sync_provider_credentials(
        db, "cust_c", base_url="https://x/auth-api", token_resolver=lambda: ""
    )
    assert called == []


def test_category_cache_ttl_stays_short_enough_to_see_temu_category_changes() -> None:
    """类目树缓存不能太长：Temu 调整/下架类目后，用户可能发到已废弃类目。

    原来是 6 小时，实测太保守（改完类目第二天上午还是旧的）。1 小时是折中；
    真要更长就得配一个手动刷新入口，否则这条会红。
    """

    from wh_local.modules.temu_publish import client as temu_client

    assert temu_client.CACHE_TTL_CATEGORIES <= 60 * 60, (
        "类目缓存超过 1 小时：Temu 改类目后用户看不到，可能发到已废弃类目"
    )


def test_invalidate_categories_also_drops_attribute_cache_but_keeps_logistics() -> None:
    """手动刷新类目树：类目 + 类目属性一起清，但别碰运费模板。

    属性表是按 catId 定义的，Temu 给某个类目加/改必填属性时，只清类目树的话
    用户点完刷新拿到的还是旧属性表（连必填判断一起是旧的），得等 1 小时或重启。
    运费模板跟类目改动无关，一起清等于每次刷新都白打几轮网关。
    """

    cats = {
        "success": True,
        "errorCode": "1000000",
        "result": {"categoryDTOList": [{"catId": 1}]},
    }
    attrs = {
        "success": True,
        "errorCode": "1000000",
        "result": {"properties": [{"pid": 11, "required": True}]},
    }
    freight = {
        "success": True,
        "errorCode": "1000000",
        "result": {"freightTemplateDTOList": []},
    }
    # 类目/属性缓存是**模块级共享**的（见 test_shared_catalog_cache_...），
    # 先清掉别的用例留下的条目，否则「清掉几条」和「全命中缓存」两个断言都会被污染。
    from wh_local.modules.temu_publish.client import clear_shared_catalog_cache

    clear_shared_catalog_cache()

    # 前 3 个响应给首次取数，第 4/5 个给清缓存后的重取（运费模板不该重取）
    client, session = _client(
        [
            FakeResponse(cats),
            FakeResponse(attrs),
            FakeResponse(freight),
            FakeResponse(cats),
            FakeResponse(attrs),
        ]
    )

    client.categories(0)
    client.attributes(9)
    client.freight_templates([100])
    assert len(session.calls) == 3, "首次三个接口都该打网关"

    client.categories(0)
    client.attributes(9)
    client.freight_templates([100])
    assert len(session.calls) == 3, "第二次全命中缓存"

    dropped = client.invalidate_categories()
    assert dropped >= 2, "cats: + attrs: 各一条（共享缓存里可能还有别的用例留下的条目）"

    client.categories(0)
    client.attributes(9)
    client.freight_templates([100])

    assert len(session.calls) == 5, "类目和属性必须重取；运费模板仍应命中缓存"
    # 第 3 次之后没有第 6 次调用 = 运费模板确实还在缓存里（重取的话会变 6）
    assert session.calls[-2]["json"]["type"] == "bg.glo.goods.cats.get"
    assert session.calls[-1]["json"]["type"] == "bg.glo.goods.attrs.get"


class _CategoryCacheClient:
    """只用来验证 service 层缓存的假客户端。"""

    def __init__(self) -> None:
        self.category_calls = 0
        self.attribute_calls = 0
        self.invalidated = 0

    def categories(self, parent_cat_id: int = 0) -> dict[str, Any]:  # noqa: ARG002
        self.category_calls += 1
        return {"categoryDTOList": []}

    def attributes(self, cat_id: int) -> dict[str, Any]:  # noqa: ARG002
        self.attribute_calls += 1
        return {"properties": []}

    def invalidate_categories(self) -> int:
        self.invalidated += 1
        return 1


def test_refresh_categories_clears_attribute_cache_in_both_layers(tmp_path: Path) -> None:
    """service 外面还套了一层 180 秒缓存：类目和属性都必须两层一起清。

    只清 client 层时，service 缓存的 ``attrs:{catId}`` 还活着 —— 用户点完
    「刷新类目树」，前端重拉属性拿到的仍是旧表。
    """

    fake = _CategoryCacheClient()
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=fake)

    service.categories(0)
    service.attributes(9)
    service.categories(0)
    service.attributes(9)
    assert fake.category_calls == 1, "service 层类目缓存应命中"
    assert fake.attribute_calls == 1, "service 层属性缓存应命中"

    result = service.refresh_categories()

    assert result["ok"] is True
    # service 2 条（categories:0 + attrs:9）+ 假客户端报的 1 条
    assert result["dropped"] == 3
    assert fake.invalidated == 1

    service.categories(0)
    service.attributes(9)
    assert fake.category_calls == 2, "刷新后必须真的重新取类目"
    assert fake.attribute_calls == 2, "刷新后必须真的重新取属性，否则必填判断还是旧的"



def test_empty_top_level_lists_are_omitted_from_payload(tmp_path: Path) -> None:
    """顶层空数组字段整个不发 —— 这是 7000015 sign is invalid 的根因。

    网关算签名时把空数组忽略、转发时又带上，两边对不上；不发就不会有这个歧义。
    """
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=NoSaleCatalogClient())

    payload = service.build_goods_payload(_single_sku_draft())["payload"]

    # 顶层不许出现「空数组」值：空数组会让网关的 sign 对不上（7000015）
    empty_lists = [k for k, v in payload.items() if isinstance(v, list) and not v]
    assert empty_lists == [], f"顶层不该有空数组字段：{empty_lists}"
    # 这件 draft 有「形状」属性，非空属性照发
    assert payload["productPropertyReqs"]
    # 其它数组字段照常发
    assert payload["carouselImageUrls"]
    assert payload["productSkcReqs"]

    # 属性也一个都没有时，productPropertyReqs 同样不发（发 [] 一样踩签名）
    bare = TemuPublishService(tmp_path / "bare.sqlite3", client=NoSaleCatalogClient())
    bare_payload = bare.build_goods_payload(
        _single_sku_draft().model_copy(update={"properties": [], "validate_catalog": False})
    )["payload"]
    assert "productPropertyReqs" not in bare_payload


def test_non_empty_spec_reqs_are_still_sent(tmp_path: Path) -> None:
    """有销售属性的类目规格非空，字段必须照发（不能因为「空就不发」把它也砍掉）。"""
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=SaleCatalogClient())

    payload = service.build_goods_payload(_draft())["payload"]
    assert payload["productSpecPropertyReqs"], "非空的规格声明必须保留"

    # 商品属性：用一个非销售属性验证「非空要发」
    with_prop = TemuPublishService(tmp_path / "p.sqlite3", client=StubCatalogClient())
    prop_payload = with_prop.build_goods_payload(_draft())["payload"]
    assert prop_payload["productPropertyReqs"], "非空的商品属性必须保留"


def test_catsmandatory_request_omits_empty_property_list(tmp_path: Path) -> None:
    """类目必填校验的请求体同样不发空数组（这个接口长期 sign is invalid 就是这个）。"""
    from wh_local.modules.temu_publish.client import TemuProductClient

    sent: list[dict[str, Any]] = []

    class _Probe(TemuProductClient):
        def __init__(self) -> None:  # noqa: D107 - 不走真实网关
            pass

        def call(self, method, body=None, **kwargs):  # noqa: ANN001, D102
            sent.append({"method": method, "body": body})
            return {}

    probe = _Probe()
    probe.mandatory_config(10498, [100], [])
    probe.mandatory_config(10498, [100], [{"pid": 1, "vid": 10}])

    assert sent[0]["method"] == "bg.glo.goods.catsmandatory.get"
    assert "productPropertyReqs" not in sent[0]["body"], "空数组不该出现在请求体里"
    assert sent[1]["body"]["productPropertyReqs"] == [{"pid": 1, "vid": 10}]


def _draft_with_specs(specs: list[SkuSpecInput]) -> GoodsDraftInput:
    """按「SKU 上带几个规格」造草稿：0 个（无销售属性）/ 1 个（只有颜色）/ 2 个（颜色+尺码）。"""
    return GoodsDraftInput(
        category_ids=[18768, 19503, 19668, 19669],
        title="Any category product",
        carousel_image_urls=["https://img.example.com/main.jpg"],
        freight_template_id="HFT-19421251915858203874",
        properties=[GoodsPropertyInput(pid=1, vid=10)],
        skcs=[
            SkcInput(
                ext_code="SKC-1",
                preview_image_urls=["https://img.example.com/skc.jpg"],
                main_specs=[specs[0]] if specs else [],
                skus=[
                    SkuInput(
                        ext_code="SKU-1",
                        supplier_price=1299,
                        stock=20,
                        warehouse_id="WH-07346074419333874",
                        weight_mg=200000,
                        length_mm=200,
                        width_mm=150,
                        height_mm=30,
                        specs=specs,
                    )
                ],
            )
        ],
    )


def test_payload_supports_zero_one_two_spec_columns(tmp_path: Path) -> None:
    """不同类目的销售属性列数完全不同，三种都要能组装出合法 payload。

    - 0 列：桌布 / 亚麻布这类没有销售属性的类目 → 后端补真实兜底规格「颜色：默认」
    - 1 列：毛巾 / 手机壳（只有颜色或只有型号）→ 以前前端硬要第二列，会抛
      「Temu 父规格列表为空，无法创建规格」，等于这类商品永远发不出去
    - 2 列：颜色 + 尺码（最常见）
    """
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=StubCatalogClient())

    color = SkuSpecInput(
        parent_spec_id=1001, parent_spec_name="颜色", spec_id=2001, spec_name="Red"
    )
    size = SkuSpecInput(
        parent_spec_id=1002, parent_spec_name="尺码", spec_id=2002, spec_name="M"
    )

    # ① 两列：原样透传
    two = service.build_goods_payload(_draft_with_specs([color, size]))["payload"]
    sku_two = two["productSkcReqs"][0]["productSkuReqs"][0]
    assert [item["specName"] for item in sku_two["productSkuSpecReqs"]] == ["Red", "M"]

    # ② 一列：只发这一个规格，不能被强行补出第二个
    one = service.build_goods_payload(_draft_with_specs([color]))["payload"]
    sku_one = one["productSkcReqs"][0]["productSkuReqs"][0]
    assert [item["specName"] for item in sku_one["productSkuSpecReqs"]] == ["Red"]
    # 规格声明在**顶层**（payload.productSpecPropertyReqs），不在 SKC 里
    assert [item["specName"] for item in one["productSpecPropertyReqs"]] == ["Red"]

    # ③ 零列：补一条**真实**兜底规格（颜色/默认），否则 Temu 判「缺规格」；
    #    而 id 全 0 的占位规格会让 goods.add 一直回 6000002（详见 client.ensure_default_spec）
    zero = service.build_goods_payload(_draft_with_specs([]))["payload"]
    sku_zero = zero["productSkcReqs"][0]["productSkuReqs"][0]
    decl = zero["productSpecPropertyReqs"]
    assert decl and decl[0]["specName"] == "默认"
    assert decl[0]["specId"] == 20832, "兜底规格必须带真实 specId"
    assert [item["specName"] for item in sku_zero["productSkuSpecReqs"]] == ["默认"]


def test_shared_catalog_cache_is_cleared_across_client_instances() -> None:
    """类目/属性缓存必须跨实例共享 —— 否则「清了但没用」。

    2026-11-03 实测：发布被 Temu 回 6000002「货品类目属性更新」后，
    router._handle 里调 `service.client().invalidate_categories()` 清缓存。
    但 _handle 拿不到当前 actor，构造出来的是**另一个 client 实例**，
    清掉的是它自己的空缓存，真正在用的那份纹丝不动 ——
    结果就是「点了刷新、错误照旧」，连刷 6 次全失败。

    所以这三类（全网统一、与店铺无关）的缓存被提升到模块级：
    任何实例清，等于所有实例清。而 parent-specs / freight / warehouse 是**店铺级**，
    必须留在实例上，否则会串店。
    """
    from wh_local.modules.temu_publish.client import (
        TemuCredentials,
        TemuProductClient,
        clear_shared_catalog_cache,
    )

    def make(access_token: str) -> TemuProductClient:
        return TemuProductClient(
            TemuCredentials(client="c", token="t", access_token=access_token),
            session=object(),  # 不真的发请求
        )

    a = make("shop-a")
    b = make("shop-b")

    # ① 属性表：A 写入，B 能读到（共享）
    a._cache_put("attrs:10498:1", {"properties": ["old"]}, 3600)
    assert b._cache_get("attrs:10498:1") == {"properties": ["old"]}

    # ② 任意一处清缓存，所有实例立即失效
    assert clear_shared_catalog_cache() >= 1
    assert b._cache_get("attrs:10498:1") is None

    # ③ 店铺级缓存不能共享（否则换店会拿到别人家的规格/运费模板/仓库）
    a._cache_put("parent-specs", {"specs": ["a"]}, 3600)
    a._cache_put("freight:100", {"templates": ["a"]}, 3600)
    assert b._cache_get("parent-specs") is None
    assert b._cache_get("freight:100") is None
    # 但 A 自己还读得到
    assert a._cache_get("parent-specs") == {"specs": ["a"]}

    # ④ invalidate_categories 也走共享清理（手动「刷新类目树」按钮走这条）
    a._cache_drop("parent-specs")
    a._cache_drop("freight:100")
    a._cache_put("cats:0", {"categoryDTOList": []}, 3600)
    assert b._cache_get("cats:0") == {"categoryDTOList": []}
    assert a.invalidate_categories() >= 1
    assert b._cache_get("cats:0") is None


def test_spec_decl_never_borrows_non_sale_property_ids() -> None:
    """规格声明的 pid/refPid/templatePid 不能借用同名的**商品属性**。

    实测（2026-11-03，化妆包类目 30988）：类目里有一个叫「颜色」的商品属性
    （isSale=false、parentSpecId=null、pid=13）。按名字兜底匹配到它之后，
    声明里带上了 pid=13/refPid=63/templatePid=1230780，Temu 回

        属性[颜色]校验错误:关键词属性 白色 不是有效值。

    把这三个 id 全填 parentSpecId（1001）后同一个请求立刻发布成功
    （productId 52140751895）。所以这里必须只认 parentSpecId。
    """
    from wh_local.modules.temu_publish.service import (
        _SimpleSpec,
        _catalog_property_by_parent,
        _spec_decl_payload,
    )

    catalog = {
        "properties": [
            {
                "pid": 13,
                "name": "颜色",
                "refPid": 63,
                "templatePid": 1230780,
                "isSale": False,
                "parentSpecId": None,
                "required": True,
                "values": [{"vid": 376, "value": "白色"}],
            }
        ]
    }
    # 认不出销售属性 → 不返回任何属性
    assert _catalog_property_by_parent(catalog, 1001, "颜色") is None

    decl = _spec_decl_payload(
        _SimpleSpec(
            {"parentSpecId": 1001, "parentSpecName": "颜色", "specId": 2001, "specName": "白色"}
        ),
        catalog,
    )
    assert (decl["pid"], decl["refPid"], decl["templatePid"]) == (1001, 1001, 1001)
    assert decl["parentSpecId"] == 1001
    assert decl["vid"] == 0, "vid 必须是 0，填属性值 id 会被判「主销售属性不合法」"


def test_default_spec_value_must_land_in_category_color_values() -> None:
    """兜底规格的值必须落在类目「颜色」属性的预设值里，否则 Temu 判「不是有效值」。"""
    from wh_local.modules.temu_publish.client import TemuCredentials, TemuProductClient

    class FakeClient(TemuProductClient):
        def __init__(self) -> None:
            super().__init__(
                TemuCredentials(client="c", token="t", access_token="a"), session=object()
            )
            self.created: list[tuple[int, str]] = []

        def parent_specs(self) -> dict[str, Any]:
            return {"parentSpecDTOS": [{"parentSpecId": 1001, "parentSpecName": "颜色"}]}

        def create_spec(self, parent_spec_id: int, spec_name: str) -> dict[str, Any]:
            self.created.append((int(parent_spec_id), spec_name))
            return {"specId": 777}

    catalog = {
        "properties": [
            {
                "pid": 13,
                "name": "颜色",
                "isSale": False,
                "required": True,
                "values": [{"vid": 376, "value": "白色"}, {"vid": 377, "value": "红色"}],
            }
        ]
    }

    client = FakeClient()
    # 随便给个不在列表里的值（以前的「默认」）→ 退回第一个合法值
    spec = client.ensure_default_spec("默认", catalog)
    assert spec["specName"] == "白色"
    assert client.created == [(1001, "白色")]

    # 用户自己选了红色 → 就用红色，不要覆盖成白色
    assert client.ensure_default_spec("红色", catalog)["specName"] == "红色"

    # 类目没有「颜色」属性（桌布 10498）→ 才用「默认」
    client2 = FakeClient()
    assert client2.ensure_default_spec("", {"properties": []})["specName"] == "默认"


def test_bundled_gateway_reads_encrypted_blob(tmp_path: Path, monkeypatch) -> None:
    """兼容老包：temu_gateway.enc 仍能读，但**新版本不再打包它**。

    2026-11-04 起改为「安装包不带凭据，登录后由服务器下发」：加密随包只能防
    grep，解包后自带 decrypt 命令就能还原，挡不住人。所以正式包不再生成 .enc，
    这段读取只留给历史安装包和开发机；这里锁住「能读 + 损坏/缺失不崩」。
    """
    from wh_local import secrets as secrets_mod
    from wh_local.db import init_db
    from wh_local.modules.temu_publish import service as service_mod
    from wh_local.modules.temu_publish.service import TemuCredentialStore

    db = tmp_path / "runtime.sqlite3"
    init_db(db)

    payload = json.dumps({"yl_client": "enc-client", "yl_token": "enc-token"}).encode("utf-8")
    enc = tmp_path / "temu_gateway.enc"
    enc.write_bytes(secrets_mod.encrypt_bytes(payload))

    # 明文两份都不存在，只有 .enc
    monkeypatch.setattr(service_mod, "BUNDLED_GATEWAY_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(service_mod, "BUNDLED_GATEWAY_LOCAL_PATH", tmp_path / "missing.local.json")
    monkeypatch.setattr(service_mod, "_encrypted_gateway_paths", lambda: [enc])
    for name in ("WH_TEMU_YL_CLIENT", "WH_TEMU_YL_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    loaded = TemuCredentialStore(db, owner_id="cust_enc").load()
    assert (loaded.client, loaded.token) == ("enc-client", "enc-token")

    # 明文存在时优先用明文（打包机 / 开发机的场景）
    local = tmp_path / "temu_gateway.local.json"
    local.write_text(json.dumps({"yl_client": "plain-client"}), encoding="utf-8")
    monkeypatch.setattr(service_mod, "BUNDLED_GATEWAY_LOCAL_PATH", local)
    loaded = TemuCredentialStore(db, owner_id="cust_enc").load()
    assert loaded.client == "plain-client"
    assert loaded.token == "enc-token", "明文缺的字段继续用 .enc 里的"

    # .enc 坏掉（比如拷错文件）时不能抛异常，按「没配」处理
    enc.write_bytes(b"not-a-valid-blob")
    local.unlink()
    loaded = TemuCredentialStore(db, owner_id="cust_enc2").load()
    assert loaded.client == "" and loaded.token == ""


def test_sync_provider_credentials_overwrites_when_server_rotates(tmp_path: Path, monkeypatch) -> None:
    """服务商换过 client/token 后，本地那份要**自动换成新的**。

    这是「凭据泄露能作废」的唯一开关：以前只在本地为空时才拉，一旦写过就再也不更新，
    结果服务器改了新值，老用户还在用旧值、一直 403，而且自己没法修（不知道新值）。
    """
    from wh_local.modules.temu_publish import gateway_sync
    from wh_local.modules.temu_publish.service import TemuCredentialStore

    db = tmp_path / "runtime.sqlite3"
    init_db(db)
    gateway_sync.reset_backoff()
    served = {"client": "old-client", "token": "old-token"}

    def fake_fetch(base_url: str, token: str, *, timeout: float = 4.0):
        return {"yl_client": served["client"], "yl_token": served["token"]}

    monkeypatch.setattr(gateway_sync, "fetch_gateway_credentials", fake_fetch)
    kwargs = {"base_url": "https://x/auth-api", "token_resolver": lambda: "tok"}

    assert gateway_sync.sync_provider_credentials(db, "cust_r", **kwargs)
    store = TemuCredentialStore(db, owner_id="cust_r")
    assert (store.load().client, store.load().token) == ("old-client", "old-token")
    old_version = store.provider_version()
    assert old_version, "指纹要落库，否则没法判断服务器那边变没变"

    # 服务商在服务器上换了一对 → 指纹变了 → 本地自动覆盖
    served.update(client="new-client", token="new-token")
    gateway_sync.reset_backoff()
    assert gateway_sync.sync_provider_credentials(db, "cust_r", **kwargs)
    assert (store.load().client, store.load().token) == ("new-client", "new-token")
    assert store.provider_version() != old_version

    # 值没变就不再写库（避免每次核对都动一次数据库）
    gateway_sync.reset_backoff()
    assert not gateway_sync.sync_provider_credentials(db, "cust_r", **kwargs)
    gateway_sync.reset_backoff()


def test_sync_provider_credentials_throttles_verification(tmp_path: Path, monkeypatch) -> None:
    """已有凭据时 5 分钟内不再问服务器：进页面、发布都想快，不能每次都等一个 RTT。"""
    from wh_local.modules.temu_publish import gateway_sync

    db = tmp_path / "runtime.sqlite3"
    init_db(db)
    gateway_sync.reset_backoff()
    calls: list[str] = []

    def fake_fetch(base_url: str, token: str, *, timeout: float = 4.0):
        calls.append(token)
        return {"yl_client": "c", "yl_token": "t"}

    monkeypatch.setattr(gateway_sync, "fetch_gateway_credentials", fake_fetch)
    kwargs = {"base_url": "https://x/auth-api", "token_resolver": lambda: "tok"}

    assert gateway_sync.sync_provider_credentials(db, "cust_t", **kwargs)
    assert not gateway_sync.sync_provider_credentials(db, "cust_t", **kwargs)
    assert calls == ["tok"], "刚核对过，不该再打远端"

    # force=True（网关说凭据失效）要无视节流
    assert not gateway_sync.sync_provider_credentials(db, "cust_t", force=True, **kwargs)
    assert calls == ["tok", "tok"], "force 必须真的重拉"
    gateway_sync.reset_backoff()


def test_gateway_rejects_credentials_triggers_refresh_and_retry_hint(tmp_path: Path, monkeypatch) -> None:
    """网关说 client/token 失效时：忘掉指纹 + 立刻按服务器最新值重拉，并让用户重试一次。

    场景：服务商重置了 token，服务器已经换成新值，但用户本机还存着旧的 ——
    以前只会回一句「服务商网关拒绝了这个 client…IP 白名单」，用户永远修不好。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from wh_local.modules.temu_publish import router as router_mod
    from wh_local.modules.temu_publish.client import TemuGatewayError
    from wh_local.modules.temu_publish.service import TemuCredentialStore
    from wh_local.session import Actor, actor_from_authorization

    db_path = tmp_path / "runtime.sqlite3"
    init_db(db_path)
    store = TemuCredentialStore(db_path, owner_id="cust_z")
    store.save_provider_credentials(client="old", token="old", actor_id="cust_z", version="v1")

    sync_calls: list[bool] = []

    def fake_sync(*args, **kwargs):  # noqa: ANN001 - 替身，只记录 force
        sync_calls.append(kwargs.get("force", False))
        return True  # 「确实拉到了新值」

    monkeypatch.setattr(router_mod, "sync_provider_credentials", fake_sync)

    def boom(self, payload, *, force=False):  # noqa: ANN001 - 替身
        raise TemuGatewayError(
            "bg.glo.goods.add", "Client is not allowed to call this API",
            error_code="CLIENT_PERMISSION_DENIED", http_status=403,
        )

    monkeypatch.setattr(router_mod.TemuPublishService, "publish_goods", boom)

    app = FastAPI()
    app.include_router(
        router_mod.create_temu_publish_router(
            db_path,
            remote_token_resolver=lambda actor: "remote-token",
            customer_auth_base_url="https://x/auth-api",
        )
    )
    app.dependency_overrides[actor_from_authorization] = lambda: Actor(
        id="cust_z", username="z", role="admin"
    )
    http = TestClient(app)

    resp = http.post("/api/temu-publish/publish", json=_draft().model_dump(mode="json"))
    assert resp.status_code == 502, resp.text
    body = resp.json()["detail"]
    assert body["error_code"] == "TEMU_CREDENTIAL_REFRESHED"
    assert "再点一次" in body["message"]

    # 发布前核对一次（force=False），失败后强制重拉一次（force=True）
    assert sync_calls == [False, True]
    assert store.provider_version() == "", "失效后要忘掉指纹，下次才会以服务器为准"


def test_provider_fingerprint_never_leaks_credentials() -> None:
    """指纹必须是摘要：落库/日志里的东西不能反推出凭据。"""
    from wh_local.modules.temu_publish.gateway_sync import provider_fingerprint

    fingerprint = provider_fingerprint("client-abc", "token-xyz")
    assert len(fingerprint) == 16
    assert "client-abc" not in fingerprint and "token-xyz" not in fingerprint
    # 同一对永远同一个值，换任一个都会变
    assert provider_fingerprint("client-abc", "token-xyz") == fingerprint
    assert provider_fingerprint("client-abc", "token-xy2") != fingerprint
    assert provider_fingerprint("client-abd", "token-xyz") != fingerprint


def test_publish_history_splits_success_and_failure(tmp_path: Path) -> None:
    """发布历史要能按成功/失败分开取，并带上商品 id 与失败原因。"""
    from wh_local.modules.temu_publish import submissions as ledger

    db = tmp_path / "runtime.sqlite3"
    init_db(db)
    ledger.ensure_table(db)

    ok_id = ledger.begin(
        db, "cust_h", "fp-ok", "OK 商品", 2,
        draft_id="pod:batch1:1", leaf_cat_id=30988, image="https://img/x.jpg",
    )
    ledger.finish(db, ok_id, "success", gid="50210594175")
    bad_id = ledger.begin(db, "cust_h", "fp-bad", "坏商品", 1, draft_id="ai:task1:item9")
    ledger.finish(db, bad_id, "failed", error="属性[颜色]校验错误")

    all_rows = ledger.list_history(db, "cust_h")
    assert all_rows["total"] == 2
    assert all_rows["counts"] == {"success": 1, "failed": 1}

    ok = ledger.list_history(db, "cust_h", status="success")
    assert ok["total"] == 1
    item = ok["items"][0]
    assert (item["goods_id"], item["draft_id"], item["leaf_cat_id"]) == (
        "50210594175", "pod:batch1:1", 30988,
    )
    assert item["image"] == "https://img/x.jpg"

    bad = ledger.list_history(db, "cust_h", status="failed")
    assert bad["total"] == 1
    assert "属性[颜色]" in bad["items"][0]["error"]

    # 别人看不到
    assert ledger.list_history(db, "cust_other")["total"] == 0


def test_publish_history_carries_display_fields_and_search(tmp_path: Path) -> None:
    """历史页要「看得懂」：类目路径名/价格区间/店铺名/错误码都要落库，且支持搜索。

    以前只记 cat id 和一个错误码数字，用户在历史列表里既认不出是哪件商品、
    也看不出错在哪一步。这几个字段专门给列表展示用，不参与任何判定。
    """
    from wh_local.modules.temu_publish import submissions as ledger

    db = tmp_path / "runtime.sqlite3"
    init_db(db)
    ledger.ensure_table(db)

    ok_id = ledger.begin(
        db, "cust_h", "fp-1", "Cosmetic Bag", 3,
        draft_id="ai:t1:i1", leaf_cat_id=19669,
        image="https://img/1.jpg",
        cat_name="美妆个护 > 化妆包 > 手拿包",
        price_min=1250, price_max=1899,
        shop_name="WIG Mall US",
    )
    ledger.finish(db, ok_id, "success", gid="50210594175")
    bad_id = ledger.begin(db, "cust_h", "fp-2", "Storage Basket", 1, cat_name="家居 > 收纳桶")
    ledger.finish(db, bad_id, "failed", error="货品类目属性更新失败", error_code="6000002")

    item = ledger.list_history(db, "cust_h", status="success")["items"][0]
    assert item["cat_name"] == "美妆个护 > 化妆包 > 手拿包"
    assert (item["price_min"], item["price_max"]) == (1250, 1899)
    assert item["shop_name"] == "WIG Mall US"
    assert item["created_ts"] > 0

    failed = ledger.list_history(db, "cust_h", status="failed")["items"][0]
    assert failed["error_code"] == "6000002"
    # 老记录（没这两列的值）不能崩，取空值即可
    assert failed["price_min"] == 0 and failed["shop_name"] == ""

    # 搜索：标题 / 商品 id / 草稿 id / 类目名都能命中
    assert ledger.list_history(db, "cust_h", q="Cosmetic")["total"] == 1
    assert ledger.list_history(db, "cust_h", q="50210594175")["total"] == 1
    assert ledger.list_history(db, "cust_h", q="ai:t1")["total"] == 1
    assert ledger.list_history(db, "cust_h", q="收纳桶")["total"] == 1
    assert ledger.list_history(db, "cust_h", q="不存在的词")["total"] == 0
    # 搜索和状态过滤同时生效
    assert ledger.list_history(db, "cust_h", status="success", q="收纳桶")["total"] == 0


def test_backfill_history_display_fills_old_records(tmp_path: Path) -> None:
    """老记录没有主图/类目名：按商品 id 去店铺列表认一遍补上，补完不再重复处理。

    `image`/`cat_name` 是 2026-11-04 加的列，之前的记录打开就是一片「无图」。
    商品详情接口不返回图片，只有列表接口带 mainImageUrl —— 所以只能翻列表按
    productId 认。失败记录没有商品 id，不该被当成「补不到」反复尝试。
    """
    from wh_local.modules.temu_publish import submissions as ledger

    db = tmp_path / "runtime.sqlite3"
    init_db(db)
    ledger.ensure_table(db)

    ok_id = ledger.begin(db, "cust_b", "fp-1", "化妆包 A", 1)
    ledger.finish(db, ok_id, "success", gid="48396983649")
    failed_id = ledger.begin(db, "cust_b", "fp-2", "化妆包 B", 1)
    ledger.finish(db, failed_id, "failed", error="属性校验错误")
    gone_id = ledger.begin(db, "cust_b", "fp-3", "已被后台删掉的商品", 1)
    ledger.finish(db, gone_id, "success", gid="99999999999")

    client = PublishStubClient()
    client.goods_pages = [
        [
            {
                "productId": 48396983649,
                "productName": "化妆包 A",
                "mainImageUrl": "https://img.kwcdn.com/a-goods.jpeg",
                "leafCat": {"catName": "化妆包"},
                "createdAt": 1793758384000,
            }
        ]
    ]
    service = TemuPublishService(db, client=client, owner_id="cust_b")

    result = service.backfill_history_display()
    # 只有「有商品 id 且能在店铺里认出来」的那条能补
    assert result["updated"] == 1
    item = ledger.list_history(db, "cust_b", status="success")["items"]
    by_gid = {row["goods_id"]: row for row in item}
    assert by_gid["48396983649"]["image"] == "https://img.kwcdn.com/a-goods.jpeg"
    assert by_gid["48396983649"]["cat_name"] == "化妆包"
    # 店铺里没有了的那条：保持空，不写脏值（前端继续显示占位）
    assert by_gid["99999999999"]["image"] == ""

    # 再调一次：已补好的不再进候选（只会剩下那条查不到的）
    again = service.backfill_history_display()
    assert again["checked"] == 1
    assert again["updated"] == 0


def test_published_draft_ids_only_counts_success(tmp_path: Path) -> None:
    """只有「成功且带 draft_id」的草稿才算已发布；失败/手填的不算。"""
    from wh_local.modules.temu_publish import submissions as ledger

    db = tmp_path / "runtime.sqlite3"
    init_db(db)
    ledger.ensure_table(db)

    ok1 = ledger.begin(db, "cust_p", "f1", "A", 1, draft_id="pod:b:1")
    ledger.finish(db, ok1, "success", gid="1")
    ok2 = ledger.begin(db, "cust_p", "f2", "B", 1, draft_id="ai:t:2")
    ledger.finish(db, ok2, "success", gid="2")
    bad = ledger.begin(db, "cust_p", "f3", "C", 1, draft_id="ai:t:3")
    ledger.finish(db, bad, "failed", error="x")
    manual = ledger.begin(db, "cust_p", "f4", "手填", 1)
    ledger.finish(db, manual, "success", gid="4")

    assert ledger.published_draft_ids(db, "cust_p") == {"pod:b:1", "ai:t:2"}
    assert ledger.published_draft_ids(db, "cust_other") == set()


def test_draft_library_drops_published_entries(tmp_path: Path, monkeypatch) -> None:
    """草稿库里已经成功发布的条目要消失 —— 否则用户会重复上架同一件商品。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from wh_local.modules.temu_publish import router as router_mod
    from wh_local.modules.temu_publish import submissions as ledger
    from wh_local.session import Actor, actor_from_authorization

    db = tmp_path / "runtime.sqlite3"
    init_db(db)
    ledger.ensure_table(db)
    done = ledger.begin(db, "cust_lib", "f1", "发过的", 1, draft_id="pod:b:1")
    ledger.finish(db, done, "success", gid="999")

    monkeypatch.setattr(
        router_mod,
        "collect_all_draft_entries",
        lambda **kwargs: {
            "entries": [
                {"id": "pod:b:1", "source": "pod", "title": "发过的"},
                {"id": "ai:t:2", "source": "ai", "title": "还没发"},
            ],
            "warnings": [],
        },
    )

    app = FastAPI()
    app.include_router(router_mod.create_temu_publish_router(db))
    app.dependency_overrides[actor_from_authorization] = lambda: Actor(
        id="cust_lib", username="lib", role="admin"
    )
    http = TestClient(app)

    data = http.get("/api/temu-publish/draft-library?refresh=true").json()
    ids = [item["id"] for item in data["entries"]]
    assert ids == ["ai:t:2"], f"已发布的草稿应被过滤掉，实际 {ids}"

    history = http.get("/api/temu-publish/publish-history?status=success").json()
    assert history["total"] == 1
    assert history["items"][0]["draft_id"] == "pod:b:1"


def test_draft_extra_fields_keeps_every_variant(tmp_path: Path) -> None:
    """一条草稿里的多个 SKU 要全部带出来，不能只取第一条。

    真实数据（product_processing_drafts.raw_payload_json.source_variant_records）
    是一组「一个颜色一条」的记录，每条有自己的 sku_id、price_cny、quantity、image_url。
    以前只读第一条的属性值 → 11 个颜色进发布页只剩 1 个，用户还得手填其余 10 个。
    """
    from wh_local.modules.temu_publish.library import _draft_extra_fields

    draft = {
        "description": "帆布托特包",
        "raw_payload": {
            "source_url": "https://detail.1688.com/offer/1.html",
            "source_variant_records": [
                {
                    "sku_id": "S1",
                    "attributes": {"颜色": "黑茶色-性价比版"},
                    "price_cny": "13.20",
                    "quantity": 304,
                    "image_url": "https://img/1.jpg",
                },
                {
                    "sku_id": "S2",
                    "attributes": {"颜色": "米黄色-性价比版"},
                    "price_cny": "16.90",
                    "quantity": 0,
                    "image_url": "https://img/2.jpg",
                },
                {
                    "sku_id": "S3",
                    "attributes": {"颜色": "灰色-性价比版", "尺码": "M"},
                    "price_cny": "15.20",
                    "quantity": 12,
                    "image_url": "https://img/3.jpg",
                },
            ],
        },
    }
    fields = _draft_extra_fields(draft)

    assert fields["spec_name_1"] == "颜色"
    assert fields["spec_values_1"] == ["黑茶色-性价比版", "米黄色-性价比版", "灰色-性价比版"]
    assert fields["spec_name_2"] == "尺码"
    assert fields["spec_values_2"] == ["M"]
    assert len(fields["variants"]) == 3
    first, second, third = fields["variants"]
    assert first["sku"] == "S1" and first["color"] == "黑茶色-性价比版" and first["stock"] == 304
    assert first["image"] == "https://img/1.jpg"
    # 成本 13.20 × 4 < 下限 150 → 申报价按导出规则兜到 150
    assert first["price"] == 150.0
    # 库存 0 是合法值（先建品后补货），不能被当成「没值」
    assert second["stock"] == 0
    assert third["size"] == "M"


def test_draft_extra_fields_tolerates_empty_payload() -> None:
    """草稿没有变体记录时不要炸，返回空列表即可。"""
    from wh_local.modules.temu_publish.library import _draft_extra_fields

    for draft in (None, {}, {"raw_payload": None}, {"raw_payload": {"source_variant_records": "bad"}}):
        fields = _draft_extra_fields(draft)
        assert fields.get("spec_values_1") == []
        assert fields.get("variants") == []


def test_untrusted_upstream_stock_is_dropped() -> None:
    """上游把库存填成 99999999 这种占位值时，不能原样当 Temu 库存带出去。

    1688 大量商家把「库存」填成 100005820 / 99999999 表示无限量。原样带到
    Temu 半托管后台毫无意义（用户看到的就是「库存 100005820」这种鬼数字），
    而且这些值也不是真的可售数量。超过上限一律当「上游没给」。
    """
    from wh_local.modules.temu_publish.library import MAX_TRUSTED_STOCK, _trusted_stock

    assert _trusted_stock(0) == 0
    assert _trusted_stock("304") == 304
    assert _trusted_stock(MAX_TRUSTED_STOCK) == MAX_TRUSTED_STOCK
    # 占位值 / 超范围 / 负数 / 非数字 → 当没给
    assert _trusted_stock(MAX_TRUSTED_STOCK + 1) is None
    assert _trusted_stock(99999999) is None
    assert _trusted_stock(100005820) is None
    assert _trusted_stock(-5) is None
    assert _trusted_stock(None) is None
    assert _trusted_stock("abc") is None


def test_variants_stock_uses_trusted_value_only() -> None:
    """变体里的库存也要过同一道闸：99999999 不带，真实 304 照带。"""
    from wh_local.modules.temu_publish.library import _draft_extra_fields

    draft = {
        "raw_payload": {
            "source_variant_records": [
                {"sku_id": "A", "attributes": {"颜色": "黑"}, "price_cny": "8", "quantity": 100005820},
                {"sku_id": "B", "attributes": {"颜色": "白"}, "price_cny": "8", "quantity": 304},
                {"sku_id": "C", "attributes": {"颜色": "灰"}, "price_cny": "8", "quantity": 0},
            ]
        }
    }
    variants = _draft_extra_fields(draft)["variants"]
    assert [item["stock"] for item in variants] == [None, 304, 0]


def test_size_charts_exposes_pickable_tables(tmp_path: Path) -> None:
    """店里已有的尺码表要收敛成 {id, name} 给前端点选。

    网关那行的字段名一直没确认（businessId / sizeSpecId 都可能），所以按优先级试；
    认不出 id 的行必须丢掉 —— 给前端一个填不进去的假 ID 比不给更糟。
    """
    client = SizeChartClient(
        [
            {"businessId": 908812, "name": "女装标准尺码表"},
            {"sizeSpecId": 908815},
            {"foo": "bar"},
        ],
        elements=[{"id": 10002, "name": "胸围全围", "necessary": True}],
    )
    service = TemuPublishService(tmp_path / "e.sqlite3", client=client)
    info = service.size_charts(24376)

    assert [table["id"] for table in info["tables"]] == [908812, 908815]
    assert info["tables"][0]["name"] == "女装标准尺码表"
    # 没有名字也要留一行，前端会退化成「表 #id」
    assert info["tables"][1]["name"] == ""
    # count 是网关给的原始行数，tables 是能用的那些 —— 两者可以不等
    assert info["count"] == 3
    # 原始结构照旧保留，排查时还要看它
    assert info["raw"][0] == {"businessId": 908812, "name": "女装标准尺码表"}


def test_draft_library_pages_inside_a_big_task(tmp_path: Path) -> None:
    """一个批次里放 500 个商品时，靠 item_offset 一页页翻出来。

    以前固定 item_limit=20，500 个只能看到前 20 个，界面还写「共 20 条」。
    现在每页取 20、has_more 告诉前端还能继续，成本恒定（不随 500 增长）。
    """
    from wh_local.modules.temu_publish.library import collect_all_draft_entries, paginate_draft_library
    from wh_local.session import Actor

    class _Repo:
        def get_drafts(self, ids, workspace_id=""):
            return []

    class _PP:
        total = 500

        def __init__(self) -> None:
            self.repository = _Repo()

        def task_history(self, limit, ws):
            return {"tasks": [{"task_id": 1, "status": "completed", "success_count": self.total,
                               "updated_at": "2026-11-01T10:00:00", "title": "大批次"}]}

        def task_preview(self, task_id, *, workspace_id, item_limit, item_offset, media_limit):
            end = min(self.total, item_offset + item_limit)
            return {
                "items": [
                    {"item_id": k, "product_draft_id": 1000 + k, "exportable": True, "excluded": False,
                     "title": f"商品{k}", "status": "completed",
                     "carousel_images": [f"/api/x/{k}.jpg"], "detail_images": [],
                     "main_image": f"/api/x/{k}.jpg", "core_fields": {}}
                    for k in range(item_offset, end)
                ],
                "item_count": self.total,
                "item_offset": item_offset,
                "has_more": end < self.total,
            }

    actor = Actor(id="x", username="x", role="admin", workspace_id="default")
    pp = _PP()
    seen: list[str] = []
    offset = 0
    rounds = 0
    while True:
        payload = collect_all_draft_entries(
            product_processing=pp, pod_service=None, actor=actor, limit=200, item_offset=offset
        )
        page = paginate_draft_library(payload, page=1, page_size=30)
        seen.extend(entry["title"] for entry in page["entries"])
        rounds += 1
        if not page["has_more"]:
            break
        offset += 20
        assert rounds < 100, "翻页没有终止"

    assert rounds == 25  # 500 / 20
    assert len(seen) == 500
    assert len(set(seen)) == 500  # 没有重复
    assert seen[0] == "商品0" and seen[-1] == "商品499"


class _WholeTaskAi:
    """一个任务里塞满商品，只有第 480 个带关键字。

    模拟真实情况：普通聚合只看每个任务前 20 个，480 号那条根本进不了列表。
    """

    def __init__(self, total: int = 500) -> None:
        self.total = total
        self.media_calls: list[list[int]] = []
        self.repository = self

    def task_history(self, limit: int, ws: str) -> dict[str, Any]:
        return {
            "tasks": [
                {
                    "task_id": 7,
                    "status": "completed",
                    "success_count": self.total,
                    "updated_at": "2026-11-01T10:00:00",
                    "title": "大批次",
                }
            ]
        }

    def task_preview(
        self,
        task_id: int,
        *,
        workspace_id: str = "",
        item_limit: int | None = None,
        item_offset: int = 0,
        media_limit: int | None = None,
    ) -> dict[str, Any]:
        end = min(self.total, item_offset + (item_limit or 0))
        has_media = media_limit is None or media_limit > 0
        items = []
        for k in range(item_offset, end):
            items.append(
                {
                    "item_id": k,
                    "product_draft_id": 1000 + k,
                    "exportable": True,
                    "excluded": False,
                    "title": "蓝色桌布" if k == 480 else f"商品{k}",
                    "status": "completed",
                    "carousel_images": [f"/api/x/{k}.jpg"] if has_media else [],
                    "detail_images": [],
                    "main_image": f"/api/x/{k}.jpg" if has_media else "",
                    "core_fields": {"sku": f"SKU-{k}"},
                }
            )
        return {
            "items": items,
            "item_count": self.total,
            "item_offset": item_offset,
            "has_more": end < self.total,
        }

    def search_task_items_bulk(
        self,
        task_ids,
        needle: str,
        *,
        limit_per_task: int = 2000,
        cancelled_task_ids=(),
        match_all: bool = False,
    ) -> dict[int, list[int]]:
        """模拟库内批量匹配（真实实现一条 SQL 查完所有批次）。"""
        out: dict[int, list[int]] = {}
        for task_id in task_ids:
            hits: list[int] = []
            for k in range(self.total):
                title = "蓝色桌布" if k == 480 else f"商品{k}"
                if not match_all and needle.lower() not in f"{title} sku-{k}".lower():
                    continue
                hits.append(k)
                if len(hits) >= limit_per_task:
                    break
            if hits:
                out[int(task_id)] = hits
        return out

    def task_search_preview(
        self,
        task_id: int,
        needle: str,
        *,
        workspace_id: str = "",
        limit: int = 2000,
        match_all: bool = False,
        matched_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """库里做匹配的那份（这里用 Python 模拟 SQL 行为）。"""
        if matched_ids is None:
            matched_ids = self.search_task_items_bulk(
                [task_id], needle, limit_per_task=limit, match_all=match_all
            ).get(int(task_id), [])
        hits = []
        for k in matched_ids[:limit]:
            title = "蓝色桌布" if k == 480 else f"商品{k}"
            hits.append(
                {
                    "item_id": k,
                    "product_draft_id": 1000 + k,
                    "exportable": True,
                    "excluded": False,
                    "title": title,
                    "status": "completed",
                    "carousel_images": [],
                    "detail_images": [],
                    "main_image": "",
                    "core_fields": {"sku": f"SKU-{k}"},
                }
            )
        return {
            "task_id": task_id,
            "item_count": len(hits),
            "truncated": len(hits) >= limit,
            "items": hits,
        }

    def task_preview_media(
        self, task_id: int, draft_ids: list[int], *, workspace_id: str = ""
    ) -> dict[str, Any]:
        self.media_calls.append(list(draft_ids))
        return {
            "items": {
                str(draft_id): {
                    "carousel_images": [f"/api/x/{draft_id - 1000}.jpg"],
                    "detail_images": [],
                    "main_image": f"/api/x/{draft_id - 1000}.jpg",
                }
                for draft_id in draft_ids
            }
        }

    def get_drafts(self, ids, **kwargs):  # noqa: ANN001, ANN201
        return []


def test_search_finds_item_far_inside_a_big_task() -> None:
    """搜索必须扫全量：第 480 个商品不能因为「前 20 个没它」就搜不到。

    以前搜索只在「已经聚合出来的那几十条」里过滤，用户搜自己明明有的商品
    会得到「没有匹配」，然后以为商品丢了。
    """
    from wh_local.modules.temu_publish.library import (
        collect_all_draft_entries,
        materialize_draft_search,
        paginate_draft_library,
        scan_draft_library,
    )
    from wh_local.session import Actor

    actor = Actor(id="x", username="x", role="admin", workspace_id="default")
    pp = _WholeTaskAi()

    # 普通路径看不到它：只有前 20 个
    normal = paginate_draft_library(
        collect_all_draft_entries(
            product_processing=pp, pod_service=None, actor=actor, limit=200
        ),
        query="蓝色桌布",
        page=1,
        page_size=30,
    )
    assert normal["entries"] == []

    scan = scan_draft_library(
        product_processing=pp, pod_service=None, actor=actor, query="蓝色桌布"
    )
    assert [entry["title"] for entry in scan["ai"]] == ["蓝色桌布"]
    assert scan["counts"] == {"ai": 1, "pod": 0, "total": 1}

    page = materialize_draft_search(
        scan, product_processing=pp, actor=actor, page=1, page_size=30
    )
    assert page["total"] == 1
    assert page["has_more"] is False
    # 只有命中的那一条去取图，不是把 500 条全投影一遍
    assert pp.media_calls == [[1480]]
    assert page["entries"][0]["images"] == ["/api/x/480.jpg"]
    assert page["entries"][0]["sku"] == "SKU-480"


def test_search_pages_whole_result_and_projects_only_that_page() -> None:
    """命中 500 条时：翻页是真翻页（total/pages 正确），且每页只投影 30 张图。"""
    from wh_local.modules.temu_publish.library import materialize_draft_search, scan_draft_library
    from wh_local.session import Actor

    actor = Actor(id="x", username="x", role="admin", workspace_id="default")
    pp = _WholeTaskAi()
    scan = scan_draft_library(product_processing=pp, pod_service=None, actor=actor, query="商品")

    assert scan["counts"]["ai"] == 499  # 除了「蓝色桌布」那条，其余都匹配

    first = materialize_draft_search(scan, product_processing=pp, actor=actor, page=1, page_size=30)
    assert first["total"] == 499
    assert first["pages"] == 17
    assert len(first["entries"]) == 30
    assert len(pp.media_calls[0]) == 30  # 只给这一页补图

    last = materialize_draft_search(scan, product_processing=pp, actor=actor, page=17, page_size=30)
    assert len(last["entries"]) == 19  # 499 - 16*30
    assert len(pp.media_calls[-1]) == 19


def test_search_respects_source_filter_and_skips_published() -> None:
    """搜索也要认「仅 AI / 仅 POD」和已发布剔除，否则 tab 上的数字对不上列表。"""
    from wh_local.modules.temu_publish.library import materialize_draft_search, scan_draft_library
    from wh_local.session import Actor

    actor = Actor(id="x", username="x", role="admin", workspace_id="default")
    pp = _WholeTaskAi(total=40)
    scan = scan_draft_library(product_processing=pp, pod_service=None, actor=actor, query="商品")

    only_pod = materialize_draft_search(
        scan, product_processing=pp, actor=actor, source="pod", page=1, page_size=30
    )
    assert only_pod["entries"] == [] and only_pod["total"] == 0

    dropped = materialize_draft_search(
        scan,
        product_processing=pp,
        actor=actor,
        page=1,
        page_size=30,
        exclude_ids={"ai:7:0"},
    )
    assert dropped["total"] == 39
    assert all(entry["id"] != "ai:7:0" for entry in dropped["entries"])


def test_draft_library_search_endpoint_scans_whole_task(tmp_path: Path) -> None:
    """接口层：带 q 时走全量扫描，且命中集合按关键字缓存（翻页/切来源不重扫）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from wh_local.modules.temu_publish.router import create_temu_publish_router
    from wh_local.session import Actor, actor_from_authorization

    pp = _WholeTaskAi(total=60)
    scans = {"n": 0}
    original = pp.task_search_preview

    def counting_search(*args, **kwargs):  # noqa: ANN001, ANN202
        scans["n"] += 1
        return original(*args, **kwargs)

    pp.task_search_preview = counting_search

    db_path = tmp_path / "runtime.sqlite3"
    init_db(db_path)
    app = FastAPI()
    app.include_router(create_temu_publish_router(db_path, product_processing=pp))
    app.dependency_overrides[actor_from_authorization] = lambda: Actor(
        id="search-tester", username="t", role="admin", workspace_id="default"
    )
    http = TestClient(app)

    # 第 48 个商品，普通列表（前 20 个）里没有它
    found = http.get("/api/temu-publish/draft-library?q=商品48")
    assert found.status_code == 200
    body = found.json()
    assert body["total"] == 1
    assert body["entries"][0]["title"] == "商品48"
    assert body["entries"][0]["images"] == ["/api/x/48.jpg"]
    # 计数跟着命中走
    assert body["counts"]["ai"] == 1
    assert body["has_more"] is False

    after_first = scans["n"]
    assert after_first >= 1

    # 同一个关键字再查一次（翻页 / 切来源）不再扫任务
    again = http.get("/api/temu-publish/draft-library?q=商品48&page=1")
    assert again.status_code == 200
    assert scans["n"] == after_first


def _real_pp_service(tmp_path: Path):  # noqa: ANN202
    from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
    from wh_local.modules.product_processing.infrastructure.database import create_database
    from wh_local.modules.product_processing.infrastructure.repository import (
        ProductProcessingRepository,
    )
    from wh_local.modules.product_processing.service import ProductProcessingService

    return ProductProcessingService(
        ProductProcessingRepository(
            create_database(f"sqlite:///{(tmp_path / 'pp.sqlite3').as_posix()}")
        ),
        ProductProcessingAssets(tmp_path / "assets"),
    )


def _seed_search_task(service, titles: list[str], categories: list[str], ws: str = "default") -> int:
    drafts = []
    for index, title in enumerate(titles):
        draft, _ = service.create_draft(
            {
                "source_type": "manual",
                "title": title,
                "product_name": title,
                "skc": f"SKC-{index}",
                # 采集链路就是把类目路径放在 payload 顶层
                "category_path": ["家居", categories[index]],
            },
            workspace_id=ws,
        )
        drafts.append(draft)
    task = service.repository.create_task(
        title="春季批次",
        preflight_only=False,
        settings={"target_site": "US", "target_language": "en"},
        drafts=drafts,
        idempotency_key=None,
        workspace_id=ws,
    )
    results = [
        {
            "item_id": item["id"],
            "status": "completed",
            "result": {
                "optimized_title": titles[pos],
                "sku": f"SKU-{pos}",
                "provider_status_classes": {"four_grid": "ok"},
            },
        }
        for pos, item in enumerate(task["items"])
    ]
    service.repository.finish_task(
        task["id"],
        results,
        output_file="",
        error_report_file="",
        video_manifest_file="",
        workspace_id=ws,
    )
    return int(task["id"])


def test_task_search_matches_title_sku_category_in_sql(tmp_path: Path) -> None:
    """关键字匹配下推到 SQLite（JSON1 + LIKE）：标题 / 货号 / 类目 / 通配符转义。

    这条是「全量搜索」的底座：以前是把几百条 result_json 读进 Python 再比对，
    现在一行 SQL 就筛完，命中多少才解析多少。
    """
    service = _real_pp_service(tmp_path)
    titles = ["纯棉桌布", "涤纶桌布", "100%棉麻垫", "硅胶隔热垫"]
    task_id = _seed_search_task(service, titles, ["桌布", "桌布", "餐垫", "餐垫"])

    assert len(service.repository.search_task_items(task_id, "桌布")) == 2
    # 货号
    assert len(service.repository.search_task_items(task_id, "sku-2")) == 1
    # 类目：raw_payload.category_path 是列表，也要能搜到
    assert len(service.repository.search_task_items(task_id, "家居")) == 4
    # 通配符必须转义：搜 "%" 只命中真的含 % 的那条，而不是「全部」
    assert len(service.repository.search_task_items(task_id, "%")) == 1
    assert len(service.repository.search_task_items(task_id, "100%")) == 1
    # 搜的是批次名 → 整个任务都算命中
    assert len(service.repository.search_task_items(task_id, "春季", match_all=True)) == 4
    assert service.repository.search_task_items(task_id, "   ") == []

    preview = service.task_search_preview(task_id, "桌布", workspace_id="default")
    assert [item["title"] for item in preview["items"]] == ["纯棉桌布", "涤纶桌布"]
    # 没投影图片，但「能不能导出」的结论要在
    assert all(item["media_loaded"] is False for item in preview["items"])
    assert all(item["exportable"] is True for item in preview["items"])
    assert all(item["main_image"] == "" for item in preview["items"])


def test_search_warns_when_media_projection_fails() -> None:
    """图片投影挂了不能静默返回空列表：用户会以为库里没有这个商品。"""
    from wh_local.modules.temu_publish.library import materialize_draft_search, scan_draft_library
    from wh_local.session import Actor

    class BrokenMedia(_WholeTaskAi):
        def task_preview_media(self, task_id, draft_ids, *, workspace_id=""):  # noqa: ANN001
            raise RuntimeError("media bundle broken")

    actor = Actor(id="x", username="x", role="admin", workspace_id="default")
    pp = BrokenMedia(total=40)
    scan = scan_draft_library(product_processing=pp, pod_service=None, actor=actor, query="商品")
    page = materialize_draft_search(scan, product_processing=pp, actor=actor, page=1, page_size=30)

    assert page["entries"] == []  # 没图确实没法发布
    assert any("图片读取失败" in warning for warning in page["warnings"])


def test_search_cache_evicts_oldest_when_full() -> None:
    """搜索结果缓存要有上限：一条命中集合可能上千条，连打十几个关键字不能把内存吃光。"""
    from wh_local.modules.temu_publish import router as router_module

    cache = router_module._SEARCH_CACHE
    cache.clear()
    try:
        limit = router_module._SEARCH_CACHE_MAX
        now = 1_000_000.0
        # 全是「新鲜」的（没过期）：只删过期的那种清理在这里不够
        for index in range(limit + 20):
            cache[("u", f"q{index}")] = (now - index, {"ai": [], "pod": []})
        router_module._evict_search_cache(now)
        assert len(cache) <= limit
        # 淘汰的是最老的（时间戳最小），不是刚刚查的
        assert ("u", "q0") in cache
        assert ("u", f"q{limit + 19}") not in cache
    finally:
        cache.clear()


def test_search_cache_drops_expired_entries() -> None:
    """过期条目优先清掉，别占着位置。"""
    from wh_local.modules.temu_publish import router as router_module

    cache = router_module._SEARCH_CACHE
    cache.clear()
    try:
        now = 1_000_000.0
        ttl = router_module._SEARCH_CACHE_TTL
        for index in range(router_module._SEARCH_CACHE_MAX):
            cache[("u", f"old{index}")] = (now - ttl - 10, {})
        router_module._evict_search_cache(now)
        assert cache == {}
    finally:
        cache.clear()


def test_list_task_summaries_skips_items(tmp_path: Path) -> None:
    """批次摘要不读任务条目：搜全库只用批次号/标题/状态，别把 result_json 全拖出来。"""
    service = _real_pp_service(tmp_path)
    task_id = _seed_search_task(service, ["纯棉桌布", "涤纶桌布"], ["桌布", "桌布"])

    tasks, total = service.repository.list_task_summaries(10, "default")
    assert total == 1
    assert len(tasks) == 1
    summary = tasks[0]
    assert summary["task_id"] == task_id
    assert summary["status"] == "completed"
    assert summary["success_count"] == 2
    # 明确不该带的字段（带 items 就要把整任务 result_json 读出来）
    assert "items" not in summary
