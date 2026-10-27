from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from types import SimpleNamespace

import pytest
import requests
from PIL import Image, JpegImagePlugin

from wh_local.modules.product_processing import service as service_module
from wh_local.modules.product_processing import provider_config as provider_config_module
from wh_local.modules.ai_service.temporary_cos import TemporaryReferenceError
from wh_local.modules.product_processing.domain.prompts import (
    ARK_GRID_IMAGE_PROMPT,
    ARK_GRID_IMAGE_PROMPT_B,
    ARK_GRID_IMAGE_PROMPT_C,
    ARK_GRID_IMAGE_PROMPT_D,
    GRID_IMAGE_PROMPT,
    GRID_IMAGE_PROMPT_B,
    GRID_IMAGE_PROMPT_C,
    GRID_IMAGE_PROMPT_D,
    GRID_IMAGE_REPAIR_PROMPT,
    GRID_RUNTIME_CONTRACT,
    PREMIUM_IMAGE_PROMPT,
)
from wh_local.modules.product_processing.domain.workbooks import _http_urls
from wh_local.modules.product_processing.infrastructure import ocr_gate
from wh_local.modules.product_processing.infrastructure import media as media_module
from wh_local.modules.product_processing.infrastructure.media import (
    GeneratedMedia,
    MediaProcessingError,
    ProductImageProcessor,
)


def _grid_bytes(size: int = 2048) -> bytes:
    image = Image.new("RGB", (size, size), "white")
    half = size // 2
    image.paste((210, 30, 30), (0, 0, half - 8, half - 8))
    image.paste((30, 180, 50), (half + 8, 0, size, half - 8))
    image.paste((30, 70, 210), (0, half + 8, half - 8, size))
    image.paste((220, 180, 30), (half + 8, half + 8, size, size))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _two_grid_bytes() -> bytes:
    image = Image.new("RGB", (2048, 1024), "white")
    image.paste((210, 30, 30), (0, 0, 1016, 1024))
    image.paste((30, 70, 210), (1032, 0, 2048, 1024))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _media(content: bytes) -> GeneratedMedia:
    return GeneratedMedia(
        stage="grid_image",
        content=content,
        content_type="image/png",
        suffix=".png",
        provider="fake",
        model="fake",
        reference_count=1,
    )


def test_wuyin_result_download_uses_pinned_transport(monkeypatch) -> None:
    class _SubmitResponse:
        ok = True

        def iter_content(self, chunk_size: int = 64 * 1024):
            yield b'{"code":200,"data":{"id":"task-1"}}'

        def close(self) -> None:
            return

    download_urls: list[str] = []

    def download(url: str, **_kwargs):
        download_urls.append(url)
        return b"generated-image", "image/png"

    monkeypatch.setattr(media_module._SESSION, "post", lambda *args, **kwargs: _SubmitResponse())
    monkeypatch.setattr(media_module, "_download_pinned_public_image", download)
    monkeypatch.setattr(media_module.time, "sleep", lambda *_args: None)
    processor = ProductImageProcessor(lambda: {})
    monkeypatch.setattr(
        processor,
        "_poll_wuyin_image_result",
        lambda *_args, **_kwargs: "https://scapi.net/result.png",
    )

    content, content_type = processor._request_wuyin_image(
        {
            "base_url": "https://api.wuyinkeji.com",
            "api_key": "secret",
            "image_size": "2K",
        },
        "prompt",
        [(b"reference", "source.png", "image/png", "https://example.test/source.png")],
        timeout_seconds=600,
    )

    assert content == b"generated-image"
    assert content_type == "image/png"
    assert download_urls == ["https://scapi.net/result.png"]


def test_wuyin_submit_payload_carries_full_prompt_without_truncation(monkeypatch) -> None:
    """上游请求体里的 prompt 必须与传入完全一致，任何长度都不做截断/丢弃。

    回归保护：批次辅助提词可能很长（多品类 bullet 拼接），请求侧不得有
    隐藏的长度上限把它静默截断，否则用户填写的内容会「看起来生效、实际丢失」。
    """
    captured: dict[str, object] = {}

    class _SubmitResponse:
        ok = True

        def iter_content(self, chunk_size: int = 64 * 1024):
            yield b'{"code":200,"data":{"id":"task-long"}}'

        def close(self) -> None:
            return

    def fake_post(_url, *, params=None, headers=None, json=None, **_kwargs):
        captured["json"] = json
        return _SubmitResponse()

    monkeypatch.setattr(media_module._SESSION, "post", fake_post)
    processor = ProductImageProcessor(lambda: {})

    long_prompt = "BATCH-LEVEL CATEGORY EXPERT GUIDELINES\n" + ("GUARD: exactly five fingers, crisp labels.\n" * 4000)
    task_id = processor._submit_wuyin_image_task(
        {"base_url": "https://api.wuyinkeji.com", "api_key": "secret", "model": "image_gpt"},
        long_prompt,
        ["https://example.test/source.png"],
        model="image_gpt",
        image_size="2K",
        timeout_seconds=30,
    )

    assert task_id == "task-long"
    assert captured["json"]["prompt"] == long_prompt
    assert len(captured["json"]["prompt"]) == len(long_prompt)


def test_wuyin_nano_banana_submit_uses_tier_size_and_dedicated_path(monkeypatch) -> None:
    """无印 nanoBanana 系：走专用路径，size 传画质档（2K），比例走 aspectRatio。

    该模型与 gpt-image 属不同上游，用于「gpt-image 整族维护/不可用」时的替换/兜底。
    请求体若沿用 image_gpt 的「size=比例」，上游会按默认 1K 出图（四宫格每格仅 512px），
    因此这里的字段语义必须有回归保护。
    """
    captured: dict[str, object] = {}

    class _SubmitResponse:
        ok = True

        def iter_content(self, chunk_size: int = 64 * 1024):
            yield b'{"code":200,"data":{"id":"task-nano"}}'

        def close(self) -> None:
            return

    def fake_post(url, *, params=None, headers=None, json=None, **_kwargs):
        captured["url"] = url
        captured["json"] = json
        return _SubmitResponse()

    monkeypatch.setattr(media_module._SESSION, "post", fake_post)
    processor = ProductImageProcessor(lambda: {})

    task_id = processor._submit_wuyin_image_task(
        {"base_url": "https://api.wuyinkeji.com", "api_key": "secret", "model": "nanoBanana_pro"},
        "grid contract",
        ["https://example.test/a.png", "https://example.test/b.png"],
        model="nanoBanana_pro",
        image_size="2048x2048",
        timeout_seconds=30,
    )

    assert task_id == "task-nano"
    assert captured["url"].endswith("/api/async/image_nanoBanana_pro")
    assert captured["json"]["size"] == "2K"
    assert captured["json"]["aspectRatio"] == "1:1"
    assert captured["json"]["urls"] == ["https://example.test/a.png", "https://example.test/b.png"]
    assert captured["json"]["prompt"] == "grid contract"


def test_wuyin_nano_banana_lite_forces_1k_and_matches_path_by_normalized_name(monkeypatch) -> None:
    """NanoBanana-2-Lite 只支持 1K：size 必须压到 1K，且按归一化模型名命中专用路径。

    回归保护：文档写明「仅支持 1K」，沿用 2K/4K 档会被上游拒绝；模型名写法各页
    不一致（NanoBanana-2-Lite / nanoBanana2Lite），必须能匹配到同一路径。
    """
    captured: dict[str, object] = {}

    class _SubmitResponse:
        ok = True

        def iter_content(self, chunk_size: int = 64 * 1024):
            yield b'{"code":200,"data":{"id":"task-lite"}}'

        def close(self) -> None:
            return

    def fake_post(url, *, params=None, headers=None, json=None, **_kwargs):
        captured["url"] = url
        captured["json"] = json
        return _SubmitResponse()

    monkeypatch.setattr(media_module._SESSION, "post", fake_post)
    processor = ProductImageProcessor(lambda: {})

    task_id = processor._submit_wuyin_image_task(
        {"base_url": "https://api.wuyinkeji.com", "api_key": "secret", "model": "NanoBanana-2-Lite"},
        "panel prompt",
        ["https://example.test/a.png"],
        model="NanoBanana-2-Lite",
        image_size="2048x2048",
        timeout_seconds=30,
    )

    assert task_id == "task-lite"
    assert captured["url"].endswith("/api/async/image_nanoBanana2Lite")
    assert captured["json"]["size"] == "1K"
    assert captured["json"]["aspectRatio"] == "1:1"


def test_ark_image_request_sends_data_uri_refs_and_disables_watermark(monkeypatch) -> None:
    """方舟生图：参考图用 data URI 直传，且必须显式 watermark=False。

    回归保护（实测）：方舟生图默认会打「豆包AI生成」水印，商品图绝不能带；参考图走
    data URI 则不必先把图传上公网，天然免疫「上游抓不到参考图」。
    """
    captured: dict[str, object] = {}

    class _Resp:
        ok = True
        status_code = 200

        def iter_content(self, chunk_size: int = 64 * 1024):
            yield b'{"data":[{"url":"https://ark.example/out.png"}]}'

        def close(self) -> None:
            return

    def fake_post(url, *, headers=None, json=None, **_kwargs):
        captured["url"] = url
        captured["json"] = json
        return _Resp()

    monkeypatch.setattr(media_module._SESSION, "post", fake_post)
    monkeypatch.setattr(
        media_module, "_download_provider_result_image", lambda _url: (b"image-bytes", "image/png")
    )

    processor = ProductImageProcessor(lambda: {})
    content, content_type = processor._request_ark_image(
        {
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "api_key": "secret",
            "model": "doubao-seedream-5-0-pro-260628",
            "reference_model": "doubao-seedream-5-0-pro-260628",
            "image_size": "2048x2048",
        },
        "grid contract",
        [(b"ref-bytes", "a.png", "image/png")],
        timeout_seconds=30,
    )

    assert content == b"image-bytes"
    assert content_type == "image/png"
    assert captured["url"].endswith("/images/generations")
    assert captured["json"]["watermark"] is False
    assert captured["json"]["size"] == "2K"
    assert captured["json"]["output_format"] == "png"
    assert captured["json"]["response_format"] == "url"
    assert captured["json"]["image"][0].startswith("data:image/png;base64,")


def test_wuyin_result_download_never_downgrades_https_to_http(monkeypatch) -> None:
    class _SubmitResponse:
        ok = True

        def iter_content(self, chunk_size: int = 64 * 1024):
            yield b'{"code":200,"data":{"id":"task-1"}}'

        def close(self) -> None:
            return

    download_urls: list[str] = []

    def download(url: str, **_kwargs):
        download_urls.append(url)
        raise media_module.MediaProcessingError("TLS verification failed")

    monkeypatch.setattr(media_module._SESSION, "post", lambda *args, **kwargs: _SubmitResponse())
    monkeypatch.setattr(media_module, "_download_pinned_public_image", download)
    monkeypatch.setattr(media_module.time, "sleep", lambda *_args: None)
    processor = ProductImageProcessor(lambda: {})
    monkeypatch.setattr(
        processor,
        "_poll_wuyin_image_result",
        lambda *_args, **_kwargs: "https://scapi.net/result.png",
    )

    with pytest.raises(media_module.MediaProcessingError, match="TLS verification failed"):
        processor._request_wuyin_image(
            {
                "base_url": "https://api.wuyinkeji.com",
                "api_key": "secret",
                "image_size": "2K",
            },
            "prompt",
            [(b"reference", "source.png", "image/png", "https://example.test/source.png")],
            timeout_seconds=600,
        )

    assert download_urls == ["https://scapi.net/result.png"]


