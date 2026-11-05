"""每周团队汇总的打包与提交。

数据全部来自本地库，随时可以重放：提交失败不需要本地队列，
下一次自动任务或手动点「提交到团队」会重新生成同样的载荷再推一次。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any

from .client import TeamApiClient
from .dedupe import BEIJING

logger = logging.getLogger(__name__)


def week_bounds(week_start: object = "") -> tuple[date, date]:
    """返回（周一, 周日）；不传则取本周。"""
    text = str(week_start or "").strip()
    if text:
        try:
            anchor = date.fromisoformat(text[:10])
        except ValueError:
            anchor = datetime.now(BEIJING).date()
    else:
        anchor = datetime.now(BEIJING).date()
    monday = anchor - timedelta(days=anchor.weekday())
    return monday, monday + timedelta(days=6)


def _now() -> str:
    return datetime.now(BEIJING).isoformat(timespec="seconds")


def _dup_for(shop_name: str, duplicates: list[dict[str, Any]]) -> dict[str, Any]:
    """同一账号下，这家店与其他店铺重复的订单数。"""
    count = 0
    others: list[str] = []
    samples: list[str] = []
    for row in duplicates:
        left = str(row.get("left_shop") or "")
        right = str(row.get("right_shop") or "")
        if shop_name not in (left, right):
            continue
        count += int(row.get("dup_count") or 0)
        others.append(right if left == shop_name else left)
        sample = str(row.get("sample") or "")
        if sample and len(samples) < 5:
            samples.append(sample)
    if not count:
        return {}
    return {"count": count, "other_store_code": "", "other_shops": others, "samples": samples}


def build_payload(
    repository: Any,
    workspace_id: str,
    *,
    week_start: object = "",
) -> dict[str, Any]:
    """把本地数据打包成团队服务的提交载荷。

    只有拿到正式店铺码的店铺才会上报；本地临时码的店铺会单独列出来，
    提醒用户先联网同步店铺码。
    """
    monday, sunday = week_bounds(week_start)
    start, end = monday.isoformat(), sunday.isoformat()

    shops = repository.list_shops(workspace_id)
    official = [
        shop
        for shop in shops
        if str(shop.get("store_code") or "").strip() and shop.get("code_status") == "official"
    ]
    pending = [
        {
            "name": str(shop.get("name") or ""),
            "store_code": str(shop.get("store_code") or ""),
            "reason": "店铺码还是本地临时码，先同步后再提交",
        }
        for shop in shops
        if shop not in official
    ]

    # 日汇总也传全量历史：团队看板的趋势图要能看到以前的日期，
    # 服务端按 (店铺, 日期) 与源更新时间做幂等覆盖，重复提交不会产生脏数据。
    days = repository.rollup_days(
        workspace_id, start="2000-01-01", end="2099-12-31"
    )
    duplicates = repository.duplicate_orders(workspace_id)
    by_shop: dict[str, list[dict[str, Any]]] = {}
    for row in days:
        by_shop.setdefault(str(row.get("shop_name") or ""), []).append(row)

    stamp = _now()
    stores: list[dict[str, Any]] = []
    export_records = getattr(repository, "export_shop_records", None)
    for shop in official:
        name = str(shop.get("name") or "")
        rows = sorted(by_shop.get(name, []), key=lambda item: item["stat_date"])
        # 明细一起上传：团队看板的订单/财务/流量等页面直接读这些记录。
        records = export_records(workspace_id, name) if callable(export_records) else []
        stores.append(
            {
                "store_code": str(shop["store_code"]),
                "days": [
                    {**row, "source_updated_at": row.get("source_updated_at") or stamp}
                    for row in rows
                ],
                "dup_orders": _dup_for(name, duplicates),
                "records": records,
            }
        )

    # 采集批次是工作台级的（不按店铺拆分），单独放在顶层，
    # 团队看板可以按时间/模式分类展示最近入库记录。
    batch_lister = getattr(repository, "list_batches", None)
    raw_batches = batch_lister(workspace_id, 50) if callable(batch_lister) else []
    batches = [
        {
            "batch_id": str(row.get("id") or ""),
            "capture_mode": str(row.get("capture_mode") or ""),
            "order_count": int(row.get("order_count") or 0),
            "sub_order_count": int(row.get("sub_order_count") or 0),
            "item_count": int(row.get("item_count") or 0),
            "captured_at": str(row.get("captured_at") or row.get("created_at") or ""),
        }
        for row in raw_batches
        if str(row.get("id") or "")
    ]

    return {
        "week_start": start,
        "submitted_at": stamp,
        "stores": stores,
        "batches": batches,
        "pending_local_shops": pending,
        "day_count": sum(len(store["days"]) for store in stores),
        "record_count": sum(len(store["records"]) for store in stores),
        "batch_count": len(batches),
    }


def submit_week(
    repository: Any,
    workspace_id: str,
    *,
    client: TeamApiClient,
    token: str,
    week_start: object = "",
) -> dict[str, Any]:
    payload = build_payload(repository, workspace_id, week_start=week_start)
    if not token:
        raise ValueError("请先登录后再提交")
    if not payload["stores"]:
        return {
            "ok": False,
            "reason": "no_official_store",
            "status_text": "还没有拿到正式店铺码的店铺，先联网同步店铺码再提交",
            "pending_local_shops": payload["pending_local_shops"],
        }
    result = client.submit_rollup(token, {
        "week_start": payload["week_start"],
        "submitted_at": payload["submitted_at"],
        "stores": payload["stores"],
        "batches": payload["batches"],
    })
    return {
        **result,
        "week_start": payload["week_start"],
        "day_count": payload["day_count"],
        "batch_count": payload["batch_count"],
        "pending_local_shops": payload["pending_local_shops"],
    }


class TeamRollupTask:
    """周六自动提交本周汇总；其余时间由用户在采集页手动补交。

    载荷每次都从本地库重新生成，所以不需要本地队列：这次推失败，
    下一轮自动任务会带着同样（或更新）的数据再推一次。
    """

    def __init__(
        self,
        *,
        repository: Any,
        client: TeamApiClient,
        sessions: Any,
        interval_seconds: int = 1800,
        saturday_only: bool = True,
    ) -> None:
        self._repository = repository
        self._client = client
        self._sessions = sessions
        self._interval = max(60, int(interval_seconds))
        self._saturday_only = saturday_only
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name="team-rollup", daemon=True
        )
        self._thread.start()

    def _loop(self) -> None:
        # 启动后先等一分钟，避开启动高峰，再按间隔轮询。
        time.sleep(60)
        while True:
            try:
                self.run_once()
            except Exception as error:  # noqa: BLE001 - 后台任务不得拖垮主进程
                logger.warning("team rollup tick failed: %s", error)
            time.sleep(self._interval)

    def run_once(self, *, force: bool = False) -> list[dict[str, Any]]:
        now = datetime.now(BEIJING)
        if self._saturday_only and not force and now.weekday() < 5:
            return []
        resolver = getattr(self._sessions, "active_remote_sessions", None)
        if not callable(resolver):
            return []
        week = now.date() - timedelta(days=now.weekday())
        results: list[dict[str, Any]] = []
        for _user_id, workspace_id, token in resolver():
            try:
                existing = self._client.list_submissions(token).get("submissions") or []
                if any(str(item.get("week_start")) == week.isoformat() for item in existing):
                    continue
                results.append(
                    submit_week(
                        self._repository,
                        workspace_id,
                        client=self._client,
                        token=token,
                        week_start=week.isoformat(),
                    )
                )
            except Exception as error:  # noqa: BLE001
                logger.warning("team rollup submit failed for %s: %s", workspace_id, error)
        return results

