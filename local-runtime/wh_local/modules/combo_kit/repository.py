"""combo_kit 数据库初始化：原生 sqlite3 + 顺序 SQL 迁移（幂等）。"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class ComboKitRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.applied: set[str] = set()
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS combo_kit_migrations (
                       migration_id TEXT PRIMARY KEY,
                       applied_at TEXT NOT NULL
                   )"""
            )
            migration_root = Path(__file__).with_name("migrations")
            for migration in sorted(migration_root.glob("[0-9][0-9][0-9]_*.sql")):
                self._ensure_migration(connection, migration)

    def _ensure_migration(self, connection: sqlite3.Connection, path: Path) -> None:
        migration_id = path.stem
        exists = connection.execute(
            "SELECT 1 FROM combo_kit_migrations WHERE migration_id = ?",
            (migration_id,),
        ).fetchone()
        if exists is not None:
            self.applied.add(migration_id)
            return
        sql = path.read_text(encoding="utf-8")
        connection.executescript(sql)
        connection.execute(
            "INSERT INTO combo_kit_migrations (migration_id, applied_at) VALUES (?, ?)",
            (migration_id, _now()),
        )
        self.applied.add(migration_id)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    # ---- 生命周期 ----

    def create_set(self, values: dict[str, object]) -> dict[str, object]:
        set_id = str(values.get("set_id") or _uuid())
        values["set_id"] = set_id
        cols = ("set_id", "workspace_id", "owner_user_id", "name", "sku", "sku_display",
                "description", "bullets_json", "category_path", "category_id",
                "attributes_json", "sku_specs_json", "status", "stage", "text_result_json",
                "image_results_json", "error_message", "fusion_prompt",
                "declared_price", "length_cm", "width_cm", "height_cm", "weight_g",
                "stock", "category_name", "suggested_price_usd", "id_type", "id_code",
                "created_at", "updated_at")
        with self._connect() as connection:
            connection.execute(
                f"INSERT INTO combo_kit_sets ({_cols(cols)}) VALUES ({_hole(len(cols))})",
                tuple(values.get(c) if values.get(c) is not None else _default_for(c) for c in cols),
            )
        return self.get_set(set_id)

    def update_set(self, set_id: str, values: dict[str, object]) -> dict[str, object]:
        allowed = ("name", "sku", "sku_display", "description", "bullets_json",
                   "category_path", "category_id", "attributes_json", "sku_specs_json",
                   "status", "stage", "text_result_json", "image_results_json", "error_message",
                   "fusion_prompt",
                   "declared_price", "length_cm", "width_cm", "height_cm", "weight_g",
                   "stock", "category_name", "suggested_price_usd", "id_type", "id_code")
        with self._connect() as connection:
            existing = self._get_set_row(connection, set_id)
            if existing is None:
                raise KeyError(set_id)
            assignments = []
            params: list[object] = []
            for key in allowed:
                if key in values:
                    assignments.append(f"{key} = ?")
                    params.append(values[key])
            assignments.append("updated_at = ?")
            params.append(_now())
            params.append(set_id)
            connection.execute(
                f"UPDATE combo_kit_sets SET {', '.join(assignments)} WHERE set_id = ?",
                tuple(params),
            )
        return self.get_set(set_id)

    def get_set(self, set_id: str) -> dict[str, object]:
        with self._connect() as connection:
            row = self._get_set_row(connection, set_id)
            if row is None:
                raise KeyError(set_id)
            return self._set_dict(row)

    def list_sets(
        self, workspace_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM combo_kit_sets WHERE workspace_id = ? "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (workspace_id, limit, offset),
            ).fetchall()
            return [self._set_dict(row) for row in rows]

    def _get_set_row(self, connection: sqlite3.Connection, set_id: str) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM combo_kit_sets WHERE set_id = ?", (set_id,)
        ).fetchone()

    def _set_dict(self, row: sqlite3.Row) -> dict[str, object]:
        data = self._row_keys(row)
        return _parse_json_fields(data, ("bullets_json", "attributes_json", "sku_specs_json",
                                         "text_result_json", "image_results_json"))

    # ---- 子商品素材 ----

    def list_items(self, set_id: str) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM combo_kit_items WHERE set_id = ? ORDER BY item_index ASC",
                (set_id,),
            ).fetchall()
            return [self._item_dict(row) for row in rows]

    def get_item(self, item_id: str) -> dict[str, object]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM combo_kit_items WHERE item_id = ?", (item_id,)
            ).fetchone()
            if row is None:
                raise KeyError(item_id)
            return self._item_dict(row)

    def add_item(self, values: dict[str, object]) -> dict[str, object]:
        item_id = str(values.get("item_id") or _uuid())
        values["item_id"] = item_id
        cols = ("item_id", "set_id", "workspace_id", "owner_user_id", "item_index",
                "original_asset_id", "original_path", "original_url", "subject_keywords",
                "mask_json", "mask_inverted", "mask_regex_serial", "subject_parsed_json",
                "spec_text", "is_primary", "width", "height", "error_message", "created_at", "updated_at")
        with self._connect() as connection:
            connection.execute(
                f"INSERT INTO combo_kit_items ({_cols(cols)}) VALUES ({_hole(len(cols))})",
                tuple(values.get(c) if values.get(c) is not None else _default_for(c) for c in cols),
            )
        return self.get_item(item_id)

    def update_item(self, item_id: str, values: dict[str, object]) -> dict[str, object]:
        allowed = ("subject_keywords", "mask_json", "mask_inverted", "mask_regex_serial",
                   "subject_parsed_json", "spec_text", "is_primary", "original_url",
                   "original_path", "error_message", "item_index")
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM combo_kit_items WHERE item_id = ?", (item_id,)
            ).fetchone()
            if existing is None:
                raise KeyError(item_id)
            assignments = []
            params: list[object] = []
            for key in allowed:
                if key in values:
                    assignments.append(f"{key} = ?")
                    params.append(values[key])
            assignments.append("updated_at = ?")
            params.append(_now())
            params.append(item_id)
            connection.execute(
                f"UPDATE combo_kit_items SET {', '.join(assignments)} WHERE item_id = ?",
                tuple(params),
            )
        return self.get_item(item_id)

    def remove_item(self, set_id: str, item_id: str) -> bool:
        with self._connect() as connection:
            result = connection.execute(
                "DELETE FROM combo_kit_items WHERE set_id = ? AND item_id = ?",
                (set_id, item_id),
            )
            return bool(result.rowcount)

    def set_primary_item(self, set_id: str, item_id: str) -> dict[str, object]:
        """把该 set 的某成员设为唯一主要商品（其余成员 is_primary 归零，原子）。"""
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM combo_kit_items WHERE set_id = ? AND item_id = ?",
                (set_id, item_id),
            ).fetchone()
            if exists is None:
                raise KeyError(item_id)
            connection.execute(
                "UPDATE combo_kit_items SET is_primary = 0, updated_at = ? WHERE set_id = ?",
                (_now(), set_id),
            )
            connection.execute(
                "UPDATE combo_kit_items SET is_primary = 1, updated_at = ? WHERE item_id = ?",
                (_now(), item_id),
            )
        return self.get_item(item_id)

    def clear_primary_item(self, set_id: str) -> None:
        """取消该 set 的主要商品标记（全部归零）。"""
        with self._connect() as connection:
            connection.execute(
                "UPDATE combo_kit_items SET is_primary = 0, updated_at = ? WHERE set_id = ?",
                (_now(), set_id),
            )

    def remove_set(self, set_id: str) -> bool:
        """级联删除一个组合套装及其所有子记录（素材/Prompt/任务/扣费/预检）。"""
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM combo_kit_sets WHERE set_id = ?", (set_id,)
            ).fetchone()
            if exists is None:
                return False
            for table in (
                "combo_kit_items",
                "combo_kit_prompts",
                "combo_kit_tasks",
                "combo_kit_billing",
                "combo_kit_previews",
            ):
                connection.execute(f"DELETE FROM {table} WHERE set_id = ?", (set_id,))
            connection.execute("DELETE FROM combo_kit_sets WHERE set_id = ?", (set_id,))
            return True

    def _item_dict(self, row: sqlite3.Row) -> dict[str, object]:
        data = self._row_keys(row)
        data["mask_inverted"] = bool(data.get("mask_inverted"))
        data["is_primary"] = bool(data.get("is_primary"))
        return _parse_json_fields(data, ("mask_json", "subject_parsed_json"))

    # ---- Prompt 配置 ----

    def upsert_prompt(self, values: dict[str, object]) -> dict[str, object]:
        set_id = str(values.get("set_id") or "")
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM combo_kit_prompts WHERE set_id = ?", (set_id,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO combo_kit_prompts "
                    "(prompt_id, set_id, workspace_id, owner_user_id, base_prompt_a, "
                    "base_prompt_b, image_prompts_json, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (_uuid(), set_id, str(values.get("workspace_id") or "local"),
                     str(values.get("owner_user_id") or ""),
                     str(values.get("base_prompt_a") or ""),
                     str(values.get("base_prompt_b") or ""),
                     values.get("image_prompts_json") or "{}", _now()),
                )
            else:
                connection.execute(
                    "UPDATE combo_kit_prompts SET base_prompt_a = ?, base_prompt_b = ?, "
                    "image_prompts_json = ?, updated_at = ? WHERE set_id = ?",
                    (str(values.get("base_prompt_a") or ""),
                     str(values.get("base_prompt_b") or ""),
                     values.get("image_prompts_json") or "{}", _now(), set_id),
                )
        return self.get_prompt(set_id)

    def get_prompt(self, set_id: str) -> dict[str, object]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM combo_kit_prompts WHERE set_id = ?", (set_id,)
            ).fetchone()
            if row is None:
                raise KeyError(set_id)
            data = self._row_keys(row)
            return _parse_json_fields(data, ("image_prompts_json",))

    # ---- AI 任务 ----

    def upsert_task(self, values: dict[str, object]) -> dict[str, object]:
        set_id = str(values.get("set_id") or "")
        task_type = str(values.get("task_type") or "")
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM combo_kit_tasks WHERE set_id = ? AND task_type = ?",
                (set_id, task_type),
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO combo_kit_tasks "
                    "(task_id, set_id, workspace_id, owner_user_id, task_type, status, "
                    "prompt_snapshot_json, result_json, attempt_count, error_kind, "
                    "error_message, created_at, started_at, finished_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (_uuid(), set_id, str(values.get("workspace_id") or "local"),
                     str(values.get("owner_user_id") or ""), task_type,
                     str(values.get("status") or "queued"),
                     values.get("prompt_snapshot_json") or "{}",
                     values.get("result_json") or "{}",
                     int(values.get("attempt_count") or 0),
                     str(values.get("error_kind") or ""),
                     str(values.get("error_message") or ""),
                     _now(), values.get("started_at"), values.get("finished_at")),
                )
            else:
                connection.execute(
                    "UPDATE combo_kit_tasks SET status = ?, result_json = ?, attempt_count = ?, "
                    "error_kind = ?, error_message = ?, started_at = ?, finished_at = ? "
                    "WHERE set_id = ? AND task_type = ?",
                    (str(values.get("status") or "queued"), values.get("result_json") or "{}",
                     int(values.get("attempt_count") or 0), str(values.get("error_kind") or ""),
                     str(values.get("error_message") or ""), values.get("started_at"),
                     values.get("finished_at"), set_id, task_type),
                )
        return self.get_task(set_id, task_type)

    def get_task(self, set_id: str, task_type: str) -> dict[str, object]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM combo_kit_tasks WHERE set_id = ? AND task_type = ?",
                (set_id, task_type),
            ).fetchone()
            if row is None:
                raise KeyError(set_id)
            data = self._row_keys(row)
            return _parse_json_fields(data, ("prompt_snapshot_json", "result_json"))

    # ---- 扣费记录 ----

    def add_billing(self, values: dict[str, object]) -> dict[str, object]:
        billing_id = str(values.get("billing_id") or _uuid())
        values["billing_id"] = billing_id
        cols = ("billing_id", "set_id", "workspace_id", "owner_user_id", "billing_type",
                "freeze_id", "rule_version", "points", "status", "result_status",
                "settled_at", "error_message", "created_at", "updated_at")
        with self._connect() as connection:
            connection.execute(
                f"INSERT INTO combo_kit_billing ({_cols(cols)}) VALUES ({_hole(len(cols))})",
                tuple(values.get(c) if values.get(c) is not None else _default_for(c) for c in cols),
            )
        return self.get_billing(billing_id)

    def update_billing(self, billing_id: str, values: dict[str, object]) -> dict[str, object]:
        allowed = ("status", "result_status", "settled_at", "error_message", "freeze_id",
                   "rule_version", "points")
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM combo_kit_billing WHERE billing_id = ?", (billing_id,)
            ).fetchone()
            if existing is None:
                raise KeyError(billing_id)
            assignments = []
            params: list[object] = []
            for key in allowed:
                if key in values:
                    assignments.append(f"{key} = ?")
                    params.append(values[key])
            assignments.append("updated_at = ?")
            params.append(_now())
            params.append(billing_id)
            connection.execute(
                f"UPDATE combo_kit_billing SET {', '.join(assignments)} WHERE billing_id = ?",
                tuple(params),
            )
        return self.get_billing(billing_id)

    def get_billing(self, billing_id: str) -> dict[str, object]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM combo_kit_billing WHERE billing_id = ?", (billing_id,)
            ).fetchone()
            if row is None:
                raise KeyError(billing_id)
            return self._row_keys(row)

    def list_billing(self, set_id: str) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM combo_kit_billing WHERE set_id = ? ORDER BY created_at ASC",
                (set_id,),
            ).fetchall()
            return [self._row_keys(row) for row in rows]

    # ---- 预检 ----

    def upsert_preview(self, values: dict[str, object]) -> dict[str, object]:
        set_id = str(values.get("set_id") or "")
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM combo_kit_previews WHERE set_id = ?", (set_id,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO combo_kit_previews "
                    "(preview_id, set_id, workspace_id, owner_user_id, status, payload_json, "
                    "reject_reason, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (_uuid(), set_id, str(values.get("workspace_id") or "local"),
                     str(values.get("owner_user_id") or ""), str(values.get("status") or "pending"),
                     values.get("payload_json") or "{}", str(values.get("reject_reason") or ""),
                     _now(), _now()),
                )
            else:
                connection.execute(
                    "UPDATE combo_kit_previews SET status = ?, payload_json = ?, "
                    "reject_reason = ?, updated_at = ? WHERE set_id = ?",
                    (str(values.get("status") or "pending"), values.get("payload_json") or "{}",
                     str(values.get("reject_reason") or ""), _now(), set_id),
                )
        return self.get_preview(set_id)

    def get_preview(self, set_id: str) -> dict[str, object]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM combo_kit_previews WHERE set_id = ?", (set_id,)
            ).fetchone()
            if row is None:
                raise KeyError(set_id)
            data = self._row_keys(row)
            return _parse_json_fields(data, ("payload_json",))

    @staticmethod
    def _row_keys(row: sqlite3.Row) -> dict[str, object]:
        return {key: row[key] for key in row.keys()}