def test_direct_mode_prefers_remote_reference_urls_over_local_cache_paths(tmp_path, monkeypatch) -> None:
    """直连提供方必须收到公网参考图 URL，本地缓存路径不能顶替 URL。

    回归保护：b7fa457 引入的本地缓存替换会让参考值变成 [本地路径, 远端 URL]，
    直连分支若只取到本地路径，提交给提供方的 urls=[]，图生图任务会以
    status=3 静默失败（无图无原因）。直连模式必须把远端 URL 排在前面。
    """
    local_file = tmp_path / "local-cache-source.jpg"
    local_file.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-bytes")
    remote_url = "https://img.kwcdn.com/product/open/source-goods.jpeg"

    captured: dict[str, object] = {}

    def fake_load(values, *, limit):
        captured["values"] = list(values)
        captured["limit"] = limit
        return [(b"reference", "source.png", "image/jpeg", remote_url)]

    def fake_request_edit(*_args, **_kwargs):
        return b"generated-image", "image/png"

    processor = ProductImageProcessor(lambda: {})
    monkeypatch.setattr(processor, "_load_references", fake_load)
    monkeypatch.setattr(processor, "_request_edit", fake_request_edit)

    output = processor._generate_with_limits(
        stage="grid_image",
        prompt="prompt",
        reference_values=[str(local_file), remote_url],
        providers=[
            {
                "base_url": "https://api.wuyinkeji.com",
                "api_key": "secret",
                "name": "image_gpt",
                "model": "image_gpt",
                "reference_model": "",
            }
        ],
        config={},
    )

    # 直连模式：远端 URL 必须排在本地路径前面，提供方才能拿到可下载的参考图。
    assert captured["limit"] == 1
    assert captured["values"][0] == remote_url
    assert output.provider == "image_gpt"


def test_direct_wuyin_relays_local_only_reference_and_cleans_it_up(monkeypatch) -> None:
    """本地缓存图必须通过临时 COS URL 传给无印，完成后立即清理。"""

    class _SubmitResponse:
        ok = True
        status_code = 200

        def iter_content(self, chunk_size: int = 64 * 1024):
            yield b'{"code":200,"data":{"id":"task-1"}}'

        def close(self) -> None:
            return

    class _TemporaryStore:
        def __init__(self) -> None:
            self.published: list[tuple[bytes, str]] = []
            self.deleted: list[object] = []

        def publish(self, content: bytes, content_type: str):
            self.published.append((content, content_type))
            return SimpleNamespace(
                key="ai-service/transient/reference.image",
                url="https://bucket.cos.ap-guangzhou.myqcloud.com/reference.image?signature=short-lived",
            )

        def delete(self, reference) -> None:
            self.deleted.append(reference)

    processor = ProductImageProcessor(lambda: {})
    store = _TemporaryStore()
    submitted: dict[str, object] = {}

    def submit(*_args, **kwargs):
        submitted.update(kwargs)
        return _SubmitResponse()

    monkeypatch.setattr(processor, "_config", lambda: {"cos": {
        "bucket": "bucket",
        "region": "ap-guangzhou",
        "secret_id": "secret-id",
        "secret_key": "secret-key",
    }})
    monkeypatch.setattr(media_module, "TemporaryCosStore", lambda _config: store)
    monkeypatch.setattr(media_module._SESSION, "post", submit)
    monkeypatch.setattr(
        processor,
        "_poll_wuyin_image_result",
        lambda *_args, **_kwargs: "https://scapi.net/result.png",
    )
    monkeypatch.setattr(
        media_module,
        "_download_provider_result_image",
        lambda _url: (b"generated-image", "image/png"),
    )

    content, content_type = processor._request_wuyin_image(
        {
            "base_url": "https://api.wuyinkeji.com",
            "api_key": "secret",
            "image_size": "2K",
        },
        "prompt",
        [(b"reference", "source.png", "image/jpeg")],
        timeout_seconds=600,
    )

    assert (content, content_type) == (b"generated-image", "image/png")
    assert store.published == [(b"reference", "image/jpeg")]
    assert submitted["json"]["urls"] == [
        "https://bucket.cos.ap-guangzhou.myqcloud.com/reference.image?signature=short-lived"
    ]
    assert [item.key for item in store.deleted] == ["ai-service/transient/reference.image"]


def test_wuyin_poll_does_not_print_provider_response(monkeypatch, capsys) -> None:
    """Provider task IDs and result URLs must not leak into production stdout."""

    class _DetailResponse:
        ok = True
        status_code = 200

        def iter_content(self, chunk_size: int = 64 * 1024):
            yield (
                b'{"code":200,"data":{"status":"success",'
                b'"url":"https://scapi.net/private-result.png"}}'
            )

        def close(self) -> None:
            return

    monkeypatch.setattr(media_module._SESSION, "get", lambda *args, **kwargs: _DetailResponse())
    monkeypatch.setattr(media_module.time, "sleep", lambda *_args: None)
    processor = ProductImageProcessor(lambda: {})

    result = processor._poll_wuyin_image_result(
        {"base_url": "https://api.wuyinkeji.com", "api_key": "secret"},
        "sensitive-task-id",
        timeout_seconds=30,
    )

    assert result == "https://scapi.net/private-result.png"
    assert capsys.readouterr().out == ""


def test_four_grid_contract_owns_typography_dividers_and_fidelity_rules() -> None:
    """结构 / 保真 / 文字禁令统一归 GRID_RUNTIME_CONTRACT，四份模板不再各写一遍。"""
    assert "zero AI-added visible text" in GRID_RUNTIME_CONTRACT
    assert "separator is mandatory and is validated" in GRID_RUNTIME_CONTRACT
    assert "uniform, and uninterrupted" in GRID_RUNTIME_CONTRACT
    assert "PRODUCT FIDELITY" in GRID_RUNTIME_CONTRACT
    # 几何畸变（多边形化 / 臆造手柄 / 多出分层）是现场高频缺陷，契约必须带这段硬约束。
    assert "GEOMETRY INTEGRITY" in GRID_RUNTIME_CONTRACT
    # 四格趋同（只有背景在变）是现场高频缺陷，契约必须带「变异矩阵 + 差异下限 + 生成后自查」。
    assert "FRAME VARIATION MATRIX" in GRID_RUNTIME_CONTRACT
    assert "DIFFERENTIATION FLOORS" in GRID_RUNTIME_CONTRACT
    assert "POST-GENERATION SELF-CHECK" in GRID_RUNTIME_CONTRACT
    # 关键：只统一品质，绝不统一机位/光位/景别/色温，否则模型会收敛到同一张图。
    assert "QUALITY FLOOR vs STYLE UNITY" in GRID_RUNTIME_CONTRACT
    assert "never unify camera position" in GRID_RUNTIME_CONTRACT
    assert "15 degrees" in GRID_RUNTIME_CONTRACT
    assert "30 degrees" in GRID_RUNTIME_CONTRACT
    assert "never polygonise" in GRID_RUNTIME_CONTRACT
    assert "handle into a flat bar" in GRID_RUNTIME_CONTRACT
    assert "PANEL ROLES" in GRID_RUNTIME_CONTRACT
    assert "pure macro crop without the complete sellable product is forbidden" in GRID_RUNTIME_CONTRACT
    # 首图填幅按平台惯例提上来，内边距同步收紧（85% 填幅与 10% 边距在数学上互斥）。
    assert "COMPLETENESS & FRAME FILL" in GRID_RUNTIME_CONTRACT
    assert "78%-85%" in GRID_RUNTIME_CONTRACT
    assert "7% inner safe margin" in GRID_RUNTIME_CONTRACT
    # 四条通用能力上提到契约（原先在四套模板里逐字重复）：真实感与光影物理 / 反 AI 痕迹 /
    # 手部解剖 / 艺术方向层 / 策略 1 的尺度锚与卖点证据。
    assert "REALISM & LIGHT PHYSICS" in GRID_RUNTIME_CONTRACT
    assert "ambient-occlusion contact shadow" in GRID_RUNTIME_CONTRACT
    assert "reflection anchor" in GRID_RUNTIME_CONTRACT
    assert "flat grey metal is forbidden" in GRID_RUNTIME_CONTRACT
    assert "caustic pool" in GRID_RUNTIME_CONTRACT
    assert "ANTI-AI-TELL" in GRID_RUNTIME_CONTRACT
    # 默认无人；「真实使用」模板会由 service._grid_runtime_contract 换成受限允许手部。
    assert "HANDS-FREE (hard rule)" in GRID_RUNTIME_CONTRACT
    # 艺术方向层：只允许统一这五类，且必须显式列清「不允许统一」的十项，否则模型会收敛。
    assert "ART DIRECTION LOCK" in GRID_RUNTIME_CONTRACT
    assert "colour grade" in GRID_RUNTIME_CONTRACT
    assert "unifying any of those ten is a failure" in GRID_RUNTIME_CONTRACT
    # 调色优先级：调色底座可统一，但照明色温仍须各格不同——否则模型会二选一。
    assert "NOT the scene's lighting colour" in GRID_RUNTIME_CONTRACT
    assert "PANEL 2 SCALE ANCHOR" in GRID_RUNTIME_CONTRACT
    assert "PANEL 3 BENEFIT PROOF" in GRID_RUNTIME_CONTRACT
    # 尺度锚必须是无文字参照物（手是 AI 第一穿帮点、硬币自带文字可能被误判为加字），
    # Panel 4 必须是真正交而非 45° 视角，Panel 3 还要能指出证据在画面里的位置。
    assert "Never use a hand, palm, finger or person as the scale reference" in GRID_RUNTIME_CONTRACT
    assert "preferably an A4 sheet or a phone" in GRID_RUNTIME_CONTRACT
    assert "never a 45-degree three-quarter view" in GRID_RUNTIME_CONTRACT
    assert "AND point at exactly where in the frame it is visible" in GRID_RUNTIME_CONTRACT
    # 只有 Panel 4 的语义与「1-3 格必须三个互不相同」是全局的；各套模板自己的机位留在模板里。
    assert "Panel 4 (bottom-right) is ALWAYS the dimension-annotation background" in GRID_RUNTIME_CONTRACT
    assert "Do not invent packaging" in GRID_IMAGE_PROMPT_B


def test_grid_templates_delegate_structure_and_skip_conditional_branches() -> None:
    """减重回归护栏：模板只保留各自创作方向，结构契约与条件性分支不得再回到模板里。

    条件性内容（价值/用法展示、季节礼赠、悬挂陈列）已归位到「本批次辅助提词」按品类生成，
    结构/保真/文字禁令已归位到 GRID_RUNTIME_CONTRACT；模板重新膨胀会让单次请求变重、
    上游耗时回升（2026-10-16 实测：模板越重，速创单次出图越慢）。
    """
    templates = (
        GRID_IMAGE_PROMPT,
        GRID_IMAGE_PROMPT_B,
        GRID_IMAGE_PROMPT_C,
        GRID_IMAGE_PROMPT_D,
    )
    duplicated_sections = (
        "CRITICAL LAYOUT UNDERSTANDING",
        "Grid & splitting infrastructure",
        "Output contract (fixed",
        "Generate-then-self-check",
        "Product Integrity Constraints",
        "Value & usage demonstration",
        "Seasonal & gift styling",
        "Display & light staging",
    )
    for template in templates:
        # 占位符注入仍然必须齐备（format_prompt 依赖它们）。
        for placeholder in (
            "{category_path}",
            "{title}",
            "{value_evidence}",
            "{scene_plan}",
            "{visual_style}",
            "{background_plan}",
            "{verified_material_evidence}",
        ):
            assert placeholder in template
        for section in duplicated_sections:
            assert section not in template, section
        assert "Panel 4 (bottom-right)" in template
        assert len(template) < 12_000, len(template)


