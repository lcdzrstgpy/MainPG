from __future__ import annotations

import csv
import math
import os
import re
import threading
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook

from .image_slots import apply_slot_overrides
from .policy import is_safe_external_url


# 店小秘导入默认值（对齐原型 native_product_engine 常量）
DEFAULT_SHIP_DAYS = 2
DECLARED_PRICE_MULTIPLIER = 4
DECLARED_PRICE_MIN_CNY = 150.0
DXM_STOCK_MIN = 0
DXM_STOCK_MAX = 999999
# 外包装形状/类型（对齐原型 _build_dxm_row：soft → 软包装软物/气泡袋，rigid → 硬包装硬物/纸箱）
_PACKAGE_EXPORT_BY_PROFILE = {
    "rigid_container": ("硬包装硬物", "纸箱"),
}
_PACKAGE_EXPORT_DEFAULT = ("软包装软物", "气泡袋")

# 变种规格轴名称本地映射（对齐原型 §8.2：映射为 Color/Size/Pack/Style/Capacity 等店小秘规格轴）
_VARIANT_AXIS_NAMES = {
    "规格": "Style",
    "规格分类": "Style",
    "规格型号": "Model",
    "款式": "Style",
    "样式": "Style",
    "颜色": "Color",
    "颜色分类": "Color",
    "颜色名称": "Color",
    "尺寸": "Size",
    "尺码": "Size",
    "型号": "Model",
    "材质": "Material",
    "材料": "Material",
    "图案": "Pattern",
    "套装": "Pack",
    "数量": "Quantity",
    "容量": "Capacity",
    "包装": "Packaging",
    "高度": "Height",
    "长度": "Length",
    "宽度": "Width",
    "形状": "Shape",
}

# ===== 变种属性清洗/校验层（导出店小秘前） =====
# Temu 采集会把页面级元数据（品牌/评分/运费/支付方式/导航/整段商品描述）误当成变种属性
# 名或值。此清洗层在生成店小秘模板行前剔除这些噪音，避免导入后出现「奇奇怪怪」的变种行。
# 仅剔除明显噪音；未识别的一律保留（宁缺勿滥）。

# 噪音属性名：绝不作为店小秘变种规格轴（平台系统字段、导购元数据、纯数字 ID）。
_NOISE_NAME_RE = re.compile(
    r"^(?:(?-i:is[A-Z])\w*|brand|sold.?by|afterpay|klarna|import|arrows|from|pre.?discount|"
    r"品[类牌]|链接|平台|来源|图片|推荐|评分|评价|已售|库存|客服|运费|免运费)$",
    re.IGNORECASE,
)
_NOISE_NUMERIC_NAME_RE = re.compile(r"^\d+$")

# 噪音属性值：命中即整对剔除（这些是页面元数据，不是真实变种选项）。
_NOISE_VALUE_RE = re.compile(
    r"no\s+import\s+fees?|"  # No import fees
    r"\bbrand\b\s*[:：]?|"  # Brand: JODIMACH / Brand
    r"out\s+of\s+\d+(?:\.\d+)?\s*stars?|"  # 4.8 out of 5 stars
    r"\d+(?:\.\d+)?\s+stars?\s+out\s+of\s*\d+|\d+(?:\.\d+)?\s*star\s+rating|"  # 5.0 stars out of 5 / 5.0 Star Rating
    r"afterpay|klarna|"  # Afterpay 211 / Klarna
    r"arrows|"  # common arrows / common_arrows
    r"sold\s+by\b|"  # Sold by
    r"^from$|"  # From（元数据）
    r"pre[- ]?discount|"  # Pre-Discount Price
    r"no\s+additional\s+variants",  # No Additional Variants
    re.IGNORECASE,
)

# 纯重量值（如 100 grams / 50克）：若出现在非重量/容量轴上，属采集错位（如 Color → 100 grams）。
_WEIGHT_VALUE_RE = re.compile(r"^\d+(?:\.\d+)?\s*(?:g|grams?|克|kg|千克)$", re.IGNORECASE)
# 可承载纯重量值的轴（容量/数量/套装/重量）；其余视觉/型号/尺寸轴出现纯重量均视为噪音。
_WEIGHT_TOLERANT_AXES = {"Capacity", "Quantity", "Pack", "Packaging", "Weight"}

# 商品描述/优惠说明：以「数量+单位」开头明显是商品描述，或含多子句的营销/福利文本。
_START_COUNT_UNIT_RE = re.compile(
    r"^\s*\d+(?:\.\d+)?\s*(?:pc|pcs|piece|pieces|inch|in|cm|mm|只|支|个|件)\b", re.IGNORECASE
)


def _variant_axis_name(name_text: str) -> str:
    return _VARIANT_AXIS_NAMES.get(name_text, name_text)


def _is_variant_value_noise(name: str, value: str) -> bool:
    """判定某一个（规格轴名, 属性值）是否为采集噪音，命中即应剔除整对。"""
    if _NOISE_VALUE_RE.search(value):
        return True
    if _NOISE_NAME_RE.match(name) or _NOISE_NUMERIC_NAME_RE.match(name):
        return True
    axis = _variant_axis_name(name)
    if _WEIGHT_VALUE_RE.match(value) and axis not in _WEIGHT_TOLERANT_AXES:
        return True
    if _is_description_like(value):
        return True
    return False


def _is_description_like(value: str) -> bool:
    if len(value) > 90:
        return True
    if _START_COUNT_UNIT_RE.match(value) and len(value) > 40:
        return True
    # 含多个逗号且较长的文本，多为商品描述/福利说明（产品级属性列表、营销文案等）
    if len(value) > 40 and value.count(",") >= 2:
        return True
    return False


