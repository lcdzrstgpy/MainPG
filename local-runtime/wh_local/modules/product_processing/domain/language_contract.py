"""产品处理语言契约（对齐原型 native_product_engine 的语言管控）。

按目标语言（en/es）向 AI 提示词注入语言指令，并对 AI 结果做语言校验：
- en：禁止中文，所有面向买家的文案必须是英文；
- es：西班牙语任务（四宫格图不渲染任何附加文字、详情图无可见文字标签），
  文本结果必须呈现可靠的西班牙语特征，拒绝静默回退为英语。
"""

from __future__ import annotations

import re
from typing import Any

LANGUAGE_CONTRACT_VERSION = "product-language-v1"

LANGUAGE_PROFILES: dict[str, dict[str, str]] = {
    "en": {"code": "en", "label": "英语", "native_label": "English", "ai_language": "English", "locale": "en-US"},
    "es": {"code": "es", "label": "西班牙语", "native_label": "Español", "ai_language": "Spanish", "locale": "es-EC"},
}

_TARGET_LANGUAGE_ALIASES = {
    "en": "en", "eng": "en", "english": "en", "英语": "en", "英文": "en",
    "es": "es", "spa": "es", "spanish": "es", "espanol": "es", "español": "es", "西班牙语": "es", "西语": "es",
}

_CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")

_SPANISH_SIGNAL_WORDS = {
    "accesorio", "acero", "algodón", "aluminio", "almacenamiento", "ancho", "aplica",
    "azul", "bambú", "básico", "beige", "blanco", "brillante", "caja", "capacidad",
    "color", "compacto", "con", "cuerda", "de", "del", "detalle", "desconocido", "el",
    "en", "escritorio", "estampado", "estrecho", "fino", "grande", "gris", "grueso",
    "hierro", "hogar", "juego", "la", "las", "los", "madera", "mate", "material",
    "mediano", "morado", "negro", "ninguno", "organizador", "otro", "para", "pequeño",
    "pieza", "piezas", "plástico", "poliéster", "portátil", "producto", "redondo",
    "rayas", "rojo", "rosa", "sencillo", "silicona", "sin", "tela", "tapa", "tamaño",
    "uso", "verde", "y",
}

_ENGLISH_ONLY_SIGNAL_WORDS = {
    "and", "bamboo", "basic", "black", "blue", "box", "capacity", "classic", "cloth",
    "compact", "cotton", "detail", "fabric", "finish", "foldable", "for", "home",
    "large", "lid", "medium", "matte", "modern", "narrow", "organizer", "piece",
    "pieces", "plastic", "polyester", "portable", "product", "red", "rope", "round",
    "set", "silicone", "small", "smooth", "stainless", "steel", "storage", "striped",
    "thick", "thin", "the", "use", "wide", "with", "without", "wood", "adjustable",
    "glossy", "printed",
}

_SPANISH_LOCALIZED_TERMS = {
    "cotton rope": "Cuerda de algodón",
    "stainless steel": "Acero inoxidable",
    "cotton": "Algodón",
    "plastic": "Plástico",
    "polyester": "Poliéster",
    "silicone": "Silicona",
    "steel": "Acero",
    "wood": "Madera",
    "bamboo": "Bambú",
    "fabric": "Tela",
    "cloth": "Tela",
    "iron": "Hierro",
    "black": "Negro",
    "white": "Blanco",
    "gray": "Gris",
    "grey": "Gris",
    "dark gray": "Gris oscuro",
    "dark grey": "Gris oscuro",
    "light gray": "Gris claro",
    "light grey": "Gris claro",
    "blue": "Azul",
    "red": "Rojo",
    "green": "Verde",
    "yellow": "Amarillo",
    "pink": "Rosa",
    "purple": "Morado",
    "beige": "Beige",
    "orange": "Naranja",
    "gold": "Dorado",
    "silver": "Plata",
    "nylon": "Nailon",
    "leather": "Cuero",
    "glass": "Vidrio",
    "ceramic": "Cerámica",
    "stone": "Piedra",
    "plastic": "Plástico",
    "rubber": "Goma",
    "metal": "Metal",
}

_REPLACEMENTS_TO_SPANISH = (
    ("Full English only", "Full Spanish only"),
    ("full English only", "full Spanish only"),
    ("English product description", "Spanish product description"),
    ("English listing title", "Spanish listing title"),
    ("English shopper-readable value", "Spanish shopper-readable value"),
    ("English shopper-readable values", "Spanish shopper-readable values"),
    ("concise English values", "concise Spanish values"),
    ("concise English", "concise Spanish"),
    ("Natural fluent English", "Natural fluent Spanish"),
    ("natural fluent English", "natural fluent Spanish"),
    ("English title", "Spanish title"),
    ("English description", "Spanish description"),
    ("English text only", "Spanish text only"),
    ("English only", "Spanish only"),
    ("for US and European shoppers", "for Spanish-speaking shoppers in the selected market"),
    ("US/EU shoppers", "shoppers in the selected market"),
    ("US consumers", "shoppers in the selected market"),
    ("US/EU marketplace style", "marketplace style for the selected market"),
)


