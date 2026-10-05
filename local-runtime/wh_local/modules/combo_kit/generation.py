"""combo_kit 生图：复用 product_processing 的 ProductImageProcessor 并行产出成品图。

本模块禁止 OCR 校验：不调用 inspect_visible_text；单图直出，无四宫格裁切。

成品图构成（第 1 张「套装主图」由主体解析阶段的融合主图复用，不入本模块）：
- 使用场景图 1 / 使用场景图 2 / 白底尺寸图 / 细节图：并行调用生图 API（最多并发 4）。
- 详情图：本地拼接合成（用主图 + 上述成品图），不调用生图 API。
"""
from __future__ import annotations

import io
import random
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from ..product_processing.infrastructure.media import (
    MediaConfigurationError,
    MediaProcessingError,
    ProductImageProcessor,
)
from ..product_processing.service import (
    ProductProcessingService,
)
from .contracts import (
    FUSION_MAIN_ROLE,
    GENERATED_API_ROLES,
    IMAGE_ROLES,
    MAX_IMAGES,
    ComboKitValidationError,
)


def crop_subject_references(sources: list[dict[str, Any]], *, normalize: bool = False) -> list[str]:
    """按用户蒙版把每张原图抠出主体，返回可用于生图参考的本地图片路径。

    sources 每项：{"path": 原图路径, "points": 归一化六点 [[x,y],...], "inverted": bool}。
    抠图规则：默认保留多边形内主体（反选则保留多边形外），其余区域填充纯白背景，
    确保生图 provider 拿到的是「已按框选主体的图」，而不是整张原图。
    normalize=True（单品多视角）时额外把主体裁成固定占比的 1:1 画布，提供统一尺度锚点。
    任一抠图失败则回退到原图路径。
    """
    references: list[str] = []
    for source in sources:
        raw = str(source.get("path") or "")
        if not raw:
            continue
        try:
            cropped = _crop_to_mask(raw, source.get("points"), bool(source.get("inverted")), normalize=normalize)
            if cropped:
                references.append(cropped)
                continue
        except Exception:
            pass
        references.append(raw)
    return references


# 归一化后主体在画布中的占比：留约三成白边，既不贴边也不缩小到失去细节。
_SUBJECT_FRAME_RATIO = 0.7


