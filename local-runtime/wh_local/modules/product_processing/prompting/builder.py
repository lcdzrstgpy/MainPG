"""把 brief + style 拼成一条 prompt：身份 → 场景卡 → 逐格 → 契约 → 输出。"""

from __future__ import annotations

from .brief import ProductBrief
from .contract import contract
from .panel_plan import build_panel_plan
from .scenes import build_scene_cards
from .styles import get_style

MAX_CHARS = 8000
MAX_USER_ADDITIONS = 1200


def _scene_section(cards, forbid_studio: bool = True) -> str:
    lines = ["SCENE CARDS (four different places; props are context only, never included in the sale):"]
    for index, card in enumerate(cards, 1):
        props = ", ".join(card.props) if card.props else "no props"
        lines.append(
            f"{index} | {card.place} · {card.surface} | {card.light} | props: {props} | {card.depth}"
        )
    if forbid_studio:
        lines.append(
            "REAL-SET RULE: every panel is shot inside the real place named above; "
            "a plain white or grey seamless studio sweep is forbidden."
        )
    return "\n".join(lines)


def _panel_section(plan: list[str]) -> str:
    return "PANEL PLAN (what each quadrant shows; follow the fill numbers exactly):\n" + "\n".join(plan)


def _campaign_lock(brief: ProductBrief, style) -> str:
    """整套图共用的视觉合同（借鉴 Campaign Style Lock）：固定色板/光/呈现，四格不许漂移。"""

    if style.key == "A":
        palette = "pure white #FFFFFF background, product colour plus neutral grey #D9D9D9 contact shadow"
        light = "neutral 5500K softbox, soft contact shadow only"
    elif style.key in {"E", "B"}:
        palette = "warm ivory #F5F1E8, deep walnut #4A3728, brass #B08D57"
        light = "warm 3200K side light, soft background bokeh, gentle rim light"
    elif style.key == "C":
        palette = "clean paper #FAF7F2, pale oak #C8A97E, soft grey #E6E1D8"
        light = "even 5000K top light, minimal shadow"
    else:
        palette = "product colour plus two neutrals, sky #DCE6EC, stone #B9B2A6"
        light = "soft natural 5000K daylight from one side"
    return (
        "CAMPAIGN STYLE LOCK (identical across all four panels; never drift): "
        f"palette = {palette}; {light}; 35mm lens look, natural fine grain; "
        "product always centred at the planned fill with 12%-18% clear space; "
        "same colour temperature and same retouch level in every panel; "
        "no palette drift, no mixed colour temperature, no random background swap."
    )


def build_grid_prompt(
    brief: ProductBrief,
    style_key: str = "A",
    *,
    user_additions: str = "",
    language: str = "zh",
    seed: int = 0,
) -> str:
    style = get_style(style_key)
    ornate = style.key in {"E", "B"}
    cards = build_scene_cards(
        domain=brief.domain,
        season=brief.season,
        style_key=style.key,
        seed=seed,
        scale_anchor=brief.scale_anchor,
        prop_policy=brief.prop_policy,
        ornate=ornate,
    )
    plan = build_panel_plan(brief, style, cards)
    identity = (
        f"PRODUCT: {brief.subject}\n"
        f"CATEGORY: {brief.category or 'n/a'} | SIZE: {brief.size_class} | USAGE: {brief.usage}"
    )
    parts = [
        identity,
        _campaign_lock(brief, style),
        _scene_section(cards, forbid_studio=style.key != "A"),
        _panel_section(plan),
        contract(language),
    ]
    if user_additions.strip():
        text = user_additions.strip()[:MAX_USER_ADDITIONS]
        parts.append("OPERATOR NOTES (must not break the hard contract above):\n" + text)
    parts.append("OUTPUT: one 2048x2048 2x2 grid, four equal quadrants, continuous straight dividers.")
    prompt = "\n\n".join(parts)
    if len(prompt) > MAX_CHARS:
        prompt = prompt[:MAX_CHARS]
    return prompt
