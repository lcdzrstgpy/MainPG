from wh_local.modules.product_processing.domain.image_slots import (
    DEFAULT_SLOT_IDS,
    apply_slot_overrides,
    base_image_slots,
)


def test_manifest_roles_are_normalized_without_losing_order() -> None:
    result = {
        "image_manifest": [
            {"slot_id": "carousel.hero", "role": "hero", "value": "hero.jpg"},
            {
                "slot_id": "carousel.dimension_background",
                "role": "dimension_background",
                "value": "background.jpg",
            },
        ]
    }

    assert base_image_slots(result) == result["image_manifest"]


def test_legacy_array_becomes_baseline_before_dimension_slot_patch() -> None:
    result = {
        "image_manifest": [
            {"slot_id": slot_id, "value": f"generated-{index}.jpg"}
            for index, slot_id in enumerate(DEFAULT_SLOT_IDS, start=1)
        ]
    }
    overrides = {
        "carousel_images": [
            "manual-1.jpg",
            "manual-2.jpg",
            "manual-3.jpg",
            "manual-4.jpg",
        ],
        "image_slot_overrides": {
            "carousel.dimension_background": {"url": "dimension.jpg"}
        },
    }

    slots = apply_slot_overrides(result, overrides)

    assert [slot["value"] for slot in slots] == [
        "manual-1.jpg",
        "manual-2.jpg",
        "manual-3.jpg",
        "dimension.jpg",
    ]
