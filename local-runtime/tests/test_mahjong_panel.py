from __future__ import annotations

import io
import time

import pytest
from PIL import Image, ImageDraw

from wh_local.modules.product_processing.domain.mahjong_panel import (
    MAHJONG_BLANK_FACE_RULE,
    MahjongPatternLibrary,
    build_pattern_library,
    build_source_panels,
    choose_distinct_patterns,
    complete_pattern_by_symmetry,
    extract_pattern,
    is_blank_face,
    library_key,
    local_blank_panel,
    normalize_pattern_orientation,
    paste_pattern,
    paste_patterns_in_scene,
    score_source_image,
    symmetry_scores,
)

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")


def _face_bytes(pattern: str = "flower", *, size=(300, 400), angle: float = 0.0) -> bytes:
    """白底牌面 + 深色刻花，模拟透视矫正后的正视图。"""
    image = Image.new("RGB", size, (238, 238, 236))
    draw = ImageDraw.Draw(image)
    width, height = size
    cx, cy = width // 2, height // 2
    if pattern == "flower":
        draw.ellipse((cx - 40, cy - 40, cx + 40, cy + 40), outline=(45, 45, 45), width=8)
        draw.ellipse((cx - 14, cy - 14, cx + 14, cy + 14), outline=(45, 45, 45), width=6)
        draw.rectangle((cx - 5, cy - 70, cx + 5, cy + 70), fill=(45, 45, 45))
        draw.rectangle((cx - 70, cy - 5, cx + 70, cy + 5), fill=(45, 45, 45))
    else:  # 非对称字形：L 形
        draw.rectangle((cx - 60, cy - 70, cx + 10, cy - 50), fill=(45, 45, 45))
        draw.rectangle((cx - 60, cy - 70, cx - 40, cy + 70), fill=(45, 45, 45))
    if angle:
        image = image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=(238, 238, 236))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _pattern_rgba(shape: str = "symmetric", *, occlude: float = 0.0) -> bytes:
    canvas = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    if shape == "symmetric":
        draw.rectangle((50, 40, 150, 60), fill=(40, 40, 40, 255))
        draw.rectangle((90, 40, 110, 160), fill=(40, 40, 40, 255))
        draw.ellipse((60, 90, 140, 150), outline=(40, 40, 40, 255), width=6)
    else:
        draw.rectangle((60, 40, 140, 58), fill=(40, 40, 40, 255))
        draw.rectangle((60, 40, 78, 160), fill=(40, 40, 40, 255))
    if occlude > 0:
        pixels = np.array(canvas)
        columns = np.where((pixels[..., 3] > 0).any(axis=0))[0]
        left, right = int(columns.min()), int(columns.max())
        cut = int(left + (right - left + 1) * (1.0 - occlude))
        pixels[:, cut:, 3] = 0
        canvas = Image.fromarray(pixels)
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()


def _scene_panel_bytes() -> bytes:
    """模拟「实景多牌」面板：深木台面 + 两张玉色牌（一张带倾斜）。"""
    image = Image.new("RGB", (600, 600), (58, 44, 36))
    for left, top, angle in ((90, 130, 0), (320, 200, 9)):
        tile = Image.new("RGBA", (150, 210), (0, 0, 0, 0))
        draw = ImageDraw.Draw(tile)
        draw.rounded_rectangle(
            (0, 0, 149, 209),
            radius=14,
            fill=(176, 210, 196, 255),
            outline=(120, 160, 145, 255),
            width=3,
        )
        tile = tile.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
        image.paste(tile, (left, top), tile)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_face_rule_keeps_scene_and_only_blanks_faces():
    lowered = MAHJONG_BLANK_FACE_RULE.lower()
    assert "keep the scene exactly as planned" in lowered
    assert "never reduce the shot to a single tile" in lowered
    assert "front faces change" in lowered
    assert "no character" in lowered


