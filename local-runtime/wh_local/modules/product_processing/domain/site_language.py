"""Immutable site/language snapshots for Product Processing tasks.

The page may change its selections at any time, but a task keeps the exact
market and language decision that was active when it was started.
"""

from __future__ import annotations

from typing import Any


SITE_LANGUAGE_CONTRACT_VERSION = "product-processing-site-language-v1"

_SITES: dict[str, dict[str, str]] = {
    "US": {"label": "美国站", "market": "United States", "default_language": "en"},
    "CO": {"label": "哥伦比亚站", "market": "Colombia", "default_language": "es"},
    "EC": {"label": "厄瓜多尔站", "market": "Ecuador", "default_language": "es"},
}
_LANGUAGES: dict[str, dict[str, str]] = {
    "en": {"label": "英语 · English", "native_label": "English"},
    "es": {"label": "西班牙语 · Español", "native_label": "Español"},
}
_LANGUAGE_ALIASES = {
    "en": "en", "english": "en", "英语": "en",
    "es": "es", "spanish": "es", "español": "es", "西班牙语": "es",
}
_SELECTION_SOURCES = {"site_default", "employee_override"}


def resolve_site_language(
    *, target_site: Any = "US", target_language: Any = None, language_selected_by: Any = None
) -> dict[str, str]:
    """Validate a page selection and return the snapshot stored with a task."""
    site = str(target_site or "US").strip().upper()
    if site not in _SITES:
        raise ValueError("target_site must be US, CO or EC")
    site_profile = _SITES[site]
    default_language = site_profile["default_language"]
    language = _normalize_language(target_language) if target_language not in (None, "") else default_language
    selected_by = str(language_selected_by or "").strip().lower()
    if selected_by and selected_by not in _SELECTION_SOURCES:
        raise ValueError("language_selected_by must be site_default or employee_override")
    if not selected_by:
        selected_by = "site_default" if language == default_language else "employee_override"
    if selected_by == "site_default" and language != default_language:
        raise ValueError("site_default language must match the selected site's default language")
    language_profile = _LANGUAGES[language]
    return {
        "target_site": site,
        "target_site_label": site_profile["label"],
        "target_market": site_profile["market"],
        "site_default_language": default_language,
        "target_language": language,
        "target_language_label": language_profile["label"],
        "target_language_native_label": language_profile["native_label"],
        "language_selected_by": selected_by,
        "language_contract_version": SITE_LANGUAGE_CONTRACT_VERSION,
    }


def site_language_options(
    *, target_site: Any = "US", target_language: Any = None, language_selected_by: Any = None
) -> dict[str, Any]:
    """Return the values needed to render the top site/language controls."""
    selected = resolve_site_language(
        target_site=target_site,
        target_language=target_language,
        language_selected_by=language_selected_by,
    )
    return {
        "contract_version": SITE_LANGUAGE_CONTRACT_VERSION,
        "selection_scope": "new_tasks_only",
        "selection_rule": "Changing site without an explicit language resets language to that site's default.",
        "sites": [
            {"site_code": code, "label": profile["label"], "market": profile["market"], "default_language": profile["default_language"]}
            for code, profile in _SITES.items()
        ],
        "languages": [{"language_code": code, **profile} for code, profile in _LANGUAGES.items()],
        "selected": selected,
    }


def _normalize_language(value: Any) -> str:
    normalized = _LANGUAGE_ALIASES.get(str(value or "").strip().casefold())
    if normalized is None:
        raise ValueError("target_language must be en or es")
    return normalized
