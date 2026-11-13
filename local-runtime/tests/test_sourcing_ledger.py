"""货源台账：采集匹配、人工确认、历史迁移、多货源与利润（P1 + P2）。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from wh_local.modules.sourcing_ledger.repository import SourcingLedgerRepository
from wh_local.modules.sourcing_ledger.service import SourcingLedgerService, landed_cost
from wh_local.price_verification.quote_normalizer import QuoteItem

WORKSPACE = "default"


@dataclass
class _Preview:
    net_profit: Decimal
    profit_rate: Decimal
    total_cost: Decimal = Decimal("60")
    gross_profit: Decimal = Decimal("5")


class _FakeProfit:
    """替代利润活动服务：只验证台账把真实站点/成本传进了引擎。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.products: list[dict] = []

    def calculate(self, site_code, selling_price, cost_price, weight_kg):  # noqa: ANN001
        self.calls.append((site_code, selling_price, cost_price, weight_kg))
        return {"preview": _Preview(net_profit=Decimal("5.00"), profit_rate=Decimal("0.2500"))}

    def upsert_product(self, payload, **_kwargs):  # noqa: ANN001
        self.products.append(dict(payload))
        return {"skc": payload.get("skc"), "site": payload.get("site")}

    def delete_product(self, skc, site, **_kwargs):  # noqa: ANN001
        return {"skc": skc, "site": site}


