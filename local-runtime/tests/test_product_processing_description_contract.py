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
