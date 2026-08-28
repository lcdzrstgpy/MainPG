"""combo_kit 店小秘导入模板导出：把一套组合套装数据映射到 42 列导入行。

复用 pod_customization.dianxiaomi 的列定义与 xlsx 构建（DXM_COLUMNS /
build_dianxiaomi_workbook），但把 combo_kit_sets 的字段映射到对应列。
这是「导出并补字段」的落地点：套装主档补录的店小秘字段（申报价/长宽高/
重量/库存/分类/识别码/建议售价）在此被填入必需列。
"""
from __future__ import annotations

from typing import Any

from ..pod_customization.dianxiaomi import DXM_COLUMNS, build_dianxiaomi_workbook

# 成品图角色（含融合主图）在店小秘导入行中的展示顺序。
COMBO_IMAGE_ROLES = ("main", "carousel_2", "carousel_3", "white_bg", "detail_shot", "detail_page")

# 店小秘必填列（带 *）对应 combo_kit 必录项；导出前逐一校验，缺失即阻塞。
_REQUIRED_COLUMNS: dict[int, str] = {
    0: "产品标题",
    1: "英文标题",
    4: "变种属性名称一",
    5: "变种属性值一",
    8: "预览图",
    9: "申报价格",
    11: "长(cm)",
    12: "宽(cm)",
    13: "高(cm)",
    14: "重量(g)",
    18: "轮播图",
    19: "产品素材图",
    26: "产品分类",
}

_IMAGE_ROLE_LABELS = {
    "main": "套装主图",
    "carousel_2": "轮播图2",
    "carousel_3": "轮播图3",
    "white_bg": "白底尺寸图",
    "detail_shot": "细节图",
    "detail_page": "详情图",
}


class ComboDianxiaomiExportError(ValueError):
    """导出前校验失败（缺必填字段/图片未发布到 COS）。"""


class ComboDianxiaomiExport:
    def __init__(self, content: bytes, filename: str, set_id: str) -> None:
        self.content = content
        self.filename = filename
        self.set_id = set_id


def build_combo_dianxiaomi_export(set_data: dict[str, Any]) -> ComboDianxiaomiExport:
    """生成一整套组合套装的店小秘导入 xlsx。

    ``set_data`` 应为 ``ComboKitService.get_set`` 的返回（已解析 json 字段）。
    缺必填项时抛 ``ComboDianxiaomiExportError``。
    """
    images = _public_images(set_data)
    row = _build_row(set_data, images)
    _validate_row(row)
    set_id = str(set_data.get("set_id") or "")
    return ComboDianxiaomiExport(
        content=build_dianxiaomi_workbook([row]),
        filename=f"combo_dxm_{set_id[:8] or 'set'}.xlsx",
        set_id=set_id,
    )


def _public_images(set_data: dict[str, Any]) -> dict[str, str]:
    """取每张成品图已发布到 COS 的公网直链（跳过本机受管 URL）。"""
    images: dict[str, str] = {}
    entries = set_data.get("image_results_json") or []
    if isinstance(entries, dict):
        entries = [entries]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("role") or "")
        public_url = str(entry.get("public_url") or "").strip()
        if role and public_url:
            images[role] = public_url
    return images


def _build_row(set_data: dict[str, Any], images: dict[str, str]) -> list[Any]:
    text = set_data.get("text_result_json") or {}
    if isinstance(text, dict):
        ai_title = str(text.get("title") or "").strip()
        ai_description = str(text.get("description") or "").strip()
    else:
        ai_title = ""
        ai_description = ""

    title = ai_title or str(set_data.get("name") or "").strip()
    english_title = ai_title or title
    description = ai_description or str(set_data.get("description") or "").strip()
    sku = str(set_data.get("sku") or "").strip()
    sku_display = str(set_data.get("sku_display") or "").strip()

    # 图片列：预览图/产品素材图取主图（缺主图时退回白底图）。
    main = images.get("main") or images.get("white_bg") or ""
    carousel = "\n".join(images.get(role) for role in COMBO_IMAGE_ROLES if images.get(role))

    row: list[Any] = ["" for _ in DXM_COLUMNS]
    values: dict[int, Any] = {
        0: title,
        1: english_title,
        2: description,
        3: sku or f"COMBO-{str(set_data.get('set_id') or '')[:8]}",
        4: "套装",
        5: sku_display or title,
        8: main,
        9: str(set_data.get("declared_price") or ""),
        10: sku,
        11: set_data.get("length_cm") or 0,
        12: set_data.get("width_cm") or 0,
        13: set_data.get("height_cm") or 0,
        14: set_data.get("weight_g") or 0,
        15: str(set_data.get("id_type") or ""),
        16: str(set_data.get("id_code") or ""),
        18: carousel,
        19: main,
        23: set_data.get("suggested_price_usd") or 0,
        24: set_data.get("stock") or 0,
        26: str(set_data.get("category_name") or ""),
        27: str(set_data.get("category_name") or ""),
        28: str(set_data.get("category_path") or ""),
        29: str(set_data.get("category_id") or ""),
        30: "单品",
        31: 1,
        32: "件",
    }
    for index, value in values.items():
        row[index] = value
    if len(row) != 42:
        raise AssertionError("combo_kit Dianxiaomi row must contain exactly 42 cells")
    return row


def _validate_row(row: list[Any]) -> None:
    missing: list[str] = []
    for index, label in _REQUIRED_COLUMNS.items():
        value = str(row[index]).strip() if row[index] is not None else ""
        if index == 8 and row[index] is None:
            continue
        if index in {11, 12, 13, 14}:
            try:
                if float(value or 0) <= 0:
                    missing.append(label)
            except (TypeError, ValueError):
                missing.append(label)
            continue
        if value:
            continue
        if index == 8 or index == 18 or index == 19:
            missing.append(f"{label}（需已发布到 COS 的公网图片直链）")
        elif index == 9:
            missing.append(f"{label}（未录入）")
        elif index == 26:
            missing.append(f"{label}（分类未填写）")
        else:
            missing.append(label)
    if missing:
        raise ComboDianxiaomiExportError(
            "该套装无法导出店小秘，缺少必填项：" + "、".join(missing)
        )


__all__ = [
    "build_combo_dianxiaomi_export",
    "ComboDianxiaomiExport",
    "ComboDianxiaomiExportError",
]
