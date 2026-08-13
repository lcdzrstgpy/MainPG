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


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("\n".join(VALID.splitlines()[:4]), "exactly five"),
        (VALID + "\n- EXTRA SELLING DETAIL: Another unsupported line is not allowed in the listing output.", "exactly five"),
        (VALID.replace("VERIFIED SOLID BUILD", "Verified Solid Build"), "ALL-CAPS"),
        (VALID.replace("regular tabletop sessions", "日常桌面游戏"), "English"),
        (VALID.replace(VALID.splitlines()[4], VALID.splitlines()[0]), "distinct"),
        (
            "\n".join(
                [
                    "- FIRST FACT: Small item.",
                    "- SECOND FACT: Simple use.",
                    "- THIRD FACT: Neat shape.",
                    "- FOURTH FACT: Easy handling.",
                    "- FIFTH FACT: Clear finish.",
                ]
            ),
            "80-150",
        ),
        (VALID.replace("The confirmed construction", "Source information preserved for operator review. The confirmed construction"), "internal fallback"),
    ],
)
def test_invalid_descriptions_are_rejected(value: str, message: str) -> None:
    with pytest.raises(DescriptionContractError, match=message):
        normalize_five_point_description(value)