def test_ark_grid_template_is_region_planned_and_delegates_the_contract() -> None:
    """四份方舟紧凑版都必须：区域规划成篇、带光影与自然度、且不抄契约条款。"""
    variants = {
        "A": ARK_GRID_IMAGE_PROMPT,
        "B": ARK_GRID_IMAGE_PROMPT_B,
        "C": ARK_GRID_IMAGE_PROMPT_C,
        "D": ARK_GRID_IMAGE_PROMPT_D,
    }
    for name, template in variants.items():
        assert "【区域规划" in template, name
        for panel in ("【第①格", "【第②格", "【第③格", "【第④格"):
            assert panel in template, (name, panel)
        assert "光影技法" not in template, name
        assert "自然度要点" not in template, name
        # 真实感/光影物理/反 AI 痕迹已上提到契约，模板只留一句委派，不再各写一遍。
        assert "布光与真实感" in template, name
        assert "REALISM & LIGHT PHYSICS" in template, name
        # 首图填幅与契约同步（平台惯例：白底主图必须做大）。
        assert "78%-85%" in template, name
        # 策略 1/2 必须落在模板的逐格指令里（写在契约里模型注意力会被稀释，实测不生效）。
        assert "尺度锚（本格硬要求）" in template, name
        # 尺度锚优先无文字参照物（A4 纸 / 手机），只有「环境远景」套允许以画面里的已知尺寸物为先。
        assert "A4 纸或手机优先" in template, name
        assert "卖点证据（本格硬要求）" in template, name
        assert "具体位置" in template, name
        assert "正交硬要求" in template, name
        assert "调色与成像底座" in template, name
        assert "不是场景照明色" in template, name
        # 四格趋同的两个根因必须被钉死：逐格参数矩阵（硬参数）+ 不再统一布光。
        assert "逐格参数矩阵" in template, name
        assert "四格不得复用同一套参数" in template, name
        assert "不统一机位、光位、景别与色温" in template, name
        assert "四格不得沿用同一套布光" in template, name
        # 矩阵必须给到具体的角度/光比/色温数值，不能只说「不同」
        for numeric in ("平视 0°", "光比 3:1", "5500K"):
            assert numeric in template, (name, numeric)
        # 契约已承载的条款不再抄一遍（两处规则打架 / 体量膨胀）
        for duplicated in ("版式：", "以参考图为唯一依据", "画面内不出现任何文字", "GEOMETRY"):
            assert duplicated not in template, (name, duplicated)
        # 方舟对输入文本做审核：每份紧凑版都必须保持精简
        assert len(template) < 2_500, (name, len(template))
    # 四份必须彼此不同：否则界面上的模板选择等于没生效
    assert len({*variants.values()}) == 4


def test_ark_chain_keeps_the_ui_template_selection_effective() -> None:
    """方舟链路必须按界面选的模板后缀挑对应紧凑版，而不是一律用同一份。"""
    mapping = service_module._ARK_GRID_TEMPLATES_BY_SUFFIX
    assert mapping[""] is ARK_GRID_IMAGE_PROMPT
    assert mapping["_b"] is ARK_GRID_IMAGE_PROMPT_B
    assert mapping["_c"] is ARK_GRID_IMAGE_PROMPT_C
    assert mapping["_d"] is ARK_GRID_IMAGE_PROMPT_D


def test_split_four_grid_uses_exact_center_and_preserves_outer_edges() -> None:
    processor = ProductImageProcessor(lambda: {})
    parts = processor.split_four_grid(_media(_grid_bytes()))
    assert len(parts) == 5
    expected = [(210, 30, 30), (30, 180, 50), (30, 70, 210), (220, 180, 30)]
    for part, color in zip(parts[:4], expected):
        with Image.open(BytesIO(part.content)) as image:
            assert image.size == (800, 800)
            actual = image.convert("RGB").getpixel((400, 400))
            assert all(abs(value - wanted) <= 4 for value, wanted in zip(actual, color))


def test_split_four_grid_accepts_provider_1k_fallback() -> None:
    processor = ProductImageProcessor(lambda: {})
    parts = processor.split_four_grid(_media(_grid_bytes(1024)))
    assert len(parts) == 5
    for part in parts:
        with Image.open(BytesIO(part.content)) as image:
            assert image.size == (800, 800)


def test_split_four_grid_rejects_source_below_1k_with_dimensions() -> None:
    processor = ProductImageProcessor(lambda: {})
    with pytest.raises(MediaProcessingError, match=r"900x900.*at least 1024px"):
        processor.split_four_grid(_media(_grid_bytes(900)))


def test_split_premium_four_grid_preserves_approximately_2k_panels() -> None:
    processor = ProductImageProcessor(lambda: {})
    parts = processor.split_premium_four_grid(_media(_grid_bytes(4096)))

    assert [part.stage for part in parts] == [
        "premium_image_1",
        "premium_image_2",
        "premium_image_3",
        "premium_image_4",
        "premium_image_summary",
    ]
    for part in parts[:4]:
        with Image.open(BytesIO(part.content)) as image:
            assert image.size == (2048, 2048)
            assert image.format == "JPEG"
            assert JpegImagePlugin.get_sampling(image) == 0
    with Image.open(BytesIO(parts[-1].content)) as image:
        assert image.size == (800, 800)


def test_split_premium_four_grid_accepts_2k_gateway_output_and_uses_exact_center() -> None:
    processor = ProductImageProcessor(lambda: {})
    parts = processor.split_premium_four_grid(_media(_grid_bytes(2048)))
    assert len(parts) == 5
    for part in parts[:4]:
        with Image.open(BytesIO(part.content)) as opened:
            assert opened.size == (1024, 1024)


def test_split_premium_four_grid_rejects_too_small_or_repeated_panels() -> None:
    processor = ProductImageProcessor(lambda: {})
    with pytest.raises(MediaProcessingError, match="cannot be split"):
        processor.split_premium_four_grid(_media(_grid_bytes(1700)))

    repeated = Image.new("RGB", (2048, 2048), (30, 70, 120))
    buffer = BytesIO()
    repeated.save(buffer, format="PNG")
    with pytest.raises(MediaProcessingError, match="cannot be split"):
        processor.split_premium_four_grid(_media(buffer.getvalue()))


def test_premium_prompt_requires_exact_split_safe_4k_grid() -> None:
    assert "4096 x 4096" in PREMIUM_IMAGE_PROMPT
    assert "exactly FOUR equal square panels" in PREMIUM_IMAGE_PROMPT
    assert "50% vertical center" in PREMIUM_IMAGE_PROMPT
    assert "50% horizontal center" in PREMIUM_IMAGE_PROMPT
    assert "Nothing may cross either center divider" in PREMIUM_IMAGE_PROMPT


def test_split_two_grid_preserves_two_landscape_panels() -> None:
    processor = ProductImageProcessor(lambda: {})
    parts = processor.split_two_grid(_media(_two_grid_bytes()), start_index=3)

    assert [part.stage for part in parts] == ["grid_image_3", "grid_image_4"]
    expected = [(210, 30, 30), (30, 70, 210)]
    for part, color in zip(parts, expected):
        with Image.open(BytesIO(part.content)) as image:
            assert image.size == (800, 800)
            actual = image.convert("RGB").getpixel((400, 400))
            assert all(abs(value - wanted) <= 4 for value, wanted in zip(actual, color))


