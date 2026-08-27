from pathlib import Path

import pytest
from pydantic import ValidationError

from wh_local.data_collection.routes import _plugin_physical_evidence, _plugin_product_to_draft
from wh_local.modules.product_processing.api.schemas import PreviewDesiredState, PreviewSaveItem
from wh_local.modules.product_processing.domain.workbooks import _dxm_export_rows
from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository
from wh_local.modules.product_processing.service import ProductProcessingService
from wh_local.modules.product_processing.service import ProductProcessingValidationError


def test_1688_package_measurements_are_bound_to_matching_sku_records() -> None:
    draft = _plugin_product_to_draft(
        {
            "platform": "1688",
            "product_id": "offer-1",
            "product_link": "https://detail.1688.com/offer/1.html",
            "title": "水龙头",
            "variant_combinations": [
                {"source_sku_id": "sku-black", "attributes": {"颜色": "黑色"}, "spec_text": "201不锈钢抽拉式水龙头（黑色）"},
                {"source_sku_id": "sku-gray", "attributes": {"颜色": "枪灰色"}, "spec_text": "201不锈钢抽拉式水龙头（枪灰色）"},
            ],
            "shipping_package_records": [
                {
                    "specification": "201不锈钢抽拉式水龙头（黑色）",
                    "length_cm": 52,
                    "width_cm": 24,
                    "height_cm": 6,
                    "volume_cm3": 7488,
                    "weight_g": 1150,
                },
                {
                    "specification": "不存在的规格",
                    "length_cm": 52,
                    "width_cm": 24,
                    "height_cm": 6,
                    "weight_g": 1200,
                },
            ],
        }
    )

    records = draft["shipping_package_records"]
    matched = records[0]
    assert matched["match_status"] == "matched"
    assert matched["variant_sku_id"] == "sku-black"
    assert matched["length_cm"] == 52
    assert matched["weight_g"] == 1150
    assert records[1]["match_status"] == "unmatched"
    assert "variant_sku_id" not in records[1]
    assert draft["source_variant_records"][0]["shipping_package"] == matched


def test_1688_package_measurement_prefers_existing_variant_key() -> None:
    draft = _plugin_product_to_draft(
        {
            "platform": "1688",
            "product_id": "offer-2",
            "product_link": "https://detail.1688.com/offer/2.html",
            "title": "水龙头",
            "variant_combinations": [
                {"source_sku_id": "sku-black", "attributes": {"颜色": "黑色"}},
                {"source_sku_id": "sku-gray", "attributes": {"颜色": "枪灰色"}},
            ],
            "shipping_package_records": [
                {
                    "variant_key": "sku-gray",
                    "specification": "供应商内部规格名称",
                    "length_cm": 52,
                    "width_cm": 24,
                    "height_cm": 6,
                    "weight_g": 1300,
                }
            ],
        }
    )

    assert draft["shipping_package_records"][0]["variant_key"] == "sku-gray"
    assert draft["shipping_package_records"][0]["match_status"] == "matched"


def test_unmatched_same_specification_rows_get_distinct_stable_variant_keys() -> None:
    draft = _plugin_product_to_draft(
        {
            "platform": "1688",
            "product_id": "offer-3",
            "product_link": "https://detail.1688.com/offer/3.html",
            "title": "水龙头",
            "shipping_package_records": [
                {"specification": "未匹配规格", "length_cm": 52, "width_cm": 24, "height_cm": 6, "weight_g": 1300},
                {"specification": "未匹配规格", "length_cm": 53, "width_cm": 24, "height_cm": 6, "weight_g": 1350},
                {"specification": "缺少重量", "length_cm": 53, "width_cm": 24, "height_cm": 6},
            ],
        }
    )

    records = draft["shipping_package_records"]
    assert len(records) == 2
    assert {record["variant_key"] for record in records} == {"未匹配规格", "未匹配规格#2"}
    assert all(record["match_status"] == "unmatched" for record in records)


