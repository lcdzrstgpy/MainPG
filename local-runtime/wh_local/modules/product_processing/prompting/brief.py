"""ProductBrief：结构化档案。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from .profiles import match_profile
from .scenes import detect_season


@dataclass(frozen=True)
class ProductBrief:
    title: str
    category: str
    subject: str
    profile: str
    size_class: str
    rigidity: str
    usage: str
    human_policy: str
    scale_anchor: bool
    placement: tuple[str, ...]
    prop_policy: str
    domain: str
    season: str
    material: tuple[str, ...] = ()
    colors: tuple[str, ...] = ()
    attributes: tuple[str, ...] = ()
    species: str = "pet"
    is_set: bool = False


def _vision_props(vision: Any) -> tuple[str, ...]:
    if not isinstance(vision, Mapping):
        return ()
    raw = vision.get("excluded_elements") or ()
    if isinstance(raw, str):
        raw = (raw,)
    stop = re.compile(r"house|building|walkway|lawn|yard|room|background|floor|ground|\bprops?\b", re.I)
    keep = []
    for item in raw:
        text = str(item or "").strip()
        if 3 <= len(text) <= 60 and not stop.search(text):
            keep.append(text)
    return tuple(keep[:4])


def _species(title: str, subject: str, attributes: tuple[str, ...]) -> str:
    text = f"{subject} {' '.join(attributes)} {title}".lower()
    has_cat = bool(re.search(r"\bcat\b|\bcats\b|kitten|猫", text))
    has_dog = bool(re.search(r"\bdog\b|\bdogs\b|puppy|狗", text))
    if has_cat and not has_dog:
        return "cat"
    if has_dog and not has_cat:
        return "dog"
    if has_cat and has_dog:
        return "cat" if text.find("cat") < text.find("dog") else "dog"
    return "pet"


def _is_set(title: str, attributes: tuple[str, ...]) -> bool:
    text = f"{title} {' '.join(attributes)}".lower()
    if re.search(r"\bset of\b|\b\d+\s*(?:pcs|pieces|piece)\b|\b(?:pack|set)\b|\bassorted\b", text):
        return True
    return bool(re.search(r"套|件套|多件", text))


def infer_brief(
    title: str,
    category: str = "",
    vision_identity: Any = None,
    *,
    season_hint: str = "",
) -> ProductBrief:
    profile = match_profile(title, category)
    subject = ""
    material: tuple[str, ...] = ()
    colors: tuple[str, ...] = ()
    attributes: tuple[str, ...] = ()
    if isinstance(vision_identity, Mapping):
        subject = str(vision_identity.get("sellable_subject") or "")
        raw_attrs = vision_identity.get("visible_attributes") or ()
        if isinstance(raw_attrs, str):
            raw_attrs = (raw_attrs,)
        attributes = tuple(str(x) for x in raw_attrs if str(x).strip())[:6]
        colors = tuple(a for a in attributes if re.search(r"colou?r", a, re.I))[:3]
        material = tuple(a for a in attributes if re.search(r"fabric|nylon|polyester|cotton|metal|wood|leather|plush|ceramic|glass", a, re.I))[:3]
    season = season_hint or detect_season(title, category)
    if not season:
        joined = " ".join(_vision_props(vision_identity)).lower()
        if "pumpkin" in joined or "cobweb" in joined:
            season = "halloween"
        elif "christmas" in joined or "pine cone" in joined:
            season = "christmas"
    species = _species(title, subject, attributes)
    is_set = _is_set(title, attributes)
    return ProductBrief(
        title=str(title or "").strip(),
        category=str(category or "").strip(),
        subject=subject or str(title or "").strip(),
        profile=profile.key,
        size_class=profile.size_class,
        rigidity=profile.rigidity,
        usage=profile.usage,
        human_policy=profile.human_policy,
        scale_anchor=profile.scale_anchor,
        placement=profile.placement,
        prop_policy=profile.prop_policy,
        domain=profile.domain,
        season=season,
        material=material,
        colors=colors,
        attributes=attributes,
        species=species,
        is_set=is_set,
    )
