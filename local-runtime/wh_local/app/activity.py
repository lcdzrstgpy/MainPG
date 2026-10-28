"""全局任务进度总览。

顶部标签栏的「进度」按钮靠这个接口拿数据：

- ``items``：各模块**正在跑**的任务，空 = 没有在跑的东西（按钮不出现）；
- ``finished``：最近结束的几条（含最终状态），供前端判断「刚完成的那几项」，
  用来在跑完之后把按钮切成「✓ 成功 N 项」而不是立刻消失。

设计约定：
- 只报「正在跑」的（queued / running 之类），暂停中、等结算的不算；
- 每个模块独立 try/except：某个模块查询失败不能把整块结果打掉，
  也不能因为一个模块的异常让按钮消失（宁可少报一项）；
- ``done`` / ``total`` 拿不准的模块允许 null，前端按「不确定进度」显示。
"""

from __future__ import annotations

import logging

from typing import Any, Callable

from fastapi import APIRouter, Depends

from ..session import Actor, actor_from_authorization

logger = logging.getLogger(__name__)

# POD 批次里算「正在跑」的状态。paused / pausing 是用户主动停的，
# settlement_pending 是等结算的，都不算在跑。
_POD_ACTIVE_STATUSES = frozenset(
    {
        "queued",
        "generating_pattern",
        "generating_patterns",
        "compositing",
        "optimizing_scene",
        "generating_titles",
    }
)

# 两边共用的「已结束」状态。partial_failure 也算结束，只是不算全成功。
_TERMINAL_STATUSES = frozenset({"completed", "failed", "partial_failure", "cancelled"})

# 给 finished 用的回溯条数：只用来匹配「刚刚从 items 里消失的那几项」，
# 前端拿不到匹配项就自然忽略，所以多给几条不影响正确性。
_FINISHED_LIMIT = 8


def _progress_item(
    *,
    module: str,
    module_label: str,
    key: str,
    title: str,
    status: str,
    done: int | None,
    total: int | None,
    updated_at: str,
) -> dict[str, Any]:
    return {
        "module": module,
        "moduleLabel": module_label,
        "key": key,
        "title": title,
        "status": status,
        "done": done,
        "total": total,
        "updatedAt": updated_at,
    }


def _finished_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": item["key"],
        "module": item["module"],
        "title": item["title"],
        "status": item["status"],
        "ok": item["status"] == "completed",
    }


# ---------------------------------------------------------------- 产品处理


def _product_processing_items(service: Any, workspace_id: str) -> list[dict[str, Any]]:
    """产品处理（AI 处理）任务：total_count / success+failed+skipped 直接可算进度。"""
    items: list[dict[str, Any]] = []
    for task in service.repository.active_tasks(workspace_id):
        task_id = task.get("id")
        total = int(task.get("total_count") or 0)
        done = sum(
            int(task.get(key) or 0)
            for key in ("success_count", "failed_count", "skipped_count")
        )
        items.append(
            _progress_item(
                module="product_processing",
                module_label="AI 处理",
                key=f"pp:{task_id}",
                title=str(task.get("title") or f"任务 {task_id}"),
                status=str(task.get("status") or ""),
                # 计数偶尔会因为重试略微越界，钳一下避免前端进度条跑到 100% 以上。
                done=min(done, total) if total else done,
                total=total,
                updated_at=str(task.get("updated_at") or ""),
            )
        )
    return items


def _product_processing_finished(service: Any, workspace_id: str) -> list[dict[str, Any]]:
    tasks, _ = service.repository.list_tasks(_FINISHED_LIMIT, workspace_id)
    finished: list[dict[str, Any]] = []
    for task in tasks:
        status = str(task.get("status") or "")
        if status not in _TERMINAL_STATUSES:
            continue
        task_id = task.get("id")
        finished.append(
            _finished_item(
                {
                    "key": f"pp:{task_id}",
                    "module": "product_processing",
                    "title": str(task.get("title") or f"任务 {task_id}"),
                    "status": status,
                }
            )
        )
    return finished


# ---------------------------------------------------------------- POD


def _pod_batch_rows(service: Any, workspace_id: str, owner_user_id: str) -> list[dict[str, Any]]:
    """一次性取回近期批次，items 与 finished 共用，避免重复查一遍 N+1。"""
    rows, _ = service.repository.list_batches(
        workspace_id,
        owner_user_id,
        limit=50,
        offset=0,
    )
    return rows