def test_export_uses_matched_package_rows_and_per_sku_manual_overrides() -> None:
    row = {
        "optimized_title": "水龙头",
        "description": "desc",
        "skc": "SKC-1",
        "sku": "SKU-1",
        "product_dimensions": {"length_cm": 20, "width_cm": 15, "height_cm": 10, "weight_g": 1000},
        "source_variant_records": [
            {"sku_id": "sku-black", "attributes": {"颜色": "黑色"}},
            {"sku_id": "sku-gray", "attributes": {"颜色": "枪灰色"}},
            {"sku_id": "sku-unmatched", "attributes": {"颜色": "白色"}},
        ],
        "shipping_package_records": [
            {
                "record_key": "sku-black",
                "variant_sku_id": "sku-black",
                "match_status": "matched",
                "length_cm": 52,
                "width_cm": 24,
                "height_cm": 6,
                "weight_g": 1300,
            },
            {
                "record_key": "sku-gray",
                "variant_sku_id": "sku-gray",
                "match_status": "matched",
                "length_cm": 54,
                "width_cm": 25,
                "height_cm": 6,
                "weight_g": 1400,
            },
            {
                "record_key": "not-exported",
                "match_status": "unmatched",
                "length_cm": 99,
                "width_cm": 99,
                "height_cm": 99,
                "weight_g": 9999,
            },
        ],
        "preview_overrides": {
            "shipping_package_records": {
                "sku-black": {"length_cm": 53, "weight_g": 100},
            }
        },
    }

    exported = _dxm_export_rows(row)

    assert [(values[10], values[11:15]) for values in exported] == [
        ("sku-black", [53, 24, 6, 100]),
        ("sku-gray", [54, 25, 6, 1400]),
        ("sku-unmatched", [20, 15, 10, 1000]),
    ]


