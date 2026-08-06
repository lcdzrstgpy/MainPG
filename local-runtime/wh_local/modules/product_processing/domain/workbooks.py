from __future__ import annotations

import csv
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook


HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("标题", "商品标题", "商品名称", "产品标题", "title", "name"),
    "skc": ("SKC", "skc", "商品ID", "商品编号"),
    "sku": ("SKU", "sku", "产品货号", "货号"),
    "category": ("类目", "分类", "category"),
    "image_url": ("缩略图链接", "主图 URL", "主图URL", "主图", "图片", "image_url"),
    "source_url": ("链接", "来源", "商品链接", "source_url", "product_link"),
    "price": ("价格", "售价", "最低价格", "建议售价", "price", "price_cny"),
    "description": ("描述", "商品描述", "description"),
}


def read_product_workbook(filename: str, content: bytes) -> list[dict[str, Any]]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        return _read_csv(content)
    if suffix not in {".xlsx", ".xlsm"}:
        raise ValueError("only .xlsx, .xlsm or .csv product files are supported")
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    sheet = workbook.active
    iterator = sheet.iter_rows(values_only=True)
    try:
        headers = [_clean_header(value) for value in next(iterator)]
    except StopIteration as exc:
        raise ValueError("uploaded product workbook is empty") from exc
    rows: list[dict[str, Any]] = []
    for row_number, values in enumerate(iterator, start=2):
        raw = {headers[index]: value for index, value in enumerate(values) if index < len(headers) and headers[index]}
        if not any(value not in (None, "") for value in raw.values()):
            continue
        rows.append(_normalize_row(raw, row_number))
    workbook.close()
    return rows


# 店小秘导入模板列（与原型程序 native_product_engine.DXM_COLUMNS 一致）
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
]

DXM_SKU_CLASSIFICATION_COLUMNS = [
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


def create_result_workbook(rows: list[dict[str, Any]], destination: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "店小秘导入"
    sheet.append(DXM_COLUMNS + ["*产品分类", "产品分类", "类目路径", "类目ID"] + DXM_SKU_CLASSIFICATION_COLUMNS)
    for row in rows:
        sheet.append(_dxm_export_row(row))
    sheet.freeze_panes = "A2"
    for index, width in enumerate((36, 36, 60, 18, 14, 16, 14, 16, 45, 14, 18, 12, 12, 12, 14, 12, 16, 45, 60, 60, 14, 14, 45, 14, 10, 12), start=1):
        sheet.column_dimensions[_column_letter(index)].width = width
    destination.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(destination)


def _dxm_export_row(row: dict[str, Any]) -> list[Any]:
    optimized_title = str(row.get("optimized_title") or "").strip()
    description = str(row.get("description") or "").strip()
    skc = str(row.get("skc") or "").strip()
    sku = str(row.get("sku") or skc).strip()
    main_image_url = str(row.get("image_url") or "").strip()
    source_url = str(row.get("source_url") or "").strip()
    source_image_urls = row.get("source_image_urls") or []
    source_detail_image_urls = row.get("source_detail_image_urls") or []
    source_attributes = row.get("source_attributes") or []
    source_variant_records = row.get("source_variant_records") or []
    declared_price = row.get("declared_price")
    cost = row.get("cost")
    category = str(row.get("category") or "").strip()

    # 变种属性：取第一条变种记录的一/二级属性
    variant_name_1, variant_value_1, variant_name_2, variant_value_2 = "", "", "", ""
    attribute_names = list(dict.fromkeys(str(attr.get("name") or "") for attr in source_attributes if isinstance(attr, dict)))
    if attribute_names:
        variant_name_1 = attribute_names[0]
        if len(attribute_names) > 1:
            variant_name_2 = attribute_names[1]
    first_variant = source_variant_records[0] if source_variant_records else {}
    if isinstance(first_variant, dict):
        attributes = first_variant.get("attributes") or {}
        values = [str(value) for value in attributes.values() if str(value)]
        if values:
            variant_value_1 = values[0]
        if len(values) > 1:
            variant_value_2 = values[1]
    if not variant_name_1:
        variant_name_1 = "规格"

    carousel = "\n".join(str(url) for url in source_image_urls if str(url))
    material_images = "\n".join(str(url) for url in source_detail_image_urls if str(url))
    if not material_images and main_image_url:
        material_images = main_image_url

    return [
        optimized_title,
        optimized_title,
        description,
        skc,
        variant_name_1,
        variant_value_1,
        variant_name_2,
        variant_value_2,
        main_image_url,
        declared_price if declared_price not in (None, "") else "",
        sku,
        "",  # *长（cm）
        "",  # *宽（cm）
        "",  # *高（cm）
        "",  # *重量（g）
        "",  # 识别码类型
        "",  # 识别码
        source_url,
        carousel,
        material_images,
        "",  # 外包装形状
        "Bubble bag",  # 外包装类型
        "",  # 外包装图片
        cost if cost not in (None, "") else "",  # 建议售价（USD），暂以成本兜底
        "",  # 库存
        "",  # 发货时效（天）
        category,  # *产品分类
        category,  # 产品分类
        category,  # 类目路径
        "",  # 类目ID
        "单品",  # SKU分类
        1,  # SKU分类数量
        "件",  # SKU分类单位
        "", "", "", "", "", "", "", "", "",  # 其余 SKU 分类字段占位
    ]


def _column_letter(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def create_error_report(rows: list[dict[str, Any]], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["item_id", "draft_id", "title", "status", "reason"])
        for row in rows:
            writer.writerow(
                [row.get("item_id"), row.get("product_draft_id"), row.get("title"), row.get("status"), row.get("reason")]
            )


def create_video_manifest(rows: list[dict[str, Any]], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["skc", "title", "source_url", "video_status"])
        for row in rows:
            writer.writerow([row.get("skc"), row.get("optimized_title"), row.get("source_url"), "pending"])


def _read_csv(content: bytes) -> list[dict[str, Any]]:
    decoded = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(StringIO(decoded))
    return [_normalize_row(dict(row), index) for index, row in enumerate(reader, start=2) if any(row.values())]


def _normalize_row(raw: dict[str, Any], row_number: int) -> dict[str, Any]:
    normalized: dict[str, Any] = {"raw_row": raw, "source_row": row_number}
    for target, aliases in HEADER_ALIASES.items():
        normalized[target] = next(
            (raw[alias] for alias in aliases if alias in raw and raw[alias] not in (None, "")), ""
        )
    title = str(normalized["title"] or "").strip()
    if not title:
        normalized["import_warning"] = "missing_title"
    normalized["title"] = title
    normalized["product_name"] = title
    normalized["source_ref"] = str(normalized.get("source_url") or f"workbook-row:{row_number}")
    return normalized


def _clean_header(value: Any) -> str:
    return str(value or "").replace("\n", " ").strip()
