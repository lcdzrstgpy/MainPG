from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


AXIS_FIELDS = ("length_cm", "width_cm", "height_cm", "weight_g")
AXIS_PREFIXES = {
    "length_cm": "len",
    "width_cm": "wid",
    "height_cm": "hei",
    "weight_g": "wgt",
}
GLOBAL_WORKSPACE = "__global__"
MIN_STAT_SAMPLES = 20


@dataclass(frozen=True)
class SeedTemplate:
    category_key: str
    package_profile: str
    bounds: Mapping[str, tuple[float, float, float]]


# Conservative logistics priors. They are hard safety envelopes, not learned
# percentiles. Learned P10/P50/P90 values remain soft expected ranges.
SEED_TEMPLATES = (
    SeedTemplate(
        "profile:flat_soft_item",
        "flat_soft_item",
        {
            "length_cm": (10, 60, 30), "width_cm": (8, 45, 24),
            "height_cm": (1, 15, 5), "weight_g": (30, 3000, 350),
        },
    ),
    SeedTemplate(
        "profile:foldable_soft_bag",
        "foldable_soft_bag",
        {
            "length_cm": (12, 70, 35), "width_cm": (10, 55, 28),
            "height_cm": (2, 25, 8), "weight_g": (80, 5000, 650),
        },
    ),
    SeedTemplate(
        "profile:rigid_container",
        "rigid_container",
        {
            "length_cm": (8, 100, 35), "width_cm": (8, 80, 28),
            "height_cm": (4, 70, 20), "weight_g": (100, 15000, 1200),
        },
    ),
    SeedTemplate(
        "profile:compact_tool",
        "compact_tool",
        {
            "length_cm": (5, 60, 24), "width_cm": (4, 40, 16),
            "height_cm": (2, 30, 8), "weight_g": (50, 8000, 700),
        },
    ),
    SeedTemplate(
        "profile:small_accessory",
        "small_accessory",
        {
            "length_cm": (3, 35, 15), "width_cm": (2, 30, 10),
            "height_cm": (1, 18, 4), "weight_g": (5, 2000, 120),
        },
    ),
    SeedTemplate(
        "fallback",
        "generic",
        {
            "length_cm": (3, 120, 30), "width_cm": (2, 100, 22),
            "height_cm": (1, 80, 10), "weight_g": (5, 30000, 500),
        },
    ),
)


_PROFILE_KEYWORDS = (
    ("flat_soft_item", ("服装", "衣服", "衬衫", "裤", "裙", "毛巾", "布", "garment", "shirt", "pants", "towel")),
    ("foldable_soft_bag", ("包", "袋", "背包", "手提包", "收纳袋", "bag", "pouch", "backpack")),
    ("rigid_container", ("收纳箱", "盒", "箱", "瓶", "杯", "罐", "container", "box", "bottle", "cup")),
    ("compact_tool", ("工具", "扳手", "钳", "钻", "螺丝刀", "tool", "wrench", "plier", "drill")),
    ("small_accessory", ("耳机", "数据线", "线缆", "首饰", "配件", "充电器", "earphone", "cable", "jewelry", "charger")),
)


def normalize_category_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    text = re.sub(r"\s*(?:>|/|\\|›|»|→|\|)+\s*", "/", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" /.")


def infer_package_profile(*values: Any) -> str:
    haystack = " ".join(normalize_category_text(value) for value in values if value)
    for profile, keywords in _PROFILE_KEYWORDS:
        if any(keyword in haystack for keyword in keywords):
            return profile
    return "generic"


def category_identity(raw: Mapping[str, Any], title: str = "") -> tuple[str, str, tuple[str, ...]]:
    platform = normalize_category_text(raw.get("source_platform") or raw.get("platform")) or "unknown"
    category_id = normalize_category_text(raw.get("category_id") or raw.get("leaf_category_id"))
    category_path = normalize_category_text(
        raw.get("category_path") or raw.get("source_category_path") or raw.get("category")
    )
    profile = infer_package_profile(category_path, raw.get("category"), title)
    candidates: list[str] = []
    if category_id:
        candidates.append(f"{platform}:id:{category_id}")
    if category_path:
        parts = [part.strip() for part in category_path.split("/") if part.strip()]
        candidates.extend(f"path:{'/'.join(parts[:depth])}" for depth in range(len(parts), 0, -1))
    candidates.extend((f"profile:{profile}", "fallback"))
    unique = tuple(dict.fromkeys(candidates))
    # Observations learn against a stable source category when available. A
    # normalized path is the next-best key; profile/fallback are priors only.
    learned_key = unique[0] if unique and unique[0] not in {f"profile:{profile}", "fallback"} else f"profile:{profile}"
    return learned_key, profile, unique


def template_signature(template: Mapping[str, Any] | None) -> str:
    if not template:
        return "none"
    relevant = {
        key: template.get(key)
        for key in sorted(template)
        if key.startswith(("known_", "stat_"))
        or key in {"workspace_id", "category_key", "package_profile", "updated_at"}
    }
    payload = json.dumps(relevant, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def template_axis_policy(template: Mapping[str, Any], field: str) -> dict[str, Any]:
    prefix = AXIS_PREFIXES[field]
    count = int(template.get(f"{prefix}_sample_count") or 0)
    use_stats = count >= MIN_STAT_SAMPLES and all(
        template.get(f"stat_{prefix}_{quantile}") is not None for quantile in ("p10", "p50", "p90")
    )
    return {
        "hard_min": template.get(f"known_{prefix}_min"),
        "hard_max": template.get(f"known_{prefix}_max"),
        "default": (
            template.get(f"stat_{prefix}_p50") if use_stats else template.get(f"known_{prefix}_default")
        ),
        "expected_min": (
            template.get(f"stat_{prefix}_p10") if use_stats else template.get(f"known_{prefix}_min")
        ),
        "expected_max": (
            template.get(f"stat_{prefix}_p90") if use_stats else template.get(f"known_{prefix}_max")
        ),
        "sample_count": count,
        "uses_statistics": use_stats,
    }

