from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any, Sequence

from openpyxl import Workbook


DXM_COLUMNS = [
    "*产品标题",
    "*英文标题",
    "产品描述",
    "产品货号",
    "*变种属性名称一",
    "*变种属性值一",
    "变种属性名称二",
    "变种属性值二",
    "预览图",
    "*申报价格\n(店铺币种)",
    "SKU货号",
    "*长（cm）",
    "*宽（cm）",
    "*高（cm）",
    "*重量（g）",
    "识别码类型",
    "识别码",
    "站外产品链接",
    "*轮播图",
    "*产品素材图",
    "外包装形状",
    "外包装类型",
    "外包装图片",
    "建议售价（USD）",
    "库存",
    "发货时效（天）",
    "*产品分类",
    "产品分类",
    "类目路径",
    "类目ID",
    "SKU分类",
    "SKU分类数量",
    "SKU分类单位",
    "独立包装",
    "净含量数值",
    "净含量单位",
    "混合套装类型",
    "SKU分类总数量",
    "SKU分类总数量单位",
    "总净含量",
    "总净含量单位",
    "包装清单",
]

DXM_COLUMN_WIDTHS = (
    36, 36, 60, 18, 14, 16, 14, 16, 45, 14, 18, 12, 12, 12, 14,
    12, 16, 45, 60, 60, 14, 14, 45, 14, 10, 12,
)


def build_dianxiaomi_workbook(rows: Sequence[Sequence[Any]]) -> bytes:
    """Build a Dianxiaomi import workbook without adding sample data rows."""
    workbook = Workbook()
    try:
        sheet = workbook.active
        sheet.title = "店小秘导入"
        sheet.append(DXM_COLUMNS)
        _force_appended_strings_to_literal_text(sheet)
        for row_number, row in enumerate(rows, start=2):
            if len(row) != len(DXM_COLUMNS):
                raise ValueError(
                    f"Dianxiaomi row {row_number} must contain exactly {len(DXM_COLUMNS)} cells"
                )
            sheet.append(row)
            _force_appended_strings_to_literal_text(sheet)
        sheet.freeze_panes = "A2"
        for index, width in enumerate(DXM_COLUMN_WIDTHS, start=1):
            sheet.column_dimensions[_column_letter(index)].width = width
        content = BytesIO()
        workbook.save(content)
        return content.getvalue()
    finally:
        workbook.close()


def save_dianxiaomi_workbook(rows: Sequence[Sequence[Any]], destination: Path) -> None:
    """Write a Dianxiaomi import workbook to ``destination``."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(build_dianxiaomi_workbook(rows))


def _column_letter(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _force_appended_strings_to_literal_text(sheet: Any) -> None:
    for cell in sheet[sheet.max_row]:
        if isinstance(cell.value, str):
            cell.data_type = "s"
