from __future__ import annotations

import re
from typing import Mapping, Sequence

from .contracts import BusinessFields


PATTERN_PROMPT_VERSION = "v1"
LISTING_IMAGE_ROLES = ("hero", "detail_a", "detail_b", "lifestyle")
# Generic fallback motifs, used only when the brief supplies no theme and no
# style keywords. They must never override an explicit user theme (see
# build_style_listing_prompt below).
_GENERIC_MOTIFS = (
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

# Theme-conditioned motif vocabulary. When the brief declares a theme, the
# per-style recipe must draw its subject from the theme's own motif pool instead
# of from the generic list, so a batch of "ocean" styles stays on-ocean while
# still varying between styles.
_THEME_MOTIFS: dict[str, tuple[str, ...]] = {
    "ocean": (
        "rolling ocean waves",
        "schools of fish and bubbles",
        "seashells, starfish, and coral",
        "anchor and sailing-rope motifs",
        "lighthouse and sailboat silhouettes",
        "sea-turtle and whale silhouettes",
        "underwater kelp and coral reefs",
        "nautical compass and rope knots",
    ),
    "tropical": (
        "monstera and palm fronds",
        "tropical foliage with hibiscus",
        "pineapple and banana-leaf motifs",
        "toucan and parrot silhouettes",
        "flamingo and palm silhouettes",
        "plumeria and frangipani blooms",
        "jungle leaves and vines",
        "coconut and surf motifs",
    ),
    "botanical": (
        "eucalyptus branches",
        "olive branches",
        "wildflower sprigs and stems",
        "fern fronds and leafy vines",
        "dried grass and seed heads",
        "citrus branches with blossoms",
        "succulent and aloe rosettes",
        "leaf-skeleton linework",
    ),
    "floral": (
        "rose and peony blooms",
        "daisy and chamomile clusters",
        "tulip and lily linework",
        "cherry-blossom branches",
        "sunflower and marigold motifs",
        "lavender and wildflower sprigs",
        "lotus and water-lily forms",
        "dahlia and zinnia rosettes",
    ),
    "celestial": (
        "crescent moons and stars",
        "constellations and dot networks",
        "sun and moon phases",
        "planets and orbit rings",
        "shooting-star streaks",
        "abstract zodiac symbols",
        "galaxy swirls",
        "celestial orbs",
    ),
    "geometric": (
        "triangles and diamond facets",
        "hexagons and honeycomb",
        "concentric circles and arcs",
        "chevrons and bold stripes",
        "modular grid and lattice",
        "rhombus and prism shapes",
        "abstract polyhedra",
        "woven geometric bands",
    ),
    "desert": (
        "cactus and saguaro silhouettes",
        "desert mesas and mountains",
        "sun and cactus silhouettes",
        "desert flora and succulents",
        "sand-dune linework",
        "southwestern geometric marks",
        "tumbleweed and yucca",
        "desert night-sky motifs",
    ),
    "folk": (
        "folk floral ornaments",
        "tribal geometric bands",
        "boho diamond motifs",
        "handwoven stripe patterns",
        "folk bird and flower motifs",
        "ethnic diamond lattice",
        "kilim-inspired motifs",
        "folk sun and star symbols",
    ),
    "animal": (
        "cat and paw motifs",
        "dog and bone motifs",
        "bird and feather motifs",
        "butterfly and insect motifs",
        "deer and antler motifs",
        "wildlife silhouette scenes",
        "jungle animal faces",
        "ocean animal silhouettes",
    ),
    "retro": (
        "mid-century atomic shapes",
        "retro floral clusters",
        "vintage travel emblems without text",
        "1970s wave forms",
        "retro stripes and dots",
        "groovy abstract shapes",
        "mid-century starbursts",
        "retro sun and rainbow arcs",
    ),
    "minimal": (
        "a single bold line motif",
        "a single geometric accent",
        "negative-space shapes",
        "a simple dot arrangement",
        "a minimal arch shape",
        "a single organic silhouette",
        "restrained line composition",
        "a bare geometric accent",
    ),
    "abstract": (
        "freeform brush strokes",
        "ink-splatter shapes",
        "marbled swirls",
        "layered organic shapes",
        "geometric abstraction",
        "drip and splatter marks",
        "fluid wave forms",
        "abstract collage shapes",
    ),
    "christmas": (
        "snowflakes and winter motifs",
        "christmas trees and baubles",
        "holly and berry sprigs",
        "reindeer and sleigh motifs",
        "candy canes and wreaths",
        "festive ornaments",
        "winter village shapes",
        "gift and ribbon motifs",
    ),
    "halloween": (
        "pumpkins and gourds",
        "bats and crescent moons",
        "ghost and cobweb motifs",
        "witch hats and brooms",
        "skull and candle motifs",
        "spiders and webs",
        "halloween cats and owls",
        "haunted-house shapes",
    ),
}

# Ordered theme detection. The first theme whose keyword appears in the brief
# wins; more specific themes are listed before their broader neighbours so that
# e.g. "tropical plants" resolves to "tropical" rather than "botanical".
_THEME_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Holiday themes first: an explicit holiday word (圣诞/万圣) is a dominant
    # signal and must beat a broad subject word like "植物" that can sneak into
    # the same brief ("圣诞植物手绘").
    (
        "christmas",
        ("圣诞", "节日", "圣诞树", "雪花", "雪人", "christmas", "holiday", "snowflake", "santa", "xmas"),
    ),
    (
        "halloween",
        ("万圣", "万圣节", "南瓜", "蝙蝠", "骷髅", "halloween", "pumpkin", "bats", "ghost", "spooky"),
    ),
    (
        "ocean",
        (
            "海洋", "海浪", "海边", "海滩", "航海", "海星", "贝壳", "珊瑚", "鲸", "海豚", "沙滩", "水母",
            "ocean", "sea", "wave", "waves", "nautical", "seashell", "starfish", "coral", "beach", "underwater",
        ),
    ),
    (
        "tropical",
        ("热带", "棕榈", "夏威夷", "菠萝", "火烈鸟", "tropical", "palm", "hibiscus", "pineapple", "jungle", "hawaii"),
    ),
    (
        "botanical",
        ("植物", "绿植", "枝叶", "草木", "蕨", "桉树", "尤加利", "botanical", "leaf", "leaves", "fern", "foliage",
         "greenery", "eucalyptus", "plant"),
    ),
    (
        "floral",
        ("花卉", "花束", "花朵", "玫瑰", "郁金香", "牡丹", "雏菊", "floral", "flower", "flowers", "bloom", "rose",
         "tulip", "daisy", "peony", "blossom"),
    ),
    (
        "celestial",
        ("星空", "星座", "宇宙", "太空", "月亮", "星辰", "银河", "celestial", "stars", "moon", "constellation",
         "galaxy", "zodiac", "astrology"),
    ),
    (
        "geometric",
        ("几何", "几何图案", "菱形", "格纹", "条纹", "几何风", "geometric", "geometry", "diamond", "hexagon", "stripes",
         "checkered", "chequered"),
    ),
    (
        "desert",
        ("沙漠", "仙人掌", "西南", "西部", "戈壁", "desert", "cactus", "southwestern"),
    ),
    (
        "folk",
        ("民族", "民俗", "波西米亚", "部落", "波希米亚", "民族风", "folk", "boho", "bohemian", "tribal", "kilim", "ethnic"),
    ),
    (
        "animal",
        ("动物", "野生动物", "萌宠", "宠物", "animal", "wildlife", "cat", "dog", "bird", "butterfly", "deer", "safari",
         "zoo"),
    ),
    (
        "retro",
        ("复古", "怀旧", "中古", "retro", "vintage", "mid-century", "groovy", "70s"),
    ),
    (
        "minimal",
        ("极简", "简约", "素色", "纯色", "极简风", "minimal", "minimalist", "monochrome", "simple"),
    ),
    (
        "abstract",
        ("抽象", "涂鸦", "泼墨", "abstract", "doodle", "marble", "marbled"),
    ),
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
        "Panel 1 — MATERIAL IMAGE (top-left): show one complete product against a newly generated clean neutral ecommerce background. Keep the whole product clearly visible, make it fill most of the panel, and show the full design sharply. This is supporting material imagery, not the marketplace primary image.",
        "Panel 2 — DETAIL IMAGE A (top-right): show a tight high-resolution close-up of the newly invented surface artwork on this same product. Make color, edges, print or material texture, and manufacturing detail easy to inspect; do not alter the artwork or its placement.",
        "Panel 3 — DETAIL IMAGE B (bottom-left): show a different close product detail or three-quarter product view. Choose a product-appropriate structural or material detail, while keeping the artwork visibly identical to Panel 1 and Panel 2.",
        "Panel 4 — PRIMARY IMAGE (bottom-right): show the same complete product in one newly generated, natural, commercially useful lifestyle setting. Keep the full product and unchanged artwork visible; this is the marketplace primary image and title reference. Do not reuse the template background or add another product.",
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


def _brief_theme_keywords(business_fields: Mapping[str, object] | None) -> tuple[str, list[str]]:
    if not business_fields:
        return "", []
    theme = str(business_fields.get("design_theme") or "").strip()
    raw_keywords = business_fields.get("style_keywords") or []
    if isinstance(raw_keywords, str):
        raw_keywords = [raw_keywords]
    keywords = [str(item).strip() for item in raw_keywords if str(item).strip()]
    return theme, keywords


def _brief_color_preferences(business_fields: Mapping[str, object] | None) -> list[str]:
    if not business_fields:
        return []
    raw = business_fields.get("color_preferences") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(item).strip() for item in raw if str(item).strip()]


