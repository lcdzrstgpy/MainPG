"""Taobao adaptation for the browser-plugin capture interface.

The browser extension still scans 1688 list pages; the backend capture service
must accept (future) Taobao/Tmall item links as well, resolve the platform from
the URL, build a Taobao provider, and normalize candidates as ``taobao:``.
No real network access is performed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter

from wh_local.data_collection.plugin_onebound_capture import (
    PluginOneBoundCaptureDependencies,
    register_plugin_onebound_capture_routes,
)
from wh_local.data_collection.plugin_queue import DataCollectionPluginQueue


class _Budget:
    def reserve(self, *, workspace_id: str, provider_fingerprint: str, max_api_calls: int, api_calls: int = 1):
        return {"reservation_granted": True}


class _Provider:
    def __init__(self) -> None:
        self.calls = 0

    def get_item_detail(self, offer_id: str):
        self.calls += 1
        return type(
            "Result",
            (),
            {
                "ok": True,
                "response": {
                    "item": {
                        "num_iid": offer_id,
                        "title": "淘宝商品",
                        "price": "19.9",
                        "seller_id": "2174893850",
                        "shop_id": "112790207",
                        "seller_info": {"nick": "吉百居家居旗舰店", "shop_name": "吉百居家居旗舰店"},
                        "detail_url": f"https://item.taobao.com/item.htm?id={offer_id}",
                    }
                },
                "audit": None,
            },
        )()


class _Drafts:
    def __init__(self) -> None:
        self.by_candidate: dict[str, dict] = {}
        self.intakes: list[dict] = []

    @property
    def repository(self):
        return self

    def draft_by_candidate(self, candidate_id: str, workspace_id: str):
        return self.by_candidate.get(candidate_id)

    def intake_shop_candidate(self, *, batch_id: str, workspace_id: str, candidate: dict, **kwargs: object):
        self.intakes.append({"batch_id": batch_id, "workspace_id": workspace_id, "candidate": candidate, **kwargs})
        draft = {"id": len(self.intakes), "status": "draft", "candidate_id": candidate["candidate_id"]}
        self.by_candidate[candidate["candidate_id"]] = draft
        return {"action": "created", "draft": draft}

    @property
    def media_assets(self):
        return self

    def materialize_until_idle(self, *, workspace_id: str):
        return {"materialized": 0}


def _service(tmp_path: Path) -> tuple[Any, Any, _Provider, _Drafts]:
    from wh_local.data_collection.plugin_onebound_capture import (
        PluginOneBoundCaptureService,
    )

    queue = DataCollectionPluginQueue(tmp_path / "runtime.sqlite3")
    session = queue.create_session(actor_id="actor-1", workspace_id="workspace-1")
    provider = _Provider()
    drafts = _Drafts()
    seen_configs: list[dict] = []

    def factory(config: dict) -> _Provider:
        seen_configs.append(dict(config))
        return provider

    router = APIRouter()
    service = register_plugin_onebound_capture_routes(
        router,
        PluginOneBoundCaptureDependencies(
            plugin_queue=queue,
            provider_config_resolver=lambda _actor: {
                "api_key": "key", "api_secret": "secret", "base_url": "https://api-gw.onebound.cn/1688",
            },
            provider_factory=factory,
            budget=_Budget(),
            draft_writer=drafts,
        ),
    )
    assert isinstance(service, PluginOneBoundCaptureService)
    return service, session, provider, drafts, seen_configs


def test_taobao_prepare_detects_platform_and_builds_taobao_provider(tmp_path: Path) -> None:
    service, session, provider, drafts, seen_configs = _service(tmp_path)
    token = session["session_token"]
    taobao_link = "https://item.taobao.com/item.htm?id=831569268224"

    prepared = service.prepare(
        session_token=token,
        page_url=taobao_link,
        source_urls=[taobao_link],
    )
    service.start(session_token=token, batch_token=prepared["batch_token"])

    batch = service._batches[prepared["batch_token"]]
    assert batch.platform == "taobao"
    item_url = next(iter(batch.items.values())).source_url
    assert item_url == "https://item.taobao.com/item.htm?id=831569268224"
    # 平台化 provider：解析器默认 1688 配置，启动时须转换为淘宝 base_url。
    assert seen_configs[0]["base_url"] == "https://api-gw.onebound.cn/taobao"

    response = service.item(
        session_token=token,
        batch_token=prepared["batch_token"],
        source_url=taobao_link,
    )
    assert response["outcome"] == "succeeded"
    candidate = service._batches[prepared["batch_token"]].items["831569268224"].candidate
    assert candidate["candidate_id"] == "taobao:831569268224"
    assert candidate["source_platform"] == "taobao"
    assert candidate["source_url"] == "https://item.taobao.com/item.htm?id=831569268224"
    assert provider.calls == 1
    assert drafts.intakes == []


def test_taobao_existing_draft_dedupe_uses_platform_prefix(tmp_path: Path) -> None:
    service, session, _provider, drafts, _seen = _service(tmp_path)
    token = session["session_token"]
    # 预置一条已入池的淘宝草稿（candidate_id 以 taobao: 开头）。
    drafts.by_candidate["taobao:1065914843343"] = {"status": "draft", "source_type": "onebound_api"}

    prepared = service.prepare(
        session_token=token,
        page_url="https://item.taobao.com/item.htm?id=1065914843343",
        source_urls=["https://item.taobao.com/item.htm?id=1065914843343"],
    )

    assert prepared["total_count"] == 1
    assert prepared["existing_count"] == 1
    assert prepared["pending_count"] == 0


def test_1688_prepare_still_defaults_to_1688_platform(tmp_path: Path) -> None:
    service, session, _provider, _drafts, seen_configs = _service(tmp_path)
    token = session["session_token"]
    link = "https://detail.1688.com/offer/12345678.html"

    prepared = service.prepare(
        session_token=token,
        page_url=link,
        source_urls=[link],
    )
    service.start(session_token=token, batch_token=prepared["batch_token"])

    assert service._batches[prepared["batch_token"]].platform == "1688"
    assert seen_configs[0]["base_url"] == "https://api-gw.onebound.cn/1688"


def _failing_provider(
    upstream_error: str | None = None,
    provider_code: str = "upstream_failed",
    error_context: dict[str, Any] | None = None,
):
    class _FailingProvider:
        def __init__(self) -> None:
            self.calls = 0

        def get_item_detail(self, offer_id: str):
            self.calls += 1
            response: dict[str, Any] = {}
            if upstream_error:
                response["error"] = upstream_error
            return type(
                "Result",
                (),
                {
                    "ok": False,
                    "response": response,
                    "error": type(
                        "Error",
                        (),
                        {
                            "code": provider_code,
                            "message": "OneBound returned an unsuccessful response",
                            "context": dict(error_context or {}),
                        },
                    )(),
                },
            )()

    return _FailingProvider()


def test_item_failure_keeps_item_not_found_diagnostic(tmp_path: Path) -> None:
    """万邦 item-not-found 失败须保留具体原因，而不是笼统的采集失败。"""
    from wh_local.data_collection.plugin_onebound_capture import (
        PluginOneBoundCaptureService,
    )

    queue = DataCollectionPluginQueue(tmp_path / "runtime.sqlite3")
    session = queue.create_session(actor_id="actor-1", workspace_id="workspace-1")
    provider = _failing_provider(upstream_error="item-not-found", provider_code="upstream_failed")

    router = APIRouter()
    service = register_plugin_onebound_capture_routes(
        router,
        PluginOneBoundCaptureDependencies(
            plugin_queue=queue,
            provider_config_resolver=lambda _actor: {
                "api_key": "key", "api_secret": "secret", "base_url": "https://api-gw.onebound.cn/1688",
            },
            provider_factory=lambda _config: provider,
            budget=_Budget(),
            draft_writer=_Drafts(),
        ),
    )
    assert isinstance(service, PluginOneBoundCaptureService)
    token = session["session_token"]
    link = "https://item.taobao.com/item.htm?id=1072399809675"

    prepared = service.prepare(session_token=token, page_url=link, source_urls=[link])
    service.start(session_token=token, batch_token=prepared["batch_token"])
    response = service.item(session_token=token, batch_token=prepared["batch_token"], source_url=link)

    assert response["ok"] is False
    assert response["error_code"] == "item-not-found"
    assert "商品不存在或已下架" in response["message"]
    assert provider.calls == 1


def test_item_failure_without_mapped_code_keeps_provider_code_and_generic_reason(tmp_path: Path) -> None:
    """未知上游错误码：保留稳定的 provider 错误码，中文原因走兜底文案。"""
    from wh_local.data_collection.plugin_onebound_capture import (
        PluginOneBoundCaptureService,
    )

    queue = DataCollectionPluginQueue(tmp_path / "runtime.sqlite3")
    session = queue.create_session(actor_id="actor-1", workspace_id="workspace-1")
    provider = _failing_provider(upstream_error="strange-upstream-code-xyz", provider_code="upstream_failed")

    router = APIRouter()
    service = register_plugin_onebound_capture_routes(
        router,
        PluginOneBoundCaptureDependencies(
            plugin_queue=queue,
            provider_config_resolver=lambda _actor: {
                "api_key": "key", "api_secret": "secret", "base_url": "https://api-gw.onebound.cn/1688",
            },
            provider_factory=lambda _config: provider,
            budget=_Budget(),
            draft_writer=_Drafts(),
        ),
    )
    assert isinstance(service, PluginOneBoundCaptureService)
    token = session["session_token"]
    link = "https://item.taobao.com/item.htm?id=1072399809675"

    prepared = service.prepare(session_token=token, page_url=link, source_urls=[link])
    service.start(session_token=token, batch_token=prepared["batch_token"])
    response = service.item(session_token=token, batch_token=prepared["batch_token"], source_url=link)

    assert response["ok"] is False
    assert response["error_code"] == "upstream_failed"
    assert response["message"] == "万邦接口返回失败"


def test_item_failure_surfaces_onebound_error_code_instead_of_generic_reason(tmp_path: Path) -> None:
    """上游 item_get 返回 error_code=5000 时，须展示真实错误码而非笼统的'万邦接口返回失败'。"""
    from wh_local.data_collection.plugin_onebound_capture import (
        PluginOneBoundCaptureService,
    )

    queue = DataCollectionPluginQueue(tmp_path / "runtime.sqlite3")
    session = queue.create_session(actor_id="actor-1", workspace_id="workspace-1")
    provider = _failing_provider(
        upstream_error="data",
        provider_code="upstream_failed",
        error_context={"upstream_code": "5000", "upstream_reason": "data error"},
    )

    router = APIRouter()
    service = register_plugin_onebound_capture_routes(
        router,
        PluginOneBoundCaptureDependencies(
            plugin_queue=queue,
            provider_config_resolver=lambda _actor: {
                "api_key": "key", "api_secret": "secret", "base_url": "https://api-gw.onebound.cn/1688",
            },
            provider_factory=lambda _config: provider,
            budget=_Budget(),
            draft_writer=_Drafts(),
        ),
    )
    assert isinstance(service, PluginOneBoundCaptureService)
    token = session["session_token"]
    link = "https://item.taobao.com/item.htm?id=1072399809675"

    prepared = service.prepare(session_token=token, page_url=link, source_urls=[link])
    service.start(session_token=token, batch_token=prepared["batch_token"])
    response = service.item(session_token=token, batch_token=prepared["batch_token"], source_url=link)

    assert response["ok"] is False
    assert response["error_code"] == "upstream_failed"
    assert "error_code: 5000" in response["message"]
    assert "万邦接口返回失败" not in response["message"]
