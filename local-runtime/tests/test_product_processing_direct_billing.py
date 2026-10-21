from __future__ import annotations

import json as jsonlib
from pathlib import Path
from typing import Any

import pytest

from wh_local.modules.product_processing import batch_billing as batch_billing_module
from wh_local.modules.product_processing import provider_config as provider_config_module
from wh_local.modules.product_processing import server_ai_proxy
from wh_local.modules.product_processing.doubao_ark import DoubaoArkClient, DoubaoArkError
from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository
from wh_local.modules.product_processing.service import ProductProcessingService


def test_server_ai_context_carries_granted_keys_and_freeze_id() -> None:
    with server_ai_proxy.server_ai_context(
        "token-1",
        {"text": "usage-1"},
        granted_keys={"wuyin": "W-KEY", "ark": "A-KEY"},
        freeze_id="fz-1",
    ):
        assert server_ai_proxy.granted_key("wuyin") == "W-KEY"
        assert server_ai_proxy.granted_key("ark") == "A-KEY"
        assert server_ai_proxy.batch_freeze_id() == "fz-1"
    # 退出上下文后必须清空，避免串批
    assert server_ai_proxy.granted_key("wuyin") == ""
    assert server_ai_proxy.batch_freeze_id() == ""


def test_server_ai_context_propagates_to_worker_thread() -> None:
    # 生产路径（service._submit_with_context）用 contextvars.copy_context() + context.run
    # 把直连密钥上下文投递进 ThreadPoolExecutor 子线程；这里用同一机制验证。
    import contextvars
    import threading

    seen: dict[str, str] = {}

    def worker() -> None:
        seen["wuyin"] = server_ai_proxy.granted_key("wuyin")
        seen["freeze"] = server_ai_proxy.batch_freeze_id()

    with server_ai_proxy.server_ai_context("t", {}, granted_keys={"wuyin": "K"}, freeze_id="fz"):
        context = contextvars.copy_context()
        thread = threading.Thread(target=context.run, args=(worker,))
        thread.start()
        thread.join()
    assert seen == {"wuyin": "K", "freeze": "fz"}