def _database(tmp_path: Path) -> Path:
    path = tmp_path / "workbench.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE product_processing_drafts (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               workspace_id TEXT NOT NULL,
               skc TEXT,
               title TEXT DEFAULT '',
               status TEXT DEFAULT 'draft'
           )"""
    )
    connection.executemany(
        "INSERT INTO product_processing_drafts (workspace_id, skc, title) VALUES (?, ?, ?)",
        [
            (WORKSPACE, "PP-001554", "多层网纱宠物裙"),
            (WORKSPACE, "PP-001555", "另一个商品"),
        ],
    )
    connection.commit()
    connection.close()
    return path


def _service(tmp_path: Path) -> tuple[SourcingLedgerService, SourcingLedgerRepository]:
    repository = SourcingLedgerRepository(_database(tmp_path))
    return SourcingLedgerService(repository), repository


def _item(**overrides) -> QuoteItem:
    values = {
        "skc_id": "468618588060",
        "market_skc_code": "PP-001554",
        "sku_id": "572860893279",
        "sku_merchant_code": "6297074883160",
        "sku_attribute_text": "L-Royal Gown Dress - Blue",
        "site": "美国站",
        "original_declared_price_cny": Decimal("150.00"),
        "adjusted_declared_price_cny": Decimal("43.33"),
        "product_title": "Multi-layer Tulle Tutu Pet Dress",
        "captured_at": "2026-10-22T09:49:57Z",
    }
    values.update(overrides)
    return QuoteItem(**values)


def _declared_rows(path: Path) -> list[dict]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute("SELECT * FROM product_declared_prices")]
    finally:
        connection.close()


def test_capture_matches_by_product_code(tmp_path: Path) -> None:
    path = _database(tmp_path)
    service = SourcingLedgerService(SourcingLedgerRepository(path))

    summary = service.ingest_capture(
        workspace_id=WORKSPACE, batch_id="batch-1", items=[_item(), _item(sku_id="482939892533")]
    )

    assert summary["matched"] == 1 and summary["pending"] == 0 and summary["rows"] == 2
    rows = _declared_rows(path)
    assert {row["skc"] for row in rows} == {"PP-001554"}
    assert {row["match_method"] for row in rows} == {"code"}
    assert service.pending_matches(WORKSPACE) == []


def test_same_sku_code_with_multiple_attributes_keeps_every_variant(tmp_path: Path) -> None:
    """商家一个 SKU 货号挂多个属性时，每个属性都要留一行明细。"""
    path = _database(tmp_path)
    service = SourcingLedgerService(SourcingLedgerRepository(path))

    service.ingest_capture(
        workspace_id=WORKSPACE,
        batch_id="batch-1",
        items=[
            _item(sku_id="", sku_merchant_code="866344969878", sku_attribute_text="1cm Width - Black"),
            _item(sku_id="", sku_merchant_code="866344969878", sku_attribute_text="0.8cm Width - Gold"),
            _item(sku_id="", sku_merchant_code="866344969878", sku_attribute_text="2cm Width - White"),
        ],
    )

    rows = _declared_rows(path)
    assert len(rows) == 3
    assert {row["sku_attribute_text"] for row in rows} == {
        "1cm Width - Black",
        "0.8cm Width - Gold",
        "2cm Width - White",
    }
    assert len({row["sku_ref"] for row in rows}) == 3


def test_list_items_sorts_by_net_profit_desc(tmp_path: Path) -> None:
    """列表默认按净利润倒序；没有利润的商品即使刚更新也排在最后。"""

    class _ProfitByPrice:
        def calculate(self, site_code, selling_price, cost_price, weight_kg):  # noqa: ANN001
            return {
                "preview": _Preview(
                    net_profit=Decimal(selling_price) - Decimal(cost_price),
                    profit_rate=Decimal("0.3000"),
                )
            }

    path = _database(tmp_path)
    repo = SourcingLedgerRepository(path)
    service = SourcingLedgerService(repo, profit_service=_ProfitByPrice())
    service.ingest_capture(
        workspace_id=WORKSPACE,
        batch_id="b1",
        items=[
            _item(
                skc_id="600000000001",
                market_skc_code="PP-001554",
                sku_id="sku-high",
                adjusted_declared_price_cny=Decimal("100.00"),
            ),
            _item(
                skc_id="600000000002",
                market_skc_code="PP-001555",
                sku_id="sku-low",
                adjusted_declared_price_cny=Decimal("60.00"),
            ),
        ],
    )
    for key, url in (
        ("PP-001554", "https://detail.1688.com/offer/583023218037.html"),
        ("PP-001555", "https://detail.1688.com/offer/1062682536505.html"),
    ):
        service.save_source(
            WORKSPACE,
            key,
            {"source_url": url, "price_cny": "10"},
        )
    # 只有货源、没有申报价：没有利润，必须排在最后。
    repo.insert_source(
        {
            "workspace_id": WORKSPACE,
            "product_key": "temu:999",
            "source_url": "https://detail.1688.com/offer/999.html",
            "price_cny": "10",
        }
    )

    items = service.list_items(WORKSPACE)["items"]

    assert [item["skc"] for item in items] == ["PP-001554", "PP-001555", "temu:999"]
    assert [item["profit"]["net_profit"] for item in items[:2]] == ["90.0000", "50.0000"]


def test_list_items_searches_multiple_keywords(tmp_path: Path) -> None:
    """关键词支持换行/空格/逗号分隔：一次贴多个货号，任意命中即返回。"""
    path = _database(tmp_path)
    service = SourcingLedgerService(SourcingLedgerRepository(path))
    service.ingest_capture(workspace_id=WORKSPACE, batch_id="b1", items=[_item()])

    def skcs(keyword: str) -> list[str]:
        return [item["skc"] for item in service.list_items(WORKSPACE, keyword=keyword)["items"]]

    assert skcs("468618588060\nPP-001554") == ["PP-001554"]
    assert skcs("PP-999999, 468618588060") == ["PP-001554"]
    assert skcs("PP-999999 466666666666") == []


def test_list_items_keyword_matches_exactly(tmp_path: Path) -> None:
    """精准查询：必须整串等于货号 / SKC，子串和标题片段都不再命中。"""
    service = SourcingLedgerService(SourcingLedgerRepository(_database(tmp_path)))
    service.ingest_capture(workspace_id=WORKSPACE, batch_id="b1", items=[_item()])

    def skcs(keyword: str) -> list[str]:
        return [item["skc"] for item in service.list_items(WORKSPACE, keyword=keyword)["items"]]

    assert skcs("PP-001554") == ["PP-001554"]
    assert skcs("pp-001554") == ["PP-001554"]  # 大小写不敏感，但仍然要整串相等
    assert skcs("468618588060") == ["PP-001554"]
    assert skcs("PP-0015") == []  # 子串不命中
    assert skcs("PP-001554 多层网纱") == ["PP-001554"]  # 多词仍是任一精准命中


def test_capture_falls_back_to_exact_listed_title(tmp_path: Path) -> None:
    path = _database(tmp_path)
    service = SourcingLedgerService(SourcingLedgerRepository(path))
    service.record_listed_titles(WORKSPACE, [("PP-001555", "Cute Pet Dress for Small Dogs")])

    summary = service.ingest_capture(
        workspace_id=WORKSPACE,
        batch_id="batch-1",
        items=[
            _item(
                skc_id="111111111111",
                market_skc_code="",
                product_title="Cute, Pet Dress for Small Dogs!",
            )
        ],
    )

    assert summary["matched"] == 1
    rows = _declared_rows(path)
    assert rows[0]["skc"] == "PP-001555"
    assert rows[0]["match_method"] == "title"


def test_similar_title_goes_to_pending_never_auto_matches(tmp_path: Path) -> None:
    path = _database(tmp_path)
    service = SourcingLedgerService(SourcingLedgerRepository(path))
    service.record_listed_titles(WORKSPACE, [("PP-001555", "Cute Pet Dress for Small Dogs and Cats")])

    summary = service.ingest_capture(
        workspace_id=WORKSPACE,
        batch_id="batch-1",
        items=[
            _item(
                skc_id="222222222222",
                market_skc_code="",
                product_title="Cute Pet Dress for Small Dogs and Cats Blue",
            )
        ],
    )

    assert summary["matched"] == 0 and summary["pending"] == 1
    rows = _declared_rows(path)
    assert rows[0]["skc"] == ""
    assert rows[0]["match_status"] == "ambiguous"
    pending = service.pending_matches(WORKSPACE)
    assert pending[0]["match_candidates"][0]["skc"] == "PP-001555"


def test_confirm_match_writes_binding_and_rekeys_sources(tmp_path: Path) -> None:
    path = _database(tmp_path)
    repo = SourcingLedgerRepository(path)
    service = SourcingLedgerService(repo)
    repo.insert_source(
        {
            "workspace_id": WORKSPACE,
            "product_key": "temu:468618588060",
            "key_kind": "market_skc",
            "source_url": "https://detail.1688.com/offer/583023218037.html",
            "price_cny": "10",
            "domestic_freight_cny": "6",
            "moq": 3,
        }
    )

    result = service.confirm_match(
        workspace_id=WORKSPACE,
        market_skc_id="468618588060",
        site="美国站",
        skc="PP-001554",
    )

    assert result["rows"] == 0 and result["sources_moved"] == 1
    sources = service.sources_for(WORKSPACE, "PP-001554")
    assert len(sources) == 1
    assert repo.market_link(WORKSPACE, "temu", "468618588060", "美国站")["skc"] == "PP-001554"


def test_confirm_match_rejects_unknown_product(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    try:
        service.confirm_match(
            workspace_id=WORKSPACE,
            market_skc_id="468618588060",
            site="美国站",
            skc="PP-999999",
        )
    except LookupError:
        return
    raise AssertionError("未知货号必须被拒绝")


def test_legacy_sources_migrate_once(tmp_path: Path) -> None:
    path = _database(tmp_path)
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE price_verification_skc_source_links (
               id TEXT PRIMARY KEY, workspace_id TEXT, batch_id TEXT, skc_id TEXT, offer_id TEXT,
               source_url TEXT, source_title TEXT, main_image_url TEXT, price_cny TEXT,
               weight_kg TEXT, moq INTEGER, domestic_freight_cny TEXT, source_decision TEXT,
               note TEXT, status TEXT, product_title TEXT, site TEXT, selling_price TEXT
           )"""
    )
    connection.execute(
        """INSERT INTO price_verification_skc_source_links
           (id, workspace_id, batch_id, skc_id, offer_id, source_url, source_title, price_cny,
            moq, domestic_freight_cny, status, product_title, site)
           VALUES ('link-1', ?, 'batch-1', '468618588060', '583023218037',
                   'https://detail.1688.com/offer/583023218037.html', '货源', '10', 1, '2',
                   'active', '标题', '美国站')""",
        (WORKSPACE,),
    )
    connection.commit()
    connection.close()

    service = SourcingLedgerService(SourcingLedgerRepository(path))

    first = service.migrate_legacy_sources(WORKSPACE)
    second = service.migrate_legacy_sources(WORKSPACE)

    assert first["migrated"] == 1 and first["skipped"] == 0
    assert second["migrated"] == 0 and second["skipped"] == 1
    sources = service.sources_for(WORKSPACE, "temu:468618588060")
    assert len(sources) == 1
    # 单价 10 + 运费 2（按一件）
    assert sources[0]["landed_cost_cny"] == "12.0000"
    assert sources[0]["source_kind"] == "image_search"


