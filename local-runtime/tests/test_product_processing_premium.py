"""精选处理（第一版形态）：排版层 + 提示词层的行为测试。"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

from wh_local.modules.product_processing.domain.premium_composer import (
    MarketingCopy,
    compose_feature_cards,
    compose_marketing_image,
    filter_copy,
)
from wh_local.modules.product_processing.domain.marketing_gen import (
    build_prompt,
    pick_best,
)
from wh_local.modules.product_processing.domain.premium_prompts import (
    fallback_copy,
    parse_copy_response,
)


def _photo_bytes(side: int = 800, color=(200, 200, 200)) -> bytes:
    image = Image.new("RGB", (side, side), color)
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def test_filter_copy_removes_banned_words() -> None:
    assert filter_copy("The Best No.1 perfect cutter") == "The cutter"
    assert "guaranteed" not in filter_copy("100% guaranteed sharp").lower()


def test_parse_copy_response_tolerates_markdown_fence() -> None:
    text = '```json\n{"headline": "Sharp And Durable", "subline": "Cuts steel wire with ease"}\n```'
    copy = parse_copy_response(text)
    assert copy is not None
    assert copy.headline == "Sharp And Durable"
    assert copy.subline == "Cuts steel wire with ease"


def test_parse_copy_response_rejects_garbage() -> None:
    assert parse_copy_response("sorry I cannot help") is None
    assert parse_copy_response("") is None
    assert parse_copy_response('{"subline": "no headline"}') is None


def test_fallback_copy_uses_title_without_inventing() -> None:
    copy = fallback_copy("Industrial Steel Wire Cutter Pliers Heavy Duty Tool For Home")
    assert copy.headline.startswith("Industrial")
    assert len(copy.headline.split()) <= 8
    assert copy.subline == ""


def test_compose_marketing_image_outputs_square_jpeg() -> None:
    for template in ("headline_hero", "action_proof", "icon_grid", "material_strip", "use_cases"):
        data = compose_marketing_image(
            _photo_bytes(),
            template,
            MarketingCopy(headline="Sharp And Smooth\nStrong Shearing", subline="Cuts steel wire with ease"),
        )
        image = Image.open(BytesIO(data))
        assert image.size == (1600, 1600)
        assert data[:2] == b"\xff\xd8"  # JPEG magic


def test_compose_rejects_unknown_template() -> None:
    try:
        compose_marketing_image(_photo_bytes(), "nope", MarketingCopy(headline="x"))
    except ValueError as exc:
        assert "未知版式" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("未知版式没有报错")


def test_prompt_is_short_and_reference_driven() -> None:
    """v2：短提示词——只写构图/光，产品靠参考图（不再堆长合同）。"""
    prompt = build_prompt("headline_hero")
    assert len(prompt) < 1000
    assert "reference images" in prompt
    assert "empty seamless backdrop" in prompt
    assert "HARD RULES" not in prompt


def test_display_prompts_empty_the_product_but_scene_prompts_keep_contents() -> None:
    """商品展示图里商品必须空（参考图里的衣服是陈列道具）；使用场景图保留内容物。"""
    for template in ("headline_hero", "icon_grid", "side_rail", "white_main", "feature_strip"):
        assert "not part of the product" in build_prompt(template), template
    for template in ("use_cases", "action_proof"):
        assert "not part of the product" not in build_prompt(template), template


def test_pick_best_falls_back_to_first_on_error() -> None:
    candidates = [b"a", b"b", b"c"]

    def broken(_msgs):
        raise RuntimeError("vision down")

    assert pick_best(candidates, complete=broken) == 0
    assert pick_best([b"only"], complete=broken) == 0
    assert pick_best(candidates, complete=lambda _m: '{"best": 2}') == 2
    assert pick_best(candidates, complete=lambda _m: '{"best": 7}') == 0


def test_compose_feature_cards_layout() -> None:
    hero = _photo_bytes()
    card = _photo_bytes(400, (60, 60, 60))
    out = compose_feature_cards(
        hero,
        [(card, "Wire Stripping"), (card, "Crimping"), (card, "Cutting"), (card, "Twisting")],
        MarketingCopy(headline="Precision Working Head", subline="Designed for control"),
    )
    image = Image.open(BytesIO(out))
    assert image.size == (1600, 1600)
    assert out[:2] == b"\xff\xd8"


def test_batch_hook_passes_real_task_id() -> None:
    """批次里调营销图生成必须传真实 task_id（曾把 draft_id 当 task_id，三张全丢）。"""
    import re
    from pathlib import Path as _Path

    import wh_local.modules.product_processing.service as service_module

    source = _Path(service_module.__file__).read_text(encoding="utf-8")
    assert re.search(
        r"_generate_marketing_images\(\s*\n\s*task_id,\s*\n\s*draft\[\"id\"\]",
        source,
    ), "批次挂载处必须用 task_id 调 _generate_marketing_images"


def test_marketing_replaces_plain_four_grid() -> None:
    """精选处理替代普通四宫格：选了精选版式后不得再产出四宫格裁剪图。

    这条链路很长，无法单测整段；用源码守卫钉住三处关键判定，防止后续改动
    把四宫格又加回来（或让精选图退回"追加"而不是"替代"）。
    """
    from pathlib import Path as _Path

    import wh_local.modules.product_processing.service as service_module

    source = _Path(service_module.__file__).read_text(encoding="utf-8")

    assert "marketing_replaces_grid = marketing_enabled and not preflight_only" in source, (
        "必须由 marketing_images 推导出「精选替代四宫格」标志"
    )
    assert source.count("and not marketing_replaces_grid") >= 2, (
        "need_grid 与 need_detail 都必须被 marketing_replaces_grid 关掉"
    )
    assert (
        "if marketing_enabled and not preflight_only and not grid_source_fallback:" in source
    ), "精选生成不能再依赖 grid_image_paths（否则没有四宫格就不出精选图）"
    assert "list(marketing_paths)" in source and "if need_grid" in source, (
        "精选替代四宫格时轮播应直接用精选图，而不是追加"
    )


def test_compose_callout_ring_with_and_without_coords() -> None:
    from wh_local.modules.product_processing.domain.premium_composer import compose_callout_ring

    copy = MarketingCopy(headline="RATCHET SELF-LOCKING", subline="Powerful grip")
    hero = _photo_bytes(1200, (40, 40, 44))
    out = compose_callout_ring(
        hero,
        [
            {"label": "RATCHET LOCK", "x": 420, "y": 300},
            {"label": "FORGED STEEL", "x": 620, "y": 260},
            {"label": "GRIP", "x": 500, "y": 700},
        ],
        copy,
    )
    assert Image.open(BytesIO(out)).size == (1600, 1600)
    # 无坐标也不炸：退化为编号列表
    fallback = compose_callout_ring(hero, [{"label": "No Coords", "x": -1, "y": -1}], copy)
    assert Image.open(BytesIO(fallback)).size == (1600, 1600)
    # 一个部位都没标出来（视觉模型失败）：退化为居中大标题，而不是只剩左上标题
    empty = compose_callout_ring(hero, [], copy)
    assert Image.open(BytesIO(empty)).size == (1600, 1600)


def test_feature_strip_labels_never_overflow_their_cell() -> None:
    from wh_local.modules.product_processing.domain.premium_composer import (
        _fit_label_to_cell,
    )
    from PIL import ImageDraw

    draw = ImageDraw.Draw(Image.new("RGB", (1600, 400)))
    max_width = 300
    text, font = _fit_label_to_cell(
        draw, "Complete 3-Piece Set With Hardened Alloy Steel", base_size=38, max_width=max_width
    )
    assert draw.textlength(text, font=font) <= max_width
    assert text.endswith("…") or "Complete" in text


def test_marketing_vision_data_url_downscales_large_hero() -> None:
    """标注请求发的是压缩图：2048 原图 base64 后容易触发上游 413/超时。"""
    import base64

    from wh_local.modules.product_processing.service import ProductProcessingService

    service = object.__new__(ProductProcessingService)
    data_url = service._marketing_vision_data_url(_photo_bytes(2048))
    assert data_url.startswith("data:image/jpeg;base64,")
    raw = base64.b64decode(data_url.split(",", 1)[1])
    with Image.open(BytesIO(raw)) as image:
        assert max(image.size) <= 1280


def test_marketing_callout_reports_failure_reason(monkeypatch) -> None:
    """callout 失败必须写清原因（任务 56 只剩 parts0，查不出超时还是 413）。"""
    from wh_local.modules.product_processing import doubao_ark
    from wh_local.modules.product_processing.service import ProductProcessingService

    class _Boom:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def complete(self, *_args, **_kwargs):
            raise doubao_ark.DoubaoArkError(
                "gateway exploded", error_kind="provider_http", retryable=False
            )

    monkeypatch.setattr(doubao_ark, "DoubaoArkClient", _Boom)
    service = object.__new__(ProductProcessingService)
    notes: list[str] = []

    assert service._marketing_callout_parts(_photo_bytes(400), ai_notes=notes) == []
    assert any(note.startswith("marketing:callout:error:provider_http") for note in notes)


def test_marketing_callout_reports_unparseable_response(monkeypatch) -> None:
    from wh_local.modules.product_processing import doubao_ark
    from wh_local.modules.product_processing.service import ProductProcessingService

    class _Prose:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def complete(self, *_args, **_kwargs):
            return "Sure! This tool has several notable parts."

    monkeypatch.setattr(doubao_ark, "DoubaoArkClient", _Prose)
    service = object.__new__(ProductProcessingService)
    notes: list[str] = []

    assert service._marketing_callout_parts(_photo_bytes(400), ai_notes=notes) == []
    assert "marketing:callout:no-parts-in-response" in notes


def test_build_prompt_appends_category_fragment() -> None:
    from wh_local.modules.product_processing.domain.marketing_gen import (
        MEDIA_RULES,
        build_prompt,
    )

    prompt = build_prompt("headline_hero", "hand_tools")
    # auto 默认明亮影棚（不出暗黑环境图）；暗色片段只在显式选 dark 时拼入
    assert "Bright editorial studio" in prompt
    assert "Dark industrial studio" not in prompt
    dark_prompt = build_prompt("headline_hero", "hand_tools", style_template="dark")
    assert "Dark industrial studio" in dark_prompt
    assert MEDIA_RULES in prompt
    # 未命中品类（general/空）保持旧提示词不变
    assert build_prompt("headline_hero", "general") == build_prompt("headline_hero")


def test_parse_category_response_validates_taxonomy() -> None:
    from wh_local.modules.product_processing.domain.premium_prompts import (
        parse_category_response,
    )

    assert parse_category_response(
        '{"category_id": "hand_tools", "confidence": "high", "reason": "pliers"}'
    ) == ("hand_tools", "high")
    assert parse_category_response('```json\n{"category_id":"apparel_accessories"}\n```') == (
        "apparel_accessories",
        "",
    )
    assert parse_category_response('{"category_id": "not_a_category"}') == ("", "")
    assert parse_category_response("no json here") == ("", "")


def test_marketing_category_uses_confident_vision(monkeypatch) -> None:
    from wh_local.modules.product_processing.service import ProductProcessingService

    monkeypatch.setattr(
        ProductProcessingService,
        "_marketing_vision_category",
        lambda self, *, title, reference_urls: ("hand_tools", "high"),
    )
    service = object.__new__(ProductProcessingService)
    notes: list[str] = []

    picked = service._marketing_category_id(
        title="重型工业铁皮剪刀",
        category="",
        reference_urls=["https://img.example/a.jpg"],
        ai_notes=notes,
    )

    assert picked == "hand_tools"
    assert "marketing:category:hand_tools:vision" in notes


def test_marketing_category_falls_back_to_rule_when_vision_is_low(monkeypatch) -> None:
    from wh_local.modules.product_processing.service import ProductProcessingService

    monkeypatch.setattr(
        ProductProcessingService,
        "_marketing_vision_category",
        lambda self, *, title, reference_urls: ("general", "low"),
    )
    service = object.__new__(ProductProcessingService)
    notes: list[str] = []

    picked = service._marketing_category_id(
        title="工业级高硬度钳子三件套钢丝钳8寸老虎钳",
        category="",
        reference_urls=["https://img.example/a.jpg"],
        ai_notes=notes,
    )

    assert picked == "hand_tools"  # 规则命中优先于低置信视觉
    assert "marketing:category:hand_tools:rule" in notes


def test_marketing_category_survives_vision_failure(monkeypatch) -> None:
    from wh_local.modules.product_processing.service import ProductProcessingService

    def boom(self, *, title, reference_urls):
        raise RuntimeError("vision down")

    monkeypatch.setattr(ProductProcessingService, "_marketing_vision_category", boom)
    service = object.__new__(ProductProcessingService)
    notes: list[str] = []

    picked = service._marketing_category_id(
        title="工业级高硬度钳子三件套",
        category="工具",
        reference_urls=["https://img.example/a.jpg"],
        ai_notes=notes,
    )

    assert picked == "hand_tools"
    assert any(note.startswith("marketing:category-vision-failed:") for note in notes)
    assert "marketing:category:hand_tools:rule" in notes


def test_render_blocks_passes_category_into_hero_prompt() -> None:
    from wh_local.modules.product_processing.domain.marketing_pipeline import (
        plan_for,
        render_blocks,
    )

    prompts: list[str] = []

    def fake_render(prompt: str, _refs: list[str], _count: int = 1) -> bytes:
        prompts.append(prompt)
        return _photo_bytes(400)

    render_blocks(
        plan=plan_for("headline_hero"),
        copy=MarketingCopy(headline="TOUGH", subline=""),
        refs=["https://img.example/a.jpg"],
        render=fake_render,
        category_id="hand_tools",
    )

    assert prompts and "Bright editorial studio" in prompts[0]


def test_detail_layout_is_skipped_when_part_coords_missing() -> None:
    """部位坐标缺失时细节块是自由重画的（歪图），这类版式宁可不产出。"""
    from wh_local.modules.product_processing.domain.marketing_pipeline import (
        compose,
        plan_for,
    )

    notes: list[str] = []
    composed = compose(
        plan=plan_for("feature_strip"),
        copy=MarketingCopy(headline="TOUGH", subline=""),
        blocks={
            "hero": _photo_bytes(400),
            "details": [(_photo_bytes(400), "A"), (_photo_bytes(400), "B")],
            "detail_source": "label",
        },
        engine_notes=notes,
    )

    assert composed is None
    assert notes == ["feature_strip:skipped:no-part-coords"]


def test_white_main_plan_and_prompt() -> None:
    """白底主图：独立版式，提示词固定正立/正面/全身/纯白底，供尺寸画布使用。"""
    from wh_local.modules.product_processing.domain.marketing_pipeline import PLANS

    assert "white_main" in PLANS
    prompt = build_prompt("white_main")
    assert "pure white background" in prompt
    assert "upright and centred" in prompt
    assert "whole product visible" in prompt


def test_white_main_prompt_ignores_category_fragment() -> None:
    """品类片段写的是深色工作台/室内场景，会把白底顶掉，所以白底版式必须跳过它。"""
    prompt = build_prompt("white_main", "hand_tools")
    assert "pure white background" in prompt
    assert "workbench" not in prompt.lower()


def _near_white_photo(side: int = 800) -> bytes:
    """近白渐变背景 + 中间深色方块：模拟「白底」产品图（模型给的白底常带灰阶）。"""
    image = Image.new("RGB", (side, side))
    draw = ImageDraw.Draw(image)
    for y in range(side):
        value = int(252 - 16 * y / max(1, side - 1))
        draw.line([(0, y), (side, y)], fill=(value, value, value))
    draw.rectangle(
        [side * 0.35, side * 0.2, side * 0.65, side * 0.8], fill=(40, 42, 46)
    )
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def test_white_main_normalizes_background_to_pure_white() -> None:
    from wh_local.modules.product_processing.domain.white_main import (
        white_background_main,
    )

    out = white_background_main(_near_white_photo(), side=400)
    assert out
    with Image.open(BytesIO(out)) as image:
        assert image.size == (400, 400)
        # 背景必须是真 255 白（不是模型的 236 灰）
        assert image.getpixel((0, 0)) == (255, 255, 255)
        assert image.getpixel((399, 399)) == (255, 255, 255)
        # 商品像素保持不变（没有被漂白/裁掉）
        assert min(image.getpixel((200, 200))) < 120


def test_white_main_rejects_non_white_background() -> None:
    """深色底的图不能被当成白底交付。"""
    from wh_local.modules.product_processing.domain.white_main import (
        white_background_main,
    )

    assert white_background_main(_photo_bytes(400, (30, 30, 36)), side=400) is None


def test_white_main_compose_prefers_cutout(monkeypatch) -> None:
    """有分割模型时走真抠图（商品边缘独立、白底必然 255）。"""
    from wh_local.modules.product_processing.domain import dimension_source_reuse, white_main
    from wh_local.modules.product_processing.domain.marketing_pipeline import (
        compose,
        plan_for,
    )

    calls: list[str] = []
    monkeypatch.setattr(
        dimension_source_reuse,
        "clean_product_canvas",
        lambda _content, *, side=800, model_name="u2net": (
            calls.append(model_name) or b"cutout-jpeg"
        ),
    )

    def normalization_must_not_run(_content, *, side=1600):
        raise AssertionError("cutout 成功时不应再走归一化")

    monkeypatch.setattr(white_main, "white_background_main", normalization_must_not_run)
    notes: list[str] = []
    composed = compose(
        plan=plan_for("white_main"),
        copy=MarketingCopy(headline="TOUGH", subline=""),
        blocks={"hero": _photo_bytes(400)},
        engine_notes=notes,
    )

    assert composed == b"cutout-jpeg"
    assert calls == ["u2netp"]
    assert notes == ["white_main:cutout"]


def test_white_main_falls_back_to_normalization(monkeypatch) -> None:
    """模型缺失（抠图不可用）时退回近白背景归一化，并如实记录来源。"""
    from wh_local.modules.product_processing.domain import dimension_source_reuse, white_main
    from wh_local.modules.product_processing.domain.marketing_pipeline import (
        compose,
        plan_for,
    )

    monkeypatch.setattr(
        dimension_source_reuse,
        "clean_product_canvas",
        lambda _content, *, side=800, model_name="u2net": None,
    )
    monkeypatch.setattr(
        white_main, "white_background_main", lambda _content, *, side=1600: b"normalized-jpeg"
    )
    notes: list[str] = []
    composed = compose(
        plan=plan_for("white_main"),
        copy=MarketingCopy(headline="TOUGH", subline=""),
        blocks={"hero": _photo_bytes(400)},
        engine_notes=notes,
    )

    assert composed == b"normalized-jpeg"
    assert notes == ["white_main:normalized"]


def test_white_main_compose_fails_closed(monkeypatch) -> None:
    """两种后处理都不可用时不出图（而不是把深色底当白底塞进轮播）。"""
    from wh_local.modules.product_processing.domain import dimension_source_reuse, white_main
    from wh_local.modules.product_processing.domain.marketing_pipeline import (
        compose,
        plan_for,
    )

    monkeypatch.setattr(
        dimension_source_reuse,
        "clean_product_canvas",
        lambda _content, *, side=800, model_name="u2net": None,
    )
    monkeypatch.setattr(
        white_main, "white_background_main", lambda _content, *, side=1600: None
    )
    notes: list[str] = []
    composed = compose(
        plan=plan_for("white_main"),
        copy=MarketingCopy(headline="TOUGH", subline=""),
        blocks={"hero": _photo_bytes(400)},
        engine_notes=notes,
    )

    assert composed is None
    assert notes == ["white_main:no-white-background"]


def test_white_main_is_derived_locally_and_placed_in_dimension_slot() -> None:
    """白底主图不占用户版式名额，优先本地派生，且固定排到第 4 格（尺寸画布底图槽位）。"""
    from wh_local.modules.product_processing import service as service_module

    source = Path(service_module.__file__).read_text(encoding="utf-8")
    assert "item != \"white_main\"" in source
    assert "clean_product_canvas(" in source
    assert "marketing:white-main:" in source
    # 第 4 格固定白底主体图，不再用源图尺寸图替代。
    assert "rest.insert(min(3, len(rest)), white_main_path)" in source
    assert "第 4 格必须是白底主体图" in source


def test_source_dimension_image_is_copied_to_dimension_slot(monkeypatch) -> None:
    """源图有尺寸图时直接复制原图占第 4 格（只归一化白底，不重画标注）。"""

    from wh_local.modules.product_processing import service as service_module
    from wh_local.modules.product_processing.infrastructure import ocr_gate

    service = object.__new__(service_module.ProductProcessingService)
    service._marketing_reference_bytes = lambda _value: _photo_bytes(800, (255, 255, 255))
    service._persist_media_for_preview = (
        lambda _parts, *_args, **_kwargs: ["preview://dimension"]
    )
    monkeypatch.setattr(
        ocr_gate,
        "recognize_text",
        lambda _content: ["总长 210mm", "手柄长 115mm", "钳头宽 30mm"],
    )
    notes: list[str] = []
    path = service._marketing_source_dimension_slot(
        1, 2, ["https://img.example/a.jpg"], "local", notes
    )

    assert path == "preview://dimension"
    assert "marketing:dimension:source-copy" in notes


def test_source_dimension_image_with_colored_background_is_rejected(monkeypatch) -> None:
    """深色/彩色底的尺寸图不能直接进第 4 格，必须回退白底主图。"""

    from wh_local.modules.product_processing import service as service_module
    from wh_local.modules.product_processing.infrastructure import ocr_gate

    service = object.__new__(service_module.ProductProcessingService)
    service._marketing_reference_bytes = lambda _value: _photo_bytes(800, (20, 80, 40))
    service._persist_media_for_preview = (
        lambda _parts, *_args, **_kwargs: ["preview://dimension"]
    )
    monkeypatch.setattr(
        ocr_gate,
        "recognize_text",
        lambda _content: ["总长 210mm", "手柄长 115mm"],
    )
    notes: list[str] = []
    path = service._marketing_source_dimension_slot(
        1, 2, ["https://img.example/a.jpg"], "local", notes
    )

    assert path == ""
    assert "marketing:dimension:source-not-white" in notes


def test_compose_feature_strip() -> None:
    from wh_local.modules.product_processing.domain.premium_composer import compose_feature_strip

    hero = _photo_bytes(1200, (30, 30, 36))
    card = _photo_bytes(400, (90, 90, 96))
    out = compose_feature_strip(
        hero,
        [(card, "Wire Stripping"), (card, "Cutting"), (card, "Crimping"), (card, "Twisting")],
        MarketingCopy(headline="4-IN-1 MULTI-FUNCTION"),
    )
    assert Image.open(BytesIO(out)).size == (1600, 1600)


def test_parse_callout_response() -> None:
    from wh_local.modules.product_processing.domain.marketing_gen import parse_callout_response

    parts = parse_callout_response(
        '{"parts": [{"label": "Jaws", "x": 400, "y": 300}, {"label": "Pivot", "x": 1200, "y": 5}]}'
    )
    assert len(parts) == 1 and parts[0]["label"] == "Jaws"
    assert parse_callout_response("nope") == []


def test_callout_prompt_uses_grid_anchors() -> None:
    """坐标只允许落在 3×3 网格锚点上：比连续 0-1000 坐标稳定得多。"""
    from wh_local.modules.product_processing.domain.marketing_gen import build_callout_messages

    messages = build_callout_messages("data:image/jpeg;base64,AAAA")
    text = messages[1]["content"][1]["text"]
    assert "167" in text and "500" in text and "833" in text
    assert "MUST each be one of" in text


def test_callout_refine_prompt_and_parse() -> None:
    from wh_local.modules.product_processing.domain.marketing_gen import (
        build_callout_refine_messages,
        parse_callout_refine_response,
    )

    messages = build_callout_refine_messages("data:image/jpeg;base64,AAAA", "Jaws")
    text = messages[1]["content"][1]["text"]
    assert "Jaws" in text and "0-1000" in text
    assert parse_callout_refine_response('{"x": 250, "y": 750}') == (250.0, 750.0)
    assert parse_callout_refine_response('{"x": -1, "y": -1}') is None
    assert parse_callout_refine_response("nope") is None


def test_set_count_prompt_and_set_rule() -> None:
    from wh_local.modules.product_processing.domain.marketing_gen import (
        build_prompt,
        build_set_count_messages,
        parse_set_count_response,
    )

    messages = build_set_count_messages("data:image/jpeg;base64,AAAA")
    text = messages[1]["content"][-1]["text"]
    assert "Count the separate tools" in text
    assert parse_set_count_response('{"count": 3}') == 3
    assert parse_set_count_response('{"count": 99}') == 8
    assert parse_set_count_response("nope") == 1

    prompt = build_prompt("headline_hero", set_count=3)
    assert "set of 3 separate pieces" in prompt
    assert "do not omit any" in prompt


def test_qc_prompt_ignores_brand_text() -> None:
    """AI 不应被要求复刻品牌刻字；QC 不能因此误杀好图。"""
    from wh_local.modules.product_processing.domain.marketing_gen import (
        verify_composed_messages,
    )

    messages = verify_composed_messages(
        ["data:image/jpeg;base64,AAAA"], "data:image/jpeg;base64,BBBB", "S1"
    )
    text = messages[1]["content"][-1]["text"]
    assert "brand text" in text.lower()
    assert "NOT a failure" in text


def test_marketing_callout_refines_grid_anchor(monkeypatch) -> None:
    """第二段把锚点裁出来放大后再定位，坐标要能映射回整图。"""
    from wh_local.modules.product_processing import doubao_ark
    from wh_local.modules.product_processing.service import ProductProcessingService

    calls: list[str] = []

    class _Fake:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def complete(self, messages, timeout=None):
            content = messages[1]["content"]
            text = content[-1]["text"] if isinstance(content, list) else ""
            if "close-up crop" in text:
                calls.append("refine")
                return '{"x": 833, "y": 167}'
            calls.append("grid")
            return '{"parts": [{"label": "Jaws", "x": 500, "y": 500}]}'

    monkeypatch.setattr(doubao_ark, "DoubaoArkClient", _Fake)
    service = object.__new__(ProductProcessingService)
    notes: list[str] = []

    parts = service._marketing_callout_parts(_photo_bytes(400), ai_notes=notes)

    assert len(parts) == 1 and parts[0]["label"] == "Jaws"
    assert calls.count("grid") == 1 and calls.count("refine") == 1
    # 锚点 (0.5,0.5) → 裁 (0.33..0.67)，局部 (833,167) 映射回整图约 (613,387)
    assert abs(parts[0]["x"] - 613.2) < 1.0
    assert abs(parts[0]["y"] - 386.8) < 1.0
    assert any(note.startswith("marketing:callout:refined:1/1") for note in notes)


def test_reference_select_prompt_and_parse() -> None:
    from wh_local.modules.product_processing.domain.marketing_gen import (
        build_reference_select_messages,
        parse_reference_select_response,
    )

    messages = build_reference_select_messages(
        ["data:image/jpeg;base64,AAAA", "data:image/jpeg;base64,BBBB"]
    )
    text = messages[1]["content"][-1]["text"]
    assert "no text" in text and "collage" in text
    assert parse_reference_select_response('{"best": [1, 0]}', 2) == [1, 0]
    assert parse_reference_select_response('{"best": [5]}', 2) == []
    assert parse_reference_select_response("nope", 2) == []


def test_detail_pick_prompt_and_parse() -> None:
    from wh_local.modules.product_processing.domain.marketing_gen import (
        build_detail_pick_messages,
        parse_detail_pick_response,
    )

    messages = build_detail_pick_messages(
        ["data:image/jpeg;base64,AAAA", "data:image/jpeg;base64,BBBB"]
    )
    text = messages[1]["content"][-1]["text"]
    assert "CLOSE-UPS" in text and "label" in text
    labelled = [
        item.get("text")
        for item in messages[1]["content"]
        if item.get("type") == "text"
    ]
    assert "CANDIDATE 0" in labelled and "CANDIDATE 1" in labelled

    # 带参考图时：第一张是 REFERENCE PRODUCT，候选编号仍从 0 开始
    with_ref = build_detail_pick_messages(
        ["data:image/jpeg;base64,AAAA"], reference_data_url="data:image/jpeg;base64,REF"
    )
    assert with_ref[1]["content"][0]["type"] == "image_url"
    assert with_ref[1]["content"][1]["text"].startswith("REFERENCE PRODUCT")
    assert "SAME product" in str(
        [item.get("text") for item in with_ref[1]["content"] if item.get("type") == "text"][-1]
    )

    assert parse_detail_pick_response(
        '{"items": [{"index": 1, "label": "CUTTING JAWS"}, {"index": 3, "label": "GRIP"}]}',
        4,
    ) == [(1, "CUTTING JAWS"), (3, "GRIP")]
    # 越界/重复/空标签都要丢弃
    assert parse_detail_pick_response(
        '{"items": [{"index": 9, "label": "X"}, {"index": 1, "label": ""}, '
        '{"index": 1, "label": "A"}, {"index": 1, "label": "B"}]}',
        4,
    ) == [(1, "A")]
    assert parse_detail_pick_response("nope", 4) == []


def test_s4_without_cards_falls_back_to_full_bleed() -> None:
    """S4 没有卡片内容时不能再留半屏黑：改满版叠加（S3）。"""
    from wh_local.modules.product_processing.domain.html_composer import (
        build_skeleton_html,
    )

    hero = _photo_bytes(800, color=(40, 40, 40))
    html = build_skeleton_html(
        template="headline_hero",
        skeleton="S4",
        hero=hero,
        headline="RATCHET POWER",
        category_id="hand_tools",
    )
    # S4 的大图卡是 88% 宽 × 50% 高；兜底 S3 是整图铺满。
    assert "height:50%" not in html
    assert "object-fit:cover" in html


def test_reference_score_prefers_white_centered_product() -> None:
    """白底、商品居中不贴边的原始图，应该比整屏深色营销图更适合当参考。"""
    from wh_local.modules.product_processing.service import ProductProcessingService

    white = Image.new("RGB", (600, 600), (255, 255, 255))
    ImageDraw.Draw(white).rectangle((200, 200, 400, 400), fill=(30, 30, 30))
    white_buffer = BytesIO()
    white.save(white_buffer, format="JPEG")
    dark = Image.new("RGB", (600, 600), (25, 25, 25))
    dark_buffer = BytesIO()
    dark.save(dark_buffer, format="JPEG")

    white_score = ProductProcessingService._marketing_reference_score(
        white_buffer.getvalue()
    )
    dark_score = ProductProcessingService._marketing_reference_score(
        dark_buffer.getvalue()
    )
    assert white_score > dark_score


def test_generate_best_gate_filters_rejected_candidates() -> None:
    """本地构图门先淘汰贴边/占满画面的候选，再交给视觉排序。"""
    from wh_local.modules.product_processing.domain.marketing_gen import generate_best

    produced: list[bytes] = []

    def render(prompt: str, refs: list[str]) -> bytes:
        content = _photo_bytes(200, (len(produced) * 40, 20, 20))
        produced.append(content)
        return content

    out = generate_best(
        prompt="x",
        refs=[],
        render=render,
        complete=lambda _messages: "{}",
        count=2,
        gate=lambda content: content == produced[1],
    )

    assert out == produced[1]


def test_pipeline_plan_and_blocks() -> None:
    """分块管线：计划正确、无部位坐标时不生细节块、缺块时版式降级。"""
    from wh_local.modules.product_processing.domain.marketing_pipeline import (
        compose as compose_plan,
        plan_for,
        render_blocks,
    )

    strip = plan_for("feature_strip")
    assert strip.details == 4
    assert plan_for("nope").template == "headline_hero"
    copy = MarketingCopy(headline="4-IN-1", subline="", bullets=["Stripping", "Cutting", "Crimping", "Twisting"])

    calls: list[str] = []

    def fake_render(prompt: str, refs: list[str], count: int = 1) -> bytes:
        calls.append(prompt[:40])
        return _photo_bytes(200, (50, 50, 50))

    blocks = render_blocks(plan=strip, copy=copy, refs=["http://x"], render=fake_render)
    assert blocks["hero"]
    # 没有部位坐标：只生 hero，不再花 4 次调用画注定被丢弃的歪图
    assert blocks["details"] == []
    assert calls and len(calls) == 1
    assert blocks["detail_source"] == "label"
    out = compose_plan(plan=strip, copy=copy, blocks=blocks)
    # 没有部位坐标（label 模式）→ 这张版式不出
    assert out is None

    # 有真实部位坐标（crop）时才出图
    crop_blocks = {
        "hero": _photo_bytes(200),
        "details": [(_photo_bytes(200), "Cutting")] * 4,
        "detail_source": "crop",
    }
    cropped = compose_plan(plan=strip, copy=copy, blocks=crop_blocks)
    assert cropped is not None and Image.open(BytesIO(cropped)).size == (1600, 1600)

    # 缺细节 → 版式降级为 None（不生成残图）
    empty = {"hero": _photo_bytes(200), "details": []}
    assert compose_plan(plan=strip, copy=copy, blocks=empty) is None
    # headline_hero 只要有 hero 就能出
    hero_only = compose_plan(plan=plan_for("headline_hero"), copy=copy, blocks={"hero": _photo_bytes(200), "details": []})
    assert hero_only is not None


def test_copy_messages_carry_subject_and_caution() -> None:
    """文案提示词必须带视觉确认的主体与操作者注意点，避免文案自相矛盾。"""
    from wh_local.modules.product_processing.domain.premium_prompts import (
        build_copy_messages,
    )

    text = build_copy_messages(
        "Pliers",
        "tools",
        "icon_grid",
        subject="two-piece pliers",
        caution="must have two handles",
    )[-1]["content"]
    assert "two-piece pliers" in text
    assert "must have two handles" in text
    # 每个版式都有专属 hint，不再全部落到兜底文案
    assert "short feature chips" in text


def test_copy_messages_carry_operator_key_information() -> None:
    """用户填写的「重要信息」必须作为硬事实进文案请求；留空时不注入。"""
    from wh_local.modules.product_processing.domain.premium_prompts import (
        build_copy_messages,
    )

    text = build_copy_messages(
        "Bags",
        "apparel_accessories",
        "infographic_hero",
        key_info="加厚帆布，适合通勤",
    )[-1]["content"]
    assert "Operator key information" in text
    assert "加厚帆布，适合通勤" in text
    assert "do not invent anything beyond it" in text

    empty = build_copy_messages(
        "Bags", "apparel_accessories", "infographic_hero", key_info=""
    )[-1]["content"]
    assert "Operator key information" not in empty


def test_fallback_copy_is_ascii_and_varies_by_variant() -> None:
    """兜底文案：绝不含中文（图上禁中文），不同版式取不同词窗不复读。"""
    from wh_local.modules.product_processing.domain.premium_prompts import fallback_copy

    zh = fallback_copy("新款加厚帆布托特包 大容量")
    assert zh.headline == "Quality Product"

    first = fallback_copy("Heavy Duty Compound Action Wire Cutting Pliers Set", variant=0)
    second = fallback_copy("Heavy Duty Compound Action Wire Cutting Pliers Set", variant=1)
    assert first.headline.isascii() and second.headline.isascii()
    assert first.headline != second.headline
    assert first.bullets == []  # 不编造卖点


def test_copy_parse_rejects_non_ascii_text() -> None:
    """中文防护：中文 headline 拒收走保底；中文 subline/标签只丢该部分/该条。"""
    from wh_local.modules.product_processing.domain.marketing_gen import (
        parse_detail_pick_response,
    )
    from wh_local.modules.product_processing.domain.premium_prompts import (
        parse_copy_response,
        parse_copy_set_response,
    )

    # 中文 headline：整条拒收（走 ASCII 保底文案），不让中文被画上图
    assert parse_copy_response('{"headline": "超值好货", "subline": "s", "bullets": []}') is None

    ok = parse_copy_response(
        '{"headline": "Cut Clean", "subline": "质量很好", "bullets": ["Sharp Jaw", "好用"]}'
    )
    assert ok is not None
    assert ok.headline == "Cut Clean"
    assert ok.subline == ""
    assert ok.bullets == ["Sharp Jaw"]

    zh_set = parse_copy_set_response(
        '{"copies": ['
        '{"key": "headline_hero", "headline": "超值好货", "subline": "", "bullets": []},'
        '{"key": "icon_grid", "headline": "Grip That Fits", "subline": "", '
        '"bullets": ["好用", "Comfort Grip"]}]}',
        ["headline_hero", "icon_grid"],
    )
    assert "headline_hero" not in zh_set
    assert zh_set["icon_grid"].bullets == ["Comfort Grip"]

    # 中文细节标签：丢该候选，不让中文标签画上卡
    picked = parse_detail_pick_response(
        '{"items": [{"index": 0, "label": "刀口"}, {"index": 1, "label": "Jaw Tip"}]}',
        2,
        limit=4,
    )
    assert picked == [(1, "Jaw Tip")]


def test_generation_guards_keep_hard_rules_and_layout_hint() -> None:
    """生图护栏：长 instructions 不许截掉末尾硬约束；QC 拿布局描述不是代号。"""
    from wh_local.modules.product_processing.domain.marketing_gen import (
        _qc_messages,
        build_prompt,
    )

    # 约束句排在长场景句之后：旧的 300 字符硬截会把它静默切掉
    long_instructions = (
        "scene cue " * 35
        + "do not change the product design, materials, parts or proportions, "
        "and never render any text."
    )
    prompt = build_prompt(
        "headline_hero", "hand_tools", 0, skeleton="S3", instructions=long_instructions
    )
    assert "never render any text" in prompt
    assert "do not change the product design" in prompt

    # QC 提示词要给布局描述（S1 代号模型看不懂，布局检查等于没做）
    messages = _qc_messages(["https://r"], "https://c", "S1")
    text = messages[-1]["content"][-1]["text"]
    assert "photo occupies the top area" in text
    assert "matches the S1" not in text


def test_copy_set_messages_differ_per_template() -> None:
    """差异化文案集：一次请求出多份，标题/卖点互不复读；缺失版式由调用方保底。"""
    from wh_local.modules.product_processing.domain.premium_prompts import (
        build_copy_set_messages,
        parse_copy_set_response,
    )

    messages = build_copy_set_messages(
        "Pliers",
        "tools",
        ["headline_hero", "icon_grid"],
        key_info="适合户外",
    )
    text = messages[-1]["content"]
    assert 'key "headline_hero"' in text and 'key "icon_grid"' in text
    assert "must differ from the others" in text
    assert "5 INCH SIZE" in text  # 明确禁止尺寸数字当卖点
    assert "适合户外" in text

    parsed = parse_copy_set_response(
        '{"copies": ['
        '{"key": "headline_hero", "headline": "Cut Clean Every Time", "subline": "s", '
        '"bullets": ["Sharp Jaw"]},'
        '{"key": "icon_grid", "headline": "Grip That Fits", "subline": "s", '
        '"bullets": ["Comfort Grip"]},'
        '{"key": "icon_grid", "headline": "dup ignored", "subline": "", "bullets": []}'
        "]}",
        ["headline_hero", "icon_grid", "feature_cards"],
    )
    assert parsed["headline_hero"].headline == "Cut Clean Every Time"
    assert parsed["icon_grid"].headline == "Grip That Fits"
    assert "feature_cards" not in parsed


def test_dedupe_card_grid_templates_keeps_one_card_band() -> None:
    """卡带型版式互斥：同批最多 1 个，其余换形态不同的版式。"""
    from wh_local.modules.product_processing.service import _dedupe_card_grid_templates

    result, notes = _dedupe_card_grid_templates(
        ["feature_cards", "infographic_hero", "headline_hero", "icon_grid"]
    )
    assert result[0] == "feature_cards"
    assert "infographic_hero" not in result
    assert result[1] == "infographic_details"
    assert any(
        "card-grid:infographic_hero->infographic_details" in note for note in notes
    )

    # 只有一个卡带型时原样不动
    same, notes = _dedupe_card_grid_templates(
        ["infographic_hero", "infographic_details"]
    )
    assert same == ["infographic_hero", "infographic_details"]
    assert notes == []


def test_dedupe_caps_band_family_at_two() -> None:
    """「照片+底部带」家族同批最多 2 个：第 3 个换不同骨架，避免三胞胎。"""
    from wh_local.modules.product_processing.service import _dedupe_card_grid_templates

    result, notes = _dedupe_card_grid_templates(
        ["feature_cards", "icon_grid", "callout_ring", "headline_hero"]
    )
    assert result[:2] == ["feature_cards", "icon_grid"]
    assert "callout_ring" not in result
    assert "infographic_details" in result  # 第 3 个家族位换成清单引线
    assert any("card-grid:callout_ring->infographic_details" in note for note in notes)
    assert result[-1] == "headline_hero"

    # 2 个家族成员正常共存
    same, notes = _dedupe_card_grid_templates(["icon_grid", "callout_ring"])
    assert same == ["icon_grid", "callout_ring"]
    assert notes == []


def test_build_prompt_accepts_subject_and_operator_instructions() -> None:
    from wh_local.modules.product_processing.domain.marketing_gen import build_prompt

    prompt = build_prompt(
        "headline_hero",
        "hand_tools",
        7,
        subject="two-piece pliers",
        instructions="the handle must have two pieces",
    )
    assert "The product is exactly: two-piece pliers." in prompt
    assert "Follow strictly: the handle must have two pieces." in prompt
    # 未传时保持旧提示词（不注入空约束）
    assert "Follow strictly" not in build_prompt("headline_hero")


def test_infographic_prompt_follows_category_brightness() -> None:
    """auto 默认亮调信息图（不出暗黑环境图）；显式选暗色质感才走暗背景。"""
    from wh_local.modules.product_processing.domain.marketing_gen import build_prompt

    bag = build_prompt("infographic_hero", "apparel_accessories", 7)
    assert "bright uncluttered background" in bag
    assert "dark uncluttered background" not in bag

    # auto 默认明亮：工具类也不再走暗黑工业风
    tool = build_prompt("infographic_hero", "hand_tools", 7)
    assert "bright uncluttered background" in tool
    assert "dark uncluttered background" not in tool

    # 显式选「暗色质感」才出暗背景
    tool_dark = build_prompt("infographic_hero", "hand_tools", 7, style_template="dark")
    assert "dark uncluttered background" in tool_dark


def test_style_template_overrides_category_default_prompt() -> None:
    """共通模板优先于品类：工具选亮色、包袋选暗色都要生效。"""
    from wh_local.modules.product_processing.domain.marketing_gen import build_prompt

    tool_bright = build_prompt(
        "infographic_hero", "hand_tools", style_template="bright"
    )
    assert "bright uncluttered background" in tool_bright

    bag_dark = build_prompt(
        "infographic_hero", "apparel_accessories", style_template="dark"
    )
    assert "dark uncluttered background" in bag_dark


def test_marketing_candidate_rejects_chinese_text(monkeypatch) -> None:
    """营销主图候选里检出中文必须直接淘汰（中文标签常被模型从原图画回来）。"""
    from wh_local.modules.product_processing import service as service_module
    from wh_local.modules.product_processing.infrastructure import ocr_gate

    service = object.__new__(service_module.ProductProcessingService)
    service._marketing_verify_candidate = lambda *_a, **_k: True
    monkeypatch.setattr(
        ocr_gate, "detect_chinese_boxes", lambda _c: [(0, 0, 10, 10)]
    )
    notes: list[str] = []
    assert service._marketing_candidate_is_clean(b"img", ["ref"], "S1", notes) is False
    assert "marketing:qc:chinese-text" in notes

    monkeypatch.setattr(ocr_gate, "detect_chinese_boxes", lambda _c: [])
    notes.clear()
    assert service._marketing_candidate_is_clean(b"img", ["ref"], "S1", notes) is True


def test_marketing_detail_images_erase_chinese_and_keep_good_crop(monkeypatch) -> None:
    """原图局部带中文时先本地擦字：擦干净就保留这张好图，不直接丢掉。"""
    from wh_local.modules.product_processing import doubao_ark, service as service_module
    from wh_local.modules.product_processing.domain import marketing_pipeline
    from wh_local.modules.product_processing.infrastructure import ocr_gate

    class _FakeArk:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def complete(self, *_args, **_kwargs) -> str:
            return '{"items": [{"index": 0, "label": "SIDE POCKET"}]}'

    monkeypatch.setattr(doubao_ark, "DoubaoArkClient", _FakeArk)
    monkeypatch.setattr(
        marketing_pipeline, "_product_bbox_ratios", lambda _c: (0.2, 0.2, 0.8, 0.8)
    )
    calls = {"count": 0}

    def _boxes(_content):
        calls["count"] += 1
        return [(0, 0, 10, 10)] if calls["count"] == 1 else []

    monkeypatch.setattr(ocr_gate, "detect_chinese_boxes", _boxes)
    monkeypatch.setattr(ocr_gate, "recognize_text", lambda _c: [])
    monkeypatch.setattr(
        ocr_gate, "erase_chinese_text", lambda content: (_photo_bytes(300), 1)
    )
    service = object.__new__(service_module.ProductProcessingService)
    service._marketing_reference_bytes = lambda _v: _photo_bytes(400)
    notes: list[str] = []
    images = service._marketing_detail_images(["https://x/a.jpg"], ai_notes=notes)
    assert len(images) == 1
    assert "marketing:detail:chinese-erased:1" in notes


def test_marketing_detail_images_drop_crop_when_chinese_remains(monkeypatch) -> None:
    """擦完仍检出中文：这张细节卡必须丢弃，宁可不放。"""
    from wh_local.modules.product_processing import doubao_ark, service as service_module
    from wh_local.modules.product_processing.domain import marketing_pipeline
    from wh_local.modules.product_processing.infrastructure import ocr_gate

    class _FakeArk:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def complete(self, *_args, **_kwargs) -> str:
            return '{"items": [{"index": 0, "label": "SIDE POCKET"}]}'

    monkeypatch.setattr(doubao_ark, "DoubaoArkClient", _FakeArk)
    monkeypatch.setattr(
        marketing_pipeline, "_product_bbox_ratios", lambda _c: (0.2, 0.2, 0.8, 0.8)
    )
    monkeypatch.setattr(
        ocr_gate, "detect_chinese_boxes", lambda _c: [(0, 0, 10, 10)]
    )
    monkeypatch.setattr(ocr_gate, "recognize_text", lambda _c: [])
    monkeypatch.setattr(ocr_gate, "erase_chinese_text", lambda content: (content, 0))
    service = object.__new__(service_module.ProductProcessingService)
    service._marketing_reference_bytes = lambda _v: _photo_bytes(400)
    notes: list[str] = []
    images = service._marketing_detail_images(["https://x/a.jpg"], ai_notes=notes)
    assert images == []
    assert "marketing:detail:chinese-skip:1" in notes


def test_detail_styled_prompt_pins_part_and_forbids_text() -> None:
    """同风格特写提示词：部位来自真实裁切标签、场景光线跟随主图、禁画字。"""
    from wh_local.modules.product_processing.domain.detail_specs import (
        build_detail_styled_prompt,
    )

    prompt = build_detail_styled_prompt(
        "Jaw Tip", "Dark industrial studio: black steel surface, soft rim light"
    )
    assert "the Jaw Tip" in prompt
    assert "same scene, surface and lighting mood" in prompt
    assert "keep the exact product design" in prompt
    assert "No text, no watermark, no logos" in prompt


def test_marketing_style_detail_redraws_and_falls_back(monkeypatch) -> None:
    """AI 重绘同风格特写：合格就用；换色/重绘失败就回退原图裁切。"""
    from wh_local.modules.product_processing import service as service_module
    from wh_local.modules.product_processing.domain import marketing_pipeline

    service = object.__new__(service_module.ProductProcessingService)
    service._media_processor = lambda: object()

    class _Result:
        content = _photo_bytes(300)

    service._marketing_render = lambda *_args, **_kwargs: _Result()
    monkeypatch.setattr(
        marketing_pipeline, "_product_bbox_ratios", lambda _c: (0.2, 0.2, 0.8, 0.8)
    )
    notes: list[str] = []
    out = service._marketing_style_detail(_photo_bytes(200), "Jaw Tip", ai_notes=notes)
    assert out == _photo_bytes(300)

    # 换色/换件：重绘图多数主体色块在真实裁切里找不到近色 → 回退
    monkeypatch.setattr(
        service_module.ProductProcessingService,
        "_marketing_subject_rgb_blocks",
        lambda _self, content: [(10.0, 10.0, 10.0)] * 9
        if content == _photo_bytes(200)
        else [(200.0, 200.0, 200.0)] * 9,
    )
    assert service._marketing_style_detail(_photo_bytes(200), "Jaw Tip") == b""

    # 重绘抛异常 → 回退
    monkeypatch.undo()
    service._marketing_render = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("boom")
    )
    assert service._marketing_style_detail(_photo_bytes(200), "Jaw Tip") == b""


def test_marketing_detail_images_use_styled_or_fall_back(monkeypatch) -> None:
    """细节小图：重绘成功用重绘图，失败回退原图裁切；日志可区分两者。"""
    from wh_local.modules.product_processing import doubao_ark, service as service_module
    from wh_local.modules.product_processing.domain import marketing_pipeline
    from wh_local.modules.product_processing.infrastructure import ocr_gate

    class _FakeArk:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def complete(self, *_args, **_kwargs) -> str:
            return '{"items": [{"index": 0, "label": "JAW TIP"}]}'

    monkeypatch.setattr(doubao_ark, "DoubaoArkClient", _FakeArk)
    monkeypatch.setattr(
        marketing_pipeline, "_product_bbox_ratios", lambda _c: (0.2, 0.2, 0.8, 0.8)
    )
    monkeypatch.setattr(ocr_gate, "detect_chinese_boxes", lambda _c: [])
    monkeypatch.setattr(ocr_gate, "recognize_text", lambda _c: [])
    service = object.__new__(service_module.ProductProcessingService)
    service._marketing_reference_bytes = lambda _v: _photo_bytes(400)

    styled = _photo_bytes(300)
    service._marketing_style_detail = lambda *_args, **_kwargs: styled
    notes: list[str] = []
    images = service._marketing_detail_images(["https://x/a.jpg"], ai_notes=notes)
    assert [content for content, _label in images] == [styled]
    assert "marketing:detail:styled:1" in notes

    service._marketing_style_detail = lambda *_args, **_kwargs: b""
    notes.clear()
    images = service._marketing_detail_images(["https://x/a.jpg"], ai_notes=notes)
    assert len(images) == 1 and images[0][0] != b""
    assert images[0][1] == "JAW TIP"
    assert "marketing:detail:style-fallback:1" in notes


def test_palette_mismatch_uses_block_coverage() -> None:
    """多色商品的不同部位不误杀；整件换色才拦；信息不足不误杀。"""
    from wh_local.modules.product_processing.service import (
        ProductProcessingService as S,
    )

    bicolor = [(200.0, 60.0, 50.0)] * 5 + [(40.0, 40.0, 44.0)] * 4
    # 黑刀口近景（多数块是黑）在红+黑的参考里都有近色 → 通过
    black_part = [(45.0, 44.0, 48.0)] * 9
    assert S._marketing_palette_mismatch(black_part, bicolor, 118.0) is False
    # 红柄近景同样通过
    red_part = [(195.0, 65.0, 55.0)] * 9
    assert S._marketing_palette_mismatch(red_part, bicolor, 118.0) is False
    # 整件换色（米白件 vs 红黑参考）：多数块找不到近色 → 拦
    ivory_part = [(235.0, 228.0, 210.0)] * 9
    assert S._marketing_palette_mismatch(ivory_part, bicolor, 118.0) is True
    # 采不出色块时不误杀
    assert S._marketing_palette_mismatch([], bicolor, 118.0) is False
    assert S._marketing_palette_mismatch(black_part, [], 118.0) is False


def test_marketing_detail_images_drop_text_heavy_diagram(monkeypatch) -> None:
    """规格示意图/吊牌图表（文字密集）必须被本地拦掉，不进细节卡。"""
    from wh_local.modules.product_processing import doubao_ark, service as service_module
    from wh_local.modules.product_processing.domain import marketing_pipeline
    from wh_local.modules.product_processing.infrastructure import ocr_gate

    class _FakeArk:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def complete(self, *_args, **_kwargs) -> str:
            return '{"items": [{"index": 0, "label": "SPEC CHART"}]}'

    monkeypatch.setattr(doubao_ark, "DoubaoArkClient", _FakeArk)
    monkeypatch.setattr(
        marketing_pipeline, "_product_bbox_ratios", lambda _c: (0.2, 0.2, 0.8, 0.8)
    )
    monkeypatch.setattr(ocr_gate, "detect_chinese_boxes", lambda _c: [])
    monkeypatch.setattr(
        ocr_gate,
        "recognize_text",
        lambda _c: ["SPEC SHEET", "Model A-200", "Rated 8 inch", "www.example.com"],
    )
    service = object.__new__(service_module.ProductProcessingService)
    service._marketing_reference_bytes = lambda _v: _photo_bytes(400)
    service._marketing_style_detail = lambda *_a, **_k: b""
    notes: list[str] = []
    images = service._marketing_detail_images(["https://x/a.jpg"], ai_notes=notes)
    assert images == []
    assert "marketing:detail:text-skip:1" in notes


def test_skeleton_photo_cards_never_fabricate_without_details() -> None:
    """照片卡版式没有真实细节图就整版降级，绝不拿图标+短词硬凑空卡。"""
    from wh_local.modules.product_processing.domain.html_composer import (
        _skeleton_items,
        _promo_tagline,
    )

    style, items = _skeleton_items(
        "feature_cards", ["A", "B", "C", "D"], "sub", [], [], 4
    )
    assert style == "cards"
    assert items == []

    style, items = _skeleton_items(
        "feature_cards", ["A", "B", "C", "D"], "sub",
        [(b"img1", "Jaw Tip"), (b"img2", "Grip")], [], 4,
    )
    assert len(items) == 2 and items[0]["thumb"] == b"img1"

    # 底条不复读副标题（副标题已在标题区出现）
    assert _promo_tagline([], "Fits in your pocket") == "PRECISION BUILT · EVERYDAY RELIABLE"
    assert "POCKET" not in _promo_tagline([], "Fits in your pocket")


def test_pose_plan_rotates_orientation_and_can_be_disabled() -> None:
    from wh_local.modules.product_processing.domain.marketing_poses import (
        pose_for,
        pose_rule,
    )

    poses = [pose_for(index) for index in range(4)]
    # 前 4 个输出位拿到 4 种不同朝向，避免一批图全是同一个姿势
    assert poses[0] == "upright"
    assert len(set(poses)) == 4
    # 关闭多角度：所有位统一正向
    assert all(pose_for(index, enabled=False) == "upright" for index in range(4))
    assert "tilted about 30 degrees" in pose_rule("tilt_right")
    # 套装要求所有件一起按同一朝向摆放
    assert "All 3 pieces" in pose_rule("diagonal", set_count=3)


def test_build_prompt_injects_pose_rule_but_not_for_white_main() -> None:
    from wh_local.modules.product_processing.domain.marketing_gen import build_prompt

    prompt = build_prompt("headline_hero", pose="diagonal")
    assert "laid diagonally" in prompt
    assert "Re-stage it even if the reference image shows a different pose." in prompt
    assert "laid diagonally" not in build_prompt("headline_hero")
    # 白底主图始终正向：即使误传 pose 也不注入朝向约束
    assert "Re-stage" not in build_prompt("white_main", pose="diagonal")


def test_white_main_uses_single_candidate_but_layouts_use_two() -> None:
    """白底主图是工具图：1 张即可；版式图 2 张候选择优（省钱，Pro 补救通道兜底）。"""
    from wh_local.modules.product_processing.domain.marketing_pipeline import (
        plan_for,
        render_blocks,
    )

    counts: list[int] = []

    def fake_render(_prompt: str, _refs: list[str], count: int = 1) -> bytes:
        counts.append(count)
        return _photo_bytes(200)

    render_blocks(
        plan=plan_for("white_main"),
        copy=MarketingCopy(headline="TOUGH"),
        refs=["https://img.example/a.jpg"],
        render=fake_render,
    )
    assert counts == [1]

    counts.clear()
    render_blocks(
        plan=plan_for("headline_hero"),
        copy=MarketingCopy(headline="TOUGH"),
        refs=["https://img.example/a.jpg"],
        render=fake_render,
    )
    assert counts == [2]


def test_marketing_images_generate_copy_once_and_forward_constraints(monkeypatch) -> None:
    """每商品只调用一次差异化文案集（不是每版式一次），并把主体/注意点透传到生图层。"""
    from wh_local.modules.product_processing import doubao_ark, service as service_module
    from wh_local.modules.product_processing.domain import marketing_pipeline as pipeline

    class _FakeArk:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def complete(self, *_args, **_kwargs) -> str:
            return "{}"

    monkeypatch.setattr(doubao_ark, "DoubaoArkClient", _FakeArk)

    service = object.__new__(service_module.ProductProcessingService)
    import threading

    service._reference_layout_lock = threading.Lock()
    service._reference_layout_cache = {}
    copy_calls: list[tuple] = []
    forwarded: list[dict] = []

    service._raise_if_task_stopped = lambda *_args, **_kwargs: None
    service._media_processor = lambda: object()
    service._marketing_category_id = lambda **_kwargs: "general"
    service._translate_combo_prompt = lambda text: {
        "把手必须是两只": "the handle must have two pieces",
        "适合户外": "suitable for outdoor use",
    }.get(str(text or "").strip(), str(text or "").strip())

    def fake_copy_set(
        title,
        category,
        templates,
        *,
        subject="",
        caution="",
        key_info="",
        bullet_count=6,
        set_count=1,
    ):
        copy_calls.append((tuple(templates), subject, caution, bullet_count, key_info))
        return {
            key: MarketingCopy(headline=f"TOUGH {key}", subline="", bullets=["A"])
            for key in templates
        }

    service._marketing_copy_set = fake_copy_set
    service._persist_media_for_preview = lambda _parts, *_args, **_kwargs: ["preview://x"]

    def fake_render_blocks(**kwargs):
        forwarded.append(kwargs)
        return {"hero": b"hero", "details": [], "detail_source": "label"}

    monkeypatch.setattr(pipeline, "render_blocks", fake_render_blocks)
    monkeypatch.setattr(pipeline, "compose", lambda **_kwargs: b"jpeg")
    # 白底主图优先本地派生：模拟抠图成功，不应再触发专用白底生成。
    from io import BytesIO as _BytesIO

    from PIL import Image as _Image

    from wh_local.modules.product_processing.domain import dimension_source_reuse

    white_buffer = _BytesIO()
    _Image.new("RGB", (800, 800), (255, 255, 255)).save(white_buffer, format="JPEG")
    monkeypatch.setattr(
        dimension_source_reuse,
        "clean_product_canvas",
        lambda *_args, **_kwargs: white_buffer.getvalue(),
    )
    notes: list[str] = []

    values, white_main = service._generate_marketing_images(
        1,
        2,
        title="Pliers",
        category="",
        reference_urls=["https://img.example/a.jpg"],
        workspace_id="local",
        templates=["headline_hero", "icon_grid"],
        vision_subject="two-piece pliers",
        caution="把手必须是两只",
        ai_notes=notes,
    )

    assert len(copy_calls) == 1, "文案集必须每商品一次，而不是每版式一次"
    assert copy_calls[0][1] == "two-piece pliers"
    assert copy_calls[0][2] == "把手必须是两只"
    # 只有 2 个版式；白底主图由本地抠图派生，不再多一次生图
    assert len(forwarded) == 2
    assert all(item["subject"] == "two-piece pliers" for item in forwarded)
    assert all(item["instructions"] == "the handle must have two pieces" for item in forwarded)
    # 每个版式拿到自己的差异化文案（标题互不相同，不再复读同一份）
    assert [item["copy"].headline for item in forwarded] == [
        "TOUGH headline_hero",
        "TOUGH icon_grid",
    ]
    # 同一商品的多个模板要拿到不同结构
    skeletons = [item.get("skeleton") for item in forwarded]
    assert len(set(skeletons)) == 2
    assert len(values) == 2
    assert white_main == "preview://x"
    assert "marketing:white-main:local-cutout" in notes
    # 多角度默认开启：两个版式拿到不同朝向（第 1 位正向，第 2 位右倾）
    assert [item.get("pose") for item in forwarded] == ["upright", "tilt_right"]

    # 多张候选参考图时，每个版式换一张首图（主参考仍带上保真）
    service._marketing_pick_references = (
        lambda _values, **_kwargs: [
            "https://img.example/a.jpg",
            "https://img.example/b.jpg",
        ]
    )
    forwarded.clear()
    notes.clear()
    service._generate_marketing_images(
        1,
        2,
        title="Pliers",
        category="",
        reference_urls=["https://img.example/a.jpg"],
        workspace_id="local",
        templates=["headline_hero", "icon_grid"],
        ai_notes=notes,
    )
    assert [item.get("refs") for item in forwarded] == [
        ["https://img.example/a.jpg"],
        ["https://img.example/b.jpg", "https://img.example/a.jpg"],
    ]

    forwarded.clear()
    notes.clear()
    service._generate_marketing_images(
        1,
        2,
        title="Pliers",
        category="",
        reference_urls=["https://img.example/a.jpg"],
        workspace_id="local",
        templates=["headline_hero", "icon_grid"],
        pose_variety=False,
        ai_notes=notes,
    )
    # 关闭多角度：不注入朝向，参考图也不做轮换，行为回到旧版
    assert [item.get("pose") for item in forwarded] == ["", ""]
    assert all(
        item.get("refs") == ["https://img.example/a.jpg", "https://img.example/b.jpg"]
        for item in forwarded
    )

    # 用户「重要信息」：原文进文案（由模型翻英文），译文只作为场景/氛围进生图提示词。
    forwarded.clear()
    copy_calls.clear()
    notes.clear()
    service._generate_marketing_images(
        1,
        2,
        title="Pliers",
        category="",
        reference_urls=["https://img.example/a.jpg"],
        workspace_id="local",
        templates=["headline_hero"],
        key_info="适合户外",
        ai_notes=notes,
    )
    assert copy_calls[0][4] == "适合户外"
    instruction = forwarded[0]["instructions"]
    assert "Operator key information: suitable for outdoor use." in instruction
    assert "never render any text" in instruction

    # 纯白模板不注入场景句（「纯白背景」与「按场景画」自相矛盾，会把白底带跑）
    forwarded.clear()
    notes.clear()
    service._generate_marketing_images(
        1,
        2,
        title="Pliers",
        category="",
        reference_urls=["https://img.example/a.jpg"],
        workspace_id="local",
        templates=["headline_hero"],
        key_info="适合户外",
        style_template="white",
        ai_notes=notes,
    )
    assert "Operator key information" not in forwarded[0]["instructions"]