def test_preview_persists_per_sku_package_overrides_without_mutating_capture(tmp_path: Path) -> None:
    service = ProductProcessingService(
        ProductProcessingRepository(create_database("sqlite:///:memory:")),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    draft, _ = service.create_draft(
        {"source_type": "manual", "title": "水龙头", "product_name": "水龙头", "skc": "SKC-1"},
        workspace_id="local",
    )
    task = service.repository.create_task(
        title="件重尺预检",
        preflight_only=False,
        settings={"target_site": "US", "target_language": "en"},
        drafts=[draft],
        idempotency_key=None,
        workspace_id="local",
    )
    item = task["items"][0]
    source_record = {
        "record_key": "sku-black",
        "variant_sku_id": "sku-black",
        "specification": "水龙头（黑色）",
        "match_status": "matched",
        "source": "1688_product_pack_info",
        "length_cm": 52,
        "width_cm": 24,
        "height_cm": 6,
        "weight_g": 1300,
    }
    service.repository.finish_task(
        task["id"],
        [
            {
                "item_id": item["id"],
                "status": "completed",
                "reason": "",
                "title": "水龙头",
                "image_url": "",
                "result": {
                    "product_draft_id": draft["id"],
                    "optimized_title": "水龙头",
                    "description": "desc",
                    "source_variant_records": [{"sku_id": "sku-black", "attributes": {"颜色": "黑色"}}],
                    "shipping_package_records": [source_record],
                    "product_dimensions": {"length_cm": 20, "width_cm": 15, "height_cm": 10, "weight_g": 1000},
                },
            }
        ],
        output_file="",
        error_report_file="",
        video_manifest_file="",
        workspace_id="local",
    )

    before = service.task_preview(task["id"], workspace_id="local")["items"][0]
    assert before["shipping_package_records"] == [source_record]
    service.save_task_preview(
        task["id"],
        [
            {
                "product_draft_id": draft["id"],
                "expected_preview_revision": before["preview_revision"],
                "overrides": {"shipping_package_records": {"sku-black": {"weight_g": 1350}}},
            }
        ],
        workspace_id="local",
    )

    after = service.task_preview(task["id"], workspace_id="local")["items"][0]
    assert after["shipping_package_records"] == [source_record]
    assert after["overrides"]["shipping_package_records"] == {"sku-black": {"weight_g": 1350}}


@pytest.mark.parametrize("patch", [{"weight_g": float("nan")}, {"weight_g": float("inf")}, {"weight_g": True}, {"weight_g": 1, "specification": "spoof"}])
def test_preview_package_override_schema_rejects_non_finite_and_unknown_fields(patch: dict) -> None:
    with pytest.raises(ValidationError):
        PreviewDesiredState(
            title="",
            description="",
            image_manifest_v2={},
            shipping_package_records={"sku-black": patch},
        )


def test_1688_selected_package_weight_is_not_product_level_physical_evidence(tmp_path: Path) -> None:
    payload = {
        "weight_source": "1688_product_pack_info_selected_sku",
        "weight_text": "重量 1300g",
        "weight_kg": 1.3,
        "employee_action_validation": {
            "weight_source": "1688_product_pack_info_selected_sku",
            "weight_text": "重量 1300g",
            "weight_kg": 1.3,
        },
        "shipping_package_records": [
            {
                "variant_key": "sku-black",
                "specification": "水龙头（黑色）",
                "match_status": "matched",
                "length_cm": 52,
                "width_cm": 24,
                "height_cm": 6,
                "weight_g": 1300,
            }
        ],
    }
    weight_text, package_text = _plugin_physical_evidence(payload)
    service = ProductProcessingService(
        ProductProcessingRepository(create_database("sqlite:///:memory:")),
        ProductProcessingAssets(tmp_path / "assets"),
    )

    assert weight_text is None
    assert package_text is None
    assert service._extract_deterministic_size(payload) is None


def test_preview_save_rejects_unknown_and_unmatched_package_override_keys(tmp_path: Path) -> None:
    service = ProductProcessingService(
        ProductProcessingRepository(create_database("sqlite:///:memory:")),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    draft, _ = service.create_draft(
        {"source_type": "manual", "title": "水龙头", "product_name": "水龙头", "skc": "SKC-1"},
        workspace_id="local",
    )
    task = service.repository.create_task(
        title="覆盖校验",
        preflight_only=False,
        settings={"target_site": "US", "target_language": "en"},
        drafts=[draft],
        idempotency_key=None,
        workspace_id="local",
    )
    item = task["items"][0]
    service.repository.finish_task(
        task["id"],
        [{
            "item_id": item["id"], "status": "completed", "reason": "", "title": "水龙头", "image_url": "",
            "result": {
                "product_draft_id": draft["id"], "optimized_title": "水龙头", "description": "desc",
                "shipping_package_records": [
                    {"variant_key": "sku-matched", "match_status": "matched", "length_cm": 52, "width_cm": 24, "height_cm": 6, "weight_g": 1300},
                    {"variant_key": "sku-unmatched", "match_status": "unmatched", "length_cm": 52, "width_cm": 24, "height_cm": 6, "weight_g": 1300},
                ],
            },
        }],
        output_file="", error_report_file="", video_manifest_file="", workspace_id="local",
    )
    revision = service.task_preview(task["id"], workspace_id="local")["items"][0]["preview_revision"]
    for key in ("sku-unknown", "sku-unmatched"):
        with pytest.raises(ProductProcessingValidationError):
            service.save_task_preview(
                task["id"],
                [{
                    "product_draft_id": draft["id"],
                    "expected_preview_revision": revision,
                    "overrides": {"shipping_package_records": {key: {"weight_g": float("nan")}}},
                }],
                workspace_id="local",
            )
    with pytest.raises(ProductProcessingValidationError):
        service.save_task_preview(
            task["id"],
            [{
                "product_draft_id": draft["id"],
                "expected_preview_revision": revision,
                "overrides": {"shipping_package_records": {"sku-matched": {"weight_g": float("nan")}}},
            }],
            workspace_id="local",
        )
    assert service.repository.get_draft(draft["id"], workspace_id="local")["preview_overrides"] == {}


def test_preview_save_accepts_a_matched_package_embedded_in_variant_result(tmp_path: Path) -> None:
    service = ProductProcessingService(
        ProductProcessingRepository(create_database("sqlite:///:memory:")),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    draft, _ = service.create_draft(
        {"source_type": "manual", "title": "水龙头", "product_name": "水龙头", "skc": "SKC-1"},
        workspace_id="local",
    )
    task = service.repository.create_task(
        title="嵌入式件重尺", preflight_only=False,
        settings={"target_site": "US", "target_language": "en"}, drafts=[draft],
        idempotency_key=None, workspace_id="local",
    )
    item = task["items"][0]
    service.repository.finish_task(
        task["id"],
        [{
            "item_id": item["id"], "status": "completed", "reason": "", "title": "水龙头", "image_url": "",
            "result": {
                "product_draft_id": draft["id"], "optimized_title": "水龙头", "description": "desc",
                "source_variant_records": [{
                    "sku_id": "sku-black", "attributes": {"颜色": "黑色"},
                    "shipping_package": {
                        "variant_key": "sku-black", "match_status": "matched",
                        "length_cm": 52, "width_cm": 24, "height_cm": 6, "weight_g": 1300,
                    },
                }],
            },
        }],
        output_file="", error_report_file="", video_manifest_file="", workspace_id="local",
    )
    revision = service.task_preview(task["id"], workspace_id="local")["items"][0]["preview_revision"]

    request_item = PreviewSaveItem(
        product_draft_id=draft["id"], expected_preview_revision=revision,
        overrides=PreviewDesiredState(
            title="", description="", image_manifest_v2={},
            shipping_package_records={"sku-black": {"weight_g": 1350}},
        ),
    )
    saved = service.save_task_preview(
        task["id"],
        [request_item.model_dump()],
        workspace_id="local",
    )

    assert saved["saved_count"] == 1


def test_1688_package_match_never_uses_substring_or_rebinds_plugin_unmatched() -> None:
    base = {
        "platform": "1688", "product_id": "offer-strict", "product_link": "https://detail.1688.com/offer/strict.html", "title": "水龙头",
        "variant_combinations": [{"source_sku_id": "sku-red", "attributes": {"颜色": "红色"}, "spec_text": "颜色：红色"}],
    }
    deep_red = _plugin_product_to_draft({
        **base,
        "shipping_package_records": [{"specification": "颜色：深红色", "length_cm": 52, "width_cm": 24, "height_cm": 6, "weight_g": 1300}],
    })
    plugin_unmatched = _plugin_product_to_draft({
        **base,
        "shipping_package_records": [{"variant_key": "sku-red", "specification": "颜色：红色", "match_status": "unmatched", "length_cm": 52, "width_cm": 24, "height_cm": 6, "weight_g": 1300}],
    })

    assert deep_red["shipping_package_records"][0]["match_status"] == "unmatched"
    assert "shipping_package" not in deep_red["source_variant_records"][0]
    assert plugin_unmatched["shipping_package_records"][0]["match_status"] == "unmatched"
    assert "shipping_package" not in plugin_unmatched["source_variant_records"][0]


def test_1688_package_rows_reject_non_finite_numbers() -> None:
    draft = _plugin_product_to_draft(
        {
            "platform": "1688", "product_id": "offer-nan", "product_link": "https://detail.1688.com/offer/nan.html", "title": "水龙头",
            "shipping_package_records": [
                {"specification": "颜色：黑色", "length_cm": 52, "width_cm": 24, "height_cm": 6, "weight_g": float("inf")},
                {"specification": "颜色：白色", "length_cm": 52, "width_cm": 24, "height_cm": 6, "weight_g": float("nan")},
            ],
        }
    )

    assert draft["shipping_package_records"] == []


def test_1688_package_match_uses_exact_terminal_parenthesized_sku_values() -> None:
    draft = _plugin_product_to_draft(
        {
            "platform": "1688", "product_id": "offer-screenshot", "product_link": "https://detail.1688.com/offer/screenshot.html", "title": "水龙头",
            "variant_combinations": [{"source_sku_id": "sku-black", "attributes": {"颜色": "黑色"}}],
            "shipping_package_records": [{
                "specification": "201不锈钢抽拉式水龙头（黑色）",
                "length_cm": 52, "width_cm": 24, "height_cm": 6, "weight_g": 1300,
            }],
        }
    )

    record = draft["shipping_package_records"][0]
    assert record["match_status"] == "matched"
    assert record["variant_key"] == "sku-black"
