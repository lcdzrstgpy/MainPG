from __future__ import annotations

import io

from openpyxl import Workbook

from wh_local.modules.order_profit.settlement_file import parse_settlement_file


CSV_TEXT = """结算中心导出（测试）
PO单号,商品信息*销售件数,币种,销售回款,销售冲回,运费回款,运费冲回
PO-211-03146780759671242,"2/3/4 Layers White Closet Storage × 1 SKU ID: 49741038378 申报价格 ¥311.50 是否活动价：是",CNY,"¥311.50","-¥311.50","¥0.00","¥0.00"
PO-211-06248131016313303,"3D Printed Retro Boot Shaped Pen Holder × 2 SKU ID: 13515021587 申报价格 ¥52.10",CNY,"¥52.10","¥0.00","¥20.30","¥0.00"
"""


def test_parse_csv_settlement_export() -> None:
    rows, header = parse_settlement_file(CSV_TEXT.encode("utf-8-sig"), "settlement.csv")
    assert "PO单号" in header
    assert len(rows) == 2
    first = rows[0]
    assert first["parent_order_sn"] == "PO-211-03146780759671242"
    assert first["sales_payback"] == 311.5
    assert first["sales_reversal"] == -311.5
    assert first["freight_payback"] == 0
    assert first["declared_price"] == 311.5
    assert first["quantity"] == 1
    assert first["sku_id"] == "49741038378"
    assert rows[1]["quantity"] == 2


def test_parse_xlsx_settlement_export() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["PO单号", "商品信息*销售件数", "币种", "销售回款", "销售冲回", "运费回款", "运费冲回"])
    sheet.append([
        "PO-211-0001",
        "Sample Goods × 1 SKU ID: 123 申报价格 ¥10.50",
        "CNY",
        10.5,
        -2.5,
        1.2,
        0,
    ])
    buffer = io.BytesIO()
    workbook.save(buffer)
    rows, _header = parse_settlement_file(buffer.getvalue(), "settlement.xlsx")
    assert len(rows) == 1
    assert rows[0]["parent_order_sn"] == "PO-211-0001"
    assert rows[0]["declared_price"] == 10.5
    assert rows[0]["sales_payback"] == 10.5
    assert rows[0]["sales_reversal"] == -2.5


def test_reject_file_without_header() -> None:
    try:
        parse_settlement_file("foo,bar\n1,2\n".encode(), "bad.csv")
    except ValueError as error:
        assert "表头" in str(error)
    else:  # pragma: no cover
        raise AssertionError("should reject file without settlement header")
