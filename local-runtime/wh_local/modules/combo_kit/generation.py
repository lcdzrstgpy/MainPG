"""combo_kit 生图：复用 product_processing 的 ProductImageProcessor 并行产出成品图。

本模块禁止 OCR 校验：不调用 inspect_visible_text；单图直出，无四宫格裁切。

成品图构成（第 1 张「套装主图」由主体解析阶段的融合主图复用，不入本模块）：
- 轮播图 2 / 轮播图 3 / 白底尺寸图 / 细节图：并行调用生图 API（最多并发 4）。
- 详情图：本地拼接合成（用主图 + 上述成品图），不调用生图 API。
"""
from __future__ import annotations

import io
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from PIL import Image, ImageDraw, ImageOps

from ..product_processing.infrastructure.media import (
    MediaConfigurationError,
    MediaProcessingError,
    ProductImageProcessor,
)
from ..product_processing.service import (
    ProductProcessingService,
)
from .contracts import (
    GENERATED_API_ROLES,
    IMAGE_ROLES,
    ComboKitValidationError,
)


def crop_subject_references(sources: list[dict[str, Any]]) -> list[str]:
    """按用户蒙版把每张原图抠出主体，返回可用于生图参考的本地图片路径。

    sources 每项：{"path": 原图路径, "points": 归一化六点 [[x,y],...], "inverted": bool}。
    抠图规则：默认保留多边形内主体（反选则保留多边形外），其余区域填充纯白背景，
    确保生图 provider 拿到的是「已按框选主体的图」，而不是整张原图。
    任一抠图失败则回退到原图路径。
    """
    references: list[str] = []
    for source in sources:
        raw = str(source.get("path") or "")
        if not raw:
            continue
        try:
            cropped = _crop_to_mask(raw, source.get("points"), bool(source.get("inverted")))
            if cropped:
                references.append(cropped)
                continue
        except Exception:
            pass
        references.append(raw)
    return references


def _crop_to_mask(raw_path: str, points: Any, inverted: bool) -> str | None:
    image = Image.open(raw_path).convert("RGB")
    width, height = image.size
    selection = Image.new("L", (width, height), 0)
    if isinstance(points, (list, tuple)) and len(points) >= 3:
        polygon = [(float(point[0]) * width, float(point[1]) * height) for point in points]
        ImageDraw.Draw(selection).polygon(polygon, fill=255)
    else:
        selection.paste(255, (0, 0, width, height))
    if inverted:
        selection = ImageOps.invert(selection)
    canvas = Image.new("RGB", (width, height), (255, 255, 255))
    canvas.paste(image, (0, 0), selection)
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    try:
        canvas.save(handle.name, "PNG")
    finally:
        handle.close()
    return handle.name

_static_config = ProductProcessingService._media_config_provider


def _make_media_processor() -> ProductImageProcessor:
    # 复用老模块的 AI provider 配置解析（直连/托管双轨），保证密钥与模型一致。
    return ProductImageProcessor(config_provider=_static_config)