def _theme_contains(haystack: str, needle: str) -> bool:
    """Match CJK needles by substring, ASCII needles on word boundaries.

    Word boundaries stop short ASCII needles such as "cat" or "sea" from
    matching unrelated substrings like "category" or "season".
    """
    if needle.isascii():
        return re.search(rf"(?<![A-Za-z]){re.escape(needle)}(?![A-Za-z])", haystack) is not None
    return needle in haystack


def _detect_theme(business_fields: Mapping[str, object] | None, free_text: str = "") -> str | None:
    """Return the first matching theme for the brief, or None if no theme signal exists.

    Structured fields (design theme and style keywords) are scanned first, then
    the user's creative direction, so an explicit theme always wins over
    incidental words in the product description or creative prompt.
    """
    theme, keywords = _brief_theme_keywords(business_fields)
    # Priority is by source, not whole-blob: design theme (alone) beats style
    # keywords beats free text. Scanning them together let a broad keyword such
    # as "植物" in "圣诞植物手绘" hijack an explicit holiday theme
    # ("美式复古圣诞节") and turn a christmas brief into a generic botanical one.
    for source in (theme, " ".join(keywords), free_text):
        haystack = source.lower()
        for name, needles in _THEME_KEYWORDS:
            if any(_theme_contains(haystack, needle) for needle in needles):
                return name
    return None


