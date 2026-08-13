from wh_local.modules.product_processing.domain.physical_dimensions import (
    PhysicalDimensions,
    extract_physical_dimensions,
    prefill_physical_dimensions,
)


def test_extracts_explicit_product_size_only() -> None:
    result = extract_physical_dimensions(
        {"source_attributes": {"产品尺寸（长×宽×高）": "30×20×10cm"}}
    )

    assert result.model_dump(mode="json") == {
        "length": {
            "value_cm": 30.0,
            "provenance": "source_confirmed",
            "evidence_ref": "source_attributes.产品尺寸（长×宽×高）",
        },
        "width": {
            "value_cm": 20.0,
            "provenance": "source_confirmed",
            "evidence_ref": "source_attributes.产品尺寸（长×宽×高）",
        },
        "height": {
            "value_cm": 10.0,
            "provenance": "source_confirmed",
            "evidence_ref": "source_attributes.产品尺寸（长×宽×高）",
        },
        "conflict": False,
    }
    assert result.drawable is True


def test_rejects_package_dimensions_for_canvas() -> None:
    result = extract_physical_dimensions(
        {"source_attributes": {"包装尺寸（长×宽×高）": "40×30×20cm"}}
    )

    assert result.length.provenance == "package_estimate"
    assert result.width.provenance == "package_estimate"
    assert result.height.provenance == "package_estimate"
    assert result.drawable is False


def test_conflicting_product_sizes_remain_unconfirmed() -> None:
    result = extract_physical_dimensions(
        {
            "source_attributes": {
                "产品长度": "30cm",
                "成品长度": "31cm",
                "商品宽度": "20cm",
            }
        }
    )

    assert result.conflict is True
    assert result.length.provenance == "unconfirmed"
    assert result.width.provenance == "source_confirmed"
    assert result.drawable_fields == ("width",)


def test_converts_explicit_millimeters_to_centimeters() -> None:
    result = extract_physical_dimensions(
        {
            "source_attributes": {
                "item size (height x length x width)": "100 x 300 x 200 mm"
            }
        }
    )

    assert result.length.value_cm == 30.0
    assert result.width.value_cm == 20.0
    assert result.height.value_cm == 10.0


def test_ambiguous_axis_or_missing_unit_stays_unconfirmed() -> None:
    ambiguous_axis = extract_physical_dimensions(
        {"source_attributes": {"产品尺寸": "30×20×10cm"}}
    )
    missing_unit = extract_physical_dimensions(
        {"source_attributes": {"产品尺寸（长×宽×高）": "30×20×10"}}
    )

    assert ambiguous_axis.drawable_fields == ()
    assert missing_unit.drawable_fields == ()
    assert ambiguous_axis.length.provenance == "unconfirmed"
    assert missing_unit.length.provenance == "unconfirmed"


def test_single_confirmed_field_is_drawable_without_other_dimensions() -> None:
    result = extract_physical_dimensions(
        {"source_attributes": {"商品高度": "85 mm"}}
    )

    assert result.height.value_cm == 8.5
    assert result.drawable_fields == ("height",)
    assert result.drawable is True


def test_product_fields_win_independently_over_mixed_shipping_fields() -> None:
    result = extract_physical_dimensions(
        {
            "source_attributes": {
                "shipping height": "10 cm",
                "包裹长度": "40 cm",
                "产品宽度": "20 cm",
                "商品长度": "30 cm",
            }
        }
    )

    assert result.length.value_cm == 30.0
    assert result.length.provenance == "source_confirmed"
    assert result.width.value_cm == 20.0
    assert result.width.provenance == "source_confirmed"
    assert result.height.value_cm == 10.0
    assert result.height.provenance == "package_estimate"
    assert result.drawable_fields == ("length", "width")


def test_manual_confirmed_dimensions_are_drawable() -> None:
    result = PhysicalDimensions.model_validate(
        {
            name: {"value_cm": value, "provenance": "manual_confirmed"}
            for name, value in (("length", 10), ("width", 8), ("height", 4))
        }
    )

    assert result.drawable is True


def test_processing_table_dimensions_prefill_without_becoming_trusted() -> None:
    result = prefill_physical_dimensions(
        {
            "physical_dimensions": {},
            "product_dimensions": {
                "length_cm": 30,
                "width_cm": 20,
                "height_cm": 10,
                "source": "ai_estimated",
            },
        }
    )

    assert (result.length.value_cm, result.width.value_cm, result.height.value_cm) == (30, 20, 10)
    assert result.length.provenance == "package_estimate"
    assert result.length.evidence_ref == "product_dimensions.length_cm:ai_estimated"
    assert result.drawable is False


def test_explicit_product_evidence_wins_over_processing_estimate_per_axis() -> None:
    result = prefill_physical_dimensions(
        {
            "physical_dimensions": {
                "length": {
                    "value_cm": 12,
                    "provenance": "source_confirmed",
                    "evidence_ref": "source.length",
                }
            },
            "product_dimensions": {"length_cm": 99, "width_cm": 8},
        }
    )

    assert result.length.value_cm == 12
    assert result.length.provenance == "source_confirmed"
    assert result.width.value_cm == 8
    assert result.width.provenance == "package_estimate"


def test_manually_cleared_dimension_is_not_silently_refilled() -> None:
    result = prefill_physical_dimensions(
        {
            "physical_dimensions": {
                "length": {
                    "value_cm": None,
                    "provenance": "unconfirmed",
                    "evidence_ref": "manual",
                }
            },
            "product_dimensions": {"length_cm": 30},
        }
    )

    assert result.length.value_cm is None
    assert result.length.evidence_ref == "manual"
