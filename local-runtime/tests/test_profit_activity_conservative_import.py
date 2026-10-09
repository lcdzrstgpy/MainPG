from __future__ import annotations

from pathlib import Path

from wh_local.modules.profit_activity.domain.workbooks import new_workbook, workbook_bytes
from wh_local.modules.profit_activity.service import create_profit_activity_service


def test_import_merges_repeated_and_existing_product_conservatively(tmp_path: Path) -> None:
    service = create_profit_activity_service(tmp_path / "profit.sqlite3")
    try:
        _import(service, [["55555", 100, 10, 0.2]])
        result = _import(service, [["55555", 58.41, 9.3, 0.4], ["55555", 60.52, 15.4, 0.3]])

        assert result["imported"] == 0
        assert result["replaced"] == 1
        assert len(result["products"]) == 1
        product = service.list_products(site="US")[0]
        assert (product["selling_price"], product["cost_price"], product["weight_kg"]) == (58.41, 15.4, 0.4)
    finally:
        service.close()


def _import(service, rows: list[list[object]]) -> dict:
    workbook = new_workbook()
    sheet = workbook.active
    sheet.append(["SKC ID", "售价", "成本", "重量KG"])
    for row in rows:
        sheet.append(row)
    preview = service.preview_import(workbook_bytes(workbook), "products.xlsx", "US")
    return service.confirm_import(preview["import_id"], None, "replace")
