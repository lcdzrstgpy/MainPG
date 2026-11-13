"""服务端采集器的本地存储。

只存两类东西：Temu 店铺账号、每次采集的原始快照。
密码用 Fernet 加密落盘，密钥文件和数据库放同一目录。
"""

from __future__ import annotations

import json
import math
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id              TEXT PRIMARY KEY,
    alias           TEXT NOT NULL DEFAULT '',
    phone           TEXT NOT NULL,
    password_enc    TEXT NOT NULL DEFAULT '',
    shop_name       TEXT NOT NULL DEFAULT '',
    site            TEXT NOT NULL DEFAULT 'us',
    status          TEXT NOT NULL DEFAULT 'idle',
    last_error      TEXT NOT NULL DEFAULT '',
    last_login_at   TEXT NOT NULL DEFAULT '',
    last_capture_at TEXT NOT NULL DEFAULT '',
    order_count     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_accounts_phone ON accounts(phone);

CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  TEXT NOT NULL,
    kind        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    captured_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_account ON snapshots(account_id, captured_at);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS translations (
    src        TEXT PRIMARY KEY,
    dst        TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS order_details (
    parent_order_sn TEXT PRIMARY KEY,
    payload         TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS temu_workflows (
    id          TEXT PRIMARY KEY,
    account_id  TEXT NOT NULL,
    status      TEXT NOT NULL,
    current_step TEXT NOT NULL DEFAULT '',
    started_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT '',
    steps       TEXT NOT NULL DEFAULT '[]',
    error       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_temu_workflows_account ON temu_workflows(account_id, started_at);

CREATE TABLE IF NOT EXISTS sku_spu_mappings (
    account_id TEXT NOT NULL,
    sku_id TEXT NOT NULL,
    spu_id TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account_id, sku_id)
);
CREATE INDEX IF NOT EXISTS idx_sku_spu_spu ON sku_spu_mappings(account_id, spu_id);

CREATE TABLE IF NOT EXISTS bridge_clients (
    client_id  TEXT PRIMARY KEY,
    label      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bridge_bindings (
    client_id  TEXT NOT NULL,
    account_id TEXT NOT NULL,
    claimed_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (client_id, account_id)
);
CREATE INDEX IF NOT EXISTS idx_bridge_bindings_client ON bridge_bindings(client_id);

CREATE TABLE IF NOT EXISTS item_costs (
    account_id TEXT NOT NULL,
    skc_id     TEXT NOT NULL,
    unit_cost  REAL NOT NULL,
    currency   TEXT NOT NULL DEFAULT 'CNY',
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account_id, skc_id)
);
CREATE INDEX IF NOT EXISTS idx_item_costs_account ON item_costs(account_id);

CREATE TABLE IF NOT EXISTS dxm_accounts (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL,
    password_enc  TEXT NOT NULL DEFAULT '',
    label         TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'idle',
    last_error    TEXT NOT NULL DEFAULT '',
    last_login_at TEXT NOT NULL DEFAULT '',
    last_align_at TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_dxm_accounts_username ON dxm_accounts(username);

CREATE TABLE IF NOT EXISTS dxm_pairs (
    po         TEXT NOT NULL,
    xm         TEXT NOT NULL,
    account_id TEXT NOT NULL DEFAULT '',
    shop       TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL DEFAULT '',
    logistics  TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (po, xm)
);
CREATE INDEX IF NOT EXISTS idx_dxm_pairs_account ON dxm_pairs(account_id);

CREATE TABLE IF NOT EXISTS freight_accounts (
    platform      TEXT PRIMARY KEY,
    username      TEXT NOT NULL DEFAULT '',
    password_enc  TEXT NOT NULL DEFAULT '',
    label         TEXT NOT NULL DEFAULT '',
    site_url      TEXT NOT NULL DEFAULT '',
    adapter       TEXT NOT NULL DEFAULT 'wujie',
    status        TEXT NOT NULL DEFAULT 'idle',
    last_error    TEXT NOT NULL DEFAULT '',
    last_login_at TEXT NOT NULL DEFAULT '',
    last_query_at TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS freight_quotes (
    platform   TEXT NOT NULL,
    xm         TEXT NOT NULL,
    order_no   TEXT NOT NULL DEFAULT '',
    weight     REAL,
    amount     REAL,
    currency   TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT '',
    account    TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (platform, xm)
);
CREATE INDEX IF NOT EXISTS idx_freight_quotes_platform ON freight_quotes(platform);
CREATE TABLE IF NOT EXISTS freight_runs (
    id TEXT PRIMARY KEY, status TEXT NOT NULL, payload TEXT NOT NULL,
    started_at TEXT NOT NULL, finished_at TEXT NOT NULL DEFAULT '',
    heartbeat_at TEXT NOT NULL, details TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS freight_sync (
    xm TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'never',
    attempt_count INTEGER NOT NULL DEFAULT 0, last_synced_at TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL, platforms TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS freight_platform_results (
    run_id TEXT NOT NULL, platform TEXT NOT NULL, result TEXT NOT NULL,
    finished_at TEXT NOT NULL, PRIMARY KEY(run_id, platform)
);
CREATE TABLE IF NOT EXISTS freight_schedule (
    key TEXT PRIMARY KEY, value TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CollectorStore:
    def __init__(self, data_dir: str | Path) -> None:
        self._dir = Path(data_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._db = self._dir / "collector.sqlite3"
        self._fernet = Fernet(self._load_key())
        self._initialize()

    # ---------- 基础 ----------

    def _load_key(self) -> bytes:
        path = self._dir / "collector.key"
        if path.exists():
            return path.read_bytes().strip()
        key = Fernet.generate_key()
        path.write_bytes(key)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return key

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db, timeout=20)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            for table in ("freight_runs", "freight_sync"):
                names = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
                if "updated_at" not in names:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
            # 旧库补列：认领账号按登录账号记录，历史行回退到客户端标签。
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(bridge_bindings)").fetchall()
            }
            if "claimed_by" not in columns:
                conn.execute(
                    "ALTER TABLE bridge_bindings ADD COLUMN claimed_by TEXT NOT NULL DEFAULT ''"
                )
            # 发货平台账号补列：站点地址与适配器（wujie / hy / rtb）。
            freight_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(freight_accounts)").fetchall()
            }
            if "site_url" not in freight_columns:
                conn.execute(
                    "ALTER TABLE freight_accounts ADD COLUMN site_url TEXT NOT NULL DEFAULT ''"
                )
            if "adapter" not in freight_columns:
                conn.execute(
                    "ALTER TABLE freight_accounts ADD COLUMN adapter TEXT NOT NULL DEFAULT 'wujie'"
                )
            # 店小秘配对补列：物流方式（下单时选的渠道，如「浩远快递-TEMU平台面单(美西)标准」）。
            pair_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(dxm_pairs)").fetchall()
            }
            if "logistics" not in pair_columns:
                conn.execute(
                    "ALTER TABLE dxm_pairs ADD COLUMN logistics TEXT NOT NULL DEFAULT ''"
                )

    # ---------- 账号 ----------

    def list_accounts(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM accounts ORDER BY created_at ASC"
            ).fetchall()
        return [self._account(row) for row in rows]

    def get_account(self, account_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
        return self._account(row) if row else None

    def get_credentials(self, account_id: str) -> dict[str, str] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT phone, password_enc, site FROM accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            password = self._fernet.decrypt(row["password_enc"].encode()).decode()
        except (InvalidToken, ValueError, AttributeError):
            password = ""
        return {
            "phone": str(row["phone"] or ""),
            "password": password,
            "site": str(row["site"] or "us"),
        }

    def save_account(
        self,
        *,
        phone: str,
        password: str = "",
        alias: str = "",
        site: str = "us",
        account_id: str = "",
    ) -> dict[str, Any]:
        phone_text = str(phone or "").strip()
        if not phone_text:
            raise ValueError("手机号不能为空")
        encrypted = self._fernet.encrypt(password.encode()).decode() if password else ""
        now = now_iso()
        with self._connect() as conn:
            if account_id:
                row = conn.execute(
                    "SELECT * FROM accounts WHERE id = ?", (account_id,)
                ).fetchone()
                if row is None:
                    raise LookupError("账号不存在")
                conn.execute(
                    "UPDATE accounts SET phone = ?, alias = ?, site = ?, "
                    "password_enc = CASE WHEN ? <> '' THEN ? ELSE password_enc END, "
                    "updated_at = ? WHERE id = ?",
                    (phone_text, alias, site, encrypted, encrypted, now, account_id),
                )
            else:
                existing = conn.execute(
                    "SELECT id FROM accounts WHERE phone = ?", (phone_text,)
                ).fetchone()
                if existing:
                    account_id = str(existing["id"])
                    conn.execute(
                        "UPDATE accounts SET alias = ?, site = ?, "
                        "password_enc = CASE WHEN ? <> '' THEN ? ELSE password_enc END, "
                        "updated_at = ? WHERE id = ?",
                        (alias, site, encrypted, encrypted, now, account_id),
                    )
                else:
                    account_id = uuid.uuid4().hex
                    conn.execute(
                        "INSERT INTO accounts (id, alias, phone, password_enc, site, "
                        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (account_id, alias, phone_text, encrypted, site, now, now),
                    )
        return self.get_account(account_id) or {}

    def delete_account(self, account_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            conn.execute("DELETE FROM snapshots WHERE account_id = ?", (account_id,))
        return cursor.rowcount > 0

    def update_account(self, account_id: str, **fields: Any) -> None:
        allowed = {
            "shop_name",
            "status",
            "last_error",
            "last_login_at",
            "last_capture_at",
            "order_count",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{key} = ?" for key in updates)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE accounts SET {assignments}, updated_at = ? WHERE id = ?",
                (*updates.values(), now_iso(), account_id),
            )

    # ---------- 快照 ----------

    def save_snapshot(
        self, account_id: str, kind: str, payload: dict[str, Any]
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO snapshots (account_id, kind, payload, captured_at) "
                "VALUES (?, ?, ?, ?)",
                (account_id, kind, json.dumps(payload, ensure_ascii=False), now_iso()),
            )

    def latest_snapshot(self, account_id: str, kind: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload, captured_at FROM snapshots "
                "WHERE account_id = ? AND kind = ? ORDER BY id DESC LIMIT 1",
                (account_id, kind),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["payload"])
        except ValueError:
            payload = {}
        return {"payload": payload, "captured_at": str(row["captured_at"])}

    def create_temu_workflow(self, account_id: str) -> str:
        workflow_id = uuid.uuid4().hex
        now = now_iso()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO temu_workflows (id, account_id, status, started_at, updated_at) "
                "VALUES (?, ?, 'running', ?, ?)",
                (workflow_id, account_id, now, now),
            )
        return workflow_id

    def update_temu_workflow(self, workflow_id: str, *, status: str | None = None,
                             current_step: str | None = None, steps: list[dict[str, Any]] | None = None,
                             error: str | None = None) -> None:
        values: dict[str, Any] = {"updated_at": now_iso()}
        if status is not None:
            values["status"] = status
            if status in {"completed", "partial", "failed", "waiting_human"}:
                values["finished_at"] = now_iso()
        if current_step is not None:
            values["current_step"] = current_step
        if steps is not None:
            values["steps"] = json.dumps(steps, ensure_ascii=False)
        if error is not None:
            values["error"] = error
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self._connect() as conn:
            conn.execute(f"UPDATE temu_workflows SET {assignments} WHERE id = ?",
                         (*values.values(), workflow_id))

    def latest_temu_workflow(self, account_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM temu_workflows WHERE account_id = ? ORDER BY started_at DESC LIMIT 1",
                (account_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        try:
            result["steps"] = json.loads(result.get("steps") or "[]")
        except ValueError:
            result["steps"] = []
        return result

    def add_order_spu_mapping(self, account_id: str, mapping: dict[str, str], source: str = "") -> None:
        """保存按账号隔离、来自精确 SKU 查询的 SKU→SPU 映射。"""
        now = now_iso()
        rows = [(account_id, str(sku).strip(), str(spu).strip(), source, now)
                for sku, spu in mapping.items() if str(sku).strip() and str(spu).strip()]
        if not rows:
            return
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO sku_spu_mappings (account_id, sku_id, spu_id, source, updated_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(account_id, sku_id) DO UPDATE SET "
                "spu_id = excluded.spu_id, source = excluded.source, updated_at = excluded.updated_at",
                rows,
            )

    def order_spu_mapping(self, account_id: str, sku_ids: list[str]) -> dict[str, str]:
        ids = list(dict.fromkeys(str(value).strip() for value in sku_ids if str(value).strip()))
        result: dict[str, str] = {}
        with self._connect() as conn:
            for offset in range(0, len(ids), 200):
                batch = ids[offset:offset + 200]
                marks = ",".join("?" for _ in batch)
                rows = conn.execute(
                    f"SELECT sku_id, spu_id FROM sku_spu_mappings WHERE account_id = ? AND sku_id IN ({marks})",
                    (account_id, *batch),
                ).fetchall()
                result.update({str(row["sku_id"]): str(row["spu_id"]) for row in rows})
        return result

    # ---------- 翻译缓存 ----------

    def get_translations(self, texts: list[str]) -> dict[str, str]:
        if not texts:
            return {}
        keys = [str(text) for text in texts]
        result: dict[str, str] = {}
        with self._connect() as conn:
            for start in range(0, len(keys), 200):
                chunk = keys[start : start + 200]
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"SELECT src, dst FROM translations WHERE src IN ({placeholders})",
                    chunk,
                ).fetchall()
                for row in rows:
                    result[str(row["src"])] = str(row["dst"] or "")
        return result

    def save_translations(self, mapping: dict[str, str]) -> None:
        now = now_iso()
        with self._connect() as conn:
            for src, dst in mapping.items():
                if not src or not dst:
                    continue
                conn.execute(
                    "INSERT INTO translations (src, dst, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(src) DO UPDATE SET dst = excluded.dst, "
                    "updated_at = excluded.updated_at",
                    (str(src), str(dst), now),
                )

    # ---------- 订单详情缓存 ----------

    def get_order_details(self, parents: list[str], ttl_days: int = 3) -> dict[str, dict[str, Any]]:
        """取详情缓存；超过 TTL 的视为过期（金额后续可能结算更新）。"""
        if not parents:
            return {}
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=max(1, int(ttl_days)))
        ).isoformat(timespec="seconds")
        result: dict[str, dict[str, Any]] = {}
        with self._connect() as conn:
            for start in range(0, len(parents), 200):
                chunk = [str(po) for po in parents[start : start + 200] if po]
                if not chunk:
                    continue
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"SELECT parent_order_sn, payload FROM order_details "
                    f"WHERE updated_at >= ? AND parent_order_sn IN ({placeholders})",
                    (cutoff, *chunk),
                ).fetchall()
                for row in rows:
                    try:
                        payload = json.loads(row["payload"])
                    except ValueError:
                        continue
                    if isinstance(payload, dict) and not payload.get("error") and payload.get("image_url"):
                        result[str(row["parent_order_sn"])] = payload
        return result

    def save_order_details(self, mapping: dict[str, dict[str, Any]]) -> None:
        now = now_iso()
        with self._connect() as conn:
            for parent, payload in mapping.items():
                if not parent or not isinstance(payload, dict) or payload.get("error"):
                    continue
                conn.execute(
                    "INSERT INTO order_details (parent_order_sn, payload, updated_at) "
                    "VALUES (?, ?, ?) ON CONFLICT(parent_order_sn) DO UPDATE SET "
                    "payload = excluded.payload, updated_at = excluded.updated_at",
                    (str(parent), json.dumps(payload, ensure_ascii=False), now),
                )

    def order_status_map(self) -> dict[str, str]:
        """订单详情缓存里的 PO → 订单状态，作为列表快照的补充来源。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT parent_order_sn, "
                "COALESCE(NULLIF(json_extract(payload, '$.order_status'), ''), "
                "json_extract(payload, '$.order_status_code')) AS status "
                "FROM order_details ORDER BY updated_at ASC"
            ).fetchall()
        result: dict[str, str] = {}
        for row in rows:
            status = str(row["status"] or "").strip()
            if status and row["parent_order_sn"]:
                result[str(row["parent_order_sn"])] = status
        return result

    # ---------- 设置 ----------
    def settings(self) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        data: dict[str, Any] = {}
        for row in rows:
            try:
                data[str(row["key"])] = json.loads(row["value"])
            except ValueError:
                data[str(row["key"])] = row["value"]
        return data

    def save_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as conn:
            for key, value in values.items():
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (str(key), json.dumps(value, ensure_ascii=False)),
                )
        return self.settings()

    # ---------- 桥接：绑定 / 成本 ----------

    def binding_account_ids(self, client_id: str) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT account_id FROM bridge_bindings WHERE client_id = ? "
                "ORDER BY created_at ASC",
                (client_id,),
            ).fetchall()
        return [str(row["account_id"]) for row in rows]

    def list_bindings(self, client_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT account_id, created_at FROM bridge_bindings "
                "WHERE client_id = ? ORDER BY created_at ASC",
                (client_id,),
            ).fetchall()
        return [
            {"account_id": str(row["account_id"]), "bound_at": str(row["created_at"])}
            for row in rows
        ]

    def list_claims(self) -> list[dict[str, Any]]:
        """所有客户端的认领记录（跨店铺），供服务端与客户端审计。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT b.account_id, b.client_id, b.claimed_by, b.created_at, c.label "
                "FROM bridge_bindings b "
                "LEFT JOIN bridge_clients c ON c.client_id = b.client_id "
                "ORDER BY b.account_id ASC, b.created_at ASC"
            ).fetchall()
        return [
            {
                "account_id": str(row["account_id"]),
                "client_id": str(row["client_id"]),
                "claimed_by": str(row["claimed_by"] or ""),
                "label": str(row["label"] or ""),
                "bound_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def bind_with_credentials(
        self, client_id: str, phone: str, password: str, label: str = ""
    ) -> dict[str, Any]:
        """用 Temu 账号密码与服务器已有账号建立绑定。

        只认服务器上已录入且密码一致的账号，客户端不能凭 account_id 直接绑定。
        """
        phone_text = str(phone or "").strip()
        if not phone_text or not str(password or "").strip():
            return {"ok": False, "reason": "missing"}
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM accounts WHERE phone = ?", (phone_text,)
            ).fetchone()
        if row is None:
            return {"ok": False, "reason": "account_not_found"}
        account = self._account(row)
        credentials = self.get_credentials(account["id"]) or {}
        if str(credentials.get("password") or "") != str(password):
            return {"ok": False, "reason": "bad_password"}
        now = now_iso()
        display = str(label or "").strip() or client_id
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO bridge_clients (client_id, label, created_at, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(client_id) DO UPDATE SET "
                "label = excluded.label, updated_at = excluded.updated_at",
                (client_id, display, now, now),
            )
            existing = conn.execute(
                "SELECT 1 FROM bridge_bindings WHERE client_id = ? AND account_id = ?",
                (client_id, account["id"]),
            ).fetchone()
            conn.execute(
                "INSERT INTO bridge_bindings (client_id, account_id, claimed_by, created_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(client_id, account_id) DO UPDATE SET "
                "claimed_by = excluded.claimed_by WHERE bridge_bindings.claimed_by = ''",
                (client_id, account["id"], display, now),
            )
        return {
            "ok": True,
            "account_id": account["id"],
            "shop_name": account["shop_name"] or account["alias"] or account["phone_tail"],
            "phone_tail": account["phone_tail"],
            "site": account["site"],
            "already_bound": existing is not None,
        }

    def delete_binding(self, client_id: str, account_id: str = "") -> int:
        with self._connect() as conn:
            if account_id:
                cursor = conn.execute(
                    "DELETE FROM bridge_bindings WHERE client_id = ? AND account_id = ?",
                    (client_id, account_id),
                )
            else:
                cursor = conn.execute(
                    "DELETE FROM bridge_bindings WHERE client_id = ?", (client_id,)
                )
        return int(cursor.rowcount)

    def list_costs(self, account_ids: list[str]) -> list[dict[str, Any]]:
        if not account_ids:
            return []
        rows: list[dict[str, Any]] = []
        with self._connect() as conn:
            for start in range(0, len(account_ids), 200):
                chunk = account_ids[start : start + 200]
                placeholders = ",".join("?" for _ in chunk)
                result = conn.execute(
                    f"SELECT account_id, skc_id, unit_cost, currency, updated_by, updated_at "
                    f"FROM item_costs WHERE account_id IN ({placeholders})",
                    chunk,
                ).fetchall()
                rows.extend(
                    {
                        "account_id": str(row["account_id"]),
                        "skc_id": str(row["skc_id"]),
                        "unit_cost": float(row["unit_cost"]),
                        "currency": str(row["currency"] or "CNY"),
                        "updated_by": str(row["updated_by"] or ""),
                        "updated_at": str(row["updated_at"]),
                    }
                    for row in result
                )
        return rows

    def save_costs(
        self, account_id: str, items: list[dict[str, Any]], updated_by: str = ""
    ) -> dict[str, Any]:
        now = now_iso()
        applied: list[str] = []
        skipped: list[dict[str, str]] = []
        with self._connect() as conn:
            for item in items:
                skc_id = str(item.get("skc_id") or "").strip()
                if not skc_id:
                    skipped.append({"skc_id": "", "reason": "skc_id 为空"})
                    continue
                try:
                    unit_cost = float(item.get("unit_cost"))
                except (TypeError, ValueError):
                    skipped.append({"skc_id": skc_id, "reason": "unit_cost 不是数字"})
                    continue
                if unit_cost < 0:
                    skipped.append({"skc_id": skc_id, "reason": "unit_cost 不能为负"})
                    continue
                currency = str(item.get("currency") or "CNY").strip().upper() or "CNY"
                conn.execute(
                    "INSERT INTO item_costs (account_id, skc_id, unit_cost, currency, "
                    "updated_by, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(account_id, skc_id) DO UPDATE SET "
                    "unit_cost = excluded.unit_cost, currency = excluded.currency, "
                    "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
                    (account_id, skc_id, unit_cost, currency, updated_by, now),
                )
                applied.append(skc_id)
        return {
            "applied": len(applied),
            "skc_ids": applied,
            "skipped": skipped,
            "updated_at": now,
        }

    # ---------- 店小秘账号 / 单号配对 ----------

    def list_dxm_accounts(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM dxm_accounts ORDER BY created_at ASC"
            ).fetchall()
        return [self._dxm_account(row) for row in rows]

    def get_dxm_account(self, account_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM dxm_accounts WHERE id = ?", (account_id,)
            ).fetchone()
        return self._dxm_account(row) if row else None

    def get_dxm_credentials(self, account_id: str) -> dict[str, str] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT username, password_enc, label FROM dxm_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            password = self._fernet.decrypt(row["password_enc"].encode()).decode()
        except (InvalidToken, ValueError, AttributeError):
            password = ""
        return {
            "username": str(row["username"] or ""),
            "password": password,
            "label": str(row["label"] or ""),
        }

    def ensure_dxm_default(
        self, username: str, password: str, label: str = "店小秘南京"
    ) -> None:
        """首次启动时把默认店小秘账号写入，登录页可直接自动填入。"""
        if not str(username or "").strip():
            return
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM dxm_accounts LIMIT 1").fetchone()
        if row is not None:
            return
        self.save_dxm_account(username=username, password=password, label=label)

    def save_dxm_account(
        self,
        *,
        username: str,
        password: str = "",
        label: str = "",
        account_id: str = "",
    ) -> dict[str, Any]:
        name = str(username or "").strip()
        if not name:
            raise ValueError("店小秘账号不能为空")
        encrypted = self._fernet.encrypt(password.encode()).decode() if password else ""
        now = now_iso()
        with self._connect() as conn:
            if account_id:
                row = conn.execute(
                    "SELECT * FROM dxm_accounts WHERE id = ?", (account_id,)
                ).fetchone()
                if row is None:
                    raise LookupError("账号不存在")
                conn.execute(
                    "UPDATE dxm_accounts SET username = ?, label = ?, "
                    "password_enc = CASE WHEN ? <> '' THEN ? ELSE password_enc END, "
                    "updated_at = ? WHERE id = ?",
                    (name, label, encrypted, encrypted, now, account_id),
                )
            else:
                existing = conn.execute(
                    "SELECT id FROM dxm_accounts WHERE username = ?", (name,)
                ).fetchone()
                if existing:
                    account_id = str(existing["id"])
                    conn.execute(
                        "UPDATE dxm_accounts SET label = ?, "
                        "password_enc = CASE WHEN ? <> '' THEN ? ELSE password_enc END, "
                        "updated_at = ? WHERE id = ?",
                        (label, encrypted, encrypted, now, account_id),
                    )
                else:
                    account_id = uuid.uuid4().hex
                    conn.execute(
                        "INSERT INTO dxm_accounts (id, username, password_enc, label, "
                        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (account_id, name, encrypted, label, now, now),
                    )
        return self.get_dxm_account(account_id) or {}

    def update_dxm_account(self, account_id: str, **fields: Any) -> None:
        allowed = {"label", "status", "last_error", "last_login_at", "last_align_at"}
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{key} = ?" for key in updates)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE dxm_accounts SET {assignments}, updated_at = ? WHERE id = ?",
                (*updates.values(), now_iso(), account_id),
            )

    def delete_dxm_account(self, account_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM dxm_accounts WHERE id = ?", (account_id,))
        return cursor.rowcount > 0

    def save_dxm_pairs(
        self,
        po: str,
        xms: list[str],
        account_id: str = "",
        shop: str = "",
        source: str = "",
        logistics: dict[str, str] | None = None,
    ) -> None:
        """一个 PO 的配对结果整体替换；空结果说明店小秘里查不到，清掉旧值。"""
        code = str(po or "").strip()
        if not code:
            return
        now = now_iso()
        unique = [str(xm).strip() for xm in xms if str(xm).strip()]
        channels = logistics or {}
        with self._connect() as conn:
            conn.execute("DELETE FROM dxm_pairs WHERE po = ?", (code,))
            for xm in dict.fromkeys(unique):
                conn.execute(
                    "INSERT INTO dxm_pairs (po, xm, account_id, shop, source, logistics, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        code,
                        xm,
                        account_id,
                        shop,
                        source,
                        str(channels.get(xm) or ""),
                        now,
                    ),
                )

    def list_dxm_pairs(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT po, xm, account_id, shop, logistics, updated_at FROM dxm_pairs "
                "ORDER BY updated_at DESC"
            ).fetchall()
        return [
            {
                "po": str(row["po"]),
                "xm": str(row["xm"]),
                "account_id": str(row["account_id"] or ""),
                "shop": str(row["shop"] or ""),
                "logistics": str(row["logistics"] or ""),
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
        ]

    def count_dxm_pairs(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM dxm_pairs").fetchone()
        return int(row["n"] if row else 0)

    # ---------- 头程平台账号 / 报价 ----------

    def ensure_freight_default(
        self,
        platform: str,
        username: str,
        password: str,
        label: str = "",
        site_url: str = "",
        adapter: str = "",
    ) -> None:
        """首次启动时写入默认发货平台账号，登录页可直接自动填入。"""
        if not str(username or "").strip():
            return
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM freight_accounts WHERE platform = ?", (platform,)
            ).fetchone()
        if row is not None:
            return
        self.save_freight_account(
            platform=platform,
            username=username,
            password=password,
            label=label,
            site_url=site_url,
            adapter=adapter,
        )

    def save_freight_account(
        self,
        *,
        platform: str,
        username: str,
        password: str = "",
        label: str = "",
        site_url: str = "",
        adapter: str = "",
    ) -> dict[str, Any]:
        code = str(platform or "").strip()
        name = str(username or "").strip()
        if not code or not name:
            raise ValueError("平台和账号不能为空")
        encrypted = self._fernet.encrypt(password.encode()).decode() if password else ""
        site = str(site_url or "").strip()
        kind = str(adapter or "").strip()
        now = now_iso()
        with self._connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM freight_accounts WHERE platform = ?", (code,)
            ).fetchone()
            if exists:
                conn.execute(
                    "UPDATE freight_accounts SET username = ?, label = ?, "
                    "site_url = CASE WHEN ? <> '' THEN ? ELSE site_url END, "
                    "adapter = CASE WHEN ? <> '' THEN ? ELSE adapter END, "
                    "password_enc = CASE WHEN ? <> '' THEN ? ELSE password_enc END, "
                    "updated_at = ? WHERE platform = ?",
                    (name, label, site, site, kind, kind, encrypted, encrypted, now, code),
                )
            else:
                conn.execute(
                    "INSERT INTO freight_accounts (platform, username, password_enc, label, "
                    "site_url, adapter, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (code, name, encrypted, label, site, kind or "wujie", now, now),
                )
        return self.get_freight_account(code) or {}

    def list_freight_accounts(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM freight_accounts ORDER BY created_at ASC, platform ASC"
            ).fetchall()
        return [
            {
                "platform": str(row["platform"]),
                "username": str(row["username"] or ""),
                "label": str(row["label"] or ""),
                "site_url": str(row["site_url"] or ""),
                "adapter": str(row["adapter"] or "wujie"),
                "has_password": bool(str(row["password_enc"] or "")),
                "status": str(row["status"] or "idle"),
                "last_error": str(row["last_error"] or ""),
                "last_login_at": str(row["last_login_at"] or ""),
                "last_query_at": str(row["last_query_at"] or ""),
                "created_at": str(row["created_at"] or ""),
                "updated_at": str(row["updated_at"] or ""),
            }
            for row in rows
        ]

    def delete_freight_account(self, platform: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM freight_accounts WHERE platform = ?", (platform,))
            conn.execute("DELETE FROM freight_quotes WHERE platform = ?", (platform,))

    def get_freight_account(self, platform: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM freight_accounts WHERE platform = ?", (platform,)
            ).fetchone()
        if row is None:
            return None
        return {
            "platform": str(row["platform"]),
            "username": str(row["username"] or ""),
            "label": str(row["label"] or ""),
            "site_url": str(row["site_url"] or ""),
            "adapter": str(row["adapter"] or "wujie"),
            "has_password": bool(str(row["password_enc"] or "")),
            "status": str(row["status"] or "idle"),
            "last_error": str(row["last_error"] or ""),
            "last_login_at": str(row["last_login_at"] or ""),
            "last_query_at": str(row["last_query_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }

    def get_freight_credentials(self, platform: str) -> dict[str, str] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT username, password_enc, label, site_url, adapter "
                "FROM freight_accounts WHERE platform = ?",
                (platform,),
            ).fetchone()
        if row is None:
            return None
        try:
            password = self._fernet.decrypt(row["password_enc"].encode()).decode()
        except (InvalidToken, ValueError, AttributeError):
            password = ""
        return {
            "username": str(row["username"] or ""),
            "password": password,
            "label": str(row["label"] or ""),
            "site_url": str(row["site_url"] or ""),
            "adapter": str(row["adapter"] or "wujie"),
        }

    def update_freight_account(self, platform: str, **fields: Any) -> None:
        allowed = {
            "status",
            "last_error",
            "last_login_at",
            "last_query_at",
            "label",
            "site_url",
            "adapter",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{key} = ?" for key in updates)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE freight_accounts SET {assignments}, updated_at = ? WHERE platform = ?",
                (*updates.values(), now_iso(), platform),
            )

    def save_freight_quotes(self, platform: str, quotes: list[dict[str, Any]]) -> None:
        """按 XM 覆盖写入头程金额；查不到的保留旧值，由界面区分。"""
        now = now_iso()
        with self._connect() as conn:
            for item in quotes:
                xm = str(item.get("xm") or "").strip()
                amount = item.get("amount")
                if not xm or isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount):
                    continue
                conn.execute(
                    "INSERT INTO freight_quotes (platform, xm, order_no, weight, amount, "
                    "currency, status, account, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(platform, xm) DO UPDATE SET order_no = excluded.order_no, "
                    "weight = excluded.weight, amount = excluded.amount, "
                    "currency = excluded.currency, status = excluded.status, "
                    "account = excluded.account, updated_at = excluded.updated_at",
                    (
                        platform,
                        xm,
                        str(item.get("order_no") or ""),
                        item.get("weight"),
                        item.get("amount"),
                        str(item.get("currency") or ""),
                        str(item.get("status") or ""),
                        str(item.get("account") or ""),
                        now,
                    ),
                )

    def list_freight_quotes(self, platform: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT xm, order_no, weight, amount, currency, status, account, updated_at "
                "FROM freight_quotes WHERE platform = ? ORDER BY updated_at DESC",
                (platform,),
            ).fetchall()
        return [
            {
                "xm": str(row["xm"]),
                "order_no": str(row["order_no"] or ""),
                "weight": row["weight"],
                "amount": row["amount"],
                "currency": str(row["currency"] or ""),
                "status": str(row["status"] or ""),
                "account": str(row["account"] or ""),
                "updated_at": str(row["updated_at"] or ""),
            }
            for row in rows
        ]

    def count_freight_quotes(self, platform: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM freight_quotes WHERE platform = ?", (platform,)
            ).fetchone()
        return int(row["n"] if row else 0)

    def create_freight_run(self, payload: dict[str, Any]) -> tuple[str, bool]:
        now = now_iso()
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                "SELECT id, payload FROM freight_runs WHERE status IN ('queued', 'running') LIMIT 1"
            ).fetchone()
            if active:
                if active["payload"] == encoded:
                    return str(active["id"]), False
                raise RuntimeError("已有头程同步任务正在运行，请稍后重试")
            run_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO freight_runs (id, status, payload, started_at, heartbeat_at, updated_at) "
                "VALUES (?, 'queued', ?, ?, ?, ?)", (run_id, encoded, now, now, now),
            )
            for xm in payload["xms"]:
                conn.execute(
                    "INSERT INTO freight_sync (xm, state, attempt_count, run_id, platforms, updated_at) "
                    "VALUES (?, 'never', 0, ?, ?, ?) ON CONFLICT(xm) DO UPDATE SET "
                    "run_id = excluded.run_id, "
                    "platforms = excluded.platforms, updated_at = excluded.updated_at",
                    (xm, run_id, json.dumps(payload["platforms"]), now),
                )
        return run_id, True

    def mark_freight_attempted(self, run_id: str) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT details FROM freight_runs WHERE id = ?", (run_id,)).fetchone()
            details = json.loads(row["details"])
            if details.get("attempt_counted"):
                return
            details["attempt_counted"] = True
            conn.execute("UPDATE freight_runs SET details = ? WHERE id = ?", (json.dumps(details), run_id))
            conn.execute("UPDATE freight_sync SET attempt_count = attempt_count + 1 WHERE run_id = ?", (run_id,))

    def mark_freight_initialized(
        self, platforms: list[str], date_from: str | None = None, date_to: str | None = None
    ) -> None:
        value: dict[str, Any] = {"platforms": sorted(platforms)}
        if date_from is not None or date_to is not None:
            value.update(date_from=date_from, date_to=date_to)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO freight_schedule (key, value) VALUES ('initialized', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (json.dumps(value, ensure_ascii=False),),
            )

    def recover_interrupted_freight_runs(self) -> int:
        """Call only while holding an exclusive process-wide lock for the runner."""
        now = now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT id, details FROM freight_runs WHERE status IN ('queued', 'running')"
            ).fetchall()
            for row in rows:
                try:
                    details = json.loads(row["details"] or "{}")
                except (TypeError, ValueError):
                    details = {}
                details["message"] = "interrupted by process restart"
                conn.execute(
                    "UPDATE freight_runs SET status='failed', details=?, heartbeat_at=?, "
                    "updated_at=? WHERE id=?",
                    (json.dumps(details, ensure_ascii=False), now, now, row["id"]),
                )
                conn.execute(
                    "UPDATE freight_sync SET state='partial', updated_at=? "
                    "WHERE run_id=? AND state NOT IN ('found', 'partial')",
                    (now, row["id"]),
                )
        return len(rows)

    def update_freight_run(self, run_id: str, status: str, details: dict[str, Any]) -> None:
        now = now_iso()
        finished = "" if status in ("queued", "running") else now
        with self._connect() as conn:
            conn.execute(
                "UPDATE freight_runs SET status = ?, details = ?, heartbeat_at = ?, "
                "updated_at = ?, finished_at = ? WHERE id = ?",
                (status, json.dumps(details, ensure_ascii=False), now, now, finished, run_id),
            )

    def save_freight_platform_result(self, run_id: str, platform: str, result: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO freight_platform_results (run_id, platform, result, finished_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(run_id, platform) DO UPDATE SET "
                "result = excluded.result, finished_at = excluded.finished_at",
                (run_id, platform, json.dumps(result, ensure_ascii=False), now_iso()),
            )

    def finish_freight_sync(self, run_id: str, states: dict[str, str]) -> None:
        now = now_iso()
        with self._connect() as conn:
            run = conn.execute("SELECT details FROM freight_runs WHERE id = ?", (run_id,)).fetchone()
            attempted = bool(run and json.loads(run["details"]).get("attempt_counted"))
            for xm, state in states.items():
                cached = conn.execute(
                    "SELECT q.amount FROM freight_quotes q JOIN freight_accounts a ON a.platform = q.platform "
                    "WHERE q.xm = ?", (xm,),
                ).fetchall()
                if any(isinstance(r["amount"], (int, float)) and math.isfinite(r["amount"]) for r in cached):
                    state = "found"
                elif not attempted:
                    continue
                conn.execute(
                    "UPDATE freight_sync SET state = ?, "
                    "last_synced_at = CASE WHEN ? THEN ? ELSE last_synced_at END, updated_at = ? "
                    "WHERE xm = ? AND run_id = ?", (state, attempted, now, now, xm, run_id),
                )

    def freight_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM freight_runs WHERE id = ?", (run_id,)).fetchone()
            results = conn.execute(
                "SELECT platform, result FROM freight_platform_results WHERE run_id = ? ORDER BY platform",
                (run_id,),
            ).fetchall()
        if row is None:
            return None
        result = dict(row)
        result["run_id"] = result["id"]
        result["payload"] = json.loads(result["payload"])
        result["details"] = json.loads(result["details"])
        result["platform_results"] = {r["platform"]: json.loads(r["result"]) for r in results}
        return result

    def freight_sync_records(self, platforms: list[str]) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM freight_sync ORDER BY xm").fetchall()
            cached = conn.execute(
                "SELECT q.xm, q.amount FROM freight_quotes q JOIN freight_accounts a ON a.platform = q.platform"
            ).fetchall()
        found = {r["xm"] for r in cached if isinstance(r["amount"], (int, float)) and math.isfinite(r["amount"])}
        records = []
        for row in rows:
            record = dict(row)
            record["platforms"] = json.loads(record["platforms"])
            if record["xm"] in found:
                record["state"] = "found"
            if record["state"] == "all_not_found" and set(record["platforms"]) != set(platforms):
                record["state"] = "partial"
            records.append(record)
        return records

    # ---------- 行转换 ----------

    @staticmethod
    def _account(row: sqlite3.Row) -> dict[str, Any]:
        phone = str(row["phone"] or "")
        return {
            "id": str(row["id"]),
            "alias": str(row["alias"] or ""),
            "phone": phone,
            "phone_tail": phone[-4:] if len(phone) >= 4 else phone,
            "has_password": bool(str(row["password_enc"] or "")),
            "shop_name": str(row["shop_name"] or ""),
            "site": str(row["site"] or "us"),
            "status": str(row["status"] or "idle"),
            "last_error": str(row["last_error"] or ""),
            "last_login_at": str(row["last_login_at"] or ""),
            "last_capture_at": str(row["last_capture_at"] or ""),
            "order_count": int(row["order_count"] or 0),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }

    @staticmethod
    def _dxm_account(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "username": str(row["username"] or ""),
            "label": str(row["label"] or ""),
            "has_password": bool(str(row["password_enc"] or "")),
            "status": str(row["status"] or "idle"),
            "last_error": str(row["last_error"] or ""),
            "last_login_at": str(row["last_login_at"] or ""),
            "last_align_at": str(row["last_align_at"] or ""),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }
