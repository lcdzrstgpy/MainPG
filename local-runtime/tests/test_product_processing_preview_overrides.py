from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from wh_local.modules.product_processing.domain import sku_availability
from wh_local.modules.product_processing.domain.workbooks import (
    _dxm_export_rows,
    _dxm_single_export_row,
)
from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.repository import (
    PreviewSlotConflict,
    ProductProcessingRepository,
)
from wh_local.modules.product_processing.service import ProductProcessingService


def _service(tmp_path: Path) -> ProductProcessingService:
    return ProductProcessingService(
        ProductProcessingRepository(create_database("sqlite:///:memory:")),
        ProductProcessingAssets(tmp_path / "assets"),
    )


def _base_result() -> dict:
    return {
        "product_draft_id": 1,
        "skc": "SKC-1",
        "sku": "SKU-1",
        "category": "Home & Kitchen",
        "category_path": "Home & Kitchen > Drinkware",
        "category_id": "12345",
        "optimized_title": "Original AI Generated Title",
        "description": "DURABLE MATERIAL - Made of stainless steel.",
        "image_url": "https://src.example.com/main.jpg",
        "source_url": "https://src.example.com/product",
        "source_image_urls": ["https://src.example.com/1.jpg", "https://src.example.com/2.jpg"],
        "source_detail_image_urls": ["https://src.example.com/d1.jpg"],
        "source_attributes": [],
        "source_variant_records": [],
        "variant_value_translations": {},
        "cost": 40.0,
        "declared_price": 160.0,
        "suggested_price": 40.0,
        "product_dimensions": {"length_cm": 20, "width_cm": 15, "height_cm": 10, "weight_g": 300},
        "stock": 50,
        "carousel_image_paths": [
            "https://cos.example.com/c1.jpg",
            "https://cos.example.com/c2.jpg",
            "https://cos.example.com/c3.jpg",
            "https://cos.example.com/c4.jpg",
        ],
        "grid_image_summary_path": "https://cos.example.com/summary.jpg",
        "detail_image_paths": ["https://cos.example.com/detail.jpg"],
        "status": "completed",
        "preflight_only": False,
    }


def _create_task_with_result(service: ProductProcessingService) -> dict:
    draft, _ = service.create_draft(
        {"source_type": "manual", "title": "Source Title", "product_name": "Source Title", "skc": "SKC-1"},
        workspace_id="local",
    )
    result = _base_result()
    result["product_draft_id"] = draft["id"]
    task = service.repository.create_task(
        title="预检测试",
        preflight_only=False,
        settings={"target_site": "US", "target_language": "en"},
        drafts=[draft],
        idempotency_key=None,
        workspace_id="local",
    )
    item = task["items"][0]
    finished = service.repository.finish_task(
        task["id"],
        [
            {
                "item_id": item["id"],
                "status": "completed",
                "reason": "",
                "title": result["optimized_title"],
                "image_url": result["image_url"],
                "result": result,
            }
        ],
        output_file=f"task_{task['id']}/dxm_import_task_{task['id']}.xlsx",
        error_report_file=f"task_{task['id']}/error_report_task_{task['id']}.csv",
        video_manifest_file="",
        workspace_id="local",
    )
    return finished


def test_preview_default_matches_generated_results(tmp_path: Path) -> None:
    service = _service(tmp_path)
    task = _create_task_with_result(service)
    preview = service.task_preview(task["id"], workspace_id="local")
    assert preview["item_count"] == 1
    item = preview["items"][0]
    assert item["title"] == "Original AI Generated Title"
    assert item["source_url"] == "https://src.example.com/product"
    assert item["overrides"] == {}
    assert item["carousel_images"][0] == "https://cos.example.com/c1.jpg"
    assert item["main_image"] == "https://cos.example.com/c1.jpg"
    assert item["core_fields"]["declared_price"] == 160.0
    assert item["core_fields"]["length_cm"] == 20