def test_extract_pattern_returns_asset_for_symmetric_motif():
    asset = extract_pattern(_face_bytes("flower"))
    assert asset is not None
    assert 0.0 < asset.bbox[2] <= 1.0
    assert 0.0 < asset.bbox[3] <= 1.0
    assert asset.blank_face
    assert asset.symmetry_score > 0.5
    assert asset.signature


def test_normalize_pattern_orientation_recovers_small_tilt():
    tilted = _pattern_rgba("symmetric")
    raw = Image.open(io.BytesIO(tilted))
    rotated = raw.rotate(7, resample=Image.Resampling.BICUBIC, expand=True)
    buffer = io.BytesIO()
    rotated.save(buffer, format="PNG")

    _, angle = normalize_pattern_orientation(buffer.getvalue())
    assert -14.0 <= angle <= -2.0, angle


def test_complete_pattern_by_symmetry_fills_one_block_occlusion():
    content = _pattern_rgba("symmetric", occlude=0.2)
    before = max(symmetry_scores(content).values())
    filled, kind, score, completion = complete_pattern_by_symmetry(content)
    assert kind == "vertical"
    assert completion > 0
    assert score >= before
    assert score >= 0.85


def test_complete_pattern_by_symmetry_rejects_asymmetric_glyph():
    content = _pattern_rgba("asymmetric", occlude=0.2)
    _, _, _, completion = complete_pattern_by_symmetry(content)
    assert completion == 0.0


def test_choose_distinct_patterns_never_repeats():
    assets = [extract_pattern(_face_bytes("flower", angle=index)) for index in range(4)]
    pool = [asset for asset in assets if asset is not None]
    picked = choose_distinct_patterns(pool, 4, seed="sku-1")
    signatures = [asset.signature for asset in picked]
    assert len(signatures) == len(set(signatures))


def test_paste_pattern_keeps_aspect_and_darkens_face():
    asset = extract_pattern(_face_bytes("flower"))
    assert asset is not None
    blank = local_blank_panel(asset.blank_face, side=600)
    assert is_blank_face(blank)
    pasted = paste_pattern(blank, asset)
    before = np.asarray(Image.open(io.BytesIO(blank)).convert("L"), dtype=np.float32)
    after = np.asarray(Image.open(io.BytesIO(pasted)).convert("L"), dtype=np.float32)
    assert after.mean() < before.mean()
    assert after.shape == before.shape


def test_paste_patterns_in_scene_keeps_background_and_fills_faces():
    asset = extract_pattern(_face_bytes("flower"))
    assert asset is not None
    panel = _scene_panel_bytes()
    before = np.asarray(Image.open(io.BytesIO(panel)).convert("L"), dtype=np.float32)
    pasted, count = paste_patterns_in_scene(panel, [asset], max_faces=2)
    after = np.asarray(Image.open(io.BytesIO(pasted)).convert("L"), dtype=np.float32)
    assert count >= 1
    assert after.shape == before.shape
    # 台面角落保持原样：没有整格换灰底，也没有把背景抹掉。
    assert abs(float(after[:60, :60].mean()) - float(before[:60, :60].mean())) < 1.0
    # 牌面被压暗（深色刻纹 multiply 上去）。
    assert float(after.mean()) < float(before.mean())


def test_build_source_panels_prefers_clean_photo_over_banner_collage():
    clean = _scene_panel_bytes()
    # 上下各一条高饱和「促销横幅」：边框脏、应排到后面。
    banner = Image.new("RGB", (600, 600), (238, 232, 220))
    draw = ImageDraw.Draw(banner)
    draw.rectangle((0, 0, 600, 90), fill=(230, 0, 120))
    draw.rectangle((0, 510, 600, 600), fill=(255, 90, 0))
    buffer = io.BytesIO()
    banner.save(buffer, format="PNG")
    collage = buffer.getvalue()

    assert score_source_image(clean) > score_source_image(collage)

    panels = build_source_panels([collage, clean], side=512, count=4)
    assert len(panels) == 4
    for panel in panels:
        with Image.open(io.BytesIO(panel)) as image:
            assert image.size == (512, 512)