def test_split_four_grid_falls_back_to_center_when_no_divider_evidence() -> None:
    # 无分隔线证据时回退正中切分（面板独立性校验只在面板内出现完整长分隔线时拒绝）。
    image = Image.new("RGB", (2048, 2048), (40, 80, 120))
    for x in range(2048):
        color = (40 + x // 16, 80, 120)
        image.paste(color, (x, 0, x + 1, 2048))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    processor = ProductImageProcessor(lambda: {})
    parts = processor.split_four_grid(_media(buffer.getvalue()))
    assert len(parts) == 5


def test_split_four_grid_accepts_one_small_shifted_separator_as_local_fallback() -> None:
    image = Image.new("RGB", (2048, 2048), (34, 72, 118))
    image.paste((235, 235, 235), (1060, 0, 1072, 2048))
    image.paste((235, 235, 235), (0, 1060, 2048, 1072))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    processor = ProductImageProcessor(lambda: {})
    assert len(processor.split_four_grid(_media(buffer.getvalue()))) == 5


def _image_provider_config(source_path, *, retries: int = 3):
    return {
        "image": {
            "base_url": "https://provider.example/v1",
            "api_key": "configured",
            "model": "gpt-image-test",
            "reference_model": "gpt-image-test",
            "reference_model_1k": "gpt-image-test-1k",
            "image_size": "2048x2048",
        },
        "limits": {"image_retry_attempts": retries},
        "source_path": str(source_path),
    }


def test_image_http_400_is_not_retried(monkeypatch, tmp_path) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(_grid_bytes())
    calls = 0

    def fail(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise MediaProcessingError("bad request", status_code=400)

    monkeypatch.setattr(ProductImageProcessor, "_request_edit", staticmethod(fail))
    processor = ProductImageProcessor(lambda: _image_provider_config(source))
    with pytest.raises(MediaProcessingError) as caught:
        processor.generate(stage="grid_image", prompt="contract", reference_values=[str(source)])
    assert calls == 1
    assert caught.value.status_class == "non_retryable_4xx"
    assert caught.value.attempt_count == 1


def test_image_timeout_with_unknown_outcome_is_not_retried(monkeypatch, tmp_path) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(_grid_bytes())
    calls = 0

    def fail(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise requests.Timeout("provider timed out")

    monkeypatch.setattr(ProductImageProcessor, "_request_edit", staticmethod(fail))
    processor = ProductImageProcessor(lambda: _image_provider_config(source))
    with pytest.raises(MediaProcessingError) as caught:
        processor.generate(stage="grid_image", prompt="contract", reference_values=[str(source)])
    assert calls == 1
    assert caught.value.status_class == "unknown_outcome_timeout"


def test_transient_provider_cooldown_skips_to_backup_but_never_removes_only_route(monkeypatch, tmp_path) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(_grid_bytes())
    calls: list[str] = []

    config = _image_provider_config(source)
    config["backup_image"] = {
        "base_url": "https://backup.example/v1",
        "api_key": "configured",
        "model": "gpt-image-backup",
        "reference_model": "gpt-image-backup",
        "image_size": "2048x2048",
    }
    config["limits"]["image_provider_strategy"] = "primary_first"

    def respond(provider, *_args, **_kwargs):
        calls.append(provider["name"])
        if provider["name"].startswith("primary"):
            raise requests.Timeout("primary timed out")
        return _grid_bytes(), "image/png"

    monkeypatch.setattr(ProductImageProcessor, "_request_edit", staticmethod(respond))
    processor = ProductImageProcessor(lambda: config)
    for _ in range(2):
        with pytest.raises(MediaProcessingError, match="primary"):
            processor.generate(stage="grid_image", prompt="contract", reference_values=[str(source)])

    media = processor.generate(stage="grid_image", prompt="contract", reference_values=[str(source)])

    assert calls == [
        "primary:gpt-image-test",
        "primary:gpt-image-test",
        "backup:gpt-image-backup",
    ]
    assert media.provider == "backup:gpt-image-backup"

    only_primary = ProductImageProcessor(lambda: _image_provider_config(source))
    only_key = only_primary._provider_health_key(only_primary._providers(_image_provider_config(source))[0])
    only_primary._provider_cooldown_until[only_key] = time.monotonic() + 60
    assert len(only_primary._provider_order(only_primary._providers(_image_provider_config(source)), _image_provider_config(source))) == 1


def test_stalled_wuyin_hands_over_to_cross_protocol_backup(monkeypatch, tmp_path) -> None:
    """无印停滞、且池里存在同步协议备用时，必须轮到备用。

    回归保护（线上实测）：hedge 只认无印异步链路，中转的 /images/edits 永远进不了对冲；
    旧逻辑在无印停滞时直接抛错并把整段时长预算烧在主链路上，备用一次都没被调用，用户
    只能看到「整批回退来源图」。
    """
    source = tmp_path / "source.png"
    source.write_bytes(_grid_bytes())
    calls: list[str] = []

    config = _image_provider_config(source)
    config["image"]["base_url"] = "https://api.wuyinkeji.com"
    config["image"]["model"] = "nanoBanana_pro"
    config["image"]["reference_model"] = "nanoBanana_pro"
    config["backup_image"] = {
        "base_url": "https://api.aicoming.top/v1",
        "api_key": "relay-key",
        "model": "gpt-image-2-2k",
        "reference_model": "gpt-image-2-2k",
        "image_size": "2048x2048",
    }
    config["limits"]["image_provider_strategy"] = "primary_first"

    def respond(provider, *_args, **_kwargs):
        calls.append(provider["name"])
        if provider["name"].startswith("primary"):
            raise MediaProcessingError("provider image task stalled", status_class="provider_stalled")
        return _grid_bytes(), "image/png"

    monkeypatch.setattr(ProductImageProcessor, "_request_edit", staticmethod(respond))
    media = ProductImageProcessor(lambda: config).generate(
        stage="grid_image", prompt="contract", reference_values=[str(source)]
    )

    assert media.provider.startswith("backup")
    assert calls == ["primary:nanoBanana_pro", "backup:gpt-image-2-2k"]


def test_provider_rate_limit_payload_is_retryable() -> None:
    """中转/上游返回 HTTP 200 + rate_limited 错误体时，必须判为可退避重试。

    回归保护（线上实测）：此前这类响应没有 status_class，_retry_class 落到
    non_retryable_local，外层直接放弃——上游限流时 17s 就结束、一次重试都没有。
    """
    payload = {
        "error": {
            "attempts": 3,
            "code": "upstream_rate_limited",
            "detail": '{"error":{"code":"rate_limited"}}',
        }
    }
    assert media_module._submit_rejection_class(payload) == "rate_limited"
    error = media_module.MediaProcessingError(
        "provider response does not contain a safe image result",
        status_class=media_module._submit_rejection_class(payload),
    )
    assert media_module._retry_class(error) == "rate_limited"


def test_provider_billing_rejection_stays_non_retryable() -> None:
    """欠费/鉴权类拒绝仍判不可重试（重试无意义且有重复计费风险）。"""
    payload = {"error": {"code": "insufficient_balance", "message": "账户余额不足"}}
    assert media_module._submit_rejection_class(payload) == "non_retryable_4xx"


def test_image_early_500_retries_only_once(monkeypatch, tmp_path) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(_grid_bytes())
    calls = 0

    def respond(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise MediaProcessingError("server error", status_code=500)
        return _grid_bytes(), "image/png"

    monkeypatch.setattr(ProductImageProcessor, "_request_edit", staticmethod(respond))
    processor = ProductImageProcessor(lambda: _image_provider_config(source))
    media = processor.generate(
        stage="grid_image",
        prompt="contract",
        reference_values=[str(source)],
        layout_scaffold=True,
    )
    assert calls == 2
    assert media.attempt_count == 2
    assert media.reference_count == 1


def _image_provider_config_with_backup(source_path):
    config = _image_provider_config(source_path)
    config["backup_image"] = {
        "base_url": "https://backup.example/v1",
        "api_key": "configured",
        "model": "gpt-image-backup",
        "reference_model": "gpt-image-backup",
        "image_size": "2048x2048",
    }
    config["limits"]["image_provider_strategy"] = "primary_first"
    return config


def _stalled_error() -> MediaProcessingError:
    return MediaProcessingError(
        "provider image task stalled: no field change for 121s status=3 code=200 成功",
        status_class="provider_stalled",
    )


def test_grid_stall_retries_in_place_for_single_provider(monkeypatch, tmp_path) -> None:
    """单 provider 停滞时必须原地重试一次，而不是直接回退来源图。"""
    source = tmp_path / "source.png"
    source.write_bytes(_grid_bytes())
    calls: list[str] = []

    def respond(provider, *_args, **_kwargs):
        calls.append(provider["name"])
        if len(calls) == 1:
            raise _stalled_error()
        return _grid_bytes(), "image/png"

    monkeypatch.setattr(ProductImageProcessor, "_request_edit", staticmethod(respond))
    processor = ProductImageProcessor(lambda: _image_provider_config(source))

    media = processor.generate(stage="grid_image", prompt="contract", reference_values=[str(source)])

    assert calls == ["primary:gpt-image-test", "primary:gpt-image-test"]
    assert media.attempt_count == 2
    assert media.provider == "primary:gpt-image-test"


def test_grid_stall_switches_provider_before_retrying_same(monkeypatch, tmp_path) -> None:
    """多 provider 时，停滞后的第二次尝试必须先换 provider。"""
    source = tmp_path / "source.png"
    source.write_bytes(_grid_bytes())
    calls: list[str] = []

    def respond(provider, *_args, **_kwargs):
        calls.append(provider["name"])
        if provider["name"].startswith("primary"):
            raise _stalled_error()
        return _grid_bytes(), "image/png"

    monkeypatch.setattr(ProductImageProcessor, "_request_edit", staticmethod(respond))
    processor = ProductImageProcessor(lambda: _image_provider_config_with_backup(source))

    media = processor.generate(stage="grid_image", prompt="contract", reference_values=[str(source)])

    assert calls == ["primary:gpt-image-test", "backup:gpt-image-backup"]
    assert media.provider == "backup:gpt-image-backup"
    assert media.attempt_count == 2


def test_grid_relay_failure_is_retryable_and_switches_provider(monkeypatch, tmp_path) -> None:
    """参考图 COS 中转失败不再一次即回退：标记可重试并换 provider 再试。"""
    source = tmp_path / "source.png"
    source.write_bytes(_grid_bytes())
    calls: list[str] = []

    def respond(provider, *_args, **_kwargs):
        calls.append(provider["name"])
        if provider["name"].startswith("primary"):
            raise MediaProcessingError(
                "failed to relay local reference image to the direct image provider",
                status_class="reference_relay_failed",
            )
        return _grid_bytes(), "image/png"

    monkeypatch.setattr(ProductImageProcessor, "_request_edit", staticmethod(respond))
    processor = ProductImageProcessor(lambda: _image_provider_config_with_backup(source))

    media = processor.generate(stage="grid_image", prompt="contract", reference_values=[str(source)])

    assert calls == ["primary:gpt-image-test", "backup:gpt-image-backup"]
    assert media.provider == "backup:gpt-image-backup"


def test_grid_gateway_rejection_is_retryable_not_terminal(monkeypatch, tmp_path) -> None:
    """网关拒绝（可重试类）不得一次即中止，必须再试一次。"""
    source = tmp_path / "source.png"
    source.write_bytes(_grid_bytes())
    calls: list[str] = []

    def respond(provider, *_args, **_kwargs):
        calls.append(provider["name"])
        if len(calls) == 1:
            raise MediaProcessingError(
                "provider submit failed: server image gateway rejected the request",
                status_class="gateway_rejected",
            )
        return _grid_bytes(), "image/png"

    monkeypatch.setattr(ProductImageProcessor, "_request_edit", staticmethod(respond))
    processor = ProductImageProcessor(lambda: _image_provider_config(source))

    media = processor.generate(stage="grid_image", prompt="contract", reference_values=[str(source)])

    assert calls == ["primary:gpt-image-test", "primary:gpt-image-test"]
    assert media.attempt_count == 2


def _cos_config() -> dict:
    return {
        "cos": {
            "bucket": "bucket",
            "region": "ap-guangzhou",
            "secret_id": "secret-id",
            "secret_key": "secret-key",
        }
    }


def test_wuyin_reference_relay_retries_publish_with_backoff(monkeypatch) -> None:
    """COS 中转发布瞬时失败必须带退避重试，成功即返回临时 URL。"""

    class _FlakyStore:
        def __init__(self) -> None:
            self.calls = 0

        def publish(self, content: bytes, content_type: str):
            self.calls += 1
            if self.calls < 3:
                raise TemporaryReferenceError("transient cos failure")
            return SimpleNamespace(
                key="ai-service/transient/ref.image",
                url="https://bucket.cos.ap-guangzhou.myqcloud.com/ref.image?sig=1",
            )

        def delete(self, reference) -> None:
            return

    store = _FlakyStore()
    sleeps: list[float] = []
    monkeypatch.setattr(media_module, "TemporaryCosStore", lambda _config: store)
    monkeypatch.setattr(media_module.time, "sleep", lambda seconds: sleeps.append(seconds))
    processor = ProductImageProcessor(lambda: _cos_config())

    urls, _store, temporary = processor._wuyin_reference_urls(
        [(b"reference", "source.png", "image/jpeg")]
    )

    assert store.calls == 3
    assert urls == ["https://bucket.cos.ap-guangzhou.myqcloud.com/ref.image?sig=1"]
    assert sleeps == [0.5, 1.5]
    assert len(temporary) == 1


def test_wuyin_reference_relay_failure_falls_back_to_public_url(monkeypatch) -> None:
    """COS 中转始终失败但参考图带原始公网 URL 时，退回该 URL 而不是失败。"""

    class _DeadStore:
        def __init__(self) -> None:
            self.calls = 0

        def publish(self, *_args, **_kwargs):
            self.calls += 1
            raise TemporaryReferenceError("cos down")

        def delete(self, reference) -> None:
            return

    store = _DeadStore()
    monkeypatch.setattr(media_module, "TemporaryCosStore", lambda _config: store)
    monkeypatch.setattr(media_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(media_module, "is_safe_external_url", lambda _url: True)
    processor = ProductImageProcessor(lambda: _cos_config())

    urls, _store, temporary = processor._wuyin_reference_urls(
        [(b"reference", "source.png", "image/jpeg", "https://cdn.example/source.png")]
    )

    assert urls == ["https://cdn.example/source.png"]
    assert temporary == []
    assert store.calls == media_module.COS_RELAY_PUBLISH_ATTEMPTS


def test_wuyin_reference_relay_exhaustion_is_retryable_not_terminal(monkeypatch) -> None:
    """本地独有参考图中转彻底失败时，错误必须可重试（交上层换 provider/换参考图）。"""

    class _DeadStore:
        def publish(self, *_args, **_kwargs):
            raise TemporaryReferenceError("cos down")

        def delete(self, reference) -> None:
            return

    monkeypatch.setattr(media_module, "TemporaryCosStore", lambda _config: _DeadStore())
    monkeypatch.setattr(media_module.time, "sleep", lambda _seconds: None)
    processor = ProductImageProcessor(lambda: _cos_config())

    with pytest.raises(MediaProcessingError) as caught:
        processor._wuyin_reference_urls([(b"reference", "source.png", "image/jpeg")])

    assert caught.value.status_class == "reference_relay_failed"
    assert media_module._retry_class(caught.value) == "reference_relay_failed"


def test_stall_threshold_defaults_are_recalibrated_and_configurable() -> None:
    """2K（四宫格）停滞阈值按实测重定标为 240s，1K 维持 120s，两档均可配置覆盖。"""
    # 1K 档维持不变（image_gpt_2.5 恒 1K；实测 max 95.5s → 120s 约 26% 余量）。
    assert media_module.WUYIN_IMAGE_STALL_THRESHOLD_SECONDS == 120.0
    # 2K 档：仍在成功样本 max 216s 之上，同时对齐上游真实分布（POD 链路 p95 115s、
    # 健康日上限 136s → 240s ≈ 3×p95）。旧值 300s 在 2026-10-16 复测中让单条吃满 486s。
    assert media_module.WUYIN_IMAGE_STALL_THRESHOLD_2K_SECONDS == 240.0
    assert media_module._wuyin_stall_threshold_seconds("image_gpt", "2048x2048") == 240.0
    assert media_module._wuyin_stall_threshold_seconds("image_gpt", "2048x1024") == 240.0
    assert media_module._wuyin_stall_threshold_seconds("image_gpt", None) == 120.0
    assert media_module._wuyin_stall_threshold_seconds("image_gpt_2.5", "1024x1024") == 120.0
    override = {
        "limits": {
            "image_stall_threshold_seconds": 90,
            "image_stall_threshold_2k_seconds": 150,
        }
    }
    assert media_module._wuyin_stall_threshold_seconds("image_gpt_2.5", None, override) == 90.0
    assert media_module._wuyin_stall_threshold_seconds("image_gpt", "2048x2048", override) == 150.0


def test_submit_rejection_classification() -> None:
    assert (
        media_module._submit_rejection_class({"msg": "server image gateway rejected the request"})
        == "gateway_rejected"
    )
    assert (
        media_module._retry_class(MediaProcessingError("x", status_class="gateway_rejected"))
        == "gateway_rejected"
    )
    assert media_module._submit_rejection_class({"msg": "余额不足"}) == "non_retryable_4xx"
    assert media_module._submit_rejection_class({"msg": "invalid api key"}) == "non_retryable_4xx"


def test_layout_scaffold_keeps_original_1688_image_as_first_provider_reference(
    monkeypatch, tmp_path
) -> None:
    source = tmp_path / "original-1688.png"
    source.write_bytes(_grid_bytes())
    captured_names: list[str] = []

    def respond(_provider, _prompt, references, **_kwargs):
        captured_names.extend(name for _content, name, _content_type in references)
        return _grid_bytes(), "image/png"

    monkeypatch.setattr(ProductImageProcessor, "_request_edit", staticmethod(respond))
    processor = ProductImageProcessor(lambda: _image_provider_config(source))

    processor.generate(
        stage="grid_image",
        prompt="contract",
        reference_values=[str(source)],
        layout_scaffold=True,
    )

    assert captured_names == ["original-1688.png", "fixed-four-grid-layout.png"]


def test_repair_keeps_original_1688_image_before_previous_generated_image(
    monkeypatch, tmp_path
) -> None:
    source = tmp_path / "original-1688.png"
    source.write_bytes(_grid_bytes())
    captured_names: list[str] = []

    def respond(_provider, _prompt, references, **_kwargs):
        captured_names.extend(name for _content, name, _content_type in references)
        return _grid_bytes(), "image/png"

    monkeypatch.setattr(ProductImageProcessor, "_request_edit", staticmethod(respond))
    processor = ProductImageProcessor(lambda: _image_provider_config(source))

    media = processor.repair_generated(
        stage="detail_image",
        prompt="repair contract",
        prior_content=_grid_bytes(),
        prior_content_type="image/png",
        reference_values=[str(source)],
    )

    assert captured_names == ["original-1688.png", "generated_previous.png"]
    assert media.reference_count == 1


def test_second_grid_attempt_uses_only_the_remaining_total_timeout_budget(monkeypatch, tmp_path) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(_grid_bytes())
    clock = [0.0]
    request_timeouts: list[float] = []

    def respond(*_args, **kwargs):
        request_timeouts.append(float(kwargs["timeout_seconds"]))
        if len(request_timeouts) == 1:
            clock[0] = 625.0
            raise MediaProcessingError("server error", status_code=500)
        return _grid_bytes(), "image/png"

    monkeypatch.setattr(
        "wh_local.modules.product_processing.infrastructure.media.time.monotonic",
        lambda: clock[0],
    )
    monkeypatch.setattr(ProductImageProcessor, "_request_edit", staticmethod(respond))
    processor = ProductImageProcessor(lambda: _image_provider_config(source))

    media = processor.generate(stage="grid_image", prompt="contract", reference_values=[str(source)])

    assert media.attempt_count == 2
    assert request_timeouts == [600.0, 35.0]


def test_failed_slot_model_override_does_not_change_regular_2k_grid_profile(monkeypatch, tmp_path) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(_grid_bytes())
    calls: list[dict[str, str | None]] = []

    def respond(_provider, _prompt, _references, **kwargs):
        calls.append(
            {
                "reference_model": kwargs.get("reference_model"),
                "image_size": kwargs.get("image_size"),
            }
        )
        return _grid_bytes(), "image/png"

    monkeypatch.setattr(ProductImageProcessor, "_request_edit", staticmethod(respond))
    processor = ProductImageProcessor(lambda: _image_provider_config(source))

    one_k = processor.generate(
        stage="grid_image_3",
        prompt="fill failed slot",
        reference_values=[str(source)],
        image_size="1024x1024",
        model_override="gpt-image-test-1k",
    )
    two_k = processor.generate(
        stage="grid_image",
        prompt="regular four grid",
        reference_values=[str(source)],
    )

    assert one_k.model == "gpt-image-test-1k"
    assert two_k.model == "gpt-image-test"
    assert calls == [
        {"reference_model": "gpt-image-test-1k", "image_size": "1024x1024"},
        {"reference_model": None, "image_size": None},
    ]


def test_compose_grid_summary_uses_replaced_normalized_slots() -> None:
    processor = ProductImageProcessor(lambda: {})
    colors = [(210, 30, 30), (30, 180, 50), (30, 70, 210), (220, 180, 30)]
    parts: list[GeneratedMedia] = []
    for index, color in enumerate(colors, start=1):
        image = Image.new("RGB", (800, 800), color)
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=94, subsampling=0)
        parts.append(
            GeneratedMedia(
                stage=f"grid_image_{index}",
                content=buffer.getvalue(),
                content_type="image/jpeg",
                suffix=".jpg",
                provider="test",
                model="test",
                reference_count=1,
            )
        )

    summary = processor.compose_grid_summary(parts)

    assert summary.stage == "grid_image_summary"
    assert summary.content_type == "image/jpeg"
    with Image.open(BytesIO(summary.content)) as image:
        assert image.size == (800, 800)
        samples = [image.convert("RGB").getpixel(point) for point in ((200, 200), (600, 200), (200, 600), (600, 600))]
    for actual, expected in zip(samples, colors):
        assert all(abs(value - wanted) <= 5 for value, wanted in zip(actual, expected))


def test_reference_download_cache_single_flights_concurrent_reads(monkeypatch) -> None:
    calls = 0
    calls_lock = threading.Lock()

    def download(_url: str) -> tuple[bytes, str]:
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.04)
        return b"same-image", "image/jpeg"

    monkeypatch.setattr(media_module, "_download_reference_image", download)
    processor = ProductImageProcessor(lambda: {"limits": {"reference_download_cache_entries": 2}})

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _index: processor._load_references(["https://example.com/source.jpg"], limit=1),
                range(4),
            )
        )

    assert calls == 1
    assert [result[0][0] for result in results] == [b"same-image"] * 4


def test_reference_loading_uses_shared_pinned_public_transport(monkeypatch) -> None:
    calls: list[str] = []

    def pinned(url: str, **_kwargs) -> tuple[bytes, str]:
        calls.append(url)
        return b"safe-reference", "image/jpeg"

    monkeypatch.setattr(media_module, "_download_pinned_public_image", pinned)
    monkeypatch.setattr(
        media_module._SESSION,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("reference download must not resolve the hostname through requests")
        ),
    )
    processor = ProductImageProcessor(lambda: {})
    url = "https://reference.example.test/source.jpg"

    loaded = processor._load_references([url], limit=1)

    assert loaded[0][:3] == (b"safe-reference", "source.jpg", "image/jpeg")
    assert calls == [url]


