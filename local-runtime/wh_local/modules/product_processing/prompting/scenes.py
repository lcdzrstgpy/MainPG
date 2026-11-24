"""场景卡：地点 / 台面 / 光 / 色调 / 道具，唯一场景来源。"""

from __future__ import annotations

from dataclasses import dataclass

DOMAIN_PLACES: dict[str, tuple[str, ...]] = {
    "home": (
        "warm living-room sideboard with a linen runner",
        "sunlit wall corner with a wooden stool",
        "entryway console with a small plant",
        "quiet shelf beside a window",
    ),
    "living": (
        "low oak coffee table with an open book",
        "side table beside a linen sofa",
        "windowsill bench with a mug",
        "console table with a lit candle",
    ),
    "outdoor": (
        "sunlit park path with a low hedge",
        "wooden boardwalk at the edge of a lake",
        "gravel trailhead with pine trees",
        "quiet residential sidewalk with a stone wall",
    ),
    "kitchen": (
        "warm oak dining table with a linen runner",
        "kitchen island with a marble top",
        "open pantry shelf with glass jars",
        "breakfast nook beside a window",
    ),
    "office": (
        "minimal oak desk with a laptop stand",
        "pegboard workbench with tools",
        "shelf workspace with books and a plant",
        "meeting-table corner with a laptop",
    ),
    "apparel": (
        "bedroom bench at the foot of a linen bed",
        "open wardrobe with wooden hangers",
        "dressing table with a mirror",
        "window seat with folded knitwear",
    ),
    "bath": (
        "marble bathroom counter with a folded towel",
        "stone shower shelf with amber bottles",
        "white vanity with a round mirror",
        "bathtub edge with a wooden tray",
    ),
    "kids": (
        "nursery corner with a crib and a soft rug",
        "kids' play table with wooden blocks",
        "white changing table with baskets",
        "reading nook with a low bookshelf",
    ),
    "workshop": (
        "wooden workbench with hand tools",
        "concrete garage floor with a work tray",
        "metal shelving with labelled boxes",
        "outdoor shed bench with rope and gloves",
    ),
    "pet": (
        "sunlit wooden floor with a soft woven rug",
        "by the front door with a leash hook and a wooden bench",
        "window seat with a folded blanket",
        "grassy garden edge with a stone path",
    ),
    "car": (
        "car dashboard in warm afternoon daylight",
        "center console beside a coffee cup",
        "rearview mirror area with soft sky light",
        "air vent with a phone mount",
    ),
}


DOMAIN_SURFACES: dict[str, tuple[str, ...]] = {
    "home": ("oiled oak", "pale plaster", "linen", "stone"),
    "living": ("oak", "marble", "linen", "walnut"),
    "outdoor": ("weathered wood", "gravel", "stone paving", "packed earth"),
    "kitchen": ("oak", "marble", "ceramic tile", "linen"),
    "office": ("oak", "matte laminate", "felt", "glass"),
    "apparel": ("linen", "oak", "velvet", "marble"),
    "bath": ("marble", "stone", "ceramic", "glass"),
    "kids": ("pale wood", "cotton rug", "laminate", "felt"),
    "workshop": ("steel", "concrete", "plywood", "rubber mat"),
    "pet": ("oak floor", "woven rug", "linen", "stone"),
    "car": ("dashboard plastic", "leather seat", "wood trim", "matte console"),
}


DOMAIN_PROPS: dict[str, tuple[str, str]] = {
    "home": ("dried flowers in a vase", "a stack of books"),
    "living": ("a ceramic mug", "an open book", "a lit candle"),
    "outdoor": ("a folded blanket", "a wicker basket"),
    "kitchen": ("a cutting board", "a glass of juice"),
    "office": ("a notebook and pen", "a coffee cup"),
    "apparel": ("a knit throw", "a leather belt"),
    "bath": ("a folded towel", "a ceramic soap dish"),
    "kids": ("a wooden block", "a soft cushion"),
    "workshop": ("a coil of rope", "a pair of gloves"),
    "pet": ("a folded blanket", "a chew toy"),
    "car": ("car keys", "sunglasses"),
}