def clean_variant_attributes(attributes: Any) -> list[tuple[str, str]]:
    """把来源变种属性清洗为店小秘可用的 (规格轴名, 属性值) 列表（保持原顺序）。

    兼容 dict / list[dict] / list[tuple] 三种来源形态；剔除噪音名称/值，规格轴名本地化。
    """
    if isinstance(attributes, dict):
        items = list(attributes.items())
    elif isinstance(attributes, list):
        items = []
        for entry in attributes:
            if isinstance(entry, dict):
                items.append((entry.get("name"), entry.get("value")))
            elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
                items.append((entry[0], entry[1]))
    else:
        return []
    cleaned: list[tuple[str, str]] = []
    for name, value in items:
        name_text = str(name or "").strip()
        value_text = str(value or "").strip()
        if not name_text or not value_text:
            continue
        if _is_variant_value_noise(name_text, value_text):
            continue
        cleaned.append((_variant_axis_name(name_text), value_text))
    return cleaned


def is_variant_value_noise(name: Any, value: Any) -> bool:
    """公开判定：一个（规格轴名, 属性值）对是否为采集噪音。

    供导出前清洗（clean_variant_attributes）与翻译前收集（service 层）共同复用，
    确保「不提交 AI 翻译 + 不写入导出行」两处对噪音的判断保持一致。
    """
    name_text = str(name or "").strip()
    value_text = str(value or "").strip()
    if not name_text or not value_text:
        return False
    return _is_variant_value_noise(name_text, value_text)


HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("标题", "商品标题", "商品名称", "产品标题", "title", "name"),
    "skc": ("SKC", "skc", "商品ID", "商品编号"),
    "sku": ("SKU", "sku", "产品货号", "货号"),
    "category": ("类目", "分类", "category"),
    "image_url": ("缩略图链接", "主图 URL", "主图URL", "主图", "图片", "image_url"),
    "source_url": ("链接", "来源", "商品链接", "source_url", "product_link"),
    "price": ("价格", "售价", "最低价格", "建议售价", "price", "price_cny"),
    "description": ("描述", "商品描述", "description"),
    "weight_text": ("*重量（g）", "重量（g）", "*重量(g)", "重量(g)", "重量", "净重"),
    "length_cm": ("*长（cm）", "长（cm）", "*长(cm)", "长(cm)", "长度（cm）", "长"),
    "width_cm": ("*宽（cm）", "宽（cm）", "*宽(cm)", "宽(cm)", "宽度（cm）", "宽"),
    "height_cm": ("*高（cm）", "高（cm）", "*高(cm)", "高(cm)", "高度（cm）", "高"),
}


def _is_http_url(value: Any) -> bool:
    """店小秘图片列只接受无凭据、非本机/内网的公开 HTTP(S) 地址。"""
    return is_safe_external_url(str(value or "").strip())


def _http_urls(values: Any) -> list[str]:
    return [str(value).strip() for value in (values or []) if _is_http_url(value)]


def require_final_public_image_urls(values: list[str]) -> list[str]:
    """Fail closed when a final workbook still contains a local/private image."""
    normalized = [str(value or "").strip() for value in values]
    if any(
        not value.lower().startswith("https://") or not is_safe_external_url(value)
        for value in normalized
    ):
        raise ValueError("final workbook images must be public HTTPS URLs")
    return normalized


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
        for export_row in _dxm_export_rows(row):
            sheet.append(export_row)
    sheet.freeze_panes = "A2"
    for index, width in enumerate((36, 36, 60, 18, 14, 16, 14, 16, 45, 14, 18, 12, 12, 12, 14, 12, 16, 45, 60, 60, 14, 14, 45, 14, 10, 12), start=1):
        sheet.column_dimensions[_column_letter(index)].width = width
    destination.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(destination)


def _dxm_export_rows(row: dict[str, Any]) -> list[list[Any]]:
    """店小秘模板按 SKU 逐行输出：每个来源变种一行，无变种时输出单行。

    店小秘以「变种属性名称+值组合」识别 SKU：来源 1688 数据里同一规格组合可能对应多个
    SKU 货号（价格/库存不同），逐行全量导出会被判定为重复行导致整行拒绝
    （对齐交接文档 §8.4“每个导出规格组合唯一”）。因此按导出的属性组合去重，仅保留首行。
    """
    variant_records = row.get("source_variant_records") or []
    records = [item for item in variant_records if isinstance(item, dict)]
    if not records:
        return [_dxm_single_export_row(row, None)]
    exported: list[list[Any]] = []
    seen: set[tuple[Any, Any, Any, Any]] = set()
    for record in records:
        values = _dxm_single_export_row(row, record)
        # 变种属性名一/值一 + 属性名二/值二（export 行第 4~7 列）
        variant_key = (values[4], values[5], values[6], values[7])
        if variant_key in seen:
            continue
        seen.add(variant_key)
        exported.append(values)
    return exported if exported else [_dxm_single_export_row(row, None)]