# Themes whose built-in pool describes a STYLE/ERA/technique rather than a visual
# subject. For these the user's own design theme (bow, wildflower, ...) is what
# defines the subject, so the fixed pool must NOT auto-supply the motif; instead
# the brief falls back to "invent within the theme" or a Doubao-learned pool keyed
# by the full design theme.
_STYLE_BIAS_THEMES = frozenset({"retro", "minimal", "abstract"})


def _motif_for_style(
    business_fields: Mapping[str, object] | None,
    style_index: int,
    creative_prompt: str = "",
    theme_pools: Mapping[str, Sequence[str]] | None = None,
) -> str:
    """Return a per-style subject that stays on the brief.

    The SUBJECT always comes from the user's own theme (``design_theme``) so the
    model never replaces the brief's subject with a fixed pool subject. Resolution
    order: (1) a Doubao-learned pool keyed by the exact design theme, (2) a
    built-in pool only for subject themes (never style/era descriptors such as
    retro/minimal/abstract), (3) "invent a subject within the brief's theme" so a
    novel or compound theme is never replaced by an unrelated subject. Subjects
    reuse across styles (variety comes from the full recipe). The generic pool is
    reached only when the user expressed nothing at all.
    """
    if theme_pools is None:
        theme_pools = _THEME_MOTIFS
    theme = _detect_theme(business_fields, creative_prompt)
    theme_text, keywords = _brief_theme_keywords(business_fields)
    subject = theme_text or theme
    if subject:
        # 1) learned pool keyed by the user's exact design theme (highest priority)
        pool = theme_pools.get(theme_text) if theme_text else None
        # 2) built-in pool, but only for subject themes (never style/era descriptors)
        if pool is None and theme and theme not in _STYLE_BIAS_THEMES:
            pool = theme_pools.get(theme)
        if pool:
            variation = pool[(style_index - 1) % len(pool)]
            return f"a variation inspired by '{variation}', within the brief's theme ({subject})"
        return f"a NEW specific subject within the brief's theme ({subject})"
    if keywords:
        return f"a NEW specific subject derived from the style keywords ({', '.join(keywords[:3])})"
    if creative_prompt.strip():
        return "a NEW specific subject from the creative direction"
    return _GENERIC_MOTIFS[(style_index - 1) % len(_GENERIC_MOTIFS)]


