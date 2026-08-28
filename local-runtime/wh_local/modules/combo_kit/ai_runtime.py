"""combo_kit AI 运行时：组合套装文本生成 + 6 张单图直连生成 + 主体解析。

复用底层工具（product_processing.doubao_text / doubao_vision / media），
但 Prompt 组装、图片角色、产出合同均独立于老 AI 处理模块。
本模块关闭 OCR 文字质检：不调用任何 inspect_visible_text。
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..product_processing.doubao_ark import DoubaoArkClient, DoubaoArkError
from ..product_processing.infrastructure.media import ProductImageProcessor
from .contracts import IMAGE_ROLES, ComboKitValidationError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ComboKitAiRuntime:
    def __init__(self, media_processor: ProductImageProcessor | None = None) -> None:
        # 复用生图处理器；config_provider 由调用方注入，保证直连/托管双轨可用。
        self._media = media_processor

    # ---- 主体解析（串行：主体词 + 人工蒙版 → AI 解析） ----
    def analyze_subject(
        self,
        *,
        image_path: str,
        subject_keywords: str,
        mask: dict[str, Any] | None,
        original_fallback_title: str,
    ) -> dict[str, Any]:
        from ..product_processing.doubao_vision import (
            SUBJECT_ANALYSIS_PROMPT,
            subject_analysis_from_dict,
        )

        analysis = analyze_subject_with_mask(
            image_path=image_path,
            subject_keywords=subject_keywords,
            mask=mask,
            fallback_title=original_fallback_title,
        )
        return analysis

    # ---- 文本生成（标题 + 详情描述 + 五点） ----
    def generate_text(self, *, prompt: str) -> dict[str, Any]:
        result = generate_combo_text(prompt)
        return result

    # ---- 融合套装主图（主体解析后立即生成，作为第 1 张成品图复用） ----
    def generate_fusion_main(
        self,
        *,
        reference_values: list[str],
        set_name: str,
        subject_summaries: list[str],
        custom_prompt: str = "",
    ) -> dict[str, Any]:
        from .generation import _make_media_processor
        from .prompts import build_fusion_main_prompt

        processor = self._media or _make_media_processor()
        if not processor:
            raise ComboKitValidationError("融合主图处理器不可用")
        prompt = build_fusion_main_prompt(
            set_name=set_name, subject_summaries=subject_summaries, custom_prompt=custom_prompt
        )
        media = processor.generate(
            stage="main",
            prompt=prompt,
            reference_values=reference_values,
            image_size="2048x2048",
        )
        normalized = processor.normalize_standalone_image(media, stage="main")
        return {
            "role": "main",
            "content": bytes(getattr(normalized, "content", b"") or b""),
            "suffix": str(getattr(normalized, "suffix", ".jpg") or ".jpg"),
            "provider": str(getattr(normalized, "provider", "") or ""),
            "model": str(getattr(normalized, "model", "") or ""),
            "attempt_count": int(getattr(normalized, "attempt_count", 0) or 0),
            "status_class": str(getattr(normalized, "provider_status_class", "") or "success"),
        }

    # ---- 成品图生成（单图直出，无四宫格；主图由融合主图复用） ----
    def generate_images(
        self,
        *,
        reference_values: list[str],
        prompts: dict[str, str],
        fusion_content: bytes | None,
        fusion_suffix: str,
        set_id: str,
        workspace_id: str,
    ) -> list[dict[str, Any]]:
        from .generation import generate_combo_images

        return generate_combo_images(
            media_processor=self._media,
            reference_values=reference_values,
            prompts=prompts,
            fusion_content=fusion_content,
            fusion_suffix=fusion_suffix,
            set_id=set_id,
            workspace_id=workspace_id,
        )


def analyze_subject_with_mask(
    *,
    image_path: str,
    subject_keywords: str,
    mask: dict[str, Any] | None,
    fallback_title: str,
) -> dict[str, Any]:
    """AI 依据「主体词 + 人工选区掩码」解析商品主体。

    串行前提：必须先有用户主体词与蒙版。仅保留可见属性；不调用 OCR。

    图片以本地文件读取后转 base64 data URL 内嵌给模型（与 POD 主图/主体
    识别一致），方舟上游无需访问任何本机或鉴权 URL，因此不会因地址不可达
    而回退到主体词。
    """
    from ..product_processing.doubao_vision import SUBJECT_ANALYSIS_PROMPT

    data_url = _local_image_data_url(image_path)
    messages = build_subject_messages(
        prompt=SUBJECT_ANALYSIS_PROMPT,
        data_url=data_url,
        subject_keywords=subject_keywords,
        mask=mask,
        fallback_title=fallback_title,
    )
    client = _ark_client()
    try:
        content = client.complete(messages)
    except DoubaoArkError as exc:
        return {
            "sellable_subject": fallback_title or "商品主体",
            "subject_explanation": str(exc)[:200],
            "visible_attributes": [],
            "excluded_elements": [],
            "confidence": "low",
            "uncertainty_reason": str(exc)[:200],
            "explicit_measurements": {},
        }
    return parse_subject_json(content, fallback_title=fallback_title)


def build_subject_messages(
    *,
    prompt: str,
    data_url: str,
    subject_keywords: str,
    mask: dict[str, Any] | None,
    fallback_title: str,
) -> list[dict[str, Any]]:
    """组装主体解析消息：把用户主体词与选区掩码一并交给模型。

    掩码仅作为「人工确认的选区」参考，不校验文字、不做 OCR。图片以
    base64 data URL 内嵌，避免方舟上游访问不到本机/鉴权 URL。
    """
    mask_text = json.dumps(mask or {}, ensure_ascii=False)[:2000]
    keyword_text = str(subject_keywords or "").strip() or "（未填写）"
    base = (
        f"{prompt}\n\n"
        f"SELLER SUBJECT KEYWORDS: {keyword_text}\n"
        f"MANUAL REGION MASK (normalized): {mask_text}\n"
        f"FALLBACK TITLE from source: {fallback_title}\n"
        "Use the seller keywords and the manual region as the primary object "
        "selection; the mask marks the region the seller confirmed as the product."
    )
    image_content = (
        [{"type": "image_url", "image_url": {"url": data_url}}]
        if data_url.startswith("data:image/")
        else []
    )
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": base},
                *image_content,
            ],
        }
    ]


def parse_subject_json(content: str, *, fallback_title: str) -> dict[str, Any]:
    from ..product_processing.doubao_vision import (
        subject_analysis_from_dict,
    )

    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return _subject_fallback(fallback_title, "subject analysis returned invalid JSON")
    if not isinstance(payload, dict):
        return _subject_fallback(fallback_title, "subject analysis returned non-object")
    try:
        analysis = subject_analysis_from_dict(payload)
        return analysis.as_dict()
    except Exception:
        return _subject_fallback(fallback_title, "subject analysis failed validation")


def _subject_fallback(fallback_title: str, reason: str) -> dict[str, Any]:
    return {
        "sellable_subject": fallback_title or "商品主体",
        "subject_explanation": reason,
        "visible_attributes": [],
        "excluded_elements": [],
        "confidence": "low",
        "uncertainty_reason": reason,
        "explicit_measurements": {},
    }


def generate_combo_text(prompt: str) -> dict[str, Any]:
    """组合套装文本：标题 + 详情描述 + 五点特性。strict JSON 合同。

    成功才返回内容；失败抛 ComboKitValidationError（由调用方决定扣费成败）。
    """
    client = _ark_client()
    schema_prompt = (
        f"{prompt}\n\n"
        "Respond with exactly one JSON object and no Markdown, no extra text:\n"
        '{"title": "...", "description": "...", "bullets": ["...","...","...","...","..."]}'
    )
    try:
        content = client.complete([{"role": "user", "content": schema_prompt}])
    except DoubaoArkError as exc:
        raise ComboKitValidationError(f"文本生成失败：{str(exc)[:200]}") from exc
    payload = _parse_text_json(content)
    return payload


def _parse_text_json(content: str) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ComboKitValidationError("文本生成返回非严格 JSON") from exc
    if not isinstance(payload, dict):
        raise ComboKitValidationError("文本生成返回非对象")
    title = str(payload.get("title") or "").strip()
    description = str(payload.get("description") or "").strip()
    bullets = payload.get("bullets") or []
    if not isinstance(bullets, list):
        bullets = []
    cleaned = [str(item).strip() for item in bullets if str(item).strip()]
    if not title:
        raise ComboKitValidationError("文本生成缺少标题")
    return {
        "title": title[:500],
        "description": description[:3000],
        "bullets": cleaned[:5],
    }


def _local_image_data_url(path: str) -> str:
    """读取本地图片并转成 base64 data URL（POD 主图/主体识别的传图方式）。

    方舟上游无法访问本机路径或带鉴权的路由地址，因此必须内嵌图片数据。
    文件不存在或读取失败时返回空串，调用方据此省略图像输入并回退到主体词。
    """
    raw = str(path or "").strip()
    if not raw:
        return ""
    file_path = Path(raw)
    if not file_path.is_file():
        return ""
    content_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(file_path.suffix.lower(), "image/jpeg")
    try:
        content = file_path.read_bytes()
    except OSError:
        return ""
    if not content:
        return ""
    return f"data:{content_type};base64," + base64.b64encode(content).decode("ascii")


def _ark_client() -> DoubaoArkClient:
    return DoubaoArkClient()


__all__ = [
    "ComboKitAiRuntime",
    "analyze_subject_with_mask",
    "generate_combo_text",
]