def _uuid() -> str:
    import uuid

    return uuid.uuid4().hex


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cols(names) -> str:
    return ", ".join(names)


def _hole(count: int) -> str:
    return ", ".join("?" for _ in range(count))


def _default_for(column: str) -> object:
    return {
        "owner_user_id": "",
        "name": "",
        "sku": "",
        "sku_display": "",
        "description": "",
        "fusion_prompt": "",
        "category_path": "",
        "category_id": "",
        "status": "draft",
        "stage": "",
        "error_message": "",
        "bullets_json": "[]",
        "attributes_json": "{}",
        "sku_specs_json": "[]",
        "text_result_json": "{}",
        "image_results_json": "[]",
        "declared_price": "",
        "length_cm": 0,
        "width_cm": 0,
        "height_cm": 0,
        "weight_g": 0,
        "stock": 0,
        "category_name": "",
        "suggested_price_usd": 0,
        "id_type": "",
        "id_code": "",
        "subject_keywords": "",
        "mask_json": "{}",
        "subject_parsed_json": "{}",
        "spec_text": "",
        "is_primary": 0,
        "original_asset_id": "",
        "original_path": "",
        "original_url": "",
        "mask_inverted": 0,
        "mask_regex_serial": 0,
        "width": 0,
        "height": 0,
        "freeze_id": "",
        "rule_version": 0,
        "points": 0,
        "status_billing": "frozen",
        "result_status": "",
        "settled_at": None,
        "created_at": "",
        "updated_at": "",
    }.get(column, "")


_LIST_JSON_FIELDS = {"bullets_json", "image_results_json", "sku_specs_json"}


def _parse_json_fields(data: dict[str, object], fields: tuple[str, ...]) -> dict[str, object]:
    import json

    for field in fields:
        raw = data.get(field)
        if raw is None or str(raw) == "":
            data[field] = [] if field in _LIST_JSON_FIELDS else {}
            continue
        try:
            data[field] = json.loads(str(raw))
        except (ValueError, TypeError):
            data[field] = raw
    return data
