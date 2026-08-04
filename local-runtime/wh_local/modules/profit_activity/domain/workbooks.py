from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Any, Iterable


_HEADER_ALIASES = {
    "skc": {"skc", "skc id", "商品id", "商品 id", "商品编号", "产品id", "产品 id"},
    "selling_price": {"售价", "销售价", "售卖价", "selling price", "price"},
    "cost_price": {"成本", "成本价", "采购成本", "cost", "cost price"},
    "weight_kg": {"重量", "重量kg", "重量 kg", "weight", "weight kg"},
    "note": {"备注", "说明", "note"},
    "source_url": {"货源", "货源链接", "采购链接", "source", "source url"},
}


def parse_product_workbook(workbook_bytes: bytes, site: str, duplicate_keys: set[tuple[str, str]]) -> list[dict[str, Any]]:
    workbook = _load_workbook(workbook_bytes)
    rows: list[dict[str, Any]] = []
    for worksheet in workbook.worksheets:
        header_map = _headers(worksheet)
        for row_number, cells in enumerate(worksheet.iter_rows(min_row=2, values_only=True), start=2):
            values = {field: _cell(cells, index) for field, index in header_map.items()}
            if not any(str(value or "").strip() for value in values.values()):
                continue
            skc = str(values.get("skc") or "").strip()
            blockers: list[str] = []
            warnings: list[str] = []
            if not skc:
                blockers.append("missing_skc")
            numeric = {name: _decimal(values.get(name)) for name in ("selling_price", "cost_price", "weight_kg")}
            for name, value in numeric.items():
                if value is None or value <= 0:
                    blockers.append(f"invalid_{name}")
            is_duplicate = (site, skc) in duplicate_keys if skc else False
            if is_duplicate:
                warnings.append("duplicate_skc")
            rows.append({
                "row_id": f"{worksheet.title}:{row_number}", "worksheet": worksheet.title,
                "row_number": row_number, "status": "blocked" if blockers else "ready",
                "warnings": warnings, "blockers": blockers, "site": site, "skc": skc,
                "product_id": skc, "product_id_type": "skc", "product_id_label": "SKC",
                "selling_price": _number(numeric["selling_price"]), "cost_price": _number(numeric["cost_price"]),
                "weight_kg": _number(numeric["weight_kg"]), "domestic_fee": None,
                "note": str(values.get("note") or "").strip(),
                "source_text": str(values.get("source_url") or "").strip(),
                "source_url": str(values.get("source_url") or "").strip(),
                "table_profit": None, "table_profit_rate": None,
                "has_product_image": False, "has_source_image": False, "is_duplicate": is_duplicate,
            })
    return rows


def parse_activity_workbook(workbook_bytes: bytes) -> tuple[object, list[tuple[object, int, str]]]:
    workbook = _load_workbook(workbook_bytes)
    rows: list[tuple[object, int, str]] = []
    for worksheet in workbook.worksheets:
        header_map = _headers(worksheet)
        skc_index = header_map.get("skc")
        if skc_index is None:
            continue
        for row_number, cells in enumerate(worksheet.iter_rows(min_row=2, values_only=True), start=2):
            skc = str(_cell(cells, skc_index) or "").strip()
            if skc:
                rows.append((worksheet, row_number, skc))
    return workbook, rows


def new_workbook():
    try:
        from openpyxl import Workbook
    except ImportError as exc:
        raise ValueError("openpyxl is required for Excel support; install openpyxl") from exc
    return Workbook()


def workbook_bytes(workbook) -> bytes:
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _load_workbook(workbook_bytes: bytes):
    if not workbook_bytes:
        raise ValueError("uploaded workbook is empty")
    try:
        from openpyxl import load_workbook
        return load_workbook(BytesIO(workbook_bytes), data_only=False)
    except ImportError as exc:
        raise ValueError("openpyxl is required for Excel support; install openpyxl") from exc
    except Exception as exc:
        raise ValueError("invalid Excel workbook") from exc


def _headers(worksheet) -> dict[str, int]:
    first_row = next(worksheet.iter_rows(min_row=1, max_row=1, values_only=True), ())
    result: dict[str, int] = {}
    for index, raw in enumerate(first_row):
        key = _normalize_header(raw)
        for field, aliases in _HEADER_ALIASES.items():
            if key in aliases and field not in result:
                result[field] = index
    return result


def _normalize_header(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _cell(cells: tuple[object, ...], index: int | None) -> object | None:
    return None if index is None or index >= len(cells) else cells[index]


def _decimal(value: object) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)