def test_reference_loading_skips_system_dns_preflight_before_pinned_transport(
    monkeypatch,
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(media_module, "is_safe_external_url", lambda _url: False)
    monkeypatch.setattr(
        media_module,
        "_download_pinned_public_image",
        lambda url, **_kwargs: calls.append(url) or (b"safe-reference", "image/jpeg"),
    )
    processor = ProductImageProcessor(lambda: {})
    url = "https://reference.example.test/source.jpg"

    loaded = processor._load_references([url], limit=1)

    assert loaded[0][:3] == (b"safe-reference", "source.jpg", "image/jpeg")
    assert calls == [url]


def test_reference_download_never_downgrades_https_or_uses_domain_session(monkeypatch) -> None:
    calls: list[str] = []

    def pinned(url: str, **_kwargs):
        calls.append(url)
        raise media_module.MediaProcessingError("TLS certificate verification failed")

    monkeypatch.setattr(media_module, "_download_pinned_public_image", pinned)
    monkeypatch.setattr(
        media_module._SESSION,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("requests hostname transport is forbidden")
        ),
    )

    with pytest.raises(media_module.MediaProcessingError, match="TLS certificate"):
        media_module._download_reference_image("https://reference.example.test/source.jpg")

    assert calls == ["https://reference.example.test/source.jpg"]


def test_prime_references_uses_existing_safe_single_flight_cache(monkeypatch) -> None:
    calls = 0

    def download(_url: str) -> tuple[bytes, str]:
        nonlocal calls
        calls += 1
        return b"same-image", "image/jpeg"

    monkeypatch.setattr(media_module, "_download_reference_image", download)
    processor = ProductImageProcessor(lambda: {})
    reference = "https://example.com/source.jpg"

    assert processor.prime_references([reference]) == 1
    assert processor._load_references([reference], limit=1)[0][0] == b"same-image"
    assert calls == 1


def test_reference_download_cache_is_bounded_and_does_not_cache_failures(monkeypatch) -> None:
    calls: dict[str, int] = {}

    def download(url: str) -> tuple[bytes, str]:
        calls[url] = calls.get(url, 0) + 1
        if url.endswith("flaky.jpg") and calls[url] == 1:
            raise requests.ConnectionError("temporary")
        return url.encode(), "image/jpeg"

    monkeypatch.setattr(media_module, "_download_reference_image", download)
    processor = ProductImageProcessor(lambda: {"limits": {"reference_download_cache_entries": 1}})
    flaky = "https://example.com/flaky.jpg"
    first = "https://example.com/first.jpg"
    second = "https://example.com/second.jpg"

    with pytest.raises(MediaProcessingError, match="download failed"):
        processor._load_references([flaky], limit=1)
    assert processor._load_references([flaky], limit=1)[0][0] == flaky.encode()
    processor._load_references([first], limit=1)
    processor._load_references([second], limit=1)
    processor._load_references([first], limit=1)

    assert calls[flaky] == 2
    assert calls[first] == 2
    assert calls[second] == 1


def test_reference_loading_uses_next_candidate_when_first_image_is_unavailable(monkeypatch) -> None:
    calls: list[str] = []

    def download(url: str) -> tuple[bytes, str]:
        calls.append(url)
        if url.endswith("original-main.jpg"):
            raise requests.ConnectionError("main unavailable")
        return b"fallback-image", "image/jpeg"

    monkeypatch.setattr(media_module, "_download_reference_image", download)
    processor = ProductImageProcessor(lambda: {})

    loaded = processor._load_references(
        [
            "https://example.com/original-main.jpg",
            "https://example.com/detail.jpg",
        ],
        limit=1,
    )

    assert loaded[0][0] == b"fallback-image"
    assert calls == [
        "https://example.com/original-main.jpg",
        "https://example.com/detail.jpg",
    ]


def test_reference_download_retries_transient_connection_and_logs_stage(monkeypatch, capsys) -> None:
    calls = 0

    def pinned(_url: str, **_kwargs) -> tuple[bytes, str]:
        nonlocal calls
        calls += 1
        if calls < 3:
            try:
                raise ConnectionResetError("connection reset by peer")
            except ConnectionResetError as root:
                raise MediaProcessingError(
                    "provider result download is temporarily unavailable"
                ) from root
        return b"recovered-image", "image/jpeg"

    monkeypatch.setattr(media_module, "_download_pinned_public_image", pinned)
    monkeypatch.setattr(media_module.time, "sleep", lambda *_args: None)

    content, content_type = media_module._download_reference_image(
        "https://reference.example.test/source.jpg"
    )

    assert (content, content_type) == (b"recovered-image", "image/jpeg")
    assert calls == 3
    output = capsys.readouterr().out
    assert "stage=reference_input" in output
    assert "host=reference.example.test" in output
    assert "error_type=ConnectionResetError" in output


def test_provider_result_download_exhaustion_never_retries_paid_generation(
    monkeypatch,
) -> None:
    calls = 0

    def pinned(_url: str, **_kwargs) -> tuple[bytes, str]:
        nonlocal calls
        calls += 1
        try:
            raise ConnectionResetError("connection reset by peer")
        except ConnectionResetError as root:
            raise MediaProcessingError(
                "provider result download is temporarily unavailable"
            ) from root

    monkeypatch.setattr(media_module, "_download_pinned_public_image", pinned)
    monkeypatch.setattr(media_module.time, "sleep", lambda *_args: None)

    with pytest.raises(MediaProcessingError) as captured:
        media_module._download_provider_result_image("https://result.example.test/image.png")

    assert calls == 3
    assert captured.value.status_class == "provider_result_download_failed"
    assert media_module._retry_class(captured.value) == "non_retryable_local"


