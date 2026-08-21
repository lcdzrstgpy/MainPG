from __future__ import annotations

from .contracts import BusinessFields


PATTERN_PROMPT_VERSION = "v1"
LISTING_IMAGE_ROLES = ("hero", "detail_a", "detail_b", "lifestyle")
_STYLE_MOTIFS = (
    "asymmetric botanical silhouettes",
    "abstract topographic contours",
    "scattered celestial symbols",
    "geometric folk ornaments",
    "mid-century organic shapes",
    "coastal wave geometry",
    "desert flora linework",
    "architectural arches and steps",
    "playful fruit and leaf forms",
    "minimal wildlife silhouettes",
    "interlocking ribbon shapes",
    "hand-cut paper forms",
    "mosaic-inspired fragments",
    "retro travel emblems without text",
    "micro floral sprigs",
    "bold tropical foliage",
    "constellation-like dot networks",
    "ceramic glaze-inspired marks",
    "woven stripe abstractions",
    "freeform brush symbols",
)
_STYLE_COMPOSITIONS = (
    "one off-center focal composition",
    "balanced all-over repeat",
    "large cropped edge-to-edge motif",
    "orderly modular grid",
    "diagonal movement with open negative space",
    "radial composition without a sunburst",
    "vertical cascading arrangement",
    "horizontal banded arrangement",
    "sparse floating placement",
    "layered foreground-and-background motif",
)
_STYLE_PALETTES = (
    "two-color high contrast",
    "muted earth tones with one dark accent",
    "cool analogous colors",
    "warm analogous colors",
    "deep jewel tones",
    "soft mineral pastels",
    "black plus one saturated accent",
    "desaturated heritage palette",
    "bright complementary colors",
    "monochrome tonal variation",
    "natural greens and clay neutrals",
    "navy, cream, and restrained warm accents",
)
_STYLE_RENDERINGS = (
    "clean vector-like flat shapes",
    "rough screen-print edges",
    "fine ink linework",
    "block-print texture",
    "cut-paper texture",
    "dry-brush marks",
    "stitched applique appearance",
    "subtle grain with crisp silhouettes",
    "watercolor-like edges without gradients",
    "bold marker-like strokes",
)
_STYLE_DENSITIES = (
    "very sparse with generous negative space",
    "sparse",
    "medium density",
    "dense but readable",
    "one oversized motif with minimal supporting marks",
    "small-scale repeat with clear rhythm",
    "mixed scale with one dominant and several supporting forms",
)
PATTERN_PROMPT_V1 = """Create one square 2x2 contact sheet containing exactly four distinct, production-ready POD surface patterns.
Each quadrant must be a seamless flat design tile shown straight-on: no product mockup, no perspective, no border, no caption, no letters, no numbers, no logo, and no watermark.
Keep the four designs visibly different while following the same creative brief. Preserve a clean center split so the contact sheet can be divided into four equal tiles.
Use only the supplied business facts. Do not invent certifications, claims, brands, dimensions, or readable text."""


def build_pattern_prompt(fields: BusinessFields, creative_prompt: str) -> str:
    values = fields.model_dump()
    lines = [PATTERN_PROMPT_V1, "", "Business brief:"]
    for key, value in values.items():
        if isinstance(value, list):
            rendered = ", ".join(item.strip() for item in value if item.strip())
        else:
            rendered = str(value).strip()
        if rendered:
            lines.append(f"- {key}: {rendered}")
    if creative_prompt.strip():
        lines.extend(("", f"Creative direction: {creative_prompt.strip()}"))
    return "\n".join(lines)


def build_direct_listing_prompt(fields: BusinessFields, creative_prompt: str) -> str:
    """Prompt for one product-locked four-panel listing contact sheet."""
    parts = [
        "Create one square 2x2 ecommerce contact sheet with exactly four equal panels.",
        "Treat the supplied template only as a structural product reference for product geometry, construction, proportions, material, scale, and printable surface location.",
        "Do not copy, trace, preserve, or extend the template's existing artwork, decoration, product color, background, room, furniture, surface, lighting, shadows, camera framing, or scene.",
        "The template is not a background plate and must not appear as the base image. Invent a completely new surface design and a fresh commercially suitable product color and presentation.",
        "Keep the same exact product across all four panels: identical structure, material, proportions, base color, newly invented artwork, artwork scale, and artwork placement.",
        "Do not invent another product, extra accessories, text, captions, logos, labels, watermarks, collages, or borders. Keep the four-panel divider clean and centered.",
        "Panel order is fixed and every panel must show the same exact product with the same unchanged newly invented artwork.",
        "Panel 1 — HERO IMAGE (top-left): show one complete product as the marketplace primary image. Use a newly generated clean neutral ecommerce background, keep the whole product clearly visible, make it fill most of the panel, and show the full design sharply.",
        "Panel 2 — DETAIL IMAGE A (top-right): show a tight high-resolution close-up of the newly invented surface artwork on this same product. Make color, edges, print or material texture, and manufacturing detail easy to inspect; do not alter the artwork or its placement.",
        "Panel 3 — DETAIL IMAGE B (bottom-left): show a different close product detail or three-quarter product view. Choose a product-appropriate structural or material detail, while keeping the artwork visibly identical to Panel 1 and Panel 2.",
        "Panel 4 — LIFESTYLE IMAGE (bottom-right): show the same complete product in one newly generated, natural, commercially useful lifestyle setting. Keep the full product and unchanged artwork visible; do not reuse the template background or add another product.",
        f"Product name: {fields.product_name or 'POD product'}.",
    ]
    for label, value in (
        ("Product category", fields.product_category),
        ("Target market", fields.target_market),
        ("Target audience", fields.target_audience),
        ("Core selling points", ", ".join(fields.core_selling_points)),
        ("Design theme", fields.design_theme),
        ("Style keywords", ", ".join(fields.style_keywords)),
        ("Color preferences", ", ".join(fields.color_preferences)),
        ("Excluded elements", ", ".join(fields.excluded_elements)),
        ("Creative direction", creative_prompt.strip()),
    ):
        if value:
            parts.append(f"{label}: {value}.")
    return "\n".join(parts)


def build_style_listing_prompt(base_prompt: str, *, style_index: int, attempt: int) -> str:
    """Append one deterministic, batch-diverse creative recipe to a listing prompt."""
    if style_index < 1:
        raise ValueError("style_index must be positive")
    if attempt not in {1, 2}:
        raise ValueError("attempt must be 1 or 2")
    offset = style_index - 1
    motif = _STYLE_MOTIFS[offset % len(_STYLE_MOTIFS)]
    composition = _STYLE_COMPOSITIONS[(offset * 3) % len(_STYLE_COMPOSITIONS)]
    palette = _STYLE_PALETTES[(offset * 7) % len(_STYLE_PALETTES)]
    rendering = _STYLE_RENDERINGS[(offset * 7) % len(_STYLE_RENDERINGS)]
    density = _STYLE_DENSITIES[(offset * 3) % len(_STYLE_DENSITIES)]
    signature = (
        f"STYLE-{style_index:03d} | motif={motif} | composition={composition} | "
        f"palette={palette} | rendering={rendering} | density={density}"
    )
    rules = [
        base_prompt.rstrip(),
        "",
        "STYLE-SPECIFIC DIVERSITY CONTRACT:",
        f"Style creative signature: {signature}",
        f"Create this as style {style_index}. Its surface artwork must be visibly different from every other style in this batch.",
        "Use the motif, composition, palette, rendering, and density recipe above as binding art direction. Do not fall back to the template artwork or a generic design used for another style.",
        "All four panels in this one contact sheet must nevertheless use exactly this one new design; do not create four design variants inside the sheet.",
    ]
    if attempt == 2:
        rules.extend((
            "",
            "RETRY ATTEMPT 2 OF 2:",
            "The first result was invalid, failed, or too similar to another style; reinvent the surface artwork from scratch while keeping this style's assigned recipe and the same product structure.",
            "Do not reuse the first attempt's focal shape, motif arrangement, or color blocking.",
        ))
    return "\n".join(rules)
