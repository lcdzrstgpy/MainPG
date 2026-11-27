"""营销图生成管线：分块生成 → 确定性拼版。

2026-11-24 重构：不再让模型"一张图里画出全部内容"。每张营销图 = 若干块：

    block(hero)      整机主图（产品参考 → Pro）
    block(detail×N)  功能细节微距（产品参考 + 短词 → Pro）
    排版块            标题/标签/编号/圆圈/卡片（本地排版层，零失真）

模板只是块的组合 + 一个排版函数。所有块都用 Pro 档（失败自动落 flash），
任何块失败都不拖垮整张图：缺块时版式自动降级（少画几个圈、少放几卡）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .premium_composer import (
    CANVAS_SIZE,
    MarketingCopy,
    compose_band_skeleton,
    compose_callout_ring,
    compose_feature_cards,
    compose_feature_strip,
    compose_marketing_image,
)
from .detail_specs import (
    DetailSpec,
    build_detail_prompt_from_spec,
    crop_reference_bytes,
    detail_crop_usable,
    fallback_detail_specs,
    lift_detail_exposure,
    png_data_url,
    specs_from_callout_parts,
)
from .marketing_gen import build_prompt


def _product_bbox_ratios(hero: bytes) -> tuple[float, float, float, float] | None:
    """商品在 hero 中的包围盒：优先抠图蒙版，失败退回画面启发式。"""
    if not hero:
        return None
    try:
        from .dimension_source_reuse import subject_bbox_ratios  # noqa: PLC0415

        bbox = subject_bbox_ratios(hero)
    except Exception:  # noqa: BLE001
        bbox = None
    if bbox:
        return bbox
    try:
        from .premium_composer import content_bbox_ratios  # noqa: PLC0415

        return content_bbox_ratios(hero)
    except Exception:  # noqa: BLE001
        return None


@dataclass
class BlockPlan:
    """一张营销图的块计划。"""

    template: str
    hero: bool = True
    details: int = 0              # 需要的细节微距块数量（0 = 不要）
    label: str = ""
    # hero 候选数：2 张择优够用（省钱：省 1/3 生图）；标准档 QC 失败还有 Pro
    # 补救通道兜底。白底主图 1 张即可，省掉择优。
    candidates: int = 2


PLANS: dict[str, BlockPlan] = {
    "headline_hero": BlockPlan("headline_hero", hero=True, details=0, label="大字卖点"),
    "action_proof": BlockPlan("action_proof", hero=True, details=0, label="细节特写"),
    "icon_grid": BlockPlan("icon_grid", hero=True, details=0, label="功能图标网格"),
    "material_strip": BlockPlan("material_strip", hero=True, details=0, label="材质适用条"),
    "use_cases": BlockPlan("use_cases", hero=True, details=0, label="场景应用"),
    "side_rail": BlockPlan("side_rail", hero=True, details=0, label="侧栏卖点"),
    "feature_cards": BlockPlan("feature_cards", hero=True, details=4, label="功能细节卡"),
    "feature_strip": BlockPlan("feature_strip", hero=True, details=4, label="功能横条"),
    "callout_ring": BlockPlan("callout_ring", hero=True, details=0, label="编号标注"),
    # A+ 信息图：图 A 卖点+利益点+2×2 场景卡；图 B 细节清单+编号引线+尺寸。
    "infographic_hero": BlockPlan("infographic_hero", hero=True, details=4, label="信息图·卖点场景"),
    "infographic_details": BlockPlan("infographic_details", hero=True, details=5, label="信息图·细节标注"),
    # 白底主图：不叠任何文字，生成后本地抠图 → 纯白 1:1，兼作尺寸画布底图。
    "white_main": BlockPlan("white_main", hero=True, details=0, label="白底主图", candidates=1),
}


def plan_for(template: str) -> BlockPlan:
    return PLANS.get(str(template or ""), PLANS["headline_hero"])


def render_blocks(
    *,
    plan: BlockPlan,
    copy: MarketingCopy,
    refs: list[str],
    render: Callable[[str, list[str], int], bytes],
    specs_factory: Callable[[bytes], list[DetailSpec]] | None = None,
    detail_mode: str = "crop",
    category_id: str = "",
    category_seed: int = 0,
    subject: str = "",
    instructions: str = "",
    skeleton: str = "",
    set_count: int = 1,
    pose: str = "",
    hero_override: bytes | None = None,
    detail_images: list[tuple[bytes, str]] | None = None,
    style_template: str = "auto",
) -> dict[str, Any]:
    """按计划生成块：hero + 细节。``render(prompt, refs)`` 由调用方注入（含 Pro 回落）。

    ``category_id``：商品大类 id（hand_tools / apparel_accessories …），命中时在
    hero 与细节提示词里追加该品类的表面/光线片段；为空则保持旧行为。

    ``subject`` / ``instructions``：视觉确认的主体与操作者注意点，原样写进 hero
    提示词，保证模型画的就是这个商品、并遵守注意点。

    ``skeleton``：本次排版用的结构（S1-S4）。生图提示词会按结构预留文字区，
    否则排版层只能把字缩小。为空时保持旧构图。

    ``specs_factory(hero_bytes)`` 可选：拿到主图后返回结构化的细节描述（含裁剪框）。

    ``detail_mode``：
    - ``crop``（默认）：细节块**直接裁切主图**，不再交给模型重画——像素级保真，
      零畸形，且省掉 N 次生图调用。裁剪不可用时自动退回 ``generate``。
    - ``generate``：用「部位 + 动作 + 构图 + 必须/禁止」的提示词重画，并把裁剪图
      作为第一张参考（比旧版只给营销短词好，但仍有重绘幻觉风险）。
    没有 ``specs_factory`` 时一律退回旧的「营销短词」提示词，行为不变。
    """
    blocks: dict[str, Any] = {"hero": b"", "details": [], "category_id": category_id}
    if hero_override:
        # 多件套：直接用原始干净图合成的 hero，保证每一件都在，不交给 AI 重绘。
        blocks["hero"] = hero_override
    elif plan.hero:
        try:
            # 信息图有自己的构图（产品在右上 / 居中留白），不能被 S1-S4 的
            # 预留区描述顶掉，否则商品位置和版式对不上。
            prompt_skeleton = "" if plan.template.startswith("infographic_") else skeleton
            blocks["hero"] = render(
                build_prompt(
                    plan.template,
                    category_id,
                    category_seed,
                    subject=subject,
                    instructions=instructions,
                    skeleton=prompt_skeleton,
                    set_count=set_count,
                    pose=pose,
                    style_template=style_template,
                ),
                refs,
                max(1, int(plan.candidates)),
            )
        except Exception:  # noqa: BLE001
            blocks["hero"] = b""
    if plan.details:
        if detail_images:
            # 真实原图局部：标签由视觉按图给出，图和字天然一致，不再从 AI 主图裁。
            blocks["details"] = [
                (content, label)
                for content, label in list(detail_images)[: plan.details]
                if content and str(label or "").strip()
            ]
            blocks["detail_source"] = "source"
            return blocks
        mode = str(detail_mode or "crop").strip().lower()
        specs: list[DetailSpec] = []
        if callable(specs_factory) and blocks["hero"]:
            try:
                specs = list(specs_factory(blocks["hero"]) or [])
            except Exception:  # noqa: BLE001 - 结构化失败不能阻断出图
                specs = []
        # 按部位在画面中的高度从上到下排序：细节清单、编号和右侧引线槽位
        # 全部用同一顺序，图上不会出现「1、5、2…」这种乱序编号。
        specs.sort(
            key=lambda spec: (
                (spec.crop[1] + spec.crop[3]) / 2 if spec.crop else 1.0
            )
        )
        from_specs = bool(specs)
        fallback_used = False
        labels = [
            str(item).strip()
            for item in (getattr(copy, "bullets", None) or [])
            if str(item or "").strip()
        ]
        if not from_specs and callable(specs_factory):
            # 视觉没标出部位坐标：沿商品真实包围盒切段兜底，信息图/功能细节卡
            # 不再因为一次视觉调用失败而整张不出，裁切也不会切到纯背景。
            specs = fallback_detail_specs(
                labels,
                limit=plan.details,
                bbox=_product_bbox_ratios(blocks["hero"]),
            )
            from_specs = bool(specs)
            fallback_used = from_specs
        if not from_specs:
            # 调用方没提供部位识别能力：仍然只生 hero，不按营销短词自由重画
            # （那种特写经常是歪的/不对位的，拼进去反而毁图）。
            blocks["detail_source"] = "label"
            return blocks
        # 部位中心点（0-1000）留给信息图的编号引线：同一批坐标既裁细节又画标注，
        # 不再重复付一次视觉调用。
        blocks["callout_parts"] = [
            {
                "label": spec.display_label,
                "x": (spec.crop[0] + spec.crop[2]) / 2 * 1000,
                "y": (spec.crop[1] + spec.crop[3]) / 2 * 1000,
            }
            for spec in specs
            if spec.crop
        ]
        cropped_count = 0
        fallback_specs: list[DetailSpec] = []
        for index, spec in enumerate(specs[: plan.details]):
            cropped = crop_reference_bytes(blocks["hero"], spec.crop) if spec.crop else b""
            if mode == "crop" and cropped:
                if detail_crop_usable(cropped):
                    blocks["details"].append(
                        (lift_detail_exposure(cropped), spec.display_label)
                    )
                    cropped_count += 1
                    continue
                # 裁到纯背景/太暗：换成商品包围盒里的兜底区域，避免出现空黑卡。
                if not fallback_specs:
                    fallback_specs = fallback_detail_specs(
                        labels,
                        limit=plan.details,
                        bbox=_product_bbox_ratios(blocks["hero"]),
                    )
                alt = fallback_specs[index].crop if index < len(fallback_specs) else None
                alt_bytes = crop_reference_bytes(blocks["hero"], alt) if alt else b""
                if alt_bytes and detail_crop_usable(alt_bytes):
                    blocks["details"].append(
                        (lift_detail_exposure(alt_bytes), spec.display_label)
                    )
                    cropped_count += 1
                continue
            prompt = build_detail_prompt_from_spec(spec)
            block_refs = refs
            if cropped:
                # 裁剪图放第一位：让模型优先照着真实结构画，而不是照着整机猜。
                block_refs = [png_data_url(cropped), *refs]
            try:
                content = render(prompt, block_refs, 1)
            except Exception:  # noqa: BLE001
                continue
            if content:
                blocks["details"].append(
                    (lift_detail_exposure(content), spec.display_label)
                )
        if fallback_used and cropped_count:
            blocks["detail_source"] = "fallback-crop"
        else:
            blocks["detail_source"] = "crop" if cropped_count else "spec"
    return blocks


def compose(
    *,
    plan: BlockPlan,
    copy: MarketingCopy,
    blocks: dict[str, Any],
    callout_parts: list[dict] | None = None,
    engine_notes: list[str] | None = None,
    skeleton: str = "",
    contain: bool = False,
    style_template: str = "auto",
) -> bytes | None:
    """拼版：按模板 + 结构选排版函数；缺块时自动降级。

    ``skeleton`` 非空时走「多结构骨架」（S1-S4，HTML 优先）；
    浏览器不可用时统一回退到 PIL 的 S1 上下分栏。
    为空时保持旧行为（用于单独调用与老测试）。
    ``contain=True``：成图终检发现商品被裁后重排，照片整图完整缩放不裁切。
    """
    hero: bytes = blocks.get("hero") or b""
    details: list[tuple[bytes, str]] = list(blocks.get("details") or [])
    detail_source = str(blocks.get("detail_source") or "")
    category_id = str(blocks.get("category_id") or "")

    if plan.template in {"infographic_hero", "infographic_details"}:
        # 信息图是独立的多区块版式，不参与 S1-S4 骨架与「细节缺坐标就跳过」的规则。
        # 没有可用的真实细节图时，「细节标注」降级成卖点信息图，不硬凑空标注。
        kind = plan.template
        if kind == "infographic_details" and not details:
            kind = "infographic_hero"
            if engine_notes is not None:
                engine_notes.append("infographic_details:fallback:infographic_hero")
        return _compose_infographic(
            kind=kind,
            hero=hero,
            copy=copy,
            details=details,
            callout_parts=list(callout_parts or []),
            category_id=category_id,
            engine_notes=engine_notes,
            style_template=style_template,
        )

    if plan.details and detail_source == "label" and not skeleton:
        # 视觉模型没标出部位坐标、也没有骨架可补内容时，细节块是模型按短词自由
        # 重画的（缺块图常见「歪的/不对位」的特写）。与其把废图拼进成品，不如
        # 这张版式直接不出。有骨架时改由骨架用短词/图标卡补齐，不会再出废特写。
        if engine_notes is not None:
            engine_notes.append(f"{plan.template}:skipped:no-part-coords")
        return None

    if skeleton and plan.template != "white_main" and hero:
        # 细节卡缺位时不再整张不出：骨架会用短词/图标卡补齐内容，
        # 用户看到的是一张完整的图，而不是被砍掉的版式。
        rendered = _compose_skeleton_html(
            template=plan.template,
            skeleton=skeleton,
            hero=hero,
            copy=copy,
            details=details,
            callout_parts=list(callout_parts or []),
            category_id=category_id,
            contain=contain,
            style_template=style_template,
        )
        if rendered:
            if engine_notes is not None:
                engine_notes.append(f"{plan.template}:{skeleton}:html")
            return rendered
        if engine_notes is not None:
            engine_notes.append(f"{plan.template}:{skeleton}:pil-fallback")
        return compose_band_skeleton(
            hero,
            plan.template,
            copy,
            details,
            list(callout_parts or []),
            contain=contain,
        )

    if plan.template == "feature_strip":
        if not hero or not details:
            return None
        return compose_feature_strip(hero, details, copy)
    if plan.template == "feature_cards":
        if not hero or len(details) < 1:
            return None
        rendered = _compose_feature_cards_html(hero, details, copy, category_id)
        if rendered:
            if engine_notes is not None:
                engine_notes.append("feature_cards:html")
            return rendered
        if engine_notes is not None:
            engine_notes.append("feature_cards:pil")
        return compose_feature_cards(hero, details, copy)
    if plan.template == "callout_ring":
        if not hero:
            return None
        return compose_callout_ring(hero, list(callout_parts or []), copy)
    if plan.template == "white_main":
        # 纯白底主图：优先本地抠图（u2netp，随包分发）→ 商品边缘独立、白底必然 255；
        # 模型缺失时退回「近白背景归一化」；两者都不成立就不出图，宁缺毋滥。
        if not hero:
            return None
        cleaned = None
        source = ""
        try:
            from .dimension_source_reuse import clean_product_canvas  # noqa: PLC0415

            cleaned = clean_product_canvas(hero, side=CANVAS_SIZE, model_name="u2netp")
            if cleaned:
                source = "cutout"
        except Exception:  # noqa: BLE001 - 抠图失败不能阻断出图
            cleaned = None
        if not cleaned:
            try:
                from .white_main import white_background_main  # noqa: PLC0415

                cleaned = white_background_main(hero, side=CANVAS_SIZE)
                if cleaned:
                    source = "normalized"
            except Exception:  # noqa: BLE001
                cleaned = None
        if not cleaned:
            if engine_notes is not None:
                engine_notes.append("white_main:no-white-background")
            return None
        if engine_notes is not None:
            engine_notes.append(f"white_main:{source}")
        return cleaned
    if not hero:
        return None
    rendered = _compose_marketing_html(hero, plan.template, copy, category_id)
    if rendered:
        if engine_notes is not None:
            engine_notes.append(f"{plan.template}:html")
        return rendered
    if engine_notes is not None:
        engine_notes.append(f"{plan.template}:pil")
    return compose_marketing_image(hero, plan.template, copy)


def _compose_marketing_html(
    hero: bytes, template: str, copy: MarketingCopy, category_id: str = ""
) -> bytes:
    """HTML/CSS 版「照片+文案」版式；任何失败返回空 bytes 让调用方回退 PIL。"""
    try:
        from .html_composer import LAYOUT_TEMPLATES, render_marketing  # noqa: PLC0415
        from .premium_composer import filter_copy  # noqa: PLC0415

        if template not in LAYOUT_TEMPLATES:
            return b""
        return render_marketing(
            template=template,
            hero=hero,
            headline=filter_copy(copy.headline),
            subline=filter_copy(copy.subline),
            bullets=[filter_copy(item) for item in (copy.bullets or [])],
            category_id=category_id,
        )
    except Exception:  # noqa: BLE001 - 排版引擎问题绝不能阻断出图
        return b""


def _compose_infographic(
    *,
    kind: str,
    hero: bytes,
    copy: MarketingCopy,
    details: list[tuple[bytes, str]],
    callout_parts: list[dict],
    category_id: str = "",
    engine_notes: list[str] | None = None,
    style_template: str = "auto",
) -> bytes:
    """信息图（A+ 风格）渲染；浏览器不可用返回空 bytes，调用方跳过该图。"""

    if not hero:
        return b""
    try:
        from .infographic_composer import render_infographic  # noqa: PLC0415
        from .premium_composer import filter_copy  # noqa: PLC0415

        cleaned = MarketingCopy(
            headline=filter_copy(copy.headline),
            subline=filter_copy(copy.subline),
            bullets=[filter_copy(item) for item in (copy.bullets or [])],
        )
        rendered = render_infographic(
            kind,
            hero=hero,
            copy=cleaned,
            details=details,
            callout_parts=callout_parts,
            category_id=category_id,
            style_template=style_template,
        )
    except Exception:  # noqa: BLE001 - 排版引擎问题不能阻断整批出图
        rendered = b""
    if rendered and engine_notes is not None:
        engine_notes.append(f"{kind}:html")
    return rendered


def _compose_skeleton_html(
    *,
    template: str,
    skeleton: str,
    hero: bytes,
    copy: MarketingCopy,
    details: list[tuple[bytes, str]],
    callout_parts: list[dict],
    category_id: str = "",
    contain: bool = False,
    style_template: str = "auto",
) -> bytes:
    """HTML/CSS 版多结构骨架；任何失败返回空 bytes 让调用方回退 PIL。"""
    try:
        from .html_composer import render_skeleton  # noqa: PLC0415
        from .premium_composer import filter_copy  # noqa: PLC0415

        return render_skeleton(
            template=template,
            skeleton=skeleton,
            hero=hero,
            headline=filter_copy(copy.headline),
            subline=filter_copy(copy.subline),
            bullets=[filter_copy(item) for item in (copy.bullets or [])],
            details=details,
            callout_parts=callout_parts,
            category_id=category_id,
            contain=contain,
            style_template=style_template,
        )
    except Exception:  # noqa: BLE001 - 排版引擎问题绝不能阻断出图
        return b""


def _compose_feature_cards_html(
    hero: bytes,
    details: list[tuple[bytes, str]],
    copy: MarketingCopy,
    category_id: str = "",
) -> bytes:
    """HTML/CSS 版 feature_cards；任何失败返回空 bytes 让调用方回退 PIL。"""
    try:
        from .html_composer import render_feature_cards  # noqa: PLC0415

        return render_feature_cards(
            hero=hero,
            cards=details,
            headline=str(copy.headline or ""),
            # 细节卡的画面很紧，再塞副文案就会压到照片面板上；信息交给 4 张卡本身。
            subline="",
            category_id=category_id,
        )
    except Exception:  # noqa: BLE001 - 排版引擎问题绝不能阻断出图
        return b""
