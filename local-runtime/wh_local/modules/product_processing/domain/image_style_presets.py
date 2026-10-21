"""Selectable global image styles.

These are the user-facing "风格统全局" layer.  They are appended to the
auto-classified category prompt at batch processing time, so the same category
can produce different moods without changing the category's scene/light/proof
guidance.

Keep every prompt short, single-line and free of ``{}`` placeholders.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ImageStylePreset:
    id: str
    label_zh: str
    prompt_en: str


IMAGE_STYLE_PRESETS: dict[str, ImageStylePreset] = {
    "auto": ImageStylePreset(
        "auto",
        "自动匹配",
        "",
    ),
    "calm_domestic": ImageStylePreset(
        "calm_domestic",
        "安静生活",
        "calm lived-in domestic mood, photorealistic marketplace finish, subtle natural grain",
    ),
    "bright_minimal": ImageStylePreset(
        "bright_minimal",
        "明亮极简",
        "bright airy mood, clean catalog finish, no waxy or plastic surfaces",
    ),
    "warm_evening": ImageStylePreset(
        "warm_evening",
        "温暖夜晚",
        "warm cozy evening mood, soft falloff, subtle natural grain",
    ),
    "clinical_clean": ImageStylePreset(
        "clinical_clean",
        "临床干净",
        "clinical clean mood, bright even light, no clutter",
    ),
    "industrial": ImageStylePreset(
        "industrial",
        "工业工坊",
        "industrial workshop mood, honest wear, no over-sharpening",
    ),
    "fresh_outdoor": ImageStylePreset(
        "fresh_outdoor",
        "清新户外",
        "fresh outdoor morning mood, natural daylight, subtle natural grain",
    ),
    "editorial_minimal": ImageStylePreset(
        "editorial_minimal",
        "编辑极简",
        "refined editorial minimal mood, clean retouching, generous negative space",
    ),
}

