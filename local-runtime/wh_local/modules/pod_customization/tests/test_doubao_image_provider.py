from __future__ import annotations

import base64
import io
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from wh_local.modules.pod_customization import ai_runtime as pod_ai_runtime
from wh_local.modules.pod_customization.ai_runtime import PodCustomizationAiRuntime
from wh_local.modules.pod_customization.billing_contract import PodExecutionGrant
from wh_local.modules.pod_customization.image_providers import (
    DOUBAO_PLATFORM,
    SUCHUANG_PLATFORM,
)
from wh_local.modules.pod_customization.image_providers.base import (
    GridImageRequest,
    GridReference,
)
from wh_local.modules.pod_customization.image_providers.doubao import (
    ARK_BASE_URL,
    ARK_IMAGE_PATH,
    ARK_RESOLUTION_TIER,
    ARK_SQUARE_SIZE,
    DoubaoSeedreamImageProvider,
    ark_size,
    sanitize_prompt_for_ark,
)
from wh_local.modules.pod_customization.runtime_contracts import (
    DirectListingGridRequest,
    ListingReferenceImage,
)
from wh_local.modules.product_processing.infrastructure.media import MediaProcessingError

DOUBAO_MODEL = "doubao-seedream-5-0-flash-260915"


def _png(width: int = 32) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, width), "#2563eb").save(buffer, "PNG")
    return buffer.getvalue()


def _grant(**keys: str) -> PodExecutionGrant:
    return PodExecutionGrant("freeze-doubao", 1, "2099-01-01T00:00:00Z", dict(keys))


class _ArkSession:
    """记录提交体并回一个同步生图结果。"""

    def __init__(self, *, payload: dict[str, Any] | None = None, status_code: int = 200) -> None:
        self.payload = payload if payload is not None else {"data": [{"url": "https://1.1.1.1/grid.png"}]}
        self.status_code = status_code
        self.posts: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, **kwargs: Any):
        self.posts.append((url, kwargs))
        return SimpleNamespace(
            status_code=self.status_code,
            headers={"Content-Type": "application/json"},
            text="" if self.status_code == 200 else '{"error": {"code": "AuthenticationError"}}',
            json=lambda: self.payload,
            close=lambda: None,
        )

    def close(self) -> None:
        return None