def test_mahjong_mode_uses_source_images_without_any_ai_call(monkeypatch):
    import wh_local.modules.product_processing.service as service_module
    from wh_local.modules.product_processing.service import ProductProcessingService

    monkeypatch.delenv("WH_MAHJONG_LOCAL_PASTE", raising=False)
    monkeypatch.setenv("WH_MAHJONG_SOURCE_DIRECT", "1")
    scene = _scene_panel_bytes()
    service = object.__new__(ProductProcessingService)
    service.repository = _Repository()
    processor = _BlankProcessor(scene)
    monkeypatch.setattr(service_module, "_ai_enabled", lambda: True)
    monkeypatch.setattr(service_module, "_media_types", lambda: (object, RuntimeError, ValueError))
    monkeypatch.setattr(service, "_media_processor", lambda: processor)
    monkeypatch.setattr(service, "_mahjong_source_panels", lambda *a, **k: [scene] * 4)
    monkeypatch.setattr(
        service,
        "_persist_media_for_preview",
        lambda parts, _task_id, _draft_id, _workspace_id: [
            f"https://example.com/{part.stage}.png" for part in parts
        ],
    )

    output = service._generate_grid_images(
        1,
        2,
        {"category_path": "Sports > Mahjong"},
        "Mahjong Tile Set",
        "Mahjong",
        ["https://example.com/source.jpg"],
        "en",
        "US",
        image_generation_count=4,
        mahjong_mode=True,
    )

    assert processor.calls == []  # 原图直出：一次生图都不调
    assert output.provider_status_class == "local_source"
    assert output.attempt_count == 0
    assert len(output.carousel_urls) == 4


def test_mahjong_3d_mode_returns_rendered_panels_without_ai(monkeypatch):
    import wh_local.modules.product_processing.service as service_module
    from wh_local.modules.product_processing.service import ProductProcessingService

    monkeypatch.setenv("WH_MAHJONG_3D", "1")
    monkeypatch.setattr(service_module, "_mahjong_render_available", lambda: True)

    asset = extract_pattern(_face_bytes("flower"))
    assert asset is not None
    rendered = _face_bytes("flower")

    service = object.__new__(ProductProcessingService)
    service.repository = _Repository()
    processor = _BlankProcessor(_scene_panel_bytes())
    monkeypatch.setattr(service_module, "_ai_enabled", lambda: True)
    monkeypatch.setattr(service_module, "_media_types", lambda: (object, RuntimeError, ValueError))
    monkeypatch.setattr(service, "_media_processor", lambda: processor)
    monkeypatch.setattr(service, "_mahjong_pattern_pool", lambda *a, **k: [asset])
    monkeypatch.setattr(
        service, "_mahjong_render_panels", lambda assets, notes=None, **kw: [rendered] * 4
    )
    monkeypatch.setattr(
        service,
        "_persist_media_for_preview",
        lambda parts, _task_id, _draft_id, _workspace_id: [
            f"https://example.com/{part.stage}.png" for part in parts
        ],
    )

    output = service._generate_grid_images(
        1,
        2,
        {"category_path": "Sports > Mahjong"},
        "Mahjong Tile Set",
        "Mahjong",
        ["https://example.com/source.jpg"],
        "en",
        "US",
        image_generation_count=4,
        mahjong_mode=True,
    )

    # 3D 渲染出图：一次 provider 都不调，面板来自本地渲染。
    assert processor.calls == []
    assert output.provider_status_class == "local_source"
    assert len(output.carousel_urls) == 4
    assert {part.provider for part in output.carousel_media} == {"mahjong-3d"}
    assert all(part.content == rendered for part in output.carousel_media)


