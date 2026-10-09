from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ...db import connect


# 工作台所有「今日」口径统一按北京时间（UTC+8）切分，与前端 Intl 展示保持一致。
# 本机可能在任意时区，因此显式偏移而不是依赖 SQLite 的 localtime。
BEIJING = timezone(timedelta(hours=8))
DEFAULT_TREND_DAYS = 30
RECENT_TASK_LIMIT = 6

TASK_STATUS_LABELS: dict[str, str] = {
    "queued": "排队中",
    "running": "处理中",
    "completed": "已完成",
    "partial_failure": "部分失败",
    "failed": "失败",
    "cancelled": "已取消",
}
TASK_STATUS_ORDER = ("completed", "partial_failure", "failed", "cancelled", "running", "queued")
ACTIVE_TASK_STATUSES = ("queued", "running")


@dataclass(frozen=True)
class DashboardService:
    """汇总本地库指标，供工作台看板一次性读取（避免前端逐模块拉全量再本地统计）。"""

    database_path: Path

    def overview(self, workspace_id: str, *, trend_days: int = DEFAULT_TREND_DAYS) -> dict[str, Any]:
        today = _beijing_today()
        start_day = (today - timedelta(days=trend_days - 1)).isoformat()
        with closing(connect(self.database_path)) as conn:
            kpis = self._kpis(conn, workspace_id, today)
            points = self._trend(conn, workspace_id, today, start_day, trend_days)
            task_status = self._task_status(conn, workspace_id)
            site_distribution = self._site_distribution(conn, workspace_id)
            recent_tasks = self._recent_tasks(conn, workspace_id)
        return {
            "workspace_id": workspace_id,
            "generated_at": datetime.now(BEIJING).isoformat(timespec="seconds"),
            "kpis": kpis,
            "trend": {"days": trend_days, "start": start_day, "end": today.isoformat(), "points": points},
            "task_status": task_status,
            "site_distribution": site_distribution,
            "recent_tasks": recent_tasks,
        }

    def _kpis(self, conn: Any, workspace_id: str, today: date) -> dict[str, int]:
        today_key = today.isoformat()
        today_tasks = conn.execute(
            "SELECT COUNT(*) AS task_count,"
            " COALESCE(SUM(total_count), 0) AS product_count,"
            " COALESCE(SUM(success_count), 0) AS success_count,"
            " COALESCE(SUM(failed_count), 0) AS failed_count"
            " FROM product_processing_tasks"
            " WHERE workspace_id = ? AND date(created_at, '+8 hours') = ?",
            (workspace_id, today_key),
        ).fetchone()
        return {
            "product_total": _count(
                conn,
                "SELECT COUNT(*) FROM profit_activity_records WHERE workspace_id = ?",
                (workspace_id,),
            ),
            "today_inbound": _count(
                conn,
                "SELECT COUNT(*) FROM profit_activity_records"
                " WHERE workspace_id = ? AND date(created_at, '+8 hours') = ?",
                (workspace_id, today_key),
            ),
            "drafts_pending": _count(
                conn,
                "SELECT COUNT(*) FROM product_processing_drafts WHERE workspace_id = ? AND status = 'draft'",
                (workspace_id,),
            ),
            "active_tasks": _count(
                conn,
                "SELECT COUNT(*) FROM product_processing_tasks"
                " WHERE workspace_id = ? AND status IN ('queued', 'running')",
                (workspace_id,),
            ),
            "attention_required": _count(
                conn,
                "SELECT COUNT(*) FROM product_processing_task_items AS items"
                " JOIN product_processing_tasks AS tasks ON tasks.id = items.task_id"
                " WHERE tasks.workspace_id = ? AND items.status = 'attention_required'",
                (workspace_id,),
            ),
            "task_total": _count(
                conn,
                "SELECT COUNT(*) FROM product_processing_tasks WHERE workspace_id = ?",
                (workspace_id,),
            ),
            "today_task_count": int(today_tasks["task_count"]),
            "today_processed_products": int(today_tasks["success_count"]),
            "today_failed_products": int(today_tasks["failed_count"]),
            "today_product_count": int(today_tasks["product_count"]),
        }

    def _trend(
        self,
        conn: Any,
        workspace_id: str,
        today: date,
        start_day: str,
        trend_days: int,
    ) -> list[dict[str, Any]]:
        inbound = _group_counts(
            conn,
            "SELECT date(created_at, '+8 hours') AS day, COUNT(*) AS total"
            " FROM profit_activity_records"
            " WHERE workspace_id = ? AND date(created_at, '+8 hours') >= ?"
            " GROUP BY day",
            (workspace_id, start_day),
        )
        processed = _group_counts(
            conn,
            "SELECT date(created_at, '+8 hours') AS day, COUNT(*) AS total"
            " FROM product_processing_tasks"
            " WHERE workspace_id = ? AND date(created_at, '+8 hours') >= ?"
            " GROUP BY day",
            (workspace_id, start_day),
        )
        points: list[dict[str, Any]] = []
        for offset in range(trend_days):
            day = (today - timedelta(days=trend_days - 1 - offset)).isoformat()
            points.append(
                {
                    "date": day,
                    "label": day[5:],
                    "inbound": inbound.get(day, 0),
                    "processed": processed.get(day, 0),
                }
            )
        return points

    def _task_status(self, conn: Any, workspace_id: str) -> list[dict[str, Any]]:
        counts = _group_counts(
            conn,
            "SELECT status AS day, COUNT(*) AS total FROM product_processing_tasks"
            " WHERE workspace_id = ? GROUP BY status",
            (workspace_id,),
        )
        ordered = [status for status in TASK_STATUS_ORDER if counts.get(status)]
        ordered += [status for status in counts if status not in TASK_STATUS_ORDER]
        return [
            {
                "status": status,
                "label": TASK_STATUS_LABELS.get(status, status),
                "count": counts[status],
            }
            for status in ordered
        ]

    def _site_distribution(self, conn: Any, workspace_id: str) -> list[dict[str, Any]]:
        counts = _group_counts(
            conn,
            "SELECT site_code AS day, COUNT(*) AS total FROM profit_activity_records"
            " WHERE workspace_id = ? GROUP BY site_code",
            (workspace_id,),
        )
        return [
            {"site_code": site, "label": site, "count": count}
            for site, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        ]

    def _recent_tasks(self, conn: Any, workspace_id: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT id, title, status, total_count, success_count, failed_count, skipped_count,"
            " created_at, updated_at FROM product_processing_tasks"
            " WHERE workspace_id = ? ORDER BY created_at DESC LIMIT ?",
            (workspace_id, RECENT_TASK_LIMIT),
        ).fetchall()
        return [
            {
                "task_id": int(row["id"]),
                "title": row["title"] or "",
                "status": row["status"],
                "status_label": TASK_STATUS_LABELS.get(row["status"], row["status"]),
                "total_count": int(row["total_count"] or 0),
                "success_count": int(row["success_count"] or 0),
                "failed_count": int(row["failed_count"] or 0),
                "skipped_count": int(row["skipped_count"] or 0),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]


def _beijing_today() -> date:
    return datetime.now(BEIJING).date()


def _count(conn: Any, sql: str, args: tuple[Any, ...]) -> int:
    row = conn.execute(sql, args).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _group_counts(conn: Any, sql: str, args: tuple[Any, ...]) -> dict[str, int]:
    return {
        str(row["day"]): int(row["total"])
        for row in conn.execute(sql, args).fetchall()
        if row["day"] not in (None, "")
    }
