"""Temu 商品发布配套：网关客户端缓存/错误/重试与 goods.add 请求体组装测试。"""

from __future__ import annotations

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

    def add_goods(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.submitted.append(payload)
        return {"goodsId": "G-1", "goodsName": payload.get("productName")}


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
    """盈銮网关不支持 catsmandatory.get（实测 sign is invalid）→ 必须退回 attrs 必填校验。"""

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
    """CLIENT_TOKEN_INVALID 要说清是「盈銮网关凭据」而不是店铺 token，并给排查方向。"""
    from wh_local.modules.temu_publish.router import _gateway_message

    text = _gateway_message("CLIENT_TOKEN_INVALID", "client_id or token is invalid")
    assert text.startswith("Temu 拒绝了这次提交：client_id or token is invalid")
    assert "CLIENT_TOKEN_INVALID" in text
    assert "盈銮网关" in text and "access_token" in text

    net = _gateway_message("NETWORK_ERROR", "网关网络请求失败：SSLEOFError")
    assert "系统代理" in net and "WH_TEMU_PROXY" in net

    # 没登记的错误码不加多余的话，保持上游原话
    plain = _gateway_message("SOME_NEW_CODE", "boom")
    assert plain == "Temu 拒绝了这次提交：boom（错误码 SOME_NEW_CODE）"


def test_unbinding_keeps_gateway_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """解绑只清店铺授权：盈銮 client_id / token 是服务商那层的，换店还要用，必须留着。"""
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
    assert 2002 in applicable and 2003 not in applicable
    assert dropped == ["不可充电电池"]

    # 父级没选 → 两个子属性都不出现
    applicable, dropped = _resolve_applicable_properties(catalog, [])
    assert 2002 not in applicable and 2003 not in applicable
    assert set(dropped) == {"可充电电池", "不可充电电池"}
    # 无联动规则的属性永远在
    assert 2001 in applicable and 2004 in applicable


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


def test_dimension_check_requires_mall_id(tmp_path: Path) -> None:
    """尺寸图检测要 mallId；缺了要给出中文提示，而不是发一个必然失败的请求。"""
    client = SearchClient()
    service = TemuPublishService(tmp_path / "runtime.sqlite3", client=client)
    with pytest.raises(ValueError, match="mallId"):
        service.dimension_check("https://img.example.com/a.jpg", mall_id="")


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
