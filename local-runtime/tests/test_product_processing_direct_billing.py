from __future__ import annotations

import json as jsonlib
from pathlib import Path
from typing import Any

import pytest

from wh_local.modules.product_processing import batch_billing as batch_billing_module
from wh_local.modules.product_processing import provider_config as provider_config_module
from wh_local.modules.product_processing import server_ai_proxy
from wh_local.modules.product_processing.doubao_ark import DoubaoArkClient
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
    assert recorded["json"]["model"] == "doubao-seed-2-0-mini-260428"


def test_doubao_ark_gateway_fallback_without_granted_key(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    recorded: dict[str, Any] = {}

    def fake_post(url: str, *, headers: dict[str, str], json: dict[str, Any], **_: Any):
        recorded["url"] = url
        recorded["auth"] = headers.get("Authorization")
        recorded["json"] = json
        response = requests.Response()
        response.status_code = 200
        response._content = jsonlib.dumps({"choices": [{"message": {"content": "reply"}}]}).encode("utf-8")
        return response

    monkeypatch.setattr("wh_local.modules.product_processing.doubao_ark._HTTP_SESSION.post", fake_post)
    with server_ai_proxy.server_ai_context("token-1", {"text": "usage-1"}):
        client = DoubaoArkClient()
        assert client.direct is False
        client.complete([{"role": "user", "content": "hi"}])
    assert "gateway" not in recorded["url"]
    assert recorded["auth"] == "Bearer token-1"
    assert "usage_id" in recorded["json"]


def test_provider_config_direct_image_section_with_granted_wuyin_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with server_ai_proxy.server_ai_context("t", {}, granted_keys={"wuyin": "WUYIN-KEY"}):
        provider = provider_config_module.resolve_ai_provider()
        assert provider["direct_mode"] is True
        sys_image = provider["_sys_image_ai"]
        assert sys_image["base_url"] == "https://api.wuyinkeji.com"
        assert sys_image["api_key"] == "WUYIN-KEY"


def test_provider_config_stays_server_managed_without_granted_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with server_ai_proxy.server_ai_context("t", {}):
        provider = provider_config_module.resolve_ai_provider()
        assert provider["direct_mode"] is False
        sys_image = provider["_sys_image_ai"]
        assert sys_image["base_url"] == "server-managed-wuyin"
        assert sys_image["api_key"] == "server-managed"


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
    monkeypatch.setenv("WH_PRODUCT_AI_DIRECT", "1")
    service, task_id = _make_service(tmp_path)
    service._task_remote_tokens[task_id] = "remote-token"
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


def test_execute_task_legacy_path_skips_freeze_when_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WH_PRODUCT_AI_DIRECT", "0")
    service, task_id = _make_service(tmp_path)
    service._task_remote_tokens[task_id] = "remote-token"
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
    assert client.freeze_calls == []
    assert client.settle_calls == []


def test_reconcile_open_batches_settles_pending_freezes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WH_PRODUCT_AI_DIRECT", "1")
    service, task_id = _make_service(tmp_path)
    client = RecordingBatchClient()
    monkeypatch.setattr("wh_local.modules.product_processing.service._batch_billing_client", lambda: client)
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