def _normalize_subject_frame(canvas: Image.Image, selection: Image.Image) -> Image.Image:
    """把抠图结果按主体包围盒紧裁后放进 1:1 白底画布，主体约占 70%。

    同一件商品的原图机位、构图、占比各不相同，原样送参考会让模型缺少统一尺度锚点，
    同一件商品在成品图里被拉长压扁、弧度不一致。统一「主体包围盒 -> 固定占比」后，
    每张参考图的主体尺度与画面占比一致，模型才有稳定的比例与曲率参照。
    """
    # 软化后的蒙版边缘有过渡灰阶，按 128 阈值取包围盒，避免把羽化外圈算成主体。
    binary = selection.point(lambda value: 255 if value >= 128 else 0)
    bbox = binary.getbbox()
    if not bbox:
        return canvas
    left, top, right, bottom = bbox
    width, height = canvas.size
    # 蒙版是用户手绘多边形，可能略微画进主体内部；向外留一成余量避免裁掉主体边缘。
    pad_x = round((right - left) * 0.1)
    pad_y = round((bottom - top) * 0.1)
    subject = canvas.crop(
        (max(0, left - pad_x), max(0, top - pad_y), min(width, right + pad_x), min(height, bottom + pad_y))
    )
    subject_w, subject_h = subject.size
    if subject_w <= 0 or subject_h <= 0:
        return canvas
    side = max(1, round(max(subject_w, subject_h) / _SUBJECT_FRAME_RATIO))
    frame = Image.new("RGB", (side, side), (255, 255, 255))
    frame.paste(subject, ((side - subject_w) // 2, (side - subject_h) // 2))
    return frame


def _crop_to_mask(raw_path: str, points: Any, inverted: bool, *, normalize: bool = False) -> str | None:
    image = Image.open(raw_path).convert("RGB")
    width, height = image.size
    selection = Image.new("L", (width, height), 0)
    has_polygon = isinstance(points, (list, tuple)) and len(points) >= 3
    if has_polygon:
        polygon = [(float(point[0]) * width, float(point[1]) * height) for point in points]
        ImageDraw.Draw(selection).polygon(polygon, fill=255)
    else:
        selection.paste(255, (0, 0, width, height))
    if inverted:
        selection = ImageOps.invert(selection)
    if has_polygon:
        # 多边形边界是硬边：直接涂白会在生成物上切出笔直斜切口，因此只做羽化
        # （GaussianBlur）让主体柔和过渡到白底。不做 MinFilter 内收：内收会把
        # 主体边缘一圈像素按部分透明度与白底混合，留下肉眼可见的浅灰重影/光晕。
        radius = max(1, min(round(min(width, height) * 0.005), 12))
        selection = selection.filter(ImageFilter.GaussianBlur(radius=max(1.0, radius * 1.5)))
    canvas = Image.new("RGB", (width, height), (255, 255, 255))
    canvas.paste(image, (0, 0), selection)
    # 单品多视角要在多张成品图之间保持同一比例与弧度：把参考图统一成
    # 「主体占画布固定比例」的 1:1 白底图，模型才有一致的尺度锚点。
    # 套装（bundle）不做归一化：成员商品之间的真实大小关系本身就是参考信息。
    if normalize and has_polygon:
        canvas = _normalize_subject_frame(canvas, selection)
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    try:
        canvas.save(handle.name, "PNG")
    finally:
        handle.close()
    return handle.name

def _static_config() -> dict[str, Any]:
    """共享媒体配置 + 组合套装专用参考图上限。

    共享配置只为 grid_image / detail_image 提供了参考图上限，套装角色
    （main、使用场景图、白底尺寸图、细节图）没有对应配置会退化到默认 2 张，
    导致 3 件以上套装的成员商品无法全部进入生图请求。这里按 MAX_IMAGES 放开。
    """
    config = dict(ProductProcessingService._media_config_provider() or {})
    limits = dict(config.get("limits") or {})
    for role in (*GENERATED_API_ROLES, FUSION_MAIN_ROLE):
        limits[f"{role}_reference_max_count"] = MAX_IMAGES
    limits["reference_max_cap"] = MAX_IMAGES
    config["limits"] = limits
    return config


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
    roles: Iterable[str] | None = None,
    title: str = "",
    category: str = "",
    references_by_role: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """并行生成整套成品图中的生图部分 + 本地拼接详情图。

    返回顺序与 IMAGE_ROLES 一致（不含 main），即
    [carousel_2, carousel_3, white_bg, detail_shot, detail_page]。
    传入 roles 时只生成指定角色（单张重做/替换），并跳过详情图合成，
    用于避免二次生成覆盖其它图。
    references_by_role 为单品多视角按角色分配的参考图（同一商品的不同机位先按角色
    筛选过）：把 4 个机位无差别全送给每个角色，模型会在机位之间「平均」出一个真机
    不存在的造型。未命中的角色回退到 reference_values（全部参考图）。
    任一张生图失败抛 ComboKitValidationError（调用方据此结算生图失败）。
    """
    processor = media_processor or _make_media_processor()
    outputs: dict[str, dict[str, Any]] = {}
    per_role_references = references_by_role or {}

    def _generate_one(role: str, label: str, prompt: str) -> dict[str, Any]:
        if not prompt:
            raise ComboKitValidationError(f"{label} 缺少辅助 Prompt")
        media = processor.generate(
            stage=role,
            prompt=prompt,
            reference_values=per_role_references.get(role) or reference_values,
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

    # 并发生图：使用场景图 1/2、白底尺寸图、细节图（并发上限 4）。
    # 传入 roles 时只生成指定的生图角色（单张重做/替换），其余角色保留不动。
    label_map = {str(spec["role"]): str(spec["label"]) for spec in IMAGE_ROLES}
    target_roles = set(roles) if roles else set(GENERATED_API_ROLES)
    api_roles = [role for role in GENERATED_API_ROLES if role in target_roles]
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(_generate_one, role, label_map[role], str(prompts.get(role) or "")): role
            for role in api_roles
        }
        for future in as_completed(futures):
            role = futures[future]
            try:
                outputs[role] = future.result()
            except (MediaConfigurationError, MediaProcessingError, ComboKitValidationError) as exc:
                raise ComboKitValidationError(
                    f"{label_map.get(role, role)} 生成失败：{str(exc)[:200]}"
                ) from exc

    # 详情图：仅在全量生成（未指定 roles）时本地拼接（主图 + 上述成品图），不调用生图 API。
    # 单张替换时重算详情图会覆盖其它已有图，故跳过。
    if roles:
        ordered = [str(spec["role"]) for spec in IMAGE_ROLES if str(spec["role"]) in GENERATED_API_ROLES]
    else:
        ordered = [str(spec["role"]) for spec in IMAGE_ROLES if str(spec["role"]) in GENERATED_API_ROLES]
        detail_page = _compose_detail_page(
            fusion_content, outputs, ordered, fusion_suffix=fusion_suffix,
            title=title, category=category,
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
    title: str = "",
    category: str = "",
) -> bytes | None:
    """把主图 + 已生成的成品图用 Pillow 拼成一张 1024×1024 详情海报。

    复用老 AI 处理模块的本地拼接模板卡池（D 极简白底海报 / E 圆形拼贴 /
    F 混合形状蒙版），每张详情图随机抽取一种版式；不调用生图 API。
    素材不足（<2 张）返回 None。
    """
    sources: list[bytes] = []
    if fusion_content:
        sources.append(fusion_content)
    for role in ordered:
        content = outputs.get(role, {}).get("content")
        if content:
            sources.append(content)
    sources = sources[:4]
    if len(sources) < 2:
        return None
    try:
        images = [Image.open(io.BytesIO(content)).convert("RGB") for content in sources]
    except Exception:
        return None
    while len(images) < 4:
        images.append(images[len(images) % len(images)])

    target = 1024

    def load_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
        candidates = [
            "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        ]
        for candidate in candidates:
            try:
                return ImageFont.truetype(candidate, size)
            except Exception:
                continue
        return ImageFont.load_default()

    def cover(image: Image.Image, box_w: int, box_h: int) -> Image.Image:
        ratio = max(box_w / image.width, box_h / image.height)
        resized = image.resize(
            (max(1, int(image.width * ratio)), max(1, int(image.height * ratio))),
            Image.Resampling.LANCZOS,
        )
        left = max((resized.width - box_w) // 2, 0)
        top = max((resized.height - box_h) // 2, 0)
        return resized.crop((left, top, left + box_w, top + box_h))

    def paste_rounded(
        base: Image.Image, image: Image.Image, box: tuple[int, int, int, int], radius: int = 22
    ) -> None:
        part = cover(image, box[2] - box[0], box[3] - box[1])
        mask = Image.new("L", part.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, part.width, part.height), radius=radius, fill=255)
        base.paste(part, box[:2], mask)

    def shape_mask(size: int, shape: str) -> Image.Image:
        mask = Image.new("L", (size, size), 0)
        shape_draw = ImageDraw.Draw(mask)
        if shape == "circle":
            shape_draw.ellipse((0, 0, size - 1, size - 1), fill=255)
        elif shape == "square":
            shape_draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=int(size * 0.18), fill=255)
        elif shape == "diamond":
            shape_draw.polygon(
                [(size / 2, 0), (size - 1, size / 2), (size / 2, size - 1), (0, size / 2)], fill=255
            )
        return mask

    def paste_shaped(
        base: Image.Image, image: Image.Image, center: tuple[int, int], size: int, shape: str, ring: int = 10
    ) -> None:
        cx, cy = center
        mask = shape_mask(size, shape)
        base.paste(
            Image.new("RGB", (size, size), (255, 255, 255)),
            (cx - size // 2, cy - size // 2),
            mask,
        )
        inner = size - 2 * ring
        part = cover(image, inner, inner)
        base.paste(part, (cx - inner // 2, cy - inner // 2), shape_mask(inner, shape))

    def wrap(draw: ImageDraw.ImageDraw, text: str, text_font, max_width: int, max_lines: int) -> list[str]:
        words = str(text).split()
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and draw.textlength(candidate, font=text_font) > max_width:
                lines.append(current)
                current = word
                if len(lines) >= max_lines:
                    break
            else:
                current = candidate
        if current and len(lines) < max_lines:
            lines.append(current)
        return lines or [str(text)[:40]]

    clean_title = re.sub(r"\s+", " ", str(title or "")).strip(" -_|/")
    title_text = clean_title[:96] or "Product Detail"
    category_text = (re.sub(r"\s+", " ", str(category or "")).strip(" -_|/")[:44]) or "Selected Detail"

    def compose_d() -> Image.Image:
        """D 极简白底：标题置顶 + 居中大图 + 底部三小图 + 类目注脚"""
        canvas = Image.new("RGB", (target, target), (255, 255, 255))
        text_draw = ImageDraw.Draw(canvas)
        title_font = load_font(38, bold=True)
        sub_font = load_font(19)
        y = 64
        for line in wrap(text_draw, title_text, title_font, 880, 2):
            text_draw.text((52, y), line, font=title_font, fill=(28, 30, 32))
            y += 44
        text_draw.rounded_rectangle((54, y + 8, 118, y + 16), radius=4, fill=(232, 150, 62))
        y += 38
        text_draw.text((54, y), category_text.upper(), font=sub_font, fill=(120, 123, 124))
        hero_top = 210
        hero_h = 540
        paste_rounded(canvas, images[0], (62, hero_top, target - 62, hero_top + hero_h), 26)
        margin, gap, radius = 62, 20, 18
        thumbs_w = (target - 2 * margin - 2 * gap) // 3
        thumbs_h = target - hero_top - hero_h - 56
        for index in range(3):
            x0 = margin + index * (thumbs_w + gap)
            paste_rounded(
                canvas,
                images[index + 1],
                (x0, hero_top + hero_h + 24, x0 + thumbs_w, hero_top + hero_h + 24 + thumbs_h),
                radius,
            )
        return canvas

    def compose_e() -> Image.Image:
        """E 圆形拼贴：主图居中 + 三张圆形蒙版嵌图（无文字覆盖）"""
        canvas = Image.new("RGB", (target, target), (244, 242, 238))
        canvas.paste(cover(images[0], 820, 820), (102, 102))
        paste_shaped(canvas, images[1], (150, 150), 290, "circle")
        paste_shaped(canvas, images[2], (874, 150), 290, "circle")
        paste_shaped(canvas, images[3], (512, 950), 290, "circle")
        return canvas

    def compose_f() -> Image.Image:
        """F 混合形状：主图居中 + 圆形/圆角方形/菱形蒙版嵌图（无文字覆盖）"""
        canvas = Image.new("RGB", (target, target), (244, 242, 238))
        canvas.paste(cover(images[0], 820, 820), (102, 102))
        paste_shaped(canvas, images[1], (150, 150), 300, "circle")
        paste_shaped(canvas, images[2], (874, 150), 300, "square")
        paste_shaped(canvas, images[3], (512, 950), 320, "diamond")
        return canvas

    compositor = {"D": compose_d, "E": compose_e, "F": compose_f}[random.choice(("D", "E", "F"))]
    canvas = compositor()
    output = io.BytesIO()
    canvas.save(output, "JPEG", quality=92)
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