def test_save_preview_overrides_then_preview_merges(tmp_path: Path) -> None:
    service = _service(tmp_path)
    task = _create_task_with_result(service)
    draft_id = task["items"][0]["product_draft_id"]
    revision = service.task_preview(task["id"], workspace_id="local")["items"][0]["preview_revision"]
    saved = service.save_task_preview(
        task["id"],
        [
            {
                "product_draft_id": draft_id,
                "expected_preview_revision": revision,
                "overrides": {
                    "title": "Manual Edited Title",
                    "carousel_images": ["https://user.example.com/new1.jpg"],
                    "core_fields": {"declared_price": 888, "stock": 7},
                },
            }
        ],
        workspace_id="local",
    )
    assert saved["saved_count"] == 1
    preview = service.task_preview(task["id"], workspace_id="local")
    item = preview["items"][0]
    assert item["title"] == "Manual Edited Title"
    assert item["carousel_images"] == ["https://user.example.com/new1.jpg"]
    assert item["main_image"] == "https://user.example.com/new1.jpg"
    assert item["core_fields"]["declared_price"] == 888
    assert item["core_fields"]["stock"] == 7


def test_clean_preview_overrides_drops_empty_values() -> None:
    cleaned = ProductProcessingService._clean_preview_overrides(
        {
            "title": "",
            "description": "  ",
            "main_image": "https://x.example.com/m.jpg",
            "carousel_images": [],
            "detail_images": ["https://x.example.com/d.jpg", ""],
            "image_slot_overrides": {
                "carousel.dimension_background": {"url": "https://x.example.com/dimension.jpg"},
                "carousel.detail": {"url": ""},
            },
            "core_fields": {"sku": "", "declared_price": None, "stock": 5, "category_path": "  "},
        }
    )
    assert cleaned == {
        "main_image": "https://x.example.com/m.jpg",
        "detail_images": ["https://x.example.com/d.jpg"],
        "image_slot_overrides": {
            "carousel.dimension_background": {"url": "https://x.example.com/dimension.jpg"},
        },
        "core_fields": {"stock": 5},
    }


def test_preview_revision_changes_only_when_overrides_change(tmp_path: Path) -> None:
    service = _service(tmp_path)
    task = _create_task_with_result(service)
    draft_id = task["items"][0]["product_draft_id"]
    overrides = {"title": "Manual Edited Title"}

    first = service.repository.save_draft_preview_overrides(draft_id, overrides)
    unchanged = service.repository.save_draft_preview_overrides(draft_id, overrides)

    assert first is not None
    assert unchanged is not None
    assert first["preview_revision"] == 1
    assert unchanged["preview_revision"] == 1


def test_dimension_accept_preserves_unrelated_preview_edits(tmp_path: Path) -> None:
    service = _service(tmp_path)
    task = _create_task_with_result(service)
    draft_id = task["items"][0]["product_draft_id"]
    service.repository.save_draft_preview_overrides(
        draft_id,
        {"title": "Edited after canvas import"},
    )

    updated = service.repository.apply_dimension_slot_patch(
        draft_id,
        target_slot="carousel.dimension_background",
        patch={"url": "https://user.example.com/dimension.jpg", "asset_id": "asset-1"},
        base_slot_value="https://cos.example.com/c4.jpg",
    )

    assert updated is not None
    assert updated["preview_revision"] == 2
    assert updated["preview_overrides"]["title"] == "Edited after canvas import"
    assert updated["preview_overrides"]["image_slot_overrides"] == {
        "carousel.dimension_background": {
            "url": "https://user.example.com/dimension.jpg",
            "asset_id": "asset-1",
        }
    }


def test_dimension_accept_rejects_newer_target_slot_edit(tmp_path: Path) -> None:
    service = _service(tmp_path)
    task = _create_task_with_result(service)
    draft_id = task["items"][0]["product_draft_id"]
    service.repository.save_draft_preview_overrides(
        draft_id,
        {
            "image_slot_overrides": {
                "carousel.dimension_background": {"url": "https://user.example.com/newer.jpg"}
            }
        },
    )

    import pytest

    with pytest.raises(PreviewSlotConflict):
        service.repository.apply_dimension_slot_patch(
            draft_id,
            target_slot="carousel.dimension_background",
            patch={"url": "https://user.example.com/stale-canvas.jpg"},
            base_slot_value="https://cos.example.com/c4.jpg",
        )