def generate_combo_images(
    *,
    media_processor: ProductImageProcessor | None,
    prompts: dict[str, str],
    reference_values: list[str],
    fusion_content: bytes | None,
    fusion_suffix: str,
    set_id: str,
    workspace_id: str,
) -> list[dict[str, Any]]:
    """并行生成整套成品图中的生图部分 + 本地拼接详情图。

    返回顺序与 IMAGE_ROLES 一致（不含 main），即
    [carousel_2, carousel_3, white_bg, detail_shot, detail_page]。
    任一张生图失败抛 ComboKitValidationError（调用方据此结算生图失败）。
    """
    processor = media_processor or _make_media_processor()
    outputs: dict[str, dict[str, Any]] = {}

    def _generate_one(role: str, label: str, prompt: str) -> dict[str, Any]:
        if not prompt:
            raise ComboKitValidationError(f"{label} 缺少辅助 Prompt")
        media = processor.generate(
            stage=role,
            prompt=prompt,
            reference_values=reference_values,
            image_size="2048x2048",
        )
        # 仅做尺寸归一到方图，不做 OCR 文字质检。
        normalized = processor.normalize_standalone_image(media, stage=role)
        return {
            "role": role,
            "label": label,
            "content": bytes(getattr(normalized, "content", b"") or b""),
            "suffix": str(getattr(normalized, "suffix", ".jpg") or ".jpg"),
            "provider": str(getattr(normalized, "provider", "") or ""),
            "model": str(getattr(normalized, "model", "") or ""),
            "attempt_count": int(getattr(normalized, "attempt_count", 0) or 0),
            "status_class": str(getattr(normalized, "provider_status_class", "") or "success"),
        }

    # 并发生图：轮播2/3、白底尺寸图、细节图（并发上限 4）。
    label_map = {str(spec["role"]): str(spec["label"]) for spec in IMAGE_ROLES}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(_generate_one, role, label_map[role], str(prompts.get(role) or "")): role
            for role in GENERATED_API_ROLES
        }
        for future in as_completed(futures):
            role = futures[future]
            try:
                outputs[role] = future.result()
            except (MediaConfigurationError, MediaProcessingError, ComboKitValidationError) as exc:
                raise ComboKitValidationError(
                    f"{label_map.get(role, role)} 生成失败：{str(exc)[:200]}"
                ) from exc

    # 详情图：本地拼接（主图 + 上述成品图），不调用生图 API。
    ordered = [str(spec["role"]) for spec in IMAGE_ROLES if str(spec["role"]) in GENERATED_API_ROLES]
    detail_page = _compose_detail_page(
        fusion_content, outputs, ordered, fusion_suffix=fusion_suffix
    )
    if detail_page:
        outputs["detail_page"] = {
            "role": "detail_page",
            "label": label_map.get("detail_page", "详情图"),
            "content": detail_page,
            "suffix": ".jpg",
            "provider": "local-synthesis",
            "model": "pillow",
            "attempt_count": 0,
            "status_class": "success",
        }

    # 按 IMAGE_ROLES 顺序返回。
    return [outputs[role] for role in ordered if role in outputs] + (
        [outputs["detail_page"]] if "detail_page" in outputs else []
    )


def _compose_detail_page(
    fusion_content: bytes | None,
    outputs: dict[str, dict[str, Any]],
    ordered: list[str],
    *,
    fusion_suffix: str,
) -> bytes | None:
    """把主图 + 已生成的成品图用 Pillow 拼成一张 1024×1024 详情海报。

    复用老 AI 处理模块的本地拼接思路（确定性排版）；不调用生图 API。
    """
    sources: list[bytes] = []
    if fusion_content:
        sources.append(fusion_content)
    for role in ordered:
        content = outputs.get(role, {}).get("content")
        if content:
            sources.append(content)
    sources = sources[:5]
    if len(sources) < 2:
        return None
    try:
        images = [_cover(Image.open(io.BytesIO(content)).convert("RGB"), (1024, 512)) for content in sources]
    except Exception:
        return None
    canvas = Image.new("RGB", (1024, 1024), (255, 255, 255))
    canvas.paste(images[0], (0, 0))
    # 其余 4 张铺 2×2 底部区域（不足则留白）。
    tiles = sources[1:5]
    for index, content in enumerate(tiles[:4]):
        try:
            cell = _cover(Image.open(io.BytesIO(content)).convert("RGB"), (512, 256))
        except Exception:
            continue
        canvas.paste(cell, ((index % 2) * 512, 512 + (index // 2) * 256))
    output = io.BytesIO()
    canvas.save(output, "JPEG", quality=90)
    return output.getvalue()


def _cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    target = _contain(image, size)
    canvas = Image.new("RGB", size, (255, 255, 255))
    canvas.paste(target, ((size[0] - target.width) // 2, (size[1] - target.height) // 2))
    return canvas


def _contain(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    ratio = min(size[0] / image.width, size[1] / image.height)
    new_size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
    return image.resize(new_size, Image.LANCZOS)


__all__ = ["generate_combo_images"]
