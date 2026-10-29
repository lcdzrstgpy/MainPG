"""订单利润服务：插件命令、采集结果合并与月度/当日聚合。"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Mapping

from ...data_collection.plugin_queue import (
    TEMU_CAPTURE_ALL,
    TEMU_CAPTURE_INCREMENTAL,
    TEMU_CAPTURE_RESOURCE,
    TEMU_ORDER_PROFIT_CAPTURE,
    DataCollectionPluginQueue,
    PluginCommand,
)
from .account_vault import AccountVault
from .repository import OrderProfitRepository

_AMOUNT_KEYS = (
    "declared_price", "gmv", "gmv_cny", "tail_freight", "tail_freight_cny",
    "unit_cost_cny", "purchase_cost_cny", "weight_kg", "commission_cny",
    "head_freight_cny", "profit_estimate_cny", "profit_rate",
)

# 订单状态归类：与前端订单列表页签保持一致，趋势图按这五类统计。
_STATUS_BUCKETS = (
    ("pending", re.compile(r"待发货|未接收|待揽收|待处理")),
    ("shipped", re.compile(r"已发货|运输|派送|待签收|已揽收")),
    ("signed", re.compile(r"已签收|已完成|已送达")),
    ("cancelled", re.compile(r"已取消|取消")),
)


class StaffNoRequiredError(ValueError):
    """首次创建店铺必须先填写员工标识（团队服务返回 staff_no_required）。"""


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value + 1e-9, digits)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _is_unreceived_status(status: Any, code: Any = "") -> bool:
    text = _text(status)
    code_text = _text(code)
    if code_text == "2":
        return True
    return any(word in text for word in ("待发货", "未接收", "待处理", "待揽收"))


def _finance_category(entry_type: Any, note: Any) -> str:
    text = f"{_text(entry_type)} {_text(note)}"
    if any(word in text for word in ("推广", "广告")):
        return "promotion"
    if any(word in text for word in ("罚款", "违规", "处罚", "违约", "赔付")):
        return "penalty"
    if "结算" in text:
        return "settlement"
    return "other"


class OrderProfitService:
    def __init__(
        self,
        repository: OrderProfitRepository,
        *,
        plugin_queue: DataCollectionPluginQueue | None = None,
    ) -> None:
        self._repository = repository
        self._queue = plugin_queue
        # 采集进度（command_id → 进度快照）：只放内存，重启后由插件下一次上报重建。
        self._progress: dict[int, dict[str, Any]] = {}
        # 登录指令的手机号/密码只放内存，等插件取走后立刻删除，不落队列库。
        self._account_secrets: dict[int, dict[str, str]] = {}
        # 软件里输入的验证码，等插件取走。
        self._capture_inputs: dict[int, str] = {}
        self._account_vault: AccountVault | None = None

    # ---------- plugin sessions / captures ----------
    def report_progress(
        self, workspace_id: str, command_id: int, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        if self._queue is None:
            raise RuntimeError("plugin queue is unavailable")
        # 校验指令归属，避免别的 workspace 往这里写进度。
        self._queue.get_command_for_workspace(
            workspace_id=workspace_id, command_id=int(command_id)
        )
        key = _text(payload.get("key"))
        state = _text(payload.get("state")) or "running"
        label = _text(payload.get("step_label")) or _text(payload.get("label"))
        detail = _text(payload.get("detail"))
        previous = self._progress.get(int(command_id)) or {}
        # 每一步单独记录：前端要「完成一个出一个」，不能等整条指令结束才拿到结果。
        steps = [dict(item) for item in (previous.get("steps") or [])]
        if key:
            step = next((item for item in steps if item.get("key") == key), None)
            if step is None:
                step = {"key": key, "label": label, "state": "running", "detail": ""}
                steps.append(step)
            if label:
                step["label"] = label
            if state:
                step["state"] = state
            if detail:
                step["detail"] = detail
            step["updated_at"] = _now()
        snapshot = {
            "command_id": int(command_id),
            "step": _int(payload.get("step")),
            "step_total": _int(payload.get("step_total")),
            "step_label": label or _text(previous.get("step_label")),
            "detail": detail,
            "percent": max(0, min(_int(payload.get("percent")), 100)),
            "steps": steps,
            "waiting_for": _text(payload.get("waiting_for"))
            or ("" if state in {"ok", "skipped", "failed"} else _text(previous.get("waiting_for"))),
            "updated_at": _now(),
        }
        self._progress[int(command_id)] = snapshot
        return snapshot

    def _progress_for(self, command_id: int) -> dict[str, Any] | None:
        return self._progress.get(int(command_id))
    def list_sessions(self, workspace_id: str) -> dict[str, Any]:
        if self._queue is None:
            raise RuntimeError("plugin queue is unavailable")
        rows = self._queue.list_sessions_for_workspace(
            workspace_id=workspace_id,
            command_type=TEMU_ORDER_PROFIT_CAPTURE,
            active_only=False,
        )
        sessions = []
        for row in rows:
            capabilities = row.get("capabilities") or {}
            sessions.append({
                "session_id": str(row.get("session_id") or ""),
                "browser": _text(capabilities.get("browser_name")) or "Edge",
                "plugin_version": _text(capabilities.get("extension_version")),
                "capabilities": capabilities,
                "status": _text(row.get("status")),
                "online": bool(row.get("online")),
                "last_seen_at": _text(row.get("last_seen_at")),
                "device_id": _text(row.get("device_id")),
                "device_name": _text(row.get("device_name")),
            })
        return {"sessions": sessions}

    def request_capture(
        self,
        workspace_id: str,
        *,
        session_id: str,
        mode: str = "list",
    ) -> dict[str, Any]:
        if self._queue is None:
            raise RuntimeError("plugin queue is unavailable")
        mode = _text(mode) or "list"
        if mode not in {"list", "detail"}:
            raise ValueError("采集模式只能是 list 或 detail")
        rows = self._queue.list_sessions_for_workspace(
            workspace_id=workspace_id,
            command_type=TEMU_ORDER_PROFIT_CAPTURE,
            active_only=False,
        )
        session = next(
            (row for row in rows if str(row.get("session_id")) == str(session_id)),
            None,
        )
        if session is None:
            raise LookupError("plugin session not found")
        if not session.get("online"):
            raise ValueError("plugin session is offline")
        capabilities = session.get("capabilities") or {}
        if capabilities.get(TEMU_ORDER_PROFIT_CAPTURE) is not True:
            raise ValueError("plugin session does not support order profit capture")
        command = self._queue.queue_command(
            actor_id=str(session.get("actor_id") or ""),
            workspace_id=workspace_id,
            session_id=int(session.get("session_id")),
            command_type=TEMU_ORDER_PROFIT_CAPTURE,
            payload={
                "mode": mode,
                "auto_query": True,
                "all_statuses": True,
            },
            idempotency_key=f"order-profit:{workspace_id}:{mode}:{int(time.time() * 1000)}",
        )
        return self._command_summary(command)

    def _session_for_command(self, workspace_id: str, session_id: str) -> Mapping[str, Any]:
        if self._queue is None:
            raise RuntimeError("plugin queue is unavailable")
        rows = self._queue.list_sessions_for_workspace(
            workspace_id=workspace_id,
            command_type=TEMU_ORDER_PROFIT_CAPTURE,
            active_only=False,
        )
        session = next(
            (row for row in rows if str(row.get("session_id")) == str(session_id)),
            None,
        )
        if session is None:
            raise LookupError("plugin session not found")
        if not session.get("online"):
            raise ValueError("plugin session is offline")
        return session

    def _reject_if_capture_running(
        self, workspace_id: str, session: Mapping[str, Any]
    ) -> None:
        """同一浏览器里同时跑两条采集会互相打断，终止也会失灵，直接挡住。"""
        if self._queue is None:
            return
        active = self._queue.active_capture_command(
            workspace_id=workspace_id, session_id=int(session.get("session_id"))
        )
        if active is not None:
            raise ValueError(
                "上一次采集还没结束：请先点「终止采集」，或等它跑完再开始"
            )

    def request_capture_all(
        self,
        workspace_id: str,
        *,
        session_id: str,
        incremental: bool = False,
    ) -> dict[str, Any]:
        """一键采集。incremental=True 时只补最新数据（订单/财务/结算只读第一页）。"""
        session = self._session_for_command(workspace_id, session_id)
        command_type = TEMU_CAPTURE_INCREMENTAL if incremental else TEMU_CAPTURE_ALL
        required = TEMU_CAPTURE_INCREMENTAL if incremental else TEMU_CAPTURE_ALL
        if (session.get("capabilities") or {}).get(required) is not True:
            label = "增量更新" if incremental else "一键全量采集"
            raise ValueError(
                f"当前插件会话不支持{label}：请在浏览器扩展页重新加载插件"
                "（更新到最新版本）后重新连接，再点一次"
            )
        self._reject_if_capture_running(workspace_id, session)
        command = self._queue.queue_command(
            actor_id=str(session.get("actor_id") or ""),
            workspace_id=workspace_id,
            session_id=int(session.get("session_id")),
            command_type=command_type,
            payload={"incremental": bool(incremental)},
            idempotency_key=(
                f"order-profit:{workspace_id}:{'inc' if incremental else 'all'}"
                f":{int(time.time() * 1000)}"
            ),
        )
        return self._command_summary(command)

    def request_resource(
        self, workspace_id: str, *, session_id: str, resource: str
    ) -> dict[str, Any]:
        """单个页面的「更新」：只采这一块数据。"""
        allowed = {"store", "traffic", "sales", "finance", "settlement", "orders"}
        target = _text(resource).lower()
        if target not in allowed:
            raise ValueError("unsupported capture resource")
        session = self._session_for_command(workspace_id, session_id)
        if (session.get("capabilities") or {}).get(TEMU_CAPTURE_RESOURCE) is not True:
            raise ValueError(
                "当前插件会话不支持单页更新：请在浏览器扩展页重新加载插件后再试"
            )
        self._reject_if_capture_running(workspace_id, session)
        command = self._queue.queue_command(
            actor_id=str(session.get("actor_id") or ""),
            workspace_id=workspace_id,
            session_id=int(session.get("session_id")),
            command_type=TEMU_CAPTURE_RESOURCE,
            payload={"resource": target},
            idempotency_key=f"order-profit:{workspace_id}:{target}:{int(time.time() * 1000)}",
        )
        return self._command_summary(command)

    def cancel_capture(self, workspace_id: str, command_id: int) -> dict[str, Any]:
        if self._queue is None:
            raise RuntimeError("plugin queue is unavailable")
        command = self._queue.cancel_command(
            workspace_id=workspace_id, command_id=int(command_id)
        )
        if command.command_type.startswith("temu_capture"):
            account_id = _text((command.payload or {}).get("account_id"))
            if account_id:
                self._repository.update_account_state(
                    workspace_id, account_id, status="ready", last_error="采集已终止"
                )
        return {
            "command_id": command.command_id,
            "status": command.status,
            "cancelled": command.status == "cancelled",
        }

    def capture_status(self, workspace_id: str, command_id: int) -> dict[str, Any]:
        if self._queue is None:
            raise RuntimeError("plugin queue is unavailable")
        command = self._queue.get_command_for_workspace(
            workspace_id=workspace_id, command_id=command_id
        )
        result = dict(command.result or {})
        payload = {
            "command_id": command.command_id,
            "command_type": command.command_type,
            "status": command.status,
            "capture_mode": _text(result.get("capture_mode")),
            "ok": bool(result.get("ok")),
            "ingested": False,
            "batch_id": "",
            "order_count": 0,
            "sub_order_count": 0,
            "item_count": 0,
            "gmv_total": 0,
            "declared_total": 0,
            "tail_freight_total": 0,
            "tail_currency": "",
            "status_text": _text(result.get("statusText")),
            "error": _text(result.get("error")),
            "help": _text(result.get("help")),
            "results": result.get("results") or [],
            "ok_count": _int(result.get("ok_count")),
            "skipped_count": _int(result.get("skipped_count")),
            "failed_count": _int(result.get("failed_count")),
            "progress": self._progress_for(command.command_id),
            "waiting_for": _text((self._progress_for(command.command_id) or {}).get("waiting_for")),
            "pending_input": self._capture_inputs.pop(int(command.command_id), ""),
        }
        if command.status == "succeeded":
            batch = self._repository.batch_for_command(workspace_id, command.command_id)
            if batch is None and result.get("ok") and result.get("items"):
                batch = self._ingest(workspace_id, command, result)
            if batch:
                payload.update({
                    "ingested": True,
                    "batch_id": _text(batch.get("id")),
                    "capture_mode": _text(batch.get("capture_mode")),
                    "order_count": _int(batch.get("order_count")),
                    "sub_order_count": _int(batch.get("sub_order_count")),
                    "item_count": _int(batch.get("item_count")),
                    "captured_at": _text(batch.get("captured_at")),
                })
            summary = result.get("summary") or {}
            payload.update({
                "gmv_total": _float(summary.get("gmv_total")) or 0,
                "declared_total": _float(summary.get("declared_total")) or 0,
                "tail_freight_total": _float(summary.get("tail_freight_total")) or 0,
                "tail_currency": _text(summary.get("tail_currency")),
            })
        self._sync_account_capture_state(workspace_id, command)
        return payload

    def latest_capture(self, workspace_id: str) -> dict[str, Any]:
        batch = self._repository.latest_batch(workspace_id)
        return {"capture": batch or None}

    def import_capture(self, workspace_id: str, result: Mapping[str, Any]) -> dict[str, Any]:
        """插件弹窗手动同步入口：不依赖插件会话，直接把最近一次采集写入本地。"""
        if not isinstance(result, Mapping):
            raise ValueError("capture result must be an object")
        items = result.get("items")
        if not result.get("ok") or not isinstance(items, list) or not items:
            raise ValueError("capture result has no order items")
        # 绑定店铺优先：采集结果统一归类到当前绑定/指定的店铺。
        shop_name = self._resolve_shop_name(workspace_id, result)
        if shop_name:
            result = {
                **result,
                "items": [
                    {**item, "shop_name": shop_name} if isinstance(item, Mapping) else item
                    for item in items
                ],
            }
        # 手动同步没有真实 command_id，用负数时间戳保证批次幂等键唯一。
        command_id = -int(time.time() * 1000)
        batch = self._ingest(
            workspace_id,
            SimpleNamespace(command_id=command_id),
            result,
        )
        return {
            "ok": True,
            "shop_name": shop_name,
            "batch_id": _text(batch.get("id")),
            "order_count": _int(batch.get("order_count")),
            "sub_order_count": _int(batch.get("sub_order_count")),
            "item_count": _int(batch.get("item_count")),
        }

    def _resolve_shop_name(self, workspace_id: str, payload: Mapping[str, Any]) -> str:
        """采集归类店铺：payload 指定优先，否则用工作台当前绑定的店铺。"""
        name = _text(payload.get("shop_name"))
        if name:
            self._repository.upsert_shop(workspace_id, name, activate=False)
        else:
            active = self._repository.active_shop(workspace_id)
            name = _text(active.get("name")) if active else ""
        if name:
            self._repository.touch_shop_capture(workspace_id, name)
        return name

    # ---------- 店铺绑定 ----------
    def _scope_shop(self, workspace_id: str, shop: Any = "") -> str:
        """取数范围：显式传 all 表示不按店铺过滤，其余情况默认用当前绑定店铺。

        绑定店铺后各页面默认只展示该店铺的数据，避免上一个店铺（或绑定前未归类）的数据
        混在同一个页面里。
        """
        text = _text(shop)
        if text.lower() == "all":
            return ""
        if text:
            return text
        active = self._repository.active_shop(workspace_id) or {}
        return _text(active.get("name"))

    def shops(self, workspace_id: str) -> dict[str, Any]:
        return {
            "shops": self._repository.list_shops(workspace_id),
            "active": self._repository.active_shop(workspace_id),
        }

    def bind_shop(
        self,
        workspace_id: str,
        *,
        name: str,
        platform: str = "temu",
        note: str = "",
        activate: bool = True,
        mall_id: str = "",
        team_client: Any = None,
        remote_token: str = "",
    ) -> dict[str, Any]:
        # 先向团队服务登记拿正式店铺码；断网/未登录时退回本地临时码，不挡用户采集。
        store_code, code_status, staff_no, warning = "", "local", "", ""
        if team_client is not None and getattr(team_client, "configured", False) and remote_token:
            try:
                registered = team_client.register_store(
                    remote_token,
                    name=name,
                    platform=platform,
                    mall_id=mall_id,
                )
                store = registered.get("store") or {}
                store_code = _text(store.get("store_code"))
                if store_code:
                    code_status = "official"
                if registered.get("conflict") == "other_account":
                    warning = "店铺名已被其他账号占用，本次按协助录入处理，数据会合并到同一店铺"
            except Exception as error:  # noqa: BLE001 - 远端异常一律降级
                if getattr(error, "code", "") == "staff_no_required":
                    raise StaffNoRequiredError("首次创建店铺需要先填写员工标识") from error
                warning = "团队服务暂时不可用，已生成本地临时店铺码，联网后可在店铺列表补同步"
        shop = self._repository.upsert_shop(
            workspace_id,
            name,
            platform=platform,
            note=note,
            activate=activate,
            store_code=store_code,
            code_status=code_status,
            staff_no=staff_no,
        )
        # 绑定前采集的数据没有店铺名，切到该店铺时把这些数据一起归过来，
        # 否则用户刚绑定完会看到所有页面都是空的。
        backfilled = 0
        if activate and shop:
            backfilled = self._repository.backfill_shop_name(workspace_id, _text(shop.get("name")))
        return {
            "ok": True,
            "shop": shop,
            "backfilled": backfilled,
            "sync_warning": warning,
            "shops": self._repository.list_shops(workspace_id),
            "active": self._repository.active_shop(workspace_id),
        }

    def sync_shop_code(
        self,
        workspace_id: str,
        shop_id: str,
        *,
        team_client: Any = None,
        remote_token: str = "",
    ) -> dict[str, Any]:
        """把本地临时店铺码换成团队服务发放的正式码。"""
        shop = self._repository.get_shop(workspace_id, shop_id)
        if shop is None:
            raise LookupError("shop not found")
        if team_client is None or not getattr(team_client, "configured", False):
            raise ValueError("团队服务未配置")
        if not remote_token:
            raise ValueError("请先登录后再同步店铺码")
        registered = team_client.register_store(
            remote_token,
            name=_text(shop.get("name")),
            platform=_text(shop.get("platform")) or "temu",
        )
        code = _text((registered.get("store") or {}).get("store_code"))
        if not code:
            raise ValueError("团队服务未返回店铺码")
        updated = self._repository.set_shop_code(workspace_id, shop_id, store_code=code)
        return {
            "ok": True,
            "shop": updated,
            "conflict": registered.get("conflict", ""),
            "shops": self._repository.list_shops(workspace_id),
            "active": self._repository.active_shop(workspace_id),
        }

    def team_profile(
        self,
        *,
        team_client: Any = None,
        remote_token: str = "",
    ) -> dict[str, Any]:
        if team_client is None or not getattr(team_client, "configured", False):
            return {"available": False, "profile": None}
        if not remote_token:
            return {"available": False, "profile": None, "reason": "not_logged_in"}
        try:
            payload = team_client.profile(remote_token)
        except Exception as error:  # noqa: BLE001
            return {"available": False, "profile": None, "reason": str(error)}
        return {"available": True, "profile": payload.get("profile")}

    def save_team_profile(
        self,
        *,
        staff_no: str,
        username: str = "",
        team_client: Any = None,
        remote_token: str = "",
    ) -> dict[str, Any]:
        if team_client is None or not getattr(team_client, "configured", False):
            raise ValueError("团队服务未配置")
        if not remote_token:
            raise ValueError("请先登录后再填写员工标识")
        payload = team_client.save_profile(
            remote_token,
            staff_no=staff_no,
            username=username,
        )
        return {"ok": True, "profile": payload.get("profile")}

    def submit_team_rollup(
        self,
        workspace_id: str,
        *,
        week_start: str = "",
        team_client: Any = None,
        remote_token: str = "",
    ) -> dict[str, Any]:
        """把一周的店铺汇总提交到团队服务（周六自动跑，也可随时手动补交）。"""
        from ..team_store.sync import submit_week

        if team_client is None or not getattr(team_client, "configured", False):
            raise ValueError("团队服务未配置")
        if not remote_token:
            raise ValueError("请先登录后再提交团队数据")
        return submit_week(
            self._repository,
            workspace_id,
            client=team_client,
            token=remote_token,
            week_start=week_start,
        )

    def team_submissions(
        self,
        *,
        team_client: Any = None,
        remote_token: str = "",
    ) -> dict[str, Any]:
        if team_client is None or not getattr(team_client, "configured", False):
            return {"available": False, "submissions": []}
        if not remote_token:
            return {"available": False, "submissions": [], "reason": "not_logged_in"}
        try:
            payload = team_client.list_submissions(remote_token)
        except Exception as error:  # noqa: BLE001
            return {"available": False, "submissions": [], "reason": str(error)}
        return {"available": True, "submissions": payload.get("submissions") or []}

    # ---------- 关联账号（工作台侧，透传到团队服务） ----------

    @staticmethod
    def _team_guard(team_client: Any, remote_token: str) -> None:
        if team_client is None or not getattr(team_client, "configured", False):
            raise ValueError("团队服务未配置")
        if not remote_token:
            raise ValueError("请先登录后再管理关联账号")

    def team_links(
        self,
        *,
        team_client: Any = None,
        remote_token: str = "",
    ) -> dict[str, Any]:
        if team_client is None or not getattr(team_client, "configured", False):
            return {"available": False, "reason": "团队服务未配置"}
        if not remote_token:
            return {"available": False, "reason": "not_logged_in"}
        try:
            payload = team_client.links(remote_token)
        except Exception as error:  # noqa: BLE001
            return {"available": False, "reason": str(error)}
        return {"available": True, **payload}

    def team_link_request(
        self,
        target_account_id: str,
        *,
        team_client: Any = None,
        remote_token: str = "",
    ) -> dict[str, Any]:
        self._team_guard(team_client, remote_token)
        payload = team_client.request_link(remote_token, target_account_id)
        return {"available": True, **payload}

    def team_link_accept(
        self,
        account_id: str,
        *,
        team_client: Any = None,
        remote_token: str = "",
    ) -> dict[str, Any]:
        self._team_guard(team_client, remote_token)
        payload = team_client.accept_link(remote_token, account_id)
        return {"available": True, **payload}

    def team_link_reject(
        self,
        account_id: str,
        *,
        team_client: Any = None,
        remote_token: str = "",
    ) -> dict[str, Any]:
        self._team_guard(team_client, remote_token)
        payload = team_client.reject_link(remote_token, account_id)
        return {"available": True, **payload}

    def team_link_remove(
        self,
        account_id: str,
        *,
        team_client: Any = None,
        remote_token: str = "",
    ) -> dict[str, Any]:
        self._team_guard(team_client, remote_token)
        payload = team_client.remove_link(remote_token, account_id)
        return {"available": True, **payload}



    def activate_shop(self, workspace_id: str, shop_id: str) -> dict[str, Any]:
        shop = self._repository.activate_shop(workspace_id, shop_id)
        if shop is None:
            raise LookupError("shop not found")
        backfilled = self._repository.backfill_shop_name(workspace_id, _text(shop.get("name")))
        return {
            "ok": True,
            "shop": shop,
            "backfilled": backfilled,
            "shops": self._repository.list_shops(workspace_id),
            "active": self._repository.active_shop(workspace_id),
        }

    def active_shop(self, workspace_id: str) -> dict[str, Any]:
        return {"shop": self._repository.active_shop(workspace_id)}

    def overview(self, workspace_id: str, *, shop: str = "") -> dict[str, Any]:
        scope = self._scope_shop(workspace_id, shop)
        items, _ = self._repository.list_items(workspace_id, shop=scope, limit=5000)
        parent_orders = {
            _text(item.get("parent_order_sn")) or _text(item.get("sub_order_sn"))
            for item in items
            if item.get("parent_order_sn") or item.get("sub_order_sn")
        }
        unreceived = [
            item for item in items
            if _is_unreceived_status(item.get("order_status"), item.get("order_status_code"))
        ]
        unreceived_orders = {
            _text(item.get("parent_order_sn")) or _text(item.get("sub_order_sn"))
            for item in unreceived
        }
        missing_cost: set[str] = set()
        missing_weight: set[str] = set()
        missing_declared = 0
        missing_gmv = 0
        for item in items:
            sku = _text(item.get("sku_id")) or _text(item.get("skc_id"))
            if _float(item.get("purchase_cost_cny")) is None and sku:
                missing_cost.add(sku)
            if _float(item.get("weight_kg")) is None and sku:
                missing_weight.add(sku)
            if _float(item.get("declared_price")) is None:
                missing_declared += 1
            if _float(item.get("gmv_cny")) is None:
                missing_gmv += 1
        links = self._repository.list_link_changes(workspace_id)
        after_sales = self._repository.list_after_sales(workspace_id)
        appeals = self._repository.list_appeals(workspace_id)
        traffic = self._repository.list_traffic_weekly(workspace_id)
        latest = self._repository.latest_batch(workspace_id)
        store = self._repository.latest_store_metrics(workspace_id, scope) or {}
        finance = self._repository.finance_totals(workspace_id, shop=scope)
        daily = self.daily(workspace_id, shop=scope)
        pending_shipment = _int(store.get("pending_shipment")) or len(unreceived_orders)
        cards = [
            {"key": "orders", "label": "订单总数", "value": len(parent_orders), "tone": ""},
            {"key": "unreceived", "label": "未接收订单", "value": len(unreceived_orders), "tone": "is-warning"},
            {"key": "pending_shipment", "label": "待发货(店铺)", "value": pending_shipment, "tone": "is-warning"},
            {"key": "active_products", "label": "在售商品", "value": _int(store.get("active_products")), "tone": ""},
            {"key": "sales_today", "label": "今日销量", "value": _int(store.get("sales_today")), "tone": ""},
            {"key": "sales_7d", "label": "近7日销量", "value": _int(store.get("sales_7d")), "tone": ""},
            {"key": "sales_30d", "label": "近30日销量", "value": _int(store.get("sales_30d")), "tone": ""},
            {"key": "refund_pending", "label": "退货待处理", "value": _int(store.get("refund_pending")), "tone": ""},
            {"key": "cancel_pending", "label": "取消待处理", "value": _int(store.get("cancel_pending")), "tone": ""},
            {"key": "missing_cost", "label": "未维护采购价", "value": len(missing_cost), "tone": "is-warning"},
            {"key": "missing_weight", "label": "未维护重量", "value": len(missing_weight), "tone": "is-warning"},
            {"key": "ad_cost", "label": "广告费用", "value": finance["ad_cost"], "tone": "is-negative"},
            {"key": "penalty", "label": "平台罚款", "value": finance["penalty"], "tone": "is-negative"},
        ]
        todos = []
        if unreceived_orders:
            todos.append({
                "key": "unreceived", "label": "未接收订单",
                "count": len(unreceived_orders), "view": "unreceived",
                "hint": "优先处理待发货/未接收订单，避免超时。",
            })
        if missing_cost:
            todos.append({
                "key": "missing_cost", "label": "未维护采购价",
                "count": len(missing_cost), "view": "daily",
                "hint": "到货源台账或商品列表补采购价，利润才准确。",
            })
        if missing_weight:
            todos.append({
                "key": "missing_weight", "label": "未维护重量",
                "count": len(missing_weight), "view": "daily",
                "hint": "补重量后才能计算头程运费。",
            })
        if missing_gmv or missing_declared:
            todos.append({
                "key": "missing_amounts", "label": "订单金额待补",
                "count": max(missing_gmv, missing_declared), "view": "orders",
                "hint": "打开订单详情页采集 GMV/申报价/回款。",
            })
        data_sources = [
            {"key": "orders", "label": "Temu 订单列表/详情", "status": "ready" if items else "empty"},
            {"key": "capture", "label": "插件采集", "status": "ready" if latest else "pending"},
            {"key": "store_home", "label": "店铺首页指标", "status": "ready" if store else "pending"},
            {"key": "finance", "label": "对账中心/财务流水", "status": "ready" if finance["entries"] else "pending"},
            {"key": "links", "label": "商品链接变动", "status": "ready" if links else "pending"},
            {"key": "after_sales", "label": "售后/退款", "status": "ready" if after_sales else "pending"},
            {"key": "appeals", "label": "申诉时效", "status": "ready" if appeals else "pending"},
            {"key": "traffic", "label": "流量周报", "status": "ready" if traffic else "pending"},
        ]
        return {
            "cards": cards,
            "todos": todos,
            "data_sources": data_sources,
            "latest_capture": latest,
            "store_metrics": store or None,
            "finance": finance,
            "daily": daily.get("summary"),
            "counts": {
                "orders": len(parent_orders),
                "unreceived": len(unreceived_orders),
                "pending_shipment": pending_shipment,
                "active_products": _int(store.get("active_products")),
                "sales_today": _int(store.get("sales_today")),
                "sales_7d": _int(store.get("sales_7d")),
                "sales_30d": _int(store.get("sales_30d")),
                "refund_pending": _int(store.get("refund_pending")),
                "cancel_pending": _int(store.get("cancel_pending")),
                "missing_cost_sku": len(missing_cost),
                "missing_weight_sku": len(missing_weight),
                "ad_cost": finance["ad_cost"],
                "penalty": finance["penalty"],
                "finance_entries": finance["entries"],
                "link_changes": len(links),
                "after_sales": len(after_sales),
                "appeals": len(appeals),
                "traffic_weeks": len(traffic),
            },
        }

    def unreceived(
        self,
        workspace_id: str,
        *,
        store: str = "",
        keyword: str = "",
        shop: str = "",
    ) -> dict[str, Any]:
        data = self.orders(workspace_id, month="", store=store, keyword=keyword, shop=shop, limit=5000)
        rows = [
            order for order in data.get("orders", [])
            if _is_unreceived_status(order.get("order_status"))
        ]
        return {
            "orders": rows,
            "total": len(rows),
            "summary": self._summary_from_orders(rows),
        }

    def markup(self, workspace_id: str, *, month: str = "", store: str = "", shop: str = "") -> dict[str, Any]:
        items, _ = self._repository.list_items(
            workspace_id,
            month=month,
            store=store,
            shop=self._scope_shop(workspace_id, shop),
            limit=5000,
        )
        groups: dict[str, dict[str, Any]] = {}
        for item in items:
            key = (
                _text(item.get("sku_id")) or _text(item.get("skc_id"))
                or _text(item.get("ext_code")) or _text(item.get("goods_name"))
            )
            if not key:
                continue
            group = groups.setdefault(key, {
                "sku_id": _text(item.get("sku_id")),
                "skc_id": _text(item.get("skc_id")),
                "ext_code": _text(item.get("ext_code")),
                "goods_name": _text(item.get("goods_name")),
                "site": _text(item.get("site")),
                "quantity": 0,
                "declared_price": None,
                "unit_cost_cny": None,
                "order_count": set(),
            })
            group["quantity"] += max(1, _int(item.get("quantity"), 1))
            if item.get("parent_order_sn"):
                group["order_count"].add(_text(item["parent_order_sn"]))
            declared = _float(item.get("declared_price"))
            if declared is None:
                gmv = _float(item.get("gmv_cny"))
                quantity = max(1, _int(item.get("quantity"), 1))
                declared = _round(gmv / quantity) if gmv is not None else None
            if declared is not None:
                group["declared_price"] = declared
            if _float(item.get("unit_cost_cny")) is not None:
                group["unit_cost_cny"] = _float(item.get("unit_cost_cny"))
        rows = []
        matched = 0
        missing_cost = 0
        missing_declared = 0
        for group in groups.values():
            declared = _float(group.get("declared_price"))
            cost = _float(group.get("unit_cost_cny"))
            quantity = max(1, _int(group.get("quantity"), 1))
            markup_rate = (
                _round((declared - cost) / cost, 6)
                if declared is not None and cost not in (None, 0) else None
            )
            gross_profit = (
                _round((declared - cost) * quantity)
                if declared is not None and cost is not None else None
            )
            if cost is None:
                status = "needs_cost"
                missing_cost += 1
            elif declared is None:
                status = "needs_declared"
                missing_declared += 1
            else:
                status = "matched"
                matched += 1
            rows.append({
                "sku_id": group["sku_id"],
                "skc_id": group["skc_id"],
                "ext_code": group["ext_code"],
                "goods_name": group["goods_name"],
                "site": group["site"],
                "quantity": quantity,
                "order_count": len(group["order_count"]),
                "declared_price": declared,
                "unit_cost_cny": cost,
                "markup_rate": markup_rate,
                "gross_profit_cny": gross_profit,
                "status": status,
            })
        rows.sort(key=lambda row: _float(row.get("gross_profit_cny")) or 0, reverse=True)
        rates = [_float(row.get("markup_rate")) for row in rows if _float(row.get("markup_rate")) is not None]
        return {
            "items": rows,
            "summary": {
                "sku_count": len(rows),
                "matched_count": matched,
                "missing_cost_count": missing_cost,
                "missing_declared_count": missing_declared,
                "avg_markup_rate": _round(sum(rates) / len(rates), 6) if rates else None,
            },
        }

    @staticmethod
    def _operation_payload(items: list[dict[str, Any]], pending_hint: str) -> dict[str, Any]:
        return {
            "items": items,
            "total": len(items),
            "data_source": "ready" if items else "pending",
            "message": "" if items else pending_hint,
        }

    def link_changes(self, workspace_id: str) -> dict[str, Any]:
        return self._operation_payload(
            self._repository.list_link_changes(workspace_id),
            "待接入 Temu 商品链接变动接口；接入后这里展示改价、改标题、换主图记录。",
        )

    def after_sales(self, workspace_id: str) -> dict[str, Any]:
        return self._operation_payload(
            self._repository.list_after_sales(workspace_id),
            "待接入 Temu 售后/退款接口；接入后按质量问题、物流问题、买家原因分类。",
        )

    def appeals(self, workspace_id: str) -> dict[str, Any]:
        return self._operation_payload(
            self._repository.list_appeals(workspace_id),
            "待接入 Temu 申诉接口；接入后展示申诉类型、截止时间和处理结果。",
        )

    def traffic_weekly(self, workspace_id: str) -> dict[str, Any]:
        return self._operation_payload(
            self._repository.list_traffic_weekly(workspace_id),
            "待接入 Temu 流量周报接口；接入后按周/站点展示访客、订单、GMV 和转化率。",
        )

    def capture_batches(self, workspace_id: str) -> dict[str, Any]:
        batches = self._repository.list_batches(workspace_id)
        return {"batches": batches, "latest": batches[0] if batches else None}

    def update_order_cost(
        self,
        workspace_id: str,
        parent_order_sn: str,
        *,
        purchase_cost_cny: Any,
        shop: str = "",
    ) -> dict[str, Any]:
        """手工填写整单采购成本。

        用户只看到一个「采购成本」格，这里按件数把它分摊到每个 SKU 行，
        并立刻按新成本重算该单利润；重算全量时手工值优先级高于台账。
        """
        items = self._repository.list_items_by_parent(
            workspace_id, parent_order_sn, shop=shop
        )
        if not items:
            raise LookupError("order not found")
        settings = self._repository.settings(workspace_id)
        total = _float(purchase_cost_cny)
        if total is not None and total < 0:
            raise ValueError("采购成本不能是负数")
        total_quantity = sum(max(1, _int(item.get("quantity"), 1)) for item in items)
        for item in items:
            quantity = max(1, _int(item.get("quantity"), 1))
            if total is None:
                item["manual_cost_cny"] = None
            else:
                item["manual_cost_cny"] = _round(total * quantity / total_quantity, 2)
            merged = self._apply_computed(item, settings)
            self._repository.upsert_item(workspace_id, merged)
        return {
            "ok": True,
            "parent_order_sn": parent_order_sn,
            "updated": len(items),
            "purchase_cost": None if total is None else _round(total, 2),
        }

    def recalculate(self, workspace_id: str) -> dict[str, Any]:
        settings = self._repository.settings(workspace_id)
        items, _ = self._repository.list_items(workspace_id, limit=5000)
        updated = 0
        for item in items:
            cost = self._repository.sourcing_cost(
                workspace_id,
                skc_id=_text(item.get("skc_id")),
                skc_code=_text(item.get("skc_code")),
            )
            if cost:
                item["unit_cost_cny"] = _float(cost.get("unit_cost_cny"))
                item["weight_kg"] = _float(cost.get("weight_kg"))
                item["cost_source"] = _text(cost.get("cost_source"))
            item = self._apply_computed(item, settings)
            self._repository.upsert_item(workspace_id, item)
            updated += 1
        return {
            "updated": updated,
            "summary": self._summary_from_orders(
                self.orders(workspace_id, limit=5000).get("orders", [])
            ),
        }

    def import_finance(self, workspace_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        raw_entries = payload.get("entries")
        if not isinstance(raw_entries, list) or not raw_entries:
            raise ValueError("finance capture has no entries")
        entries = []
        months: set[str] = set()
        for raw in raw_entries:
            if not isinstance(raw, Mapping):
                continue
            entry_time = _text(raw.get("entry_time")) or _text(raw.get("time"))
            amount = _float(raw.get("amount"))
            if not entry_time or amount is None:
                continue
            entry_type = _text(raw.get("entry_type")) or _text(raw.get("type"))
            note = _text(raw.get("note"))
            category = _text(raw.get("category")) or _finance_category(entry_type, note)
            entries.append({
                "entry_time": entry_time,
                "entry_type": entry_type,
                "amount": amount,
                "currency": _text(raw.get("currency")) or "CNY",
                "note": note,
                "category": category,
            })
            months.add(entry_time[:7])
        if not entries:
            raise ValueError("finance capture has no valid entries")
        shop_name = self._resolve_shop_name(workspace_id, payload)
        inserted = self._repository.upsert_finance_entries(
            workspace_id,
            entries,
            shop_name=shop_name,
            captured_at=_text(payload.get("capturedAt")),
        )
        return {
            "ok": True,
            "inserted": inserted,
            "entry_count": len(entries),
            "shop_name": shop_name,
            "months": sorted(months),
            "summary": self._repository.finance_totals(workspace_id),
        }

    def finance_summary(self, workspace_id: str, *, month: str = "", shop: str = "") -> dict[str, Any]:
        scope = self._scope_shop(workspace_id, shop)
        return {
            "shop_name": scope,
            "summary": self._repository.finance_totals(workspace_id, month, scope),
            "items": self._repository.list_finance_entries(workspace_id, month=month, shop=scope),
        }

    def import_store_metrics(self, workspace_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        raw_metrics = payload.get("metrics")
        metrics = dict(raw_metrics) if isinstance(raw_metrics, Mapping) else dict(payload)
        shop_name = self._resolve_shop_name(workspace_id, payload)
        if shop_name:
            metrics["shop_name"] = shop_name
        saved = self._repository.upsert_store_metrics(
            workspace_id,
            metrics,
            captured_at=_text(payload.get("capturedAt")),
        )
        return {"ok": True, "metrics": saved}

    def store_metrics(self, workspace_id: str, *, shop: str = "") -> dict[str, Any]:
        scope = self._scope_shop(workspace_id, shop)
        return {
            "shop_name": scope,
            "metrics": self._repository.latest_store_metrics(workspace_id, scope),
        }

    def import_settlement(self, workspace_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        raw_rows = payload.get("rows")
        if not isinstance(raw_rows, list):
            raw_rows = payload.get("items")
        status = _text(payload.get("status")).lower()
        if status not in {"pending", "settled"}:
            status = "pending"
        rows: list[dict[str, Any]] = []
        for raw in raw_rows or []:
            if not isinstance(raw, Mapping):
                continue
            po_text = (
                _text(raw.get("parent_order_sn"))
                or _text(raw.get("po_sn"))
                or _text(raw.get("po"))
            )
            match = re.search(r"PO-[A-Za-z0-9-]+", po_text)
            if match is None:
                continue
            rows.append({
                "parent_order_sn": match.group(0),
                "currency": _text(raw.get("currency")) or "CNY",
                "sales_payback": _float(raw.get("sales_payback")),
                "sales_reversal": _float(raw.get("sales_reversal")),
                "freight_payback": _float(raw.get("freight_payback")),
                "freight_reversal": _float(raw.get("freight_reversal")),
                "declared_price": _float(raw.get("declared_price")),
                "quantity": _int(raw.get("quantity")),
                "goods_name": _text(raw.get("goods_name")),
                "site": _text(raw.get("site")),
            })
        if not rows:
            raise ValueError("settlement capture has no valid rows")
        written = self._repository.upsert_settlements(
            workspace_id,
            rows,
            status=status,
            shop_name=self._resolve_shop_name(workspace_id, payload),
            captured_at=_text(payload.get("capturedAt")),
        )
        backfilled = self._apply_settlement_to_items(workspace_id, rows)
        return {
            "ok": True,
            "status": status,
            "written": written,
            "row_count": len(rows),
            "backfilled_items": backfilled,
            "summary": self._repository.settlement_totals(workspace_id, status),
        }

    def _apply_settlement_to_items(
        self, workspace_id: str, rows: list[Mapping[str, Any]]
    ) -> int:
        """把 PO 单结算金额按申报价×件数分摊回订单行，订单利润改用实际回款口径。"""
        settings = self._repository.settings(workspace_id)
        updated = 0
        for row in rows:
            items = self._repository.items_for_parent_order(
                workspace_id, _text(row.get("parent_order_sn"))
            )
            if not items:
                continue
            weights: list[float] = []
            for item in items:
                declared = _float(item.get("declared_price"))
                quantity = max(1, _int(item.get("quantity"), 1))
                weights.append((declared or 0) * quantity or float(quantity))
            total_weight = sum(weights) or float(len(items))
            for item, weight in zip(items, weights):
                share = (weight or 1) / total_weight
                for key in (
                    "sales_payback",
                    "sales_reversal",
                    "freight_payback",
                    "freight_reversal",
                ):
                    value = _float(row.get(key))
                    if value is not None:
                        item[key] = _round(value * share)
                declared_price = _float(row.get("declared_price"))
                if declared_price is not None and _float(item.get("declared_price")) is None:
                    item["declared_price"] = declared_price
                item = self._apply_computed(item, settings)
                self._repository.upsert_item(workspace_id, item)
                updated += 1
        return updated

    def settlement_summary(self, workspace_id: str, *, status: str = "", shop: str = "") -> dict[str, Any]:
        scope = self._scope_shop(workspace_id, shop)
        return {
            "shop_name": scope,
            "summary": self._repository.settlement_totals(workspace_id, status, scope),
            "pending": self._repository.settlement_totals(workspace_id, "pending", scope),
            "settled": self._repository.settlement_totals(workspace_id, "settled", scope),
            "rows": self._repository.list_settlements(workspace_id, status=status, shop=scope),
        }

    def import_traffic(self, workspace_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        raw_cards = payload.get("cards")
        if not isinstance(raw_cards, list) or not raw_cards:
            raise ValueError("traffic capture has no summary cards")
        scope = _text(payload.get("scope")).lower()
        if scope not in {"product", "shop"}:
            scope = "product"
        metrics: dict[str, str] = {}
        numbers: dict[str, float] = {}
        for card in raw_cards:
            if not isinstance(card, Mapping):
                continue
            label = _text(card.get("label"))
            value = _text(card.get("value"))
            if not label or not value:
                continue
            metrics[label] = value
            change = _text(card.get("change"))
            if change and change != "-":
                metrics[f"{label}(环比)"] = change
            amount = _float(value.rstrip("%").replace(",", ""))
            if amount is not None:
                numbers[label] = amount
        if not metrics:
            raise ValueError("traffic capture has no valid metrics")
        charts = self._normalize_traffic_charts(payload.get("charts"))
        if charts:
            # 趋势/渠道图是 canvas 画的，只能把页面接口返回的原始序列一起存下来。
            metrics["charts"] = charts
            trend = self._extract_flux_trend(charts)
            if trend:
                metrics["trend"] = trend
        snapshot = {
            "scope": scope,
            "period": _text(payload.get("period")),
            "stat_date": _text(payload.get("stat_date")) or _now()[:10],
            "exposure": _int(numbers.get("商品曝光量") or numbers.get("曝光量")),
            "clicks": _int(numbers.get("商品点击量") or numbers.get("点击量")),
            "visitors": _int(numbers.get("访客数")),
            "paid_orders": _int(numbers.get("支付订单数") or numbers.get("订单数")),
            "paid_items": _int(numbers.get("支付件数") or numbers.get("件数")),
            "click_rate": numbers.get("点击率"),
            "click_order_rate": numbers.get("点击订单转化率"),
            "exposure_order_rate": numbers.get("曝光订单转化率"),
            "metrics": metrics,
            "shop_name": self._resolve_shop_name(workspace_id, payload),
        }
        saved = self._repository.upsert_traffic_snapshot(
            workspace_id,
            snapshot,
            captured_at=_text(payload.get("capturedAt")),
        )
        return {"ok": True, "snapshot": saved, "metric_count": len(metrics)}

    @staticmethod
    def _normalize_traffic_charts(raw: Any) -> list[dict[str, Any]]:
        """图表接口数据：限制条数与单条体积，避免把整页接口塞进快照。"""
        charts: list[dict[str, Any]] = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, Mapping):
                continue
            endpoint = _text(item.get("endpoint"))
            response = item.get("response")
            if not endpoint or not isinstance(response, (Mapping, list)):
                continue
            if len(json.dumps(response, ensure_ascii=False)) > 200_000:
                continue
            request = item.get("request")
            charts.append(
                {
                    "endpoint": endpoint,
                    "request": request if isinstance(request, (Mapping, list)) else None,
                    "response": response,
                    "captured_at": _text(item.get("capturedAt")),
                }
            )
            if len(charts) >= 12:
                break
        return charts

    @staticmethod
    def _extract_flux_trend(charts: list[dict[str, Any]]) -> dict[str, Any] | None:
        """把 /api/flow/analysis/trend 的响应整理成前端可直接画的按小时序列。

        结构：``result.trendList`` 是「近1日 / 前1日」逐小时曝光量，
        ``result.channelList`` 是「搜索 / 推荐 / 其他」逐小时堆叠。
        """
        trend_payload: Mapping[str, Any] | None = None
        overview: Mapping[str, Any] | None = None
        for chart in charts:
            endpoint = str(chart.get("endpoint") or "")
            response = chart.get("response")
            result = response.get("result") if isinstance(response, Mapping) else None
            if not isinstance(result, Mapping):
                continue
            if endpoint.endswith("/api/flow/analysis/trend"):
                trend_payload = result
            elif endpoint.endswith("/api/flow/analysis/overview"):
                overview = result
        if trend_payload is None:
            return None

        def series(raw: Any) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for item in raw if isinstance(raw, list) else []:
                if not isinstance(item, Mapping):
                    continue
                key = _text(item.get("key"))
                points: list[dict[str, Any]] = []
                for point in item.get("trendList") or []:
                    if not isinstance(point, Mapping):
                        continue
                    time_text = _text(point.get("time"))
                    value = point.get("value")
                    if not time_text or value is None:
                        continue
                    points.append({"time": time_text, "value": float(value)})
                if key and points:
                    out.append({"key": key, "points": points})
            return out

        trend_series = series(trend_payload.get("trendList"))
        channels = series(trend_payload.get("channelList"))
        if not trend_series and not channels:
            return None
        data: dict[str, Any] = {"series": trend_series, "channels": channels}
        if isinstance(overview, Mapping):
            data["overview"] = {
                key: overview.get(key)
                for key in (
                    "impressionCount",
                    "clickCount",
                    "orderPayOrderNum",
                    "orderPayGoodsNum",
                    "clickImpressionRate",
                    "clickOrderRatio",
                    "orderPayImpressionRate",
                    "pt",
                )
            }
        return data

    def traffic_summary(self, workspace_id: str, *, shop: str = "") -> dict[str, Any]:
        scope = self._scope_shop(workspace_id, shop)
        payload: dict[str, Any] = {
            "shop_name": scope,
            "product": self._repository.latest_traffic_snapshot(workspace_id, "product", scope),
            "shop": self._repository.latest_traffic_snapshot(workspace_id, "shop", scope),
            "history": self._repository.list_traffic_snapshots(workspace_id, shop=scope, limit=30),
        }
        # 老快照只有原始 charts、没有整理好的 trend：读的时候补一次，免去重新采集。
        for key in ("product", "shop"):
            snapshot = payload.get(key)
            if not isinstance(snapshot, Mapping):
                continue
            metrics = snapshot.get("metrics")
            if not isinstance(metrics, dict) or "trend" in metrics:
                continue
            charts = metrics.get("charts")
            if isinstance(charts, list) and charts:
                trend = self._extract_flux_trend(charts)
                if trend:
                    metrics["trend"] = trend
        return payload

    def import_sales(self, workspace_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        metrics: dict[str, str] = {}
        raw_metrics = payload.get("metrics")
        for card in raw_metrics if isinstance(raw_metrics, list) else []:
            if not isinstance(card, Mapping):
                continue
            label = _text(card.get("label"))
            value = _text(card.get("value"))
            if not label or not value:
                continue
            metrics[label] = value
            day_change = _text(card.get("day_change"))
            week_change = _text(card.get("week_change"))
            if day_change and day_change != "-":
                metrics[f"{label}(较昨日)"] = day_change
            if week_change and week_change != "-":
                metrics[f"{label}(较近7日)"] = week_change
        if not metrics:
            raise ValueError("sales capture has no valid metrics")
        health: dict[str, str] = {}
        raw_health = payload.get("health")
        items = raw_health.items() if isinstance(raw_health, Mapping) else []
        for key, value in items:
            label = _text(key)
            text = _text(value)
            if label and text:
                health[label] = text
        for card in raw_health if isinstance(raw_health, list) else []:
            if not isinstance(card, Mapping):
                continue
            label = _text(card.get("label"))
            value = _text(card.get("value"))
            if label and value:
                health[label] = value
        updated_at = _text(payload.get("updated_at"))
        if updated_at:
            health["最近更新时间"] = updated_at
        saved = self._repository.upsert_sales_snapshot(
            workspace_id,
            {
                "region": _text(payload.get("region")) or "全球",
                "stat_date": _text(payload.get("stat_date")) or updated_at[:10] or _now()[:10],
                "metrics": metrics,
                "health": health,
                "shop_name": self._resolve_shop_name(workspace_id, payload),
            },
            captured_at=_text(payload.get("capturedAt")),
        )
        return {"ok": True, "snapshot": saved, "metric_count": len(metrics)}

    def sales_summary(self, workspace_id: str, *, shop: str = "") -> dict[str, Any]:
        scope = self._scope_shop(workspace_id, shop)
        return {
            "shop_name": scope,
            "latest": self._repository.latest_sales_snapshot(workspace_id, shop=scope),
            "history": self._repository.list_sales_snapshots(workspace_id, shop=scope, limit=30),
        }

    def _command_summary(self, command: PluginCommand) -> dict[str, Any]:
        return {
            "command_id": command.command_id,
            "command_type": command.command_type,
            "status": command.status,
            "payload": dict(command.payload or {}),
        }

    # ---------- ingest ----------
    def _ingest(
        self,
        workspace_id: str,
        command: PluginCommand,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        settings = self._repository.settings(workspace_id)
        items = result.get("items")
        if not isinstance(items, list):
            items = []
        parent_orders: set[str] = set()
        sub_orders: set[str] = set()
        for raw in items:
            if not isinstance(raw, Mapping):
                continue
            incoming = self._normalize_item(workspace_id, raw, settings)
            incoming["command_id"] = command.command_id
            existing = self._repository.find_item(workspace_id, incoming)
            merged = self._merge(existing, incoming)
            merged = self._apply_computed(merged, settings)
            self._repository.upsert_item(workspace_id, merged)
            # 订单行按唯一键会跨店合并成一条，这里额外记录「哪家店采到过」，
            # 供跨店铺重复订单打标记使用。
            self._repository.record_shop_claim(workspace_id, merged)
            if merged.get("parent_order_sn"):
                parent_orders.add(_text(merged["parent_order_sn"]))
            if merged.get("sub_order_sn"):
                sub_orders.add(_text(merged["sub_order_sn"]))
        batch = self._repository.create_batch(
            workspace_id,
            command_id=command.command_id,
            capture_mode=_text(result.get("capture_mode")),
            order_count=len(parent_orders),
            sub_order_count=len(sub_orders),
            item_count=len(sub_orders) or len(items),
            captured_at=_text(result.get("capturedAt")) or _now(),
        )
        return batch

    def _normalize_item(
        self,
        workspace_id: str,
        raw: Mapping[str, Any],
        settings: Mapping[str, float],
    ) -> dict[str, Any]:
        quantity = max(1, _int(raw.get("quantity"), 1))
        item = {
            "workspace_id": workspace_id,
            "batch_id": "",
            "command_id": 0,
            "parent_order_sn": _text(raw.get("parent_order_sn")),
            "sub_order_sn": _text(raw.get("sub_order_sn")),
            "sku_id": _text(raw.get("sku_id")),
            "skc_id": _text(raw.get("skc_id")),
            "skc_code": _text(raw.get("skc_code")),
            "ext_code": _text(raw.get("ext_code")),
            "goods_name": _text(raw.get("goods_name")),
            "image_url": _text(raw.get("image_url")),
            "quantity": quantity,
            "declared_price": _float(raw.get("declared_price")),
            "declared_currency": _text(raw.get("declared_currency")) or "CNY",
            "gmv": _float(raw.get("gmv")),
            "gmv_currency": _text(raw.get("gmv_currency")),
            "sales_payback": _float(raw.get("sales_payback")),
            "sales_reversal": _float(raw.get("sales_reversal")),
            "freight_payback": _float(raw.get("freight_payback")),
            "freight_reversal": _float(raw.get("freight_reversal")),
            "estimated_income": _float(raw.get("estimated_income")),
            "order_payment": _float(raw.get("order_payment")),
            "second_payment": _float(raw.get("second_payment")),
            "tail_freight": _float(raw.get("tail_freight")),
            "tail_currency": _text(raw.get("tail_currency")),
            "tail_freight_cny": None,
            "tracking_no": _text(raw.get("tracking_no")),
            "order_status": _text(raw.get("order_status")),
            "order_status_code": _text(raw.get("order_status_code")),
            "site": _text(raw.get("site")),
            "order_time": _text(raw.get("order_time")),
            "shop_name": _text(raw.get("shop_name")),
            "mall_id": _text(raw.get("mall_id")),
            "unit_cost_cny": None,
            "purchase_cost_cny": None,
            "weight_kg": None,
            "cost_source": "",
            "match_status": "",
            "commission_cny": None,
            "head_freight_cny": None,
            "profit_estimate_cny": None,
            "profit_rate": None,
            "source": _text(raw.get("source")),
            "captured_at": _text(raw.get("captured_at")) or _now(),
        }
        cost = self._repository.sourcing_cost(
            workspace_id,
            skc_id=item["skc_id"],
            skc_code=item["skc_code"],
        )
        if cost:
            item["unit_cost_cny"] = _float(cost.get("unit_cost_cny"))
            item["weight_kg"] = _float(cost.get("weight_kg"))
            item["cost_source"] = _text(cost.get("cost_source"))
        return self._apply_computed(item, settings)

    def _merge(
        self,
        existing: Mapping[str, Any] | None,
        incoming: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not existing:
            return dict(incoming)
        merged = dict(existing)
        for key, value in incoming.items():
            if value is None or value == "":
                continue
            if key in {"batch_id", "command_id"}:
                continue
            if key in _AMOUNT_KEYS and _float(merged.get(key)) is not None and _float(value) == 0:
                # 详情页的 0 元字段不能覆盖列表里已有的金额（例如 0 元回款占位）。
                continue
            if key == "sub_order_sn":
                old_digits = _text(merged.get("sub_order_sn")).replace("-", "")
                parent_digits = _text(merged.get("parent_order_sn")).replace("-", "")
                if old_digits and old_digits != parent_digits:
                    continue
            merged[key] = value
        return merged

    def _apply_computed(
        self,
        item: dict[str, Any],
        settings: Mapping[str, float],
    ) -> dict[str, Any]:
        rate = float(settings.get("usd_cny_rate") or 7.2)
        commission_rate = float(settings.get("commission_rate") or 0.05)
        head_unit_price = float(settings.get("head_unit_price_cny") or 0)
        gmv_cny = self._to_cny(item.get("gmv"), item.get("gmv_currency"), rate)
        tail_cny = self._to_cny(item.get("tail_freight"), item.get("tail_currency"), rate)
        quantity = max(1, _int(item.get("quantity"), 1))
        unit_cost = _float(item.get("unit_cost_cny"))
        weight = _float(item.get("weight_kg"))
        manual_cost = _float(item.get("manual_cost_cny"))
        if manual_cost is not None:
            # 用户手工填的成本优先于台账自动匹配，重算利润时也以它为准。
            purchase = _round(manual_cost)
            item["cost_source"] = "manual"
        else:
            purchase = _round(unit_cost * quantity) if unit_cost is not None else None
        head = _round(weight * quantity * head_unit_price) if weight is not None and head_unit_price > 0 else None
        commission = _round(gmv_cny * commission_rate) if gmv_cny is not None else None
        profit = None
        if gmv_cny is not None:
            profit = gmv_cny - (commission or 0) - (purchase or 0) - (head or 0) - (tail_cny or 0)
            profit = _round(profit)
        item["gmv_cny"] = gmv_cny
        item["tail_freight_cny"] = tail_cny
        item["purchase_cost_cny"] = purchase
        item["commission_cny"] = commission
        item["head_freight_cny"] = head
        item["profit_estimate_cny"] = profit
        item["profit_rate"] = _round(profit / gmv_cny, 6) if profit is not None and gmv_cny else None
        if gmv_cny is None:
            item["match_status"] = "needs_gmv"
        elif purchase is None:
            item["match_status"] = "needs_cost"
        elif weight is None and head_unit_price > 0:
            item["match_status"] = "needs_weight"
        else:
            item["match_status"] = "matched"
        return item

    @staticmethod
    def _to_cny(value: Any, currency: Any, rate: float) -> float | None:
        amount = _float(value)
        if amount is None:
            return None
        currency = _text(currency).upper()
        if currency in {"", "CNY", "RMB"}:
            return _round(amount)
        if currency == "USD":
            return _round(amount * rate)
        return None

    # ---------- aggregates ----------
    def orders(
        self,
        workspace_id: str,
        *,
        month: str = "",
        months: list[str] | None = None,
        store: str = "",
        keyword: str = "",
        shop: str = "",
        limit: int = 500,
        offset: int = 0,
    ) -> dict[str, Any]:
        scope = self._scope_shop(workspace_id, shop)
        items, total = self._repository.list_items(
            workspace_id,
            month=month,
            months=months,
            store=store,
            shop=scope,
            keyword=keyword,
            limit=limit,
            offset=offset,
        )
        orders = self._group_orders(items)
        summary = self._summary_from_orders(orders)
        finance = self._repository.finance_totals(workspace_id, month, scope, months=months)
        summary["ad_cost"] = finance["ad_cost"]
        summary["penalty"] = finance["penalty"]
        summary["finance"] = finance
        summary["profit"] = _round(
            (summary.get("profit") or 0)
            - finance["ad_cost"]
            - finance["penalty"]
        )
        summary["profit_rate"] = (
            _round(summary["profit"] / summary["gmv_total"], 6)
            if summary.get("gmv_total") else None
        )
        return {
            "shop_name": scope,
            "summary": summary,
            "orders": orders,
            "total": len(orders),
            "items_total": total,
        }

    def order_months(self, workspace_id: str, *, shop: str = "") -> dict[str, Any]:
        """有订单数据的月份列表，供订单页月份多选与趋势图使用。"""
        scope = self._scope_shop(workspace_id, shop)
        return {"months": self._repository.list_order_months(workspace_id, shop=scope, limit=24)}

    def orders_trend(
        self,
        workspace_id: str,
        *,
        months: list[str] | None = None,
        store: str = "",
        shop: str = "",
    ) -> dict[str, Any]:
        """按月汇总订单状态数量与金额，供前端趋势图使用。"""
        scope = self._scope_shop(workspace_id, shop)
        available = self._repository.list_order_months(workspace_id, shop=scope, limit=24)
        wanted = [str(month).strip() for month in (months or []) if str(month).strip()]
        if not wanted:
            wanted = list(reversed(available[:6]))
        # 时间轴按月份升序，前端图表才能从左到右展示变化。
        wanted = sorted(wanted)
        series: list[dict[str, Any]] = []
        for month in wanted:
            items, _total = self._repository.list_items(
                workspace_id,
                month=month,
                store=store,
                shop=scope,
                limit=5000,
            )
            orders = self._group_orders(items)
            counts = {key: 0 for key, _pattern in _STATUS_BUCKETS}
            counts["other"] = 0
            for order in orders:
                status = _text(order.get("order_status"))
                for key, pattern in _STATUS_BUCKETS:
                    if pattern.search(status):
                        counts[key] += 1
                        break
                else:
                    counts["other"] += 1
            summary = self._summary_from_orders(orders)
            series.append({
                "month": month,
                "order_count": len(orders),
                "pending": counts["pending"],
                "shipped": counts["shipped"],
                "signed": counts["signed"],
                "cancelled": counts["cancelled"],
                "other": counts["other"],
                "gmv_total": summary["gmv_total"],
                "sales_payback": summary["sales_payback"],
                "settled_total": summary["settled_total"],
                "purchase_cost": summary["purchase_cost"],
                "tail_freight": summary["tail_freight"],
                "profit": summary["profit"],
            })
        return {"months": wanted, "available_months": available, "series": series}

    def _group_orders(self, items: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """把子订单行按父订单汇总成订单行（订单列表与趋势图共用同一套口径）。"""
        groups: dict[str, dict[str, Any]] = {}
        for item in items:
            parent = _text(item.get("parent_order_sn")) or _text(item.get("sub_order_sn"))
            order = groups.setdefault(parent, {
                "parent_order_sn": parent,
                "shop_name": "",
                "mall_id": "",
                "site": "",
                "order_status": "",
                "order_time": "",
                "sub_orders": set(),
                "item_count": 0,
                "gmv_total": 0.0,
                "declared_total": 0.0,
                "sales_payback": 0.0,
                "sales_reversal": 0.0,
                "freight_payback": 0.0,
                "freight_reversal": 0.0,
                "purchase_cost": 0.0,
                "head_freight": 0.0,
                "tail_freight": 0.0,
                "profit_estimate": 0.0,
                "has_financials": False,
                "items": [],
            })
            order["shop_name"] = order["shop_name"] or _text(item.get("shop_name"))
            order["mall_id"] = order["mall_id"] or _text(item.get("mall_id"))
            order["site"] = order["site"] or _text(item.get("site"))
            order["order_status"] = order["order_status"] or _text(item.get("order_status"))
            order["order_time"] = order["order_time"] or _text(item.get("order_time"))
            if item.get("sub_order_sn"):
                order["sub_orders"].add(_text(item["sub_order_sn"]))
            order["item_count"] += _int(item.get("quantity"), 1)
            order["gmv_total"] += _float(item.get("gmv_cny")) or 0
            order["declared_total"] += _float(item.get("declared_price")) or 0
            order["purchase_cost"] += _float(item.get("purchase_cost_cny")) or 0
            order["head_freight"] += _float(item.get("head_freight_cny")) or 0
            order["tail_freight"] += _float(item.get("tail_freight_cny")) or 0
            order["profit_estimate"] += _float(item.get("profit_estimate_cny")) or 0
            for key in ("sales_payback", "sales_reversal", "freight_payback", "freight_reversal"):
                value = _float(item.get(key))
                if value is not None:
                    order[key] += value
                    order["has_financials"] = True
            order["items"].append(item)

        orders = []
        for order in groups.values():
            settled = (
                order["sales_payback"] + order["sales_reversal"]
                + order["freight_payback"] + order["freight_reversal"]
            )
            profit = (
                settled - order["purchase_cost"] - order["head_freight"] - order["tail_freight"]
                if order["has_financials"]
                else order["profit_estimate"]
            )
            gmv = order["gmv_total"]
            orders.append({
                "parent_order_sn": order["parent_order_sn"],
                "shop_name": order["shop_name"],
                "mall_id": order["mall_id"],
                "site": order["site"],
                "order_status": order["order_status"],
                "order_time": order["order_time"],
                "sub_order_count": len(order["sub_orders"]),
                "item_count": order["item_count"],
                "sku_count": len({_text(item.get("sku_id")) for item in order["items"] if item.get("sku_id")}),
                "gmv_total": _round(gmv),
                "declared_total": _round(order["declared_total"]),
                "sales_payback": _round(order["sales_payback"]),
                "sales_reversal": _round(order["sales_reversal"]),
                "freight_payback": _round(order["freight_payback"]),
                "freight_reversal": _round(order["freight_reversal"]),
                "settled_total": _round(settled),
                "purchase_cost": _round(order["purchase_cost"]),
                "head_freight": _round(order["head_freight"]),
                "tail_freight": _round(order["tail_freight"]),
                "penalty": 0.0,
                "ad_cost": 0.0,
                "profit": _round(profit),
                "profit_rate": _round(profit / gmv, 6) if gmv else None,
                "items": order["items"],
            })
        orders.sort(key=lambda item: _text(item.get("order_time")), reverse=True)
        return orders

    def _summary_from_orders(self, orders: list[Mapping[str, Any]]) -> dict[str, Any]:
        summary = {
            "order_count": len(orders),
            "sales_payback": 0.0,
            "settled_total": 0.0,
            "sales_reversal": 0.0,
            "purchase_cost": 0.0,
            "head_freight": 0.0,
            "tail_freight": 0.0,
            "penalty": 0.0,
            "ad_cost": 0.0,
            "gmv_total": 0.0,
            "declared_total": 0.0,
            "profit": 0.0,
            "missing_purchase_sku": 0,
            "missing_weight_sku": 0,
            "missing_declared": 0,
            "missing_gmv": 0,
        }
        missing_purchase: set[str] = set()
        missing_weight: set[str] = set()
        for order in orders:
            summary["sales_payback"] += _float(order.get("sales_payback")) or 0
            summary["settled_total"] += _float(order.get("settled_total")) or 0
            summary["sales_reversal"] += _float(order.get("sales_reversal")) or 0
            summary["purchase_cost"] += _float(order.get("purchase_cost")) or 0
            summary["head_freight"] += _float(order.get("head_freight")) or 0
            summary["tail_freight"] += _float(order.get("tail_freight")) or 0
            summary["gmv_total"] += _float(order.get("gmv_total")) or 0
            summary["declared_total"] += _float(order.get("declared_total")) or 0
            summary["profit"] += _float(order.get("profit")) or 0
            for item in order.get("items") or []:
                sku = _text(item.get("sku_id")) or _text(item.get("skc_id"))
                if _float(item.get("purchase_cost_cny")) is None and sku:
                    missing_purchase.add(sku)
                if _float(item.get("weight_kg")) is None and sku:
                    missing_weight.add(sku)
                if _float(item.get("declared_price")) is None:
                    summary["missing_declared"] += 1
                if _float(item.get("gmv_cny")) is None:
                    summary["missing_gmv"] += 1
        summary["missing_purchase_sku"] = len(missing_purchase)
        summary["missing_weight_sku"] = len(missing_weight)
        for key in ("sales_payback", "settled_total", "sales_reversal", "purchase_cost",
                    "head_freight", "tail_freight", "gmv_total", "declared_total", "profit"):
            summary[key] = _round(summary[key])
        summary["profit_rate"] = (
            _round(summary["profit"] / summary["gmv_total"], 6)
            if summary["gmv_total"] else None
        )
        return summary

    def daily(
        self,
        workspace_id: str,
        *,
        date: str = "",
        store: str = "",
        keyword: str = "",
        head_unit_price: float | None = None,
        shop: str = "",
    ) -> dict[str, Any]:
        settings = self._repository.settings(workspace_id)
        effective_head_price = (
            float(head_unit_price) if head_unit_price is not None
            else float(settings.get("head_unit_price_cny") or 0)
        )
        if not date:
            date = self._repository.latest_order_date(workspace_id)
        items, _total = self._repository.list_items(
            workspace_id,
            date=date,
            store=store,
            shop=self._scope_shop(workspace_id, shop),
            keyword=keyword,
            limit=5000,
        )
        for item in items:
            self._apply_daily_computed(item, settings, effective_head_price)
        parent_orders = {_text(item.get("parent_order_sn")) for item in items if item.get("parent_order_sn")}
        sub_orders = {_text(item.get("sub_order_sn")) for item in items if item.get("sub_order_sn")}
        summary = {
            "date": date,
            "order_count": len(parent_orders),
            "sub_order_count": len(sub_orders),
            "item_count": sum(_int(item.get("quantity"), 1) for item in items),
            "gmv_total": _round(sum(
                _float(item.get("gmv_estimate_cny")) if _float(item.get("gmv_estimate_cny")) is not None
                else (_float(item.get("gmv_cny")) or 0)
                for item in items
            )),
            "declared_total": _round(sum(_float(item.get("declared_price")) or 0 for item in items)),
            "purchase_cost": _round(sum(_float(item.get("purchase_cost_cny")) or 0 for item in items)),
            "head_freight": _round(sum(_float(item.get("head_freight_cny")) or 0 for item in items)),
            "tail_freight": _round(sum(_float(item.get("tail_freight_cny")) or 0 for item in items)),
            "commission": _round(sum(_float(item.get("commission_cny")) or 0 for item in items)),
            "profit": _round(sum(_float(item.get("profit_estimate_cny")) or 0 for item in items)),
            "head_unit_price_cny": effective_head_price,
            "tail_currency": "CNY",
        }
        summary["profit_rate"] = (
            _round(summary["profit"] / summary["gmv_total"], 6)
            if summary["gmv_total"] else None
        )
        missing_purchase = [
            item for item in items
            if _float(item.get("purchase_cost_cny")) is None
        ]
        missing_weight = [
            item for item in items
            if _float(item.get("weight_kg")) is None
        ]
        warnings = []
        if missing_purchase:
            warnings.append({
                "type": "purchase",
                "count": len(missing_purchase),
                "sku_count": len({_text(item.get("sku_id")) or _text(item.get("skc_id")) for item in missing_purchase}),
                "message": "部分子订单未维护采购价，采购成本按 0 计入",
            })
        if missing_weight:
            warnings.append({
                "type": "weight",
                "count": len(missing_weight),
                "sku_count": len({_text(item.get("sku_id")) or _text(item.get("skc_id")) for item in missing_weight}),
                "message": "部分子订单未维护重量，头程运费无法估算",
            })
        return {"summary": summary, "items": items, "warnings": warnings}

    def _apply_daily_computed(
        self,
        item: dict[str, Any],
        settings: Mapping[str, float],
        head_unit_price: float,
    ) -> None:
        gmv = _float(item.get("gmv_cny"))
        quantity = max(1, _int(item.get("quantity"), 1))
        declared = _float(item.get("declared_price"))
        declared_gmv = _round(declared * quantity) if declared is not None else None
        # 跨境小帮手的口径：当日预估的 GMV = 申报价 × 件数；没有申报价时才退回订单金额。
        effective_gmv = declared_gmv if declared_gmv is not None else gmv
        item["gmv_estimate_cny"] = declared_gmv
        commission_rate = float(settings.get("commission_rate") or 0.05)
        commission = _round(effective_gmv * commission_rate) if effective_gmv is not None else None
        unit_cost = _float(item.get("unit_cost_cny"))
        purchase = _round(unit_cost * quantity) if unit_cost is not None else None
        weight = _float(item.get("weight_kg"))
        head = _round(weight * quantity * head_unit_price) if weight is not None and head_unit_price > 0 else None
        tail = _float(item.get("tail_freight_cny"))
        profit = (
            effective_gmv - (commission or 0) - (purchase or 0) - (head or 0) - (tail or 0)
            if effective_gmv is not None else None
        )
        item["commission_cny"] = commission
        item["purchase_cost_cny"] = purchase
        item["head_freight_cny"] = head
        item["profit_estimate_cny"] = _round(profit)
        item["profit_rate"] = _round(profit / effective_gmv, 6) if profit is not None and effective_gmv else None
        item["missing"] = {
            "purchase_cost": purchase is None,
            "weight": weight is None,
            "declared_price": declared is None,
            "gmv": effective_gmv is None,
            "tail_freight": tail is None,
        }

    def stores(self, workspace_id: str, *, shop: str = "") -> dict[str, Any]:
        return {
            "shop_name": self._scope_shop(workspace_id, shop),
            "stores": self._repository.list_stores(workspace_id, self._scope_shop(workspace_id, shop)),
        }

    def cleanup_unbound_shops(self, workspace_id: str) -> dict[str, Any]:
        """清理没有对应店铺账号的店铺记录（只影响店铺下拉，不删订单数据）。"""
        accounts = self._repository.list_accounts(workspace_id)
        bound = {_text(item.get("shop_name")) for item in accounts if _text(item.get("shop_name"))}
        shops = self._repository.list_shops(workspace_id)
        stale = [shop for shop in shops if _text(shop.get("name")) not in bound]
        ids = [_text(shop.get("id")) for shop in stale if _text(shop.get("id"))]
        deleted = self._repository.delete_shops(workspace_id, ids) if ids else 0
        remaining = self._repository.list_shops(workspace_id)
        if remaining and not any(_int(shop.get("is_active")) for shop in remaining):
            self._repository.activate_shop(workspace_id, _text(remaining[0].get("id")))
            remaining = self._repository.list_shops(workspace_id)
        return {
            "deleted": deleted,
            "names": [_text(shop.get("name")) for shop in stale],
            "kept": [_text(shop.get("name")) for shop in remaining],
            "active": next((shop for shop in remaining if _int(shop.get("is_active"))), None),
        }

    def settings(self, workspace_id: str) -> dict[str, Any]:
        return {"settings": self._repository.settings(workspace_id)}

    def save_settings(
        self,
        workspace_id: str,
        *,
        usd_cny_rate: float,
        commission_rate: float,
        head_unit_price_cny: float,
    ) -> dict[str, Any]:
        settings = self._repository.save_settings(
            workspace_id,
            usd_cny_rate=max(0, float(usd_cny_rate)),
            commission_rate=min(1, max(0, float(commission_rate))),
            head_unit_price_cny=max(0, float(head_unit_price_cny)),
        )
        return {"settings": settings}

    # ---------- 店铺账号（自动登录采集） ----------

    def _vault(self) -> AccountVault:
        if self._account_vault is None:
            self._account_vault = AccountVault(self._repository.database_path.parent)
        return self._account_vault

    def accounts(self, workspace_id: str) -> dict[str, Any]:
        return {"accounts": self._repository.list_accounts(workspace_id)}

    def save_account(
        self,
        workspace_id: str,
        *,
        account_id: str = "",
        alias: str = "",
        phone: str = "",
        password: str = "",
        region: str = "global",
        shop_name: str = "",
    ) -> dict[str, Any]:
        if not _text(phone):
            raise ValueError("手机号不能为空")
        # 密码改成可选：默认由用户在浏览器里手动登录，这里只保存账号标识。
        secret = _text(password)
        encrypted = self._vault().encrypt(secret) if secret else ""
        account = self._repository.upsert_account(
            workspace_id,
            account_id=_text(account_id),
            alias=_text(alias),
            phone=_text(phone),
            password_enc=encrypted,
            region=_text(region) or "global",
        )
        manual_name = _text(shop_name)
        # 只允许在「插件还没读到店铺名」时手填；已经自动读到的名字不让覆盖。
        if manual_name and _text(account.get("shop_name")):
            manual_name = ""
        if manual_name:
            # 手填店铺名：直接写回账号并建/绑店铺，插件读不到名字时也能用。
            self._repository.update_account_state(
                workspace_id, account["id"], shop_name=manual_name
            )
            self._repository.upsert_shop(
                workspace_id, name=manual_name, platform="temu", activate=True
            )
            account = self._repository.get_account(workspace_id, account["id"]) or account
        return {"account": account}

    def delete_account(self, workspace_id: str, account_id: str) -> dict[str, Any]:
        return {"deleted": self._repository.delete_account(workspace_id, account_id)}

    def clear_account_data(self, workspace_id: str, account_id: str) -> dict[str, Any]:
        """清除该账号绑定店铺的采集数据，保留店铺和账号绑定。"""
        account = self._repository.get_account(workspace_id, account_id)
        if account is None:
            raise ValueError("账号不存在")
        shop_name = _text(account.get("shop_name"))
        if not shop_name:
            raise ValueError("该账号还没有绑定店铺，没有可清除的数据")
        counts = self._repository.clear_shop_data(workspace_id, shop_name)
        return {"shop_name": shop_name, "cleared": counts}

    def record_login_result(
        self,
        workspace_id: str,
        account_id: str,
        *,
        ok: bool,
        shop_name: str = "",
        error: str = "",
    ) -> dict[str, Any]:
        account = self._repository.get_account(workspace_id, account_id)
        if account is None:
            raise ValueError("账号不存在")
        # 插件读不到店铺名时保留账号里已有的（可能是用户手填的）。
        resolved_name = _text(shop_name) or _text((account or {}).get("shop_name"))
        self._repository.update_account_state(
            workspace_id,
            account_id,
            status="ready" if ok else "error",
            shop_name=resolved_name if ok else None,
            last_error="" if ok else _text(error),
            last_login_at=_now() if ok else "",
        )
        bound = None
        # 账号登录成功后自动用「Temu 里读到的店铺名」建/绑店铺：
        # 采集页的店铺下拉只来自这里，不再让用户手填店铺名。
        shop_name_text = resolved_name
        if ok and shop_name_text:
            bound = self._repository.upsert_shop(
                workspace_id,
                name=shop_name_text,
                platform="temu",
                activate=True,
            )
        return {
            "account": self._repository.get_account(workspace_id, account_id),
            "shop": bound,
        }

    def account_credentials(self, workspace_id: str, command_id: int) -> dict[str, str]:
        """插件取走一次登录凭据，取完即删（不落库、不回显）。"""
        if self._queue is None:
            raise RuntimeError("plugin queue is unavailable")
        self._queue.get_command_for_workspace(
            workspace_id=workspace_id, command_id=int(command_id)
        )
        secret = self._account_secrets.pop(int(command_id), None)
        if not secret:
            raise ValueError("登录凭据已失效，请重新发起登录")
        return secret

    def submit_capture_input(
        self, workspace_id: str, command_id: int, value: str
    ) -> dict[str, Any]:
        if self._queue is None:
            raise RuntimeError("plugin queue is unavailable")
        self._queue.get_command_for_workspace(
            workspace_id=workspace_id, command_id=int(command_id)
        )
        code = _text(value)
        if not code:
            raise ValueError("验证码不能为空")
        self._capture_inputs[int(command_id)] = code
        return {"command_id": int(command_id), "accepted": True}

    def _session_for_instance(
        self, workspace_id: str, instance_tag: str
    ) -> dict[str, Any] | None:
        if self._queue is None or not instance_tag:
            return None
        rows = self._queue.list_sessions_for_workspace(
            workspace_id=workspace_id,
            command_type=TEMU_ORDER_PROFIT_CAPTURE,
            active_only=True,
        )
        for row in rows:
            capabilities = row.get("capabilities") or {}
            if _text(capabilities.get("instance_tag")) == instance_tag:
                return row
        return None

    def start_account_login(
        self,
        workspace_id: str,
        account_id: str,
        *,
        session_id: str = "",
        capture: bool = True,
        manual: bool = False,
    ) -> dict[str, Any]:
        """给某个店铺账号下发「登录 + 采集」指令。

        manual=True 时插件不填表，由用户在弹出的浏览器里自己登录，
        插件只负责跟住页面、读出店铺名并绑定。
        """
        if self._queue is None:
            raise RuntimeError("plugin queue is unavailable")
        account = self._repository.get_account(workspace_id, account_id, with_secret=True)
        if account is None:
            raise ValueError("账号不存在")
        password = "" if manual else self._vault().decrypt(_text(account.get("password_enc")))
        if not manual and not password:
            raise ValueError("该账号还没有保存密码，请先编辑账号补上")

        instance_tag = _text(account.get("instance_tag"))
        session = self._session_for_instance(workspace_id, instance_tag)
        if session is None and session_id:
            session = self._session_for_command(workspace_id, session_id)
        if session is None:
            raise ValueError(
                "该账号的浏览器还没有连上工作台：请先点「打开浏览器」，等插件连上后再登录采集"
            )
        capabilities = session.get("capabilities") or {}
        if capabilities.get("temu_account_login") is not True:
            raise ValueError("插件版本过旧：请在扩展页重新加载最新插件后再试")
        self._reject_if_capture_running(workspace_id, session)

        command = self._queue.queue_command(
            actor_id=str(session.get("actor_id") or ""),
            workspace_id=workspace_id,
            session_id=int(session.get("session_id")),
            command_type="temu_account_login",
            payload={
                "account_id": account_id,
                "phone_tail": _text(account.get("phone_tail")),
                "alias": _text(account.get("alias")),
                "region": _text(account.get("region")) or "global",
                "capture": bool(capture),
                "manual": bool(manual),
            },
            idempotency_key=f"account-login:{workspace_id}:{account_id}:{int(time.time() * 1000)}",
        )
        command_id = int(getattr(command, "command_id", 0) or 0)
        if command_id and not manual:
            self._account_secrets[command_id] = {
                "phone": _text(account.get("phone")),
                "password": password,
            }
        self._repository.update_account_state(
            workspace_id,
            account_id,
            status="logging_in",
            last_error="",
            command_id=command_id,
        )
        return self._command_summary(command)

    def start_account_capture(self, workspace_id: str, account_id: str) -> dict[str, Any]:
        """只下发采集：不改绑定、不登录，插件核对登录店铺后直接拉数据。"""
        if self._queue is None:
            raise RuntimeError("plugin queue is unavailable")
        account = self._repository.get_account(workspace_id, account_id)
        if account is None:
            raise ValueError("账号不存在")
        shop_name = _text(account.get("shop_name"))
        if not shop_name:
            raise ValueError("该账号还没有绑定店铺，请先点「登录并绑定」")
        instance_tag = _text(account.get("instance_tag"))
        session = self._session_for_instance(workspace_id, instance_tag)
        if session is None:
            raise ValueError(
                "该账号的浏览器还没有连上工作台：请先点「打开浏览器」，等插件连上后再采集"
            )
        capabilities = session.get("capabilities") or {}
        if capabilities.get("temu_capture_all") is not True:
            raise ValueError("插件版本过旧：请在扩展页重新加载最新插件后再试")
        self._reject_if_capture_running(workspace_id, session)

        command = self._queue.queue_command(
            actor_id=str(session.get("actor_id") or ""),
            workspace_id=workspace_id,
            session_id=int(session.get("session_id")),
            command_type="temu_capture_all",
            payload={
                "account_id": account_id,
                "shop_name": shop_name,
                "alias": _text(account.get("alias")),
                "source": "account_capture",
            },
            idempotency_key=f"account-capture:{workspace_id}:{account_id}:{int(time.time() * 1000)}",
        )
        command_id = int(getattr(command, "command_id", 0) or 0)
        self._repository.update_account_state(
            workspace_id,
            account_id,
            status="capturing",
            last_error="",
            command_id=command_id,
        )
        return self._command_summary(command)

    def _sync_account_capture_state(
        self, workspace_id: str, command: PluginCommand
    ) -> None:
        """账号采集结束时把账号状态改回可用，避免一直停在「采集中」。"""
        if command.command_type != "temu_capture_all":
            return
        account_id = _text((command.payload or {}).get("account_id"))
        if not account_id or command.status not in {"succeeded", "failed", "cancelled"}:
            return
        result = dict(command.result or {})
        ok = command.status == "succeeded" and bool(result.get("ok"))
        error = "" if ok else (
            _text(result.get("statusText")) or _text(result.get("error")) or "采集失败"
        )
        self._repository.update_account_state(
            workspace_id, account_id, status="ready" if ok else "error", last_error=error
        )

    def open_account_browser(self, workspace_id: str, account_id: str) -> dict[str, Any]:
        from .browser_launcher import open_instance

        account = self._repository.get_account(workspace_id, account_id)
        if account is None:
            raise ValueError("账号不存在")
        tag = _text(account.get("instance_tag")) or account_id
        base = _text(os.environ.get("WH_WORKBENCH_BASE_URL")) or "http://127.0.0.1:8010"
        start_url = f"{base}/?wh_instance={tag}"
        result = open_instance(
            tag=tag,
            start_url=start_url,
            data_dir=self._repository.database_path.parent,
        )
        self._repository.set_account_instance(
            workspace_id, account_id, tag=tag, pid=int(result["pid"])
        )
        return {
            "account": self._repository.get_account(workspace_id, account_id),
            "pid": result["pid"],
            "profile": result["profile"],
            "browser": result["browser"],
            "instance_tag": tag,
        }

    def close_account_browser(self, workspace_id: str, account_id: str) -> dict[str, Any]:
        from .browser_launcher import close_instance

        account = self._repository.get_account(workspace_id, account_id)
        if account is None:
            raise ValueError("账号不存在")
        pid = int(account.get("instance_pid") or 0)
        closed = close_instance(pid) if pid else False
        self._repository.set_account_instance(
            workspace_id, account_id, tag=_text(account.get("instance_tag")), pid=0
        )
        return {"closed": closed}