def test_mahjong_3d_mode_falls_back_to_ai_when_render_fails(monkeypatch):
    import wh_local.modules.product_processing.service as service_module
    from wh_local.modules.product_processing.service import ProductProcessingService

    monkeypatch.setenv("WH_MAHJONG_3D", "1")
    monkeypatch.setattr(service_module, "_mahjong_render_available", lambda: True)

    asset = extract_pattern(_face_bytes("flower"))
    assert asset is not None
    scene = _scene_panel_bytes()

    service = object.__new__(ProductProcessingService)
    service.repository = _Repository()
    processor = _BlankProcessor(scene)
    monkeypatch.setattr(service_module, "_ai_enabled", lambda: True)
    monkeypatch.setattr(service_module, "_media_types", lambda: (object, RuntimeError, ValueError))
    monkeypatch.setattr(service, "_media_processor", lambda: processor)
    monkeypatch.setattr(service, "_mahjong_pattern_pool", lambda *a, **k: [asset])
    monkeypatch.setattr(service, "_mahjong_render_panels", lambda assets, notes=None, **kw: [])
    monkeypatch.setattr(
        service_module,
        "inspect_visible_text",
        lambda _content: {"chinese": [], "prominent": []},
    )
    monkeypatch.setattr(
        service,
        "_persist_media_for_preview",
        lambda parts, _task_id, _draft_id, _workspace_id: [
            f"https://example.com/{part.stage}.png" for part in parts
        ],
    )

    notes: list[str] = []
    output = service._generate_grid_images(
        1,
        2,
        {"category_path": "Sports > Mahjong"},
        "Mahjong Tile Set",
        "Mahjong",
        ["https://example.com/source.jpg"],
        "en",
        "US",
        image_generation_count=4,
        mahjong_mode=True,
        ai_notes=notes,
    )

    # 渲染不可用时必须安静退回 AI，不能把整个任务拖挂。
    assert len(processor.calls) == 1
    assert "mahjong:render:fallback-ai" in notes
    assert len(output.carousel_urls) == 4


def test_mahjong_3d_requested_follows_task_setting(monkeypatch):
    import wh_local.modules.product_processing.service as service_module

    monkeypatch.delenv("WH_MAHJONG_3D", raising=False)
    # 任务级选择优先：前端选了 3D 才渲染，选 ai/空都保持 AI 生图。
    assert service_module._mahjong_3d_requested("3d") is True
    assert service_module._mahjong_3d_requested("AI") is False
    assert service_module._mahjong_3d_requested("") is False
    # 环境变量作为兜底（服务端强制开启）。
    monkeypatch.setenv("WH_MAHJONG_3D", "1")
    assert service_module._mahjong_3d_requested("") is True


def test_mahjong_style_detects_us_and_cn():
    import wh_local.modules.product_processing.service as service_module

    # 美式/美国/Joker 必须判定成 us，否则会拿中式中發白去渲美式牌（等于换商品）。
    assert service_module._mahjong_style("美国麻将160张亚克力四层雕刻美式手搓麻将Joker牌") == "us"
    assert service_module._mahjong_style("跨境美国麻将 美式160张") == "us"
    assert service_module._mahjong_style("麻将麻将牌手搓家用大号麻雀42mm象牙色") == "cn"
    assert service_module._mahjong_style("") == "cn"


def test_mahjong_presets_are_available():
    from wh_local.modules.product_processing.domain.mahjong_render import preset_path

    us = preset_path("us_mahjong_gold_glitter")
    cn = preset_path("cn_mahjong_ivory")
    assert us is not None and us.name == "us_mahjong_gold_glitter.json"
    assert cn is not None and cn.name == "cn_mahjong_ivory.json"
    assert preset_path("not_a_preset") is None


def test_pattern_library_roundtrip_dedupes(tmp_path):
    asset = extract_pattern(_face_bytes("flower"))
    assert asset is not None
    library = MahjongPatternLibrary(tmp_path)
    key = library_key("麻将", "棋牌室专用麻将牌")
    first = library.extend(key, [asset])
    second = library.extend(key, [asset, asset])
    assert len(first) == len(second) == 1
    loaded = library.load(key)
    assert loaded and loaded[0].signature == asset.signature


