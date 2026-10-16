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
