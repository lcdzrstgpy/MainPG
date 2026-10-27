"""全局任务进度总览（/api/tasks/active-overview）的字段映射与容错。

三类任务的数据源结构完全不同，映射错了不会报错、只会静默显示空或错进度，
所以这里对每种来源都钉住字段约定；另外钉住「跑完了要能报成功」这条路径。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from wh_local.app.activity import (
    _combo_kit_items,
    _pod_finished,
    _pod_items,
    _product_processing_finished,
    _product_processing_items,
    create_activity_router,
)
from wh_local.session import Actor, actor_from_authorization


def _service(**kwargs: Any) -> Any:
    class _S:
        pass

    service = _S()
    for key, value in kwargs.items():
        setattr(service, key, value)
    return service


class _Repo:
    def __init__(self, **methods: Any) -> None:
        for key, value in methods.items():
            setattr(self, key, value)


def _client(**services: Any) -> TestClient:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(
        create_activity_router(
            product_processing_service=lambda: services.get("pp"),
            pod_service=lambda: services.get("pod"),
            combo_kit_service=lambda: services.get("combo"),
        )
    )
    app.dependency_overrides[actor_from_authorization] = lambda: Actor(
        id="user-1", username="u", role="operator", workspace_id="ws-1"
    )
    return TestClient(app)


def _repo(active: list[dict[str, Any]] | None = None, history: list[dict[str, Any]] | None = None) -> _Repo:
    return _Repo(
        active_tasks=lambda workspace_id: active or [],
        list_tasks=lambda limit, workspace_id: (history or [], len(history or [])),
    )


# ---------- 产品处理 ----------


def test_product_processing_maps_counts_to_done_total() -> None:
    repo = _repo(
        active=[
            {
                "id": 7,
                "title": "草稿批次 A",
                "status": "running",
                "total_count": 10,
                "success_count": 4,
                "failed_count": 1,
                "skipped_count": 0,
                "updated_at": "2026-10-23 15:00:00",
            }
        ]
    )
    items = _product_processing_items(_service(repository=repo), "ws-1")

    assert len(items) == 1
    item = items[0]
    assert item["module"] == "product_processing"
    assert item["moduleLabel"] == "AI 处理"
    assert item["key"] == "pp:7"
    assert item["title"] == "草稿批次 A"
    assert (item["done"], item["total"]) == (5, 10)


def test_product_processing_clamps_over_counted_progress() -> None:
    """重试会让 success+failed 略微超过 total，进度不能跑过 100%。"""
    repo = _repo(
        active=[
            {
                "id": 8,
                "title": "重试批次",
                "status": "running",
                "total_count": 3,
                "success_count": 3,
                "failed_count": 2,
                "skipped_count": 0,
            }
        ]
    )
    item = _product_processing_items(_service(repository=repo), "ws-1")[0]
    assert item["done"] == 3
    assert item["total"] == 3


def test_product_processing_finished_marks_only_completed_as_ok() -> None:
    repo = _repo(
        history=[
            {"id": 11, "title": "全成功", "status": "completed"},
            {"id": 12, "title": "部分失败", "status": "partial_failure"},
            {"id": 13, "title": "取消", "status": "cancelled"},
            {"id": 14, "title": "还在跑", "status": "running"},
        ]
    )
    finished = _product_processing_finished(_service(repository=repo), "ws-1")

    assert [f["key"] for f in finished] == ["pp:11", "pp:12", "pp:13"]
    assert [f["ok"] for f in finished] == [True, False, False]


# ---------- POD ----------


def test_pod_only_reports_actively_running_batches() -> None:
    rows = [
        {"batch_id": "b1", "title": "跑着的", "status": "generating_patterns", "requested_count": 4, "processed_count": 1},
        {"batch_id": "b2", "title": "暂停中", "status": "paused", "requested_count": 4, "processed_count": 2},
        {"batch_id": "b3", "title": "已完成", "status": "completed", "requested_count": 4, "processed_count": 4},
    ]
    items = _pod_items(rows)

    assert [item["key"] for item in items] == ["pod:b1"]
    assert (items[0]["done"], items[0]["total"]) == (1, 4)
    assert items[0]["module"] == "pod_customization"


def test_pod_falls_back_to_created_at_when_updated_at_missing() -> None:
    rows = [
        {
            "batch_id": "b9",
            "title": "无 updated_at",
            "status": "queued",
            "requested_count": 2,
            "processed_count": 0,
            "created_at": "2026-10-23 15:10:00",
        }
    ]
    item = _pod_items(rows)[0]
    assert item["updatedAt"] == "2026-10-23 15:10:00"


def test_pod_finished_picks_terminal_batches() -> None:
    rows = [
        {"batch_id": "b1", "title": "跑着", "status": "compositing"},
        {"batch_id": "b2", "title": "好了", "status": "completed"},
        {"batch_id": "b3", "title": "部分失败", "status": "partial_failure"},
    ]
    finished = _pod_finished(rows)

    assert [f["key"] for f in finished] == ["pod:b2", "pod:b3"]
    assert [f["ok"] for f in finished] == [True, False]


# ---------- 组合套装 ----------


def test_combo_kit_filters_other_workspaces_and_has_no_progress() -> None:
    repo = _Repo(
        list_active_tasks=lambda: [
            {"set_id": "abcdef123456", "task_type": "image", "status": "running", "workspace_id": "ws-1"},
            {"set_id": "zzz999", "task_type": "text", "status": "running", "workspace_id": "ws-2"},
        ]
    )
    items = _combo_kit_items(_service(repository=repo), "ws-1")

    assert len(items) == 1
    assert items[0]["title"] == "abcdef12 · 生图"
    assert items[0]["done"] is None and items[0]["total"] is None


# ---------- 接口层 ----------


def test_endpoint_returns_empty_when_nothing_running() -> None:
    pp = _service(repository=_repo())
    pod = _service(repository=_Repo(list_batches=lambda *a, **k: ([], 0)))
    combo = _service(repository=_Repo(list_active_tasks=lambda: []))

    response = _client(pp=pp, pod=pod, combo=combo).get("/api/tasks/active-overview")

    assert response.status_code == 200
    assert response.json() == {"count": 0, "items": [], "finished": []}


def test_endpoint_exposes_finished_so_button_can_report_success() -> None:
    pp = _service(repository=_repo(history=[{"id": 3, "title": "跑完了", "status": "completed"}]))
    pod = _service(repository=_Repo(list_batches=lambda *a, **k: ([], 0)))
    combo = _service(repository=_Repo(list_active_tasks=lambda: []))

    payload = _client(pp=pp, pod=pod, combo=combo).get("/api/tasks/active-overview").json()

    assert payload["count"] == 0
    assert payload["finished"] == [
        {"key": "pp:3", "module": "product_processing", "title": "跑完了", "status": "completed", "ok": True}
    ]


def test_endpoint_survives_one_module_failing() -> None:
    """单个模块查询炸了不能把整块结果打掉，也不能让按钮消失。"""

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("pod 库挂了")

    pp = _service(
        repository=_repo(
            active=[
                {"id": 1, "title": "还在跑", "status": "running", "total_count": 2, "success_count": 1, "failed_count": 0, "skipped_count": 0}
            ]
        )
    )
    pod = _service(repository=_Repo(list_batches=boom))
    combo = _service(repository=_Repo(list_active_tasks=boom))

    payload = _client(pp=pp, pod=pod, combo=combo).get("/api/tasks/active-overview").json()

    assert payload["count"] == 1
    assert payload["items"][0]["key"] == "pp:1"


def test_endpoint_handles_missing_module_instances() -> None:
    """app.state 上拿不到实例（模块未装配）时只报能报的，不 500。"""

    response = _client().get("/api/tasks/active-overview")

    assert response.status_code == 200
    assert response.json()["count"] == 0
