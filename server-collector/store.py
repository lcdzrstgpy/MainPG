"""服务端采集器的本地存储。

只存两类东西：Temu 店铺账号、每次采集的原始快照。
密码用 Fernet 加密落盘，密钥文件和数据库放同一目录。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
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

CREATE TABLE IF NOT EXISTS bridge_clients (
    client_id  TEXT PRIMARY KEY,
    label      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bridge_bindings (
    client_id  TEXT NOT NULL,
    account_id TEXT NOT NULL,
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
                "SELECT b.account_id, b.client_id, b.created_at, c.label "
                "FROM bridge_bindings b "
                "LEFT JOIN bridge_clients c ON c.client_id = b.client_id "
                "ORDER BY b.account_id ASC, b.created_at ASC"
            ).fetchall()
        return [
            {
                "account_id": str(row["account_id"]),
                "client_id": str(row["client_id"]),
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
                "INSERT INTO bridge_bindings (client_id, account_id, created_at) "
                "VALUES (?, ?, ?) ON CONFLICT(client_id, account_id) DO NOTHING",
                (client_id, account["id"], now),
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