def _dxm_single_export_row(row: dict[str, Any], variant: dict[str, Any] | None) -> list[Any]:
    # 预检覆盖（precheck 页保存的标题/描述/图片/核心字段，用户可改可不改，默认保留生成结果）
    preview_overrides = row.get("preview_overrides") or {}
    if not isinstance(preview_overrides, dict):
        preview_overrides = {}
    core_fields = preview_overrides.get("core_fields") or {}
    if not isinstance(core_fields, dict):
        core_fields = {}
    override_carousel = _http_urls(preview_overrides.get("carousel_images"))
    slot_overrides = preview_overrides.get("image_slot_overrides") or {}
    if not isinstance(slot_overrides, dict):
        slot_overrides = {}
    override_main = str(preview_overrides.get("main_image") or "").strip()
    override_detail = _http_urls(preview_overrides.get("detail_images"))

    optimized_title = str(preview_overrides.get("title") or row.get("optimized_title") or "").strip()
    description = str(preview_overrides.get("description") or row.get("description") or "").strip()
    skc = str(row.get("skc") or "").strip()
    sku = str(core_fields.get("sku") or row.get("sku") or skc).strip()
    main_image_url = str(row.get("image_url") or "").strip()
    source_url = str(row.get("source_url") or "").strip()
    source_image_urls = row.get("source_image_urls") or []
    source_detail_image_urls = row.get("source_detail_image_urls") or []
    source_attributes = row.get("source_attributes") or []
    cost = row.get("cost")
    category = str(row.get("category") or "").strip()
    category_path = str(core_fields.get("category_path") or row.get("category_path") or category).strip()
    category_id = str(core_fields.get("category_id") or row.get("category_id") or "").strip()

    # 变种属性值翻译表（来源中文值 → 目标语言显示名，由 service 的 AI 翻译步骤生成）
    value_translations = row.get("variant_value_translations") or {}
    if not isinstance(value_translations, dict):
        value_translations = {}

    # 变种属性：SKU 自己的 attributes 优先，其次取商品级前两条「名称+值」属性。
    # 两者都先过清洗/校验层，剔除 Temu 采集混入的页面元数据噪音（品牌/评分/运费/支付/描述等）。
    if variant is not None:
        display_name = str(variant.get("display_name") or "").strip()
        variant_values = []
        for name_text, value_text in clean_variant_attributes(variant.get("attributes")):
            # 规格轴名称本地映射 + 属性值翻译（操作员编辑的 display_name 优先）
            export_value = display_name if display_name else value_translations.get(value_text, value_text)
            variant_values.append((name_text, export_value))
        variant_sku = str(variant.get("sku_id") or "").strip() or sku
    else:
        variant_values = []
        variant_sku = sku

    if not variant_values:
        # 商品级属性兜底：清洗后仅取前两条
        for name_text, value_text in clean_variant_attributes(source_attributes):
            variant_values.append((name_text, value_translations.get(value_text, value_text)))
            if len(variant_values) >= 2:
                break

    variant_name_1, variant_value_1, variant_name_2, variant_value_2 = "", "", "", ""
    if variant_values:
        variant_name_1, variant_value_1 = variant_values[0]
        if len(variant_values) > 1:
            variant_name_2, variant_value_2 = variant_values[1]
    if not variant_name_1:
        variant_name_1 = "规格"
    if not variant_value_1:
        # 店小秘 *变种属性值一 必填；无规格值数据时对齐原型 _default_variant_export_value()
        variant_value_1 = "Estándar" if str(row.get("target_language") or "").strip().casefold() == "es" else "Standard"

    # 四宫格落位（对齐交接文档 §11.3）：预览图/素材图=第1张分图；轮播图=4张分图+完整四宫格总览（总览放最后）。
    # 生成图为本地路径（未上传 COS）时店小秘无法访问，仅 http(s) 生成图才可用，否则回退来源 http 图片。
    # 预检覆盖优先：用户改过标题/图片后以覆盖值为准，未改则走原生成/回退逻辑。
    generated_carousel = row.get("carousel_image_paths") or []
    grid_summary_path = str(row.get("grid_image_summary_path") or "").strip()
    detail_image_paths = row.get("detail_image_paths") or []
    generated_images = _http_urls(list(generated_carousel) + [grid_summary_path])
    if slot_overrides:
        # 新版尺寸画布以旧版整组人工轮播为基线再覆盖单槽；总览仍保留在末尾。
        slotted_images = _http_urls(
            [slot.get("value") for slot in apply_slot_overrides(row, preview_overrides)]
        )
        export_images = slotted_images + _http_urls([grid_summary_path])
        carousel = "\n".join(export_images)
        main_image = override_main if _is_http_url(override_main) else next(iter(slotted_images), "")
        material_images = main_image
    elif override_carousel:
        # 纯旧版整组轮播图覆盖保持原语义，避免历史预检数据被悄悄追加图片。
        carousel = "\n".join(override_carousel)
        main_image = override_main if _is_http_url(override_main) else override_carousel[0]
        material_images = main_image
    elif generated_images:
        carousel = "\n".join(generated_images)
        main_image = generated_images[0]
        material_images = generated_images[0]
    else:
        carousel = "\n".join(_http_urls(source_image_urls))
        main_image = main_image_url if _is_http_url(main_image_url) else next(iter(_http_urls(source_image_urls)), "")
        # 店小秘 *产品素材图 为单值列（最大导入1条，对齐原型 DXM_COLUMNS[19]=main_image_url）
        material_images = next(iter(_http_urls(source_detail_image_urls)), "")
        if not material_images:
            material_images = main_image

    # 详情图以 HTML 追加到产品描述（交接文档 §10/§12）；仅追加可外部访问的 http(s) 地址
    # Presence is semantic: an explicit empty array means the operator removed
    # every detail image and must never resurrect generated legacy values.
    detail_sources = (
        override_detail
        if "detail_images" in preview_overrides
        else _http_urls(detail_image_paths)
    )
    detail_html = "".join(f'<img src="{value}" />' for value in detail_sources)
    if detail_html:
        description = f"{description}\n{detail_html}".strip()

    # 物流尺寸/重量与包装（对齐原型 _build_dxm_row：AI 尺寸预估 + 包装形状/类型导出标签）
    dimensions = row.get("product_dimensions") or {}
    if not isinstance(dimensions, dict):
        dimensions = {}
    package_fields, _ = _variant_shipping_package_fields(row, variant, preview_overrides)
    length = _export_number(
        package_fields.get("length_cm")
        if "length_cm" in package_fields
        else core_fields.get("length_cm") if "length_cm" in core_fields else dimensions.get("length_cm")
    )
    width = _export_number(
        package_fields.get("width_cm")
        if "width_cm" in package_fields
        else core_fields.get("width_cm") if "width_cm" in core_fields else dimensions.get("width_cm")
    )
    height = _export_number(
        package_fields.get("height_cm")
        if "height_cm" in package_fields
        else core_fields.get("height_cm") if "height_cm" in core_fields else dimensions.get("height_cm")
    )
    weight = _export_number(
        package_fields.get("weight_g")
        if "weight_g" in package_fields
        else core_fields.get("weight_g") if "weight_g" in core_fields else dimensions.get("weight_g")
    )
    package_shape, package_type = _package_export_values(dimensions)

    # 收集尺寸文本仅用于长宽高缺失时的尺寸兜底。导出重量始终采用当前重量，
    # 不再按长×宽×高计算抛重，也不再用抛重抬高当前重量。
    source_attr_map = source_attributes if isinstance(source_attributes, dict) else {}
    dimensions_texts: list[Any] = []
    if length not in ("", None) and width not in ("", None) and height not in ("", None):
        dimensions_texts.append(f"{length}*{width}*{height}")
    # 变种属性全部值（不限于导出前两条规格轴）与商品级属性都可能携带尺寸文本
    if variant is not None:
        variant_attributes = variant.get("attributes") or {}
        if isinstance(variant_attributes, dict):
            for attr_value in variant_attributes.values():
                dimensions_texts.append(attr_value)
                # 1688 规格表：变种属性值原文（如【45*50cm】2.5丝，常规款）是规格表 key，
                # 对应 value（如 "25 20 0.50 250 4"）携带完整 长/宽/高/体积/重量。
                spec_row = source_attr_map.get(attr_value)
                if spec_row:
                    dimensions_texts.append(spec_row)
    if isinstance(source_attributes, dict):
        dimensions_texts.extend(source_attributes.values())
    dimensions_texts.extend(value for _, value in variant_values)
    # 长宽高列兜底：AI 未产出 product_dimensions（长宽高缺失）时，从变种/规格表文本
    # 解析首个三维尺寸填列，保证店小秘 *长/宽/高（cm） 必填列非空。
    if length == "" or width == "" or height == "":
        for text in dimensions_texts:
            parsed_lwh = _parse_dimensions(text)
            if parsed_lwh is not None:
                length = _export_number(parsed_lwh[0])
                width = _export_number(parsed_lwh[1])
                height = _export_number(parsed_lwh[2])
                break

    # 建议售价（对齐原型 _build_dxm_row）：变种建议售价 → 行建议售价 → 来源成本；预检核心字段覆盖优先
    suggested_price = variant.get("suggested_price") if variant else None
    if suggested_price in (None, ""):
        suggested_price = row.get("suggested_price")
    if suggested_price in (None, ""):
        suggested_price = cost
    if core_fields.get("suggested_price") not in (None, ""):
        suggested_price = core_fields.get("suggested_price")

    # 申报价格（对齐原型 _declared_price_for）：
    # 显式申报价 → max(价, 150)；否则 建议售价×4，下限 150；无任何价据 → 150
    declared_price_value = variant.get("declared_price") if variant else None
    if declared_price_value in (None, ""):
        declared_price_value = row.get("declared_price")
    if core_fields.get("declared_price") not in (None, ""):
        declared_price_value = core_fields.get("declared_price")
    parsed_declared = _parse_money(declared_price_value)
    if parsed_declared is not None:
        declared_price_value = max(parsed_declared, DECLARED_PRICE_MIN_CNY)
    else:
        declared_price_value = _declared_price_fallback(suggested_price)
        if declared_price_value is None:
            declared_price_value = DECLARED_PRICE_MIN_CNY

    stock = _normalize_stock(variant.get("stock") if variant else None)
    if stock <= 0:
        stock = _normalize_stock(row.get("stock"))
    if core_fields.get("stock") not in (None, ""):
        stock = _normalize_stock(core_fields.get("stock"))

    # 店小秘重量导出统一以当前重量向上取整到 100：不足 100 按 100、
    # 不足 200 按 200……（最低 100）。长宽高不参与重量计算。
    # 店小秘要求材积重量（长×宽×高÷6）≤ 实际重量，否则报“材积重量大于实际重量，无法录入”。
    # 导出前兜底：若体积重量超过当前重量，以店小秘导入为准，将重量抬升到体积重量，避免导入失败。
    # 最后无论怎么算都不得超过 899g，超出封顶到 899，确保在店小秘导入范围内。
    weight = _dxm_enforce_volumetric_weight(length, width, height, _ceil_weight_for_export(weight))
    weight = _cap_dxm_weight(weight)

    return [
        optimized_title,
        optimized_title,
        description,
        skc,
        variant_name_1,
        variant_value_1,
        variant_name_2,
        variant_value_2,
        main_image,
        declared_price_value if declared_price_value not in (None, "") else "",
        variant_sku,
        length,
        width,
        height,
        weight,
        "",  # 识别码类型
        "",  # 识别码
        source_url,
        carousel,
        material_images,
        package_shape,
        package_type,
        "",  # 外包装图片
        suggested_price if suggested_price not in (None, "") else "",
        stock,
        DEFAULT_SHIP_DAYS,  # 发货时效（天）
        category_path,  # *产品分类
        category_path,  # 产品分类
        category_path,  # 类目路径
        category_id,  # 类目ID
        "单品",  # SKU分类
        1,  # SKU分类数量
        "件",  # SKU分类单位
        "", "", "", "", "", "", "", "", "",  # 其余 SKU 分类字段占位
    ]


