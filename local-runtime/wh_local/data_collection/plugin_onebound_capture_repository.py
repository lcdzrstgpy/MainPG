"""Durable audit storage for ephemeral browser-plugin OneBound batches."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..db import connect, init_db


class PluginOneBoundCaptureRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        init_db(self.database_path)
        with connect(self.database_path) as conn:
            marker = "data_collection:008_plugin_onebound_capture_batches"
            if conn.execute("SELECT 1 FROM schema_migrations WHERE migration_id = ?", (marker,)).fetchone() is None:
                sql = Path(__file__).with_name("migrations").joinpath("008_plugin_onebound_capture_batches.sql").read_text(encoding="utf-8")
                conn.executescript(sql)
                conn.execute("INSERT OR IGNORE INTO schema_migrations (migration_id, module) VALUES (?, 'data_collection')", (marker,))
            self._backfill_legacy_plugin_onebound(conn)
            self._reconcile_stale_active_batches(conn)

    @staticmethod
    def _backfill_legacy_plugin_onebound(conn: Any) -> None:
        """Conservatively retain only unambiguous legacy plugin OneBound drafts.

        Daily-selection and whole-shop runs have their own durable owners and
        are intentionally excluded rather than guessed into plugin history.
        """
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='product_processing_drafts'").fetchone()
        if exists is None:
            return
        rows = conn.execute("""SELECT draft.id, draft.workspace_id, draft.selection_run_id, draft.candidate_id,
            draft.source_ref, draft.title, draft.product_name, draft.status
            FROM product_processing_drafts AS draft
            LEFT JOIN daily_selection_runs AS daily ON daily.workspace_id=draft.workspace_id AND daily.run_id=draft.selection_run_id
            LEFT JOIN shop_collection_batches AS shop ON shop.workspace_id=draft.workspace_id AND shop.batch_id=draft.selection_run_id
            WHERE draft.source_type='onebound_api' AND draft.selection_run_id IS NOT NULL AND draft.selection_run_id<>''
              AND daily.run_id IS NULL AND shop.batch_id IS NULL""").fetchall()
        batches: dict[tuple[str, str], list[tuple[str, str, str, int]]] = {}
        for row in rows:
            batch_id = str(row["selection_run_id"])
            offer_id = str(row["candidate_id"] or "").removeprefix("1688:")
            source_url = str(row["source_ref"] or "")
            if not offer_id or not source_url.startswith(("http://", "https://")):
                continue
            source_title = str(row["title"] or row["product_name"] or "").strip()
            batches.setdefault((str(row["workspace_id"]), batch_id), []).append(
                (offer_id, source_url, source_title, int(row["id"]))
            )
        duplicate_run_ids = {
            batch_id
            for _workspace_id, batch_id in batches
            if sum(candidate_batch_id == batch_id for _candidate_workspace_id, candidate_batch_id in batches) > 1
        }
        for (workspace_id, selection_run_id), items in batches.items():
            batch_id = (
                str(uuid.uuid5(uuid.NAMESPACE_URL, f"plugin-onebound-legacy/{workspace_id}/{selection_run_id}"))
                if selection_run_id in duplicate_run_ids
                else selection_run_id
            )
            conn.execute("""INSERT OR IGNORE INTO plugin_onebound_capture_batches
                (batch_id, workspace_id, actor_id, status, total_count, created_count, completed_at)
                VALUES (?, ?, 'legacy-backfill', 'completed', ?, ?, datetime('now'))""",
                (batch_id, workspace_id, len(items), len(items)))
            conn.executemany("""INSERT INTO plugin_onebound_capture_items
                (batch_id, offer_id, source_url, source_title, status, outcome, draft_id)
                VALUES (?, ?, ?, ?, 'succeeded', 'created', ?)
                ON CONFLICT(batch_id, offer_id) DO UPDATE SET
                    source_title=excluded.source_title, draft_id=excluded.draft_id""",
                [(batch_id, offer_id, source_url, source_title, draft_id)
                 for offer_id, source_url, source_title, draft_id in items])
            conn.execute("""UPDATE plugin_onebound_capture_batches
                SET status='completed', total_count=(SELECT COUNT(*) FROM plugin_onebound_capture_items WHERE batch_id=?),
                    created_count=(SELECT COUNT(*) FROM plugin_onebound_capture_items WHERE batch_id=? AND outcome='created'),
                    refreshed_count=(SELECT COUNT(*) FROM plugin_onebound_capture_items WHERE batch_id=? AND outcome='refreshed'),
                    skipped_count=(SELECT COUNT(*) FROM plugin_onebound_capture_items WHERE batch_id=? AND status='skipped'),
                    failed_count=(SELECT COUNT(*) FROM plugin_onebound_capture_items WHERE batch_id=? AND status='failed'),
                    unprocessed_count=(SELECT COUNT(*) FROM plugin_onebound_capture_items WHERE batch_id=? AND status IN ('pending','running','unprocessed')),
                    completed_at=COALESCE(completed_at, datetime('now')), updated_at=datetime('now')
                WHERE batch_id=? AND workspace_id=? AND actor_id='legacy-backfill'""",
                (batch_id, batch_id, batch_id, batch_id, batch_id, batch_id, batch_id, workspace_id))

    def _reconcile_stale_active_batches(self, conn: Any) -> None:
        rows = conn.execute("""SELECT batch_id FROM plugin_onebound_capture_batches
            WHERE status IN ('prepared', 'queued', 'running')
              AND datetime(COALESCE(NULLIF(updated_at, ''), created_at)) < datetime('now', '-30 minutes')""").fetchall()
        for row in rows:
            batch_id = str(row["batch_id"])
            conn.execute("""UPDATE plugin_onebound_capture_items
                SET status='unprocessed', outcome='unprocessed',
                    error_message=CASE WHEN error_message='' THEN '扩展会话已过期，未执行' ELSE error_message END,
                    updated_at=datetime('now')
                WHERE batch_id=? AND status IN ('pending', 'running')""", (batch_id,))
            self._refresh_batch_counts(conn, batch_id)
            conn.execute("""UPDATE plugin_onebound_capture_batches
                SET status='expired', cancelled=0, error_code='capture_expired',
                    error_message='扩展会话已过期，批次已结束',
                    completed_at=COALESCE(completed_at, datetime('now')), updated_at=datetime('now')
                WHERE batch_id=?""", (batch_id,))

    @staticmethod
    def _refresh_batch_counts(conn: Any, batch_id: str) -> None:
        conn.execute("""UPDATE plugin_onebound_capture_batches
            SET created_count=(SELECT COUNT(*) FROM plugin_onebound_capture_items WHERE batch_id=? AND outcome='created'),
                refreshed_count=(SELECT COUNT(*) FROM plugin_onebound_capture_items WHERE batch_id=? AND outcome='refreshed'),
                skipped_count=(SELECT COUNT(*) FROM plugin_onebound_capture_items WHERE batch_id=? AND status='skipped'),
                failed_count=(SELECT COUNT(*) FROM plugin_onebound_capture_items WHERE batch_id=? AND status='failed'),
                unprocessed_count=(SELECT COUNT(*) FROM plugin_onebound_capture_items WHERE batch_id=? AND status IN ('pending','running','unprocessed')),
                updated_at=datetime('now')
            WHERE batch_id=?""", (batch_id, batch_id, batch_id, batch_id, batch_id, batch_id))

    def create(self, *, batch_id: str, actor_id: str, workspace_id: str, page_url: str = "", parent_batch_id: str = "", items: Sequence[Mapping[str, str]]) -> None:
        total_count = len(items)
        created_count = sum(item.get("outcome") == "created" for item in items)
        refreshed_count = sum(item.get("outcome") == "refreshed" for item in items)
        skipped_count = sum(item.get("status") == "skipped" for item in items)
        failed_count = sum(item.get("status") == "failed" for item in items)
        unprocessed_count = sum(item.get("status", "pending") in {"pending", "running", "unprocessed"} for item in items)
        with connect(self.database_path) as conn:
            conn.execute("""INSERT INTO plugin_onebound_capture_batches
                (batch_id, parent_batch_id, actor_id, workspace_id, page_url, total_count, created_count,
                 refreshed_count, skipped_count, failed_count, unprocessed_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (batch_id, parent_batch_id, actor_id, workspace_id, page_url, total_count, created_count,
                 refreshed_count, skipped_count, failed_count, unprocessed_count))
            conn.executemany("""INSERT INTO plugin_onebound_capture_items
                (batch_id, offer_id, source_url, source_title, status, outcome) VALUES (?, ?, ?, ?, ?, ?)""",
                [(batch_id, item["offer_id"], item["source_url"], item.get("source_title", ""),
                  item.get("status", "pending"), item.get("outcome", "")) for item in items])

    def set_status(self, batch_id: str, status: str, *, cancelled: bool = False, error_code: str = "", error_message: str = "", summary: Mapping[str, int] | None = None) -> None:
        if summary is None:
            with connect(self.database_path) as conn:
                conn.execute("""UPDATE plugin_onebound_capture_batches
                    SET status=?, cancelled=?, error_code=?, error_message=?, updated_at=datetime('now')
                    WHERE batch_id=?""", (status, int(cancelled), error_code, error_message, batch_id))
            return
        values = summary or {}
        with connect(self.database_path) as conn:
            conn.execute("""UPDATE plugin_onebound_capture_batches SET status = ?, cancelled = ?, error_code = ?,
                error_message = ?, created_count = ?, refreshed_count = ?, skipped_count = ?, failed_count = ?, unprocessed_count = ?,
                completed_at = CASE WHEN ? IN ('completed','partial','cancelled','failed','expired') THEN datetime('now') ELSE completed_at END, updated_at = datetime('now') WHERE batch_id = ?""", (status, int(cancelled), error_code, error_message, int(values.get("created_count", 0)), int(values.get("refreshed_count", 0)), int(values.get("skipped_count", 0)), int(values.get("failed_count", 0)), int(values.get("unprocessed_count", 0)), status, batch_id))

    def update_item(self, batch_id: str, offer_id: str, *, status: str, outcome: str = "", draft_id: int | None = None, source_title: str = "", error_code: str = "", error_message: str = "", increment_attempt: bool = False) -> None:
        with connect(self.database_path) as conn:
            conn.execute("""UPDATE plugin_onebound_capture_items
                SET status=?, outcome=?, draft_id=?, source_title=CASE WHEN ?<>'' THEN ? ELSE source_title END,
                    error_code=?, error_message=?, attempts=attempts+?, updated_at=datetime('now')
                WHERE batch_id=? AND offer_id=?""",
                (status, outcome, draft_id, source_title, source_title, error_code, error_message,
                 int(increment_attempt), batch_id, offer_id))
            self._refresh_batch_counts(conn, batch_id)

    def get(self, *, workspace_id: str, batch_id: str) -> Mapping[str, Any] | None:
        with connect(self.database_path) as conn:
            self._reconcile_stale_active_batches(conn)
            row = conn.execute("SELECT * FROM plugin_onebound_capture_batches WHERE workspace_id=? AND batch_id=?", (workspace_id, batch_id)).fetchone()
        return dict(row) if row else None

    def list(self, *, workspace_id: str, limit: int, offset: int) -> tuple[Mapping[str, Any], ...]:
        with connect(self.database_path) as conn:
            self._reconcile_stale_active_batches(conn)
            rows = conn.execute("SELECT * FROM plugin_onebound_capture_batches WHERE workspace_id=? ORDER BY created_at DESC, batch_id DESC LIMIT ? OFFSET ?", (workspace_id, limit, offset)).fetchall()
        return tuple(dict(row) for row in rows)

    def count(self, *, workspace_id: str) -> int:
        with connect(self.database_path) as conn:
            self._reconcile_stale_active_batches(conn)
            return int(conn.execute("SELECT COUNT(*) FROM plugin_onebound_capture_batches WHERE workspace_id=?", (workspace_id,)).fetchone()[0])

    def items(self, *, workspace_id: str, batch_id: str, limit: int = 200, offset: int = 0) -> tuple[Mapping[str, Any], ...]:
        if self.get(workspace_id=workspace_id, batch_id=batch_id) is None:
            return ()
        with connect(self.database_path) as conn:
            rows = conn.execute("SELECT * FROM plugin_onebound_capture_items WHERE batch_id=? ORDER BY offer_id LIMIT ? OFFSET ?", (batch_id, limit, offset)).fetchall()
        return tuple(dict(row) for row in rows)

    def count_items(self, *, workspace_id: str, batch_id: str) -> int:
        if self.get(workspace_id=workspace_id, batch_id=batch_id) is None:
            return 0
        with connect(self.database_path) as conn:
            return int(conn.execute("SELECT COUNT(*) FROM plugin_onebound_capture_items WHERE batch_id=?", (batch_id,)).fetchone()[0])

    def failed_urls(self, *, workspace_id: str, batch_id: str) -> tuple[str, ...]:
        return tuple(str(item["source_url"]) for item in self.items(workspace_id=workspace_id, batch_id=batch_id) if item["status"] == "failed")

    def retry_child(self, *, workspace_id: str, parent_batch_id: str) -> Mapping[str, Any] | None:
        with connect(self.database_path) as conn:
            self._reconcile_stale_active_batches(conn)
            row = conn.execute("""SELECT * FROM plugin_onebound_capture_batches
                WHERE workspace_id=? AND parent_batch_id=?
                ORDER BY CASE status
                    WHEN 'completed' THEN 0 WHEN 'partial' THEN 0
                    WHEN 'prepared' THEN 1 WHEN 'queued' THEN 1 WHEN 'running' THEN 1
                    ELSE 2 END, updated_at DESC, batch_id DESC LIMIT 1""",
                (workspace_id, parent_batch_id)).fetchone()
        return dict(row) if row else None
