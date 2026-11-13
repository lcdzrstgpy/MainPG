from __future__ import annotations

from io import BytesIO

import openpyxl

from wh_local.modules.profit_activity.domain.workbooks import filter_activity_workbook


def _workbook(row_count: int) -> bytes:
    workbook = openpyxl.Workbook()
    price = workbook.active
    price.title = "活动申报价格"
    price.append(["活动类型(活动主题）", "SPU ID", "SKC ID", "活动申报价格", "站点"])
    for index in range(row_count):
        price.append([f"活动{index % 5}", f"SPU{index}", f"SKC{index}", 100 + index, "美国站"])
    inventory = workbook.create_sheet("活动库存")
    inventory.append(["活动类型(活动主题）", "SPU ID", "活动库存"])
    for index in range(row_count):
        inventory.append([f"活动{index % 5}", f"SPU{index}", 10])
    content = BytesIO()
    workbook.save(content)
    workbook.close()
    return content.getvalue()


def test_filter_rebuilds_rows_without_losing_header_or_layout() -> None:
    calls = {"count": 0}

    def evaluate(candidate_ids, price):  # noqa: ANN001
        calls["count"] += 1
        keep = calls["count"] % 2 == 0
        return {
            "keep": keep,
            "decision": "eligible" if keep else "excluded",
            "reason_code": "net_profit_passed" if keep else "below_threshold",
            "net_profit": 20.0,
            "profit_rate": 0.3,
            "matched_id": candidate_ids[0],
        }

    result = filter_activity_workbook(_workbook(200), site="US", evaluate=evaluate)

    assert result["removed_row_count"] == 100
    assert result["kept_row_count"] == 100
    workbook = openpyxl.load_workbook(BytesIO(result["filtered_bytes"]), data_only=True)
    try:
        price = workbook["活动申报价格"]
        assert price.max_row == 101
        assert price.cell(1, 1).value == "活动类型(活动主题）"
        assert price.cell(2, 3).value == "SKC1"
        inventory = workbook["活动库存"]
        assert inventory.max_row == 101
        assert inventory.cell(1, 1).value == "活动类型(活动主题）"
    finally:
        workbook.close()

    removed = openpyxl.load_workbook(BytesIO(result["removed_bytes"]), data_only=True)
    try:
        assert removed.active.max_row == 101
    finally:
        removed.close()


def test_filter_excludes_activity_rows_priced_in_foreign_currency() -> None:
    """利润口径全按人民币配置，非 CNY 的申报价不能被当成 CNY 直接计算。"""
    workbook = openpyxl.Workbook()
    price = workbook.active
    price.title = "活动申报价格"
    price.append(["活动类型(活动主题）", "SPU ID", "SKC ID", "活动申报价格", "站点", "币种"])
    price.append(["活动A", "SPU1", "SKC1", 100, "美国站", "CNY"])
    price.append(["活动A", "SPU2", "SKC2", 100, "美国站", "人民币"])
    price.append(["活动A", "SPU3", "SKC3", 100, "美国站", ""])
    price.append(["活动A", "SPU4", "SKC4", 20, "美国站", "USD"])
    content = BytesIO()
    workbook.save(content)
    workbook.close()

    def evaluate(candidate_ids, price):  # noqa: ANN001
        return {
            "keep": True, "decision": "eligible", "reason_code": "net_profit_passed",
            "net_profit": 20.0, "profit_rate": 0.3, "matched_id": candidate_ids[0],
        }

    result = filter_activity_workbook(content.getvalue(), site="US", evaluate=evaluate)

    assert result["kept_row_count"] == 3
    assert result["removed_row_count"] == 1
    assert result["currency_counts"] == {"CNY": 3, "USD": 1}
    assert [row["reason_code"] for row in result["removed_rows"]] == ["unsupported_currency"]
    assert result["removed_rows"][0]["values"][2] == "SKC4"
