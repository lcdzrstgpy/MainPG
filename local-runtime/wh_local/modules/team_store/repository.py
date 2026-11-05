"""团队服务的 SQLite 存储层。

账号画像、店铺登记、每周汇总与查重标记都走这里。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .dedupe import should_overwrite
from .rules import StoreCodeAllocator
from .schema import initialize

# 与 team_store_daily 的列顺序保持一致，upsert 时按这个顺序拼 SQL。
_DAILY_NUMERIC_FIELDS = (
    "order_count",
    "sub_order_count",
    "gmv_cny",
    "refund_cny",
    "profit_cny",
    "settlement_payback_cny",
    "finance_expense_cny",
    "exposure",
    "clicks",
    "visitors",
    "paid_items",
    "sales_today",
    "pending_shipment",
    "active_products",
)

# 利润依赖员工自己填的成本，没有数据时必须留空，不能被当成 0。
_DAILY_NULLABLE_FIELDS = frozenset({"profit_cny"})


def _daily_value(field: str, value: Any) -> Any:
    if value is None or value == "":
        return None if field in _DAILY_NULLABLE_FIELDS else 0
    try:
        return float(value)
    except (TypeError, ValueError):
        return None if field in _DAILY_NULLABLE_FIELDS else 0


class TeamRepository:
    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            initialize(conn)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_profile(self, account_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM team_account_profiles WHERE account_id = ?",
                (account_id,),
            ).fetchone()
        return dict(row) if row else None

    def find_profile_by_staff_no(self, staff_no_norm: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM team_account_profiles WHERE staff_no_norm = ?",
                (staff_no_norm,),
            ).fetchone()
        return dict(row) if row else None

    def find_profile_by_username(self, username: str) -> dict[str, Any] | None:
        """按登录账号找画像（关联账号时允许填登录账号而不是 cust_ 开头的 ID）。"""
        text = str(username or "").strip()
        if not text:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM team_account_profiles "
                "WHERE lower(username) = lower(?) ORDER BY updated_at DESC LIMIT 1",
                (text,),
            ).fetchone()
        return dict(row) if row else None

    def update_profile_username(self, account_id: str, username: str, now: str) -> bool:
        """补写/更新画像里的登录账号，供别人按登录账号找到自己。"""
        text = str(username or "").strip()
        if not text:
            return False
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE team_account_profiles SET username = ?, updated_at = ? "
                "WHERE account_id = ? AND username <> ?",
                (text, now, account_id, text),
            )
        return cursor.rowcount > 0

    def list_profiles(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM team_account_profiles ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]


    def save_profile(
        self,
        account_id: str,
        *,
        username: str,
        staff_no: str,
        staff_no_norm: str,
        now: str,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO team_account_profiles "
                "(account_id, username, staff_no, staff_no_norm, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(account_id) DO UPDATE SET "
                "username = excluded.username, staff_no = excluded.staff_no, "
                "staff_no_norm = excluded.staff_no_norm, updated_at = excluded.updated_at",
                (account_id, username, staff_no, staff_no_norm, now, now),
            )
            row = conn.execute(
                "SELECT * FROM team_account_profiles WHERE account_id = ?",
                (account_id,),
            ).fetchone()
        return dict(row) if row else {}

    def get_store_by_code(self, store_code: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM team_stores WHERE store_code = ?", (str(store_code or ""),)
            ).fetchone()
        return dict(row) if row else None

    def delete_store(self, store_code: str) -> dict[str, int]:
        """删除店铺及其汇总、明细、绑定、标记；店铺码不回收。"""
        code = str(store_code or "").strip()
        counts: dict[str, int] = {}
        if not code:
            return counts
        with self._connect() as conn:
            for table in (
                "team_store_daily",
                "team_store_records",
                "team_store_bindings",
                "team_dedupe_flags",
            ):
                cursor = conn.execute(
                    f"DELETE FROM {table} WHERE store_code = ?", (code,)
                )
                counts[table] = max(0, cursor.rowcount)
            cursor = conn.execute(
                "DELETE FROM team_stores WHERE store_code = ?", (code,)
            )
            counts["team_stores"] = max(0, cursor.rowcount)
        return counts

    def find_store_by_name(self, store_name_norm: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM team_stores WHERE store_name_norm = ?",
                (store_name_norm,),
            ).fetchone()
        return dict(row) if row else None

    def get_store(self, store_code: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM team_stores WHERE store_code = ?",
                (store_code,),
            ).fetchone()
        return dict(row) if row else None

    def create_store(
        self,
        *,
        store_name: str,
        store_name_norm: str,
        owner_account_id: str,
        platform: str,
        mall_id: str,
        now: str,
    ) -> dict[str, Any]:
        """发号并落库，发号和插入在同一个事务里，避免并发重号。"""
        with self._connect() as conn:
            store_code = StoreCodeAllocator(conn).allocate()
            conn.execute(
                "INSERT INTO team_stores "
                "(store_code, store_name, store_name_norm, owner_account_id, "
                " platform, mall_id, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                (
                    store_code,
                    store_name,
                    store_name_norm,
                    owner_account_id,
                    platform,
                    mall_id,
                    now,
                    now,
                ),
            )
            conn.execute(
                "INSERT OR IGNORE INTO team_store_bindings "
                "(store_code, account_id, role, bound_at) VALUES (?, ?, 'owner', ?)",
                (store_code, owner_account_id, now),
            )
            row = conn.execute(
                "SELECT * FROM team_stores WHERE store_code = ?", (store_code,)
            ).fetchone()
        return dict(row) if row else {}

    def fill_mall_id(self, store_code: str, mall_id: str, now: str) -> None:
        """店铺第一次带上 Temu 店铺 ID 时补写，已有值不覆盖。"""
        with self._connect() as conn:
            conn.execute(
                "UPDATE team_stores SET mall_id = ?, updated_at = ? "
                "WHERE store_code = ? AND mall_id = ''",
                (mall_id, now, store_code),
            )

    def bind_account(self, store_code: str, account_id: str, role: str, now: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO team_store_bindings (store_code, account_id, role, bound_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(store_code, account_id) DO NOTHING",
                (store_code, account_id, role, now),
            )

    def list_stores_for_account(self, account_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT s.*, b.role, b.bound_at FROM team_store_bindings b "
                "JOIN team_stores s ON s.store_code = b.store_code "
                "WHERE b.account_id = ? ORDER BY s.created_at, s.store_code",
                (account_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_flag(
        self,
        *,
        kind: str,
        account_id: str,
        store_code: str,
        other_store_code: str,
        data_key: str,
        detail: str,
        now: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO team_dedupe_flags "
                "(kind, account_id, store_code, other_store_code, data_key, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(kind, account_id, store_code, data_key) DO UPDATE SET "
                "other_store_code = excluded.other_store_code, "
                "detail = excluded.detail, created_at = excluded.created_at",
                (kind, account_id, store_code, other_store_code, data_key, detail, now),
            )

    def list_flags(self, account_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM team_dedupe_flags WHERE account_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (account_id, int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_flags_for_admin(self, limit: int = 200) -> list[dict[str, Any]]:
        """运维视角：不按账号过滤，看全部标记。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM team_dedupe_flags ORDER BY created_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    # ---------- 店铺日汇总（每周提交） ----------

    def get_store_daily(self, store_code: str, stat_date: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM team_store_daily WHERE store_code = ? AND stat_date = ?",
                (store_code, stat_date),
            ).fetchone()
        return dict(row) if row else None

    def upsert_store_daily(
        self,
        *,
        store_code: str,
        stat_date: str,
        source_account_id: str,
        source_updated_at: str,
        source_changed: bool,
        metrics: dict[str, Any],
        now: str,
    ) -> dict[str, Any]:
        """写入一天的汇总。

        同一店铺同一天只保留一份，源数据更新更晚的才覆盖；
        ``source_changed`` 由调用方判断「上一份是别的账号上报的」。
        """
        previous = self.get_store_daily(store_code, stat_date)
        if previous is not None and not should_overwrite(
            previous.get("source_updated_at"), source_updated_at
        ):
            return {"state": "stale", "previous": previous}
        values = {field: _daily_value(field, metrics.get(field)) for field in _DAILY_NUMERIC_FIELDS}
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO team_store_daily ("
                "store_code, stat_date, source_account_id, source_updated_at, "
                + ", ".join(_DAILY_NUMERIC_FIELDS)
                + ", pushed_at) VALUES ("
                + ", ".join(["?"] * (4 + len(_DAILY_NUMERIC_FIELDS) + 1))
                + ") ON CONFLICT(store_code, stat_date) DO UPDATE SET "
                "source_account_id = excluded.source_account_id, "
                "source_updated_at = excluded.source_updated_at, "
                + ", ".join(
                    f"{field} = excluded.{field}" for field in _DAILY_NUMERIC_FIELDS
                )
                + ", pushed_at = excluded.pushed_at",
                (
                    store_code,
                    stat_date,
                    source_account_id,
                    source_updated_at,
                    *[values[field] for field in _DAILY_NUMERIC_FIELDS],
                    now,
                ),
            )
        return {
            "state": "updated" if previous is not None else "created",
            "previous": previous,
            "source_changed": source_changed,
        }

    def save_submission(
        self,
        *,
        submission_id: str,
        account_id: str,
        week_start: str,
        submitted_at: str,
        store_count: int,
        day_rows: int,
    ) -> dict[str, Any]:
        """同一账号同一周只留一张提交单，重复提交视为补交并刷新统计。"""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO team_submissions "
                "(submission_id, account_id, week_start, submitted_at, "
                " store_count, day_rows, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'accepted') "
                "ON CONFLICT(account_id, week_start) DO UPDATE SET "
                "submitted_at = excluded.submitted_at, "
                "store_count = excluded.store_count, "
                "day_rows = excluded.day_rows, status = 'accepted'",
                (submission_id, account_id, week_start, submitted_at, store_count, day_rows),
            )
            row = conn.execute(
                "SELECT * FROM team_submissions WHERE account_id = ? AND week_start = ?",
                (account_id, week_start),
            ).fetchone()
        return dict(row) if row else {}

    def list_submissions(self, account_id: str, limit: int = 12) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM team_submissions WHERE account_id = ? "
                "ORDER BY week_start DESC LIMIT ?",
                (account_id, int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def summarize_daily(
        self,
        *,
        start: str,
        end: str,
        store_codes: list[str] | None = None,
        group_by: str = "shop",
    ) -> list[dict[str, Any]]:
        """按店铺 / 账号 / 日期汇总一段时间的数据。"""
        params: list[Any] = [start, end]
        clause = ""
        if store_codes:
            clause = f" AND d.store_code IN ({','.join('?' * len(store_codes))})"
            params.extend(store_codes)

        if group_by == "account":
            select = (
                "COALESCE(s.owner_account_id, '') AS group_key, "
                "COALESCE(p.staff_no, '') AS staff_no, "
                "COALESCE(p.username, '') AS username, "
                "COUNT(DISTINCT d.store_code) AS store_count, "
            )
            join = "LEFT JOIN team_account_profiles p ON p.account_id = s.owner_account_id "
            group = "GROUP BY s.owner_account_id ORDER BY gmv_cny DESC"
        elif group_by == "date":
            select = "d.stat_date AS group_key, "
            join = ""
            group = "GROUP BY d.stat_date ORDER BY d.stat_date"
        else:
            select = (
                "d.store_code AS group_key, "
                "COALESCE(s.store_name, '') AS store_name, "
                "COALESCE(s.owner_account_id, '') AS owner_account_id, "
            )
            join = ""
            group = "GROUP BY d.store_code ORDER BY gmv_cny DESC"

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT " + select +
                "COUNT(*) AS day_count, "
                "SUM(d.order_count) AS order_count, "
                "SUM(d.sub_order_count) AS sub_order_count, "
                "SUM(d.gmv_cny) AS gmv_cny, "
                "SUM(d.refund_cny) AS refund_cny, "
                "SUM(d.profit_cny) AS profit_cny, "
                "SUM(d.settlement_payback_cny) AS settlement_payback_cny, "
                "SUM(d.finance_expense_cny) AS finance_expense_cny, "
                "SUM(d.exposure) AS exposure, "
                "SUM(d.clicks) AS clicks, "
                "SUM(d.visitors) AS visitors, "
                "MAX(d.stat_date) AS last_stat_date "
                "FROM team_store_daily d "
                "LEFT JOIN team_stores s ON s.store_code = d.store_code "
                + join +
                "WHERE d.stat_date BETWEEN ? AND ?" + clause + " " + group,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    # ---------- 组长视角 ----------

    def leader_group_ids(self, account_id: str) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT group_id FROM team_groups WHERE leader_account_id = ?",
                (account_id,),
            ).fetchall()
        return [str(row["group_id"]) for row in rows]

    def member_account_ids(self, group_ids: list[str]) -> list[str]:
        if not group_ids:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT account_id FROM team_members "
                "WHERE status = 'active' AND group_id IN ("
                + ",".join("?" * len(group_ids)) + ")",
                group_ids,
            ).fetchall()
        return [str(row["account_id"]) for row in rows]

    def store_codes_for_accounts(self, account_ids: list[str]) -> list[str]:
        """这些账号可访问的店铺（自己是 owner，或被授权为 helper）。"""
        if not account_ids:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT store_code FROM team_store_bindings WHERE account_id IN ("
                + ",".join("?" * len(account_ids)) + ")",
                account_ids,
            ).fetchall()
        return [str(row["store_code"]) for row in rows]

    # ---------- 账号关联 ----------

    def list_link_rows(self, account_id: str) -> list[dict[str, Any]]:
        """我发出的关联（含待同意的申请）。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT target_account_id, status, created_at, updated_at "
                "FROM team_account_links WHERE linker_account_id = ? ORDER BY created_at",
                (account_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_links(self, account_id: str, status: str = "accepted") -> list[str]:
        """我关联的账号；status 为空时返回全部状态。"""
        rows = self.list_link_rows(account_id)
        if status:
            rows = [row for row in rows if str(row.get("status")) == status]
        return [str(row["target_account_id"]) for row in rows]

    def list_linker_rows(self, account_id: str, status: str = "accepted") -> list[dict[str, Any]]:
        """谁关联了我（默认只看已同意的）。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT linker_account_id, status, created_at, updated_at "
                "FROM team_account_links WHERE target_account_id = ? ORDER BY created_at",
                (account_id,),
            ).fetchall()
        result = [dict(row) for row in rows]
        if status:
            result = [row for row in result if str(row.get("status")) == status]
        return result

    def list_linkers(self, account_id: str, status: str = "accepted") -> list[str]:
        return [
            str(row["linker_account_id"])
            for row in self.list_linker_rows(account_id, status)
        ]

    def get_link(self, linker_account_id: str, target_account_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM team_account_links "
                "WHERE linker_account_id = ? AND target_account_id = ?",
                (linker_account_id, target_account_id),
            ).fetchone()
        return dict(row) if row else None

    def add_link(
        self,
        linker_account_id: str,
        target_account_id: str,
        now: str,
        status: str = "pending",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO team_account_links "
                "(linker_account_id, target_account_id, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(linker_account_id, target_account_id) DO NOTHING",
                (linker_account_id, target_account_id, status, now, now),
            )

    def set_link_status(
        self, linker_account_id: str, target_account_id: str, status: str, now: str
    ) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE team_account_links SET status = ?, updated_at = ? "
                "WHERE linker_account_id = ? AND target_account_id = ?",
                (status, now, linker_account_id, target_account_id),
            )
        return cursor.rowcount > 0

    def remove_link(self, linker_account_id: str, target_account_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM team_account_links "
                "WHERE linker_account_id = ? AND target_account_id = ?",
                (linker_account_id, target_account_id),
            )
        return cursor.rowcount > 0

    def profiles_by_ids(self, account_ids: list[str]) -> dict[str, dict[str, Any]]:
        ids = [str(item) for item in account_ids if str(item)]
        if not ids:
            return {}
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT account_id, username, staff_no FROM team_account_profiles "
                "WHERE account_id IN (" + ",".join("?" * len(ids)) + ")",
                ids,
            ).fetchall()
        return {str(row["account_id"]): dict(row) for row in rows}

    def list_stores_by_codes(self, store_codes: list[str]) -> list[dict[str, Any]]:
        if not store_codes:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT s.*, COALESCE(p.staff_no, '') AS staff_no, "
                "COALESCE(p.username, '') AS username, "
                "(SELECT MAX(d.stat_date) FROM team_store_daily d "
                " WHERE d.store_code = s.store_code) AS last_stat_date, "
                "(SELECT COUNT(*) FROM team_dedupe_flags f "
                " WHERE f.store_code = s.store_code) AS flag_count "
                "FROM team_stores s "
                "LEFT JOIN team_account_profiles p ON p.account_id = s.owner_account_id "
                "WHERE s.store_code IN (" + ",".join("?" * len(store_codes)) + ") "
                "ORDER BY s.created_at",
                store_codes,
            ).fetchall()
        return [dict(row) for row in rows]

    def daily_series(
        self,
        *,
        store_codes: list[str],
        start: str = "",
        end: str = "",
    ) -> list[dict[str, Any]]:
        """按「店铺 × 日期」取每日指标，用于多店铺趋势图。"""
        codes = [str(code).strip() for code in store_codes if str(code).strip()]
        if not codes:
            return []
        clauses = ["d.store_code IN (" + ",".join("?" * len(codes)) + ")"]
        params: list[Any] = list(codes)
        if start and end:
            clauses.insert(0, "d.stat_date BETWEEN ? AND ?")
            params = [start, end, *codes]
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT d.store_code, COALESCE(s.store_name, d.store_code) AS store_name, "
                "d.stat_date, d.order_count, d.sub_order_count, d.gmv_cny, "
                "d.settlement_payback_cny, d.profit_cny, d.refund_cny, d.finance_expense_cny "
                "FROM team_store_daily d "
                "LEFT JOIN team_stores s ON s.store_code = d.store_code "
                "WHERE " + " AND ".join(clauses) + " "
                "ORDER BY d.stat_date",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def create_group(self, *, group_id: str, name: str, leader_account_id: str, now: str) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO team_groups (group_id, name, leader_account_id, created_at) "
                "VALUES (?, ?, ?, ?)",
                (group_id, name, leader_account_id, now),
            )
        return {"group_id": group_id, "name": name, "leader_account_id": leader_account_id}

    def add_member(self, *, group_id: str, account_id: str, now: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO team_members (group_id, account_id, status, granted_at) "
                "VALUES (?, ?, 'active', ?) "
                "ON CONFLICT(group_id, account_id) DO UPDATE SET status = 'active'",
                (group_id, account_id, now),
            )

    def remove_member(self, *, group_id: str, account_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE team_members SET status = 'revoked' "
                "WHERE group_id = ? AND account_id = ?",
                (group_id, account_id),
            )

    def list_groups(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT g.*, "
                "(SELECT COUNT(*) FROM team_members m "
                " WHERE m.group_id = g.group_id AND m.status = 'active') AS member_count "
                "FROM team_groups g ORDER BY g.created_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def summarize_range(
        self,
        *,
        start: str,
        end: str,
        store_codes: list[str],
        group_by: str = "shop",
    ) -> list[dict[str, Any]]:
        """按店铺 / 账号 / 日期三种口径汇总一段时间的数据。"""
        if not store_codes:
            return []
        columns = {
            "shop": ("d.store_code", "COALESCE(s.store_name, d.store_code)"),
            "account": ("s.owner_account_id", "COALESCE(p.staff_no, p.username, s.owner_account_id)"),
            "date": ("d.stat_date", "d.stat_date"),
        }
        key_expr, label_expr = columns.get(group_by, columns["shop"])
        order_expr = "d.stat_date" if group_by == "date" else "gmv_cny DESC"
        # 起止为空表示「所有日期」，不加时间过滤。
        clauses = ["d.store_code IN (" + ",".join("?" * len(store_codes)) + ")"]
        params: list[Any] = list(store_codes)
        if start and end:
            clauses.insert(0, "d.stat_date BETWEEN ? AND ?")
            params = [start, end, *store_codes]
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT " + key_expr + " AS key, " + label_expr + " AS label, "
                "COUNT(*) AS day_count, "
                "SUM(d.order_count) AS order_count, "
                "SUM(d.sub_order_count) AS sub_order_count, "
                "SUM(d.gmv_cny) AS gmv_cny, "
                "SUM(d.refund_cny) AS refund_cny, "
                "SUM(d.profit_cny) AS profit_cny, "
                "SUM(d.settlement_payback_cny) AS settlement_payback_cny, "
                "SUM(d.finance_expense_cny) AS finance_expense_cny, "
                "SUM(d.exposure) AS exposure, "
                "SUM(d.clicks) AS clicks, "
                "SUM(d.visitors) AS visitors, "
                "SUM(d.sales_today) AS sales_today "
                "FROM team_store_daily d "
                "LEFT JOIN team_stores s ON s.store_code = d.store_code "
                "LEFT JOIN team_account_profiles p ON p.account_id = s.owner_account_id "
                "WHERE " + " AND ".join(clauses) + " "
                "GROUP BY " + key_expr + " ORDER BY " + order_expr,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    # ---------- 店铺明细 ----------

    def replace_store_records(
        self,
        *,
        store_code: str,
        records: list[dict[str, Any]],
        source_account_id: str = "",
        now: str,
    ) -> int:
        """按店铺整体替换明细；客户端每次提交都是全量，先清后写不会留旧行。"""
        code = str(store_code or "").strip()
        if not code:
            return 0
        rows: list[tuple[Any, ...]] = []
        for item in records or []:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "").strip()
            key = str(item.get("key") or "").strip()
            if not kind or not key:
                continue
            payload = item.get("payload")
            rows.append(
                (
                    code,
                    kind,
                    key,
                    str(item.get("stat_date") or "")[:10],
                    json.dumps(payload if isinstance(payload, dict) else {}, ensure_ascii=False),
                    source_account_id,
                    now,
                )
            )
        with self._connect() as conn:
            conn.execute("DELETE FROM team_store_records WHERE store_code = ?", (code,))
            if rows:
                conn.executemany(
                    "INSERT INTO team_store_records "
                    "(store_code, kind, record_key, stat_date, payload, source_account_id, pushed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(store_code, kind, record_key) DO UPDATE SET "
                    "stat_date = excluded.stat_date, payload = excluded.payload, "
                    "source_account_id = excluded.source_account_id, pushed_at = excluded.pushed_at",
                    rows,
                )
        return len(rows)

    # ---------- 采集批次 ----------

    @staticmethod
    def _to_int(value: Any) -> int:
        try:
            return int(float(value or 0))
        except (TypeError, ValueError):
            return 0

    def replace_capture_batches(
        self,
        account_id: str,
        *,
        batches: list[dict[str, Any]],
        now: str,
    ) -> int:
        """整账号替换最近一批入库记录；客户端每次提交都是最近 50 条的全量。"""
        account = str(account_id or "").strip()
        if not account:
            return 0
        rows: list[tuple[Any, ...]] = []
        for item in batches or []:
            if not isinstance(item, dict):
                continue
            batch_id = str(item.get("batch_id") or "").strip()
            if not batch_id:
                continue
            rows.append(
                (
                    account,
                    batch_id,
                    str(item.get("capture_mode") or ""),
                    self._to_int(item.get("order_count")),
                    self._to_int(item.get("sub_order_count")),
                    self._to_int(item.get("item_count")),
                    str(item.get("captured_at") or ""),
                    now,
                )
            )
        with self._connect() as conn:
            conn.execute("DELETE FROM team_capture_batches WHERE account_id = ?", (account,))
            if rows:
                conn.executemany(
                    "INSERT INTO team_capture_batches "
                    "(account_id, batch_id, capture_mode, order_count, sub_order_count, "
                    "item_count, captured_at, pushed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
        return len(rows)

    def list_capture_batches(self, account_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        account = str(account_id or "").strip()
        if not account:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT batch_id, capture_mode, order_count, sub_order_count, item_count, "
                "captured_at, pushed_at FROM team_capture_batches "
                "WHERE account_id = ? ORDER BY captured_at DESC, batch_id DESC LIMIT ?",
                (account, max(1, min(int(limit or 50), 200))),
            ).fetchall()
        return [dict(row) for row in rows]

    def query_records(
        self,
        *,
        kinds: list[str],
        store_codes: list[str],
        start: str = "",
        end: str = "",
        limit: int = 2000,
    ) -> list[dict[str, Any]]:
        """按明细类型 / 店铺 / 日期取记录，返回已解析好的 payload。"""
        kinds = [str(kind).strip() for kind in kinds if str(kind).strip()]
        codes = [str(code).strip() for code in store_codes if str(code).strip()]
        if not kinds or not codes:
            return []
        clauses = [
            "kind IN (" + ",".join("?" * len(kinds)) + ")",
            "store_code IN (" + ",".join("?" * len(codes)) + ")",
        ]
        params: list[Any] = [*kinds, *codes]
        if start and end:
            clauses.append("stat_date BETWEEN ? AND ?")
            params.extend([start, end])
        params.append(max(1, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT store_code, kind, record_key, stat_date, payload, pushed_at "
                "FROM team_store_records WHERE " + " AND ".join(clauses) + " "
                "ORDER BY stat_date DESC, record_key LIMIT ?",
                params,
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload"] or "{}")
            except (TypeError, ValueError):
                payload = {}
            result.append(
                {
                    "store_code": row["store_code"],
                    "kind": row["kind"],
                    "key": row["record_key"],
                    "stat_date": row["stat_date"],
                    "payload": payload,
                    "pushed_at": row["pushed_at"],
                }
            )
        return result