def build_style_listing_prompt(
    base_prompt: str,
    *,
    style_index: int,
    attempt: int,
    business_fields: Mapping[str, object] | None = None,
    creative_prompt: str = "",
    theme_pools: Mapping[str, Sequence[str]] | None = None,
) -> str:
    """Append one deterministic, batch-diverse creative recipe to a listing prompt.

    The recipe varies composition, palette, rendering, density, and (when the
    brief names a theme) a theme-scoped motif. It is diversity guidance only:
    the Design theme, Style keywords, and Creative direction already present in
    the base prompt take precedence and must never be replaced by an unrelated
    subject. The user's own creative direction is scanned (never the whole
    boilerplate) so a novel theme written as free text is still respected.
    ``theme_pools`` merges built-in and Doubao-learned subject pools.
    """
    if style_index < 1:
        raise ValueError("style_index must be positive")
    if attempt not in {1, 2}:
        raise ValueError("attempt must be 1 or 2")
    offset = style_index - 1
    motif = _motif_for_style(business_fields, style_index, creative_prompt, theme_pools)
    composition = _STYLE_COMPOSITIONS[(offset * 3) % len(_STYLE_COMPOSITIONS)]
    color_preferences = _brief_color_preferences(business_fields)
    # When the user named exact colors, defer to them instead of the recipe pool,
    # so a fixed palette ("two-color high contrast") cannot fight the brief.
    palette = (
        f"the brief's specified colors ({', '.join(color_preferences)})"
        if color_preferences
        else _STYLE_PALETTES[(offset * 7) % len(_STYLE_PALETTES)]
    )
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
        "The Design theme, Style keywords, and Creative direction above are the highest priority and must never be contradicted. The recipe below only varies how that theme is arranged; it must never replace the theme with an unrelated subject.",
        "The motif under 'Style creative signature' is only a variation idea, not the real subject: the actual subject is defined by the brief's Design theme and Style keywords and must be preserved in every panel. If the suggested variation conflicts with that subject, re-interpret the variation so it stays on the brief's subject; never swap in a different subject.",
        "Use the motif, composition, palette, rendering, and density recipe purely as diversity guidance while staying strictly within the brief's theme. Do not fall back to the template artwork or a generic design used for another style.",
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
