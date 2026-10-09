from wh_local.modules.product_processing.service import ProductProcessingService


def _result(product_dimensions: dict, shipping_records: list[dict]) -> dict:
    return {
        "product_dimensions": product_dimensions,
        "shipping_package_records": shipping_records,
        "source_variant_records": [],
    }


def test_preview_package_record_wins_over_ai_dimensions() -> None:
    service = object.__new__(ProductProcessingService)
    item = {"id": 1, "product_draft_id": 1, "skc": "SKU-1", "status": "completed"}
    # 商品本体尺寸来自 AI 预估
    result = _result(
        {"length_cm": 50, "width_cm": 35, "height_cm": 25, "weight_g": 1500, "source": "combined_ai_estimated"},
        [
            {
                "variant_key": "SKU-1",
                "match_status": "matched",
                "selected": True,
                "length_cm": 35,
                "width_cm": 15,
                "height_cm": 40,
                "weight_g": 7,
            }
        ],
    )
    preview = service._preview_item(item, result, {})
    core = preview["core_fields"]
    provenance = preview["dimension_provenance"]
    # 件重尺真实值优先于 AI 预估
    assert core["length_cm"] == 35
    assert core["width_cm"] == 15
    assert core["height_cm"] == 40
    assert core["weight_g"] == 7
    assert provenance["length_cm"] == "source"
    assert provenance["weight_g"] == "source"


def test_preview_falls_back_to_ai_when_no_package_record() -> None:
    service = object.__new__(ProductProcessingService)
    item = {"id": 1, "product_draft_id": 1, "skc": "SKU-1", "status": "completed"}
    result = _result(
        {"length_cm": 50, "width_cm": 35, "height_cm": 25, "weight_g": 1500, "source": "combined_ai_estimated"},
        [],
    )
    preview = service._preview_item(item, result, {})
    core = preview["core_fields"]
    provenance = preview["dimension_provenance"]
    assert core["weight_g"] == 1500
    assert provenance["weight_g"] == "ai"


def test_preview_manual_core_field_still_wins() -> None:
    service = object.__new__(ProductProcessingService)
    item = {"id": 1, "product_draft_id": 1, "skc": "SKU-1", "status": "completed"}
    result = _result(
        {"length_cm": 50, "weight_g": 1500, "source": "combined_ai_estimated"},
        [
            {
                "variant_key": "SKU-1",
                "match_status": "matched",
                "selected": True,
                "length_cm": 35,
                "weight_g": 7,
            }
        ],
    )
    saved = {"core_fields": {"weight_g": 999}}
    preview = service._preview_item(item, result, saved)
    core = preview["core_fields"]
    provenance = preview["dimension_provenance"]
    # 用户手动覆盖的 core_fields 优先级最高
    assert core["weight_g"] == 999
    assert provenance["weight_g"] == "manual"
    # 未手动覆盖的字段仍取自件重尺真实值
    assert core["length_cm"] == 35
    assert provenance["length_cm"] == "source"