def test_dimension_slot_patch_preserves_other_carousel_and_summary(tmp_path: Path) -> None:
    service = _service(tmp_path)
    task = _create_task_with_result(service)
    draft_id = task["items"][0]["product_draft_id"]
    revision = service.task_preview(task["id"], workspace_id="local")["items"][0]["preview_revision"]
    service.save_task_preview(
        task["id"],
        [
            {
                "product_draft_id": draft_id,
                "expected_preview_revision": revision,
                "overrides": {
                    "image_slot_overrides": {
                        "carousel.dimension_background": {
                            "url": "https://user.example.com/dimension.jpg",
                            "asset_id": "dimension-asset-1",
                        }
                    }
                },
            }
        ],
        workspace_id="local",
    )

    preview = service.task_preview(task["id"], workspace_id="local")
    item = preview["items"][0]
    assert item["carousel_images"] == [
        "https://cos.example.com/c1.jpg",
        "https://cos.example.com/c2.jpg",
        "https://cos.example.com/c3.jpg",
        "https://user.example.com/dimension.jpg",
        "https://cos.example.com/summary.jpg",
    ]
    assert item["image_slots"][3]["slot_id"] == "carousel.dimension_background"

    exported = _dxm_single_export_row({**_base_result(), "preview_overrides": item["overrides"]}, None)
    assert exported[18].splitlines() == [
        "https://cos.example.com/c1.jpg",
        "https://cos.example.com/c2.jpg",
        "https://cos.example.com/c3.jpg",
        "https://user.example.com/dimension.jpg",
        "https://cos.example.com/summary.jpg",
    ]


def test_dimension_slot_patch_uses_legacy_carousel_as_its_baseline() -> None:
    row = _base_result()
    row["image_manifest"] = [
        {"slot_id": "carousel.hero", "role": "hero", "value": "https://cos.example.com/c1.jpg"},
        {"slot_id": "carousel.detail", "role": "detail", "value": "https://cos.example.com/c2.jpg"},
        {"slot_id": "carousel.lifestyle", "role": "lifestyle", "value": "https://cos.example.com/c3.jpg"},
        {
            "slot_id": "carousel.dimension_background",
            "role": "dimension_background",
            "value": "https://cos.example.com/c4.jpg",
        },
    ]
    row["preview_overrides"] = {
        "carousel_images": [
            "https://user.example.com/legacy1.jpg",
            "https://user.example.com/legacy2.jpg",
            "https://user.example.com/legacy3.jpg",
            "https://user.example.com/legacy4.jpg",
        ],
        "image_slot_overrides": {
            "carousel.dimension_background": {"url": "https://user.example.com/dimension.jpg"}
        },
    }

    exported = _dxm_single_export_row(row, None)

    assert exported[18].splitlines() == [
        "https://user.example.com/legacy1.jpg",
        "https://user.example.com/legacy2.jpg",
        "https://user.example.com/legacy3.jpg",
        "https://user.example.com/dimension.jpg",
        "https://cos.example.com/summary.jpg",
    ]


