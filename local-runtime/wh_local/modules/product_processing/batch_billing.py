"""Client-side batch freeze/settle lifecycle for direct AI processing.

灰度直连模式下，任务开始时调用服务端 ``/batch/freeze`` 冻结 N×45 积分并领取
短期密钥，客户端直连第三方处理；任务结束时 ``/batch/settle`` 按子项状态结算。
侧车文件只记录 freeze_id 等元信息（不落任何 token / 密钥），供启动对账识别
未结算批次——结算必须携带当时会话 token，由调用方提供。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...config import default_config
from ...customer.remote_client import CustomerAuthClient

ENV_DIRECT = "WH_PRODUCT_AI_DIRECT"

# 与 auth-api 的 billing_pricing_items.feature_key 对齐（顺序即展示顺序）。
SUBITEM_FEATURES = ("title", "description", "product_dimensions", "four_grid", "detail_images")


def direct_ai_enabled() -> bool:
    """灰度开关：WH_PRODUCT_AI_DIRECT=1 时任务走批次冻结 + 客户端直连。"""
    return os.environ.get(ENV_DIRECT, "").strip().lower() in {"1", "true", "yes"}


def billing_client() -> CustomerAuthClient:
    return CustomerAuthClient(default_config().customer_auth_base_url, timeout_seconds=20)


def _open_freezes_path() -> Path:
    return Path(default_config().data_dir) / "product_processing" / "batch_freezes.json"


def _load_open_freezes() -> dict[str, Any]:
    path = _open_freezes_path()
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}
    return {}


def _save_open_freezes(data: dict[str, Any]) -> None:
    path = _open_freezes_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def remember_freeze(
    freeze_id: str,
    *,
    account_id: str,
    workspace_id: str,
    task_id: int,
    link_count: int,
    scope: list[str],
    item_ids: list[int] | None = None,
) -> None:
    """Record an open batch freeze (metadata only, never tokens or keys).

    ``item_ids`` 记录冻结时刻的 pending 商品条目；结算时按它过滤上报明细，
    保证与冻结的 link_count 严格一致（重试/混合状态任务不会多报）。
    """
    data = _load_open_freezes()
    data[freeze_id] = {
        "account_id": account_id,
        "workspace_id": workspace_id,
        "task_id": int(task_id),
        "link_count": int(link_count),
        "scope": [str(item) for item in (scope or [])],
        "item_ids": [int(item_id) for item_id in (item_ids or [])],
        "opened_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "settled": False,
    }
    _save_open_freezes(data)


def forget_freeze(freeze_id: str) -> None:
    data = _load_open_freezes()
    if freeze_id in data:
        data[freeze_id]["settled"] = True
        _save_open_freezes(data)


def open_freezes_for_account(account_id: str) -> list[dict[str, Any]]:
    return [
        {**record, "freeze_id": freeze_id}
        for freeze_id, record in _load_open_freezes().items()
        if str(record.get("account_id") or "") == str(account_id) and not record.get("settled")
    ]


def open_freeze_record(freeze_id: str) -> dict[str, Any] | None:
    """Return one still-open freeze record (or None when settled/unknown)."""
    record = _load_open_freezes().get(str(freeze_id))
    if record is None or record.get("settled"):
        return None
    return {**record, "freeze_id": str(freeze_id)}


def _enabled_features(settings: dict[str, Any]) -> list[str]:
    scope = set(settings.get("processing_scope") or [])
    enabled: list[str] = []
    if {"title", "details", "product_dimensions"} & scope or settings.get("title_optimize", True):
        enabled.append("title")
    if {"title", "details", "product_dimensions"} & scope or settings.get("description", True):
        enabled.append("description")
    if "product_dimensions" in scope or settings.get("size", True):
        enabled.append("product_dimensions")
    if "four_grid" in scope or settings.get("grid_image", True) or settings.get("image_rewrite", True):
        enabled.append("four_grid")
    if settings.get("detail_image", True) or "detail_images" in scope:
        enabled.append("detail_images")
    return [feature for feature in SUBITEM_FEATURES if feature in enabled]


def derive_item_results(
    task_items: list[dict[str, Any]],
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    """按任务结果把每条链接折算成子项状态上报。

    状态契约（与 auth-api compute_batch_charge 一致）：
    - success    成功，扣全价
    - intercept  有返回但质量门拦截，退半价
    - no_return  上游无返回/整条失败，全退
    一期保守策略：completed 链接全部 success；失败链接全部 no_return
    （拦截退半的细分留到 ai_notes 里带 quality-gate 标记时再细化）。
    """
    features = _enabled_features(settings)
    results: list[dict[str, Any]] = []
    for index, item in enumerate(task_items, start=1):
        status_value = str(item.get("status") or "")
        if status_value == "completed":
            subitems = [{"feature": feature, "status": "success"} for feature in features]
        else:
            subitems = [{"feature": feature, "status": "no_return"} for feature in features]
        # 重试溢价：链接发生过 AI 重试/重绘/修复时给每个子项打 retried 标记，
        # 服务端按重试单价结算（老服务端忽略该字段，保持固定价兼容）。
        if bool(item.get("billing_retried")):
            for subitem in subitems:
                subitem["retried"] = True
        results.append({"link_idx": index, "subitems": subitems})
    return results


def settle_batch(
    client: CustomerAuthClient,
    token: str,
    freeze_id: str,
    task_items: list[dict[str, Any]],
    settings: dict[str, Any],
) -> dict[str, Any]:
    """结算一个冻结批次（幂等：重复结算由服务端返回已结算结果）。"""
    payload = {
        "items": derive_item_results(task_items, settings),
    }
    return client.settle_batch_points(token, freeze_id, payload)