def normalize_target_language(value: Any, *, default: str = "en") -> str:
    raw = str(value or "").strip()
    if not raw:
        raw = str(default or "en").strip()
    normalized = _TARGET_LANGUAGE_ALIASES.get(raw.casefold())
    if normalized is None or normalized not in LANGUAGE_PROFILES:
        supported = ", ".join(sorted(LANGUAGE_PROFILES))
        raise ValueError(f"不支持的产品处理语言：{raw or value}；当前支持 {supported}")
    return normalized


def language_profile(value: Any) -> dict[str, str]:
    code = normalize_target_language(value)
    return dict(LANGUAGE_PROFILES[code])


def language_contract_instruction(stage: str, target_language: Any = "en", target_site: Any = "US") -> str:
    code = normalize_target_language(target_language)
    if code == "en":
        return (
            "PRODUCT LANGUAGE CONTRACT: All buyer-visible generated text for this stage must be in English. "
            "Keep verified brands, model codes, measurements, and platform codes unchanged. Never emit Chinese text."
        )
    if stage == "grid_image":
        return (
            "PRODUCT LANGUAGE CONTRACT: This is a Spanish product task. Render no added words, labels, badges, "
            "captions, or promotional text. Product-inherent brand/model printing may remain unchanged."
        )
    if stage == "detail_image":
        return (
            "PRODUCT LANGUAGE CONTRACT: This is a Spanish product task. Render the poster without any visible "
            "words or callout labels; deterministic Spanish labels are added locally when that path is used. "
            "Never render English or Chinese promotional text."
        )
    market = _market_label(target_site)
    return (
        "PRODUCT LANGUAGE CONTRACT: All buyer-visible output values for this stage must be natural Spanish. "
        f"Write for shoppers in {market}. "
        "Do not fall back to English. Keep verified brands, model codes, measurements, and platform codes unchanged. "
        "Never emit Chinese text. Internal JSON keys and platform enum codes must remain exactly as requested."
    )


def apply_language_contract_to_prompt(
    prompt: str,
    stage: str,
    target_language: Any = "en",
    target_site: Any = "US",
) -> str:
    value = str(prompt or "")
    if value.lstrip().startswith("PRODUCT LANGUAGE CONTRACT:"):
        return value
    if normalize_target_language(target_language) == "es":
        market = _market_label(target_site)
        replacements = list(_REPLACEMENTS_TO_SPANISH)
        replacements.append(("US/EU shoppers", f"shoppers in {market}"))
        replacements.append(("for US and European shoppers", f"for shoppers in {market}"))
        for old, new in replacements:
            value = value.replace(old, new)
        if stage == "detail_image":
            value = value.replace(
                "and exactly 3 light short labels placed cleanly around the poster.",
                "and no visible text labels anywhere on the poster.",
            )
            value = value.replace(
                "Callout text rules: exactly 3 factual labels, 1-4 words each, no sentence captions, slogans, unsupported claims, or invented dimensions.",
                "Callout text rules: do not render callout text; keep the poster text-free.",
            )
            value = value.replace("English only for added labels.", "Do not add labels or any visible text.")
    return f"{language_contract_instruction(stage, target_language, target_site)}\n\n{value}".strip()


def ensure_target_language_result(
    stage: str,
    text: Any,
    target_language: Any = "en",
    *,
    allow_code_only: bool = False,
) -> None:
    """校验 AI 结果语言；违规时抛 ValueError（阻止继续导出）。"""
    code = normalize_target_language(target_language)
    value = _language_audit_text(text)
    if not value:
        return
    if _CHINESE_RE.search(value):
        raise ValueError(f"{stage} 结果包含中文，未满足{language_profile(code)['label']}输出规则")
    if code == "en":
        return
    tokens = [token.casefold() for token in re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", value)]
    if not tokens:
        return
    compact = " ".join(tokens)
    if compact in _SPANISH_LOCALIZED_TERMS and _SPANISH_LOCALIZED_TERMS[compact].casefold() != compact:
        raise ValueError(f"{stage} 结果仍为英语，未满足西班牙语输出规则")
    spanish_hits = sum(token in _SPANISH_SIGNAL_WORDS for token in tokens)
    english_hits = sum(token in _ENGLISH_ONLY_SIGNAL_WORDS for token in tokens)
    has_spanish_diacritic = bool(re.search(r"[áéíóúüñ¿¡]", value, flags=re.IGNORECASE))
    code_like = all(
        len(token) <= 3
        or token.upper() == token
        or re.search(r"\d", token) is not None
        for token in re.findall(r"[A-Za-z0-9-]+", value)
    )
    if allow_code_only and code_like:
        return
    if english_hits and not spanish_hits and (english_hits >= 2 or len(tokens) <= 3):
        raise ValueError(f"{stage} 结果疑似回退为英语，已阻止西班牙语任务继续导出")
    if len(tokens) >= 5 and not spanish_hits and not has_spanish_diacritic:
        raise ValueError(f"{stage} 未检测到可靠西班牙语特征，已阻止静默英语回退")
    if english_hits >= spanish_hits + 3:
        raise ValueError(f"{stage} 中英语内容占比异常，已阻止混合语言导出")


def _language_audit_text(value: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = re.sub(r"https?://\S+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _market_label(target_site: Any) -> str:
    return {"CO": "Colombia", "EC": "Ecuador"}.get(str(target_site or "").strip().upper(), "the selected market")
