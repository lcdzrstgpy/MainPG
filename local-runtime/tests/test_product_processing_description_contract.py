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


def test_presentation_only_heading_variants_are_canonicalized_locally() -> None:
    lines = []
    for index, line in enumerate(normalize_five_point_description(VALID).splitlines(), start=1):
        content = line.removeprefix("- ")
        heading, body = content.split(":", maxsplit=1)
        lines.append(f"{index}. **{heading.title()}**：{body.strip()}")

    normalized = normalize_five_point_description("\n".join(lines))

    assert normalized == normalize_five_point_description(VALID)


def test_partial_points_and_short_copy_are_preserved_instead_of_rejected() -> None:
    partial = """- Compact Shape: Easy to place on a desk.
- Daily Use: Suitable for ordinary routines."""

    normalized = normalize_five_point_description(partial)

    assert normalized.splitlines() == [
        "- COMPACT SHAPE: Easy to place on a desk.",
        "- DAILY USE: Suitable for ordinary routines.",
    ]


def test_plain_sentences_are_kept_and_extra_points_are_capped_at_five() -> None:
    value = "\n".join(
        [
            "A compact shape fits easily into ordinary storage spaces.",
            "- VISIBLE FINISH: The smooth blue surface matches the supplied image.",
            "- SIMPLE HANDLING: The lightweight form is easy to move.",
            "- DAILY USE: Designed for ordinary everyday routines.",
            "- NEAT DISPLAY: The clean outline keeps the product easy to recognize.",
            "- EXTRA DETAIL: This sixth line should not be exported.",
        ]
    )

    normalized = normalize_five_point_description(value)

    assert len(normalized.splitlines()) == 5
    assert normalized.startswith("- PRODUCT DETAIL 1: A compact shape")
    assert "sixth line" not in normalized


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (VALID.replace("regular tabletop sessions", "日常桌面游戏"), "English"),
        (VALID.replace("The confirmed construction", "Source information preserved for operator review. The confirmed construction"), "internal fallback"),
        ("", "at least one usable"),
    ],
)
def test_invalid_descriptions_are_rejected(value: str, message: str) -> None:
    with pytest.raises(DescriptionContractError, match=message):
        normalize_five_point_description(value)