def _pod_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """POD 各批次（全定制 / 半定制 / 复刻 / 裂变）：按「款」算进度。"""
    items: list[dict[str, Any]] = []
    for batch in rows:
        status = str(batch.get("status") or "")
        if status not in _POD_ACTIVE_STATUSES:
            continue
        batch_id = str(batch.get("batch_id") or "")
        total = int(batch.get("requested_count") or 0)
        done = int(batch.get("processed_count") or 0)
        items.append(
            _progress_item(
                module="pod_customization",
                module_label="POD 定制",
                key=f"pod:{batch_id}",
                title=str(batch.get("title") or batch_id),
                status=status,
                done=min(done, total) if total else done,
                total=total,
                updated_at=str(batch.get("updated_at") or batch.get("created_at") or ""),
            )
        )
    return items


def _pod_finished(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    finished: list[dict[str, Any]] = []
    for batch in rows:
        status = str(batch.get("status") or "")
        if status not in _TERMINAL_STATUSES:
            continue
        batch_id = str(batch.get("batch_id") or "")
        finished.append(
            _finished_item(
                {
                    "key": f"pod:{batch_id}",
                    "module": "pod_customization",
                    "title": str(batch.get("title") or batch_id),
                    "status": status,
                }
            )
        )
        if len(finished) >= _FINISHED_LIMIT:
            break
    return finished


# ---------------------------------------------------------------- 组合套装


def _combo_kit_items(service: Any, workspace_id: str) -> list[dict[str, Any]]:
    """组合套装：任务按 (套装, 类型) 维度存在，没有可靠的 done/total，只报状态。"""
    type_labels = {"text": "文案生成", "image": "生图"}
    items: list[dict[str, Any]] = []
    for task in service.repository.list_active_tasks():
        task_workspace = str(task.get("workspace_id") or "")
        if task_workspace and task_workspace != workspace_id:
            continue
        set_id = str(task.get("set_id") or "")
        task_type = str(task.get("task_type") or "")
        # 这张表没有套装名称也没有 title，用「套装短号 · 任务类型」表达，
        # 比裸 uuid 好认；点进去还是看得到完整套装。
        short_id = set_id[:8] if set_id else "套装"
        items.append(
            _progress_item(
                module="combo_kit",
                module_label="商品组合套装",
                key=f"combo:{set_id}:{task_type}",
                title=f"{short_id} · {type_labels.get(task_type, task_type or '任务')}",
                status=str(task.get("status") or ""),
                done=None,
                total=None,
                updated_at=str(task.get("started_at") or task.get("created_at") or ""),
            )
        )
    return items


# ---------------------------------------------------------------- 路由


def create_activity_router(
    *,
    product_processing_service: Callable[[], Any],
    pod_service: Callable[[], Any],
    combo_kit_service: Callable[[], Any],
) -> APIRouter:
    router = APIRouter(prefix="/api/tasks", tags=["activity"])

    @router.get("/active-overview")
    def active_overview(actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        """正在处理的任务 + 最近结束的任务。items 为空 = 没有在跑的东西。"""
        workspace_id = actor.workspace_id
        items: list[dict[str, Any]] = []
        finished: list[dict[str, Any]] = []

        # 产品处理
        try:
            service = product_processing_service()
            items.extend(_product_processing_items(service, workspace_id))
            finished.extend(_product_processing_finished(service, workspace_id))
        except Exception:  # noqa: BLE001 - 单个模块查不到不影响其它模块
            logger.warning("activity overview: product_processing 查询失败", exc_info=True)

        # POD：批次列表只查一次，活跃项与刚结束项共用
        try:
            service = pod_service()
            rows = _pod_batch_rows(service, workspace_id, actor.id)
            items.extend(_pod_items(rows))
            finished.extend(_pod_finished(rows))
        except Exception:  # noqa: BLE001
            logger.warning("activity overview: pod_customization 查询失败", exc_info=True)

        # 组合套装
        try:
            items.extend(_combo_kit_items(combo_kit_service(), workspace_id))
        except Exception:  # noqa: BLE001
            logger.warning("activity overview: combo_kit 查询失败", exc_info=True)

        return {"count": len(items), "items": items, "finished": finished}

    return router