def _collection_database(tmp_path: Path) -> Path:
    """带采集原始数据的库：草稿 raw_payload + 任务子项（PP 货号）。"""
    path = tmp_path / "workbench.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE product_processing_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL,
            skc TEXT,
            title TEXT DEFAULT '',
            image_url TEXT DEFAULT '',
            raw_payload_json TEXT DEFAULT '{}',
            status TEXT DEFAULT 'processed'
        );
        CREATE TABLE product_processing_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL
        );
        CREATE TABLE product_processing_task_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            product_draft_id INTEGER,
            skc TEXT,
            title TEXT DEFAULT '',
            image_url TEXT DEFAULT '',
            status TEXT DEFAULT 'completed',
            result_json TEXT DEFAULT '{}'
        );
        """
    )
    payload = {
        "source_url": "https://detail.1688.com/offer/583023218037.html",
        "source_title": "宠物蕾丝连衣裙",
        "shop_name": "示例店铺",
        "main_image_url": "https://cbu01.alicdn.com/a.jpg",
        "price_cny": "9.00",
        "min_order_quantity": 2,
        "freight_cny": "4",
        "offer_id": "583023218037",
        "source_variant_records": [{"price_cny": "9.00"}, {"price_cny": "11.00"}],
    }
    connection.execute(
        "INSERT INTO product_processing_drafts (workspace_id, title, raw_payload_json) VALUES (?,?,?)",
        (WORKSPACE, "宠物蕾丝连衣裙", json.dumps(payload, ensure_ascii=False)),
    )
    connection.execute(
        "INSERT INTO product_processing_tasks (workspace_id) VALUES (?)", (WORKSPACE,)
    )
    connection.execute(
        "INSERT INTO product_processing_task_items (task_id, product_draft_id, skc, title) "
        "VALUES (1, 1, 'PP-001554', 'Pet Lace Dress')"
    )
    connection.commit()
    connection.close()
    return path


def test_collected_source_seeded_from_draft_payload(tmp_path: Path) -> None:
    path = _collection_database(tmp_path)
    service = SourcingLedgerService(SourcingLedgerRepository(path))

    created = service.ensure_collected_sources(WORKSPACE)
    sources = service.sources_for(WORKSPACE, "PP-001554")

    assert created == 1
    assert len(sources) == 1
    source = sources[0]
    assert source["source_url"] == "https://detail.1688.com/offer/583023218037.html"
    assert source["price_cny"] == "9.0000"
    # 到手成本 = 9 + 4（按一件，不再按 MOQ 摊销）
    assert source["landed_cost_cny"] == "13.0000"
    assert source["source_kind"] == "collect"
    assert source["moq"] == 1
    # 幂等：已有货源时不再重复创建
    assert service.ensure_collected_sources(WORKSPACE) == 0


def test_landed_cost_ignores_moq() -> None:
    assert landed_cost("10", "6", 3) == "16.0000"
    assert landed_cost("", "6", 3) == ""
    assert landed_cost("10", "", 0) == "10.0000"


def test_sources_default_lowest_then_manual_primary(tmp_path: Path) -> None:
    path = _database(tmp_path)
    profit = _FakeProfit()
    service = SourcingLedgerService(SourcingLedgerRepository(path), profit_service=profit)
    service.ingest_capture(workspace_id=WORKSPACE, batch_id="b1", items=[_item()])

    service.save_source(
        WORKSPACE,
        "PP-001554",
        {
            "source_url": "https://detail.1688.com/offer/583023218037.html",
            "price_cny": "10",
            "moq": 3,
            "domestic_freight_cny": "6",
        },
    )
    detail = service.save_source(
        WORKSPACE,
        "PP-001554",
        {
            "source_url": "https://detail.1688.com/offer/1062682536505.html",
            "price_cny": "8",
            "weight_kg": "0.2",
        },
    )

    # 默认不设主货源：按到手成本最低（8 < 12）核算。
    assert len(detail["sources"]) == 2
    item = next(
        row for row in service.list_items(WORKSPACE)["items"] if row["skc"] == "PP-001554"
    )
    assert item["site_code"] == "US"
    # 净利润 5 元 < 10 元门槛，属于利润偏低，不可入库。
    assert item["status"] == "low_profit"
    assert item["best_source"]["landed_cost_cny"] == "8.0000"
    assert item["profit"]["net_profit"] == "5.00"
    assert profit.calls[0][0] == "US"

    cheapest = next(
        source for source in detail["sources"] if source["landed_cost_cny"] == "8.0000"
    )
    detail = service.set_primary_source(WORKSPACE, "PP-001554", cheapest["id"])
    item = next(
        row for row in service.list_items(WORKSPACE)["items"] if row["skc"] == "PP-001554"
    )
    assert item["best_source"]["is_primary"] is True


def test_enter_library_uses_best_source_and_writes_all_groups(tmp_path: Path) -> None:
    path = _database(tmp_path)
    profit = _FakeProfit()
    service = SourcingLedgerService(SourcingLedgerRepository(path), profit_service=profit)
    service.ingest_capture(workspace_id=WORKSPACE, batch_id="b1", items=[_item()])
    service.save_source(
        WORKSPACE,
        "PP-001554",
        {
            "source_url": "https://detail.1688.com/offer/583023218037.html",
            "price_cny": "10",
            "moq": 3,
            "domestic_freight_cny": "6",
        },
    )

    result = service.enter_library(WORKSPACE, "PP-001554", site="美国站")

    assert result["site_code"] == "US"
    payload = profit.products[0]
    # 产品库主键用平台 SKC（与核价入库一致），货号只做关联信息。
    assert payload["skc"] == "468618588060"
    assert payload["selling_price"] == "43.33"
    assert payload["cost_price"] == "16.0000"
    assert payload["source_type"] == "price_verification"
    assert "583023218037" in payload["source_groups_json"]


def test_enter_library_requires_source_and_price(tmp_path: Path) -> None:
    path = _database(tmp_path)
    service = SourcingLedgerService(SourcingLedgerRepository(path), profit_service=_FakeProfit())
    service.ingest_capture(workspace_id=WORKSPACE, batch_id="b1", items=[_item()])
    try:
        service.enter_library(WORKSPACE, "PP-001554", site="美国站")
    except ValueError as error:
        assert "货源" in str(error)
    else:
        raise AssertionError("没有货源时必须拒绝入库")


def test_site_override_changes_site_and_profit(tmp_path: Path) -> None:
    path = _database(tmp_path)
    repo = SourcingLedgerRepository(path)
    service = SourcingLedgerService(repo)
    service.ingest_capture(workspace_id=WORKSPACE, batch_id="b1", items=[_item()])
    repo.insert_source(
        {
            "workspace_id": WORKSPACE,
            "product_key": "PP-001554",
            "source_url": "https://detail.1688.com/offer/583023218037.html",
            "price_cny": "10",
        }
    )

    detail = service.set_item_site(WORKSPACE, "PP-001554", "CO")
    assert detail["site"] == "哥伦比亚" and detail["site_code"] == "CO"
    assert service.list_items(WORKSPACE)["items"][0]["site_code"] == "CO"
    # 申报价仍取采集到的美国站数据，不会因为改站点而消失。
    assert detail["adjusted_min"] == "43.33"

    restored = service.set_item_site(WORKSPACE, "PP-001554", "")
    assert restored["site"] == "美国站" and restored["site_code"] == "US"


def test_clear_source_main_image_keeps_other_fields(tmp_path: Path) -> None:
    path = _database(tmp_path)
    repo = SourcingLedgerRepository(path)
    service = SourcingLedgerService(repo)
    repo.insert_source(
        {
            "workspace_id": WORKSPACE,
            "product_key": "PP-001554",
            "source_url": "https://detail.1688.com/offer/583023218037.html",
            "price_cny": "10",
            "main_image_url": "https://example.com/main.png",
        }
    )
    source = service.sources_for(WORKSPACE, "PP-001554")[0]

    detail = service.clear_source_main_image(WORKSPACE, "PP-001554", source["id"])

    assert detail["sources"][0]["main_image_url"] == ""
    assert repo.source_by_id(WORKSPACE, source["id"])["price_cny"] == "10"


def test_delete_items_removes_ledger_rows_and_images(tmp_path: Path) -> None:
    path = _database(tmp_path)
    repo = SourcingLedgerRepository(path)
    service = SourcingLedgerService(repo)
    service.ingest_capture(workspace_id=WORKSPACE, batch_id="b1", items=[_item()])
    repo.insert_source(
        {
            "workspace_id": WORKSPACE,
            "product_key": "PP-001554",
            "source_url": "https://detail.1688.com/offer/583023218037.html",
            "price_cny": "10",
        }
    )
    source = service.sources_for(WORKSPACE, "PP-001554")[0]
    service.add_source_image(WORKSPACE, "PP-001554", source["id"], "https://example.com/a.png")

    result = service.delete_items(WORKSPACE, ["PP-001554"])

    assert result["deleted"] == ["PP-001554"]
    assert result["sources"] >= 1 and result["declared"] == 1 and result["images"] == 1
    assert service.sources_for(WORKSPACE, "PP-001554") == []
    assert _declared_rows(path) == []


def test_delete_items_blocks_products_already_in_library(tmp_path: Path) -> None:
    path = _database(tmp_path)
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE profit_activity_records (workspace_id TEXT, skc TEXT, site_code TEXT)"
    )
    connection.execute(
        "INSERT INTO profit_activity_records VALUES (?, ?, ?)", (WORKSPACE, "468618588060", "US")
    )
    connection.commit()
    connection.close()
    service = SourcingLedgerService(SourcingLedgerRepository(path))
    service.ingest_capture(workspace_id=WORKSPACE, batch_id="b1", items=[_item()])

    try:
        service.delete_items(WORKSPACE, ["PP-001554"])
    except ValueError as error:
        assert "产品库" in str(error)
    else:
        raise AssertionError("已入产品库的商品必须先移出再删除")
    assert _declared_rows(path) != []


def test_deleted_items_are_not_resurrected_by_ensure(tmp_path: Path) -> None:
    path = _database(tmp_path)
    repo = SourcingLedgerRepository(path)
    service = SourcingLedgerService(repo)
    repo.draft_payload_for = lambda workspace_id, skc: json.dumps(
        {
            "source_url": "https://detail.1688.com/offer/583023218037.html",
            "price_cny": "10",
        }
    )

    assert service.ensure_collected_sources(WORKSPACE, ["PP-001554"]) == 1
    service.delete_items(WORKSPACE, ["PP-001554"])
    assert repo.deleted_product_keys(WORKSPACE) == {"PP-001554"}

    # 启动/详情兜底会重新执行 ensure，墓碑必须阻止商品被自动补回来。
    assert service.ensure_collected_sources(WORKSPACE, ["PP-001554"]) == 0
    assert service.sources_for(WORKSPACE, "PP-001554") == []


def test_list_items_can_filter_by_collection_batch(tmp_path: Path) -> None:
    path = _database(tmp_path)
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE product_processing_tasks (id INTEGER PRIMARY KEY, workspace_id TEXT)"
    )
    connection.execute(
        "CREATE TABLE product_processing_task_items ("
        "id INTEGER PRIMARY KEY, task_id INTEGER, product_draft_id INTEGER, skc TEXT)"
    )
    connection.execute(
        "CREATE TABLE daily_selection_runs (run_id TEXT, workspace_id TEXT, display_name TEXT, "
        "criteria_json TEXT, metadata_json TEXT, created_at TEXT)"
    )
    connection.execute("INSERT INTO product_processing_tasks VALUES (1, ?)", (WORKSPACE,))
    connection.executemany(
        "INSERT INTO product_processing_task_items VALUES (?, 1, ?, ?)",
        [(1, 1, "PP-001554"), (2, 2, "PP-001555")],
    )
    connection.execute(
        "ALTER TABLE product_processing_drafts ADD COLUMN selection_run_id TEXT"
    )
    connection.execute(
        "UPDATE product_processing_drafts SET selection_run_id = "
        "CASE skc WHEN 'PP-001554' THEN 'batch-a' ELSE 'batch-b' END"
    )
    connection.executemany(
        "INSERT INTO daily_selection_runs VALUES (?, ?, ?, '', '', ?)",
        [
            ("batch-a", WORKSPACE, "选品批次 A", "2026-10-01T00:00:00+00:00"),
            ("batch-b", WORKSPACE, "", "2026-10-02T00:00:00+00:00"),
        ],
    )
    connection.commit()
    connection.close()

    repo = SourcingLedgerRepository(path)
    service = SourcingLedgerService(repo)
    for key in ("PP-001554", "PP-001555"):
        repo.insert_source(
            {
                "workspace_id": WORKSPACE,
                "product_key": key,
                "source_url": f"https://detail.1688.com/offer/{key}.html",
                "price_cny": "10",
            }
        )

    payload = service.list_items(WORKSPACE, limit=50)

    assert {batch["batch_id"] for batch in payload["batches"]} == {"batch-a", "batch-b"}
    filtered = service.list_items(WORKSPACE, batch="batch-a", limit=50)
    assert [item["skc"] for item in filtered["items"]] == ["PP-001554"]
    assert filtered["items"][0]["batch_label"] == "选品批次 A"
    assert service.list_items(WORKSPACE, batch="__unassigned__", limit=50)["items"] == []


def test_list_items_seeds_sources_for_shop_collection(tmp_path: Path) -> None:
    """整店/定向采集不走核价同步，列表兜底也要自动生成采集货源。"""
    path = _database(tmp_path)
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE product_processing_tasks (id INTEGER PRIMARY KEY, workspace_id TEXT)"
    )
    connection.execute(
        "CREATE TABLE product_processing_task_items ("
        "id INTEGER PRIMARY KEY, task_id INTEGER, product_draft_id INTEGER, skc TEXT)"
    )
    connection.execute("ALTER TABLE product_processing_drafts ADD COLUMN raw_payload_json TEXT")
    connection.execute("INSERT INTO product_processing_tasks VALUES (1, ?)", (WORKSPACE,))
    connection.execute("INSERT INTO product_processing_task_items VALUES (1, 1, 1, 'PP-001554')")
    connection.execute(
        "UPDATE product_processing_drafts SET raw_payload_json = ? WHERE id = 1",
        (
            json.dumps(
                {
                    "collection_channel": "shop_collection",
                    "source_url": "https://item.taobao.com/item.htm?id=1050216551608",
                    "price_cny": "12.5",
                    "freight_cny": "3",
                }
            ),
        ),
    )
    connection.commit()
    connection.close()

    service = SourcingLedgerService(SourcingLedgerRepository(path))
    payload = service.list_items(WORKSPACE, limit=50)

    assert [item["skc"] for item in payload["items"]] == ["PP-001554"]
    item = payload["items"][0]
    assert item["source_count"] == 1
    assert item["best_source"]["source_url"] == "https://item.taobao.com/item.htm?id=1050216551608"
    assert item["best_source"]["landed_cost_cny"] == "15.5000"


def test_price_selection_and_deleting_sku(tmp_path: Path) -> None:
    """多个 SKU 申报价不同：默认最低价，可指定用哪个，删除后不再回来。"""
    path = _database(tmp_path)
    service = SourcingLedgerService(SourcingLedgerRepository(path))
    service.ingest_capture(
        workspace_id=WORKSPACE,
        batch_id="b1",
        items=[
            _item(sku_id="sku-a", sku_merchant_code="A1", sku_attribute_text="245ml", adjusted_declared_price_cny=Decimal("50")),
            _item(sku_id="sku-b", sku_merchant_code="B1", sku_attribute_text="450ml", adjusted_declared_price_cny=Decimal("30")),
        ],
    )

    default_item = next(item for item in service.list_items(WORKSPACE, limit=10)["items"] if item["skc"] == "PP-001554")
    assert default_item["adjusted_min"] == "30.00"
    assert default_item["price_sku_ref"] == ""

    chosen = service.set_price_selection(WORKSPACE, "PP-001554", "sku-a")
    assert chosen["price_selection"] == "sku-a"
    assert chosen["adjusted_min"] == "50.00"
    assert next(row for row in chosen["declared_prices"] if row["sku_ref"] == "sku-a")["selected"] is True
    assert next(item for item in service.list_items(WORKSPACE, limit=10)["items"] if item["skc"] == "PP-001554")["adjusted_min"] == "50.00"

    row_a = next(row for row in chosen["declared_prices"] if row["sku_ref"] == "sku-a")
    after_delete = service.delete_declared_price(WORKSPACE, "PP-001554", row_a["id"])
    assert after_delete["price_selection"] == ""
    assert after_delete["adjusted_min"] == "30.00"
    assert all(row["sku_ref"] != "sku-a" for row in after_delete["declared_prices"])

    # 同一 SKU 再次被采集回来也不再展示/参与计算。
    service.ingest_capture(
        workspace_id=WORKSPACE,
        batch_id="b2",
        items=[_item(sku_id="sku-a", sku_merchant_code="A1", sku_attribute_text="245ml", adjusted_declared_price_cny=Decimal("50"))],
    )
    refreshed = service.item_detail(WORKSPACE, "PP-001554")
    assert all(row["sku_ref"] != "sku-a" for row in refreshed["declared_prices"])
    assert refreshed["adjusted_min"] == "30.00"


def test_backfill_sku_attributes_from_capture_chunks(tmp_path: Path) -> None:
    """老数据没有 SKU 属性：用历史采集分片里的「SKU属性集」回填。"""
    path = _database(tmp_path)
    service = SourcingLedgerService(SourcingLedgerRepository(path))
    service.ingest_capture(
        workspace_id=WORKSPACE, batch_id="b1", items=[_item(sku_attribute_text="")]
    )
    assert _declared_rows(path)[0]["sku_attribute_text"] == ""

    payload = {
        "captures": {"network": {"records": [{
            "endpoint": "https://agentseller.temu.com/api/price/review",
            "method": "POST", "status": 200,
            "responseJson": {"result": {"priceReviewItemList": [{
                "productSkcId": "468618588060", "productSkuId": "572860893279",
                "productSkuExtCode": "6297074883160", "siteName": "美国站",
                "originalDeclaredPrice": 15000, "adjustedDeclaredPrice": 4333,
                "productName": "Multi-layer Tulle Tutu Pet Dress",
            }]}},
        }]}},
        "dom": {"rows": [{
            "source": "dom_table",
            "text": "Blue Star-12cm SKU货号：6297074883160 | 美国站 | ¥150.00 | ¥43.33",
            "cells": [
                {"header": "SKC(信息)", "text": "468618588060"},
                {"header": "SKU属性集", "text": "Blue Star-12cm SKU货号：6297074883160"},
                {"header": "站点", "text": "美国站"},
                {"header": "原申报价(CNY)", "text": "¥150.00"},
                {"header": "调整后申报价(CNY)", "text": "¥43.33"},
            ],
        }]},
    }
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE price_verification_quote_capture_chunks ("
        "workspace_id TEXT, chunk_id TEXT, batch_id TEXT, capture_json TEXT, captured_at TEXT)"
    )
    connection.execute(
        "INSERT INTO price_verification_quote_capture_chunks VALUES (?, ?, ?, ?, ?)",
        (WORKSPACE, "chunk-1", "b1", json.dumps(payload), "2026-10-23T00:00:00Z"),
    )
    connection.commit()
    connection.close()

    assert service.backfill_sku_attributes(WORKSPACE) == 1
    assert _declared_rows(path)[0]["sku_attribute_text"] == "Blue Star-12cm"


def test_item_store_override_round_trip(tmp_path: Path) -> None:
    """店铺为选填项：保存后详情/列表都能带出，传空可清空。"""
    path = _database(tmp_path)
    service = SourcingLedgerService(SourcingLedgerRepository(path))
    service.ingest_capture(workspace_id=WORKSPACE, batch_id="b1", items=[_item()])

    detail = service.set_item_store(WORKSPACE, "PP-001554", "Vesth ara Shop")
    assert detail["store_name"] == "Vesth ara Shop"
    assert "Vesth ara Shop" in detail["stores"]
    assert next(
        item for item in service.list_items(WORKSPACE, limit=10)["items"]
        if item["skc"] == "PP-001554"
    )["store_name"] == "Vesth ara Shop"

    cleared = service.set_item_store(WORKSPACE, "PP-001554", "")
    assert cleared["store_name"] == ""


def test_enter_library_uses_market_skc_as_key(tmp_path: Path) -> None:
    path = _database(tmp_path)
    profit = _FakeProfit()
    repo = SourcingLedgerRepository(path)
    service = SourcingLedgerService(repo, profit_service=profit)
    service.ingest_capture(workspace_id=WORKSPACE, batch_id="b1", items=[_item()])
    repo.insert_source(
        {
            "workspace_id": WORKSPACE,
            "product_key": "PP-001554",
            "source_url": "https://detail.1688.com/offer/583023218037.html",
            "price_cny": "10",
            "domestic_freight_cny": "6",
            "moq": 3,
        }
    )

    service.enter_library(WORKSPACE, "PP-001554", site="美国站")

    assert profit.products[0]["skc"] == "468618588060"