class _Host:
    """只提供平台实现需要的那部分执行资源。"""

    def __init__(self, session: _ArkSession) -> None:
        self._session = session

    @property
    def session(self) -> _ArkSession:
        return self._session

    def acquire_request_token(self) -> None:
        return None

    def ensure_open(self) -> None:
        return None

    def connection_slot(self, timeout_seconds: float):
        return nullcontext()

    def interruptible_wait(self, timeout_seconds: float) -> None:
        return None

    def publish_reference(self, *_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("豆包链路不应把参考图发布到 COS")

    def download_result(self, result_url: str) -> tuple[bytes, str]:
        return _png(), "image/png"


def test_ark_size_keeps_the_grid_square_and_clamps_oversized_pixels() -> None:
    # POD 只关心方阵母图（本地要切 2×2，再统一交付 800）：方形意图一律给 2K 方形。
    # 照字面透传 1024 会拆出 512px 面板（速创现网是 ~627px），且会重新触发 ONNX 超分。
    assert ark_size("1024x1024") == ARK_SQUARE_SIZE
    assert ark_size("2048x2048") == ARK_SQUARE_SIZE
    assert ark_size("4096x4096") == ARK_SQUARE_SIZE
    assert ark_size("1:1") == ARK_SQUARE_SIZE
    assert ark_size("auto") == ARK_SQUARE_SIZE
    # 非方形比值交给档位 + 提示词描述。
    assert ark_size("4:3") == ARK_RESOLUTION_TIER
    # 合法区间内的显式非方形宽高原样透传。
    assert ark_size("1600x1200") == "1600x1200"
    # 超上限的非方形也回落到档位。
    assert ark_size("5000x3000") == ARK_RESOLUTION_TIER


def test_doubao_generate_inlines_references_as_base64_and_never_publishes_to_cos() -> None:
    session = _ArkSession()
    published: list[str] = []

    class _NoCos:
        def upload_content_addressed_to_cos(self, *_args: Any, **_kwargs: Any) -> str:
            published.append("called")
            raise AssertionError("豆包链路不应把参考图发布到 COS")

    runtime = PodCustomizationAiRuntime(
        image_workers=1,
        requests_per_minute=0,
        session=session,
        public_image_fetcher=lambda *_args, **_kwargs: SimpleNamespace(
            content=_png(), content_type="image/png"
        ),
    )
    runtime._media = _NoCos()  # type: ignore[assignment]
    original_resolver = pod_ai_runtime._resolve_pod_image_model
    pod_ai_runtime._resolve_pod_image_model = lambda: DOUBAO_MODEL  # type: ignore[assignment]
    try:
        result = runtime.generate_listing_grid(
            DirectListingGridRequest(
                trial_id="trial-doubao",
                template_id="template-1",
                template_image=b"reference-image",
                template_content_type="image/jpeg",
                prompt="same shirt",
                attempt=1,
            ),
            grant=_grant(ark="fresh-ark-key"),
            call_id="trial-doubao:image:1",
        )
    finally:
        pod_ai_runtime._resolve_pod_image_model = original_resolver  # type: ignore[assignment]
        runtime.close()

    assert published == []
    assert len(session.posts) == 1
    url, kwargs = session.posts[0]
    assert url == f"{ARK_BASE_URL}{ARK_IMAGE_PATH}"
    assert kwargs["headers"]["Authorization"] == "Bearer fresh-ark-key"
    body = kwargs["json"]
    assert body["model"] == DOUBAO_MODEL
    # POD 默认请求尺寸 1024x1024 属「方阵意图」：统一升级为 2K 方形（拆格后每格 1024px）。
    assert body["size"] == ARK_SQUARE_SIZE
    assert body["response_format"] == "url"
    # 成品图要上架：必须显式关掉官方的「AI生成」水印。
    assert body["watermark"] is False
    # 参考图内嵌为 base64 data URL，而不是公网地址。
    assert body["image"] == "data:image/jpeg;base64," + base64.b64encode(
        b"reference-image"
    ).decode("ascii")
    assert result.provider == DOUBAO_PLATFORM
    assert result.model == DOUBAO_MODEL
    assert result.reference_count == 1


def test_doubao_sends_multiple_references_as_an_array() -> None:
    session = _ArkSession()
    provider = DoubaoSeedreamImageProvider()
    host = _Host(session)
    prepared = provider.prepare(
        host,
        (
            GridReference(content=b"artwork", content_type="image/png"),
            GridReference(content=b"target", content_type="image/jpeg"),
        ),
        namespace="trial-multi",
    )

    provider.generate(
        host,
        GridImageRequest(prompt="merge", size="2048x2048", trial_id="trial-multi"),
        prepared,
        model=DOUBAO_MODEL,
        api_key="k",
    )

    body = session.posts[0][1]["json"]
    assert body["image"] == [
        "data:image/png;base64," + base64.b64encode(b"artwork").decode("ascii"),
        "data:image/jpeg;base64," + base64.b64encode(b"target").decode("ascii"),
    ]


def test_doubao_rejects_4xx_without_retrying() -> None:
    provider = DoubaoSeedreamImageProvider()

    with pytest.raises(MediaProcessingError) as caught:
        provider.generate(
            _Host(_ArkSession(status_code=401)),
            GridImageRequest(prompt="p", size="2048x2048", trial_id="t"),
            [],
            model=DOUBAO_MODEL,
            api_key="k",
        )
    assert caught.value.status_class == "non_retryable_4xx"
    assert caught.value.status_code == 401


def test_doubao_surfaces_in_image_errors_as_non_retryable() -> None:
    session = _ArkSession(
        payload={
            "model": DOUBAO_MODEL,
            "data": [
                {"error": {"code": "ContentPolicyViolation", "message": "sensitive content"}}
            ],
        }
    )
    provider = DoubaoSeedreamImageProvider()

    with pytest.raises(MediaProcessingError) as caught:
        provider.generate(
            _Host(session),
            GridImageRequest(prompt="p", size="2048x2048", trial_id="t"),
            [],
            model=DOUBAO_MODEL,
            api_key="k",
        )
    assert caught.value.status_class == "non_retryable_4xx"
    assert "sensitive content" in str(caught.value)


def test_suchuang_platform_is_still_registered_alongside_doubao() -> None:
    runtime = PodCustomizationAiRuntime(image_workers=1, requests_per_minute=0)
    try:
        assert set(runtime._grid_providers) == {SUCHUANG_PLATFORM, DOUBAO_PLATFORM}
        assert runtime._provider_for("image_gpt_2.5").name == SUCHUANG_PLATFORM
        assert runtime._provider_for(DOUBAO_MODEL).name == DOUBAO_PLATFORM
        # 未知模型回落到默认平台（速创），不会把老批次打挂。
        assert runtime._provider_for("unknown-model").name == SUCHUANG_PLATFORM
    finally:
        runtime.close()


def test_replica_references_keep_their_order() -> None:
    request = DirectListingGridRequest(
        trial_id="trial-order",
        template_id="template-1",
        prompt="merge",
        attempt=1,
        reference_images=(
            ListingReferenceImage(
                role="pattern_source", content=b"source", content_type="image/png"
            ),
            ListingReferenceImage(
                role="target_product", content=b"target", content_type="image/png"
            ),
        ),
    )

    references = PodCustomizationAiRuntime._grid_references(request)

    assert [item.content for item in references] == [b"source", b"target"]


def test_prompt_sanitizer_only_touches_legacy_v1_prompts() -> None:
    """只清理历史 v1 模板；新主体锚定里出现「产品名称：」也不能被误删。"""
    legacy = (
        "[POD DIRECT LISTING PROMPT v1]\n"
        "Excluded elements: 品牌 logo, 商标标识, 深色高饱和色块.\n"
        "产品名称：圣诞主题麻将推尺\n"
        "硬性规则：每款生成一次。\n"
    )
    cleaned = sanitize_prompt_for_ark(legacy)
    assert "Excluded elements: 品牌 logo, 商标标识, 深色高饱和色块." in cleaned
    assert "产品名称：" not in cleaned
    assert "硬性规则：每款生成一次。" in cleaned

    modern = "=== SUBJECT ANCHOR ===\n产品名称：用户自己写的主体\n硬性规则：每款生成一次。"
    assert sanitize_prompt_for_ark(modern) == modern


def test_prompt_sanitizer_removes_duplicated_operator_fields() -> None:
    prompt = (
        "[POD DIRECT LISTING PROMPT v1]\n"
        "=== PART 1 ===\n"
        "Product name: 圣诞主题麻将推尺.\n"
        "=== BATCH-SPECIFIC HARD RULES ===\n"
        "产品名称：圣诞主题麻将推尺\n"
        "产品品类：麻将桌游配件\n"
        "目标市场：美国\n"
        "目标人群：中年麻将爱好者\n"
        "核心卖点：圣诞限定、塑料轻便\n"
        "主题整批统一风格：圣诞节喜庆\n"
        "偏好配色：圣诞正红、墨绿\n"
        "禁用元素：品牌 logo、毒品或吸毒工具、纳粹标志。\n"
        "硬性规则：每款生成一次。\n"
    )

    cleaned = sanitize_prompt_for_ark(prompt)

    # 与 PART 1 重复的运营字段整行删除，只保留硬性规则。
    for label in ("产品名称：", "产品品类：", "目标市场：", "目标人群：",
                  "核心卖点：", "主题整批统一风格：", "偏好配色：", "禁用元素："):
        assert label not in cleaned
    assert "Product name: 圣诞主题麻将推尺." in cleaned
    assert "硬性规则：每款生成一次。" in cleaned


def test_prompt_sanitizer_leaves_clean_prompts_untouched() -> None:
    prompt = "Excluded elements: 深色高饱和色块, 金属尖锐饰品.\n其余内容"

    assert sanitize_prompt_for_ark(prompt) == prompt


def test_doubao_submits_the_prompt_without_duplicated_operator_fields() -> None:
    session = _ArkSession()
    provider = DoubaoSeedreamImageProvider()
    host = _Host(session)
    prompt = (
        "[POD DIRECT LISTING PROMPT v1]\n"
        "Invent a new pattern.\n"
        "Excluded elements: 商标标识, 深色高饱和色块.\n"
        "产品名称：圣诞主题麻将推尺\n"
        "硬性规则：每款生成一次。"
    )

    provider.generate(
        host,
        GridImageRequest(prompt=prompt, size="2048x2048", trial_id="trial-sanitize"),
        [],
        model=DOUBAO_MODEL,
        api_key="k",
    )

    sent = session.posts[0][1]["json"]["prompt"]
    assert "产品名称：" not in sent
    assert "Excluded elements: 商标标识, 深色高饱和色块." in sent
    assert "硬性规则：每款生成一次。" in sent
    assert "Invent a new pattern." in sent


def test_worker_image_gate_follows_the_selected_platform(monkeypatch, tmp_path) -> None:
    """切到豆包后，生图前的密钥门禁必须按平台要 ark，而不是写死的 wuyin。

    否则远端若只下发 ark（豆包链路真正需要的那个），每个款都会先撞上
    「授权不可用」并把批次挂起，永远等不到一个不会出现的 wuyin。
    """
    from wh_local.db import init_db
    from wh_local.modules.basic_settings.service import SystemConfigService
    from wh_local.modules.pod_customization.billing_contract import (
        PodBillingAuthorizationRequired,
        PodCallPlan,
    )
    from wh_local.modules.pod_customization.worker import PodBillingRun
    from wh_local.session import Actor

    database_path = tmp_path / "workbench.sqlite3"
    init_db(database_path)
    SystemConfigService(database_path).save_pod_image_model(DOUBAO_MODEL, actor_id="tester")
    monkeypatch.setattr(
        "wh_local.modules.product_processing.provider_config.registered_system_config_db_path",
        lambda: str(database_path),
    )
    actor = Actor(id="designer-1", username="designer", role="admin", workspace_id="workspace-a")
    plan = PodCallPlan.for_retry("doubao-gate", feature="pod.image")

    assert pod_ai_runtime.pod_image_grant_provider() == "ark"

    ark_only = PodBillingRun(
        actor,
        None,  # type: ignore[arg-type]  # start() 不触碰 coordinator
        plan,
        PodExecutionGrant("freeze-ark", 1, "2099-01-01T00:00:00Z", {"ark": "key"}),
    )
    ark_only.start(plan.calls[0].call_id, "pod.image")

    wuyin_only = PodBillingRun(
        actor,
        None,  # type: ignore[arg-type]
        plan,
        PodExecutionGrant("freeze-wuyin", 1, "2099-01-01T00:00:00Z", {"wuyin": "key"}),
    )
    with pytest.raises(PodBillingAuthorizationRequired):
        wuyin_only.start(plan.calls[0].call_id, "pod.image")


def test_default_platform_requires_ark(monkeypatch) -> None:
    # 没注册配置库时回落到 POD 默认（豆包）：门禁要 ark。
    monkeypatch.setattr(
        "wh_local.modules.product_processing.provider_config.registered_system_config_db_path",
        lambda: None,
    )

    assert pod_ai_runtime.pod_image_grant_provider() == "ark"


def test_prepare_passes_published_urls_through_and_falls_back_to_data_urls() -> None:
    provider = DoubaoSeedreamImageProvider()
    png = _png()
    references = (
        GridReference(content=png, content_type="image/png", url="https://cos.example.com/a.png"),
        GridReference(content=png, content_type="image/png"),
    )

    prepared = provider.prepare(None, references, namespace="workspace")  # type: ignore[arg-type]

    assert prepared[0] == "https://cos.example.com/a.png"
    assert prepared[1].startswith("data:image/png;base64,")


def test_anchor_fallback_drops_urls_so_references_go_inline() -> None:
    png = _png()
    request = DirectListingGridRequest(
        trial_id="trial",
        prompt="prompt",
        attempt=2,
        anchor_fallback=True,
        reference_images=(
            ListingReferenceImage(
                role="anchor_1",
                content=png,
                content_type="image/png",
                url="https://cos.example.com/a.png",
            ),
        ),
    )

    references = PodCustomizationAiRuntime._grid_references(request)

    assert references[0].url == ""
    assert references[0].content == png
