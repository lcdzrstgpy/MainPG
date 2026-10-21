"""「本批次辅助提词（区域辅助出图）」功能测试。

覆盖：
1) 生成接口「批次开始前」先申请一份批次级短期 Ark 密钥（复用 /batch/freeze 机制），
   放入 granted_key 上下文后直连豆包生成，结束后结算释放；
2) 密钥申请失败与 AI 生成失败返回可区分的文案；
3) 生成期间密钥上下文正常建立、结束后清理干净（外层批次上下文不被破坏）；
4) batch_image_prompt 非空时注入轮播/精品/详情三条生图链路；为空时不注入。

所有 AI 调用与远端计费调用均被 mock/伪造，测试不会访问任何真实网络/扣费接口。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import wh_local.modules.product_processing.service as service_module
from wh_local.customer.contracts import (
    CustomerAuthRejected,
    CustomerAuthUnavailable,
)
from wh_local.modules.product_processing import server_ai_proxy
from wh_local.modules.product_processing.domain.photo_card_library import (
    assemble_panel_image_prompt,
)
from wh_local.modules.product_processing.doubao_ark import DoubaoArkClient
from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository
from wh_local.modules.product_processing.service import (
    ProductProcessingService,
    ProductProcessingValidationError,
)


GUIDELINE = "GUARD: render exactly five fingers and crisp printed labels, stable proportions."
MARKER = "BATCH-LEVEL CATEGORY EXPERT GUIDELINES"
ARK_KEY = "ark-short-lived-test-key"


def test_assemble_panel_prompt_assigns_scene_and_props_per_panel() -> None:
    assembly = assemble_panel_image_prompt(
        "office_stationery",
        [
            {"white_hero": True},
            {
                "scene": "scene_light_oak",
                "props": ["prop_notebook"],
            },
            {"scene": "scene_felt", "props": ["prop_linen_towel"]},
            {"scene": "scene_plaster", "props": []},
        ],
    )

    assert len(assembly.panels) == 4
    assert "PANEL 1" in assembly.prompt
    assert "pure white seamless studio sweep" in assembly.prompt
    assert "PANEL 2" in assembly.prompt
    assert "light oak desk surface" in assembly.prompt
    assert "one closed notebook" in assembly.prompt
    assert "PANEL 3" in assembly.prompt
    assert "deep felt surface" in assembly.prompt
    assert "one folded linen towel" in assembly.prompt
    assert "PANEL 4" in assembly.prompt
    assert "display-only and never included" in assembly.prompt
    assert assembly.selections["panel1"]["scene"] == "scene_white_sweep"
    assert assembly.selections["panel2"]["props"] == [
        "prop_notebook",
    ]
    assert assembly.selections["panel1"]["white_hero"] is True


def test_assemble_panel_prompt_uses_category_defaults_when_auto() -> None:
    assembly = assemble_panel_image_prompt("office_stationery")

    assert assembly.selections["panel2"]["scene"] == "scene_light_oak"
    assert assembly.selections["panel2"]["props"] == ["prop_notebook"]
    assert assembly.selections["panel3"]["scene"] == "scene_felt"
    assert assembly.selections["panel3"]["props"] == [
        "prop_notebook",
    ]
    assert assembly.selections["panel4"]["scene"] == "scene_plaster"
    assert assembly.selections["panel4"]["props"] == []


class _FakeBatchClient:
    """伪造远端计费客户端：记录 freeze/settle，绝不发真实请求。"""

    def __init__(
        self,
        *,
        ark_key: str = ARK_KEY,
        freeze_error: Exception | None = None,
    ) -> None:
        self.ark_key = ark_key
        self.freeze_error = freeze_error
        self.freeze_calls: list[tuple[str, dict]] = []
        self.settle_calls: list[tuple[str, str, dict]] = []

    def freeze_batch_points(self, remote_token: str, payload: dict) -> dict:
        self.freeze_calls.append((remote_token, payload))
        if self.freeze_error is not None:
            raise self.freeze_error
        keys = [] if self.ark_key is None else [{"provider": "ark", "api_key": self.ark_key}]
        return {
            "ok": True,
            "freeze": {
                "freeze_id": "fz-prompt-1",
                "account_id": "acct-1",
                "link_count": int(payload.get("link_count") or 1),
                "keys": keys,
            },
        }

    def settle_batch_points(self, remote_token: str, freeze_id: str, payload: dict) -> dict:
        self.settle_calls.append((remote_token, freeze_id, payload))
        return {"ok": True, "settle": {"freeze_id": freeze_id, "status": "settled"}}


def _raw() -> dict:
    return {
        "source_product_id": "batch-guideline-offer",
        "category_path": "Home & Kitchen > Kitchen & Dining > Drinkware",
        "source_attributes": [],
    }


class _PromptRepository:
    @staticmethod
    def prompts() -> dict[str, str]:
        return {}

    @staticmethod
    def active_prompt_template() -> None:
        return None


# ---------------------------------------------------------------------------
# 生成接口（service 层）
# ---------------------------------------------------------------------------


@pytest.fixture()
def service(tmp_path: Path) -> ProductProcessingService:
    db = create_database("sqlite:///:memory:")
    svc = ProductProcessingService(
        ProductProcessingRepository(db),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    yield svc
    db.dispose()


def _patch_batch_client(monkeypatch, client: _FakeBatchClient) -> _FakeBatchClient:
    """把 service 使用的远端计费客户端替换为伪造实现（不触网）。"""
    monkeypatch.setattr(service_module, "_batch_billing_client", lambda: client)
    return client


def _mock_doubao(
    monkeypatch,
    *,
    reply: str | None = None,
    boom: bool = False,
    captured: dict | None = None,
) -> None:
    """mock DoubaoArkClient.complete（保留真实 __init__，用于验证密钥上下文可用）。"""
    def _complete(self, messages, **kwargs):  # noqa: ANN001
        if captured is not None:
            captured["granted_key"] = self.granted_key
        if boom:
            raise RuntimeError("upstream down")
        return reply or ""

    monkeypatch.setattr(DoubaoArkClient, "complete", _complete)


def test_generate_batch_image_prompt_grants_key_and_uses_it(service, monkeypatch) -> None:
    """正常路径：先申请到短期 Ark 密钥 → 豆包生成 → 结算释放。"""
    client = _patch_batch_client(monkeypatch, _FakeBatchClient())
    captured: dict = {}
    _mock_doubao(monkeypatch, reply=GUIDELINE, captured=captured)

    out = service.generate_batch_image_prompt(
        {"titles": ["棉质T恤 A", "棉质T恤 B"]}, remote_token="remote-token"
    )

    assert out["ok"] is True
    assert out["batch_image_prompt"] == GUIDELINE
    assert out["title_count"] == 2
    # 生成期间 granted_key 上下文已注入本次申请到的短期密钥（否则 DoubaoArkClient 无法构造）
    assert captured["granted_key"] == ARK_KEY
    # 申请：冻结 1 条链接领密钥
    assert len(client.freeze_calls) == 1
    assert client.freeze_calls[0][0] == "remote-token"
    assert client.freeze_calls[0][1]["link_count"] == 1
    # 释放：按 no_return 全额退回，不向用户扣费
    assert len(client.settle_calls) == 1
    _, freeze_id, settle_payload = client.settle_calls[0]
    assert freeze_id == "fz-prompt-1"
    assert all(
        item["status"] == "no_return" for item in settle_payload["items"][0]["subitems"]
    )
    # 结束后上下文清理干净
    assert server_ai_proxy.granted_key("ark") == ""
    assert server_ai_proxy.batch_freeze_id() == ""


def test_generate_batch_image_prompt_grant_rejected_returns_distinct_error(service, monkeypatch) -> None:
    """额度不足/会话被拒 → 明确可区分文案，且绝不误报为「AI 未返回」。"""
    _patch_batch_client(
        monkeypatch,
        _FakeBatchClient(
            freeze_error=CustomerAuthRejected(402, "积分不足：需要冻结 45，当前可用 10")
        ),
    )
    captured: dict = {}
    _mock_doubao(monkeypatch, reply=GUIDELINE, captured=captured)

    out = service.generate_batch_image_prompt({"titles": ["棉质T恤 A"]}, remote_token="remote-token")

    assert out["ok"] is False
    assert out["batch_image_prompt"] == ""
    assert "额度" in out["message"]
    assert "AI 未返回有效" not in out["message"]
    # 密钥申请失败时不调用豆包
    assert "granted_key" not in captured


def test_generate_batch_image_prompt_without_session_returns_distinct_error(service, monkeypatch) -> None:
    """无计费会话（未登录/会话失效）→ 明确文案，且不发起密钥申请。"""
    client = _patch_batch_client(monkeypatch, _FakeBatchClient())

    out = service.generate_batch_image_prompt({"titles": ["棉质T恤 A"]})

    assert out["ok"] is False
    assert "会话" in out["message"] or "登录" in out["message"]
    assert "AI 未返回有效" not in out["message"]
    assert client.freeze_calls == []


def test_generate_batch_image_prompt_grant_unavailable_returns_distinct_error(service, monkeypatch) -> None:
    """授权/计费服务不可用 → 明确文案，区别于 AI 生成失败。"""
    _patch_batch_client(monkeypatch, _FakeBatchClient(freeze_error=CustomerAuthUnavailable("down")))
    captured: dict = {}
    _mock_doubao(monkeypatch, reply=GUIDELINE, captured=captured)

    out = service.generate_batch_image_prompt({"titles": ["棉质T恤 A"]}, remote_token="remote-token")

    assert out["ok"] is False
    assert "暂不可用" in out["message"]
    assert "AI 未返回有效" not in out["message"]
    assert "granted_key" not in captured


def test_generate_batch_image_prompt_missing_ark_key_releases_freeze(service, monkeypatch) -> None:
    """冻结成功但未下发可用 ark 密钥 → 报错并释放该冻结（避免预扣额度滞留）。"""
    client = _patch_batch_client(monkeypatch, _FakeBatchClient(ark_key=None))
    captured: dict = {}
    _mock_doubao(monkeypatch, reply=GUIDELINE, captured=captured)

    out = service.generate_batch_image_prompt({"titles": ["棉质T恤 A"]}, remote_token="remote-token")

    assert out["ok"] is False
    assert "Ark 密钥" in out["message"]
    assert "granted_key" not in captured
    assert len(client.settle_calls) == 1
    assert client.settle_calls[0][1] == "fz-prompt-1"


def test_generate_batch_image_prompt_ai_failure_returns_empty_and_releases(service, monkeypatch) -> None:
    """AI 失败 → 返回空串 + 明确 message；仍释放密钥冻结，不抛 500。"""
    client = _patch_batch_client(monkeypatch, _FakeBatchClient())
    _mock_doubao(monkeypatch, boom=True)

    out = service.generate_batch_image_prompt({"titles": ["棉质T恤 A"]}, remote_token="remote-token")

    assert out["batch_image_prompt"] == ""
    assert out["ok"] is False
    assert "AI 未返回有效" in out["message"]
    # 失败也要释放（结算解冻）
    assert len(client.settle_calls) == 1
    assert server_ai_proxy.granted_key("ark") == ""


def test_generate_batch_image_prompt_requires_titles(service) -> None:
    with pytest.raises(ProductProcessingValidationError):
        service.generate_batch_image_prompt({})


def test_generate_batch_image_prompt_restores_outer_batch_context(service, monkeypatch) -> None:
    """不回归：批次处理流程自有的密钥上下文在生成前后保持不变。"""
    _patch_batch_client(monkeypatch, _FakeBatchClient())
    captured: dict = {}
    _mock_doubao(monkeypatch, reply=GUIDELINE, captured=captured)

    with server_ai_proxy.server_ai_context(
        "batch-token", {}, granted_keys={"ark": "BATCH-ARK"}, freeze_id="fz-batch"
    ):
        out = service.generate_batch_image_prompt(
            {"titles": ["棉质T恤 A"]}, remote_token="remote-token"
        )
        assert out["ok"] is True
        # 生成用的是本次申请到的短期密钥，而非外层批次密钥
        assert captured["granted_key"] == ARK_KEY
        # 生成结束后外层批次上下文完好（未被清空/覆盖）
        assert server_ai_proxy.granted_key("ark") == "BATCH-ARK"
        assert server_ai_proxy.batch_freeze_id() == "fz-batch"
    assert server_ai_proxy.granted_key("ark") == ""


def test_generate_batch_image_prompt_resolves_titles_from_draft_ids(service, monkeypatch) -> None:
    _patch_batch_client(monkeypatch, _FakeBatchClient())
    captured: dict = {}

    def _fake_generate(self, titles, *, target_language="en", target_site="US", category=""):  # noqa: ANN001
        captured["titles"] = list(titles)
        captured["target_site"] = target_site
        return GUIDELINE

    monkeypatch.setattr(ProductProcessingService, "_generate_batch_image_guidelines", _fake_generate)
    monkeypatch.setattr(
        service.repository,
        "get_drafts",
        lambda draft_ids, workspace_id="local": [
            {"title": "草稿标题一", "raw_payload": {}},
            {"title": "", "product_name": "草稿标题二", "raw_payload": {}},
        ],
    )

    out = service.generate_batch_image_prompt(
        {"draft_ids": [1, 2], "target_site": "CO"}, remote_token="remote-token"
    )

    assert captured["titles"] == ["草稿标题一", "草稿标题二"]
    assert captured["target_site"] == "CO"
    assert out["batch_image_prompt"] == GUIDELINE


# ---------------------------------------------------------------------------
# 请求字段 & 批次设置
# ---------------------------------------------------------------------------


def test_draft_process_request_accepts_batch_image_prompt() -> None:
    from wh_local.modules.product_processing.api.schemas import DraftProcessRequest

    req = DraftProcessRequest(draft_ids=[1], batch_image_prompt=GUIDELINE)
    assert req.batch_image_prompt == GUIDELINE
    # 默认空 → 不注入
    assert DraftProcessRequest(draft_ids=[1]).batch_image_prompt == ""


def test_normalize_settings_preserves_batch_image_prompt() -> None:
    normalized = ProductProcessingService._normalize_settings(
        {"draft_ids": [1], "batch_image_prompt": GUIDELINE}
    )
    assert normalized["batch_image_prompt"] == GUIDELINE


# ---------------------------------------------------------------------------
# 三条生图链路注入
# ---------------------------------------------------------------------------


class _GridProcessor:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            stage="grid_image",
            content=b"generated-image",
            content_type="image/png",
            suffix=".png",
            provider="fake",
            model="fake",
            reference_count=1,
            attempt_count=1,
            provider_status_class="success",
        )

    @staticmethod
    def normalize_standalone_image(media, *, stage: str, enhance=None):
        return SimpleNamespace(stage=stage, content=media.content)

    @staticmethod
    def split_two_grid(media, *, start_index: int):
        return [
            SimpleNamespace(stage=f"grid_image_{start_index}", content=media.content),
            SimpleNamespace(stage=f"grid_image_{start_index + 1}", content=media.content),
        ]

    @staticmethod
    def split_four_grid(_media, *, enhance=None):
        return [
            SimpleNamespace(stage=f"grid_image_{slot}", content=f"slot-{slot}".encode())
            for slot in range(1, 5)
        ] + [SimpleNamespace(stage="grid_image_summary", content=b"old-summary")]

    @staticmethod
    def compose_grid_summary(parts):
        return SimpleNamespace(stage="grid_image_summary", content=b"new-summary")


def _grid_service(monkeypatch) -> tuple[ProductProcessingService, _GridProcessor]:
    svc = object.__new__(ProductProcessingService)
    svc.repository = _PromptRepository()
    processor = _GridProcessor()
    monkeypatch.setattr(service_module, "_ai_enabled", lambda: True)
    monkeypatch.setattr(service_module, "_media_types", lambda: (object, RuntimeError, ValueError))
    monkeypatch.setattr(svc, "_media_processor", lambda: processor)
    monkeypatch.setattr(
        svc,
        "_persist_media_for_preview",
        lambda parts, _task_id, _draft_id, _workspace_id: [
            f"https://example.com/{part.stage}.jpg" for part in parts
        ],
    )
    return svc, processor


def test_batch_image_prompt_injected_into_grid_chain(monkeypatch) -> None:
    svc, processor = _grid_service(monkeypatch)

    svc._generate_grid_images(
        1,
        2,
        _raw(),
        "Travel Mug",
        "Drinkware",
        ["https://example.com/source.jpg"],
        "en",
        "US",
        image_generation_count=1,
        batch_image_prompt=GUIDELINE,
    )

    assert processor.calls
    assert all(GUIDELINE in call["prompt"] for call in processor.calls)
    assert all(MARKER in call["prompt"] for call in processor.calls)


def test_batch_image_prompt_not_injected_when_empty(monkeypatch) -> None:
    svc, processor = _grid_service(monkeypatch)

    svc._generate_grid_images(
        1,
        2,
        _raw(),
        "Travel Mug",
        "Drinkware",
        ["https://example.com/source.jpg"],
        "en",
        "US",
        image_generation_count=1,
    )

    assert processor.calls
    assert all(MARKER not in call["prompt"] for call in processor.calls)


def test_batch_image_prompt_wrapper_grants_scene_on_non_main_panels_only(monkeypatch) -> None:
    """非主图格允许改背景/场景/道具，但必须带上道具安全契约与主图豁免声明。"""
    svc, processor = _grid_service(monkeypatch)

    svc._generate_grid_images(
        1,
        2,
        _raw(),
        "Travel Mug",
        "Drinkware",
        ["https://example.com/source.jpg"],
        "en",
        "US",
        image_generation_count=1,
        batch_image_prompt=GUIDELINE,
    )

    assert processor.calls
    for call in processor.calls:
        assert "PROP AND SCENE SAFETY" in call["prompt"]
        assert "EXEMPT" in call["prompt"]


def test_grid_chain_applies_preset_plan_after_runtime_contract(monkeypatch) -> None:
    """2K 四宫格链路：预设的逐格改写必须排在运行时契约之后，否则压不住写死的模板。"""

    svc, processor = _grid_service(monkeypatch)
    monkeypatch.setattr(service_module, "ocr_gate_enabled", lambda: False)
    monkeypatch.setattr(service_module, "inspect_visible_text", lambda _content: None)
    monkeypatch.setattr(svc, "_persist_provider_grid_originals", lambda *a, **k: ())

    svc._generate_grid_images(
        1,
        2,
        _raw(),
        "Bow Keychain",
        "Accessories",
        ["https://example.com/source.jpg"],
        "en",
        "US",
        image_generation_count=4,
        image_preset="lifestyle_use",
    )

    assert processor.calls
    prompt = processor.calls[0]["prompt"]
    assert "USER PRESET OVERRIDE" in prompt
    assert "THE IN-USE CLOSE-UP" in prompt
    assert "usage_scene.place" in prompt
    assert prompt.index("NON-OVERRIDABLE FOUR-GRID RUNTIME CONTRACT") < prompt.index(
        "USER PRESET OVERRIDE"
    )


def test_grid_chain_keeps_template_when_no_preset(monkeypatch) -> None:
    """没选模板（auto）时不得插入任何逐格改写，保持旧行为。"""

    svc, processor = _grid_service(monkeypatch)
    monkeypatch.setattr(service_module, "ocr_gate_enabled", lambda: False)
    monkeypatch.setattr(service_module, "inspect_visible_text", lambda _content: None)
    monkeypatch.setattr(svc, "_persist_provider_grid_originals", lambda *a, **k: ())

    svc._generate_grid_images(
        1,
        2,
        _raw(),
        "Bow Keychain",
        "Accessories",
        ["https://example.com/source.jpg"],
        "en",
        "US",
        image_generation_count=4,
        image_preset="auto",
    )

    assert processor.calls
    assert "USER PRESET OVERRIDE" not in processor.calls[0]["prompt"]


def test_generate_batch_image_guidelines_asks_for_props_with_hard_limits(
    service, monkeypatch
) -> None:
    """生成辅助提词的提示词必须同时要求：日常场景与配套道具方向 + 硬性限制 + 主图豁免。"""
    _patch_batch_client(monkeypatch, _FakeBatchClient())
    captured: dict = {}

    def _complete(self, messages, **kwargs):  # noqa: ANN001
        captured["messages"] = messages
        return GUIDELINE

    monkeypatch.setattr(DoubaoArkClient, "complete", _complete)

    out = service.generate_batch_image_prompt(
        {"titles": ["Panda Ceramic Mug", "Insulated Travel Tumbler"]},
        remote_token="remote-token",
    )

    assert out["ok"] is True
    body = captured["messages"][-1]["content"]
    assert "COMPANION PROPS" in body
    assert "HARD LIMITS" in body
    assert "MAIN image panel" in body


class _PremiumProcessor:
    """精品模式 mock：记录 4K 四宫格调用，不触发网络/OCR。"""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(
        self,
        *,
        stage: str,
        prompt: str,
        reference_values: list[str],
        layout_scaffold: bool = False,
        image_size: str | None = None,
        model_override: str | None = None,
    ) -> SimpleNamespace:
        self.prompts.append(prompt)
        return SimpleNamespace(
            stage=stage,
            content=b"image",
            content_type="image/png",
            attempt_count=1,
            provider_status_class="success",
        )

    @staticmethod
    def split_premium_four_grid(_media: SimpleNamespace) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(stage=f"premium_image_{slot}", content=f"slot-{slot}".encode())
            for slot in range(1, 5)
        ] + [SimpleNamespace(stage="premium_image_summary", content=b"summary")]

    def repair_generated(self, *, prior_content: bytes, prior_content_type: str, **kwargs):
        return SimpleNamespace(
            stage=kwargs.get("stage", "premium_image"),
            content=prior_content,
            content_type=prior_content_type,
            attempt_count=1,
            provider_status_class="success",
        )


def _premium_service(monkeypatch, processor) -> ProductProcessingService:
    svc = object.__new__(ProductProcessingService)
    svc.repository = _PromptRepository()
    monkeypatch.setattr(service_module, "_ai_enabled", lambda: True)
    monkeypatch.setattr(service_module, "_media_types", lambda: (object, RuntimeError, ValueError))
    monkeypatch.setattr(service_module, "ocr_gate_enabled", lambda: False)
    monkeypatch.setattr(svc, "_media_processor", lambda: processor)
    monkeypatch.setattr(
        svc,
        "_media_config_provider",
        lambda: {"image": {"premium_image_model": "gpt-image-2-4k", "premium_image_size": "4096x4096"}},
    )
    monkeypatch.setattr(
        svc,
        "_persist_media_for_preview",
        lambda parts, _task_id, _draft_id, _workspace_id: [
            f"https://example.com/premium_{index}.png" for index in range(len(parts))
        ],
    )
    return svc


def test_batch_image_prompt_injected_into_premium_chain(monkeypatch) -> None:
    processor = _PremiumProcessor()
    svc = _premium_service(monkeypatch, processor)

    svc._generate_premium_images(
        1,
        2,
        _raw(),
        "Insulated Travel Mug Stainless Steel 500 ml",
        "Kitchen & Dining",
        ["https://example.com/source.jpg"],
        "en",
        "US",
        [],
        batch_image_prompt=GUIDELINE,
    )

    assert len(processor.prompts) == 1
    assert GUIDELINE in processor.prompts[0]
    assert MARKER in processor.prompts[0]


class _DetailProcessor:
    def __init__(self) -> None:
        self.prompt = ""

    def generate(self, *, stage: str, prompt: str, reference_values: list[str]):
        self.prompt = prompt
        return SimpleNamespace(
            stage=stage,
            content=b"detail",
            content_type="image/png",
            suffix=".png",
            reference_count=1,
        )


def test_batch_image_prompt_injected_into_detail_chain(monkeypatch) -> None:
    svc = object.__new__(ProductProcessingService)
    svc.repository = _PromptRepository()
    processor = _DetailProcessor()
    monkeypatch.setattr(service_module, "_ai_enabled", lambda: True)
    monkeypatch.setattr(service_module, "_media_types", lambda: (object, RuntimeError, ValueError))
    monkeypatch.setattr(service_module, "ocr_gate_enabled", lambda: False)
    monkeypatch.setattr(svc, "_media_processor", lambda: processor)
    monkeypatch.setattr(
        svc,
        "_persist_media_for_preview",
        lambda _parts, _task_id, _draft_id, _workspace_id: ["https://example.com/detail.png"],
    )

    svc._generate_detail_images(
        1,
        2,
        _raw(),
        "Insulated Travel Mug Stainless Steel 500 ml",
        "Kitchen & Dining",
        ["https://example.com/original-1688.jpg"],
        "en",
        "US",
        batch_image_prompt=GUIDELINE,
    )

    assert GUIDELINE in processor.prompt
    assert MARKER in processor.prompt


def test_premium_panel_roles_follow_selected_preset() -> None:
    """四个小白模板必须各自改写四格角色，否则标签与实际出图对不上。"""

    generic = service_module._premium_panel_roles("auto")
    assert [role for role, _ in generic] == ["hero", "detail", "lifestyle", "orthographic"]

    proof = service_module._premium_panel_roles("hero_proof")
    assert "White-background hero" in proof[0][1]
    assert "no hands" in proof[2][1]

    lifestyle = service_module._premium_panel_roles("lifestyle_use")
    assert "MANDATORY" in lifestyle[2][1]
    assert "partial adult hand" in lifestyle[2][1]
    assert "no visible face" in lifestyle[2][1]
    assert "usage_scene.place" in lifestyle[2][1]

    macro = service_module._premium_panel_roles("macro_craft")
    assert "True macro craft shot" in macro[1][1]
    assert "90-degree top-down" not in macro[1][1]

    knolling = service_module._premium_panel_roles("knolling_set")
    assert "90-degree top-down" in knolling[0][1]
    assert "90-degree top-down" in knolling[2][1]

    # 未知/空的预设回落到通用四格，不能报错。
    assert service_module._premium_panel_roles("") == generic


def test_preset_switches_base_grid_template() -> None:
    """预设必须同时切换基础四宫格模板，否则平铺/人景仍然跑在影棚模板上。"""

    assert service_module._preset_image_template("knolling_set") == "C"
    assert service_module._preset_image_template("lifestyle_use") == "B"
    assert service_module._preset_image_template("hero_proof") == "A"
    assert service_module._preset_image_template("macro_craft") == "A"
    assert service_module._preset_image_template("auto") == ""
    assert service_module._preset_image_template("unknown") == ""


def test_knolling_override_keeps_front_hero_and_forces_flat_lay() -> None:
    """第 1 格必须保持平视白底主图，平铺只作用于第 2/3 格。"""

    plan = service_module._grid_preset_panel_plan("knolling_set")
    panel1 = next(line for line in plan.splitlines() if "Panel 1" in line)
    assert "FRONT VIEW" in panel1
    assert "Do NOT turn Panel 1 into a top-down shot" in panel1
    assert "TRUE 90-degree top-down flat lay" in plan
    assert "orthographic, no perspective" in plan

    roles = service_module._grid_panel_roles("knolling_set")
    assert "front view" in roles[0].lower()
    assert "90-degree top-down flat lay" in roles[1]


def test_random_preset_is_stable_per_draft_and_spreads_across_batch() -> None:
    """随机模板：同一链接重跑不变，整批链接要真的分散到四个模板上。"""

    pool = ["hero_proof", "lifestyle_use", "macro_craft", "knolling_set"]
    first = ProductProcessingService._random_preset_for_draft(7, 101, pool)
    assert first in pool
    assert first == ProductProcessingService._random_preset_for_draft(7, 101, pool)
    picks = {
        ProductProcessingService._random_preset_for_draft(7, draft_id, pool)
        for draft_id in range(1, 41)
    }
    assert picks == set(pool)
    assert ProductProcessingService._random_preset_for_draft(7, 101, []) == "auto"


def test_only_lifestyle_preset_opens_hands_and_partial_people(monkeypatch) -> None:
    """「真实使用」才允许手部/局部人物；其它模板仍保持无人。"""

    svc, _ = _grid_service(monkeypatch)
    with_people = svc._apply_batch_image_guidelines("T", "G", allow_people=True)
    without_people = svc._apply_batch_image_guidelines("T", "G")

    assert "Hands or a partial person are allowed" in with_people
    assert "no visible face" not in with_people
    assert "Keep every panel hands-free" in without_people


def test_runtime_contract_swaps_hands_rule_only_for_lifestyle_preset() -> None:
    """手部规则必须改在“不可覆盖”的契约内部，否则末尾的预设改写压不住它。"""

    default = service_module._grid_runtime_contract()
    opened = service_module._grid_runtime_contract(True)

    assert "HANDS-FREE (hard rule)" in default
    assert "HANDS-FREE (hard rule)" not in opened
    assert "HANDS & PARTIAL PEOPLE (hard rule)" in opened
    # 其余硬契约原样保留。
    assert "PRODUCT FIDELITY" in opened
    assert "zero AI-added visible text" in opened


def test_relax_hands_free_drops_only_the_forbidding_sentences() -> None:
    """模板里“禁止人物/手部”的整句要删掉，但不能误删无关句子。"""

    text = (
        "- Human & prop integrity: keep every frame hands-free - no hands, fingers, arms or people, "
        "never deformed fingers or waxy skin. Props must be few and neutral.\n"
        "- Panel 3 - Macro Detail / In-Use Close-Up: a much closer macro viewpoint. "
        "Keep it hands-free: no hands, fingers, arms or people may hold the product.\n"
        "- Safe margin: keep 7% inner margin on the hero panel."
    )
    relaxed = service_module._relax_hands_free(text)

    assert "hands-free" not in relaxed.lower()
    assert "no hands" not in relaxed.lower()
    assert "Props must be few and neutral." in relaxed
    assert "Safe margin: keep 7% inner margin on the hero panel." in relaxed


def test_local_ark_override_switches_provider_and_prompt_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WH_PP_IMAGE_* 本地覆盖：AI 处理出图切到方舟 Seedream，并用精简提示词档位。"""

    monkeypatch.delenv("WH_IMAGE_AI_MODEL", raising=False)
    monkeypatch.setenv("WH_PP_IMAGE_PROVIDER", "ark")
    monkeypatch.setenv("WH_PP_IMAGE_MODEL", "doubao-seedream-5-0-flash-260915")

    from wh_local.modules.product_processing.provider_config import (
        local_image_chain,
        resolve_ai_provider,
    )

    chain = local_image_chain()
    assert chain is not None
    assert chain["provider"] == "ark"
    assert chain["base_url"] == "https://ark.cn-beijing.volces.com/api/v3"
    assert chain["model"] == "doubao-seedream-5-0-flash-260915"

    provider = resolve_ai_provider()
    assert provider["provider"] == "ark"
    assert provider["image_model"] == "doubao-seedream-5-0-flash-260915"
    assert provider["image_models"] == ["doubao-seedream-5-0-flash-260915"]
    assert provider["_sys_image_ai"]["base_url"] == "https://ark.cn-beijing.volces.com/api/v3"

    assert service_module._image_prompt_profile() == "ark"