# 花哨档：只加氛围道具和更暖的光，场景类型不变；最多 3 件，全部在商品后方/两侧做虚化。
ORNATE_PROPS: dict[str, tuple[str, str, str]] = {
    "home": ("dried florals in a ceramic vase", "a lit pillar candle", "a folded linen throw"),
    "living": ("a lit pillar candle", "dried eucalyptus", "a folded knit throw"),
    "kitchen": ("a bowl of lemons", "a linen napkin", "a small jar of fresh herbs"),
    "outdoor": ("a woven picnic blanket", "a lit lantern", "wildflowers in a jar"),
    "office": ("a brass desk lamp", "a small trailing plant", "stacked art books"),
    "apparel": ("dried florals in a vase", "a perfume bottle", "a velvet stool"),
    "bath": ("a eucalyptus sprig", "rolled linen towels", "a ceramic tray"),
    "kids": ("a woven basket", "a soft cushion", "a small plush toy"),
    "workshop": ("a leather apron", "a coil of rope", "a metal tray"),
    "pet": ("a folded blanket", "a ceramic treat jar", "a woven basket"),
    "car": ("a leather wallet", "sunglasses", "a coffee cup"),
}

ORNATE_LIGHT = "warm golden side light, soft background bokeh, gentle rim light on the product"

SEASON_PROPS: dict[str, tuple[str, str]] = {
    "halloween": ("a carved pumpkin", "dried autumn leaves"),
    "christmas": ("a small decorated tree sprig", "pine cones"),
    "thanksgiving": ("a small gourd", "dried wheat"),
    "valentine": ("red roses", "a lit candle"),
    "easter": ("pastel eggs", "spring flowers"),
}

LIGHT_VARIANTS = (
    "warm side light from the left, soft long shadow",
    "cool window light from the right, gentle fill",
    "soft top light with a grounded contact shadow",
    "even ambient light, quiet highlights",
)

SEASON_KEYWORDS = {
    "halloween": r"halloween|万圣节|pumpkin|tombstone",
    "christmas": r"christmas|xmas|圣诞|advent|santa",
    "thanksgiving": r"thanksgiving|感恩节|harvest",
    "valentine": r"valentine|情人节|heart[- ]shaped",
    "easter": r"easter|复活节|bunny",
}


@dataclass(frozen=True)
class SceneCard:
    place: str
    surface: str
    light: str
    palette: str
    props: tuple[str, ...]
    depth: str


def detect_season(*texts: str) -> str:
    import re

    joined = " ".join(str(t or "") for t in texts)
    for season, pattern in SEASON_KEYWORDS.items():
        if re.search(pattern, joined, re.IGNORECASE):
            return season
    return ""


def build_scene_cards(
    *, domain: str, season: str, style_key: str, seed: int, scale_anchor: bool, prop_policy: str,
    ornate: bool = False,
) -> list[SceneCard]:
    places = DOMAIN_PLACES.get(domain, DOMAIN_PLACES["home"])
    surfaces = DOMAIN_SURFACES.get(domain, DOMAIN_SURFACES["home"])
    offset = seed % len(places)
    cards: list[SceneCard] = []
    for index in range(4):
        place = places[(offset + index) % len(places)]
        surface = surfaces[(offset + index) % len(surfaces)]
        props: tuple[str, ...] = ()
        if index == 0 and scale_anchor:
            props = ("a coin", "a ring box")
        elif season and prop_policy == "seasonal" and index in (0, 1):
            props = SEASON_PROPS[season]
        elif ornate and index in (0, 1):
            props = ORNATE_PROPS.get(domain, ())
        elif index == 0 and prop_policy != "none":
            props = DOMAIN_PROPS.get(domain, ())
        cards.append(
            SceneCard(
                place=place,
                surface=surface,
                light=(
                    ORNATE_LIGHT
                    if ornate and index in (0, 1)
                    else LIGHT_VARIANTS[(offset + index) % len(LIGHT_VARIANTS)]
                ),
                palette="product colour plus two neutrals",
                props=props,
                depth=(
                    "styled props behind and beside the product, softly out of focus; product is the sharpest element"
                    if ornate and index in (0, 1)
                    else "near foreground edge, midground product, legible far background"
                ),
            )
        )
    return cards
