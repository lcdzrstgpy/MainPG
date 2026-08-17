from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Any, Callable


_HEADER_ALIASES = {
    "skc": {"skc", "skc id", "商品id", "商品 id", "商品编号", "产品id", "产品 id"},
    "selling_price": {"售价", "销售价", "售卖价", "selling price", "price"},
    "cost_price": {"成本", "成本价", "采购成本", "cost", "cost price"},
    "weight_kg": {"重量", "重量kg", "重量 kg", "weight", "weight kg"},
    "note": {"备注", "说明", "note"},
    "source_url": {"货源", "货源链接", "采购链接", "source", "source url"},
    "product_image": {"商品主图", "主图", "产品图片", "product image", "main image"},
    "source_image": {"货源图", "采购截图", "source image"},
    "activity_price": {"活动申报价", "活动报价", "最低活动价", "activity price", "campaign price"},
    "activity_name": {"活动类型(活动主题)", "活动类型", "活动主题", "activity", "activity name"},
    "spu": {"spu", "spu id"},
    "site": {"站点", "site", "site code"},
}


def parse_product_workbook(workbook_bytes: bytes, site: str, duplicate_keys: set[tuple[str, str]]) -> list[dict[str, Any]]:
    workbook = _load_workbook(workbook_bytes)
    try:
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
                    "has_product_image": False, "has_source_image": False,
                    "product_image_path": "", "source_image_path": "", "is_duplicate": is_duplicate,
                })
        return rows
    finally:
        workbook.close()


def extract_product_workbook_images(workbook_bytes: bytes) -> dict[str, dict[str, list[tuple[str, bytes]]]]:
    """Read images anchored to product rows from an uploaded Excel workbook.

    The workbook format used by employees is not rigid.  An image under a
    recognised main/source-image column is classified accordingly; otherwise
    the first image in a row is treated as the main image and following images
    as source images.  Broken image records are ignored rather than blocking
    the remaining rows.
    """
    workbook = _load_workbook(workbook_bytes)
    try:
        result: dict[str, dict[str, list[tuple[str, bytes]]]] = {}
        for worksheet in workbook.worksheets:
            headers = _headers(worksheet)
            product_col = headers.get("product_image")
            source_col = headers.get("source_image")
            for image_index, image in enumerate(getattr(worksheet, "_images", []), start=1):
                anchor = getattr(image, "anchor", None)
                origin = getattr(anchor, "_from", None)
                if origin is None:
                    continue
                row_number = int(origin.row) + 1
                column = int(origin.col)
                if row_number < 2:
                    continue
                try:
                    content = image._data()
                except Exception:
                    continue
                if not content:
                    continue
                row_id = f"{worksheet.title}:{row_number}"
                group = result.setdefault(row_id, {"product": [], "source": []})
                if product_col is not None and column == product_col:
                    kind = "product"
                elif source_col is not None and column == source_col:
                    kind = "source"
                else:
                    kind = "product" if not group["product"] else "source"
                extension = getattr(image, "format", "png") or "png"
                group[kind].append((f"excel_{row_number}_{image_index}.{str(extension).lower()}", content))
        return result
    finally:
        workbook.close()