def test_reference_loading_skips_unreadable_local_cache_and_uses_next_file(
    monkeypatch,
    tmp_path,
) -> None:
    unreadable = tmp_path / "broken.jpg"
    fallback = tmp_path / "fallback.jpg"
    unreadable.write_bytes(b"broken")
    fallback.write_bytes(b"fallback")

    def read_local(path: Path) -> tuple[bytes, str]:
        if path == unreadable:
            raise MediaProcessingError("local reference image is unreadable")
        return b"valid-fallback", "image/jpeg"

    monkeypatch.setattr(media_module, "_read_local_reference", read_local)
    processor = ProductImageProcessor(lambda: {})

    loaded = processor._load_references([str(unreadable), str(fallback)], limit=1)

    assert loaded[0][:3] == (b"valid-fallback", "fallback.jpg", "image/jpeg")


def test_provider_config_defaults_to_seedream_with_explicit_1k_reference_profile(monkeypatch) -> None:
    monkeypatch.setattr(provider_config_module, "_try_system_runtime_config", lambda: None)
    monkeypatch.setenv("WH_AI_API_KEY", "configured")

    provider = provider_config_module.resolve_ai_provider()

    assert provider["image_model"] == "doubao-seedream-5-0-flash-260915"
    assert provider["reference_image_model"] == "doubao-seedream-5-0-flash-260915"
    assert provider["image_size"] == "2048x2048"
    assert provider["reference_image_model_1k"] == "gpt-image-2-1k"
    assert provider["reference_image_size_1k"] == "1024x1024"
    assert provider["premium_image_model"] == "doubao-seedream-5-0-flash-260915"
    assert provider["premium_image_size"] == "4096x4096"


def test_provider_config_ignores_saved_image_key_and_targets_direct_ark(monkeypatch) -> None:
    runtime_config = SimpleNamespace(
        text_ai=SimpleNamespace(base_url="https://text.example/v1", api_key="text-key"),
        image_ai=SimpleNamespace(
            base_url="https://legacy-image.example/v1",
            api_key="image-key",
            model="gpt-image-2-2k",
            configured=True,
        ),
        backup_image_ai=SimpleNamespace(configured=False),
        cos=SimpleNamespace(configured=False),
        limits={},
        updates={},
    )
    monkeypatch.setattr(provider_config_module, "_try_system_runtime_config", lambda: runtime_config)
    monkeypatch.delenv("WH_IMAGE_AI_BASE_URL", raising=False)

    provider = provider_config_module.resolve_ai_provider()

    # 客户端固定直连方舟生图：桌面侧保存的 image_ai 凭据一律忽略，恒用方舟直连地址；
    # 旧默认值 gpt-image-2-2k 迁移到新的默认模型 Seedream；无任务上下文时 api_key 是占位符。
    assert provider["_sys_image_ai"]["base_url"] == "https://ark.cn-beijing.volces.com/api/v3"
    assert provider["_sys_image_ai"]["model"] == "doubao-seedream-5-0-flash-260915"
    assert provider["_sys_image_ai"]["api_key"] == "direct-pending"
    assert provider["_sys_backup_image_ai"] is None
    assert "image-key" not in json.dumps(provider)


def test_b_grid_quality_failure_never_triggers_a_paid_repair(monkeypatch) -> None:
    class _Processor:
        repair_calls = 0

        @staticmethod
        def validate_four_grid(_media) -> None:
            return None

        def repair_generated(self, **_kwargs):
            self.repair_calls += 1
            return _media(_grid_bytes())

    service = object.__new__(service_module.ProductProcessingService)
    processor = _Processor()
    monkeypatch.setattr(
        service_module,
        "inspect_visible_text",
        lambda _content: {"chinese": [], "prominent": ["FACTORY DIRECT"]},
    )
    monkeypatch.setattr(service_module, "ocr_gate_enabled", lambda: True)
    monkeypatch.setattr(service_module, "_media_types", lambda: (object, RuntimeError, ValueError))

    # 质量门为软性：B 模板禁止付费重绘，检出问题只留痕 quality_unresolved，不再 raise 阻断，
    # 图片整组失败由 _process_one 回退来源图继续（用户要求不卡流程）。
    notes: list[str] = []
    result = service._repair_until_clean(
        processor,
        "grid_image",
        "four_grid",
        _media(_grid_bytes()),
        ["https://example.com/source.jpg"],
        notes,
        allow_paid_repair=False,
    )

    assert processor.repair_calls == 0
    assert result is not None
    assert "four_grid:quality_unresolved" in notes


def test_local_detail_reads_split_media_bytes_without_redownloading(monkeypatch) -> None:
    monkeypatch.setattr(
        service_module,
        "fetch_public_image",
        lambda *_args, **_kwargs: pytest.fail("split media must not be downloaded from COS"),
    )
    media = _media(_grid_bytes())
    assert service_module.ProductProcessingService._local_source_bytes(media) == media.content


def test_ocr_inspection_flags_large_added_copy_but_not_small_product_mark(monkeypatch) -> None:
    class _Engine:
        def __call__(self, _array):
            return (
                [
                    # 跨面板海报横幅：高 220px(10.7%)、宽 900px(44%) → prominent
                    [[[100, 120], [1000, 120], [1000, 340], [100, 340]], "FACTORY DIRECT", 0.99],
                    # 产品本体小标记：宽 100px(4.9%) → 不拦
                    [[[1500, 1500], [1600, 1500], [1600, 1530], [1500, 1530]], "JOKER", 0.98],
                    # 中文硬拦截（不受 prominent 放宽影响）
                    [[[100, 1700], [260, 1700], [260, 1740], [100, 1740]], "麻将", 0.97],
                ],
                None,
            )

    monkeypatch.setattr(ocr_gate, "_get_engine", lambda: _Engine())
    # OCR 质检门已在业务上彻底关闭（ocr_gate_enabled() 恒为 False）；本用例直接打开
    # 该判据以继续验证检测逻辑本身。
    monkeypatch.setattr(ocr_gate, "ocr_gate_enabled", lambda: True)
    inspection = ocr_gate.inspect_visible_text(_grid_bytes())
    assert inspection is not None
    assert inspection["prominent"] == ["FACTORY DIRECT"]
    assert inspection["chinese"] == ["麻将"]


def test_ocr_inference_uses_dedicated_two_worker_gate(monkeypatch) -> None:
    active = 0
    maximum = 0
    lock = threading.Lock()

    class _Engine:
        def __call__(self, _array):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.04)
            with lock:
                active -= 1
            return [], None

    monkeypatch.setattr(ocr_gate, "_get_engine", lambda: _Engine())
    monkeypatch.setattr(ocr_gate, "_OCR_INFERENCE_SEMAPHORE", threading.BoundedSemaphore(2))
    monkeypatch.setattr(ocr_gate, "ocr_gate_enabled", lambda: True)

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _index: ocr_gate.inspect_visible_text(_grid_bytes()), range(5)))

    assert results == [{"chinese": [], "prominent": []}] * 5
    assert maximum == 2


def test_ocr_inspection_ignores_large_print_on_product_face(monkeypatch) -> None:
    """麻将牌这类「产品本体印刷大字符」不得被误判为 AI 显著文字：
    字符再大，只要宽度局限在单面板内（<30%），就不再触发 prominent，避免重绘死循环。"""
    class _Engine:
        def __call__(self, array):
            height, width = array.shape[:2]
            scale = width / 2048.0  # 假引擎坐标基于 2048 空间，按输入实际尺寸等比换算
            return (
                [
                    # 牌面数字：高 200px(9.8%) 但宽仅 400px(19.5%) → 产品印刷标记，放过
                    [
                        [[800 * scale, 900 * scale], [1200 * scale, 900 * scale],
                         [1200 * scale, 1100 * scale], [800 * scale, 1100 * scale]],
                        "1234567890",
                        0.99,
                    ],
                ],
                None,
            )

    monkeypatch.setattr(ocr_gate, "_get_engine", lambda: _Engine())
    monkeypatch.setattr(ocr_gate, "ocr_gate_enabled", lambda: True)
    inspection = ocr_gate.inspect_visible_text(_grid_bytes())
    assert inspection is not None
    assert inspection["prominent"] == []
    assert inspection["chinese"] == []


def test_grid_repair_prompt_preserves_product_print_and_replaces_chinese() -> None:
    """重绘提示词不得自相矛盾：只删 AI 添加的跨面板文字；
    产品本体印刷字符/图案是设计（保留），产品本体印刷中文则换成英文。"""
    assert "Remove ONLY AI-added cross-panel headlines" in GRID_IMAGE_REPAIR_PROMPT
    assert "spans two or more panels must be deleted" in GRID_IMAGE_REPAIR_PROMPT
    assert "are PRODUCT DESIGN and must be kept" in GRID_IMAGE_REPAIR_PROMPT
    assert "mahjong tile faces" in GRID_IMAGE_REPAIR_PROMPT
    assert "replace that printed text with the equivalent English text" in GRID_IMAGE_REPAIR_PROMPT
    assert "never reproduce Chinese characters" in GRID_IMAGE_REPAIR_PROMPT


def test_dianxiaomi_image_filter_rejects_local_and_private_urls() -> None:
    assert _http_urls(
        [
            "https://bucket.cos.ap-guangzhou.myqcloud.com/a.jpg",
            "http://127.0.0.1:8010/a.jpg",
            "http://192.168.1.9/a.jpg",
            "/pp-media/a.jpg",
            r"C:\\temp\\a.jpg",
        ]
    ) == ["https://bucket.cos.ap-guangzhou.myqcloud.com/a.jpg"]


def test_content_addressed_cos_publish_reuses_the_same_object(monkeypatch) -> None:
    class _Missing(Exception):
        def get_status_code(self):
            return 404

    class _Client:
        def __init__(self) -> None:
            self.keys: set[str] = set()
            self.put_calls = 0

        def head_object(self, *, Bucket: str, Key: str):
            assert Bucket == "bucket-1"
            if Key not in self.keys:
                raise _Missing()
            return {
                "x-cos-meta-sha256": hashlib.sha256(b"dimension-jpeg").hexdigest(),
                "Content-Length": str(len(b"dimension-jpeg")),
                "Content-Type": "image/jpeg",
            }

        def put_object(
            self,
            *,
            Bucket: str,
            Key: str,
            Body: bytes,
            ContentType: str,
            Metadata: dict[str, str],
        ):
            assert Bucket == "bucket-1"
            assert Body == b"dimension-jpeg"
            assert ContentType == "image/jpeg"
            assert Metadata == {"x-cos-meta-sha256": hashlib.sha256(Body).hexdigest()}
            self.keys.add(Key)
            self.put_calls += 1
            return {}

    client = _Client()
    monkeypatch.setitem(
        sys.modules,
        "qcloud_cos",
        SimpleNamespace(CosConfig=lambda **_kwargs: object(), CosS3Client=lambda _config: client),
    )
    processor = ProductImageProcessor(
        lambda: {
            "cos": {
                "bucket": "bucket-1",
                "region": "ap-guangzhou",
                "secret_id": "configured",
                "secret_key": "configured",
            }
        }
    )
    content = b"dimension-jpeg"
    digest = hashlib.sha256(content).hexdigest()
    media = GeneratedMedia(
        stage="dimension",
        content=content,
        content_type="image/jpeg",
        suffix=".jpg",
        provider="local",
        model="pillow",
        reference_count=1,
    )
    first = processor.upload_content_addressed_to_cos(
        media,
        namespace="workspace-a",
        content_hash=digest,
        collection="preview-final",
    )
    second = processor.upload_content_addressed_to_cos(
        media,
        namespace="workspace-a",
        content_hash=digest,
        collection="preview-final",
    )
    assert first == second
    assert "/preview-final/workspace-a/" in first
    assert first.endswith(f"/{digest}.jpg")
    assert client.put_calls == 1
    assert processor.is_configured_cos_url(first) is True
    assert processor.is_configured_cos_url("https://other.example.com/old.jpg") is False


