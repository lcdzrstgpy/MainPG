"""解析 Temu 结算中心导出的账单文件（PO 聚合账单 / PO 明细账单）。

结算中心一次只能导出一个月，用户按月导出多个文件后可以一起导入；
这里只做「文件 → 标准行」的转换，写库和回填仍复用 import_settlement。
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any

MONEY_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
PO_RE = re.compile(r"PO-[A-Za-z0-9-]+", re.I)
DECLARED_RE = re.compile(r"申报价格?\s*[:：]?\s*[¥￥]?\s*([\d,]+(?:\.\d+)?)")
QUANTITY_RE = re.compile(r"[×xX]\s*(\d+)")
SKU_RE = re.compile(r"SKU\s*ID\s*[:：]?\s*(\d+)", re.I)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\u00a0", " ").strip()


def _money(value: Any) -> float | None:
    """兼容 311.50 / ¥311.50 / -¥311.50 / (311.50) / 数字单元格。"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = _text(value).replace(",", "").replace("¥", "").replace("￥", "")
    negative = text.startswith("-") or (text.startswith("(") and text.endswith(")"))
    match = MONEY_RE.search(text.strip("-() "))
    if not match:
        return None
    amount = float(match.group(0).replace(",", ""))
    return -amount if negative else amount


def _rows_from_csv(content: bytes) -> list[list[Any]]:
    text = ""
    for encoding in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            text = content.decode(encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if not text:
        raise ValueError("无法识别文件编码，请用 Excel 另存为 CSV（UTF-8）后重试")
    sample = text[:4096]
    delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
    return [row for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def _xlsx_sheets(content: bytes) -> dict[str, list[list[Any]]]:
    try:
        from openpyxl import load_workbook
    except ImportError as error:  # pragma: no cover - 打包环境已带 openpyxl
        raise ValueError("当前环境缺少 Excel 解析组件，请改用 CSV 文件") from error
    workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    return {
        sheet.title: [list(row) for row in sheet.iter_rows(values_only=True)]
        for sheet in workbook.worksheets
    }


def _rows_from_xlsx(content: bytes) -> list[list[Any]]:
    sheets = _xlsx_sheets(content)
    for rows in sheets.values():
        if rows:
            return rows
    return []


def _read_rows(content: bytes, filename: str) -> list[list[Any]]:
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        return _rows_from_xlsx(content)
    if name.endswith(".xls"):
        raise ValueError("不支持旧版 .xls，请在 Excel 里另存为 .xlsx 或 CSV 后再导入")
    return _rows_from_csv(content)


def _find_header(rows: list[list[Any]]) -> int:
    for index, row in enumerate(rows[:40]):
        joined = " ".join(_text(cell) for cell in row)
        if re.search(r"PO", joined, re.I) and "销售回款" in joined:
            return index
    return -1


def _column(header: list[str], *keywords: str) -> int:
    for keyword in keywords:
        for index, value in enumerate(header):
            if keyword in value:
                return index
    return -1


def _cell(row: list[Any], index: int) -> Any:
    if index < 0 or index >= len(row):
        return None
    return row[index]


def _pick(header: list[str], exact: tuple[str, ...] = (), contains: tuple[str, ...] = ()) -> int:
    """先按完整表头名匹配，再退化成包含匹配；两行表头会拼成「分组/子列」。"""
    for name in exact:
        for index, value in enumerate(header):
            if value == name:
                return index
    for keyword in contains:
        for index, value in enumerate(header):
            if keyword in value:
                return index
    return -1


def _merge_header(top: list[Any], bottom: list[Any]) -> list[str]:
    """合并两行表头：上行合并单元格只有首列有值，需要向右填充再拼子列名。"""
    size = max(len(top), len(bottom))
    names: list[str] = []
    carry = ""
    for index in range(size):
        upper = _text(_cell(top, index))
        lower = _text(_cell(bottom, index))
        if upper:
            carry = upper
            base = upper
        else:
            base = carry
        if lower and lower != base:
            names.append(f"{base}/{lower}" if base else lower)
        else:
            names.append(base)
    return names


def _is_sub_header(row: list[Any]) -> bool:
    joined = " ".join(_text(cell) for cell in row)
    if PO_RE.search(joined):
        return False
    return any(keyword in joined for keyword in ("SKU", "件数", "申报价格"))


def _find_row_with(rows: list[list[Any]], *keywords: str, limit: int = 40) -> int:
    for index, row in enumerate(rows[:limit]):
        joined = " ".join(_text(cell) for cell in row)
        if all(keyword in joined for keyword in keywords):
            return index
    return -1


def _is_fund_detail(sheets: dict[str, list[list[Any]]]) -> bool:
    """财务明细导出（导出列表 + 账务详情）的特征：结算 sheet + PO单号/交易类型/结算金额。"""
    for rows in sheets.values():
        if rows and _find_row_with(rows, "PO单号", "结算金额") >= 0:
            return True
    return False


def _parse(
    header: list[str],
    rows: list[list[Any]],
    start: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    index = {
        "flow_id": _pick(header, ("结算流水ID",), ("结算流水ID",)),
        "batch": _pick(header, ("批次号",), ("批次号",)),
        "po": _pick(header, ("PO单号",), ("PO单号",)),
        "doc": _pick(header, ("单据号",), ("单据号",)),
        "trade": _pick(header, ("交易类型",), ("交易类型",)),
        "amount": _pick(header, ("结算金额",), ("结算金额",)),
        "currency": _pick(header, ("币种",), ("币种",)),
        "sku": _pick(header, ("商品信息/SKU ID",), ("SKU ID", "SKU")),
        "sku_name": _pick(header, ("商品信息/SKU名称",), ("SKU名称",)),
        "quantity": _pick(header, ("商品信息/件数",), ("件数", "数量")),
        "declared": _pick(header, ("商品信息/申报价格",), ("/申报价格",)),
        "arrive": _pick(header, ("到账时间",), ("到账时间",)),
        "site": _pick(header, ("站点", "国家"), ("站点", "国家")),
    }
    if index["po"] < 0 or index["amount"] < 0:
        return [], index

    records: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows[start:]:
        if not any(_text(cell) for cell in row):
            continue
        po = _text(_cell(row, index["po"])).upper()
        if not po:
            continue
        record = records.get(po)
        if record is None:
            record = records[po] = {
                "parent_order_sn": po,
                "currency": _text(_cell(row, index["currency"])) or "CNY",
                "settlement_amount": 0.0,
                "sales_payback": 0.0,
                "freight_payback": 0.0,
                "sales_reversal": 0.0,
                "freight_reversal": 0.0,
                "declared_price": None,
                "quantity": 0,
                "goods_name": "",
                "sku_id": "",
                "batch_no": "",
                "flow_id": "",
                "doc_no": "",
                "arrive_time": "",
                "site": _text(_cell(row, index["site"])),
                "trade_types": [],
            }
            order.append(po)
        amount = _money(_cell(row, index["amount"])) or 0.0
        trade = _text(_cell(row, index["trade"]))
        record["settlement_amount"] += amount
        if trade:
            if trade not in record["trade_types"]:
                record["trade_types"].append(trade)
        if "冲回" in trade:
            if "运费" in trade:
                record["freight_reversal"] += amount
            else:
                record["sales_reversal"] += amount
        elif "运费" in trade:
            record["freight_payback"] += amount
        elif "销售" in trade:
            record["sales_payback"] += amount
        quantity = _money(_cell(row, index["quantity"]))
        if quantity:
            record["quantity"] += int(quantity)
        declared = _money(_cell(row, index["declared"]))
        if declared is not None:
            record["declared_price"] = declared
        name = _text(_cell(row, index["sku_name"]))
        if name and not record["goods_name"]:
            record["goods_name"] = name[:200]
        sku = _text(_cell(row, index["sku"]))
        if sku and not record["sku_id"]:
            record["sku_id"] = sku
        batch = _text(_cell(row, index["batch"]))
        if batch and not record["batch_no"]:
            record["batch_no"] = batch
        flow = _text(_cell(row, index["flow_id"]))
        if flow and not record["flow_id"]:
            record["flow_id"] = flow
        doc = _text(_cell(row, index["doc"]))
        if doc and not record["doc_no"]:
            record["doc_no"] = doc
        arrive = _text(_cell(row, index["arrive"]))
        if arrive > record["arrive_time"]:
            record["arrive_time"] = arrive

    parsed = [records[po] for po in order]
    for record in parsed:
        for key in ("settlement_amount", "sales_payback", "freight_payback",
                    "sales_reversal", "freight_reversal"):
            record[key] = round(record[key], 2)
    return parsed, index


def _parse_penalty_sheets(sheets: dict[str, list[list[Any]]]) -> dict[str, float]:
    """「支出-履约违规」sheet：违规编号 / 订单编号 / 违规类型 / 支出金额 / 币种 / 账务时间。"""
    penalties: dict[str, float] = {}
    for title, rows in sheets.items():
        if "结算" in title or not rows:
            continue
        header_index = _find_row_with(rows, "订单编号", "支出金额")
        if header_index < 0:
            continue
        header = [_text(cell) for cell in rows[header_index]]
        order_col = _pick(header, ("订单编号",), ("订单编号", "订单号"))
        amount_col = _pick(header, ("支出金额",), ("支出金额", "扣款金额"))
        if order_col < 0 or amount_col < 0:
            continue
        for row in rows[header_index + 1:]:
            sn = _text(_cell(row, order_col)).upper()
            amount = _money(_cell(row, amount_col))
            if not sn or amount is None:
                continue
            penalties[sn] = round(penalties.get(sn, 0.0) + amount, 2)
    return penalties


def _parse_fund_detail(sheets: dict[str, list[list[Any]]]) -> tuple[list[dict[str, Any]], list[str]]:
    for title, rows in sheets.items():
        if "结算" not in title:
            continue
        header_index = _find_row_with(rows, "PO单号", "结算金额")
        if header_index < 0:
            continue
        bottom = rows[header_index + 1] if header_index + 1 < len(rows) else []
        has_sub = bool(bottom) and _is_sub_header(bottom)
        header = _merge_header(rows[header_index], bottom) if has_sub else [
            _text(cell) for cell in rows[header_index]
        ]
        parsed, _ = _parse(header, rows, header_index + (2 if has_sub else 1))
        if not parsed:
            continue
        penalties = _parse_penalty_sheets(sheets)
        for record in parsed:
            record["penalty_amount"] = penalties.get(record["parent_order_sn"], 0.0)
            penalties.pop(record["parent_order_sn"], None)
        # 违规单里没对上 PO 的（例如子单号）单独留一行，保证罚款合计不缺。
        for sn, amount in penalties.items():
            parsed.append({
                "parent_order_sn": sn,
                "currency": "CNY",
                "settlement_amount": 0.0,
                "sales_payback": 0.0,
                "freight_payback": 0.0,
                "sales_reversal": 0.0,
                "freight_reversal": 0.0,
                "declared_price": None,
                "quantity": 0,
                "goods_name": "",
                "sku_id": "",
                "batch_no": "",
                "flow_id": "",
                "doc_no": "",
                "arrive_time": "",
                "site": "",
                "trade_types": [],
                "penalty_amount": amount,
            })
        return parsed, header
    raise ValueError("没有找到包含「PO单号 / 结算金额」的结算表，请确认导出的是「导出列表 + 账务详情」")


def parse_settlement_file(content: bytes, filename: str = "") -> tuple[list[dict[str, Any]], list[str]]:
    """返回（标准结算行，表头）。解析失败时抛 ValueError，文案直接给用户看。"""
    if not content:
        raise ValueError("文件是空的")
    if len(content) > 20 * 1024 * 1024:
        raise ValueError("文件超过 20MB，请拆分成多个文件再导入")

    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        sheets = _xlsx_sheets(content)
        if _is_fund_detail(sheets):
            return _parse_fund_detail(sheets)
        rows: list[list[Any]] = []
        for sheet_rows in sheets.values():
            if sheet_rows:
                rows = sheet_rows
                break
    else:
        rows = _read_rows(content, filename)
    return _parse_legacy_po_file(rows)


def _parse_legacy_po_file(rows: list[list[Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """老的按 PO 聚合账单（含「PO单号 / 销售回款」表头）。"""
    header_index = _find_header(rows)
    if header_index < 0:
        raise ValueError("没有找到包含「PO 单号 / 销售回款」的表头，请确认是结算中心导出的文件")
    header = [_text(cell) for cell in rows[header_index]]

    index = {
        "po": _column(header, "PO单号", "PO 单号", "PO"),
        "currency": _column(header, "币种"),
        "sales": _column(header, "销售回款"),
        "sales_reversal": _column(header, "销售冲回"),
        "freight": _column(header, "运费回款"),
        "freight_reversal": _column(header, "运费冲回"),
        "declared": _column(header, "申报价"),
        "quantity": _column(header, "件数", "数量"),
        "sku": _column(header, "SKU"),
        "goods": _column(header, "商品信息", "商品"),
        "site": _column(header, "站点", "国家"),
    }
    if index["po"] < 0:
        for position, value in enumerate(header):
            if re.search(r"订单|PO", value, re.I):
                index["po"] = position
                break

    parsed: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for row in rows[header_index + 1:]:
        if not any(_text(cell) for cell in row):
            continue
        po_text = _text(_cell(row, index["po"]))
        if not po_text:
            po_text = " ".join(_text(cell) for cell in row)
        po_match = PO_RE.search(po_text)
        if not po_match:
            continue
        po = po_match.group(0).upper()
        goods_text = _text(_cell(row, index["goods"]))
        declared = _money(_cell(row, index["declared"]))
        if declared is None:
            match = DECLARED_RE.search(goods_text)
            declared = _money(match.group(1)) if match else None
        quantity = 0
        raw_quantity = _cell(row, index["quantity"])
        if raw_quantity not in (None, ""):
            try:
                quantity = int(float(str(raw_quantity).replace(",", "")))
            except (TypeError, ValueError):
                quantity = 0
        if not quantity:
            match = QUANTITY_RE.search(goods_text)
            quantity = int(match.group(1)) if match else 0
        sku = _text(_cell(row, index["sku"]))
        if not sku:
            match = SKU_RE.search(goods_text)
            sku = match.group(1) if match else ""
        goods_name = goods_text
        for marker in ("SKU ID", "SKU", "×", "申报价格"):
            position = goods_name.find(marker)
            if position > 0:
                goods_name = goods_name[:position]
        record = {
            "parent_order_sn": po,
            "currency": _text(_cell(row, index["currency"])) or "CNY",
            "sales_payback": _money(_cell(row, index["sales"])),
            "sales_reversal": _money(_cell(row, index["sales_reversal"])),
            "freight_payback": _money(_cell(row, index["freight"])),
            "freight_reversal": _money(_cell(row, index["freight_reversal"])),
            "declared_price": declared,
            "quantity": quantity,
            "goods_name": goods_name.strip()[:200],
            "sku_id": sku,
            "site": _text(_cell(row, index["site"])),
        }
        # 同一 PO 可能出现在多行（多 SKU）：以最后一行为准，避免重复写库。
        if po in seen:
            parsed[seen[po]] = record
        else:
            seen[po] = len(parsed)
            parsed.append(record)
    if not parsed:
        raise ValueError("文件里没有解析到 PO 单号，请确认导出的是「PO 聚合账单」")
    return parsed, header