def _variant_shipping_package_fields(
    row: dict[str, Any],
    variant: dict[str, Any] | None,
    preview_overrides: dict[str, Any],
) -> tuple[dict[str, Any], set[str]]:
    """Return exportable package evidence for exactly one matched SKU.

    The 1688 package table can contain variants missing from the product's SKU
    matrix. Such rows are intentionally ignored here; showing them in precheck
    is useful, but allowing them to alter any exported SKU is not.
    """
    if not isinstance(variant, dict):
        return {}, set()
    variant_key = str(variant.get("sku_id") or "").strip()
    if not variant_key:
        return {}, set()
    package_record: dict[str, Any] | None = None
    records = row.get("shipping_package_records") or []
    if isinstance(records, list):
        for record in records:
            if not isinstance(record, dict) or record.get("match_status") != "matched":
                continue
            if str(record.get("variant_key") or record.get("variant_sku_id") or record.get("record_key") or "").strip() == variant_key:
                package_record = record
                break
    if package_record is None:
        embedded = variant.get("shipping_package")
        if isinstance(embedded, dict) and embedded.get("match_status") == "matched":
            package_record = embedded
    if package_record is None:
        return {}, set()

    fields = {
        key: package_record[key]
        for key in ("length_cm", "width_cm", "height_cm", "weight_g")
        if _positive_export_number(package_record.get(key))
    }
    overrides = preview_overrides.get("shipping_package_records") or {}
    if not isinstance(overrides, dict):
        return fields, set()
    manual = overrides.get(variant_key)
    if not isinstance(manual, dict):
        return fields, set()
    manual_fields: set[str] = set()
    for key in ("length_cm", "width_cm", "height_cm", "weight_g"):
        if _positive_export_number(manual.get(key)):
            fields[key] = manual[key]
            manual_fields.add(key)
    return fields, manual_fields