def test_doubao_ark_direct_uses_granted_key_and_upstream_url(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    recorded: dict[str, Any] = {}

    def fake_post(url: str, *, headers: dict[str, str], json: dict[str, Any], **_: Any):
        recorded["url"] = url
        recorded["auth"] = headers.get("Authorization")
        recorded["json"] = json
        response = requests.Response()
        response.status_code = 200
        response._content = jsonlib.dumps(
            {"choices": [{"message": {"content": '{"optimized_title":"OK","description":"D","variant_translations":[],"product_dimensions":{}}'}}]}
        ).encode("utf-8")
        return response

    monkeypatch.setattr("wh_local.modules.product_processing.doubao_ark._HTTP_SESSION.post", fake_post)
    with server_ai_proxy.server_ai_context("t", {}, granted_keys={"ark": "ARK-KEY"}, freeze_id="fz"):
        client = DoubaoArkClient()
        assert client.direct is True
        content = client.complete([{"role": "user", "content": "hi"}])
    assert content.startswith('{"optimized_title"')
    assert recorded["url"].startswith("https://ark.cn-beijing.volces.com")
    assert recorded["auth"] == "Bearer ARK-KEY"
    assert recorded["json"]["model"] == "doubao-seed-2-1-lite-260915"


def test_doubao_ark_requires_granted_key_without_gateway_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无 ark 短期密钥时不再回落到平台网关，直接报错（由上层中止并上报）。"""
    calls: list[Any] = []

    def fake_post(*args: Any, **kwargs: Any):
        calls.append((args, kwargs))

    monkeypatch.setattr("wh_local.modules.product_processing.doubao_ark._HTTP_SESSION.post", fake_post)
    with server_ai_proxy.server_ai_context("token-1", {"text": "usage-1"}):
        with pytest.raises(DoubaoArkError):
            DoubaoArkClient()
    # 缺少直连密钥时绝不能发出任何上游/网关请求
    assert calls == []


def test_provider_config_direct_image_section_defaults_to_ark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with server_ai_proxy.server_ai_context("t", {}, granted_keys={"ark": "ARK-KEY"}):
        provider = provider_config_module.resolve_ai_provider()
        assert provider["direct_mode"] is True
        assert provider["provider"] == "ark"
        sys_image = provider["_sys_image_ai"]
        assert sys_image["base_url"] == "https://ark.cn-beijing.volces.com/api/v3"
        assert sys_image["api_key"] == "ARK-KEY"
        assert sys_image["model"] == "doubao-seedream-5-0-flash-260915"


def test_provider_config_direct_placeholder_without_granted_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无任务密钥时恒用直连地址 + 占位符，绝不回落到托管网关地址。"""
    with server_ai_proxy.server_ai_context("t", {}):
        provider = provider_config_module.resolve_ai_provider()
        assert provider["direct_mode"] is True
        assert provider["provider"] == "ark"
        sys_image = provider["_sys_image_ai"]
        assert sys_image["base_url"] == "https://ark.cn-beijing.volces.com/api/v3"
        # 占位符仅为让启动自检报告图片能力就绪；无任务上下文时不会发起真实请求。
        assert sys_image["api_key"] == "direct-pending"
        # 未下发链路策略时没有备用链路——客户端不自行切换。
        assert provider["_sys_backup_image_ai"] is None


def test_provider_config_follows_server_image_plan_in_auto_mode() -> None:
    """服务端下发 auto 策略：主链路无印 + 备用中转，客户端只按顺序对接。

    这是「上游无印维护/拒单」时的预备措施：链路开关与切换权都在服务器，
    客户端既不内置链路凭据，也不自行决定走哪条链路。
    """
    plan = {
        "mode": "auto",
        "providers": [
            {
                "role": "primary",
                "base_url": "https://api.wuyinkeji.com",
                "api_key": "WUYIN-K",
                "model": "image_gpt",
                "reference_model": "image_gpt",
            },
            {
                "role": "backup",
                "base_url": "https://relay.example/v1/",
                "api_key": "RELAY-K",
                "model": "gpt-image-2-2k",
                "reference_model": "gpt-image-2-2k",
            },
        ],
    }
    with server_ai_proxy.server_ai_context(
        "t", {}, granted_keys={"wuyin": "WUYIN-K"}, image_plan=plan
    ):
        provider = provider_config_module.resolve_ai_provider()

    assert provider["_sys_image_ai"]["base_url"] == "https://api.wuyinkeji.com"
    assert provider["_sys_image_ai"]["api_key"] == "WUYIN-K"
    assert provider["_sys_image_ai"]["model"] == "image_gpt"
    backup = provider["_sys_backup_image_ai"]
    assert backup is not None
    # 末尾斜杠归一化，避免拼出 //images/edits。
    assert backup["base_url"] == "https://relay.example/v1"
    assert backup["api_key"] == "RELAY-K"
    assert backup["model"] == "gpt-image-2-2k"


def test_provider_config_relay_only_mode_makes_relay_the_primary_chain() -> None:
    """服务端切到「只用中转」：中转成为主链路，且不再要求 wuyin 短期密钥。"""
    plan = {
        "mode": "relay",
        "providers": [
            {
                "role": "primary",
                "base_url": "https://relay.example/v1",
                "api_key": "RELAY-K",
                "model": "gpt-image-2-2k",
                "reference_model": "gpt-image-2-2k",
            }
        ],
    }
    with server_ai_proxy.server_ai_context("t", {}, granted_keys={"ark": "ARK-K"}, image_plan=plan):
        provider = provider_config_module.resolve_ai_provider()

    assert provider["_sys_image_ai"]["base_url"] == "https://relay.example/v1"
    assert provider["_sys_image_ai"]["api_key"] == "RELAY-K"
    assert provider["_sys_image_ai"]["model"] == "gpt-image-2-2k"
    assert provider["_sys_backup_image_ai"] is None
    # 策略已指定出图链路 → 服务端本就不下发 wuyin，不应把 wuyin 算作必需密钥而中止任务。
    assert provider_config_module.image_chain_supplied(plan) is True


def test_provider_config_ignores_malformed_image_plan() -> None:
    """策略字段不全时视为「未下发」，客户端回落内置无印直连而非用空链路。"""
    malformed = {
        "mode": "auto",
        "providers": [{"role": "primary", "base_url": "", "api_key": "", "model": ""}],
    }
    assert provider_config_module.parse_image_chain(malformed) == []
    assert provider_config_module.image_chain_supplied(malformed) is False

    with server_ai_proxy.server_ai_context("t", {}, image_plan=malformed):
        provider = provider_config_module.resolve_ai_provider()
    assert provider["_sys_image_ai"]["base_url"] == "https://ark.cn-beijing.volces.com/api/v3"
    assert provider["_sys_backup_image_ai"] is None


def test_media_config_exposes_both_chains_under_auto_plan() -> None:
    """端到端：auto 策略下媒体配置同时带出主/备两段，provider 池变成 2 条。"""
    from wh_local.modules.product_processing.infrastructure.media import ProductImageProcessor

    plan = {
        "mode": "auto",
        "providers": [
            {
                "role": "primary",
                "base_url": "https://api.wuyinkeji.com",
                "api_key": "WUYIN-K",
                "model": "image_gpt",
                "reference_model": "image_gpt",
            },
            {
                "role": "backup",
                "base_url": "https://relay.example/v1",
                "api_key": "RELAY-K",
                "model": "gpt-image-2-2k",
                "reference_model": "gpt-image-2-2k",
            },
        ],
    }
    with server_ai_proxy.server_ai_context(
        "t", {}, granted_keys={"wuyin": "WUYIN-K"}, image_plan=plan
    ):
        media_config = ProductProcessingService._media_config_provider()

    assert media_config["image"]["base_url"] == "https://api.wuyinkeji.com"
    assert media_config["image"]["api_key"] == "WUYIN-K"
    assert media_config["backup_image"]["base_url"] == "https://relay.example/v1"
    assert media_config["backup_image"]["api_key"] == "RELAY-K"

    pool = ProductImageProcessor._providers(media_config)
    assert [item["name"] for item in pool] == ["primary:image_gpt", "backup:gpt-image-2-2k"]


def test_server_single_image_mode_forces_four_standalone_images() -> None:
    """服务端下发 image_mode=single 时，必须走「4 张独立方图」路径（count=1）。

    nanoBanana / Gemini 系不遵守严格 2x2 运输四宫格契约（自由拼版 → 本地切分必失败），
    这类链路改用单图模式；开关权在服务器，客户端只照做。
    """
    from wh_local.modules.product_processing import service as service_module

    assert service_module._effective_image_generation_count(4) == 4
    assert service_module._effective_image_generation_count(None) == 4

    with server_ai_proxy.server_ai_context("t", {}, image_plan={"image_mode": "single"}):
        assert service_module._effective_image_generation_count(4) == 1

    with server_ai_proxy.server_ai_context("t", {}, image_plan={"image_mode": "grid"}):
        assert service_module._effective_image_generation_count(4) == 4


def test_derive_batch_item_results_maps_completed_and_failed() -> None:
    settings = {
        "processing_scope": [],
        "title_optimize": True,
        "description": True,
        "size": True,
        "grid_image": True,
        "detail_image": True,
    }
    items = [
        {"status": "completed"},
        {"status": "failed"},
    ]
    derived = batch_billing_module.derive_item_results(items, settings)
    assert len(derived) == 2
    assert [sub["status"] for sub in derived[0]["subitems"]] == ["success"] * 5
    assert [sub["status"] for sub in derived[1]["subitems"]] == ["no_return"] * 5
    assert [sub["feature"] for sub in derived[0]["subitems"]] == list(batch_billing_module.SUBITEM_FEATURES)


def test_derive_batch_item_respects_disabled_features() -> None:
    settings = {
        "processing_scope": [],
        "title_optimize": True,
        "description": False,
        "size": False,
        "grid_image": False,
        "image_rewrite": False,
        "detail_image": False,
    }
    derived = batch_billing_module.derive_item_results([{"status": "completed"}], settings)
    features = [sub["feature"] for sub in derived[0]["subitems"]]
    assert features == ["title"]


def test_derive_batch_item_refunds_text_subitems_when_exact_cache_skips_provider() -> None:
    settings = {
        "processing_scope": [],
        "title_optimize": True,
        "description": True,
        "size": True,
        "grid_image": True,
        "detail_image": True,
    }
    derived = batch_billing_module.derive_item_results(
        [
            {
                "status": "completed",
                "result": {"billing_skipped_kinds": ["text"]},
            }
        ],
        settings,
    )
    statuses = {
        subitem["feature"]: subitem["status"]
        for subitem in derived[0]["subitems"]
    }
    assert statuses == {
        "title": "no_return",
        "description": "no_return",
        "product_dimensions": "no_return",
        "four_grid": "success",
        "detail_images": "success",
    }


def test_derive_batch_item_refunds_image_subitems_on_source_fallback() -> None:
    """AI 生图失败回退来源图的 completed 链接，图像子项全额退款，文字子项照常计费。"""
    settings = {
        "processing_scope": [],
        "title_optimize": True,
        "description": True,
        "size": True,
        "grid_image": True,
        "detail_image": True,
    }
    derived = batch_billing_module.derive_item_results(
        [
            {
                "status": "completed",
                "result": {"provider_status_classes": {"four_grid": "source_fallback"}},
            }
        ],
        settings,
    )
    statuses = {
        subitem["feature"]: subitem["status"]
        for subitem in derived[0]["subitems"]
    }
    assert statuses == {
        "title": "success",
        "description": "success",
        "product_dimensions": "success",
        "four_grid": "no_return",
        "detail_images": "no_return",
    }


def test_derive_batch_item_images_stay_success_when_no_fallback() -> None:
    """正常生成（four_grid 非 source_fallback）的图像子项仍按 success 全价计费。"""
    settings = {
        "processing_scope": [],
        "title_optimize": True,
        "description": True,
        "size": True,
        "grid_image": True,
        "detail_image": True,
    }
    derived = batch_billing_module.derive_item_results(
        [
            {
                "status": "completed",
                "result": {"provider_status_classes": {"four_grid": "success"}},
            }
        ],
        settings,
    )
    statuses = {
        subitem["feature"]: subitem["status"]
        for subitem in derived[0]["subitems"]
    }
    assert statuses == {
        "title": "success",
        "description": "success",
        "product_dimensions": "success",
        "four_grid": "success",
        "detail_images": "success",
    }


def test_derive_batch_item_marks_retried_links_for_retry_premium() -> None:
    """重试溢价的链接，每个上报子项都带 retried 标记供服务端按重试单价结算。"""
    from wh_local.modules.product_processing.service import _item_had_retry

    assert _item_had_retry({"ai_notes": ["four_grid:slot_1k_repair:3"], "provider_attempts": {}}) is True
    assert _item_had_retry({"ai_notes": ["detail_images:chinese_repaired"], "provider_attempts": {}}) is True
    assert _item_had_retry({"ai_notes": [], "provider_attempts": {"four_grid": 4}}) is True
    assert _item_had_retry({"ai_notes": [], "provider_attempts": {"doubao_text": 3}}) is True
    assert _item_had_retry({"ai_notes": [], "provider_attempts": {"four_grid": 1, "doubao_text": 1}}) is False
    assert _item_had_retry({"ai_notes": ["images:receipt-hit"], "provider_attempts": {}}) is False

    settings = {
        "processing_scope": [],
        "title_optimize": True,
        "description": True,
        "size": True,
        "grid_image": True,
        "detail_image": True,
    }
    derived = batch_billing_module.derive_item_results(
        [
            {"status": "completed", "billing_retried": True},
            {"status": "completed", "billing_retried": False},
            {"status": "failed", "billing_retried": True},
        ],
        settings,
    )
    assert all(sub.get("retried") for sub in derived[0]["subitems"])
    assert all(not sub.get("retried") for sub in derived[1]["subitems"])
    assert all(sub.get("retried") for sub in derived[2]["subitems"])


def test_freeze_scope_items_filters_to_recorded_item_ids() -> None:
    """重试/混合状态任务：结算只上报冻结时刻记录的 pending 条目（条数==link_count）。"""
    from wh_local.modules.product_processing.service import _freeze_scope_items

    items = [
        {"item_id": 1, "status": "completed"},
        {"item_id": 2, "status": "failed"},
        {"item_id": 3, "status": "failed"},
    ]
    scoped = _freeze_scope_items(items, {"item_ids": [2, 3], "link_count": 2})
    assert [item["item_id"] for item in scoped] == [2, 3]
    assert len(scoped) == 2


def test_freeze_scope_items_fallback_all_when_no_recorded_ids() -> None:
    from wh_local.modules.product_processing.service import _freeze_scope_items

    items = [{"item_id": 1, "status": "completed"}]
    assert _freeze_scope_items(items, {}) == items


def test_freeze_scope_items_fallback_failed_when_task_missing() -> None:
    from wh_local.modules.product_processing.service import _freeze_scope_items

    scoped = _freeze_scope_items([], {"link_count": 3})
    assert len(scoped) == 3
    assert all(item["status"] == "failed" for item in scoped)


class RecordingBatchClient:
    """Fake CustomerAuthClient recording freeze/settle for direct-mode tests."""

    def __init__(self) -> None:
        self.freeze_calls: list[tuple[str, dict[str, Any]]] = []
        self.settle_calls: list[tuple[str, str, dict[str, Any]]] = []

    def freeze_batch_points(self, remote_token: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.freeze_calls.append((remote_token, payload))
        return {
            "ok": True,
            "freeze": {
                "freeze_id": "fz-direct-1",
                "link_count": int(payload.get("link_count") or 1),
                "keys": [
                    {"provider": "wuyin", "kind": "image", "api_key": "W-KEY"},
                    {"provider": "ark", "kind": "text", "api_key": "A-KEY"},
                ],
            },
        }

    def settle_batch_points(self, remote_token: str, freeze_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.settle_calls.append((remote_token, freeze_id, payload))
        return {"ok": True, "settle": {"freeze_id": freeze_id, "status": "settled"}}

    def batch_freeze_status(self, remote_token: str, freeze_id: str) -> dict[str, Any]:
        return {"ok": True, "freeze": {"freeze_id": freeze_id, "status": "frozen"}}


def _isolate_batch_freezes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """每个用例使用独立侧车文件，避免跨用例残留的 open 冻结互相污染。"""
    monkeypatch.setattr(
        batch_billing_module,
        "_open_freezes_path",
        lambda: tmp_path / "batch_freezes.json",
    )


def _make_service(tmp_path: Path) -> tuple[ProductProcessingService, int]:
    database = create_database(f"sqlite:///{(tmp_path / 'direct.sqlite3').as_posix()}")
    service = ProductProcessingService(
        ProductProcessingRepository(database),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    draft = service.repository.create_draft(
        {
            "raw_payload_json": jsonlib.dumps(
                {
                    "source_title": "Test product",
                    "images": {"main": "https://img.example.com/main.jpg", "gallery": [], "detail": [], "sku": []},
                    "skus": [],
                    "attributes": {},
                },
                ensure_ascii=False,
            ),
            "status": "pending",
            "workspace_id": "local",
        }
    )
    task = service.repository.create_task(
        title="direct task",
        preflight_only=False,
        settings={
            "_billing": {"account_id": "acct-direct"},
            "processing_scope": [],
            "title_optimize": True,
            "description": True,
            "size": True,
            "grid_image": True,
            "detail_image": True,
        },
        drafts=[draft],
        idempotency_key=None,
        workspace_id="local",
    )
    return service, int(task["id"])


def test_execute_task_direct_freezes_grants_and_settles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, task_id = _make_service(tmp_path)
    service._task_remote_tokens[task_id] = "remote-token"
    _isolate_batch_freezes(monkeypatch, tmp_path)
    client = RecordingBatchClient()
    monkeypatch.setattr("wh_local.modules.product_processing.service._batch_billing_client", lambda: client)

    captured: dict[str, str] = {}

    def fake_impl(self, task_id: int, workspace_id: str = "local") -> dict[str, Any]:
        captured["wuyin"] = server_ai_proxy.granted_key("wuyin")
        captured["ark"] = server_ai_proxy.granted_key("ark")
        captured["freeze"] = server_ai_proxy.batch_freeze_id()
        return {"status": "completed", "total_count": 1, "success_count": 1, "failed_count": 0}

    monkeypatch.setattr(ProductProcessingService, "_execute_task_impl", fake_impl)

    try:
        result = service._execute_task(task_id, "local")
    finally:
        service.repository.database.dispose()

    assert result["status"] == "completed"
    assert len(client.freeze_calls) == 1
    assert client.freeze_calls[0][0] == "remote-token"
    assert client.freeze_calls[0][1]["link_count"] == 1
    # 冻结批次携带任务号：服务端据此在消费流水/后台对账中关联处理任务
    assert client.freeze_calls[0][1]["task_id"] == str(task_id)
    # 直连密钥在任务执行上下文内可见（线程继承）
    assert captured == {"wuyin": "W-KEY", "ark": "A-KEY", "freeze": "fz-direct-1"}
    # 任务结束自动结算并携带子项明细
    assert len(client.settle_calls) == 1
    freeze_id, payload = client.settle_calls[0][1], client.settle_calls[0][2]
    assert freeze_id == "fz-direct-1"
    assert isinstance(payload["items"], list)
    assert payload["items"][0]["subitems"][0]["feature"] == "title"
    # 冻结记录被标记为已结算
    assert batch_billing_module.open_freezes_for_account("acct-direct") == []


def test_execute_task_direct_ignores_legacy_env_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """直连已写死：遗留开关 WH_PRODUCT_AI_DIRECT=0 不再生效，仍走冻结直连。"""
    monkeypatch.setenv("WH_PRODUCT_AI_DIRECT", "0")
    service, task_id = _make_service(tmp_path)
    service._task_remote_tokens[task_id] = "remote-token"
    _isolate_batch_freezes(monkeypatch, tmp_path)
    client = RecordingBatchClient()
    monkeypatch.setattr("wh_local.modules.product_processing.service._batch_billing_client", lambda: client)
    monkeypatch.setattr(
        ProductProcessingService,
        "_execute_task_impl",
        lambda self, task_id, workspace_id="local": {"status": "completed"},
    )
    try:
        service._execute_task(task_id, "local")
    finally:
        service.repository.database.dispose()
    assert len(client.freeze_calls) == 1
    assert len(client.settle_calls) == 1


def test_execute_task_without_remote_token_stops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无计费会话 token 时无法冻结直连批次 → 直接中止（不回落到托管网关）。"""
    from wh_local.modules.product_processing.service import ProductProcessingValidationError

    service, task_id = _make_service(tmp_path)
    _isolate_batch_freezes(monkeypatch, tmp_path)
    client = RecordingBatchClient()
    monkeypatch.setattr("wh_local.modules.product_processing.service._batch_billing_client", lambda: client)
    try:
        with pytest.raises(ProductProcessingValidationError):
            service._execute_task(task_id, "local")
    finally:
        service.repository.database.dispose()
    assert client.freeze_calls == []


def test_reconcile_open_batches_settles_pending_freezes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, task_id = _make_service(tmp_path)
    _isolate_batch_freezes(monkeypatch, tmp_path)
    client = RecordingBatchClient()
    monkeypatch.setattr("wh_local.modules.product_processing.service._batch_billing_client", lambda: client)
    # 任务必须到达终态后对账才会按其真实结果结算（未完成批次禁止提前退款）。
    task = service.repository.get_task(task_id, workspace_id="local")
    item_id = int(task["items"][0]["item_id"])
    service.repository.finish_task(
        task_id,
        [{"item_id": item_id, "status": "completed"}],
        output_file="",
        error_report_file="",
        video_manifest_file="",
        workspace_id="local",
    )
    batch_billing_module.remember_freeze(
        "fz-open-1",
        account_id="acct-direct",
        workspace_id="local",
        task_id=task_id,
        link_count=1,
        scope=[],
    )
    try:
        count = service.reconcile_open_batches("remote-token", account_id="acct-direct")
    finally:
        service.repository.database.dispose()
    assert count == 1
    assert client.settle_calls and client.settle_calls[0][1] == "fz-open-1"
    assert batch_billing_module.open_freezes_for_account("acct-direct") == []


def test_reconcile_open_batches_skips_still_running_tasks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """对账不得对仍在执行/暂停中的任务批次提前按全失败退款：
    任务后续成功会导致用户白拿退款且服务端幂等拒绝再次结算。"""
    service, task_id = _make_service(tmp_path)
    _isolate_batch_freezes(monkeypatch, tmp_path)
    client = RecordingBatchClient()
    monkeypatch.setattr("wh_local.modules.product_processing.service._batch_billing_client", lambda: client)
    # 新建任务 status=queued（尚未执行），冻结批次不允许被对账提前释放
    batch_billing_module.remember_freeze(
        "fz-running-1",
        account_id="acct-direct",
        workspace_id="local",
        task_id=task_id,
        link_count=1,
        scope=[],
        item_ids=[1],
    )
    # 无任务关联的历史孤儿批次：按全失败折算退款结算
    batch_billing_module.remember_freeze(
        "fz-orphan-1",
        account_id="acct-direct",
        workspace_id="local",
        task_id=0,
        link_count=1,
        scope=[],
    )
    try:
        count = service.reconcile_open_batches("remote-token", account_id="acct-direct")
    finally:
        service.repository.database.dispose()
    # 产品语义（reconcile_open_batches 注释）：task_id=0 是 combo 冻结，由
    # run_combo_direct 的 _settle_combo_freeze 按真实结果结算，对账无从得知成败
    # 硬算会误扣 → 跳过；queued 任务等终态再对账。两者都不该被碰。
    assert count == 0
    assert client.settle_calls == []
    remaining = batch_billing_module.open_freezes_for_account("acct-direct")
    assert sorted(record["freeze_id"] for record in remaining) == ["fz-orphan-1", "fz-running-1"]


class FailingSettleClient(RecordingBatchClient):
    """结算接口抛网络不可达，模拟任务结束后结算失败。"""

    def settle_batch_points(self, remote_token: str, freeze_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        from wh_local.customer.remote_client import CustomerAuthUnavailable

        raise CustomerAuthUnavailable("unavailable")


def test_settle_open_batch_failure_is_recorded_not_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """结算失败不再被静默吞掉：侧车记录失败原因/次数，供对账与后台定位。"""
    service, task_id = _make_service(tmp_path)
    _isolate_batch_freezes(monkeypatch, tmp_path)
    client = FailingSettleClient()
    monkeypatch.setattr("wh_local.modules.product_processing.service._batch_billing_client", lambda: client)
    batch_billing_module.remember_freeze(
        "fz-fail-1",
        account_id="acct-direct",
        workspace_id="local",
        task_id=task_id,
        link_count=1,
        scope=[],
        item_ids=[1],
    )
    try:
        service._settle_open_batch(task_id, "local", "remote-token", "fz-fail-1")
    finally:
        service.repository.database.dispose()
    record = batch_billing_module.open_freeze_record("fz-fail-1")
    assert record is not None
    assert record["settle_status"] == "failed"
    assert record["settle_attempts"] == 1
    assert "unavailable" in str(record.get("settle_error") or "")


class RejectingSettleClient(RecordingBatchClient):
    """结算接口抛服务端 4xx 终态拒绝（如批次已被 TTL 释放后的 409）。"""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    def settle_batch_points(self, remote_token: str, freeze_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.settle_calls.append((remote_token, freeze_id, payload))
        raise self._error


def test_settle_open_batch_voids_record_on_terminal_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """服务端 4xx 终态（批次已被 TTL 释放等）时作废记录：置 settled=True 停止
    重试并保留 void 痕迹——保留 open 会让每次任务收尾/对账都重打一轮必然失败的请求。"""
    monkeypatch.setenv("WH_PRODUCT_AI_DIRECT", "1")
    service, task_id = _make_service(tmp_path)
    _isolate_batch_freezes(monkeypatch, tmp_path)
    from wh_local.customer.contracts import CustomerAuthRejected

    client = RejectingSettleClient(
        CustomerAuthRejected(409, "batch freeze is no longer active")
    )
    monkeypatch.setattr("wh_local.modules.product_processing.service._batch_billing_client", lambda: client)
    batch_billing_module.remember_freeze(
        "fz-released-1",
        account_id="acct-direct",
        workspace_id="local",
        task_id=task_id,
        link_count=1,
        scope=[],
        item_ids=[1],
    )
    try:
        service._settle_open_batch(task_id, "local", "remote-token", "fz-released-1")
    finally:
        service.repository.database.dispose()
    assert len(client.settle_calls) == 1
    assert batch_billing_module.open_freezes_for_account("acct-direct") == []
    raw = jsonlib.loads((tmp_path / "batch_freezes.json").read_text(encoding="utf-8"))
    assert raw["fz-released-1"]["settle_status"] == "void"
    assert "409" in raw["fz-released-1"]["settle_error"]


@pytest.mark.parametrize(
    "status_code,detail",
    [(409, "batch freeze is no longer active"), (404, "batch freeze not found")],
)
def test_reconcile_voids_freeze_on_terminal_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status_code: int, detail: str
) -> None:
    """对账遇 4xx 终态 → 作废记录；下次对账不再重复打请求（修复永久重试风暴）。"""
    monkeypatch.setenv("WH_PRODUCT_AI_DIRECT", "1")
    service, task_id = _make_service(tmp_path)
    _isolate_batch_freezes(monkeypatch, tmp_path)
    from wh_local.customer.contracts import CustomerAuthRejected

    client = RejectingSettleClient(CustomerAuthRejected(status_code, detail))
    monkeypatch.setattr("wh_local.modules.product_processing.service._batch_billing_client", lambda: client)
    task = service.repository.get_task(task_id, workspace_id="local")
    item_id = int(task["items"][0]["item_id"])
    service.repository.finish_task(
        task_id,
        [{"item_id": item_id, "status": "completed"}],
        output_file="",
        error_report_file="",
        video_manifest_file="",
        workspace_id="local",
    )
    batch_billing_module.remember_freeze(
        "fz-released-2",
        account_id="acct-direct",
        workspace_id="local",
        task_id=task_id,
        link_count=1,
        scope=[],
    )
    try:
        first = service.reconcile_open_batches("remote-token", account_id="acct-direct")
        second = service.reconcile_open_batches("remote-token", account_id="acct-direct")
    finally:
        service.repository.database.dispose()
    assert first == 0 and second == 0
    # 只打了第一轮；记录已作废，第二轮不再发起请求
    assert len(client.settle_calls) == 1
    assert batch_billing_module.open_freezes_for_account("acct-direct") == []
    raw = jsonlib.loads((tmp_path / "batch_freezes.json").read_text(encoding="utf-8"))
    assert raw["fz-released-2"]["settle_status"] == "void"


def test_reconcile_keeps_freeze_open_on_network_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """网络不可达不是终态：记录保持 open，等待后续对账/服务端 TTL 兜底（回归保护）。"""
    monkeypatch.setenv("WH_PRODUCT_AI_DIRECT", "1")
    service, task_id = _make_service(tmp_path)
    _isolate_batch_freezes(monkeypatch, tmp_path)
    client = FailingSettleClient()
    monkeypatch.setattr("wh_local.modules.product_processing.service._batch_billing_client", lambda: client)
    task = service.repository.get_task(task_id, workspace_id="local")
    item_id = int(task["items"][0]["item_id"])
    service.repository.finish_task(
        task_id,
        [{"item_id": item_id, "status": "completed"}],
        output_file="",
        error_report_file="",
        video_manifest_file="",
        workspace_id="local",
    )
    batch_billing_module.remember_freeze(
        "fz-network-1",
        account_id="acct-direct",
        workspace_id="local",
        task_id=task_id,
        link_count=1,
        scope=[],
    )
    try:
        count = service.reconcile_open_batches("remote-token", account_id="acct-direct")
    finally:
        service.repository.database.dispose()
    assert count == 0
    remaining = batch_billing_module.open_freezes_for_account("acct-direct")
    assert [record["freeze_id"] for record in remaining] == ["fz-network-1"]


# ---------------------------------------------------------------------------
# 侧车文件：并发读改写 + 「读不到就不许写」语义
# ---------------------------------------------------------------------------


def _sidecar_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "product_processing" / "batch_freezes.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(batch_billing_module, "_open_freezes_path", lambda: path)
    return path


def test_sidecar_read_failure_must_not_wipe_other_freezes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """读失败时若把新内容整体写回，会抹掉其它 freeze 记录（积分滞留到 TTL）。

    必须改为抛出，绝不允许「以为是空表 → 只写自己这一条」。
    """
    path = _sidecar_path(monkeypatch, tmp_path)
    path.write_text("{ not valid json", encoding="utf-8")
    with pytest.raises(ValueError):
        batch_billing_module.remember_freeze(
            "fz-new",
            account_id="a",
            workspace_id="w",
            task_id=1,
            link_count=1,
            scope=[],
            item_ids=[1],
        )
    assert path.read_text(encoding="utf-8") == "{ not valid json"


def test_forget_freeze_read_failure_must_not_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _sidecar_path(monkeypatch, tmp_path)
    path.write_text("{ not valid json", encoding="utf-8")
    with pytest.raises(ValueError):
        batch_billing_module.forget_freeze("fz-x")
    assert path.read_text(encoding="utf-8") == "{ not valid json"


def test_mark_settle_failure_is_best_effort_and_never_wipes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """打「结算失败」标记是尽力而为：读不到就放弃打标，但绝不能写。

    它常在 except 分支里被调用，不能让「记录失败」反过来掩盖原始异常。
    """
    path = _sidecar_path(monkeypatch, tmp_path)
    path.write_text("{ not valid json", encoding="utf-8")
    batch_billing_module.mark_freeze_settle_failure("fz-x", "boom")  # 不得抛出
    assert path.read_text(encoding="utf-8") == "{ not valid json"


def test_concurrent_sidecar_updates_keep_every_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """12 个线程同时「读-改-写」：不丢记录、不抛异常、不留 .tmp。

    2026-10-09 实测：无锁 + 固定 .tmp 名的旧实现下，12 线程里 11 个抛
    PermissionError 且最终只剩 1 条记录。
    """
    import threading

    _sidecar_path(monkeypatch, tmp_path)
    errors: list[BaseException] = []
    barrier = threading.Barrier(12)

    def worker(index: int) -> None:
        try:
            barrier.wait(timeout=10)
            freeze_id = f"fz-{index}"
            batch_billing_module.remember_freeze(
                freeze_id,
                account_id=f"a{index}",
                workspace_id="w",
                task_id=index,
                link_count=1,
                scope=[],
                item_ids=[index],
            )
            if index % 2 == 0:
                batch_billing_module.mark_freeze_settle_failure(freeze_id, "boom")
            else:
                batch_billing_module.forget_freeze(freeze_id)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert errors == []
    data = batch_billing_module._load_open_freezes()
    assert set(data) == {f"fz-{i}" for i in range(12)}
    assert sum(1 for v in data.values() if v.get("settled")) == 6
    assert sum(1 for v in data.values() if v.get("settle_status") == "failed") == 6
    assert not list(tmp_path.rglob("*.tmp"))