def filter_activity_workbook(
    workbook_bytes: bytes,
    *,
    site: str,
    evaluate: Callable[[str, Decimal], dict[str, Any]],
) -> dict[str, Any]:
    """Filter an activity workbook while retaining its sheet layout and data.

    ``evaluate`` receives ``(skc, lowest_activity_price)`` and returns a
    stable decision payload containing at least ``keep``, ``reason_code``,
    ``net_profit`` and ``profit_rate``.  The function is domain-only: it does
    not write files or know about HTTP/database concerns.
    """
    workbook = _load_workbook(workbook_bytes)
    try:
        worksheet = _activity_price_sheet(workbook)
        headers = _headers(worksheet)
        skc_index = headers.get("skc")
        if skc_index is None:
            raise ValueError("activity workbook is missing an SKC column")
        price_index = headers.get("activity_price")
        activity_index = headers.get("activity_name")
        site_index = headers.get("site")
        spu_index = headers.get("spu")
        groups: dict[tuple[str, str], list[tuple[int, Decimal | None]]] = {}
        spu_by_row: dict[int, str] = {}
        removed_rows: list[dict[str, Any]] = []
        site_mismatch_rows: set[int] = set()

        for row_number, cells in enumerate(worksheet.iter_rows(min_row=2, values_only=True), start=2):
            skc = str(_cell(cells, skc_index) or "").strip()
            if not skc:
                continue
            row_site = str(_cell(cells, site_index) or "").strip().upper() if site_index is not None else ""
            if row_site and row_site != site:
                site_mismatch_rows.add(row_number)
                removed_rows.append(_removed_row(worksheet, row_number, "site_mismatch", None))
                continue
            activity_name = str(_cell(cells, activity_index) or "").strip() if activity_index is not None else ""
            price = _decimal(_cell(cells, price_index)) if price_index is not None else None
            groups.setdefault((skc, activity_name), []).append((row_number, price))
            if spu_index is not None:
                spu_by_row[row_number] = str(_cell(cells, spu_index) or "").strip()

        decisions: list[dict[str, Any]] = []
        kept_rows: set[int] = set()
        removed_row_numbers: set[int] = set(site_mismatch_rows)
        qualification_counts: Counter[str] = Counter()
        kept_skcs: set[str] = set()
        removed_skcs: set[str] = set()
        kept_spus: set[str] = set()
        kept_activity_spus: set[tuple[str, str]] = set()
        for (skc, activity_name), entries in groups.items():
            prices = [price for _, price in entries if price is not None and price > 0]
            if not prices:
                decision: dict[str, Any] = {"keep": False, "reason_code": "invalid_activity_price", "net_profit": None, "profit_rate": None}
            else:
                decision = evaluate(skc, min(prices))
            decision = {**decision, "skc": skc, "activity_name": activity_name, "min_price": float(min(prices)) if prices else None}
            decisions.append(decision)
            reason = str(decision.get("reason_code") or "unknown")
            qualification_counts[reason] += 1
            row_numbers = {number for number, _ in entries}
            if decision.get("keep"):
                kept_rows.update(row_numbers)
                kept_skcs.add(skc)
                for row_number in row_numbers:
                    spu = spu_by_row.get(row_number, "")
                    if spu:
                        kept_spus.add(spu)
                        kept_activity_spus.add((activity_name, spu))
            else:
                removed_row_numbers.update(row_numbers)
                removed_skcs.add(skc)
                for row_number, _ in entries:
                    removed_rows.append(_removed_row(worksheet, row_number, reason, decision))

        _delete_rows(worksheet, removed_row_numbers)
        # Activity templates commonly contain a second inventory sheet.  Once
        # a price-row is removed, remove its orphan inventory rows too so the
        # generated workbook remains internally consistent.
        for candidate in workbook.worksheets:
            if candidate is worksheet:
                continue
            candidate_headers = _headers(candidate)
            candidate_spu_index = candidate_headers.get("spu")
            if candidate_spu_index is None:
                continue
            candidate_activity_index = candidate_headers.get("activity_name")
            candidate_remove: set[int] = set()
            for row_number, cells in enumerate(candidate.iter_rows(min_row=2, values_only=True), start=2):
                spu = str(_cell(cells, candidate_spu_index) or "").strip()
                if not spu:
                    continue
                activity_name = str(_cell(cells, candidate_activity_index) or "").strip() if candidate_activity_index is not None else ""
                keep = (activity_name, spu) in kept_activity_spus if candidate_activity_index is not None else spu in kept_spus
                if not keep:
                    candidate_remove.add(row_number)
            _delete_rows(candidate, candidate_remove)
        filtered_bytes = workbook_bytes_from(workbook)
        removed_bytes = _removed_workbook_bytes(removed_rows)
        return {
            "filtered_bytes": filtered_bytes,
            "removed_bytes": removed_bytes,
            "kept_skc_count": len(kept_skcs), "removed_skc_count": len(removed_skcs),
            "kept_row_count": len(kept_rows), "removed_row_count": len(removed_rows),
            "kept_activity_count": sum(1 for item in decisions if item.get("keep")),
            "removed_activity_count": sum(1 for item in decisions if not item.get("keep")),
            "qualification_counts": dict(qualification_counts), "removed_rows": removed_rows,
            "activity_decisions": decisions,
            "template_site_summary": {
                "total_price_rows": sum(len(entries) for entries in groups.values()) + len(site_mismatch_rows),
                "site_counts": {site: sum(len(entries) for entries in groups.values())},
                "unique_skc_count_by_site": {site: len({key[0] for key in groups})},
            },
        }
    finally:
        workbook.close()


def new_workbook():
    try:
        from openpyxl import Workbook
    except ImportError as exc:
        raise ValueError("openpyxl is required for Excel support; install openpyxl") from exc
    return Workbook()


def workbook_bytes(workbook) -> bytes:
    return workbook_bytes_from(workbook)


def workbook_bytes_from(workbook) -> bytes:
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


def _activity_price_sheet(workbook):
    candidates = []
    for worksheet in workbook.worksheets:
        headers = _headers(worksheet)
        if "skc" in headers:
            candidates.append(("activity_price" in headers, worksheet))
    if not candidates:
        raise ValueError("activity workbook is missing a worksheet with an SKC column")
    return next((sheet for has_price, sheet in candidates if has_price), candidates[0][1])


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
        result = Decimal(str(value).strip().replace("¥", "").replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _delete_rows(worksheet, row_numbers: set[int]) -> None:
    for row_number in sorted((number for number in row_numbers if number > 1), reverse=True):
        worksheet.delete_rows(row_number, 1)


def _removed_row(worksheet, row_number: int, reason_code: str, decision: dict[str, Any] | None) -> dict[str, Any]:
    values = [cell.value for cell in worksheet[row_number]]
    return {
        "worksheet": worksheet.title, "row_number": row_number,
        "values": values, "reason_code": reason_code,
        "net_profit": decision.get("net_profit") if decision else None,
        "profit_rate": decision.get("profit_rate") if decision else None,
    }


def _removed_workbook_bytes(rows: list[dict[str, Any]]) -> bytes:
    workbook = new_workbook()
    worksheet = workbook.active
    worksheet.title = "removed_rows"
    worksheet.append(["worksheet", "row_number", "reason_code", "net_profit", "profit_rate", "original_values"])
    for row in rows:
        worksheet.append([
            row["worksheet"], row["row_number"], row["reason_code"], row["net_profit"], row["profit_rate"],
            " | ".join("" if value is None else str(value) for value in row["values"]),
        ])
    return workbook_bytes_from(workbook)
