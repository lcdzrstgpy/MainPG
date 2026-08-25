from __future__ import annotations

import pytest

from wh_local.modules.product_processing.domain.description_contract import (
    DescriptionContractError,
    normalize_five_point_description,
)


VALID = """• VERIFIED SOLID BUILD: The confirmed construction keeps the playing surface stable during regular tabletop sessions and repeated handling at home.
2. COMFORTABLE GAME USE - The broad square surface supports tile placement, sorting, and relaxed game nights without crowding the active play area.
- PRACTICAL TABLE COVERAGE: The listed square format gives players a defined space for arranging tiles, racks, and supported game accessories.
4) SIMPLE DAILY HANDLING: The flexible mat format can be placed on a suitable table and moved away after play when the surface is cleared.
* DISTINCTIVE BORDER DETAIL - The green center and orange geometric border create an easy-to-recognize appearance while preserving the verified product design."""


def test_valid_five_points_are_normalized_without_rewriting_content() -> None:
    normalized = normalize_five_point_description(VALID)
    assert normalized.count("\n") == 4
    assert normalized.startswith("- VERIFIED SOLID BUILD: ")
    assert all(line.startswith("- ") for line in normalized.splitlines())


def test_four_points_are_rejected_even_with_mixed_case_headings() -> None:
    four = "\n".join(VALID.splitlines()[:4]).replace("VERIFIED SOLID BUILD", "Verified Solid Build")
    with pytest.raises(DescriptionContractError, match="exactly five"):
        normalize_five_point_description(four)


def test_three_points_are_rejected() -> None:
    with pytest.raises(DescriptionContractError, match="exactly five"):
        normalize_five_point_description("\n".join(VALID.splitlines()[:3]))


def test_four_plain_points_are_rejected() -> None:
    plain = "\n".join(
        [
            "First selling point is about the sturdy construction used for regular sessions.",
            "Second point covers the comfortable square surface for tile placement at home.",
            "Third point mentions the practical table coverage for relaxed game nights.",
            "Fourth point describes simple daily handling after the play area is cleared.",
        ]
    )
    with pytest.raises(DescriptionContractError, match="exactly five"):
        normalize_five_point_description(plain)


def test_two_points_are_rejected() -> None:
    two = "\n".join(VALID.splitlines()[:2])
    with pytest.raises(DescriptionContractError, match="exactly five"):
        normalize_five_point_description(two)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("", "at least one usable"),
        (VALID.replace("regular tabletop sessions", "日常桌面游戏"), "English"),
        (VALID.replace(VALID.splitlines()[4], VALID.splitlines()[0]), "distinct"),
        (VALID.replace("The confirmed construction", "Source information preserved for operator review. The confirmed construction"), "internal fallback"),
    ],
)
def test_invalid_descriptions_are_rejected(value: str, message: str) -> None:
    with pytest.raises(DescriptionContractError, match=message):
        normalize_five_point_description(value)


INLINE_COLON = (
    "TWO-PIECE OUTFIT: This set includes a puff sleeve top and plaid lace-trimmed skirt for a complete look. "
    "STYLISH DESIGN: Features delicate lace trim and ruffled sleeves for a charming, cohesive aesthetic. "
    "VERSATILE USE: Perfect for daily wear, dates, friend gatherings, and Double Seventh Festival events. "
    "MULTIPLE SIZES: Available in various size options to fit different body types comfortably. "
    "EASY TO STYLE: This matching set pairs well with accessories for a personalized, trendy look."
)
INLINE_DASH = (
    "OCCASION READY - This two-piece outfit works for daily commute, date nights, friend outings, and Double Seventh Festival gatherings. "
    "STYLISH DESIGN - The set features a lace-trimmed puff sleeve top and plaid skirt with ruffled edges for a trendy look. "
    "FLATTERING FIT - This outfit is crafted to suit different body types for a comfortable and flattering wear. "
    "VERSATILE USE - The matching design transitions smoothly from casual outings to festive celebrations. "
    "THOUGHTFUL GIFT - This stylish two-piece set makes a great gift for special occasions like the Double Seventh Festival."
)


@pytest.mark.parametrize("value", [INLINE_COLON, INLINE_DASH])
def test_inline_single_line_five_points_are_split_and_accepted(value: str) -> None:
    """模型偶尔把 5 条卖点写成一整段（'LABEL: body. ...'），应拆行后通过五点校验。"""
    normalized = normalize_five_point_description(value)
    assert normalized.count("\n") == 4
    assert all(line.startswith("- ") for line in normalized.splitlines())


def test_inline_plain_paragraph_without_points_is_still_rejected() -> None:
    plain = (
        "This charming two-piece outfit is perfect for daily wear and special occasions. "
        "The set includes a lace-trimmed top and a plaid skirt with delicate ruffled details. "
        "Made from soft fabrics that are comfortable and breathable for all-day wear."
    )
    with pytest.raises(DescriptionContractError, match="exactly five"):
        normalize_five_point_description(plain)