def test_build_pattern_library_stops_within_time_budget():
    started = time.perf_counter()
    assert build_pattern_library([], time_budget_s=0.0) == []
    assert time.perf_counter() - started < 1.0


class _Repository:
    @staticmethod
    def prompts() -> dict[str, str]:
        return {}

    @staticmethod
    def active_prompt_template() -> None:
        return None


class _BlankProcessor:
    """四宫格返回真空白牌（不是占位字节），否则空白门/回贴都无从验证。"""

    def __init__(self, blank: bytes) -> None:
        self.blank = blank
        self.calls: list[dict] = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return _media("grid_image", kwargs.get("prompt", "").encode()[:64])

    def split_four_grid(self, _grid_media, *, enhance=None, preserve_aspect=False):
        return [
            _media(f"grid_image_{slot}", self.blank) for slot in range(1, 5)
        ] + [_media("grid_image_summary", self.blank)]


def _media(stage: str, content: bytes):
    from wh_local.modules.product_processing.infrastructure.media import GeneratedMedia

    return GeneratedMedia(
        stage=stage,
        content=content,
        content_type="image/png",
        suffix=".png",
        provider="fake",
        model="fake",
        reference_count=1,
    )


def test_mahjong_mode_uses_blank_prompt_then_pastes_patterns(monkeypatch):
    import wh_local.modules.product_processing.service as service_module
    from wh_local.modules.product_processing.service import ProductProcessingService

    # 本地贴图默认关闭（图案素材太糊），这里显式打开以覆盖贴图链路。
    monkeypatch.setenv("WH_MAHJONG_LOCAL_PASTE", "1")

    asset = extract_pattern(_face_bytes("flower"))
    assert asset is not None
    scene = _scene_panel_bytes()

    service = object.__new__(ProductProcessingService)
    service.repository = _Repository()
    processor = _BlankProcessor(scene)
    monkeypatch.setattr(service_module, "_ai_enabled", lambda: True)
    monkeypatch.setattr(service_module, "_media_types", lambda: (object, RuntimeError, ValueError))
    monkeypatch.setattr(service, "_media_processor", lambda: processor)
    monkeypatch.setattr(service, "_mahjong_pattern_pool", lambda *a, **k: [asset])
    monkeypatch.setattr(
        service_module,
        "inspect_visible_text",
        lambda _content: {"chinese": [], "prominent": []},
    )
    monkeypatch.setattr(
        service,
        "_persist_media_for_preview",
        lambda parts, _task_id, _draft_id, _workspace_id: [
            f"https://example.com/{part.stage}.png" for part in parts
        ],
    )

    output = service._generate_grid_images(
        1,
        2,
        {"category_path": "Sports > Mahjong"},
        "Mahjong Tile Set",
        "Mahjong",
        ["https://example.com/source.jpg"],
        "en",
        "US",
        image_generation_count=4,
        mahjong_mode=True,
    )

    assert len(processor.calls) == 1
    prompt = processor.calls[0]["prompt"]
    # 场景/机位/道具仍然沿用普通四宫格，只在末尾压一层「牌面留白」。
    assert "MAHJONG FACE OVERRIDE" in prompt
    assert "uniform light-gray background" not in prompt
    assert len(output.carousel_urls) == 4
    assert all(part.content != scene for part in output.carousel_media)
    pasted = np.asarray(
        Image.open(io.BytesIO(output.carousel_media[0].content)).convert("L"),
        dtype=np.float32,
    )
    untouched = np.asarray(Image.open(io.BytesIO(scene)).convert("L"), dtype=np.float32)
    assert pasted.mean() < untouched.mean()
    # 台面角落保持原样：绝不能再出现「整格换灰底」。
    assert abs(float(pasted[:60, :60].mean()) - float(untouched[:60, :60].mean())) < 1.5

