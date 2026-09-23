"""combo_kit AI 运行时：组合套装文本生成 + 6 张单图直连生成 + 主体解析。

复用底层工具（product_processing.doubao_text / doubao_vision / media），
但 Prompt 组装、图片角色、产出合同均独立于老 AI 处理模块。
本模块关闭 OCR 文字质检：不调用任何 inspect_visible_text。
"""
from __future__ import annotations

import base64
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..product_processing.doubao_ark import DoubaoArkClient, DoubaoArkError
from ..product_processing.infrastructure.media import ProductImageProcessor
from .contracts import DEFAULT_GENERATION_MODE, IMAGE_ROLES, ComboKitValidationError
from .prompts import VIEW_LABEL_SET, VIEW_OTHER

# 视角识别：单品多视角选型的参考图是同一商品的不同机位，但机位信息过去从未进入提示词
# （主图提示词里拿到的只是各图的商品名），模型只能自己在多张图里挑一个角度，于是俯视/
# 底部图完全没用上、所有成品图反复回到同一机位。因此单独做一次轻量视觉识别，把每张图
# 的机位落库，供后续「按角色分配参考图」与「指定输出视角」使用。
VIEW_CLASSIFY_PROMPT = """You are given several product photos of the SAME single product, in a fixed order.
For EACH photo, identify the camera angle the product is photographed from.
Use ONLY one of these exact labels:
- "front view"         (looking straight at the product's front face)
- "back view"          (looking at the side opposite the front face)
- "left side view"     (straight side profile: the product's left side faces the camera)
- "right side view"    (straight side profile: the product's right side faces the camera)
- "top view"           (looking straight down onto the product's top surface)
- "bottom view"        (the underside is clearly visible)
- "three-quarter view" (an angled perspective showing the front and one side together)
- "other view"         (cannot be determined, or none of the above fits)
Ignore background, props, hands, packaging, logos and text. Judge only the product's orientation.
Return exactly one JSON object with no Markdown and no extra text:
{"views": ["<label for image 1>", "<label for image 2>", ...]}
The array must contain exactly one label per supplied image, in the same order.
"""

# 单次机位识别的图片上限：一张套装最多几张来源图，超出部分不再识别（退化为无标签）。
MAX_VIEW_IMAGES = 8
# 机位识别缩略图边长：机位判断不需要细节，缩略图可避免多图 base64 撑爆请求体。
VIEW_THUMBNAIL_SIDE = 512


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

    # ---- 机位识别（单品多视角：给每张来源图打视角标签） ----
    def classify_view_labels(self, *, image_paths: list[str]) -> list[str]:
        return classify_view_labels(image_paths=image_paths)

    # ---- 文本生成（标题 + 详情描述 + 五点） ----
    def generate_text(self, *, prompt: str) -> dict[str, Any]:
        result = generate_combo_text(prompt)
        return result

    # ---- 套装主图（主体解析后立即生成，作为第 1 张成品图复用） ----
    # bundle：多件商品融合成一张套装主图；multiview：同一商品多视角直接出商品主图。
    def generate_fusion_main(
        self,
        *,
        reference_values: list[str],
        set_name: str,
        subject_summaries: list[str],
        view_labels: list[str] | None = None,
        output_view: str = "",
        primary_subject: str = "",
        custom_prompt: str = "",
        mode: str = DEFAULT_GENERATION_MODE,
    ) -> dict[str, Any]:
        from .generation import _make_media_processor
        from .prompts import build_fusion_main_prompt, build_multiview_main_prompt

        processor = self._media or _make_media_processor()
        if not processor:
            raise ComboKitValidationError("套装主图处理器不可用")
        if mode == "multiview":
            # 主图只送该角色匹配的视角（reference_values 已按角色筛过），机位清单必须与
            # 送出的参考图一一对应，否则模型会把机位张冠李戴。
            prompt = build_multiview_main_prompt(
                set_name=set_name,
                reference_views=list(view_labels or []),
                output_view=output_view,
                custom_prompt=custom_prompt,
            )
        else:
            prompt = build_fusion_main_prompt(
                set_name=set_name,
                subject_summaries=subject_summaries,
                primary_subject=primary_subject,
                custom_prompt=custom_prompt,
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
        title: str = "",
        category: str = "",
        roles: list[str] | None = None,
        references_by_role: dict[str, list[str]] | None = None,
    ) -> list[dict[str, Any]]:
        from .generation import generate_combo_images

        return generate_combo_images(
            media_processor=self._media,
            reference_values=reference_values,
            references_by_role=references_by_role,
            prompts=prompts,
            fusion_content=fusion_content,
            fusion_suffix=fusion_suffix,
            set_id=set_id,
            workspace_id=workspace_id,
            title=title,
            category=category,
            roles=roles,
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


def classify_view_labels(*, image_paths: list[str]) -> list[str]:
    """识别每张来源图的拍摄机位，返回与 image_paths 等长的机位标签列表。

    仅单品多视角选型使用：机位标签让「按角色分配参考图」与「指定输出视角」成为可能。
    任何一步失败（图片读不出、视觉通道不可用、返回长度不符、标签非法）都返回空列表，
    调用方退化为「不按视角分配参考图」的旧行为，绝不阻断生图主流程。
    """
    paths = [str(path or "").strip() for path in image_paths][:MAX_VIEW_IMAGES]
    if not paths:
        return []
    data_urls = [_thumbnail_data_url(path) for path in paths]
    if any(not value for value in data_urls):
        return []
    content: list[dict[str, Any]] = [{"type": "text", "text": VIEW_CLASSIFY_PROMPT}]
    content.extend(
        {"type": "image_url", "image_url": {"url": value}} for value in data_urls
    )
    try:
        reply = _ark_client().complete([{"role": "user", "content": content}])
    except DoubaoArkError:
        return []
    return _parse_view_labels(reply, expected=len(data_urls))


def _parse_view_labels(content: str, *, expected: int) -> list[str]:
    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    views = payload.get("views")
    # 长度不符说明模型漏答/多答，机位与图片的对应关系已不可信，整体作废。
    if not isinstance(views, list) or len(views) != expected:
        return []
    labels: list[str] = []
    for value in views:
        label = str(value or "").strip().lower()
        labels.append(label if label in VIEW_LABEL_SET else VIEW_OTHER)
    return labels


def _thumbnail_data_url(path: str, max_side: int = VIEW_THUMBNAIL_SIDE) -> str:
    """把本地图片压成小尺寸 JPEG data URL；读不出或不是图片时返回空串。"""
    raw = str(path or "").strip()
    if not raw:
        return ""
    file_path = Path(raw)
    if not file_path.is_file():
        return ""
    try:
        from PIL import Image

        with Image.open(file_path) as image:
            thumbnail = image.convert("RGB")
            thumbnail.thumbnail((max_side, max_side))
            buffer = io.BytesIO()
            thumbnail.save(buffer, format="JPEG", quality=80)
    except Exception:
        return ""
    data = buffer.getvalue()
    if not data:
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


def generate_combo_text(prompt: str) -> dict[str, Any]:
    """组合套装文本：标题 + 详情描述 + 五点特性。strict JSON 合同。

    成功才返回内容；失败抛 ComboKitValidationError（由调用方决定扣费成败）。
    """
    schema_prompt = (
        f"{prompt}\n\n"
        "Respond with exactly one JSON object and no Markdown, no extra text:\n"
        '{"title": "...", "description": "...", "bullets": ["...","...","...","...","..."]}'
    )
    try:
        client = _ark_client()
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
        "description": description[:320],
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
    "classify_view_labels",
    "generate_combo_text",
]