def _positive_export_number(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _package_export_values(dimensions: dict[str, Any]) -> tuple[str, str]:
    profile = str(dimensions.get("package_profile") or "").strip().lower()
    return _PACKAGE_EXPORT_BY_PROFILE.get(profile, _PACKAGE_EXPORT_DEFAULT)


def _export_number(value: Any) -> Any:
    """把数值型字段转成整数/float，空值保持空串（避免写入 0 掩盖缺失）。"""
    if value in (None, ""):
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number == int(number):
        return int(number)
    return round(number, 2)


def _ceil_weight_for_export(value: Any) -> Any:
    """店小秘重量导出向上取整到 100：不足 100 按 100、不足 200 按 200…（最低 100）。

    仅在有效数值上生效；空值/非数值原样返回，避免掩盖缺失。非正值（含 AI
    估出的 0）视为无效重量，导出为空而非写入 0。
    """
    if value in (None, ""):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    if number <= 0:
        return ""
    ceiled = int(math.ceil(number / 100.0)) * 100
    return ceiled


# 店小秘重量导入上限：最终导出的重量（g）无论如何计算都不允许超过该值，
# 否则无法导入。超过时直接封顶到上限。
DXM_WEIGHT_MAX_GRAM = 899


def _cap_dxm_weight(value: Any, max_value: float = DXM_WEIGHT_MAX_GRAM) -> Any:
    """店小秘重量上限封顶：超过上限的导出重量封顶到 max_value，空值/非数值原样返回。"""
    if value in ("", None):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    if number <= 0:
        return value
    return min(number, max_value)


def _dxm_enforce_volumetric_weight(length: Any, width: Any, height: Any, weight: Any) -> Any:
    """店小秘导入前保证 材积重量（长×宽×高÷6，单位 g）≤ 实际重量。

    店小秘校验：材积重量 > 实际重量 时报“材积重量大于实际重量，无法录入”。
    导出前兜底：仅当长/宽/高齐全且当前重量有效（正数）时，若体积重量超过当前重量，
    以店小秘导入为准，将重量抬升到体积重量并按 100 向上取整，避免导入失败。
    当前重量缺失/非正（导出为空）时不虚构重量，原样返回以保留缺失提示。
    """
    if length in ("", None) or width in ("", None) or height in ("", None):
        return weight
    try:
        volumetric = float(length) * float(width) * float(height) / 6.0
    except (TypeError, ValueError):
        return weight
    if weight in ("", None):
        return weight
    try:
        current = float(weight)
    except (TypeError, ValueError):
        return weight
    if current <= 0:
        return weight
    if current < volumetric:
        return _ceil_weight_for_export(volumetric)
    return weight


# 尺寸文本模式：如 "30*20*10" / "30×20×10cm" / "40.5*30*20 CM"（1688 变种尺寸属性值）
_DIMENSIONS_PATTERN = re.compile(
    r"(?P<l>\d+(?:\.\d+)?)\s*[*×xX]\s*(?P<w>\d+(?:\.\d+)?)\s*[*×xX]\s*(?P<h>\d+(?:\.\d+)?)"
    r"\s*(?P<unit>cm|厘米|mm|毫米)?",
    re.IGNORECASE,
)

# 1688 规格表行格式：长 宽 高 体积 重量（如 "25 20 0.50 250 4"，取前三个数为长宽高，单位 cm）
_SPACED_DIMENSIONS_PATTERN = re.compile(
    r"(?P<l>\d+(?:\.\d+)?)\s+(?P<w>\d+(?:\.\d+)?)\s+(?P<h>\d+(?:\.\d+)?)"
)


def _parse_dimensions(value: Any) -> tuple[float, float, float] | None:
    """从文本提取 (长, 宽, 高) 厘米；无法识别返回 None。

    支持两种格式：
    1. 乘号分隔三维尺寸（30*20*10cm / 30×20×10 CM），单位 mm/毫米 时换算为厘米（÷10）。
    2. 空格分隔的 1688 规格表行（"25 20 0.50 250 4"，前三个数为长/宽/高 cm）。
    1688 变种尺寸属性常以毫米标注（如 34.5cm 商品写 "345*255*55mm"），
    因此必须换算后再写入长宽高列。
    """
    if value in (None, ""):
        return None
    match = _DIMENSIONS_PATTERN.search(str(value))
    if match:
        length = float(match.group("l"))
        width = float(match.group("w"))
        height = float(match.group("h"))
        unit = (match.group("unit") or "").lower()
        if unit in {"mm", "毫米"}:
            length, width, height = length / 10.0, width / 10.0, height / 10.0
    else:
        match = _SPACED_DIMENSIONS_PATTERN.search(str(value))
        if not match:
            return None
        length = float(match.group("l"))
        width = float(match.group("w"))
        height = float(match.group("h"))
    if length <= 0 or width <= 0 or height <= 0:
        return None
    return length, width, height


def _normalize_stock(value: Any) -> int:
    amount = _parse_money(value)
    if amount is None:
        return DXM_STOCK_MIN
    return max(DXM_STOCK_MIN, min(DXM_STOCK_MAX, int(amount)))


def _declared_price_fallback(cost: Any) -> float | None:
    """对齐原型 _declared_price_for：无显式申报价时 按成本(CNY)×4，下限 150。"""
    amount = _parse_money(cost)
    if amount is None or amount <= 0:
        return None
    return round(max(amount * DECLARED_PRICE_MULTIPLIER, DECLARED_PRICE_MIN_CNY), 2)


def _parse_money(value: Any) -> float | None:
    """对齐原型 _parse_money：从任意文本中提取第一个非负数字（容忍 $、￥、逗号等）。"""
    text = str(value or "").replace(",", "").strip()
    match = re.search(r"([0-9]+(?:\.[0-9]{1,4})?)", text)
    if not match:
        return None
    try:
        number = float(match.group(1))
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


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
    # 店小秘模板的长/宽/高/重量列：归一为下游可确定性解析的物流原始文本。
    weight = str(normalized.get("weight_text") or "").strip()
    if weight:
        normalized["weight_text"] = weight
    length = str(normalized.get("length_cm") or "").strip()
    width = str(normalized.get("width_cm") or "").strip()
    height = str(normalized.get("height_cm") or "").strip()
    if length and width and height:
        normalized["package_info_text"] = f"{length}x{width}x{height}cm"
    return normalized


def _clean_header(value: Any) -> str:
    return str(value or "").replace("\n", " ").strip()


# ===== 妙手 Temu 导入模板（服饰类 / 非服饰类）=====
# 生成方式：以官方模板为底稿（保留第 1、2 行表头/字段说明），清掉示例数据行后
# 从第 3 行开始逐 SKU 写入。
#
# 性能说明：官方模板里「类目ID」(约 4.2 万行) 与「包装清单」(约 1.5 万行) 两张参考表
# 只供人工查阅，openpyxl 每次加载/保存它们要额外约 8 秒。因此：
#   1) 首次使用时把模板精简为「只含 Sheet1」的缓存文件，之后导出直接读缓存（毫秒级）；
#   2) 两张参考表抽出为独立参考表文件，需要手填类目ID 时单独打开查阅。
MS_KIND_APPAREL = "apparel"
MS_KIND_GENERAL = "general"
MS_KIND_OPTIONS = (MS_KIND_APPAREL, MS_KIND_GENERAL)

# 模板内置在 product_processing/templates 下随版本发布（发布方会在上线时同步覆盖更新）。
MS_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates"
MS_TEMPLATE_FILES = {
    MS_KIND_APPAREL: "妙手Temu导入模板-服饰类模板.xlsx",
    MS_KIND_GENERAL: "妙手Temu导入模板-非服饰类模板.xlsx",
}
# 由官方模板精简而来、只含 Sheet1 的缓存文件（放在 generated 子目录，随模板更新自动失效重建）。
MS_GENERATED_DIR_NAME = "generated"
MS_SLIM_TEMPLATE_FILES = {
    MS_KIND_APPAREL: "妙手-服饰类.sheet1.xlsx",
    MS_KIND_GENERAL: "妙手-非服饰类.sheet1.xlsx",
}
# 人工查阅用的参考表（类目ID + 包装清单），与导出分离，避免拖慢每次导出。
MS_REFERENCE_FILE = "妙手参考表-类目ID与包装清单.xlsx"
MS_MAIN_SHEET = "Sheet1"
# 精简模板与参考表各自一把锁：参考表生成要序列化两张万行级参考表（约 8 秒），
# 不能阻塞导出所需的精简模板读取。
_MS_TEMPLATE_LOCK = threading.Lock()
_MS_REFERENCE_LOCK = threading.Lock()

# 妙手默认导出值（产地等系统无数据字段的策略常量，非模板说明的字段类型）。
# 产地必须带行政区后缀（妙手按其标准产地名比对，如模板示例“中国-福建省”），
# 不带“省/市”后缀会被判“产地有误”导致导入失败。
MS_ORIGIN_DEFAULT = "中国-浙江省"
MS_CUSTOMIZED_DEFAULT = "否"
MS_SENSITIVE_DEFAULT = "否"

# 服饰类模板（Sheet1，1-based 列号），列结构见模板表头：类目ID/主编号/标题/英文标题/...
_MS_APPAREL_COLUMNS = {
    "category_id": 2,
    "main_no": 3,
    "title": 4,
    "title_en": 5,
    "description": 6,
    "ship_days": 7,
    "origin": 8,
    "made_in": 9,
    "external_url": 10,
    "material_image": 11,
    "customized": 12,
    "spec_name_1": 13,
    "spec_value_1": 14,
    "spec_name_2": 15,
    "spec_value_2": 16,
    "color_images": 17,
    "main_sku_no": 18,
    "declared_price": 19,
    "suggested_price": 20,
    "length_cm": 21,
    "width_cm": 22,
    "height_cm": 23,
    "weight_g": 24,
    "stock": 25,
    "platform_sku": 26,
    "sensitive": 27,
    "sensitive_value": 28,
    "code_type": 33,
    "code": 34,
    "sku_class_type": 35,
    "sku_class_count": 36,
    "sku_class_unit": 37,
    "independent_pack": 38,
    "pack_list": 39,
    "pack_list_count": 40,
    "video": 41,
    "manual": 42,
    "supply_url": 43,
}

# 非服饰类模板（Sheet1，1-based 列号）：列位与服饰类整体平移，图片列结构不同。
_MS_GENERAL_COLUMNS = {
    "category_id": 2,
    "main_no": 3,
    "title": 4,
    "title_en": 5,
    "description": 6,
    "ship_days": 7,
    "main_sku_no": 8,
    "origin": 9,
    "made_in": 10,
    "external_url": 11,
    "carousel_images": 12,
    "material_image": 13,
    "customized": 14,
    "spec_name_1": 15,
    "spec_value_1": 16,
    "spec_name_2": 17,
    "spec_value_2": 18,
    "preview_image": 19,
    "declared_price": 20,
    "suggested_price": 21,
    "length_cm": 22,
    "width_cm": 23,
    "height_cm": 24,
    "weight_g": 25,
    "stock": 26,
    "platform_sku": 27,
    "sensitive": 28,
    "sensitive_value": 29,
    "code_type": 34,
    "code": 35,
    "sku_class_type": 36,
    "sku_class_count": 37,
    "sku_class_unit": 38,
    "independent_pack": 39,
    "pack_list": 40,
    "pack_list_count": 41,
    "video": 42,
    "manual": 43,
    "supply_url": 44,
}


def _miaoshou_generated_dir(template_dir: Path) -> Path:
    return template_dir / MS_GENERATED_DIR_NAME


def ensure_miaoshou_reference_workbook(*, template_dir: Path | None = None) -> Path:
    """生成/更新妙手参考表文件（类目ID + 包装清单），供人工查阅。

    这两张表原本在官方模板里，导出时一并携带会让 openpyxl 每次多花约 8 秒。
    模板更新或参考表缺失时重建一次；耗时较长（约 8 秒），导出路径通过后台线程调用。
    """
    base = template_dir or MS_TEMPLATE_DIR
    full = base / MS_TEMPLATE_FILES[MS_KIND_GENERAL]
    if not full.is_file():
        raise ValueError(f"miaoshou template not bundled: {full}")
    reference = base / MS_REFERENCE_FILE
    with _MS_REFERENCE_LOCK:
        try:
            if reference.is_file() and reference.stat().st_mtime >= full.stat().st_mtime:
                return reference
        except OSError:
            pass
        workbook = load_workbook(full)
        try:
            for name in list(workbook.sheetnames):
                if name == MS_MAIN_SHEET:
                    workbook.remove(workbook[name])
            reference.parent.mkdir(parents=True, exist_ok=True)
            temporary = reference.with_name(f".{reference.name}.tmp")
            workbook.save(temporary)
        finally:
            workbook.close()
        os.replace(temporary, reference)
        return reference


def ensure_miaoshou_reference_async(*, template_dir: Path | None = None) -> None:
    """后台生成参考表，避免拖慢当前导出请求（失败静默，下次导出会重试）。"""
    def _run() -> None:
        try:
            ensure_miaoshou_reference_workbook(template_dir=template_dir)
        except Exception:  # noqa: BLE001 - 参考表生成失败不应影响导出
            return

    threading.Thread(target=_run, name="miaoshou-reference-builder", daemon=True).start()


def miaoshou_slim_template_path(kind: str, *, template_dir: Path | None = None) -> Path:
    """精简模板（只含 Sheet1）路径；缺失或官方模板更新后自动重建。

    官方模板的两张参考表（类目ID/包装清单）只供人工查阅，但会让 openpyxl 每次
    加载/保存多花约 8 秒。这里生成只含 Sheet1 的缓存副本，导出时读它即可；
    参考表则抽成独立文件（ensure_miaoshou_reference_workbook）另行查阅。
    """
    if kind not in MS_TEMPLATE_FILES:
        raise ValueError(f"unsupported miaoshou template kind: {kind}")
    base = template_dir or MS_TEMPLATE_DIR
    full = base / MS_TEMPLATE_FILES[kind]
    if not full.is_file():
        raise ValueError(f"miaoshou template not bundled: {full}")
    slim = _miaoshou_generated_dir(base) / MS_SLIM_TEMPLATE_FILES[kind]
    with _MS_TEMPLATE_LOCK:
        try:
            if slim.is_file() and slim.stat().st_mtime >= full.stat().st_mtime:
                return slim
        except OSError:
            pass
        workbook = load_workbook(full)
        try:
            for name in list(workbook.sheetnames):
                if name != MS_MAIN_SHEET:
                    workbook.remove(workbook[name])
            slim.parent.mkdir(parents=True, exist_ok=True)
            temporary = slim.with_name(f".{slim.name}.tmp")
            workbook.save(temporary)
        except OSError:
            # 安装目录不可写时退化为直接读官方模板：慢一些但导出仍可用。
            return full
        finally:
            workbook.close()
        try:
            os.replace(temporary, slim)
        except OSError:
            return full
    # 参考表与精简模板同源同代，重建精简模板时顺带后台补齐参考表。
    ensure_miaoshou_reference_async(template_dir=template_dir)
    return slim


def create_miaoshou_workbook(
    rows: list[dict[str, Any]],
    kind: str,
    destination: Path,
    *,
    template_dir: Path | None = None,
) -> int:
    """按妙手导入模板生成导出工作簿：保留模板 Sheet1 结构，逐 SKU 写入导出结果。

    返回实际写入的数据行数。模板第 1 行表头、第 2 行字段说明保留，第 3 行起的
    示例数据将被清空后重写。
    """
    if kind not in MS_TEMPLATE_FILES:
        raise ValueError(f"unsupported miaoshou template kind: {kind}")
    columns = _MS_APPAREL_COLUMNS if kind == MS_KIND_APPAREL else _MS_GENERAL_COLUMNS
    slim_path = miaoshou_slim_template_path(kind, template_dir=template_dir)

    workbook = load_workbook(slim_path)
    sheet = workbook[MS_MAIN_SHEET]
    # 移除模板示例行上残留的合并单元格后，删除第 3 行起全部示例内容。
    for merged in list(sheet.merged_cells.ranges):
        sheet.unmerge_cells(str(merged))
    if sheet.max_row and sheet.max_row > 2:
        sheet.delete_rows(3, sheet.max_row - 2)

    row_number = 3
    for product_row in rows:
        for values in _miaoshou_row_values(product_row, kind, columns):
            for column, value in values.items():
                sheet.cell(row=row_number, column=column, value=value)
            row_number += 1
    destination.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(destination)
    return row_number - 3


def _miaoshou_row_values(
    row: dict[str, Any],
    kind: str,
    columns: dict[str, int],
) -> list[dict[int, Any]]:
    """把一个商品的导出结果展开为若干妙手 SKU 行（每行=一个变种，无变种=单行）。

    规格名/值、申报价、建议售价、长宽高、图片等基础取值与店小秘导出行保持同口径
    （复用 _dxm_single_export_row）；妙手侧差异在这里单独处理：
      - 描述列：详情图按「每行一个图片 URL」追加（妙手描述列不支持 <img> 标签）
      - 重量列：导出真实重量，不做店小秘的 100 取整/899 封顶
      - 产地/定制品/是否敏感属性：系统无数据，按既定默认值填写
    """
    variant_records = [
        item for item in (row.get("source_variant_records") or []) if isinstance(item, dict)
    ]
    targets: list[dict[str, Any] | None] = [None] if not variant_records else variant_records
    exported: list[dict[int, Any]] = []
    seen: set[tuple[Any, Any, Any, Any]] = set()
    for variant in targets:
        dxm_row = _dxm_single_export_row(row, variant)
        # 与店小秘一致：按「规格名1/值1 + 规格名2/值2」组合去重，避免同规格多价行
        # 在妙手被判重复。
        variant_key = (dxm_row[4], dxm_row[5], dxm_row[6], dxm_row[7])
        if variant_key in seen:
            continue
        seen.add(variant_key)
        exported.append(_miaoshou_single_row_values(row, variant, kind, dxm_row, columns))
    return exported or [
        _miaoshou_single_row_values(row, None, kind, _dxm_single_export_row(row, None), columns)
    ]


def _miaoshou_single_row_values(
    row: dict[str, Any],
    variant: dict[str, Any] | None,
    kind: str,
    dxm_row: list[Any],
    columns: dict[str, int],
) -> dict[int, Any]:
    values: dict[int, Any] = {}
    preview_overrides = row.get("preview_overrides") or {}
    if not isinstance(preview_overrides, dict):
        preview_overrides = {}
    core_fields = preview_overrides.get("core_fields") or {}
    if not isinstance(core_fields, dict):
        core_fields = {}

    # 主编号：同一 SPU 的所有 SKU 行共用（取商品级 SKC/货号，不用变种 SKU）。
    skc = str(row.get("skc") or "").strip()
    product_sku = str(core_fields.get("sku") or row.get("sku") or "").strip()
    main_no = skc or product_sku

    # 标题/英文标题与店小秘同口径（dxm 行 0/1 列）。
    title = str(dxm_row[0] or "").strip()
    values[columns["title"]] = title
    values[columns["title_en"]] = title
    values[columns["main_no"]] = main_no

    # 产品描述：纯文本 + 详情图 URL（每行一个）；详情图优先级与店小秘一致。
    description = str(preview_overrides.get("description") or row.get("description") or "").strip()
    if "detail_images" in preview_overrides:
        detail_sources = _http_urls(preview_overrides.get("detail_images"))
    else:
        detail_sources = _http_urls(row.get("detail_image_paths"))
    lines = [description] if description else []
    lines.extend(detail_sources)
    values[columns["description"]] = "\n".join(lines)

    # 规格名/值（dxm 行 4..7 列）。规格名称2/值2 在妙手模板中为必填：有第二规格轴时
    # 用真实值；没有时兜底为「规格 / Standard」（西语站 Estándar），与店小秘无变种时的
    # 兜底口径一致，避免服饰类模板因必填列为空被拒。
    values[columns["spec_name_1"]] = dxm_row[4]
    values[columns["spec_value_1"]] = dxm_row[5]
    spec_name_2 = dxm_row[6] if dxm_row[6] else ""
    spec_value_2 = dxm_row[7] if dxm_row[7] else ""
    if not (spec_name_2 and spec_value_2):
        spec_name_2 = "规格"
        spec_value_2 = (
            "Estándar"
            if str(row.get("target_language") or "").strip().casefold() == "es"
            else "Standard"
        )
    values[columns["spec_name_2"]] = spec_name_2
    values[columns["spec_value_2"]] = spec_value_2

    # 图片：与店小秘导出同一批最终图片（均已是公网 https）。
    carousel_text = str(dxm_row[18] or "").strip()
    main_image = str(dxm_row[8] or "").strip()
    if not main_image and carousel_text:
        main_image = carousel_text.splitlines()[0]
    if kind == MS_KIND_APPAREL:
        values[columns["color_images"]] = carousel_text
        values[columns["material_image"]] = main_image
    else:
        values[columns["carousel_images"]] = carousel_text
        values[columns["material_image"]] = main_image
        values[columns["preview_image"]] = main_image

    # 价格/物流/库存。
    values[columns["declared_price"]] = dxm_row[9]
    values[columns["suggested_price"]] = dxm_row[23] if dxm_row[23] not in (None, "") else ""
    values[columns["length_cm"]] = dxm_row[11]
    values[columns["width_cm"]] = dxm_row[12]
    values[columns["height_cm"]] = dxm_row[13]
    values[columns["weight_g"]] = _miaoshou_raw_weight(row, variant, preview_overrides, core_fields)
    # 妙手要求库存为「大于等于 0 的整数」：库存为 0 或缺失时也必须写出整数 0，
    # 留空会被判为导入失败（“库存，对应值必须是大于等于0的整数”）。
    values[columns["stock"]] = _normalize_stock(dxm_row[24])
    values[columns["ship_days"]] = dxm_row[25] if dxm_row[25] not in (None, "") else DEFAULT_SHIP_DAYS

    # 系统无数据/固定策略列。
    values[columns["origin"]] = MS_ORIGIN_DEFAULT
    values[columns["customized"]] = MS_CUSTOMIZED_DEFAULT
    values[columns["sensitive"]] = MS_SENSITIVE_DEFAULT
    external_url = str(dxm_row[17] or "").strip()
    values[columns["external_url"]] = external_url
    # 货源链接（选填）店小秘无对应数据，妙手支持为空，这里留空由用户自行补充。
    values[columns["supply_url"]] = ""
    return values


def _miaoshou_raw_weight(
    row: dict[str, Any],
    variant: dict[str, Any] | None,
    preview_overrides: dict[str, Any],
    core_fields: dict[str, Any],
) -> Any:
    """妙手重量：真实重量（g），不做店小秘的向上取整 100 与 899 封顶。

    取值链与店小秘一致：匹配到的 SKU 包装件重尺 → 预检核心字段 → 商品级 AI 估算。
    """
    dimensions = row.get("product_dimensions") or {}
    if not isinstance(dimensions, dict):
        dimensions = {}
    package_fields, _ = _variant_shipping_package_fields(row, variant, preview_overrides)
    weight_value = package_fields.get("weight_g")
    if weight_value in (None, ""):
        weight_value = core_fields.get("weight_g")
    if weight_value in (None, ""):
        weight_value = dimensions.get("weight_g")
    return _export_number(weight_value)