def test_system_settings_cos_credentials_feed_internal_media_config(monkeypatch) -> None:
    monkeypatch.setattr(service_module, "_cos_local_config_paths", lambda: ())
    for key in ("WH_COS_BUCKET", "WH_COS_REGION", "WH_COS_SECRET_ID", "WH_COS_SECRET_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        service_module,
        "resolve_ai_provider",
        lambda: {
            "_sys_cos": {
                "bucket": "settings-bucket",
                "region": "ap-guangzhou",
                "secret_id": "runtime-id",
                "secret_key": "runtime-key",
            }
        },
    )
    config = service_module.ProductProcessingService._media_config_provider()
    assert config["cos"] == {
        "bucket": "settings-bucket",
        "region": "ap-guangzhou",
        "secret_id": "runtime-id",
        "secret_key": "runtime-key",
    }


def _poll_response(payload: dict) -> SimpleNamespace:
    """Build a fake requests response shaped like _SESSION.get(...)."""
    body = json.dumps(payload).encode("utf-8")
    return SimpleNamespace(
        ok=True,
        status_code=200,
        content=body,
        text=body.decode("utf-8"),
        close=lambda: None,
        iter_content=lambda chunk_size=65536: [body],
        headers={},
        url="",
    )


def test_wuyin_poll_status_3_is_processing_and_waits_for_image(monkeypatch) -> None:
    """status=3（等待中）不是失败终态，应继续轮询直到拿到 status=1 的图片 URL。"""
    processor = ProductImageProcessor(lambda: {})
    # 第一次轮询返回 status=3 无图；第二次返回 status=1 带图。
    calls: list[str] = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            return _poll_response(
                {"code": 200, "data": {"status": 3, "msg": "成功"}, "msg": "成功"}
            )
        return _poll_response(
            {
                "code": 200,
                "data": {
                    "status": 1,
                    "image": "https://wxtechsz.oss.example.com/grid.png",
                },
            }
        )

    monkeypatch.setattr(media_module._SESSION, "get", fake_get)
    monkeypatch.setattr(media_module.time, "sleep", lambda *_args: None)

    result_url = processor._poll_wuyin_image_result(
        {"base_url": "https://api.wuyinkeji.com", "api_key": "secret"},
        "task-1",
        timeout_seconds=30,
    )
    assert result_url == "https://wxtechsz.oss.example.com/grid.png"
    assert len(calls) == 2


def test_wuyin_poll_status_negative_is_failure(monkeypatch) -> None:
    """status<0（如 -1 处理失败）应抛错且状态可重试（transient）。"""
    processor = ProductImageProcessor(lambda: {})

    def fake_get(url, **kwargs):
        return _poll_response({"code": 200, "data": {"status": -1, "msg": "处理失败"}})

    monkeypatch.setattr(media_module._SESSION, "get", fake_get)
    monkeypatch.setattr(media_module.time, "sleep", lambda *_args: None)

    with pytest.raises(MediaProcessingError) as caught:
        processor._poll_wuyin_image_result(
            {"base_url": "https://api.wuyinkeji.com", "api_key": "secret"},
            "task-1",
            timeout_seconds=30,
        )
    assert "provider image task failed" in str(caught.value)
    assert caught.value.status_class == "transient"


def test_wuyin_poll_status_4_5_are_processing_not_failure(monkeypatch) -> None:
    """status=4（处理中）、5（发布处理中）都属处理中，绝不误判为失败。"""
    for numeric in ("4", "5"):
        processor = ProductImageProcessor(lambda: {})
        returned_url = "https://wxtechsz.oss.example.com/grid.png"

        def fake_get(url, _n=numeric, _url=returned_url, **kwargs):
            if _n == numeric:
                return _poll_response({"code": 200, "data": {"status": numeric, "msg": "成功"}})
            return _poll_response(
                {"code": 200, "data": {"status": 1, "image": _url}}
            )

        monkeypatch.setattr(media_module._SESSION, "get", fake_get)
        monkeypatch.setattr(media_module.time, "sleep", lambda *_args: None)

        # 只返回一次 status=4/5 无图 → 最多轮询到 deadline 仍无图，应抛超时而非“失败”。
        # 这里直接验证 status=4/5 不会被当作失败终态（不抛 provider image task failed）。
        with pytest.raises(MediaProcessingError) as caught:
            processor._poll_wuyin_image_result(
                {"base_url": "https://api.wuyinkeji.com", "api_key": "secret"},
                "task-1",
                timeout_seconds=1,
            )
        assert "timed out" in str(caught.value)
        assert caught.value.status_class == ""  # 超时不属于非重试失败


# ---------------------------------------------------------------------------
# 四宫格生图停滞：对冲请求（hedge）+ 硬封顶
# ---------------------------------------------------------------------------
# 背景：上游无印没有进度字段，任务执行期间 status 恒为 3，因此「无字段变化超过阈值」
# 实际等价于「任务已运行 N 秒」。此前的实现会在阈值处丢弃任务，导致慢但合法的任务
# 达到阈值时保持对原任务的轮询，同时向「不同链路」
# provider 追发一个任务并行推进，先到先用，且提交次数与总时长都硬封顶。


WUYIN_SUBMIT_PATH = media_module.WUYIN_IMAGE_SUBMIT_PATH
WUYIN_DETAIL_PATH = media_module.WUYIN_IMAGE_DETAIL_PATH


def _grid_hedge_config(*, stall_seconds: float = 6.0) -> dict:
    """两个「不同链路」（base_url 与 api_key 均不同）的无印 provider。"""
    return {
        "image": {
            "base_url": "https://api.wuyinkeji.com",
            "api_key": "primary-key",
            "model": "image_gpt",
            "reference_model": "image_gpt",
            "image_size": "2048x2048",
        },
        "backup_image": {
            "base_url": "https://api.wuyinkeji.com/mirror",
            "api_key": "backup-key",
            "model": "image_gpt",
            "reference_model": "image_gpt",
            "image_size": "2048x2048",
        },
        "limits": {
            "image_stall_threshold_seconds": stall_seconds,
            "image_stall_threshold_2k_seconds": stall_seconds,
            "image_provider_strategy": "primary_first",
        },
    }


def _wuyin_processing():
    return _poll_response({"code": 200, "data": {"status": 3, "msg": "处理中"}})


def _wuyin_ready(url: str):
    return _poll_response({"code": 200, "data": {"status": 1, "image": url}})


def _install_wuyin_transport(monkeypatch, responder):
    """把无印 submit/detail 两个 HTTP 动作换成受控 mock，记录提交与轮询顺序。"""
    submissions: list[tuple[str, str]] = []
    polls: list[tuple[str, str]] = []
    events: list[tuple[str, str]] = []
    counter = {"n": 0}
    poll_counts: dict[str, int] = {}

    def fake_post(url, **_kwargs):
        base = url[: -len(WUYIN_SUBMIT_PATH)] if url.endswith(WUYIN_SUBMIT_PATH) else url
        counter["n"] += 1
        task_id = f"task-{counter['n']}"
        submissions.append((base, task_id))
        events.append(("submit", task_id))
        return _poll_response({"code": 200, "data": {"id": task_id}})

    def fake_get(url, **kwargs):
        base = url[: -len(WUYIN_DETAIL_PATH)] if url.endswith(WUYIN_DETAIL_PATH) else url
        task_id = str((kwargs.get("params") or {}).get("id") or "")
        polls.append((base, task_id))
        events.append(("poll", task_id))
        poll_counts[task_id] = poll_counts.get(task_id, 0) + 1
        return responder(base, task_id, poll_counts[task_id])

    monkeypatch.setattr(media_module._SESSION, "post", fake_post)
    monkeypatch.setattr(media_module._SESSION, "get", fake_get)
    return submissions, polls, poll_counts, events


