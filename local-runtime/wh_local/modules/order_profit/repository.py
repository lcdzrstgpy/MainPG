"""订单利润 SQLite 仓储。

只读写 order_profit_* 表；采购成本只读 sourcing_ledger 的货源表。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger("wh_local.order_profit.repository")

_MIGRATIONS = Path(__file__).with_name("migrations")

_SHOP_TABLES = (
    "order_profit_items",
    "order_settlements",
    "finance_entries",
    "store_metrics",
    "traffic_snapshots",
    "sales_snapshots",
)

# 店铺维度唯一键升级：早期快照表只按 workspace 去重，第二个店铺采集会覆盖第一个店铺的数据。
# 这里为每张表准备「按店铺」重建后的建表语句与列顺序，启动时检测旧唯一键并保留数据重建。
_SHOP_SCOPED_TABLES: dict[str, dict[str, Any]] = {
    "store_metrics": {
        "key": ("workspace_id", "shop_name"),
        "columns": (
            "workspace_id", "shop_name", "pending_shipment", "active_products",
            "sales_today", "sales_7d", "sales_30d", "refund_pending", "cancel_pending",
            "ship_timeliness", "delivery_timeliness", "captured_at", "updated_at",
        ),
        "ddl": """
CREATE TABLE {table} (
    workspace_id TEXT NOT NULL,
    shop_name TEXT NOT NULL DEFAULT '',
    pending_shipment INTEGER NOT NULL DEFAULT 0,
    active_products INTEGER NOT NULL DEFAULT 0,
    sales_today INTEGER NOT NULL DEFAULT 0,
    sales_7d INTEGER NOT NULL DEFAULT 0,
    sales_30d INTEGER NOT NULL DEFAULT 0,
    refund_pending INTEGER NOT NULL DEFAULT 0,
    cancel_pending INTEGER NOT NULL DEFAULT 0,
    ship_timeliness REAL,
    delivery_timeliness REAL,
    captured_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, shop_name)
)""",
    },
    "traffic_snapshots": {
        "key": ("workspace_id", "shop_name", "scope", "period", "stat_date"),
        "columns": (
            "id", "workspace_id", "scope", "period", "stat_date", "exposure", "clicks",
            "visitors", "paid_orders", "paid_items", "click_rate", "click_order_rate",
            "exposure_order_rate", "metrics", "shop_name", "captured_at", "created_at",
            "updated_at",
        ),
        "ddl": """
CREATE TABLE {table} (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'product',
    period TEXT NOT NULL DEFAULT '',
    stat_date TEXT NOT NULL DEFAULT '',
    exposure INTEGER NOT NULL DEFAULT 0,
    clicks INTEGER NOT NULL DEFAULT 0,
    visitors INTEGER NOT NULL DEFAULT 0,
    paid_orders INTEGER NOT NULL DEFAULT 0,
    paid_items INTEGER NOT NULL DEFAULT 0,
    click_rate REAL,
    click_order_rate REAL,
    exposure_order_rate REAL,
    metrics TEXT NOT NULL DEFAULT '{}',
    shop_name TEXT NOT NULL DEFAULT '',
    captured_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, shop_name, scope, period, stat_date)
)""",
    },
    "sales_snapshots": {
        "key": ("workspace_id", "shop_name", "region", "stat_date"),
        "columns": (
            "id", "workspace_id", "region", "stat_date", "metrics", "health",
            "shop_name", "captured_at", "created_at", "updated_at",
        ),
        "ddl": """
CREATE TABLE {table} (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    region TEXT NOT NULL DEFAULT '',
    stat_date TEXT NOT NULL DEFAULT '',
    metrics TEXT NOT NULL DEFAULT '{}',
    health TEXT NOT NULL DEFAULT '{}',
    shop_name TEXT NOT NULL DEFAULT '',
    captured_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, shop_name, region, stat_date)
)""",
    },
    # 财务流水：早期唯一键不含店铺，换个店铺采同一批流水会被当成重复直接丢弃。
    "finance_entries": {
        "key": ("workspace_id", "shop_name", "entry_time", "entry_type", "amount", "note"),
        "columns": (
            "id", "workspace_id", "entry_time", "entry_type", "amount", "currency",
            "note", "category", "shop_name", "captured_at", "created_at", "updated_at",
        ),
        "ddl": """