def test_export_final_workbook_applies_overrides(tmp_path: Path) -> None:
    service = _service(tmp_path)
    task = _create_task_with_result(service)
    draft_id = task["items"][0]["product_draft_id"]
    revision = service.task_preview(task["id"], workspace_id="local")["items"][0]["preview_revision"]
    service.save_task_preview(
        task["id"],
        [
            {
                "product_draft_id": draft_id,
                "expected_preview_revision": revision,
                "overrides": {
                    "title": "Manual Edited Title",
                    "main_image": "https://user.example.com/main.jpg",
                    "carousel_images": [
                        "https://user.example.com/c1.jpg",
                        "https://user.example.com/c2.jpg",
                    ],
                    "core_fields": {"declared_price": 999, "length_cm": 30, "weight_g": 500},
                },
            }
        ],
        workspace_id="local",
    )
    exported = service.export_final_workbook(task["id"], workspace_id="local")
    assert exported["row_count"] == 1
    path = service.assets.output_root / f"task_{task['id']}" / exported["file"]
    assert path.is_file()

    from openpyxl import load_workbook

    workbook = load_workbook(path, data_only=True)
    sheet = workbook.active
    headers = [str(cell.value or "").strip() for cell in sheet[1]]
    rows = list(sheet.iter_rows(min_row=2, values_only=True))
    assert len(rows) == 1
    row = dict(zip(headers, rows[0]))
    assert row["*产品标题"] == "Manual Edited Title"
    assert row["*英文标题"] == "Manual Edited Title"
    assert row["预览图"] == "https://user.example.com/main.jpg"
    assert row["*产品素材图"] == "https://user.example.com/main.jpg"
    assert row["*轮播图"] == "https://user.example.com/c1.jpg\nhttps://user.example.com/c2.jpg"
    assert row["*申报价格\n(店铺币种)"] == 999
    assert row["*长（cm）"] == 30
    # 店小秘要求材积重量（长×宽×高÷6）≤ 实际重量。此处体积 30*15*10/6=750g 超过
    # 当前 500g，导出前兜底以店小秘导入为准，将重量抬升到 800g（向上取整到 100）。
    assert row["*重量（g）"] == 800


def test_dxm_single_export_row_defaults_without_overrides() -> None:
    row = _base_result()
    values = _dxm_single_export_row(row, None)
    assert values[0] == "Original AI Generated Title"
    assert values[8] == "https://cos.example.com/c1.jpg"
    assert values[18] == "\n".join(
        [
            "https://cos.example.com/c1.jpg",
            "https://cos.example.com/c2.jpg",
            "https://cos.example.com/c3.jpg",
            "https://cos.example.com/c4.jpg",
            "https://cos.example.com/summary.jpg",
        ]
    )
    # 系统生成的当前重量为 300g；但店小秘要求材积重量（长×宽×高÷6 ≤ 重量），
    # 此处体积 20*15*10/6=500g 超过 300g，导出前兜底以店小秘导入为准，抬升到 500g。
    assert values[14] == 500
    assert values[19] == "https://cos.example.com/c1.jpg"


def test_dxm_export_raises_weight_to_volumetric_then_caps_at_899() -> None:
    row = _base_result()
    row["product_dimensions"] = {
        "length_cm": 100,
        "width_cm": 100,
        "height_cm": 100,
        "weight_g": 301,
    }

    values = _dxm_single_export_row(row, None)

    # 店小秘要求材积重量（长×宽×高÷6）≤ 实际重量，且最终重量不得超过 899g。
    # 体积 100*100*100/6≈166666.67g 远超上限，最终封顶到 899g。
    assert values[14] == 899


# ---- SKU 规格图检出中文：导出侧兜底剔除 ----