def _grid_hedge_processor(monkeypatch, config, *, total_budget: float = 60.0):
    """受控（假）时钟 + 无网络参考图/结果下载，避免任何真实付费调用。"""
    clock = [1000.0]

    def fake_sleep(seconds):
        clock[0] += max(float(seconds), 1.0)

    monkeypatch.setattr(media_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(media_module.time, "sleep", fake_sleep)
    monkeypatch.setattr(media_module, "IMAGE_GENERATION_TOTAL_TIMEOUT_SECONDS", total_budget)
    monkeypatch.setattr(media_module, "is_safe_external_url", lambda _url: True)
    monkeypatch.setattr(
        media_module,
        "_download_provider_result_image",
        lambda url: (url.encode("utf-8"), "image/png"),
    )
    processor = ProductImageProcessor(lambda: config)
    monkeypatch.setattr(
        processor,
        "_load_references",
        lambda _values, *, limit: [
            (b"reference", "source.png", "image/png", "https://cdn.example/source.png")
        ],
    )
    return processor, clock


def test_grid_stall_hedges_to_different_link_and_keeps_polling_original(monkeypatch) -> None:
    """主 provider 停滞且存在不同链路备用 provider：追发第二个任务并继续轮询原任务，
    第二个先返回时采用它（断言提交序列、轮询序列与最终来源）。"""
    config = _grid_hedge_config(stall_seconds=6.0)
    processor, _clock = _grid_hedge_processor(monkeypatch, config, total_budget=60.0)
    backup_url = "https://result.example/backup.png"

    def responder(_base, task_id, _poll_index):
        if task_id == "task-2":
            return _wuyin_ready(backup_url)  # 对冲任务首次轮询即出图
        return _wuyin_processing()  # 原任务始终“处理中”

    submissions, _polls, poll_counts, events = _install_wuyin_transport(monkeypatch, responder)

    media = processor.generate(
        stage="grid_image",
        prompt="contract",
        reference_values=["https://cdn.example/source.png"],
        image_size="1024x1024",
    )

    # 1) 主任务先提交，停滞阈值后追发第二个（不同链路）任务。
    assert [base for base, _ in submissions] == [
        "https://api.wuyinkeji.com",
        "https://api.wuyinkeji.com/mirror",
    ]
    # 2) 原任务在对冲提交之后仍被继续轮询（没有被放弃）。
    hedge_submit_index = next(i for i, event in enumerate(events) if event == ("submit", "task-2"))
    assert any(
        event == ("poll", "task-1") for event in events[hedge_submit_index + 1 :]
    )
    assert poll_counts["task-1"] >= 4
    # 3) 先到先用：采用先返回的对冲结果（内容即该任务返回的图片 URL）。
    assert media.content == backup_url.encode("utf-8")


def test_grid_stall_with_single_link_retries_once_and_keeps_polling_original(monkeypatch) -> None:
    """只有一条链路（无「不同链路」可对冲）时退化为「同链路有界重试」：达停滞阈值后向
    同一条链路再提交 1 次（总提交数 == 2），原任务继续被轮询；后续某次返回图片即采用它，
    不再干等到总预算耗尽。（此前实现：单链路完全不追加提交，直接等到预算耗尽。）"""
    config = _grid_hedge_config(stall_seconds=6.0)
    # 备用 provider 与主 provider 链路完全一致（同 base_url、同 api_key）→ 只有一条链路。
    config["backup_image"]["base_url"] = "https://api.wuyinkeji.com"
    config["backup_image"]["api_key"] = "primary-key"
    processor, _clock = _grid_hedge_processor(monkeypatch, config, total_budget=60.0)
    retry_url = "https://result.example/same-link-retry.png"

    def responder(_base, task_id, poll_index):
        # 同链路重试任务（task-2）在第 2 次轮询出图；主任务（task-1）恒为处理中。
        if task_id == "task-2" and poll_index >= 2:
            return _wuyin_ready(retry_url)
        return _wuyin_processing()

    submissions, _polls, poll_counts, events = _install_wuyin_transport(monkeypatch, responder)

    media = processor.generate(
        stage="grid_image",
        prompt="contract",
        reference_values=["https://cdn.example/source.png"],
        image_size="1024x1024",
    )

    # 1) 主任务先提交，停滞阈值过后向「同一条链路」再提交 1 次（总提交数 == 2）。
    assert [base for base, _ in submissions] == [
        "https://api.wuyinkeji.com",
        "https://api.wuyinkeji.com",
    ]
    # 2) 原任务在重试提交之后仍被继续轮询（没有被放弃）。
    retry_submit_index = next(i for i, event in enumerate(events) if event == ("submit", "task-2"))
    assert any(event == ("poll", "task-1") for event in events[retry_submit_index + 1 :])
    assert poll_counts["task-1"] >= 4
    # 3) 先到先用：采用后到的重试任务结果（内容即该任务返回的图片 URL）。
    assert media.content == retry_url.encode("utf-8")


def test_grid_stall_with_single_link_retries_are_capped_at_two(monkeypatch) -> None:
    """单链路持续停滞：同链路重试有独立且更紧的上限——最多再提交 1 次（总提交数封顶为 2，
    而不是全局硬顶 3），且不延长总时长预算；预算耗尽仍无结果 → 抛 provider_stalled
    （不静默成功、不无限循环）。"""
    config = _grid_hedge_config(stall_seconds=6.0)
    config["backup_image"]["base_url"] = "https://api.wuyinkeji.com"
    config["backup_image"]["api_key"] = "primary-key"
    total_budget = 30.0
    processor, clock = _grid_hedge_processor(monkeypatch, config, total_budget=total_budget)

    submissions, _polls, _counts, _events = _install_wuyin_transport(
        monkeypatch, lambda _base, _task_id, _n: _wuyin_processing()
    )

    with pytest.raises(MediaProcessingError) as caught:
        processor.generate(
            stage="grid_image",
            prompt="contract",
            reference_values=["https://cdn.example/source.png"],
            image_size="1024x1024",
        )

    assert caught.value.status_class == "provider_stalled"
    # 同链路重试独立上限：总提交数封顶为 2（1 主 + 1 重试），绝不用到全局硬顶 3。
    assert len(submissions) == media_module.GRID_IMAGE_SINGLE_LINK_MAX_SUBMITS == 2
    assert media_module.GRID_IMAGE_SINGLE_LINK_MAX_SUBMITS < media_module.GRID_IMAGE_MAX_SUBMITS
    # 预算不延长：重试不追加时长，总耗时不超过一次生成的总时长预算。
    assert clock[0] - 1000.0 <= total_budget + media_module.WUYIN_IMAGE_POLL_INTERVAL_SECONDS


def test_grid_stall_hedge_submissions_are_capped_at_three(monkeypatch) -> None:
    """连续停滞时总提交次数硬封顶为 3（1 主 + 至多 2 hedge），不会无限新增请求。"""
    config = _grid_hedge_config(stall_seconds=6.0)
    config["limits"]["image_provider_strategy"] = "primary_first"
    processor, _clock = _grid_hedge_processor(monkeypatch, config, total_budget=60.0)
    providers = [
        {
            "name": f"link-{index}",
            "base_url": base_url,
            "api_key": f"key-{index}",
            "model": "image_gpt",
            "reference_model": "image_gpt",
            "image_size": "1024x1024",
        }
        for index, base_url in enumerate(
            (
                "https://api.wuyinkeji.com",
                "https://api.wuyinkeji.com/a",
                "https://api.wuyinkeji.com/b",
                "https://api.wuyinkeji.com/c",
            ),
            start=1,
        )
    ]
    submissions, _polls, _counts, _events = _install_wuyin_transport(
        monkeypatch, lambda _base, _task_id, _n: _wuyin_processing()
    )

    with pytest.raises(MediaProcessingError) as caught:
        processor._generate_with_limits(
            stage="grid_image",
            prompt="contract",
            reference_values=["https://cdn.example/source.png"],
            providers=providers,
            config=config,
        )

    assert caught.value.status_class == "provider_stalled"
    assert len(submissions) == media_module.GRID_IMAGE_MAX_SUBMITS == 3


def test_grid_stall_budget_exhaustion_raises_provider_stalled(monkeypatch) -> None:
    """对冲两条链路都长停滞：预算耗尽仍无结果 → 抛 provider_stalled，绝不静默成功或死循环。"""
    config = _grid_hedge_config(stall_seconds=6.0)
    processor, _clock = _grid_hedge_processor(monkeypatch, config, total_budget=30.0)
    submissions, _polls, _counts, _events = _install_wuyin_transport(
        monkeypatch, lambda _base, _task_id, _n: _wuyin_processing()
    )

    with pytest.raises(MediaProcessingError) as caught:
        processor.generate(
            stage="grid_image",
            prompt="contract",
            reference_values=["https://cdn.example/source.png"],
            image_size="1024x1024",
        )

    assert caught.value.status_class == "provider_stalled"
    assert 1 <= len(submissions) <= media_module.GRID_IMAGE_MAX_SUBMITS


def test_grid_hedge_first_ready_result_wins_and_stops_polling_other(monkeypatch) -> None:
    """两条任务并行时，先返回图片的那条被采用；采用后不再继续轮询另一条。"""
    config = _grid_hedge_config(stall_seconds=6.0)
    processor, _clock = _grid_hedge_processor(monkeypatch, config, total_budget=60.0)
    backup_url = "https://result.example/backup-first.png"

    def responder(_base, task_id, poll_index):
        # 对冲任务（task-2）第一次轮询就出图；主任务（task-1）恒为处理中。
        if task_id == "task-2":
            return _wuyin_ready(backup_url)
        return _wuyin_processing()

    submissions, _polls, poll_counts, events = _install_wuyin_transport(monkeypatch, responder)

    media = processor.generate(
        stage="grid_image",
        prompt="contract",
        reference_values=["https://cdn.example/source.png"],
        image_size="1024x1024",
    )

    assert len(submissions) == 2
    assert media.content == backup_url.encode("utf-8")
    # 采用先到的对冲结果后立即返回，最后一个事件是那次成功的轮询，之后不再有任何轮询。
    winning_index = next(
        i
        for i, event in enumerate(events)
        if event == ("poll", "task-2")
    )
    assert all(event[0] == "submit" for event in events[winning_index + 1 :])
    assert poll_counts["task-2"] == 1


# ---------------------------------------------------------------------------
# 2K（四宫格）停滞阈值重定标 + 每次提交可观测性
# ---------------------------------------------------------------------------
# 背景：2K 成功样本实测 max 216s，阈值由 180s 重定标为 300s。阈值仅作「触发再提交一次」
# 的信号，正常耗时（<300s）绝不触发额外提交；同时 attempt_count 反映真实提交次数，
# provider_status_class / provider_attempt_notes 记录每次提交的时间点与最终结果。


def test_two_k_grid_normal_duration_never_triggers_extra_submit(monkeypatch) -> None:
    """正常耗时（≈222s < 300s 2K 阈值）：提交数保持 1、最终成功，绝不因阈值多提交。"""
    config = _grid_hedge_config(stall_seconds=300.0)
    processor, _clock = _grid_hedge_processor(monkeypatch, config, total_budget=400.0)
    ready_url = "https://result.example/normal.png"

    def responder(_base, task_id, poll_index):
        # 主任务在第 74 次轮询（≈222s）出图；期间从未达到 300s 阈值。
        if task_id == "task-1" and poll_index >= 74:
            return _wuyin_ready(ready_url)
        return _wuyin_processing()

    submissions, _polls, _counts, _events = _install_wuyin_transport(monkeypatch, responder)

    media = processor.generate(
        stage="grid_image",
        prompt="contract",
        reference_values=["https://cdn.example/source.png"],
        image_size="2048x2048",
    )

    assert len(submissions) == 1
    assert media.attempt_count == 1
    assert media.provider_status_class == "success"
    assert media.provider_attempt_notes == ()
    assert media.content == ready_url.encode("utf-8")


def test_two_k_grid_over_threshold_triggers_one_more_submit_and_keeps_polling(monkeypatch) -> None:
    """超过 300s 阈值仍无图：向不同链路再提交 1 次，原任务继续轮询并最终出图。"""
    config = _grid_hedge_config(stall_seconds=300.0)
    processor, _clock = _grid_hedge_processor(monkeypatch, config, total_budget=400.0)
    original_url = "https://result.example/original.png"

    def responder(_base, task_id, poll_index):
        # 主任务在第 104 次轮询（≈312s，已越过 300s 阈值）出图；对冲任务恒为处理中。
        if task_id == "task-1" and poll_index >= 104:
            return _wuyin_ready(original_url)
        return _wuyin_processing()

    submissions, _polls, _counts, events = _install_wuyin_transport(monkeypatch, responder)

    media = processor.generate(
        stage="grid_image",
        prompt="contract",
        reference_values=["https://cdn.example/source.png"],
        image_size="2048x2048",
    )

    # 恰好再提交 1 次（不同链路），总提交 2 次。
    assert [base for base, _ in submissions] == [
        "https://api.wuyinkeji.com",
        "https://api.wuyinkeji.com/mirror",
    ]
    # 原任务在对冲提交之后仍被继续轮询，并最终采用原任务结果（先到先用）。
    hedge_submit_index = next(i for i, event in enumerate(events) if event == ("submit", "task-2"))
    assert any(event == ("poll", "task-1") for event in events[hedge_submit_index + 1 :])
    assert media.content == original_url.encode("utf-8")
    assert media.attempt_count == 2


def test_two_k_grid_hedge_reports_real_submit_count_and_outcomes(monkeypatch) -> None:
    """首次提交停滞、第二次提交出图：字段如实反映真实提交次数与每次结果。"""
    config = _grid_hedge_config(stall_seconds=300.0)
    processor, _clock = _grid_hedge_processor(monkeypatch, config, total_budget=400.0)
    backup_url = "https://result.example/backup.png"

    def responder(_base, task_id, _poll_index):
        if task_id == "task-2":
            return _wuyin_ready(backup_url)  # 对冲任务首次轮询即出图
        return _wuyin_processing()  # 原任务始终处理中（在 300s 阈值处被判停滞）

    submissions, _polls, _counts, _events = _install_wuyin_transport(monkeypatch, responder)

    media = processor.generate(
        stage="grid_image",
        prompt="contract",
        reference_values=["https://cdn.example/source.png"],
        image_size="2048x2048",
    )

    assert len(submissions) == 2
    assert media.attempt_count == 2
    assert media.content == backup_url.encode("utf-8")
    # 每条提交的相对时间点 + 最终结果；s1 在阈值处被判停滞，s2 出图；含总次数与总耗时。
    assert media.provider_attempt_notes[0] == "s1@0ms=stalled"
    assert media.provider_attempt_notes[1] == "s2@303000ms=ok"
    assert media.provider_attempt_notes[2] == "submits=2"
    assert media.provider_attempt_notes[3].startswith("total=")
    # provider_status_class 保留类名前缀并附追踪，随 result_json 对排障可见。
    assert media.provider_status_class.startswith("success;")
    assert "submits=2" in media.provider_status_class


def test_two_k_grid_stall_failure_carries_submit_trace_and_real_count(monkeypatch) -> None:
    """真卡死（预算耗尽）：异常携带真实提交次数，提交追踪进入异常消息（→ ai_notes）。"""
    config = _grid_hedge_config(stall_seconds=6.0)
    processor, _clock = _grid_hedge_processor(monkeypatch, config, total_budget=30.0)
    submissions, _polls, _counts, _events = _install_wuyin_transport(
        monkeypatch, lambda _base, _task_id, _n: _wuyin_processing()
    )

    with pytest.raises(MediaProcessingError) as caught:
        processor.generate(
            stage="grid_image",
            prompt="contract",
            reference_values=["https://cdn.example/source.png"],
            image_size="2048x2048",
        )

    assert caught.value.status_class == "provider_stalled"
    assert caught.value.attempt_count == len(submissions) == 2
    assert "提交追踪:" in str(caught.value)
    assert "submits=2" in str(caught.value)
    assert "stalled" in str(caught.value)