CREATE TABLE {table} (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    entry_time TEXT NOT NULL DEFAULT '',
    entry_type TEXT NOT NULL DEFAULT '',
    amount REAL NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'CNY',
    note TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'other',
    shop_name TEXT NOT NULL DEFAULT '',
    captured_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, shop_name, entry_time, entry_type, amount, note)
)""",
    },
}

_ITEM_COLUMNS = (
    "workspace_id", "batch_id", "command_id", "parent_order_sn", "sub_order_sn",
    "sku_id", "skc_id", "skc_code", "ext_code", "goods_name", "image_url",
    "quantity", "declared_price", "declared_currency", "gmv", "gmv_cny",
    "gmv_currency", "sales_payback", "sales_reversal", "freight_payback",
    "freight_reversal", "estimated_income", "order_payment", "second_payment",
    "tail_freight", "tail_currency", "tail_freight_cny", "tracking_no",
    "order_status", "order_status_code", "site", "order_time", "shop_name",
    "mall_id", "unit_cost_cny", "purchase_cost_cny", "weight_kg", "cost_source",
    "manual_cost_cny",
    "match_status", "commission_cny", "head_freight_cny", "profit_estimate_cny",
    "profit_rate", "source", "captured_at",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _sales_today(raw: Any) -> int:
    """从销量快照的 metrics JSON 里取「今日总销量」。"""
    try:
        payload = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0
    for key in ("今日总销量", "今日销量", "总销量"):
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            value = value.replace(",", "").rstrip("%").strip()
        amount = _float(value)
        if amount is not None:
            return int(amount)
    return 0


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value + 1e-9, digits)


class OrderProfitRepository:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)
        self.initialize()

    @property
    def database_path(self) -> Path:
        return self._database_path

    def initialize(self) -> None:
        migration_files = sorted(_MIGRATIONS.glob("*.sql"))
        with self._connect() as conn:
            for path in migration_files:
                conn.executescript(path.read_text(encoding="utf-8"))
            self._ensure_store_accounts(conn)
            # 迁移脚本缺失（例如打包漏了 migrations 目录）时不能把整个后端拖崩：
            # 只记录错误并跳过依赖订单利润表的升级步骤。
            if not self._table_exists(conn, "profit_shops"):
                logger.error(
                    "订单利润表未创建：迁移脚本目录 %s 只找到 %d 个 SQL，"
                    "请确认打包包含 order_profit/migrations；本次跳过字段升级",
                    _MIGRATIONS,
                    len(migration_files),
                )
                return
            self._ensure_shop_store_codes(conn)
            self._ensure_item_manual_costs(conn)
            self._upgrade_shop_scoped_tables(conn)
            self._backfill_active_shops(conn)
            conn.commit()

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _ensure_store_accounts(conn: sqlite3.Connection) -> None:
        """店铺账号：用户录入的手机号 + 加密密码，按浏览器实例绑定。"""
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS store_accounts (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                alias TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL,
                password_enc TEXT NOT NULL DEFAULT '',
                region TEXT NOT NULL DEFAULT 'global',
                shop_name TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'idle',
                last_login_at TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                instance_tag TEXT NOT NULL DEFAULT '',
                instance_pid INTEGER NOT NULL DEFAULT 0,
                last_command_id INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (workspace_id, phone)
            );
            CREATE INDEX IF NOT EXISTS idx_store_accounts_workspace
                ON store_accounts(workspace_id);
            """
        )

    @staticmethod
    def _ensure_item_manual_costs(conn: sqlite3.Connection) -> None:
        """订单行支持手工填写采购成本（覆盖台账自动匹配的结果）。"""
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info('order_profit_items')").fetchall()
        }
        if "manual_cost_cny" not in columns:
            conn.execute("ALTER TABLE order_profit_items ADD COLUMN manual_cost_cny REAL")

    @staticmethod
    def _ensure_shop_store_codes(conn: sqlite3.Connection) -> None:
        """给店铺补上团队服务发放的店铺码字段（老库平滑升级）。

        店铺码由远端团队服务按「日期 + 当天顺序」发号；断网建店先用
        ``LOCAL-`` 临时码占位，联网后再换成正式码。
        """
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info('profit_shops')").fetchall()
        }
        additions = {
            "store_code": "TEXT NOT NULL DEFAULT ''",
            "code_status": "TEXT NOT NULL DEFAULT 'local'",
            "staff_no": "TEXT NOT NULL DEFAULT ''",
            "synced_at": "TEXT NOT NULL DEFAULT ''",
        }
        for name, ddl in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE profit_shops ADD COLUMN {name} {ddl}")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_profit_shops_store_code "
            "ON profit_shops(store_code) WHERE store_code <> ''"
        )
        # 升级前建的老店铺没有店铺码，补一个本地临时码，联网后可换成正式码。
        conn.execute(
            "UPDATE profit_shops "
            "SET store_code = 'LOCAL-' || UPPER(HEX(RANDOMBLOB(4))) "
            "WHERE store_code = ''"
        )

    @staticmethod
    def _unique_keys(conn: sqlite3.Connection, table: str) -> list[tuple[str, ...]]:
        keys: list[tuple[str, ...]] = []
        for row in conn.execute(f"PRAGMA index_list('{table}')").fetchall():
            if not row["unique"]:
                continue
            columns = tuple(
                str(info["name"])
                for info in conn.execute(f"PRAGMA index_info('{row['name']}')")
            )
            keys.append(columns)
        return keys

    def _upgrade_shop_scoped_tables(self, conn: sqlite3.Connection) -> None:
        """把只按 workspace 去重的快照表重建为「按店铺」唯一键，并原样搬走已有行。"""
        for table, spec in _SHOP_SCOPED_TABLES.items():
            if tuple(spec["key"]) in self._unique_keys(conn, table):
                continue
            tmp = f"{table}__shop_scope"
            conn.execute(f"DROP TABLE IF EXISTS {tmp}")
            conn.execute(spec["ddl"].replace("{table}", tmp))
            columns = ", ".join(spec["columns"])
            conn.execute(f"INSERT OR IGNORE INTO {tmp} ({columns}) SELECT {columns} FROM {table}")
            conn.execute(f"DROP TABLE {table}")
            conn.execute(f"ALTER TABLE {tmp} RENAME TO {table}")
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_workspace_shop "
                f"ON {table}(workspace_id, shop_name)"
            )

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self._database_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---------- settings ----------
    def settings(self, workspace_id: str) -> dict[str, float]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT usd_cny_rate, commission_rate, head_unit_price_cny "
                "FROM order_profit_settings WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO order_profit_settings "
                    "(workspace_id, usd_cny_rate, commission_rate, head_unit_price_cny, updated_at) "
                    "VALUES (?, 7.2, 0.05, 0, ?)",
                    (workspace_id, _now()),
                )
                return {"usd_cny_rate": 7.2, "commission_rate": 0.05, "head_unit_price_cny": 0.0}
            return {
                "usd_cny_rate": float(row["usd_cny_rate"] or 7.2),
                "commission_rate": float(row["commission_rate"] or 0.05),
                "head_unit_price_cny": float(row["head_unit_price_cny"] or 0),
            }

    def save_settings(
        self,
        workspace_id: str,
        *,
        usd_cny_rate: float,
        commission_rate: float,
        head_unit_price_cny: float,
    ) -> dict[str, float]:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO order_profit_settings "
                "(workspace_id, usd_cny_rate, commission_rate, head_unit_price_cny, updated_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(workspace_id) DO UPDATE SET "
                "usd_cny_rate = excluded.usd_cny_rate, "
                "commission_rate = excluded.commission_rate, "
                "head_unit_price_cny = excluded.head_unit_price_cny, "
                "updated_at = excluded.updated_at",
                (workspace_id, usd_cny_rate, commission_rate, head_unit_price_cny, _now()),
            )
        return self.settings(workspace_id)

    # ---------- batches ----------
    def batch_for_command(self, workspace_id: str, command_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM order_profit_batches WHERE workspace_id = ? AND command_id = ?",
                (workspace_id, command_id),
            ).fetchone()
            return dict(row) if row else None

    def latest_batch(self, workspace_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM order_profit_batches WHERE workspace_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (workspace_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_batches(self, workspace_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM order_profit_batches WHERE workspace_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (workspace_id, max(1, min(limit, 100))),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _next_batch_id(conn: sqlite3.Connection, workspace_id: str) -> str:
        """批次号用「日期 + 当日自增」：20261028-01（北京时间）。"""
        today = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y%m%d")
        row = conn.execute(
            "SELECT id FROM order_profit_batches WHERE workspace_id = ? AND id LIKE ? "
            "ORDER BY id DESC LIMIT 1",
            (workspace_id, f"{today}-%"),
        ).fetchone()
        seq = 1
        if row:
            tail = str(row[0]).rsplit("-", 1)[-1]
            if tail.isdigit():
                seq = int(tail) + 1
        return f"{today}-{seq:02d}"

    def create_batch(
        self,
        workspace_id: str,
        *,
        command_id: int,
        capture_mode: str,
        order_count: int,
        sub_order_count: int,
        item_count: int,
        captured_at: str,
    ) -> dict[str, Any]:
        existing = self.batch_for_command(workspace_id, command_id)
        if existing:
            return existing
        now = _now()
        batch_id = ""
        with self._connect() as conn:
            # 并发入库时同日序号可能撞车，撞了就重新取下一个序号。
            for _attempt in range(5):
                batch_id = self._next_batch_id(conn, workspace_id)
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO order_profit_batches "
                    "(id, workspace_id, command_id, capture_mode, status, order_count, "
                    "sub_order_count, item_count, captured_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, 'ingested', ?, ?, ?, ?, ?, ?)",
                    (batch_id, workspace_id, command_id, capture_mode, order_count,
                     sub_order_count, item_count, captured_at, now, now),
                )
                if cursor.rowcount:
                    break
        return self.batch_for_command(workspace_id, command_id) or {
            "id": batch_id, "workspace_id": workspace_id, "command_id": command_id,
            "capture_mode": capture_mode, "status": "ingested",
            "order_count": order_count, "sub_order_count": sub_order_count,
            "item_count": item_count, "captured_at": captured_at,
        }

    # ---------- items ----------
    def _item_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for key in (
            "declared_price", "gmv", "gmv_cny", "sales_payback", "sales_reversal",
            "freight_payback", "freight_reversal", "estimated_income", "order_payment",
            "second_payment", "tail_freight", "tail_freight_cny", "unit_cost_cny",
            "purchase_cost_cny", "weight_kg", "commission_cny", "head_freight_cny",
            "profit_estimate_cny", "profit_rate",
        ):
            item[key] = _float(item.get(key))
        item["quantity"] = _int(item.get("quantity"), 1)
        return item

    def find_item(self, workspace_id: str, item: Mapping[str, Any]) -> dict[str, Any] | None:
        parent = _text(item.get("parent_order_sn"))
        sub = _text(item.get("sub_order_sn"))
        sku = _text(item.get("sku_id"))
        ext = _text(item.get("ext_code"))
        with self._connect() as conn:
            row = None
            if parent or sub:
                row = conn.execute(
                    "SELECT * FROM order_profit_items WHERE workspace_id = ? "
                    "AND parent_order_sn = ? AND sub_order_sn = ? AND sku_id = ? LIMIT 1",
                    (workspace_id, parent, sub, sku),
                ).fetchone()
            if row is None and parent and sku:
                # 列表与详情页的子订单号偶尔不一致，父订单 + SKU 是更稳的合并键。
                row = conn.execute(
                    "SELECT * FROM order_profit_items WHERE workspace_id = ? "
                    "AND parent_order_sn = ? AND sku_id = ? "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (workspace_id, parent, sku),
                ).fetchone()
            if row is None and parent and ext:
                row = conn.execute(
                    "SELECT * FROM order_profit_items WHERE workspace_id = ? "
                    "AND parent_order_sn = ? AND ext_code = ? "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (workspace_id, parent, ext),
                ).fetchone()
            if row is None and sub and sku:
                row = conn.execute(
                    "SELECT * FROM order_profit_items WHERE workspace_id = ? "
                    "AND sub_order_sn = ? AND sku_id = ? LIMIT 1",
                    (workspace_id, sub, sku),
                ).fetchone()
            return self._item_from_row(row) if row else None

    def upsert_item(self, workspace_id: str, item: Mapping[str, Any]) -> str:
        values = {key: item.get(key) for key in _ITEM_COLUMNS}
        values["workspace_id"] = workspace_id
        values["quantity"] = _int(values.get("quantity"), 1)
        values["sub_order_sn"] = _text(values.get("sub_order_sn"))
        values["parent_order_sn"] = _text(values.get("parent_order_sn"))
        values["sku_id"] = _text(values.get("sku_id"))
        now = _now()
        existing = self.find_item(workspace_id, values)
        with self._connect() as conn:
            if existing:
                item_id = str(existing["id"])
                assignments = ", ".join(f"{key} = ?" for key in _ITEM_COLUMNS)
                conn.execute(
                    f"UPDATE order_profit_items SET {assignments}, updated_at = ? WHERE id = ?",
                    (*[values.get(key) for key in _ITEM_COLUMNS], now, item_id),
                )
                return item_id
            item_id = uuid.uuid4().hex
            columns = ("id", *_ITEM_COLUMNS, "created_at", "updated_at")
            placeholders = ", ".join("?" for _ in columns)
            conn.execute(
                f"INSERT INTO order_profit_items ({', '.join(columns)}) VALUES ({placeholders})",
                (item_id, *[values.get(key) for key in _ITEM_COLUMNS], now, now),
            )
            return item_id

    def list_items_by_parent(
        self,
        workspace_id: str,
        parent_order_sn: str,
        *,
        shop: str = "",
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT * FROM order_profit_items "
            "WHERE workspace_id = ? AND parent_order_sn = ?"
        )
        params: list[Any] = [workspace_id, _text(parent_order_sn)]
        if _text(shop):
            sql += " AND shop_name = ?"
            params.append(_text(shop))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def list_items(
        self,
        workspace_id: str,
        *,
        month: str = "",
        months: list[str] | None = None,
        date: str = "",
        store: str = "",
        shop: str = "",
        keyword: str = "",
        limit: int = 500,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        where = ["workspace_id = ?"]
        values: list[Any] = [workspace_id]
        time_expr = "COALESCE(NULLIF(order_time, ''), substr(captured_at, 1, 19))"
        if shop:
            where.append("shop_name = ?")
            values.append(_text(shop))
        if month:
            where.append(f"substr({time_expr}, 1, 7) = ?")
            values.append(month)
        if months:
            wanted = [str(item) for item in months if str(item)]
            if wanted:
                where.append(f"substr({time_expr}, 1, 7) IN ({', '.join('?' * len(wanted))})")
                values.extend(wanted)
        if date:
            where.append(f"substr({time_expr}, 1, 10) = ?")
            values.append(date)
        if store:
            where.append("(mall_id = ? OR shop_name = ?)")
            values.extend([store, store])
        if keyword:
            like = f"%{keyword.strip()}%"
            where.append(
                "(parent_order_sn LIKE ? OR sub_order_sn LIKE ? OR sku_id LIKE ? "
                "OR skc_id LIKE ? OR ext_code LIKE ? OR goods_name LIKE ? OR tracking_no LIKE ?)"
            )
            values.extend([like] * 7)
        clause = " AND ".join(where)
        with self._connect() as conn:
            total = int(conn.execute(
                f"SELECT COUNT(*) FROM order_profit_items WHERE {clause}", values
            ).fetchone()[0])
            rows = conn.execute(
                f"SELECT * FROM order_profit_items WHERE {clause} "
                f"ORDER BY {time_expr} DESC, parent_order_sn, sub_order_sn LIMIT ? OFFSET ?",
                (*values, max(1, min(limit, 5000)), max(0, offset)),
            ).fetchall()
        return [self._item_from_row(row) for row in rows], total

    def list_order_months(self, workspace_id: str, *, shop: str = "", limit: int = 24) -> list[str]:
        """按时间倒序列出有订单数据的月份（YYYY-MM），供订单页月份多选。"""
        time_expr = "COALESCE(NULLIF(order_time, ''), substr(captured_at, 1, 19))"
        where = ["workspace_id = ?", f"substr({time_expr}, 1, 7) <> ''"]
        values: list[Any] = [workspace_id]
        if shop:
            where.append("shop_name = ?")
            values.append(_text(shop))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT DISTINCT substr({time_expr}, 1, 7) AS month FROM order_profit_items "
                f"WHERE {' AND '.join(where)} ORDER BY month DESC LIMIT ?",
                (*values, max(1, min(limit, 120))),
            ).fetchall()
        return [str(row["month"]) for row in rows]

    def latest_order_date(self, workspace_id: str) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(substr(COALESCE(NULLIF(order_time, ''), substr(captured_at, 1, 19)), 1, 10)) AS d "
                "FROM order_profit_items WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
        return _text(row["d"]) if row and row["d"] else ""

    def list_stores(self, workspace_id: str, shop: str = "") -> list[dict[str, str]]:
        where = ["workspace_id = ?"]
        values: list[Any] = [workspace_id]
        if shop:
            where.append("shop_name = ?")
            values.append(_text(shop))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT mall_id, shop_name, MAX(site) AS site FROM order_profit_items "
                f"WHERE {' AND '.join(where)} GROUP BY mall_id, shop_name "
                "ORDER BY MAX(updated_at) DESC LIMIT 50",
                values,
            ).fetchall()
        return [
            {
                "id": _text(row["mall_id"]) or _text(row["shop_name"]),
                "name": _text(row["shop_name"]) or _text(row["mall_id"]) or "未命名店铺",
                "site": _text(row["site"]),
            }
            for row in rows
        ]

    def _operation_rows(self, table: str, workspace_id: str, limit: int = 200) -> list[dict[str, Any]]:
        allowed = {
            "operation_link_changes",
            "operation_after_sales",
            "operation_appeals",
            "operation_traffic_weekly",
        }
        if table not in allowed:
            raise ValueError("invalid operation table")
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE workspace_id = ? ORDER BY updated_at DESC LIMIT ?",
                (workspace_id, max(1, min(limit, 1000))),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_link_changes(self, workspace_id: str, limit: int = 200) -> list[dict[str, Any]]:
        return self._operation_rows("operation_link_changes", workspace_id, limit)

    def list_after_sales(self, workspace_id: str, limit: int = 200) -> list[dict[str, Any]]:
        return self._operation_rows("operation_after_sales", workspace_id, limit)

    def list_appeals(self, workspace_id: str, limit: int = 200) -> list[dict[str, Any]]:
        return self._operation_rows("operation_appeals", workspace_id, limit)

    def list_traffic_weekly(self, workspace_id: str, limit: int = 52) -> list[dict[str, Any]]:
        return self._operation_rows("operation_traffic_weekly", workspace_id, limit)

    # ---------- finance / store metrics ----------
    def upsert_finance_entries(
        self,
        workspace_id: str,
        entries: list[Mapping[str, Any]],
        *,
        shop_name: str = "",
        captured_at: str = "",
    ) -> int:
        now = _now()
        inserted = 0
        with self._connect() as conn:
            for entry in entries:
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO finance_entries "
                    "(id, workspace_id, entry_time, entry_type, amount, currency, note, "
                    "category, shop_name, captured_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        uuid.uuid4().hex,
                        workspace_id,
                        _text(entry.get("entry_time")),
                        _text(entry.get("entry_type")),
                        _float(entry.get("amount")) or 0,
                        _text(entry.get("currency")) or "CNY",
                        _text(entry.get("note")),
                        _text(entry.get("category")) or "other",
                        _text(shop_name),
                        _text(captured_at) or now,
                        now,
                        now,
                    ),
                )
                inserted += max(0, cursor.rowcount)
            if _text(shop_name):
                conn.execute(
                    "UPDATE order_profit_items SET shop_name = ?, updated_at = ? "
                    "WHERE workspace_id = ? AND (shop_name = '' OR shop_name IS NULL)",
                    (_text(shop_name), now, workspace_id),
                )
        return inserted

    def finance_totals(
        self,
        workspace_id: str,
        month: str = "",
        shop: str = "",
        months: list[str] | None = None,
    ) -> dict[str, Any]:
        where = ["workspace_id = ?"]
        values: list[Any] = [workspace_id]
        if shop:
            where.append("shop_name = ?")
            values.append(_text(shop))
        if month:
            where.append("substr(entry_time, 1, 7) = ?")
            values.append(month)
        if months:
            wanted = [str(item) for item in months if str(item)]
            if wanted:
                where.append(f"substr(entry_time, 1, 7) IN ({', '.join('?' * len(wanted))})")
                values.extend(wanted)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT entry_type, category, amount FROM finance_entries WHERE {' AND '.join(where)}",
                values,
            ).fetchall()
        totals = {
            "entries": len(rows),
            "inflow": 0.0,
            "outflow": 0.0,
            "ad_cost": 0.0,
            "penalty": 0.0,
            "settlement": 0.0,
            "other_cost": 0.0,
        }
        for row in rows:
            amount = _float(row["amount"]) or 0
            category = _text(row["category"])
            if amount > 0:
                totals["inflow"] += amount
            elif amount < 0:
                totals["outflow"] += -amount
            if category == "promotion":
                totals["ad_cost"] += -amount
            elif category == "penalty":
                totals["penalty"] += -amount
            elif category == "settlement":
                totals["settlement"] += amount
            elif amount < 0:
                totals["other_cost"] += -amount
        for key in ("inflow", "outflow", "ad_cost", "penalty", "settlement", "other_cost"):
            totals[key] = _round(totals[key])
        return totals

    def list_finance_entries(
        self,
        workspace_id: str,
        *,
        month: str = "",
        shop: str = "",
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        where = ["workspace_id = ?"]
        values: list[Any] = [workspace_id]
        if shop:
            where.append("shop_name = ?")
            values.append(_text(shop))
        if month:
            where.append("substr(entry_time, 1, 7) = ?")
            values.append(month)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM finance_entries WHERE {' AND '.join(where)} "
                "ORDER BY entry_time DESC LIMIT ?",
                (*values, max(1, min(limit, 2000))),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["amount"] = _float(item.get("amount"))
            items.append(item)
        return items

    def upsert_store_metrics(
        self,
        workspace_id: str,
        metrics: Mapping[str, Any],
        *,
        captured_at: str = "",
    ) -> dict[str, Any]:
        now = _now()
        shop_name = _text(metrics.get("shop_name"))
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO store_metrics "
                "(workspace_id, shop_name, pending_shipment, active_products, sales_today, "
                "sales_7d, sales_30d, refund_pending, cancel_pending, ship_timeliness, "
                "delivery_timeliness, captured_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(workspace_id, shop_name) DO UPDATE SET "
                "shop_name = excluded.shop_name, "
                "pending_shipment = excluded.pending_shipment, "
                "active_products = excluded.active_products, "
                "sales_today = excluded.sales_today, "
                "sales_7d = excluded.sales_7d, "
                "sales_30d = excluded.sales_30d, "
                "refund_pending = excluded.refund_pending, "
                "cancel_pending = excluded.cancel_pending, "
                "ship_timeliness = excluded.ship_timeliness, "
                "delivery_timeliness = excluded.delivery_timeliness, "
                "captured_at = excluded.captured_at, "
                "updated_at = excluded.updated_at",
                (
                    workspace_id,
                    shop_name,
                    _int(metrics.get("pending_shipment")),
                    _int(metrics.get("active_products")),
                    _int(metrics.get("sales_today")),
                    _int(metrics.get("sales_7d")),
                    _int(metrics.get("sales_30d")),
                    _int(metrics.get("refund_pending")),
                    _int(metrics.get("cancel_pending")),
                    _float(metrics.get("ship_timeliness")),
                    _float(metrics.get("delivery_timeliness")),
                    _text(captured_at) or now,
                    now,
                ),
            )
            if shop_name:
                conn.execute(
                    "UPDATE order_profit_items SET shop_name = ?, updated_at = ? "
                    "WHERE workspace_id = ? AND (shop_name = '' OR shop_name IS NULL)",
                    (shop_name, now, workspace_id),
                )
        return self.latest_store_metrics(workspace_id, shop_name) or {}

    def latest_store_metrics(self, workspace_id: str, shop: str = "") -> dict[str, Any] | None:
        where = ["workspace_id = ?"]
        values: list[Any] = [workspace_id]
        if shop:
            where.append("shop_name = ?")
            values.append(_text(shop))
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM store_metrics WHERE {' AND '.join(where)} "
                "ORDER BY captured_at DESC LIMIT 1",
                values,
            ).fetchone()
        return dict(row) if row else None

    # ---------- 结算数据（按 PO 单） ----------
    def upsert_settlements(
        self,
        workspace_id: str,
        rows: list[Mapping[str, Any]],
        *,
        status: str = "pending",
        shop_name: str = "",
        captured_at: str = "",
    ) -> int:
        now = _now()
        written = 0
        with self._connect() as conn:
            for row in rows:
                po_sn = _text(row.get("parent_order_sn"))
                if not po_sn:
                    continue
                conn.execute(
                    "INSERT INTO order_settlements "
                    "(id, workspace_id, parent_order_sn, status, currency, sales_payback, "
                    "sales_reversal, freight_payback, freight_reversal, declared_price, "
                    "quantity, goods_name, site, shop_name, captured_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(workspace_id, parent_order_sn, status, currency) DO UPDATE SET "
                    "sales_payback = excluded.sales_payback, "
                    "sales_reversal = excluded.sales_reversal, "
                    "freight_payback = excluded.freight_payback, "
                    "freight_reversal = excluded.freight_reversal, "
                    "declared_price = excluded.declared_price, "
                    "quantity = excluded.quantity, "
                    "goods_name = excluded.goods_name, "
                    "shop_name = excluded.shop_name, "
                    "captured_at = excluded.captured_at, "
                    "updated_at = excluded.updated_at",
                    (
                        uuid.uuid4().hex,
                        workspace_id,
                        po_sn,
                        _text(status) or "pending",
                        _text(row.get("currency")) or "CNY",
                        _float(row.get("sales_payback")),
                        _float(row.get("sales_reversal")),
                        _float(row.get("freight_payback")),
                        _float(row.get("freight_reversal")),
                        _float(row.get("declared_price")),
                        _int(row.get("quantity")),
                        _text(row.get("goods_name")),
                        _text(row.get("site")),
                        _text(shop_name),
                        _text(captured_at) or now,
                        now,
                        now,
                    ),
                )
                written += 1
        return written

    def list_settlements(
        self,
        workspace_id: str,
        *,
        status: str = "",
        shop: str = "",
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        where = ["workspace_id = ?"]
        values: list[Any] = [workspace_id]
        if shop:
            where.append("shop_name = ?")
            values.append(_text(shop))
        if status:
            where.append("status = ?")
            values.append(_text(status))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM order_settlements WHERE {' AND '.join(where)} "
                "ORDER BY captured_at DESC, parent_order_sn DESC LIMIT ?",
                (*values, max(1, min(limit, 2000))),
            ).fetchall()
        return [dict(row) for row in rows]

    def settlement_totals(self, workspace_id: str, status: str = "", shop: str = "") -> dict[str, Any]:
        where = ["workspace_id = ?"]
        values: list[Any] = [workspace_id]
        if shop:
            where.append("shop_name = ?")
            values.append(_text(shop))
        if status:
            where.append("status = ?")
            values.append(_text(status))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT sales_payback, sales_reversal, freight_payback, freight_reversal "
                f"FROM order_settlements WHERE {' AND '.join(where)}",
                values,
            ).fetchall()
        totals = {
            "po_count": len(rows),
            "sales_payback": 0.0,
            "sales_reversal": 0.0,
            "freight_payback": 0.0,
            "freight_reversal": 0.0,
        }
        for row in rows:
            totals["sales_payback"] += _float(row["sales_payback"]) or 0
            totals["sales_reversal"] += _float(row["sales_reversal"]) or 0
            totals["freight_payback"] += _float(row["freight_payback"]) or 0
            totals["freight_reversal"] += _float(row["freight_reversal"]) or 0
        totals["total_payback"] = (
            totals["sales_payback"] + totals["freight_payback"]
            - totals["sales_reversal"] - totals["freight_reversal"]
        )
        for key in (
            "sales_payback",
            "sales_reversal",
            "freight_payback",
            "freight_reversal",
            "total_payback",
        ):
            totals[key] = _round(totals[key])
        return totals

    def items_for_parent_order(
        self, workspace_id: str, parent_order_sn: str
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM order_profit_items WHERE workspace_id = ? AND parent_order_sn = ?",
                (workspace_id, _text(parent_order_sn)),
            ).fetchall()
        return [self._item_from_row(row) for row in rows]

    # ---------- 流量快照（经营分析 → 商品流量/店铺流量） ----------
    def upsert_traffic_snapshot(
        self,
        workspace_id: str,
        snapshot: Mapping[str, Any],
        *,
        captured_at: str = "",
    ) -> dict[str, Any]:
        now = _now()
        metrics = snapshot.get("metrics") or {}
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO traffic_snapshots "
                "(id, workspace_id, scope, period, stat_date, exposure, clicks, visitors, "
                "paid_orders, paid_items, click_rate, click_order_rate, exposure_order_rate, "
                "metrics, shop_name, captured_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(workspace_id, shop_name, scope, period, stat_date) DO UPDATE SET "
                "exposure = excluded.exposure, clicks = excluded.clicks, "
                "visitors = excluded.visitors, paid_orders = excluded.paid_orders, "
                "paid_items = excluded.paid_items, click_rate = excluded.click_rate, "
                "click_order_rate = excluded.click_order_rate, "
                "exposure_order_rate = excluded.exposure_order_rate, "
                "metrics = excluded.metrics, shop_name = excluded.shop_name, "
                "captured_at = excluded.captured_at, updated_at = excluded.updated_at",
                (
                    uuid.uuid4().hex,
                    workspace_id,
                    _text(snapshot.get("scope")) or "product",
                    _text(snapshot.get("period")),
                    _text(snapshot.get("stat_date")),
                    _int(snapshot.get("exposure")),
                    _int(snapshot.get("clicks")),
                    _int(snapshot.get("visitors")),
                    _int(snapshot.get("paid_orders")),
                    _int(snapshot.get("paid_items")),
                    _float(snapshot.get("click_rate")),
                    _float(snapshot.get("click_order_rate")),
                    _float(snapshot.get("exposure_order_rate")),
                    json.dumps(metrics, ensure_ascii=False),
                    _text(snapshot.get("shop_name")),
                    _text(captured_at) or now,
                    now,
                    now,
                ),
            )
        return self.latest_traffic_snapshot(
            workspace_id,
            _text(snapshot.get("scope")) or "product",
            _text(snapshot.get("shop_name")),
        ) or {}

    def _traffic_row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        try:
            item["metrics"] = json.loads(item.get("metrics") or "{}")
        except json.JSONDecodeError:
            item["metrics"] = {}
        return item

    def latest_traffic_snapshot(
        self, workspace_id: str, scope: str = "product", shop: str = ""
    ) -> dict[str, Any] | None:
        where = ["workspace_id = ?", "scope = ?"]
        values: list[Any] = [workspace_id, _text(scope) or "product"]
        if shop:
            where.append("shop_name = ?")
            values.append(_text(shop))
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM traffic_snapshots WHERE {' AND '.join(where)} "
                "ORDER BY stat_date DESC, captured_at DESC LIMIT 1",
                values,
            ).fetchone()
        return self._traffic_row(row) if row else None

    def list_traffic_snapshots(
        self, workspace_id: str, *, shop: str = "", limit: int = 30
    ) -> list[dict[str, Any]]:
        where = ["workspace_id = ?"]
        values: list[Any] = [workspace_id]
        if shop:
            where.append("shop_name = ?")
            values.append(_text(shop))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM traffic_snapshots WHERE {' AND '.join(where)} "
                "ORDER BY stat_date DESC, captured_at DESC LIMIT ?",
                (*values, max(1, min(limit, 200))),
            ).fetchall()
        return [self._traffic_row(row) for row in rows]

    # ---------- 销量分析快照 ----------
    def upsert_sales_snapshot(
        self,
        workspace_id: str,
        snapshot: Mapping[str, Any],
        *,
        captured_at: str = "",
    ) -> dict[str, Any]:
        now = _now()
        region = _text(snapshot.get("region")) or "全球"
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sales_snapshots "
                "(id, workspace_id, region, stat_date, metrics, health, shop_name, "
                "captured_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(workspace_id, shop_name, region, stat_date) DO UPDATE SET "
                "metrics = excluded.metrics, health = excluded.health, "
                "shop_name = excluded.shop_name, captured_at = excluded.captured_at, "
                "updated_at = excluded.updated_at",
                (
                    uuid.uuid4().hex,
                    workspace_id,
                    region,
                    _text(snapshot.get("stat_date")),
                    json.dumps(snapshot.get("metrics") or {}, ensure_ascii=False),
                    json.dumps(snapshot.get("health") or {}, ensure_ascii=False),
                    _text(snapshot.get("shop_name")),
                    _text(captured_at) or now,
                    now,
                    now,
                ),
            )
        return self.latest_sales_snapshot(workspace_id, region) or {}

    def _sales_row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for key in ("metrics", "health"):
            try:
                item[key] = json.loads(item.get(key) or "{}")
            except json.JSONDecodeError:
                item[key] = {}
        return item

    def latest_sales_snapshot(
        self, workspace_id: str, region: str = "", shop: str = ""
    ) -> dict[str, Any] | None:
        where = ["workspace_id = ?"]
        values: list[Any] = [workspace_id]
        if shop:
            where.append("shop_name = ?")
            values.append(_text(shop))
        if region:
            where.append("region = ?")
            values.append(_text(region))
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM sales_snapshots WHERE {' AND '.join(where)} "
                "ORDER BY stat_date DESC, captured_at DESC LIMIT 1",
                values,
            ).fetchone()
        return self._sales_row(row) if row else None

    def list_sales_snapshots(
        self, workspace_id: str, *, shop: str = "", limit: int = 30
    ) -> list[dict[str, Any]]:
        where = ["workspace_id = ?"]
        values: list[Any] = [workspace_id]
        if shop:
            where.append("shop_name = ?")
            values.append(_text(shop))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM sales_snapshots WHERE {' AND '.join(where)} "
                "ORDER BY stat_date DESC, captured_at DESC LIMIT ?",
                (*values, max(1, min(limit, 200))),
            ).fetchall()
        return [self._sales_row(row) for row in rows]

    def backfill_shop_name(self, workspace_id: str, shop: str) -> int:
        """把此前未归类（shop_name 为空）的历史数据归到当前店铺。"""
        with self._connect() as conn:
            return self._backfill_shop_name(conn, workspace_id, shop)

    def _backfill_shop_name(self, conn: sqlite3.Connection, workspace_id: str, shop: str) -> int:
        """绑定/切换店铺和启动时共用：未归类数据归到当前店铺。

        绑定店铺之前采集的数据没有店铺名，如果不回填，切换店铺后这些数据在任何店铺下
        都查不到。快照表带「按店铺」唯一键，先清掉同键的旧空行再整体回填，避免唯一冲突。
        """
        name = _text(shop)
        if not name:
            return 0
        now = _now()
        empty = "(shop_name = '' OR shop_name IS NULL)"
        updated = 0
        for table in ("order_profit_items", "order_settlements", "finance_entries"):
            cursor = conn.execute(
                f"UPDATE {table} SET shop_name = ?, updated_at = ? "
                f"WHERE workspace_id = ? AND {empty}",
                (name, now, workspace_id),
            )
            updated += max(0, cursor.rowcount)
        # store_metrics 一个店铺一行：已有该店铺行时丢弃未归类的旧行。
        if conn.execute(
            "SELECT 1 FROM store_metrics WHERE workspace_id = ? AND shop_name = ? LIMIT 1",
            (workspace_id, name),
        ).fetchone():
            conn.execute(
                f"DELETE FROM store_metrics WHERE workspace_id = ? AND {empty}",
                (workspace_id,),
            )
        else:
            cursor = conn.execute(
                f"UPDATE store_metrics SET shop_name = ?, updated_at = ? "
                f"WHERE workspace_id = ? AND {empty}",
                (name, now, workspace_id),
            )
            updated += max(0, cursor.rowcount)
        # 快照表唯一键含店铺，同键的旧空行先删再回填。
        conn.execute(
            f"DELETE FROM traffic_snapshots WHERE workspace_id = ? AND {empty} AND EXISTS ("
            "SELECT 1 FROM traffic_snapshots AS t2 "
            "WHERE t2.workspace_id = traffic_snapshots.workspace_id AND t2.shop_name = ? "
            "AND t2.scope = traffic_snapshots.scope AND t2.period = traffic_snapshots.period "
            "AND t2.stat_date = traffic_snapshots.stat_date)",
            (workspace_id, name),
        )
        cursor = conn.execute(
            f"UPDATE traffic_snapshots SET shop_name = ?, updated_at = ? "
            f"WHERE workspace_id = ? AND {empty}",
            (name, now, workspace_id),
        )
        updated += max(0, cursor.rowcount)
        conn.execute(
            f"DELETE FROM sales_snapshots WHERE workspace_id = ? AND {empty} AND EXISTS ("
            "SELECT 1 FROM sales_snapshots AS t2 "
            "WHERE t2.workspace_id = sales_snapshots.workspace_id AND t2.shop_name = ? "
            "AND t2.region = sales_snapshots.region AND t2.stat_date = sales_snapshots.stat_date)",
            (workspace_id, name),
        )
        cursor = conn.execute(
            f"UPDATE sales_snapshots SET shop_name = ?, updated_at = ? "
            f"WHERE workspace_id = ? AND {empty}",
            (name, now, workspace_id),
        )
        updated += max(0, cursor.rowcount)
        return updated + self._adopt_orphan_shops(conn, workspace_id, name)

    def _adopt_orphan_shops(
        self, conn: sqlite3.Connection, workspace_id: str, target: str
    ) -> int:
        """把挂在不存在的店铺名下的历史数据归到当前店铺。

        店铺改名或重建后，旧数据仍带着旧店铺名，按店铺过滤时这些数据会变成谁都看不到的
        孤儿。这里把它们整体并到当前店铺，键冲突的行先删旧的再改名。
        """
        names = _text(target)
        if not names:
            return 0
        known = {
            _text(row["name"])
            for row in conn.execute(
                "SELECT name FROM profit_shops WHERE workspace_id = ?", (workspace_id,)
            )
        }
        orphans: set[str] = set()
        for table in _SHOP_TABLES:
            for row in conn.execute(
                f"SELECT DISTINCT shop_name FROM {table} WHERE workspace_id = ?",
                (workspace_id,),
            ):
                orphan = _text(row["shop_name"])
                if orphan and orphan not in known:
                    orphans.add(orphan)
        updated = 0
        now = _now()
        for orphan in orphans:
            if orphan == names:
                continue
            # store_metrics 一个店铺一行
            if conn.execute(
                "SELECT 1 FROM store_metrics WHERE workspace_id = ? AND shop_name = ? LIMIT 1",
                (workspace_id, names),
            ).fetchone():
                conn.execute(
                    "DELETE FROM store_metrics WHERE workspace_id = ? AND shop_name = ?",
                    (workspace_id, orphan),
                )
            else:
                cursor = conn.execute(
                    "UPDATE store_metrics SET shop_name = ?, updated_at = ? "
                    "WHERE workspace_id = ? AND shop_name = ?",
                    (names, now, workspace_id, orphan),
                )
                updated += max(0, cursor.rowcount)
            for table, keys in (
                (
                    "traffic_snapshots",
                    "t2.scope = traffic_snapshots.scope AND t2.period = traffic_snapshots.period "
                    "AND t2.stat_date = traffic_snapshots.stat_date",
                ),
                (
                    "sales_snapshots",
                    "t2.region = sales_snapshots.region AND t2.stat_date = sales_snapshots.stat_date",
                ),
            ):
                conn.execute(
                    f"DELETE FROM {table} WHERE workspace_id = ? AND shop_name = ? AND EXISTS ("
                    f"SELECT 1 FROM {table} AS t2 WHERE t2.workspace_id = {table}.workspace_id "
                    f"AND t2.shop_name = ? AND {keys})",
                    (workspace_id, orphan, names),
                )
                cursor = conn.execute(
                    f"UPDATE {table} SET shop_name = ?, updated_at = ? "
                    "WHERE workspace_id = ? AND shop_name = ?",
                    (names, now, workspace_id, orphan),
                )
                updated += max(0, cursor.rowcount)
            for table in ("order_profit_items", "order_settlements", "finance_entries"):
                # 认领孤儿数据时可能和目标店铺已有行撞唯一键（财务流水按金额+备注去重），
                # 用 OR IGNORE 跳过冲突行，避免启动直接抛 IntegrityError。
                cursor = conn.execute(
                    f"UPDATE OR IGNORE {table} SET shop_name = ?, updated_at = ? "
                    "WHERE workspace_id = ? AND shop_name = ?",
                    (names, now, workspace_id, orphan),
                )
                updated += max(0, cursor.rowcount)
        return updated

    def _backfill_active_shops(self, conn: sqlite3.Connection) -> None:
        """启动兜底：把每个工作区里未归类的历史数据挂到它当前绑定的店铺下。"""
        rows = conn.execute(
            "SELECT workspace_id, name FROM profit_shops WHERE is_active = 1"
        ).fetchall()
        for row in rows:
            self._backfill_shop_name(conn, _text(row["workspace_id"]), _text(row["name"]))

    # ---------- 店铺绑定 ----------
    def list_shops(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM profit_shops WHERE workspace_id = ? "
                "ORDER BY is_active DESC, created_at ASC",
                (workspace_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def active_shop(self, workspace_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM profit_shops WHERE workspace_id = ? AND is_active = 1 LIMIT 1",
                (workspace_id,),
            ).fetchone()
        return dict(row) if row else None

    def upsert_shop(
        self,
        workspace_id: str,
        name: str,
        *,
        platform: str = "temu",
        note: str = "",
        activate: bool = False,
        store_code: str = "",
        code_status: str = "local",
        staff_no: str = "",
    ) -> dict[str, Any]:
        now = _now()
        shop_name = _text(name)
        if not shop_name:
            raise ValueError("shop name must not be empty")
        code = _text(store_code)
        status = _text(code_status) or ("official" if code else "local")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, store_code FROM profit_shops WHERE workspace_id = ? AND name = ?",
                (workspace_id, shop_name),
            ).fetchone()
            if row:
                shop_id = str(row["id"])
                conn.execute(
                    "UPDATE profit_shops SET platform = ?, note = ?, updated_at = ? "
                    "WHERE id = ? AND workspace_id = ?",
                    (
                        _text(platform) or "temu",
                        _text(note),
                        now,
                        shop_id,
                        workspace_id,
                    ),
                )
                # 已有正式码不覆盖；本地临时码可以被正式码替换。
                current_code = _text(row["store_code"])
                if code and (current_code == "" or status == "official"):
                    conn.execute(
                        "UPDATE profit_shops SET store_code = ?, code_status = ?, "
                        "staff_no = ?, synced_at = ?, updated_at = ? WHERE id = ?",
                        (code, status, _text(staff_no), now, now, shop_id),
                    )
            else:
                shop_id = uuid.uuid4().hex
                has_active = conn.execute(
                    "SELECT 1 FROM profit_shops WHERE workspace_id = ? AND is_active = 1 LIMIT 1",
                    (workspace_id,),
                ).fetchone()
                conn.execute(
                    "INSERT INTO profit_shops "
                    "(id, workspace_id, name, platform, note, is_active, last_capture_at, "
                    " store_code, code_status, staff_no, synced_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)",
                    (
                        shop_id,
                        workspace_id,
                        shop_name,
                        _text(platform) or "temu",
                        _text(note),
                        1 if (activate or has_active is None) else 0,
                        code or self._local_store_code(),
                        status,
                        _text(staff_no),
                        now if code else "",
                        now,
                        now,
                    ),
                )
            if activate:
                conn.execute(
                    "UPDATE profit_shops SET is_active = CASE WHEN id = ? THEN 1 ELSE 0 END, "
                    "updated_at = ? WHERE workspace_id = ?",
                    (shop_id, now, workspace_id),
                )
        return self.get_shop(workspace_id, shop_id) or {}

    @staticmethod
    def _local_store_code() -> str:
        """断网建店时的临时店铺码，联网后由团队服务换成正式码。"""
        return f"LOCAL-{uuid.uuid4().hex[:8].upper()}"

    def set_shop_code(
        self,
        workspace_id: str,
        shop_id: str,
        *,
        store_code: str,
        staff_no: str = "",
    ) -> dict[str, Any] | None:
        """把本地临时码换成团队服务发放的正式码。"""
        code = _text(store_code)
        if not code:
            raise ValueError("store code must not be empty")
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE profit_shops SET store_code = ?, code_status = 'official', "
                "staff_no = ?, synced_at = ?, updated_at = ? "
                "WHERE workspace_id = ? AND id = ?",
                (code, _text(staff_no), now, now, workspace_id, _text(shop_id)),
            )
        return self.get_shop(workspace_id, shop_id)

    def delete_shops(self, workspace_id: str, shop_ids: list[str]) -> int:
        """按 id 批量删除店铺（只删下拉里的店铺记录，不改订单数据）。"""
        ids = [_text(item) for item in shop_ids if _text(item)]
        if not ids:
            return 0
        marks = ",".join("?" for _ in ids)
        with self._connect() as conn:
            cursor = conn.execute(
                f"DELETE FROM profit_shops WHERE workspace_id = ? AND id IN ({marks})",
                (workspace_id, *ids),
            )
        return max(0, cursor.rowcount)

    def clear_shop_data(self, workspace_id: str, shop_name: str) -> dict[str, int]:
        """清空某个店铺的采集数据，但保留店铺记录和账号绑定。"""
        name = _text(shop_name)
        counts: dict[str, int] = {}
        if not name:
            return counts
        tables = (
            "order_profit_items",
            "order_settlements",
            "store_metrics",
            "traffic_snapshots",
            "sales_snapshots",
            "finance_entries",
            "order_shop_claims",
            "shop_collection_batches",
        )
        with self._connect() as conn:
            existing = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            batch_ids = [
                _text(row["batch_id"])
                for row in conn.execute(
                    "SELECT DISTINCT batch_id FROM order_profit_items "
                    "WHERE workspace_id = ? AND shop_name = ?",
                    (workspace_id, name),
                )
            ]
            for table in tables:
                if table not in existing:
                    continue
                cursor = conn.execute(
                    f"DELETE FROM {table} WHERE workspace_id = ? AND shop_name = ?",
                    (workspace_id, name),
                )
                counts[table] = max(0, cursor.rowcount)
            # 批次表没有店铺字段：清掉已经没有任何明细的批次，避免出现「有批次没数据」。
            removed_batches = 0
            for batch_id in batch_ids:
                if not batch_id:
                    continue
                left = conn.execute(
                    "SELECT COUNT(*) FROM order_profit_items WHERE batch_id = ?",
                    (batch_id,),
                ).fetchone()[0]
                if int(left or 0) > 0:
                    continue
                cursor = conn.execute(
                    "DELETE FROM order_profit_batches WHERE workspace_id = ? AND id = ?",
                    (workspace_id, batch_id),
                )
                removed_batches += max(0, cursor.rowcount)
            counts["order_profit_batches"] = removed_batches
            conn.execute(
                "UPDATE profit_shops SET last_capture_at = '', updated_at = ? "
                "WHERE workspace_id = ? AND name = ?",
                (_now(), workspace_id, name),
            )
        return counts

    def get_shop(self, workspace_id: str, shop_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM profit_shops WHERE workspace_id = ? AND id = ?",
                (workspace_id, _text(shop_id)),
            ).fetchone()
        return dict(row) if row else None

    def activate_shop(self, workspace_id: str, shop_id: str) -> dict[str, Any] | None:
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE profit_shops SET is_active = CASE WHEN id = ? THEN 1 ELSE 0 END, "
                "updated_at = ? WHERE workspace_id = ?",
                (_text(shop_id), now, workspace_id),
            )
        return self.get_shop(workspace_id, shop_id)

    def touch_shop_capture(self, workspace_id: str, name: str) -> None:
        shop_name = _text(name)
        if not shop_name:
            return
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE profit_shops SET last_capture_at = ?, updated_at = ? "
                "WHERE workspace_id = ? AND name = ?",
                (now, now, workspace_id, shop_name),
            )

    # ---------- 每周团队汇总 ----------

    # ---------- 团队明细导出 ----------

    def export_shop_records(self, workspace_id: str, shop_name: str) -> list[dict[str, Any]]:
        """导出某店铺的全部明细，供团队看板展示。

        返回 ``[{kind, key, stat_date, payload}]``：
        ``kind`` 区分订单/财务/结算/流量/销量/指标，``key`` 是团队端的去重键。
        """
        shop = _text(shop_name)
        if not shop:
            return []
        records: list[dict[str, Any]] = []

        def add(kind: str, key: str, stat_date: Any, payload: dict[str, Any]) -> None:
            if not _text(key):
                return
            records.append(
                {
                    "kind": kind,
                    "key": _text(key),
                    "stat_date": _text(stat_date)[:10],
                    "payload": payload,
                }
            )

        with self._connect() as conn:
            for row in conn.execute(
                "SELECT parent_order_sn, sub_order_sn, sku_id, skc_id, skc_code, ext_code, "
                "goods_name, image_url, quantity, declared_price, gmv, gmv_cny, "
                "sales_payback, sales_reversal, freight_payback, freight_reversal, "
                "estimated_income, order_payment, tail_freight, tail_currency, tail_freight_cny, "
                "tracking_no, order_status, order_status_code, site, order_time, "
                "unit_cost_cny, purchase_cost_cny, weight_kg, commission_cny, head_freight_cny, "
                "profit_estimate_cny, profit_rate, source, captured_at, updated_at "
                "FROM order_profit_items WHERE workspace_id = ? AND shop_name = ?",
                (workspace_id, shop),
            ):
                data = dict(row)
                add(
                    "order",
                    "|".join(
                        [
                            _text(data.get("parent_order_sn")),
                            _text(data.get("sub_order_sn")),
                            _text(data.get("sku_id")),
                        ]
                    ),
                    _text(data.get("order_time")) or _text(data.get("captured_at")),
                    data,
                )
            for row in conn.execute(
                "SELECT entry_time, entry_type, amount, currency, note, category, "
                "captured_at, updated_at FROM finance_entries "
                "WHERE workspace_id = ? AND shop_name = ?",
                (workspace_id, shop),
            ):
                data = dict(row)
                add(
                    "finance",
                    "|".join(
                        [
                            _text(data.get("entry_time")),
                            _text(data.get("entry_type")),
                            _text(data.get("amount")),
                            _text(data.get("currency")),
                            _text(data.get("note")),
                        ]
                    ),
                    data.get("entry_time"),
                    data,
                )
            for row in conn.execute(
                "SELECT parent_order_sn, status, currency, sales_payback, sales_reversal, "
                "freight_payback, freight_reversal, declared_price, quantity, goods_name, "
                "site, captured_at, updated_at FROM order_settlements "
                "WHERE workspace_id = ? AND shop_name = ?",
                (workspace_id, shop),
            ):
                data = dict(row)
                add("settlement", _text(data.get("parent_order_sn")), data.get("captured_at"), data)
            for row in conn.execute(
                "SELECT scope, period, stat_date, exposure, clicks, visitors, paid_orders, "
                "paid_items, click_rate, click_order_rate, exposure_order_rate, metrics, "
                "captured_at, updated_at FROM traffic_snapshots "
                "WHERE workspace_id = ? AND shop_name = ?",
                (workspace_id, shop),
            ):
                data = dict(row)
                add(
                    "traffic",
                    "|".join(
                        [
                            _text(data.get("stat_date")),
                            _text(data.get("scope")),
                            _text(data.get("period")),
                        ]
                    ),
                    data.get("stat_date") or data.get("captured_at"),
                    data,
                )
            for row in conn.execute(
                "SELECT region, stat_date, metrics, health, captured_at, updated_at "
                "FROM sales_snapshots WHERE workspace_id = ? AND shop_name = ?",
                (workspace_id, shop),
            ):
                data = dict(row)
                add(
                    "sales",
                    "|".join([_text(data.get("stat_date")), _text(data.get("region"))]),
                    data.get("stat_date") or data.get("captured_at"),
                    data,
                )
            for row in conn.execute(
                "SELECT pending_shipment, active_products, sales_today, sales_7d, sales_30d, "
                "refund_pending, cancel_pending, ship_timeliness, delivery_timeliness, "
                "captured_at, updated_at FROM store_metrics "
                "WHERE workspace_id = ? AND shop_name = ?",
                (workspace_id, shop),
            ):
                data = dict(row)
                add("metric", _text(data.get("captured_at")), data.get("captured_at"), data)
        return records

    def rollup_days(
        self,
        workspace_id: str,
        *,
        start: str,
        end: str,
    ) -> list[dict[str, Any]]:
        """按「店铺 × 业务日」聚合订单、财务、结算、流量、销量。

        只读本地库，不访问网络；返回值直接作为每周提交的 days 数组。
        """
        buckets: dict[tuple[str, str], dict[str, Any]] = {}

        def bucket(shop: str, day: str) -> dict[str, Any]:
            key = (shop, day)
            if key not in buckets:
                buckets[key] = {
                    "shop_name": shop,
                    "stat_date": day,
                    "order_count": 0,
                    "sub_order_count": 0,
                    "gmv_cny": 0.0,
                    "refund_cny": 0.0,
                    "profit_cny": None,
                    "settlement_payback_cny": 0.0,
                    "finance_expense_cny": 0.0,
                    "exposure": 0,
                    "clicks": 0,
                    "visitors": 0,
                    "paid_items": 0,
                    "sales_today": 0,
                    "source_updated_at": "",
                }
            return buckets[key]

        def bump(row: dict[str, Any], updated_at: Any) -> None:
            stamp = _text(updated_at)
            if stamp > row["source_updated_at"]:
                row["source_updated_at"] = stamp

        with self._connect() as conn:
            orders = conn.execute(
                "SELECT shop_name, substr(order_time,1,10) AS stat_date, "
                "COUNT(DISTINCT parent_order_sn) AS order_count, COUNT(*) AS sub_order_count, "
                "COALESCE(SUM(gmv_cny),0) AS gmv_cny, "
                "COALESCE(SUM(CASE WHEN order_status LIKE '%取消%' "
                "THEN COALESCE(gmv_cny,0) ELSE 0 END),0) AS refund_cny, "
                "SUM(profit_estimate_cny) AS profit_cny, MAX(updated_at) AS updated_at "
                "FROM order_profit_items "
                "WHERE workspace_id = ? AND shop_name <> '' "
                "AND substr(order_time,1,10) BETWEEN ? AND ? "
                "GROUP BY shop_name, substr(order_time,1,10)",
                (workspace_id, start, end),
            ).fetchall()
            for row in orders:
                target = bucket(_text(row["shop_name"]), _text(row["stat_date"]))
                target["order_count"] = _int(row["order_count"])
                target["sub_order_count"] = _int(row["sub_order_count"])
                target["gmv_cny"] = _float(row["gmv_cny"]) or 0.0
                target["refund_cny"] = _float(row["refund_cny"]) or 0.0
                target["profit_cny"] = _float(row["profit_cny"])
                bump(target, row["updated_at"])

            finance = conn.execute(
                "SELECT shop_name, substr(entry_time,1,10) AS stat_date, "
                "COALESCE(SUM(CASE WHEN amount < 0 THEN -amount ELSE 0 END),0) AS expense, "
                "MAX(updated_at) AS updated_at FROM finance_entries "
                "WHERE workspace_id = ? AND shop_name <> '' "
                "AND substr(entry_time,1,10) BETWEEN ? AND ? "
                "GROUP BY shop_name, substr(entry_time,1,10)",
                (workspace_id, start, end),
            ).fetchall()
            for row in finance:
                target = bucket(_text(row["shop_name"]), _text(row["stat_date"]))
                target["finance_expense_cny"] = _float(row["expense"]) or 0.0
                bump(target, row["updated_at"])

            settlements = conn.execute(
                "SELECT shop_name, substr(captured_at,1,10) AS stat_date, "
                "COALESCE(SUM(COALESCE(sales_payback,0) + COALESCE(freight_payback,0) "
                "- COALESCE(sales_reversal,0) - COALESCE(freight_reversal,0)),0) AS payback, "
                "MAX(updated_at) AS updated_at FROM order_settlements "
                "WHERE workspace_id = ? AND shop_name <> '' "
                "AND substr(captured_at,1,10) BETWEEN ? AND ? "
                "GROUP BY shop_name, substr(captured_at,1,10)",
                (workspace_id, start, end),
            ).fetchall()
            for row in settlements:
                target = bucket(_text(row["shop_name"]), _text(row["stat_date"]))
                target["settlement_payback_cny"] = _float(row["payback"]) or 0.0
                bump(target, row["updated_at"])

            traffic = conn.execute(
                "SELECT shop_name, stat_date, scope, exposure, clicks, visitors, paid_items, "
                "captured_at FROM traffic_snapshots "
                "WHERE workspace_id = ? AND shop_name <> '' AND stat_date BETWEEN ? AND ? "
                "ORDER BY captured_at ASC",
                (workspace_id, start, end),
            ).fetchall()
            for row in traffic:
                target = bucket(_text(row["shop_name"]), _text(row["stat_date"]))
                # 同一个店同一天可能有商品/店铺两种口径，店铺口径优先。
                if _text(row["scope"]) == "shop" or not target["exposure"]:
                    target["exposure"] = _int(row["exposure"])
                    target["clicks"] = _int(row["clicks"])
                    target["visitors"] = _int(row["visitors"])
                    target["paid_items"] = _int(row["paid_items"])
                bump(target, row["captured_at"])

            sales = conn.execute(
                "SELECT shop_name, stat_date, metrics, captured_at FROM sales_snapshots "
                "WHERE workspace_id = ? AND shop_name <> '' AND stat_date BETWEEN ? AND ? "
                "ORDER BY captured_at ASC",
                (workspace_id, start, end),
            ).fetchall()
            for row in sales:
                target = bucket(_text(row["shop_name"]), _text(row["stat_date"]))
                target["sales_today"] = _sales_today(row["metrics"])
                bump(target, row["captured_at"])

        return list(buckets.values())

    def duplicate_orders(self, workspace_id: str) -> list[dict[str, Any]]:
        """同一账号下不同店铺采到同一条订单的组合。

        以 ``order_shop_claims`` 为准：订单行本身会按唯一键合并成一条，
        但「哪家店采到过」的记录是分开的。只用于打标记，不删数据。
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT a.shop_name AS left_shop, b.shop_name AS right_shop, "
                "COUNT(*) AS dup_count, MIN(a.parent_order_sn) AS sample "
                "FROM order_shop_claims a "
                "JOIN order_shop_claims b "
                "  ON a.workspace_id = b.workspace_id "
                " AND a.parent_order_sn = b.parent_order_sn "
                " AND a.sub_order_sn = b.sub_order_sn "
                " AND a.sku_id = b.sku_id "
                " AND a.shop_name < b.shop_name "
                "WHERE a.workspace_id = ? AND a.shop_name <> '' AND b.shop_name <> '' "
                "GROUP BY a.shop_name, b.shop_name",
                (workspace_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_shop_claim(self, workspace_id: str, item: Mapping[str, Any]) -> None:
        """记录「某家店采到过这条订单」，用于跨店铺重复检测。"""
        shop_name = _text(item.get("shop_name"))
        parent = _text(item.get("parent_order_sn"))
        sub = _text(item.get("sub_order_sn"))
        sku = _text(item.get("sku_id"))
        if not shop_name or not (parent or sub or sku):
            return
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO order_shop_claims "
                "(workspace_id, parent_order_sn, sub_order_sn, sku_id, shop_name, "
                " first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(workspace_id, parent_order_sn, sub_order_sn, sku_id, shop_name) "
                "DO UPDATE SET last_seen_at = excluded.last_seen_at",
                (workspace_id, parent, sub, sku, shop_name, now, now),
            )


    # ---------- sourcing ledger read-only join ----------
    def sourcing_cost(
        self,
        workspace_id: str,
        *,
        skc_id: str = "",
        skc_code: str = "",
    ) -> dict[str, Any] | None:
        candidates = []
        for value in (skc_id, skc_code):
            value = _text(value)
            if value:
                candidates.extend([value, f"temu:{value}"])
        candidates = list(dict.fromkeys(candidates))
        if not candidates:
            return None
        with self._connect() as conn:
            table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name = 'product_sourcing_sources'"
            ).fetchone()
            if table is None:
                return None
            # 台账的货源主键是 PP 货号（product_market_links.skc），
            # 先按 Temu SKC ID 反查 PP 货号，再查货源成本。
            link_table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name = 'product_market_links'"
            ).fetchone()
            if link_table is not None:
                marks = ",".join("?" for _ in candidates)
                links = conn.execute(
                    f"SELECT skc FROM product_market_links WHERE workspace_id = ? "
                    f"AND market = 'temu' AND (market_skc_id IN ({marks}) OR skc IN ({marks}))",
                    (workspace_id, *candidates, *candidates),
                ).fetchall()
                for link in links:
                    value = _text(link["skc"])
                    if value:
                        candidates.extend([value, f"temu:{value}"])
                candidates = list(dict.fromkeys(candidates))
            marks = ",".join("?" for _ in candidates)
            row = conn.execute(
                f"SELECT product_key, landed_cost_cny, weight_kg, is_primary "
                f"FROM product_sourcing_sources WHERE workspace_id = ? "
                f"AND status = 'active' AND product_key IN ({marks}) "
                f"ORDER BY is_primary DESC, CAST(landed_cost_cny AS REAL) ASC LIMIT 1",
                (workspace_id, *candidates),
            ).fetchone()
        if row is None:
            return None
        cost = _float(row["landed_cost_cny"])
        weight = _float(row["weight_kg"])
        source = "sourcing_primary" if _int(row["is_primary"]) else "sourcing_lowest"
        if cost is None and weight is None:
            return None
        return {
            "unit_cost_cny": cost,
            "weight_kg": weight,
            "cost_source": source,
            "product_key": _text(row["product_key"]),
        }

    # ---------- 店铺账号（自动登录采集） ----------

    @staticmethod
    def _account_row(row: sqlite3.Row, *, with_secret: bool = False) -> dict[str, Any]:
        data = dict(row)
        phone = _text(data.get("phone"))
        data["phone_tail"] = phone[-4:] if len(phone) >= 4 else phone
        data["has_password"] = bool(_text(data.get("password_enc")))
        if not with_secret:
            data.pop("password_enc", None)
        return data

    def list_accounts(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM store_accounts WHERE workspace_id = ? "
                "ORDER BY created_at ASC",
                (workspace_id,),
            ).fetchall()
        return [self._account_row(row) for row in rows]

    def get_account(self, workspace_id: str, account_id: str, *, with_secret: bool = False) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM store_accounts WHERE workspace_id = ? AND id = ?",
                (workspace_id, _text(account_id)),
            ).fetchone()
        return self._account_row(row, with_secret=with_secret) if row else None

    def upsert_account(
        self,
        workspace_id: str,
        *,
        account_id: str = "",
        alias: str = "",
        phone: str,
        password_enc: str | None = None,
        region: str = "global",
    ) -> dict[str, Any]:
        now = _now()
        phone_text = _text(phone)
        if not phone_text:
            raise ValueError("手机号不能为空")
        with self._connect() as conn:
            existing = None
            if account_id:
                existing = conn.execute(
                    "SELECT * FROM store_accounts WHERE workspace_id = ? AND id = ?",
                    (workspace_id, _text(account_id)),
                ).fetchone()
            if existing is None:
                duplicate = conn.execute(
                    "SELECT id FROM store_accounts WHERE workspace_id = ? AND phone = ?",
                    (workspace_id, phone_text),
                ).fetchone()
                if duplicate:
                    account_id = str(duplicate["id"])
                    existing = conn.execute(
                        "SELECT * FROM store_accounts WHERE id = ?", (account_id,)
                    ).fetchone()
            if existing is None:
                account_id = uuid.uuid4().hex
                conn.execute(
                    "INSERT INTO store_accounts "
                    "(id, workspace_id, alias, phone, password_enc, region, shop_name, status, "
                    " last_login_at, last_error, instance_tag, instance_pid, last_command_id, "
                    " created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, '', 'idle', '', '', '', 0, 0, ?, ?)",
                    (
                        account_id,
                        workspace_id,
                        _text(alias),
                        phone_text,
                        password_enc or "",
                        _text(region) or "global",
                        now,
                        now,
                    ),
                )
            else:
                account_id = str(existing["id"])
                conn.execute(
                    "UPDATE store_accounts SET alias = ?, phone = ?, region = ?, updated_at = ? "
                    "WHERE id = ? AND workspace_id = ?",
                    (_text(alias), phone_text, _text(region) or "global", now, account_id, workspace_id),
                )
                if password_enc:
                    conn.execute(
                        "UPDATE store_accounts SET password_enc = ? WHERE id = ?",
                        (password_enc, account_id),
                    )
        return self.get_account(workspace_id, account_id) or {}

    def delete_account(self, workspace_id: str, account_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM store_accounts WHERE workspace_id = ? AND id = ?",
                (workspace_id, _text(account_id)),
            )
        return cursor.rowcount > 0

    def update_account_state(
        self,
        workspace_id: str,
        account_id: str,
        *,
        status: str = "",
        shop_name: str | None = None,
        last_error: str | None = None,
        last_login_at: str = "",
        command_id: int | None = None,
    ) -> None:
        fields: list[str] = ["updated_at = ?"]
        values: list[Any] = [_now()]
        if status:
            fields.append("status = ?")
            values.append(_text(status))
        if shop_name is not None:
            fields.append("shop_name = ?")
            values.append(_text(shop_name))
        if last_error is not None:
            fields.append("last_error = ?")
            values.append(_text(last_error))
        if last_login_at:
            fields.append("last_login_at = ?")
            values.append(_text(last_login_at))
        if command_id is not None:
            fields.append("last_command_id = ?")
            values.append(int(command_id))
        values.extend([_text(account_id), workspace_id])
        with self._connect() as conn:
            conn.execute(
                f"UPDATE store_accounts SET {', '.join(fields)} "
                f"WHERE id = ? AND workspace_id = ?",
                values,
            )

    def set_account_instance(
        self,
        workspace_id: str,
        account_id: str,
        *,
        tag: str,
        pid: int,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE store_accounts SET instance_tag = ?, instance_pid = ?, updated_at = ? "
                "WHERE workspace_id = ? AND id = ?",
                (_text(tag), int(pid), _now(), workspace_id, _text(account_id)),
            )

    def account_by_instance(self, workspace_id: str, tag: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM store_accounts WHERE workspace_id = ? AND instance_tag = ? "
                "ORDER BY updated_at DESC LIMIT 1",
                (workspace_id, _text(tag)),
            ).fetchone()
        return self._account_row(row) if row else None