def _jpeg(color: str, size: tuple[int, int] = (32, 32)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def _bind_sku_media(
    service: ProductProcessingService,
    draft_id: int,
    sku_id: str,
    color: str,
    size: tuple[int, int] = (32, 32),
) -> str:
    asset = service.media_assets.register_local_asset(
        "local", f"sku-{sku_id}", _jpeg(color, size), "image/jpeg"
    )
    service.media_assets.bind_asset(
        workspace_id="local",
        asset_id=asset["id"],
        product_draft_id=draft_id,
        role="sku",
        sku_id=sku_id,
        variant_label=sku_id,
    )
    return str(asset["id"])


def _draft_with_sku_images(service: ProductProcessingService) -> tuple[int, str]:
    """建一条带两个 SKU 规格图的草稿，返回 ``(draft_id, 含中文那张图的 asset_id)``。"""
    draft, _ = service.create_draft(
        {
            "source_type": "manual",
            "title": "含中文规格图商品",
            "product_name": "含中文规格图商品",
            "skc": "SKC-1",
            "source_variant_records": [
                {"sku_id": "sku-a", "attributes": {"颜色": "白色"}},
                {"sku_id": "sku-b", "attributes": {"颜色": "黑色"}},
            ],
        },
        workspace_id="local",
    )
    draft_id = int(draft["id"])
    # 媒体合同版本 2 才支持「SKU 规格图可用性判断」（create_draft 默认写 1）。
    raw = service.get_draft(draft_id, "local")["raw_payload"]
    service.repository.update_draft(
        draft_id, {"media_contract_version": 2}, raw, workspace_id="local"
    )
    clean_asset = _bind_sku_media(service, draft_id, "sku-a", "green")
    chinese_asset = _bind_sku_media(service, draft_id, "sku-b", "blue")
    assert clean_asset != chinese_asset
    return draft_id, chinese_asset


def _fake_inspect(chinese_asset_id: str):
    """按 asset_id 伪造中文检测结果：只有 ``chinese_asset_id`` 检出中文。"""

    def _inspect(asset_id: str, workspace_id: str) -> dict:
        if asset_id == chinese_asset_id:
            return {"has_chinese": True, "chinese": ["黑色"]}
        return {"has_chinese": False, "chinese": []}

    return _inspect


def _variant_row() -> dict:
    return {
        "source_variant_records": [
            {"sku_id": "sku-a", "attributes": {"颜色": "白色"}},
            {"sku_id": "sku-b", "attributes": {"颜色": "黑色"}},
        ],
        "product_dimensions": {"length_cm": 20, "width_cm": 15, "height_cm": 10, "weight_g": 300},
        # 真实导出行总带有 overrides（服务端兜底剔除在这里并入），保持同一形状。
        "preview_overrides": {},
    }


def test_chinese_variant_keys_ignores_all_sku_chinese() -> None:
    """整条链接全部 SKU 含中文时不做 SKU 级剔除（否则商品会整条消失）。"""
    assert sku_availability.chinese_variant_keys(
        {"chinese_variant_keys": ["sku-a", "sku-b"], "all_sku_chinese": True}
    ) == []
    assert sku_availability.chinese_variant_keys(
        {"chinese_variant_keys": ["sku-b", "sku-a", "sku-b"]}
    ) == ["sku-a", "sku-b"]
    assert sku_availability.chinese_variant_keys(None) == []


def test_merge_excluded_variant_keys_unions_manual_and_chinese() -> None:
    """兜底剔除键与手工排除取并集，且不重复、不把「未设置」写成显式空。"""
    overrides = {"excluded_variant_keys": ["sku-manual"]}
    merged = sku_availability.merge_excluded_variant_keys(overrides, ["sku-b", "sku-manual"])
    assert merged["excluded_variant_keys"] == ["sku-manual", "sku-b"]

    untouched = {"title": "手动改过的标题"}
    assert sku_availability.merge_excluded_variant_keys(untouched, []) is untouched
    assert "excluded_variant_keys" not in untouched


def test_resolve_draft_usable_reports_chinese_keys_only_when_valid() -> None:
    """结论有效性口径：指纹失效 / 未判定时不给出剔除键。"""
    stored = {
        "status": sku_availability.STATUS_UNAVAILABLE,
        "fingerprint": "fp-1",
        "chinese_variant_keys": ["sku-b"],
    }
    valid = sku_availability.resolve_draft_usable(stored, current_fingerprint="fp-1")
    assert valid["judged"] is True
    assert valid["usable_source"] is False
    assert valid["chinese_variant_keys"] == ["sku-b"]

    stale = sku_availability.resolve_draft_usable(stored, current_fingerprint="fp-2")
    assert stale["judged"] is False
    assert stale["chinese_variant_keys"] == []
    assert stale["reason"] == "fingerprint_stale"


def test_is_square_view_tolerates_tiny_rounding_but_flags_real_ratio() -> None:
    """宽高比判定：方图（含 1% 内舍入）通过，明显长方/扁图判非方；尺寸读不出来不误报。"""
    assert sku_availability.is_square_view({"width": 1000, "height": 1000}) is True
    assert sku_availability.is_square_view({"width": 1000, "height": 1005}) is True
    assert sku_availability.is_square_view({"width": 1000, "height": 1200}) is False
    assert sku_availability.is_square_view({"width": 1200, "height": 900}) is False
    # 宽高缺失 / 非法时无从判断，按方形处理以免整批链接被误回退主图。
    assert sku_availability.is_square_view({}) is True
    assert sku_availability.is_square_view({"width": 0, "height": 0}) is True
    assert sku_availability.is_square_view({"width": "abc", "height": 100}) is True


def test_resolve_draft_usable_reports_not_square_keys_only_when_valid() -> None:
    """非 1:1 键随结论有效性透出：指纹失效 / 未判定时不给出。"""
    stored = {
        "status": sku_availability.STATUS_UNAVAILABLE,
        "fingerprint": "fp-1",
        "not_square_variant_keys": ["sku-b", "sku-a", "sku-b"],
    }
    valid = sku_availability.resolve_draft_usable(stored, current_fingerprint="fp-1")
    assert valid["reason"] == "unavailable"
    assert valid["not_square_variant_keys"] == ["sku-a", "sku-b"]

    stale = sku_availability.resolve_draft_usable(stored, current_fingerprint="fp-2")
    assert stale["not_square_variant_keys"] == []

    never = sku_availability.resolve_draft_usable(None, current_fingerprint=None)
    assert never["not_square_variant_keys"] == []


def test_check_draft_sku_availability_flags_not_square_sku(tmp_path: Path) -> None:
    """任一规格原图非 1:1 即判不可用（店小秘「变种预览图」列强制方图），并跳过 OCR。"""
    service = _service(tmp_path)
    draft, _ = service.create_draft(
        {
            "source_type": "manual",
            "title": "非方图规格商品",
            "product_name": "非方图规格商品",
            "skc": "SKC-NS",
            "source_variant_records": [
                {"sku_id": "sku-a", "attributes": {"颜色": "白色"}},
                {"sku_id": "sku-b", "attributes": {"颜色": "黑色"}},
            ],
        },
        workspace_id="local",
    )
    draft_id = int(draft["id"])
    raw = service.get_draft(draft_id, "local")["raw_payload"]
    service.repository.update_draft(
        draft_id, {"media_contract_version": 2}, raw, workspace_id="local"
    )
    _bind_sku_media(service, draft_id, "sku-a", "green")
    _bind_sku_media(service, draft_id, "sku-b", "blue", size=(40, 32))
    # 命中非 1:1 就不该再去 OCR：调用即失败，确保这条链接根本没进 OCR 任务。
    service._inspect_sku_asset = lambda asset_id, workspace_id: (_ for _ in ()).throw(
        AssertionError("非 1:1 的链接不应触发 OCR")
    )

    plan = service.check_draft_sku_availability([draft_id], workspace_id="local")["results"][0]
    assert plan["status"] == sku_availability.STATUS_UNAVAILABLE
    assert plan["reason"] == "not_square_image"
    assert plan["not_square_variant_keys"] == ["sku-b"]

    row = _variant_row()
    service.mark_row_sku_availability(row, draft_id, workspace_id="local")
    assert row["sku_source_usable"] is False
    # 非 1:1 不剔除任何 SKU（商品照常导出，只是整条回退商品主图）。
    assert row["preview_overrides"] == {}
    assert [values[10] for values in _dxm_export_rows(row)] == ["sku-a", "sku-b"]


def test_mark_row_sku_availability_excludes_chinese_detected_sku(tmp_path: Path) -> None:
    """服务端兜底：检出中文的 SKU 变种并入剔除键，导出行里不再出现该 SKU。"""
    service = _service(tmp_path)
    draft_id, chinese_asset = _draft_with_sku_images(service)
    service._inspect_sku_asset = _fake_inspect(chinese_asset)

    plan = service.check_draft_sku_availability([draft_id], workspace_id="local")["results"][0]
    assert plan["status"] == sku_availability.STATUS_UNAVAILABLE
    assert plan["chinese_variant_keys"] == ["sku-b"]
    assert plan["all_sku_chinese"] is False

    row = _variant_row()
    service.mark_row_sku_availability(row, draft_id, workspace_id="local")

    assert row["preview_overrides"]["excluded_variant_keys"] == ["sku-b"]
    assert row["sku_source_usable"] is False
    assert [values[10] for values in _dxm_export_rows(row)] == ["sku-a"]


def test_mark_row_sku_availability_ignores_stale_conclusion(tmp_path: Path) -> None:
    """结论指纹与当前图集不符（图被换过）时不剔除任何 SKU，避免误删导出行。"""
    service = _service(tmp_path)
    draft_id, _chinese_asset = _draft_with_sku_images(service)
    service.repository.save_draft_sku_availability(
        draft_id,
        {
            "status": sku_availability.STATUS_UNAVAILABLE,
            "fingerprint": "stale-fingerprint",
            "chinese_variant_keys": ["sku-b"],
            "all_sku_chinese": False,
        },
        workspace_id="local",
    )

    row = _variant_row()
    service.mark_row_sku_availability(row, draft_id, workspace_id="local")

    assert row["preview_overrides"] == {}
    assert [values[10] for values in _dxm_export_rows(row)] == ["sku-a", "sku-b"]


def test_mark_row_sku_availability_keeps_rows_when_all_sku_chinese(tmp_path: Path) -> None:
    """整条链接的 SKU 图都含中文：不剔除，按现状回退商品主图并保留商品。"""
    service = _service(tmp_path)
    draft_id, chinese_asset = _draft_with_sku_images(service)
    service._inspect_sku_asset = lambda asset_id, workspace_id: {
        "has_chinese": True,
        "chinese": ["黑色"],
    }
    assert chinese_asset

    plan = service.check_draft_sku_availability([draft_id], workspace_id="local")["results"][0]
    assert plan["all_sku_chinese"] is True
    assert plan["chinese_variant_keys"] == ["sku-a", "sku-b"]

    row = _variant_row()
    service.mark_row_sku_availability(row, draft_id, workspace_id="local")

    assert row["preview_overrides"] == {}
    assert [values[10] for values in _dxm_export_rows(row)] == ["sku-a", "sku-b"]


def test_export_final_workbook_drops_chinese_detected_sku(tmp_path: Path) -> None:
    """端到端：最终版导出表格里不出现含中文规格图的 SKU，其余变种照常导出。"""
    service = _service(tmp_path)
    draft_id, chinese_asset = _draft_with_sku_images(service)
    service._inspect_sku_asset = _fake_inspect(chinese_asset)
    service.check_draft_sku_availability([draft_id], workspace_id="local")

    result = _base_result()
    result["product_draft_id"] = draft_id
    result["source_variant_records"] = [
        {
            "sku_id": "sku-a",
            "attributes": {"颜色": "白色"},
            "image_url": "https://src.example.com/a.jpg",
        },
        {
            "sku_id": "sku-b",
            "attributes": {"颜色": "黑色"},
            "image_url": "https://src.example.com/b.jpg",
        },
    ]
    task = service.repository.create_task(
        title="导出兜底剔除",
        preflight_only=False,
        settings={"target_site": "US", "target_language": "en"},
        drafts=[service.get_draft(draft_id, "local")],
        idempotency_key=None,
        workspace_id="local",
    )
    item = task["items"][0]
    service.repository.finish_task(
        task["id"],
        [
            {
                "item_id": item["id"],
                "status": "completed",
                "reason": "",
                "title": result["optimized_title"],
                "image_url": result["image_url"],
                "result": result,
            }
        ],
        output_file=f"task_{task['id']}/dxm_import_task_{task['id']}.xlsx",
        error_report_file=f"task_{task['id']}/error_report_task_{task['id']}.csv",
        video_manifest_file="",
        workspace_id="local",
    )

    exported = service.export_final_workbook(task["id"], workspace_id="local")

    assert exported["row_count"] == 1
    from openpyxl import load_workbook

    path = service.assets.output_root / f"task_{task['id']}" / exported["file"]
    workbook = load_workbook(path, data_only=True)
    sheet = workbook.active
    headers = [str(cell.value or "").strip() for cell in sheet[1]]
    rows = [dict(zip(headers, values)) for values in sheet.iter_rows(min_row=2, values_only=True)]
    assert [row["SKU货号"] for row in rows] == ["sku-a"]
    assert "sku-b" not in {row["SKU货号"] for row in rows}
