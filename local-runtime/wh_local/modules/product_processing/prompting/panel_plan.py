"""逐格计划：机位 / 占比 / 摆放，具体到可指认。"""

from __future__ import annotations

from .brief import ProductBrief
from .scenes import SceneCard
from .styles import StyleCard

SET_PROFILES = {"home_decor", "soft_goods", "small_jewelry", "hair_accessory"}

SIZE_FILL = {
    "tiny": "45%-60%",
    "small": "45%-65%",
    "medium": "55%-70%",
    "large": "60%-75%",
}

HUMAN_LINE = {
    "none": "no person and no hand",
    "hand": "one adult hand and forearm may hold it, no face",
    "body_crop": "a torso or legs crop may appear, no face",
    "product_only": "product only, no person and no head",
}


def _support(brief: ProductBrief) -> str:
    if brief.usage == "headwear":
        return "displayed on a stand or flat, never worn by a person"
    if brief.rigidity == "soft":
        return "resting naturally under gravity, folds or coils following gravity, never propped upright"
    if brief.rigidity == "liquid":
        return "inside a container, level surface, no floating"
    return "resting on its own base"


def build_panel_plan(brief: ProductBrief, style: StyleCard, cards: list[SceneCard]) -> list[str]:
    fill = SIZE_FILL.get(brief.size_class, "55%-70%")
    human = HUMAN_LINE.get(brief.human_policy, HUMAN_LINE["none"])
    support = _support(brief)
    animal = brief.species if brief.species in {"cat", "dog"} else "cat or dog"

    if style.key == "C":
        hero = f"90-degree top-down flat lay on {cards[0].surface}, product {max(0, int(fill.split('%')[0]) + 5)}%, equal spacing"
    elif style.key == "D":
        hero = f"medium-wide establishing shot, product 45%-60%, {cards[0].place}"
    elif style.key == "A":
        hero = "pure white seamless, straight-on, product " + fill
    elif brief.profile == "coaster":
        hero = (
            f"0-degree scene hero: ONE single coaster under a ceramic mug, the rest of the set "
            f"stacked to the side and clearly away from the mug, product {fill}, {cards[0].place}; "
            "never put the mug on top of a stack"
        )
    elif brief.is_set and brief.profile in SET_PROFILES:
        hero = (
            f"0-degree scene hero: one piece as the hero, the rest of the set arranged beside it, "
            f"product {fill}, {cards[0].place}"
        )
    elif brief.usage == "worn_pet":
        hero = (
            f"0-degree scene hero: one real {animal} wearing the product, product {fill}, "
            f"{cards[0].place}; never a flat lay and never a white sweep"
        )
    elif brief.usage == "worn":
        hero = (
            f"0-degree scene hero: worn on a body crop (no face), product {fill}, {cards[0].place}"
        )
    elif brief.profile == "pet_walking":
        hero = (
            f"0-degree scene hero: a {animal} wearing the harness and leash, being walked on "
            f"{cards[0].place}, product {fill}, the leash held by one adult hand at natural height"
        )
    elif brief.usage == "held":
        hero = (
            f"0-degree scene hero: one adult hand holding it at natural height, product {fill}, "
            f"{cards[0].place}"
        )
    elif brief.profile == "home_storage":
        hero = (
            f"0-degree scene hero: installed under a kitchen cabinet with a tiled backsplash, "
            f"product {fill}, clearly load-bearing"
        )
    else:
        hero = (
            f"0-degree scene hero, product {fill}, {cards[0].place}; "
            "never a flat lay and never a white sweep"
        )

    in_use = f"{cards[1].place}, 45-degree alternate, product 45%-60%"
    if brief.profile == "coaster":
        in_use = (
            f"one adult hand setting a steaming mug down on ONE single coaster, {cards[1].place}, "
            "the remaining coasters stacked at the edge of the frame and not under the mug, "
            "product 45%-60%, the mug is the functional proof"
        )
    elif brief.is_set and brief.profile in SET_PROFILES:
        in_use = (
            f"the complete set arranged together, 45-degree, {cards[1].place}, product 45%-60%"
        )
    elif brief.profile == "pet_toy":
        in_use = (
            f"one real cat or dog actively playing with it, {cards[1].place}, "
            "product 45%-60%, the pet clearly interacting with the toy"
        )
    elif brief.profile == "kitchen_tool":
        in_use = (
            f"in use: citrus being pressed on the {cards[1].surface}, juice running into a glass, "
            "product 45%-60%, real kitchen counter"
        )
    elif brief.profile == "home_storage":
        in_use = (
            "installed under a kitchen cabinet and holding plates and mugs, 45-degree close, "
            "product 45%-60%, proof of function"
        )
    elif brief.usage == "worn_pet":
        in_use = f"one real {animal} wearing it, {cards[1].place}, product 45%-60%"
    elif brief.profile == "pet_walking":
        in_use = (
            f"one adult hand holding the leash while the {animal} walks, {cards[1].place}, "
            "product 45%-60%"
        )
    elif brief.usage in {"held", "consumed"}:
        in_use = f"one adult hand holding it, {cards[1].place}, product 45%-60%"
    elif brief.usage == "worn":
        in_use = f"worn on a body crop (no face), {cards[1].place}, product 45%-60%"

    detail = f"macro at 90mm, {cards[2].surface}, the strongest physical detail fills 75%-90%"
    ortho = "true orthographic view, plain surface, product " + fill + ", 12%-18% clear space"

    return [
        f"1 | {hero} | {cards[0].light} | {support} | {human}",
        f"2 | {in_use} | {cards[1].light}",
        f"3 | {detail} | {cards[2].light}",
        f"4 | {ortho} | even light",
    ]
