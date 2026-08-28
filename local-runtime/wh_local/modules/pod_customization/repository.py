from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ...pod_migrations import (
    ensure_pod_migration,
    pod_migration_effect_is_present,
    recover_interrupted_pod_migrations,
)
from .billing_contract import PodCallOutcome, PodCallPlan, PodExecutionGrant
from .contracts import BatchCreate, Calibration, grid_call_count, style_grid_call_count
from .errors import safe_error_message
from .prompts import build_direct_listing_prompt


def _safe_error(value: object) -> str:
    return safe_error_message(value) if str(value or "").strip() else ""


class PodRepositoryError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class PodCustomizationRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                       migration_id TEXT PRIMARY KEY,
                       module TEXT NOT NULL,
                       applied_at TEXT NOT NULL DEFAULT (datetime('now'))
                   )"""
            )
            migration_root = Path(__file__).with_name("migrations")
            migrations = sorted(migration_root.glob("[0-9][0-9][0-9]_*.sql"))
            migration_sql = {
                migration.stem: migration.read_text(encoding="utf-8")
                for migration in migrations
            }
            recover_interrupted_pod_migrations(connection, migration_sql)
            for migration in migrations:
                ensure_pod_migration(
                    connection,
                    migration.stem,
                    migration_sql[migration.stem],
                )

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

    def create_asset(
        self,
        *,
        workspace_id: str,
        owner_user_id: str,
        kind: str,
        filename: str,
        relative_path: str,
        content_type: str,
        byte_size: int,
        sha256: str,
        width: int,
        height: int,
    ) -> dict[str, Any]:
        asset_id = uuid.uuid4().hex
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO pod_customization_assets
                   (asset_id, workspace_id, owner_user_id, kind, filename, relative_path, content_type,
                    byte_size, sha256, width, height, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (asset_id, workspace_id, owner_user_id, kind, filename[:180], relative_path, content_type,
                 byte_size, sha256, width, height, now),
            )
        return self.get_asset(asset_id, workspace_id, owner_user_id)

    def get_asset(self, asset_id: str, workspace_id: str, owner_user_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM pod_customization_assets
                   WHERE asset_id = ? AND workspace_id = ?
                     AND (owner_user_id = ? OR kind = 'template')""",
                (asset_id, workspace_id, owner_user_id),
            ).fetchone()
        if row is None:
            raise PodRepositoryError("POD asset not found", 404)
        return dict(row)

    def create_template(
        self,
        *,
        workspace_id: str,
        owner_user_id: str,
        name: str,
        asset: dict[str, Any],
        source: str = "personal",
    ) -> dict[str, Any]:
        template_id = uuid.uuid4().hex
        snapshot_id = uuid.uuid4().hex
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO pod_customization_templates
                   (template_id, workspace_id, owner_user_id, name, source, asset_id, width, height,
                    calibration_status, calibration_json, version, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'null', 1, ?, ?)""",
                (template_id, workspace_id, owner_user_id, name[:120], source, asset["asset_id"],
                 asset["width"], asset["height"], now, now),
            )
            connection.execute(
                """INSERT INTO pod_customization_template_snapshots
                   (snapshot_id, template_id, workspace_id, owner_user_id, version, name, source,
                    asset_id, width, height, calibration_json, created_at)
                   VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, 'null', ?)""",
                (snapshot_id, template_id, workspace_id, owner_user_id, name[:120], source,
                 asset["asset_id"], asset["width"], asset["height"], now),
            )
        return self.get_template(template_id, workspace_id, owner_user_id)

    def update_template_calibration(
        self,
        template_id: str,
        workspace_id: str,
        owner_user_id: str,
        calibration: Calibration,
    ) -> dict[str, Any]:
        calibration_json = calibration.model_dump_json()
        now = _now()
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM pod_customization_templates
                   WHERE template_id = ? AND workspace_id = ? AND deleted_at = ''""",
                (template_id, workspace_id),
            ).fetchone()
            if row is None:
                raise PodRepositoryError("POD template not found", 404)
            version = int(row["version"]) + 1
            connection.execute(
                """UPDATE pod_customization_templates
                   SET calibration_status = 'ready', calibration_json = ?, error_message = '',
                       version = ?, updated_at = ?
                   WHERE template_id = ? AND workspace_id = ?""",
                (calibration_json, version, now, template_id, workspace_id),
            )
            connection.execute(
                """INSERT INTO pod_customization_template_snapshots
                   (snapshot_id, template_id, workspace_id, owner_user_id, version, name, source,
                    asset_id, width, height, calibration_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (uuid.uuid4().hex, template_id, workspace_id, row["owner_user_id"], version, row["name"], row["source"],
                 row["asset_id"], row["width"], row["height"], calibration_json, now),
            )
        return self.get_template(template_id, workspace_id, owner_user_id)

    def set_template_calibration_state(
        self,
        template_id: str,
        workspace_id: str,
        owner_user_id: str,
        status: str,
        error_message: str = "",
    ) -> dict[str, Any]:
        if status not in {"pending", "calibrating", "failed"}:
            raise ValueError("invalid template calibration state")
        with self._connect() as connection:
            result = connection.execute(
                """UPDATE pod_customization_templates
                   SET calibration_status = ?, error_message = ?, updated_at = ?
                   WHERE template_id = ? AND workspace_id = ? AND deleted_at = ''""",
                (status, _safe_error(error_message), _now(), template_id, workspace_id),
            )
        if result.rowcount != 1:
            raise PodRepositoryError("POD template not found", 404)
        return self.get_template(template_id, workspace_id, owner_user_id)

    def get_template(self, template_id: str, workspace_id: str, owner_user_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM pod_customization_templates
                   WHERE template_id = ? AND workspace_id = ? AND deleted_at = ''""",
                (template_id, workspace_id),
            ).fetchone()
        if row is None:
            raise PodRepositoryError("POD template not found", 404)
        return dict(row)

    def list_templates(self, workspace_id: str, owner_user_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM pod_customization_templates
                   WHERE workspace_id = ? AND deleted_at = ''
                   ORDER BY updated_at DESC, template_id""",
                (workspace_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_template_snapshots(self, template_id: str, workspace_id: str, owner_user_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM pod_customization_template_snapshots
                   WHERE template_id = ? AND workspace_id = ? ORDER BY version""",
                (template_id, workspace_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_direct_listing_trial(
        self,
        *,
        trial_id: str,
        workspace_id: str,
        owner_user_id: str,
        template_id: str,
        status: str,
        prompt_snapshot: str,
        grid_attempt_asset_ids: list[str],
        panel_asset_ids: dict[str, str],
        public_urls: dict[str, str],
        error_message: str = "",
        title_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in {"completed", "failed"}:
            raise ValueError("invalid direct listing trial status")
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO pod_customization_direct_listing_trials
                   (trial_id, workspace_id, owner_user_id, template_id, status, prompt_snapshot,
                    grid_attempt_asset_ids_json, panel_asset_ids_json, public_urls_json, error_message,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trial_id,
                    workspace_id,
                    owner_user_id,
                    template_id,
                    status,
                    prompt_snapshot,
                    json.dumps(grid_attempt_asset_ids),
                    json.dumps(panel_asset_ids),
                    json.dumps(public_urls),
                    _safe_error(error_message),
                    now,
                    now,
                ),
            )
            if title_result is not None:
                self._insert_direct_title(connection, trial_id, title_result, now)
        return self.get_direct_listing_trial(trial_id, workspace_id, owner_user_id)

    def get_direct_listing_trial(
        self, trial_id: str, workspace_id: str, owner_user_id: str
    ) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM pod_customization_direct_listing_trials
                   WHERE trial_id = ? AND workspace_id = ? AND owner_user_id = ?""",
                (trial_id, workspace_id, owner_user_id),
            ).fetchone()
            title_row = connection.execute(
                "SELECT * FROM pod_customization_direct_listing_titles WHERE trial_id = ?",
                (trial_id,),
            ).fetchone()
        if row is None:
            raise PodRepositoryError("POD direct listing trial not found", 404)
        result = dict(row)
        result["grid_attempt_asset_ids"] = json.loads(result.pop("grid_attempt_asset_ids_json"))
        result["panel_asset_ids"] = json.loads(result.pop("panel_asset_ids_json"))
        result["public_urls"] = json.loads(result.pop("public_urls_json"))
        result["title_result"] = self._decode_title_row(title_row) if title_row else None
        if result["title_result"] is not None:
            result["title_result"]["listing_ready"] = (
                result["title_result"]["status"] == "completed"
                and len([url for url in result["public_urls"].values() if url]) == 4
            )
        return result

    def list_direct_listing_trials(
        self, workspace_id: str, owner_user_id: str, *, limit: int = 20
    ) -> tuple[list[dict[str, Any]], int]:
        with self._connect() as connection:
            total = int(
                connection.execute(
                    """SELECT COUNT(*) FROM pod_customization_direct_listing_trials
                       WHERE workspace_id = ? AND owner_user_id = ?""",
                    (workspace_id, owner_user_id),
                ).fetchone()[0]
            )
            rows = connection.execute(
                """SELECT trial_id FROM pod_customization_direct_listing_trials
                   WHERE workspace_id = ? AND owner_user_id = ?
                   ORDER BY created_at DESC, trial_id LIMIT ?""",
                (workspace_id, owner_user_id, max(1, min(limit, 100))),
            ).fetchall()
        return [self.get_direct_listing_trial(row["trial_id"], workspace_id, owner_user_id) for row in rows], total

    def create_batch(
        self,
        workspace_id: str,
        owner_user_id: str,
        request: BatchCreate,
        *,
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        batch_id = str(batch_id or uuid.uuid4().hex)
        now = _now()
        prompt_snapshot = build_direct_listing_prompt(request.business_fields, request.creative_prompt)
        # New batches are isolated from legacy batches by a companion row.  Do
        # not reinterpret an existing batch while another runtime is working it.
        initial_calls = style_grid_call_count(request.count)
        max_refills = 0
        with self._connect() as connection:
            template = connection.execute(
                """SELECT * FROM pod_customization_templates
                   WHERE template_id = ? AND workspace_id = ? AND deleted_at = ''""",
                (request.template_id, workspace_id),
            ).fetchone()
            if template is None:
                raise PodRepositoryError("POD template not found", 404)
            if template["calibration_status"] != "ready":
                raise PodRepositoryError("POD template must be calibrated before use", 409)
            snapshot = connection.execute(
                """SELECT * FROM pod_customization_template_snapshots
                   WHERE template_id = ? AND workspace_id = ? AND version = ?""",
                (request.template_id, workspace_id, template["version"]),
            ).fetchone()
            if snapshot is None:
                raise PodRepositoryError("POD template snapshot is unavailable", 409)
            title = request.title.strip() or request.business_fields.product_name.strip() or template["name"]
            connection.execute(
                """INSERT INTO pod_customization_batches
                   (batch_id, workspace_id, owner_user_id, title, status, template_id, template_snapshot_id,
                    template_name, requested_count, initial_call_count, max_refill_calls, prompt_version,
                    prompt_snapshot, business_fields_json, listing_fields_json, creative_prompt, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (batch_id, workspace_id, owner_user_id, title[:120], request.template_id, snapshot["snapshot_id"],
                 snapshot["name"], request.count, initial_calls, max_refills, request.prompt_version,
                 prompt_snapshot, request.business_fields.model_dump_json(), request.listing_fields.model_dump_json(),
                 request.creative_prompt, now, now),
            )
            connection.executemany(
                """INSERT INTO pod_customization_style_grid_results
                   (result_id, batch_id, workspace_id, owner_user_id, style_index, variant_index, status,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)""",
                [
                    (uuid.uuid4().hex, batch_id, workspace_id, owner_user_id, style_index, variant_index, now, now)
                    for style_index in range(1, request.count + 1)
                    for variant_index in range(1, 5)
                ],
            )
            connection.execute(
                """INSERT INTO pod_customization_style_grid_batches (batch_id, created_at) VALUES (?, ?)""",
                (batch_id, now),
            )
            connection.executemany(
                """INSERT INTO pod_customization_style_titles
                   (batch_id, style_index, status, created_at, updated_at)
                   VALUES (?, ?, 'queued', ?, ?)""",
                [(batch_id, style_index, now, now) for style_index in range(1, request.count + 1)],
            )
        return self.get_batch(batch_id, workspace_id, owner_user_id)

    def preflight_batch(self, workspace_id: str, owner_user_id: str, request: BatchCreate) -> None:
        """Validate all stable local prerequisites before remote points are frozen."""
        del owner_user_id
        with self._connect() as connection:
            template = connection.execute(
                """SELECT template_id, version, calibration_status
                   FROM pod_customization_templates
                   WHERE template_id = ? AND workspace_id = ? AND deleted_at = ''""",
                (request.template_id, workspace_id),
            ).fetchone()
            if template is None:
                raise PodRepositoryError("POD template not found", 404)
            if template["calibration_status"] != "ready":
                raise PodRepositoryError("POD template must be calibrated before use", 409)
            snapshot = connection.execute(
                """SELECT 1 FROM pod_customization_template_snapshots
                   WHERE template_id = ? AND workspace_id = ? AND version = ?""",
                (request.template_id, workspace_id, template["version"]),
            ).fetchone()
            if snapshot is None:
                raise PodRepositoryError("POD template snapshot is unavailable", 409)

    def get_batch(self, batch_id: str, workspace_id: str, owner_user_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            batch = connection.execute(
                """SELECT * FROM pod_customization_batches
                   WHERE batch_id = ? AND workspace_id = ? AND owner_user_id = ?""",
                (batch_id, workspace_id, owner_user_id),
            ).fetchone()
            if batch is None:
                raise PodRepositoryError("POD batch not found", 404)
            snapshot = connection.execute(
                """SELECT * FROM pod_customization_template_snapshots
                   WHERE snapshot_id = ? AND workspace_id = ?""",
                (batch["template_snapshot_id"], workspace_id),
            ).fetchone()
            style_grid = connection.execute(
                "SELECT 1 FROM pod_customization_style_grid_batches WHERE batch_id = ?", (batch_id,)
            ).fetchone() is not None
            items = connection.execute(
                """SELECT results.result_id AS item_id,
                          ((results.style_index - 1) * 4 + results.variant_index) AS item_index,
                          results.style_index, results.variant_index, results.status,
                          results.pattern_asset_id, results.composite_asset_id,
                          results.pattern_fingerprint, results.scene_optimized, results.error_message,
                          results.created_at, results.updated_at,
                          COALESCE(publications.role, '') AS role,
                          COALESCE(publications.public_url, '') AS public_url
                   FROM pod_customization_style_grid_results AS results
                   LEFT JOIN pod_customization_style_grid_publications AS publications
                     ON publications.result_id = results.result_id
                   WHERE results.batch_id = ? AND results.workspace_id = ? AND results.owner_user_id = ?
                   ORDER BY results.style_index, results.variant_index"""
                if style_grid else
                """SELECT *, item_index AS style_index, 1 AS variant_index FROM pod_customization_batch_items
                   WHERE batch_id = ? AND workspace_id = ? AND owner_user_id = ? ORDER BY item_index""",
                (batch_id, workspace_id, owner_user_id),
            ).fetchall()
            title_rows = connection.execute(
                """SELECT titles.*,
                          CASE WHEN titles.status = 'completed'
                            AND EXISTS (
                              SELECT 1 FROM pod_customization_style_copy AS copies
                              WHERE copies.batch_id = titles.batch_id
                                AND copies.style_index = titles.style_index
                                AND TRIM(copies.title) <> ''
                                AND TRIM(copies.english_title) <> ''
                                AND TRIM(copies.description) <> ''
                            ) AND
                            (SELECT COUNT(*) FROM pod_customization_style_grid_results AS results
                             INNER JOIN pod_customization_style_grid_publications AS publications
                               ON publications.result_id = results.result_id
                             WHERE results.batch_id = titles.batch_id
                               AND results.style_index = titles.style_index
                               AND results.status = 'completed'
                               AND publications.public_url <> '') = 4
                          THEN 1 ELSE 0 END AS listing_ready
                   FROM pod_customization_style_titles AS titles
                   WHERE titles.batch_id = ? ORDER BY titles.style_index""",
                (batch_id,),
            ).fetchall()
        result = dict(batch)
        result["business_fields"] = json.loads(result.pop("business_fields_json"))
        result["listing_fields"] = json.loads(result.pop("listing_fields_json"))
        if isinstance(result["listing_fields"], dict):
            result["listing_fields"].setdefault("title_mode", "long")
        result["template"] = dict(snapshot) if snapshot else None
        result["items"] = [dict(item) for item in items]
        result["style_grid"] = style_grid
        result["style_titles"] = [self._decode_title_row(row) for row in title_rows]
        result["title_completed_count"] = sum(row["status"] == "completed" for row in result["style_titles"])
        result["title_failed_count"] = sum(row["status"] == "failed" for row in result["style_titles"])
        result["listing_ready_count"] = sum(bool(row["listing_ready"]) for row in result["style_titles"])
        return result

    def get_style_copies(
        self, batch_id: str, workspace_id: str, owner_user_id: str
    ) -> dict[int, dict[str, str]]:
        with self._connect() as connection:
            batch = connection.execute(
                """SELECT 1 FROM pod_customization_batches
                   WHERE batch_id = ? AND workspace_id = ? AND owner_user_id = ?""",
                (batch_id, workspace_id, owner_user_id),
            ).fetchone()
            if batch is None:
                raise PodRepositoryError("POD batch not found", 404)
            rows = connection.execute(
                """SELECT copies.style_index, copies.title, copies.english_title,
                          copies.description, COALESCE(titles.source, 'ai') AS source
                   FROM pod_customization_style_copy AS copies
                   LEFT JOIN pod_customization_style_titles AS titles
                     ON titles.batch_id = copies.batch_id
                    AND titles.style_index = copies.style_index
                   WHERE copies.batch_id = ? ORDER BY copies.style_index""",
                (batch_id,),
            ).fetchall()
        return {
            int(row["style_index"]): {
                "title": row["title"],
                "english_title": row["english_title"],
                "description": row["description"],
                "source": row["source"],
            }
            for row in rows
        }

    def upsert_style_copy(
        self,
        batch_id: str,
        workspace_id: str,
        owner_user_id: str,
        style_index: int,
        *,
        title: str,
        english_title: str,
        description: str,
    ) -> dict[str, str]:
        values = self._style_copy_values(title, english_title, description)
        now = _now()
        with self._connect() as connection:
            self._require_owned_style(connection, batch_id, workspace_id, owner_user_id, style_index)
            self._upsert_style_copy_record(
                connection, batch_id, style_index, values=values, now=now
            )
        return values

    @staticmethod
    def _style_copy_values(title: Any, english_title: Any, description: Any) -> dict[str, str]:
        values = {
            "title": title.strip() if isinstance(title, str) else "",
            "english_title": english_title.strip() if isinstance(english_title, str) else "",
            "description": description.strip() if isinstance(description, str) else "",
        }
        if not all(values.values()):
            raise ValueError("title, english_title, and description are required")
        return values

    @staticmethod
    def _require_owned_style(
        connection: sqlite3.Connection,
        batch_id: str,
        workspace_id: str,
        owner_user_id: str,
        style_index: int,
    ) -> None:
        batch = connection.execute(
            """SELECT requested_count FROM pod_customization_batches
               WHERE batch_id = ? AND workspace_id = ? AND owner_user_id = ?""",
            (batch_id, workspace_id, owner_user_id),
        ).fetchone()
        if batch is None:
            raise PodRepositoryError("POD batch not found", 404)
        if (
            isinstance(style_index, bool)
            or not isinstance(style_index, int)
            or not 1 <= style_index <= int(batch["requested_count"])
        ):
            raise ValueError("style_index is outside the batch range")

    @staticmethod
    def _upsert_style_copy_record(
        connection: sqlite3.Connection,
        batch_id: str,
        style_index: int,
        *,
        values: dict[str, str],
        now: str,
    ) -> None:
        connection.execute(
            """INSERT INTO pod_customization_style_copy
               (batch_id, style_index, title, english_title, description, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(batch_id, style_index) DO UPDATE SET
                 title = excluded.title,
                 english_title = excluded.english_title,
                 description = excluded.description,
                 updated_at = excluded.updated_at""",
            (
                batch_id,
                style_index,
                values["title"],
                values["english_title"],
                values["description"],
                now,
                now,
            ),
        )

    def get_batch_internal(self, batch_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT workspace_id, owner_user_id FROM pod_customization_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
        if row is None:
            raise PodRepositoryError("POD batch not found", 404)
        return self.get_batch(batch_id, row["workspace_id"], row["owner_user_id"])

    def list_batches(
        self,
        workspace_id: str,
        owner_user_id: str,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        with self._connect() as connection:
            total = int(connection.execute(
                """SELECT COUNT(*) FROM pod_customization_batches
                   WHERE workspace_id = ? AND owner_user_id = ?""",
                (workspace_id, owner_user_id),
            ).fetchone()[0])
            rows = connection.execute(
                """SELECT * FROM pod_customization_batches
                   WHERE workspace_id = ? AND owner_user_id = ?
                   ORDER BY created_at DESC, batch_id LIMIT ? OFFSET ?""",
                (workspace_id, owner_user_id, limit, offset),
            ).fetchall()
        return [
            self.get_batch(row["batch_id"], workspace_id, owner_user_id)
            for row in rows
        ], total

    def recover_interrupted_batches(self) -> int:
        now = _now()
        message = "本机服务重启且短期凭据已丢弃；重新登录后可领取 grant 并重试失败款"
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT batches.batch_id FROM pod_customization_batches AS batches
                   INNER JOIN pod_customization_style_grid_batches AS style_grids
                     ON style_grids.batch_id = batches.batch_id
                   WHERE batches.status IN ('generating_patterns', 'compositing', 'generating_titles')"""
            ).fetchall()
            for row in rows:
                batch_id = row["batch_id"]
                resumable_billing = (
                    connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'pod_customization_billing_runs'"
                    ).fetchone()
                    and connection.execute(
                        """SELECT 1 FROM pod_customization_billing_runs
                           WHERE batch_id = ? AND status IN ('authorized', 'settling', 'auth_required')
                           LIMIT 1""",
                        (batch_id,),
                    ).fetchone()
                )
                connection.execute(
                    """UPDATE pod_customization_generation_calls
                       SET status = 'interrupted', error_message = ?, finished_at = ?
                       WHERE batch_id = ? AND status IN ('queued', 'running')""",
                    (message, now, batch_id),
                )
                if resumable_billing:
                    connection.execute(
                        """UPDATE pod_customization_batches
                           SET status = 'billing_auth_required', error_message = ?, updated_at = ?
                           WHERE batch_id = ?""",
                        (message, now, batch_id),
                    )
                    continue
                connection.execute(
                    """UPDATE pod_customization_style_titles
                       SET status = 'failed', error_message = ?, updated_at = ?, finished_at = ?
                       WHERE batch_id = ? AND status IN ('queued', 'generating')""",
                    (message, now, now, batch_id),
                )
                connection.execute(
                    """UPDATE pod_customization_batch_items
                       SET status = CASE
                             WHEN pattern_asset_id <> '' AND composite_asset_id <> '' THEN 'completed'
                             ELSE 'failed'
                           END,
                           error_message = CASE
                             WHEN pattern_asset_id <> '' AND composite_asset_id <> '' THEN error_message
                             ELSE ?
                           END,
                           updated_at = ?
                       WHERE batch_id = ? AND status IN ('queued', 'generating_pattern', 'compositing', 'optimizing_scene')""",
                    (message, now, batch_id),
                )
                connection.execute(
                    """UPDATE pod_customization_style_grid_results
                       SET status = CASE
                             WHEN pattern_asset_id <> '' AND composite_asset_id <> '' THEN 'completed'
                             ELSE 'failed'
                           END,
                           error_message = CASE
                             WHEN pattern_asset_id <> '' AND composite_asset_id <> '' THEN error_message
                             ELSE ?
                           END,
                           updated_at = ?
                       WHERE batch_id = ? AND status IN ('queued', 'generating_pattern', 'compositing', 'optimizing_scene')""",
                    (message, now, batch_id),
                )
                self._refresh_counts(connection, batch_id, now)
                counts = connection.execute(
                    """SELECT completed_count, failed_count FROM pod_customization_batches WHERE batch_id = ?""",
                    (batch_id,),
                ).fetchone()
                title_count = int(connection.execute(
                    "SELECT COUNT(*) FROM pod_customization_style_titles WHERE batch_id = ?", (batch_id,)
                ).fetchone()[0])
                connection.execute(
                    """UPDATE pod_customization_batches
                       SET status = ?, error_message = ?, updated_at = ?, finished_at = ? WHERE batch_id = ?""",
                    ("billing_auth_required", message, now, now, batch_id),
                )
        return len(rows)

    def list_queued_batch_ids(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT batches.batch_id FROM pod_customization_batches AS batches
                   INNER JOIN pod_customization_style_grid_batches AS style_grids
                     ON style_grids.batch_id = batches.batch_id
                   WHERE batches.status = 'queued' ORDER BY batches.created_at"""
            ).fetchall()
        return [row["batch_id"] for row in rows]

    def create_billing_run(
        self,
        *,
        action_key: str,
        action_type: str,
        target_id: str,
        batch_id: str,
        actor_id: str,
        workspace_id: str,
        plan: PodCallPlan,
        grant: PodExecutionGrant,
        action_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run_id = uuid.uuid4().hex
        now = _now()
        plan_json = json.dumps(
            {
                "idempotency_key": plan.idempotency_key,
                "calls": [call.payload() for call in plan.calls],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO pod_customization_billing_runs
                   (run_id, action_key, action_type, target_id, batch_id, workspace_id,
                    owner_user_id, freeze_id, rule_version, grant_expires_at, plan_json, action_payload_json,
                    status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'authorized', ?, ?)""",
                (
                    run_id,
                    action_key,
                    action_type,
                    target_id,
                    batch_id,
                    workspace_id,
                    actor_id,
                    grant.freeze_id,
                    grant.rule_version,
                    grant.expires_at,
                    plan_json,
                    json.dumps(action_payload or {}, ensure_ascii=False, separators=(",", ":")),
                    now,
                    now,
                ),
            )
            connection.executemany(
                """INSERT INTO pod_customization_billing_outcomes
                   (run_id, call_id, feature, status, updated_at)
                   VALUES (?, ?, ?, 'planned', ?)""",
                [(run_id, call.call_id, call.feature, now) for call in plan.calls],
            )
        return self.get_billing_run(run_id, workspace_id, actor_id)

    def get_billing_run(
        self, run_id: str, workspace_id: str, owner_user_id: str
    ) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM pod_customization_billing_runs
                   WHERE run_id = ? AND workspace_id = ? AND owner_user_id = ?""",
                (run_id, workspace_id, owner_user_id),
            ).fetchone()
            outcomes = (
                connection.execute(
                    """SELECT call_id, feature, status, updated_at
                       FROM pod_customization_billing_outcomes
                       WHERE run_id = ? ORDER BY rowid""",
                    (run_id,),
                ).fetchall()
                if row is not None
                else []
            )
        if row is None:
            raise PodRepositoryError("POD billing run not found", 404)
        result = dict(row)
        result["plan"] = json.loads(result.pop("plan_json"))
        result["action_payload"] = json.loads(result.pop("action_payload_json"))
        result["outcomes"] = [dict(outcome) for outcome in outcomes]
        return result

    def list_pending_billing_runs(
        self, workspace_id: str, owner_user_id: str
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT run_id FROM pod_customization_billing_runs
                   WHERE workspace_id = ? AND owner_user_id = ?
                     AND status IN ('authorized', 'settling', 'settlement_pending', 'auth_required')
                   ORDER BY created_at, run_id""",
                (workspace_id, owner_user_id),
            ).fetchall()
        return [self.get_billing_run(row["run_id"], workspace_id, owner_user_id) for row in rows]

    def start_billing_call(self, action_key: str, call_id: str, feature: str) -> None:
        now = _now()
        with self._connect() as connection:
            row = connection.execute(
                """SELECT outcomes.status, outcomes.feature
                   FROM pod_customization_billing_outcomes AS outcomes
                   INNER JOIN pod_customization_billing_runs AS runs ON runs.run_id = outcomes.run_id
                   WHERE runs.action_key = ? AND outcomes.call_id = ?""",
                (action_key, call_id),
            ).fetchone()
            if row is None or row["feature"] != feature:
                raise PodRepositoryError("POD billing call is not in the frozen plan", 409)
            if row["status"] != "planned":
                raise PodRepositoryError("POD billing call was already started", 409)
            connection.execute(
                """UPDATE pod_customization_billing_outcomes
                   SET status = 'started', updated_at = ?
                   WHERE run_id = (SELECT run_id FROM pod_customization_billing_runs WHERE action_key = ?)
                     AND call_id = ?""",
                (now, action_key, call_id),
            )
            connection.execute(
                """UPDATE pod_customization_billing_runs
                   SET updated_at = ?, error_message = '' WHERE action_key = ?""",
                (now, action_key),
            )

    def record_billing_outcome(
        self, action_key: str, outcome: PodCallOutcome
    ) -> None:
        now = _now()
        with self._connect() as connection:
            row = connection.execute(
                """SELECT outcomes.status, outcomes.feature
                   FROM pod_customization_billing_outcomes AS outcomes
                   INNER JOIN pod_customization_billing_runs AS runs ON runs.run_id = outcomes.run_id
                   WHERE runs.action_key = ? AND outcomes.call_id = ?""",
                (action_key, outcome.call_id),
            ).fetchone()
            if row is None or row["feature"] != outcome.feature:
                raise PodRepositoryError("POD billing call is not in the frozen plan", 409)
            if row["status"] in {"success", "no_return"}:
                if row["status"] != outcome.status:
                    raise PodRepositoryError("POD billing call has conflicting outcomes", 409)
                return
            connection.execute(
                """UPDATE pod_customization_billing_outcomes
                   SET status = ?, updated_at = ?
                   WHERE run_id = (SELECT run_id FROM pod_customization_billing_runs WHERE action_key = ?)
                     AND call_id = ?""",
                (outcome.status, now, action_key, outcome.call_id),
            )
            connection.execute(
                "UPDATE pod_customization_billing_runs SET updated_at = ? WHERE action_key = ?",
                (now, action_key),
            )

    def prepare_billing_settlement(self, action_key: str) -> tuple[PodCallOutcome, ...]:
        """Freeze known outcomes for settlement without guessing crash-window calls."""
        now = _now()
        with self._connect() as connection:
            uncertain = connection.execute(
                """SELECT outcomes.call_id
                   FROM pod_customization_billing_outcomes AS outcomes
                   INNER JOIN pod_customization_billing_runs AS runs ON runs.run_id = outcomes.run_id
                   WHERE runs.action_key = ? AND outcomes.status = 'started' LIMIT 1""",
                (action_key,),
            ).fetchone()
        if uncertain is not None:
            message = (
                "POD provider call outcome is uncertain after interruption; "
                "automatic settlement is blocked"
            )
            self.mark_billing_pending(action_key, message)
            raise PodRepositoryError(message, 409)
        with self._connect() as connection:
            run = connection.execute(
                "SELECT run_id, batch_id FROM pod_customization_billing_runs WHERE action_key = ?",
                (action_key,),
            ).fetchone()
            if run is None:
                raise PodRepositoryError("POD billing run not found", 404)
            if run["batch_id"]:
                batch = connection.execute(
                    "SELECT status FROM pod_customization_batches WHERE batch_id = ?",
                    (run["batch_id"],),
                ).fetchone()
                if batch is not None and batch["status"] not in {
                    "billing_auth_required",
                    "settlement_pending",
                }:
                    connection.execute(
                        """UPDATE pod_customization_billing_runs
                           SET result_status = ? WHERE action_key = ? AND result_status = ''""",
                        (batch["status"], action_key),
                    )
            connection.execute(
                """UPDATE pod_customization_billing_outcomes
                   SET status = 'no_return', updated_at = ?
                   WHERE run_id = ? AND status = 'planned'""",
                (now, run["run_id"]),
            )
            connection.execute(
                """UPDATE pod_customization_billing_runs
                   SET status = 'settling', error_message = '', updated_at = ?
                   WHERE run_id = ? AND status <> 'settled'""",
                (now, run["run_id"]),
            )
            rows = connection.execute(
                """SELECT call_id, feature, status FROM pod_customization_billing_outcomes
                   WHERE run_id = ? ORDER BY rowid""",
                (run["run_id"],),
            ).fetchall()
        return tuple(PodCallOutcome(row["call_id"], row["feature"], row["status"]) for row in rows)

    def mark_billing_pending(self, action_key: str, error_message: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE pod_customization_billing_runs
                   SET status = 'settlement_pending', error_message = ?, updated_at = ?
                   WHERE action_key = ? AND status <> 'settled'""",
                (_safe_error(error_message), _now(), action_key),
            )

    def mark_billing_auth_required(self, action_key: str, error_message: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE pod_customization_billing_runs
                   SET status = 'auth_required', error_message = ?, updated_at = ?
                   WHERE action_key = ? AND status <> 'settled'""",
                (_safe_error(error_message), _now(), action_key),
            )

    def mark_billing_authorized(
        self, action_key: str, *, rule_version: int, expires_at: str
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE pod_customization_billing_runs
                   SET status = 'authorized', rule_version = ?, grant_expires_at = ?,
                       error_message = '', updated_at = ?
                   WHERE action_key = ? AND status <> 'settled'""",
                (rule_version, expires_at, _now(), action_key),
            )

    def claim_billing_resume(
        self, run_id: str, workspace_id: str, owner_user_id: str
    ) -> bool:
        with self._connect() as connection:
            result = connection.execute(
                """UPDATE pod_customization_billing_runs
                   SET status = 'resume_claimed', error_message = '', updated_at = ?
                   WHERE run_id = ? AND workspace_id = ? AND owner_user_id = ?
                     AND status IN ('auth_required', 'settlement_pending')""",
                (_now(), run_id, workspace_id, owner_user_id),
            )
        return result.rowcount == 1

    def mark_billing_settled(self, action_key: str) -> None:
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """UPDATE pod_customization_billing_runs
                   SET status = 'settled', error_message = '', updated_at = ?, settled_at = ?
                   WHERE action_key = ?""",
                (now, now, action_key),
            )

    def recover_billing_runs(self) -> int:
        now = _now()
        message = "POD short-lived grant was discarded during restart; sign in to resume billing"
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT run_id FROM pod_customization_billing_runs
                   WHERE status IN ('authorized', 'resume_claimed', 'settling')"""
            ).fetchall()
            connection.execute(
                """UPDATE pod_customization_billing_runs
                   SET status = 'auth_required', error_message = ?, updated_at = ?
                   WHERE status IN ('authorized', 'resume_claimed', 'settling')""",
                (message, now),
            )
        return len(rows)

    def pause_billing_runs_for_shutdown(self) -> int:
        now = _now()
        message = "POD worker stopped; sign in after restart to resume unfinished billing actions"
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT run_id, batch_id FROM pod_customization_billing_runs
                   WHERE status = 'authorized'"""
            ).fetchall()
            connection.execute(
                """UPDATE pod_customization_billing_runs
                   SET status = 'auth_required', error_message = ?, updated_at = ?
                   WHERE status = 'authorized'""",
                (message, now),
            )
            batch_ids = [str(row["batch_id"]) for row in rows if str(row["batch_id"])]
            if batch_ids:
                placeholders = ",".join("?" for _ in batch_ids)
                connection.execute(
                    f"""UPDATE pod_customization_batches
                        SET status = 'billing_auth_required', error_message = ?, updated_at = ?
                        WHERE batch_id IN ({placeholders})
                          AND status IN ('queued', 'generating_patterns', 'compositing', 'generating_titles')""",
                    (message, now, *batch_ids),
                )
        return len(rows)

    def claim_batch(self, batch_id: str, *, allow_billing_resume: bool = False) -> bool:
        now = _now()
        with self._connect() as connection:
            result = connection.execute(
                """UPDATE pod_customization_batches
                   SET status = 'generating_patterns', started_at = CASE WHEN started_at = '' THEN ? ELSE started_at END,
                       updated_at = ?, error_message = ''
                   WHERE batch_id = ? AND (
                     status = 'queued' OR (? = 1 AND status = 'billing_auth_required')
                   )""",
                (now, now, batch_id, int(allow_billing_resume)),
            )
        return result.rowcount == 1

    def set_batch_status(self, batch_id: str, status: str, error_message: str = "") -> None:
        now = _now()
        finished_at = now if status in {"completed", "partial_failure", "failed", "cancelled"} else ""
        with self._connect() as connection:
            result = connection.execute(
                """UPDATE pod_customization_batches SET status = ?, error_message = ?, updated_at = ?,
                       finished_at = CASE WHEN ? <> '' THEN ? ELSE finished_at END
                   WHERE batch_id = ?""",
                (status, _safe_error(error_message), now, finished_at, finished_at, batch_id),
            )
        if result.rowcount != 1:
            raise PodRepositoryError("POD batch not found", 404)

    def get_batch_status(self, batch_id: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM pod_customization_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
        if row is None:
            raise PodRepositoryError("POD batch not found", 404)
        return str(row["status"])

    def request_pause(self, batch_id: str) -> bool:
        """Atomically ask the running worker to pause at its next checkpoint."""
        now = _now()
        with self._connect() as connection:
            result = connection.execute(
                """UPDATE pod_customization_batches SET status = 'pausing', updated_at = ?
                   WHERE batch_id = ?
                     AND status IN ('queued', 'generating_patterns', 'compositing', 'generating_titles')""",
                (now, batch_id),
            )
        return result.rowcount == 1

    def request_cancel(self, batch_id: str) -> bool:
        """Atomically ask the running worker to cancel at its next checkpoint."""
        now = _now()
        with self._connect() as connection:
            result = connection.execute(
                """UPDATE pod_customization_batches SET status = 'cancelling', updated_at = ?
                   WHERE batch_id = ?
                     AND status IN ('queued', 'generating_patterns', 'compositing', 'generating_titles',
                                    'pausing', 'paused')""",
                (now, batch_id),
            )
        return result.rowcount == 1

    def mark_batch_paused(self, batch_id: str, error_message: str = "") -> None:
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """UPDATE pod_customization_batches
                   SET status = 'paused', error_message = ?, updated_at = ?, finished_at = ''
                   WHERE batch_id = ? AND status = 'pausing'""",
                (_safe_error(error_message), now, batch_id),
            )

    def mark_batch_cancelled(self, batch_id: str, error_message: str = "") -> None:
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """UPDATE pod_customization_batches
                   SET status = 'cancelled', error_message = ?, updated_at = ?, finished_at = ?
                   WHERE batch_id = ? AND status = 'cancelling'""",
                (_safe_error(error_message), now, now, batch_id),
            )

    def resume_paused_batch(self, batch_id: str) -> bool:
        """Move a paused batch back to queued so it can be resubmitted."""
        now = _now()
        with self._connect() as connection:
            result = connection.execute(
                """UPDATE pod_customization_batches
                   SET status = 'queued', error_message = '', updated_at = ?, finished_at = ''
                   WHERE batch_id = ? AND status = 'paused'""",
                (now, batch_id),
            )
        return result.rowcount == 1

    def claim_style_title(
        self,
        batch_id: str,
        style_index: int,
        *,
        style_task_id: str | None = None,
        allow_billing_resume: bool = False,
    ) -> dict[str, Any]:
        """Atomically start a title attempt, optionally replacing its image task identity."""
        now = _now()
        with self._connect() as connection:
            result = connection.execute(
                """UPDATE pod_customization_style_titles
                   SET style_task_id = CASE WHEN ? IS NULL THEN style_task_id ELSE ? END,
                       status = 'generating', title = '', normalized_title = NULL,
                       visual_tags_json = '{}', model = '', prompt_version = '', attempt_count = 0,
                       error_message = '', started_at = ?, finished_at = '', updated_at = ?
                   WHERE batch_id = ? AND style_index = ?
                     AND (status IN ('queued', 'completed', 'failed')
                          OR (? = 1 AND status = 'generating'))""",
                (
                    style_task_id,
                    style_task_id,
                    now,
                    now,
                    batch_id,
                    style_index,
                    int(allow_billing_resume),
                ),
            )
            row = connection.execute(
                "SELECT * FROM pod_customization_style_titles WHERE batch_id = ? AND style_index = ?",
                (batch_id, style_index),
            ).fetchone()
            if result.rowcount != 1 or row is None:
                raise PodRepositoryError("POD style title is not available for generation", 409)
            connection.execute(
                "DELETE FROM pod_customization_style_copy WHERE batch_id = ? AND style_index = ?",
                (batch_id, style_index),
            )
        return self._decode_title_row(row)

    def claim_title_regeneration(
        self, batch_id: str, style_index: int, workspace_id: str, owner_user_id: str
    ) -> dict[str, Any]:
        now = _now()
        with self._connect() as connection:
            batch = connection.execute(
                """SELECT status FROM pod_customization_batches
                   WHERE batch_id = ? AND workspace_id = ? AND owner_user_id = ?""",
                (batch_id, workspace_id, owner_user_id),
            ).fetchone()
            if batch is None:
                raise PodRepositoryError("POD batch not found", 404)
            ready_images = int(connection.execute(
                """SELECT COUNT(*) FROM pod_customization_style_grid_results AS results
                   INNER JOIN pod_customization_style_grid_publications AS publications
                     ON publications.result_id = results.result_id
                   WHERE results.batch_id = ? AND results.style_index = ?
                     AND results.status = 'completed' AND publications.public_url <> ''""",
                (batch_id, style_index),
            ).fetchone()[0])
            if ready_images != 4:
                raise PodRepositoryError("all four public POD images are required before regenerating a title", 409)
            batch_claim = connection.execute(
                """UPDATE pod_customization_batches
                   SET status = 'generating_titles', error_message = '', updated_at = ?, finished_at = ''
                   WHERE batch_id = ? AND workspace_id = ? AND owner_user_id = ?
                     AND status IN ('completed', 'partial_failure', 'failed', 'cancelled', 'settlement_pending')""",
                (now, batch_id, workspace_id, owner_user_id),
            )
            if batch_claim.rowcount != 1:
                raise PodRepositoryError("POD batch must settle before regenerating its title", 409)
            result = connection.execute(
                """UPDATE pod_customization_style_titles
                   SET status = 'generating', title = '', normalized_title = NULL,
                       visual_tags_json = '{}', model = '', prompt_version = '', attempt_count = 0,
                       error_message = '', started_at = ?, finished_at = '', updated_at = ?
                   WHERE batch_id = ? AND style_index = ? AND style_task_id <> ''
                     AND status = 'failed'""",
                (now, now, batch_id, style_index),
            )
            if result.rowcount != 1:
                raise PodRepositoryError("POD style title is not available for regeneration", 409)
            connection.execute(
                "DELETE FROM pod_customization_style_copy WHERE batch_id = ? AND style_index = ?",
                (batch_id, style_index),
            )
            row = connection.execute(
                "SELECT * FROM pod_customization_style_titles WHERE batch_id = ? AND style_index = ?",
                (batch_id, style_index),
            ).fetchone()
        if row is None:
            raise PodRepositoryError("POD style title is not available for regeneration", 409)
        return self._decode_title_row(row)

    def finish_style_title(
        self,
        batch_id: str,
        style_index: int,
        title_result: dict[str, Any],
        *,
        workspace_id: str | None = None,
        owner_user_id: str | None = None,
        style_copy: dict[str, Any] | None = None,
    ) -> None:
        now = _now()
        title = str(title_result.get("title") or "").strip()
        normalized = _normalize_title(str(title_result.get("normalized_title") or title)) or None
        visual_tags = {
            "visual_theme": str(title_result.get("visual_theme") or ""),
            "motif_keywords": list(title_result.get("motif_keywords") or ()),
            "color_keywords": list(title_result.get("color_keywords") or ()),
            "visual_signature": str(title_result.get("visual_signature") or ""),
        }
        copy_values = None
        if style_copy is not None:
            if workspace_id is None or owner_user_id is None:
                raise ValueError("workspace and owner are required when finishing listing copy")
            copy_values = self._style_copy_values(
                style_copy.get("title"), style_copy.get("english_title"), style_copy.get("description")
            )
        with self._connect() as connection:
            if copy_values is not None:
                self._require_owned_style(
                    connection, batch_id, workspace_id, owner_user_id, style_index
                )
            result = connection.execute(
                """UPDATE pod_customization_style_titles
                   SET status = 'completed', source = 'ai', title = ?, normalized_title = ?,
                       visual_tags_json = ?, model = ?, prompt_version = ?, attempt_count = ?,
                       error_message = '', updated_at = ?, finished_at = ?
                   WHERE batch_id = ? AND style_index = ? AND status = 'generating'""",
                (
                    title,
                    normalized,
                    json.dumps(visual_tags),
                    str(title_result.get("model") or ""),
                    str(title_result.get("prompt_version") or ""),
                    int(title_result.get("attempt_count") or 0),
                    now,
                    now,
                    batch_id,
                    style_index,
                ),
            )
            if result.rowcount != 1:
                raise PodRepositoryError("POD style title generation is not active", 409)
            if copy_values is not None:
                self._upsert_style_copy_record(
                    connection, batch_id, style_index, values=copy_values, now=now
                )

    def complete_manual_title(
        self,
        batch_id: str,
        style_index: int,
        title: str,
        workspace_id: str,
        owner_user_id: str,
    ) -> dict[str, Any]:
        """Atomically replace a finished title with a user-entered value.

        Manual titles bypass AI copy validation and cross-style deduplication:
        ``normalized_title`` stays NULL so the unique index cannot reject two
        styles sharing the same user text.  Only titles that already reached a
        terminal state (completed or failed) with all four public images are
        eligible; no provider call or billing record is created.
        """
        clean = str(title or "").strip()
        if not clean:
            raise ValueError("manual title is required")
        now = _now()
        with self._connect() as connection:
            self._require_owned_style(connection, batch_id, workspace_id, owner_user_id, style_index)
            ready_images = int(connection.execute(
                """SELECT COUNT(*) FROM pod_customization_style_grid_results AS results
                   INNER JOIN pod_customization_style_grid_publications AS publications
                     ON publications.result_id = results.result_id
                   WHERE results.batch_id = ? AND results.style_index = ?
                     AND results.status = 'completed' AND publications.public_url <> ''""",
                (batch_id, style_index),
            ).fetchone()[0])
            if ready_images != 4:
                raise PodRepositoryError(
                    "all four public POD images are required before saving a manual title", 409
                )
            result = connection.execute(
                """UPDATE pod_customization_style_titles
                   SET status = 'completed', source = 'manual', title = ?, normalized_title = NULL,
                       visual_tags_json = '{}', model = '', prompt_version = '',
                       attempt_count = 0, error_message = '', updated_at = ?, finished_at = ?
                   WHERE batch_id = ? AND style_index = ? AND status IN ('completed', 'failed')""",
                (clean, now, now, batch_id, style_index),
            )
            if result.rowcount != 1:
                raise PodRepositoryError("POD style title is not in a finished state", 409)
            self._upsert_style_copy_record(
                connection,
                batch_id,
                style_index,
                values=self._style_copy_values(clean, clean, clean),
                now=now,
            )
            row = connection.execute(
                "SELECT * FROM pod_customization_style_titles WHERE batch_id = ? AND style_index = ?",
                (batch_id, style_index),
            ).fetchone()
        if row is None:
            raise PodRepositoryError("POD style title not found", 404)
        return self._decode_title_row(row)

    def fail_style_title(
        self,
        batch_id: str,
        style_index: int,
        error_message: str,
        *,
        style_task_id: str | None = None,
        attempt_count: int = 0,
    ) -> None:
        now = _now()
        with self._connect() as connection:
            result = connection.execute(
                """UPDATE pod_customization_style_titles
                   SET style_task_id = CASE WHEN ? IS NULL THEN style_task_id ELSE ? END,
                       status = 'failed', title = '', normalized_title = NULL,
                       visual_tags_json = '{}', attempt_count = ?, error_message = ?,
                       updated_at = ?, finished_at = ?
                   WHERE batch_id = ? AND style_index = ? AND status IN ('queued', 'generating')""",
                (
                    style_task_id,
                    style_task_id,
                    max(0, int(attempt_count)),
                    _safe_error(error_message),
                    now,
                    now,
                    batch_id,
                    style_index,
                ),
            )
        if result.rowcount != 1:
            raise PodRepositoryError("POD style title generation is not active", 409)

    def fail_unready_titles(self, batch_id: str, error_message: str) -> int:
        now = _now()
        with self._connect() as connection:
            result = connection.execute(
                """UPDATE pod_customization_style_titles AS titles
                   SET style_task_id = CASE WHEN style_task_id = '' THEN COALESCE(
                           (SELECT calls.call_id FROM pod_customization_generation_calls AS calls
                            WHERE calls.batch_id = titles.batch_id
                              AND calls.call_index = titles.style_index
                            ORDER BY calls.created_at DESC, calls.rowid DESC LIMIT 1),
                           style_task_id
                       ) ELSE style_task_id END,
                       status = 'failed', title = '', normalized_title = NULL,
                       visual_tags_json = '{}', error_message = ?, updated_at = ?, finished_at = ?
                   WHERE titles.batch_id = ? AND titles.status IN ('queued', 'generating')
                     AND (SELECT COUNT(*) FROM pod_customization_style_grid_results AS results
                          INNER JOIN pod_customization_style_grid_publications AS publications
                            ON publications.result_id = results.result_id
                          WHERE results.batch_id = titles.batch_id
                            AND results.style_index = titles.style_index
                            AND results.status = 'completed'
                            AND publications.public_url <> '') <> 4""",
                (_safe_error(error_message), now, now, batch_id),
            )
        return int(result.rowcount or 0)

    def fail_pending_titles(self, batch_id: str, error_message: str) -> int:
        """Close every title attempt that cannot outlive a cancelled batch."""
        now = _now()
        with self._connect() as connection:
            result = connection.execute(
                """UPDATE pod_customization_style_titles
                   SET status = 'failed', title = '', normalized_title = NULL,
                       visual_tags_json = '{}', error_message = ?, updated_at = ?, finished_at = ?
                   WHERE batch_id = ? AND status IN ('queued', 'generating')""",
                (_safe_error(error_message), now, now, batch_id),
            )
        return int(result.rowcount or 0)

    def accepted_style_titles(self, batch_id: str, *, exclude_style_index: int | None = None) -> tuple[str, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT title FROM pod_customization_style_titles
                   WHERE batch_id = ? AND status = 'completed' AND title <> ''
                     AND (? IS NULL OR style_index <> ?)
                   ORDER BY style_index""",
                (batch_id, exclude_style_index, exclude_style_index),
            ).fetchall()
        return tuple(str(row["title"]) for row in rows)

    def get_style_title_context(self, batch_id: str, style_index: int) -> dict[str, Any]:
        batch = self.get_batch_internal(batch_id)
        title = next((row for row in batch["style_titles"] if row["style_index"] == style_index), None)
        lifestyle = next(
            (
                row for row in batch["items"]
                if row.get("style_index") == style_index and row.get("role") == "lifestyle"
                and row.get("status") == "completed" and row.get("public_url")
            ),
            None,
        )
        if title is None:
            raise PodRepositoryError("POD style title not found", 404)
        if lifestyle is None or not lifestyle.get("pattern_asset_id"):
            raise PodRepositoryError("POD style lifestyle image is unavailable", 409)
        return {"batch": batch, "title": title, "lifestyle": lifestyle}

    def settle_batch_by_listing_readiness(self, batch_id: str, error_message: str = "") -> str:
        """Set a new title-aware batch terminal status while retaining legacy semantics."""
        now = _now()
        with self._connect() as connection:
            title_count = int(connection.execute(
                "SELECT COUNT(*) FROM pod_customization_style_titles WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()[0])
            if title_count == 0:
                counts = connection.execute(
                    "SELECT completed_count, failed_count FROM pod_customization_batches WHERE batch_id = ?",
                    (batch_id,),
                ).fetchone()
                if counts is None:
                    raise PodRepositoryError("POD batch not found", 404)
                status = "completed" if counts["failed_count"] == 0 else (
                    "partial_failure" if counts["completed_count"] else "failed"
                )
            else:
                active_count = int(connection.execute(
                    """SELECT COUNT(*) FROM pod_customization_style_titles
                       WHERE batch_id = ? AND status = 'generating'""",
                    (batch_id,),
                ).fetchone()[0])
                if active_count:
                    connection.execute(
                        """UPDATE pod_customization_batches
                           SET status = 'generating_titles', error_message = '', updated_at = ?, finished_at = ''
                           WHERE batch_id = ?""",
                        (now, batch_id),
                    )
                    return "generating_titles"
                ready_count = int(connection.execute(
                    """SELECT COUNT(*) FROM pod_customization_style_titles AS titles
                       WHERE titles.batch_id = ? AND titles.status = 'completed'
                         AND EXISTS (
                           SELECT 1 FROM pod_customization_style_copy AS copies
                           WHERE copies.batch_id = titles.batch_id
                             AND copies.style_index = titles.style_index
                             AND TRIM(copies.title) <> ''
                             AND TRIM(copies.english_title) <> ''
                             AND TRIM(copies.description) <> ''
                         )
                         AND (SELECT COUNT(*) FROM pod_customization_style_grid_results AS results
                              INNER JOIN pod_customization_style_grid_publications AS publications
                                ON publications.result_id = results.result_id
                              WHERE results.batch_id = titles.batch_id
                                AND results.style_index = titles.style_index
                                AND results.status = 'completed'
                                AND publications.public_url <> '') = 4""",
                    (batch_id,),
                ).fetchone()[0])
                requested = connection.execute(
                    "SELECT requested_count FROM pod_customization_batches WHERE batch_id = ?", (batch_id,)
                ).fetchone()
                if requested is None:
                    raise PodRepositoryError("POD batch not found", 404)
                status = "completed" if ready_count == int(requested["requested_count"]) else (
                    "partial_failure" if ready_count else "failed"
                )
            connection.execute(
                """UPDATE pod_customization_batches
                   SET status = ?, error_message = ?, updated_at = ?, finished_at = ? WHERE batch_id = ?""",
                (status, _safe_error(error_message), now, now, batch_id),
            )
        return status

    def reconcile_stale_generating_titles(self, batch_id: str) -> bool:
        """Close an abandoned title phase only after all provider work is terminal.

        A worker exception can leave a title in ``generating`` after every image
        result and billing outcome has already settled.  That state cannot make
        progress on its own, so it must become a retryable terminal batch.
        """
        now = _now()
        message = "POD worker exited before title completion"
        with self._connect() as connection:
            batch = connection.execute(
                "SELECT requested_count, status FROM pod_customization_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            if batch is None or batch["status"] != "generating_titles":
                return False
            unfinished_images = int(connection.execute(
                """SELECT COUNT(*) FROM pod_customization_style_grid_results
                   WHERE batch_id = ? AND status NOT IN ('completed', 'failed')""",
                (batch_id,),
            ).fetchone()[0])
            settled_images = int(connection.execute(
                "SELECT COUNT(*) FROM pod_customization_style_grid_results WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()[0])
            active_calls = int(connection.execute(
                """SELECT COUNT(*) FROM pod_customization_billing_outcomes AS outcomes
                   INNER JOIN pod_customization_billing_runs AS runs ON runs.run_id = outcomes.run_id
                   WHERE runs.batch_id = ? AND outcomes.status IN ('planned', 'started')""",
                (batch_id,),
            ).fetchone()[0])
            active_titles = int(connection.execute(
                """SELECT COUNT(*) FROM pod_customization_style_titles
                   WHERE batch_id = ? AND status IN ('queued', 'generating')""",
                (batch_id,),
            ).fetchone()[0])
            if (
                not active_titles
                or unfinished_images
                or settled_images != int(batch["requested_count"]) * 4
                or active_calls
            ):
                return False
            connection.execute(
                """UPDATE pod_customization_style_titles
                   SET status = 'failed', title = '', normalized_title = NULL,
                       visual_tags_json = '{}', error_message = ?, updated_at = ?, finished_at = ?
                   WHERE batch_id = ? AND status IN ('queued', 'generating')""",
                (message, now, now, batch_id),
            )
        self.settle_batch_by_listing_readiness(batch_id)
        return True
        return status

    def create_generation_call(
        self,
        batch: dict[str, Any],
        *,
        call_kind: str,
        call_index: int,
        prompt_snapshot: str | None = None,
    ) -> dict[str, Any]:
        call_id = uuid.uuid4().hex
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO pod_customization_generation_calls
                   (call_id, batch_id, workspace_id, owner_user_id, call_kind, call_index, status,
                    prompt_snapshot, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)""",
                (call_id, batch["batch_id"], batch["workspace_id"], batch["owner_user_id"], call_kind,
                 call_index, prompt_snapshot if prompt_snapshot is not None else batch["prompt_snapshot"], now),
            )
            if call_kind == "refill":
                connection.execute(
                    """UPDATE pod_customization_batches SET refill_call_count = refill_call_count + 1, updated_at = ?
                       WHERE batch_id = ?""",
                    (now, batch["batch_id"]),
                )
        return {"call_id": call_id, "call_kind": call_kind, "call_index": call_index}

    def get_or_create_generation_call(
        self,
        batch: dict[str, Any],
        *,
        call_kind: str,
        call_index: int,
        prompt_snapshot: str | None = None,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM pod_customization_generation_calls
                   WHERE batch_id = ? AND call_kind = ? AND call_index = ?""",
                (batch["batch_id"], call_kind, call_index),
            ).fetchone()
        if row is not None:
            return dict(row)
        return self.create_generation_call(
            batch,
            call_kind=call_kind,
            call_index=call_index,
            prompt_snapshot=prompt_snapshot,
        )

    def billing_call_status(self, action_key: str, call_id: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT outcomes.status
                   FROM pod_customization_billing_outcomes AS outcomes
                   INNER JOIN pod_customization_billing_runs AS runs ON runs.run_id = outcomes.run_id
                   WHERE runs.action_key = ? AND outcomes.call_id = ?""",
                (action_key, call_id),
            ).fetchone()
        if row is None:
            raise PodRepositoryError("POD billing call is not in the frozen plan", 409)
        return str(row["status"])

    def next_generation_call_index(self, batch_id: str, call_kind: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COALESCE(MAX(call_index), 0) + 1 FROM pod_customization_generation_calls
                   WHERE batch_id = ? AND call_kind = ?""",
                (batch_id, call_kind),
            ).fetchone()
        return int(row[0])

    def mark_generation_call_running(self, call_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE pod_customization_generation_calls SET status = 'running', started_at = ?
                   WHERE call_id = ? AND status = 'queued'""",
                (_now(), call_id),
            )

    def requeue_generation_call(self, call_id: str) -> None:
        """Undo a local start which was stopped before the provider accepted it."""
        with self._connect() as connection:
            connection.execute(
                """UPDATE pod_customization_generation_calls
                   SET status = 'queued', started_at = '', error_message = '', finished_at = ''
                   WHERE call_id = ? AND status = 'running' AND grid_asset_id = ''""",
                (call_id,),
            )

    def finish_generation_call(
        self,
        call_id: str,
        *,
        status: str,
        grid_asset_id: str = "",
        error_message: str = "",
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE pod_customization_generation_calls
                   SET status = ?, grid_asset_id = ?, error_message = ?, finished_at = ?
                   WHERE call_id = ?""",
                (status, grid_asset_id, _safe_error(error_message), _now(), call_id),
            )

    def record_candidate(
        self,
        batch: dict[str, Any],
        *,
        call_id: str,
        grid_cell: int,
        status: str,
        rejection_reason: str,
        fingerprint: str,
        pattern_asset_id: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO pod_customization_pattern_candidates
                   (candidate_id, batch_id, call_id, workspace_id, owner_user_id, grid_cell, status,
                    rejection_reason, fingerprint, pattern_asset_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (uuid.uuid4().hex, batch["batch_id"], call_id, batch["workspace_id"], batch["owner_user_id"],
                 grid_cell, status, _safe_error(rejection_reason), fingerprint, pattern_asset_id, _now()),
            )

    def accept_candidate(
        self,
        batch: dict[str, Any],
        *,
        call_id: str,
        grid_cell: int,
        fingerprint: str,
        pattern_asset_id: str,
        composite_asset_id: str,
    ) -> dict[str, Any] | None:
        now = _now()
        with self._connect() as connection:
            item = connection.execute(
                """SELECT * FROM pod_customization_batch_items
                   WHERE batch_id = ? AND workspace_id = ? AND owner_user_id = ? AND status = 'queued'
                   ORDER BY item_index LIMIT 1""",
                (batch["batch_id"], batch["workspace_id"], batch["owner_user_id"]),
            ).fetchone()
            if item is None:
                connection.execute(
                    """INSERT INTO pod_customization_pattern_candidates
                       (candidate_id, batch_id, call_id, workspace_id, owner_user_id, grid_cell, status,
                        rejection_reason, fingerprint, pattern_asset_id, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'surplus', 'surplus', ?, ?, ?)""",
                    (uuid.uuid4().hex, batch["batch_id"], call_id, batch["workspace_id"], batch["owner_user_id"],
                     grid_cell, fingerprint, pattern_asset_id, now),
                )
                return None
            connection.execute(
                """UPDATE pod_customization_batch_items
                   SET status = 'completed', pattern_asset_id = ?, composite_asset_id = ?,
                       pattern_fingerprint = ?, error_message = '', updated_at = ?
                   WHERE item_id = ? AND status = 'queued'""",
                (pattern_asset_id, composite_asset_id, fingerprint, now, item["item_id"]),
            )
            connection.execute(
                """INSERT INTO pod_customization_pattern_candidates
                   (candidate_id, batch_id, call_id, workspace_id, owner_user_id, grid_cell, status,
                    rejection_reason, fingerprint, pattern_asset_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'accepted', '', ?, ?, ?)""",
                (uuid.uuid4().hex, batch["batch_id"], call_id, batch["workspace_id"], batch["owner_user_id"],
                 grid_cell, fingerprint, pattern_asset_id, now),
            )
            self._refresh_counts(connection, batch["batch_id"], now)
        return dict(item)

    def finish_style_grid_result(
        self,
        batch: dict[str, Any],
        *,
        style_index: int,
        variant_index: int,
        call_id: str,
        status: str,
        fingerprint: str = "",
        pattern_asset_id: str = "",
        composite_asset_id: str = "",
        role: str = "",
        public_url: str = "",
        error_message: str = "",
    ) -> None:
        now = _now()
        with self._connect() as connection:
            result = connection.execute(
                """UPDATE pod_customization_style_grid_results
                   SET status = ?, pattern_asset_id = CASE WHEN ? <> '' THEN ? ELSE pattern_asset_id END,
                       composite_asset_id = CASE WHEN ? <> '' THEN ? ELSE composite_asset_id END,
                       pattern_fingerprint = CASE WHEN ? <> '' THEN ? ELSE pattern_fingerprint END,
                       scene_optimized = CASE WHEN ? <> '' THEN 0 ELSE scene_optimized END,
                       error_message = ?, updated_at = ?
                   WHERE batch_id = ? AND style_index = ? AND variant_index = ?""",
                (status, pattern_asset_id, pattern_asset_id, composite_asset_id, composite_asset_id,
                 fingerprint, fingerprint, pattern_asset_id, _safe_error(error_message), now,
                 batch["batch_id"], style_index, variant_index),
            )
            if result.rowcount != 1:
                raise PodRepositoryError("POD style result not found", 404)
            row = connection.execute(
                """SELECT result_id FROM pod_customization_style_grid_results
                   WHERE batch_id = ? AND style_index = ? AND variant_index = ?""",
                (batch["batch_id"], style_index, variant_index),
            ).fetchone()
            if row is None:
                raise PodRepositoryError("POD style result not found", 404)
            if role or public_url:
                connection.execute(
                    """INSERT INTO pod_customization_style_grid_publications
                       (result_id, role, public_url, updated_at) VALUES (?, ?, ?, ?)
                       ON CONFLICT(result_id) DO UPDATE SET
                         role = excluded.role, public_url = excluded.public_url, updated_at = excluded.updated_at""",
                    (row["result_id"], role, public_url, now),
                )
            connection.execute(
                """INSERT INTO pod_customization_pattern_candidates
                   (candidate_id, batch_id, call_id, workspace_id, owner_user_id, grid_cell, status,
                    rejection_reason, fingerprint, pattern_asset_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (uuid.uuid4().hex, batch["batch_id"], call_id, batch["workspace_id"], batch["owner_user_id"],
                 variant_index, "accepted" if status == "completed" else "rejected",
                 _safe_error(error_message), fingerprint, pattern_asset_id, now),
            )
            self._refresh_counts(connection, batch["batch_id"], now)

    def fail_style_grid(self, batch: dict[str, Any], style_index: int, error_message: str) -> None:
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """UPDATE pod_customization_style_grid_results
                   SET status = 'failed', error_message = ?, updated_at = ?
                   WHERE batch_id = ? AND style_index = ? AND status IN ('queued', 'generating_pattern', 'compositing')""",
                (_safe_error(error_message), now, batch["batch_id"], style_index),
            )
            self._refresh_counts(connection, batch["batch_id"], now)

    def list_candidates(self, batch_id: str, workspace_id: str, owner_user_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM pod_customization_pattern_candidates
                   WHERE batch_id = ? AND workspace_id = ? AND owner_user_id = ? ORDER BY created_at, rowid""",
                (batch_id, workspace_id, owner_user_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def accepted_fingerprints(self, batch_id: str) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT pattern_fingerprint FROM pod_customization_style_grid_results
                   WHERE batch_id = ? AND status = 'completed' AND pattern_fingerprint <> ''
                   ORDER BY style_index, variant_index"""
                if self._is_style_grid_batch(connection, batch_id) else
                """SELECT pattern_fingerprint FROM pod_customization_batch_items
                   WHERE batch_id = ? AND status = 'completed' AND pattern_fingerprint <> '' ORDER BY item_index""",
                (batch_id,),
            ).fetchall()
        return [row["pattern_fingerprint"] for row in rows]

    def fail_remaining_items(self, batch_id: str, message: str) -> int:
        now = _now()
        with self._connect() as connection:
            result = connection.execute(
                """UPDATE pod_customization_style_grid_results SET status = 'failed', error_message = ?, updated_at = ?
                   WHERE batch_id = ? AND status IN ('queued', 'generating_pattern', 'compositing')"""
                if self._is_style_grid_batch(connection, batch_id) else
                """UPDATE pod_customization_batch_items SET status = 'failed', error_message = ?, updated_at = ?
                   WHERE batch_id = ? AND status = 'queued'""",
                (_safe_error(message), now, batch_id),
            )
            self._refresh_counts(connection, batch_id, now)
        return int(result.rowcount or 0)

    def get_item(self, batch_id: str, item_id: str, workspace_id: str, owner_user_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT results.result_id AS item_id,
                          ((results.style_index - 1) * 4 + results.variant_index) AS item_index,
                          results.style_index, results.variant_index, results.status,
                          results.pattern_asset_id, results.composite_asset_id,
                          results.pattern_fingerprint, results.scene_optimized, results.error_message,
                          results.created_at, results.updated_at,
                          COALESCE(publications.role, '') AS role,
                          COALESCE(publications.public_url, '') AS public_url
                   FROM pod_customization_style_grid_results AS results
                   LEFT JOIN pod_customization_style_grid_publications AS publications
                     ON publications.result_id = results.result_id
                   WHERE results.batch_id = ? AND results.result_id = ?
                     AND results.workspace_id = ? AND results.owner_user_id = ?"""
                if self._is_style_grid_batch(connection, batch_id) else
                """SELECT *, item_index AS style_index, 1 AS variant_index FROM pod_customization_batch_items
                   WHERE batch_id = ? AND item_id = ? AND workspace_id = ? AND owner_user_id = ?""",
                (batch_id, item_id, workspace_id, owner_user_id),
            ).fetchone()
        if row is None:
            raise PodRepositoryError("POD batch item not found", 404)
        return dict(row)

    def claim_scene_optimization(self, batch_id: str, item_id: str, workspace_id: str, owner_user_id: str) -> dict[str, Any]:
        now = _now()
        with self._connect() as connection:
            result = connection.execute(
                """UPDATE pod_customization_style_grid_results
                   SET status = 'optimizing_scene', error_message = '', updated_at = ?
                   WHERE batch_id = ? AND result_id = ? AND workspace_id = ? AND owner_user_id = ?
                     AND status = 'completed' AND pattern_asset_id <> '' AND composite_asset_id <> ''"""
                if self._is_style_grid_batch(connection, batch_id) else
                """UPDATE pod_customization_batch_items SET status = 'optimizing_scene', error_message = '', updated_at = ?
                   WHERE batch_id = ? AND item_id = ? AND workspace_id = ? AND owner_user_id = ?
                     AND status = 'completed' AND pattern_asset_id <> '' AND composite_asset_id <> ''""",
                (now, batch_id, item_id, workspace_id, owner_user_id),
            )
        if result.rowcount != 1:
            raise PodRepositoryError("only a completed POD item can optimize its scene", 409)
        return self.get_item(batch_id, item_id, workspace_id, owner_user_id)

    def claim_item_regeneration(self, batch_id: str, item_id: str, workspace_id: str, owner_user_id: str) -> dict[str, Any]:
        now = _now()
        with self._connect() as connection:
            batch = connection.execute(
                """SELECT status FROM pod_customization_batches
                   WHERE batch_id = ? AND workspace_id = ? AND owner_user_id = ?""",
                (batch_id, workspace_id, owner_user_id),
            ).fetchone()
            if batch is None:
                raise PodRepositoryError("POD batch not found", 404)
            if batch["status"] not in {"completed", "partial_failure", "failed", "cancelled", "settlement_pending"}:
                raise PodRepositoryError("POD batch must settle before regenerating one item", 409)
            result = connection.execute(
                """UPDATE pod_customization_batch_items
                   SET status = 'generating_pattern', error_message = '', updated_at = ?
                   WHERE batch_id = ? AND item_id = ? AND workspace_id = ? AND owner_user_id = ?
                     AND status = 'failed'""",
                (now, batch_id, item_id, workspace_id, owner_user_id),
            )
        if result.rowcount != 1:
            raise PodRepositoryError("only a settled POD item can be regenerated", 409)
        return self.get_item(batch_id, item_id, workspace_id, owner_user_id)

    def claim_style_regeneration(
        self, batch_id: str, style_index: int, workspace_id: str, owner_user_id: str
    ) -> list[dict[str, Any]]:
        now = _now()
        with self._connect() as connection:
            if not self._is_style_grid_batch(connection, batch_id):
                raise PodRepositoryError("whole-style regeneration is only available for new POD batches", 409)
            batch = connection.execute(
                """SELECT status FROM pod_customization_batches
                   WHERE batch_id = ? AND workspace_id = ? AND owner_user_id = ?""",
                (batch_id, workspace_id, owner_user_id),
            ).fetchone()
            if batch is None:
                raise PodRepositoryError("POD batch not found", 404)
            if batch["status"] not in {"completed", "partial_failure", "failed", "cancelled", "settlement_pending"}:
                raise PodRepositoryError("POD batch must settle before regenerating one style", 409)
            result = connection.execute(
                """UPDATE pod_customization_style_grid_results
                   SET status = 'generating_pattern', error_message = '', updated_at = ?
                   WHERE batch_id = ? AND workspace_id = ? AND owner_user_id = ? AND style_index = ?
                     AND status = 'failed'""",
                (now, batch_id, workspace_id, owner_user_id, style_index),
            )
            if result.rowcount != 4:
                raise PodRepositoryError("only a settled POD style can be regenerated", 409)
            title_reset = connection.execute(
                """UPDATE pod_customization_style_titles
                   SET style_task_id = '', status = 'queued', title = '', normalized_title = NULL,
                       visual_tags_json = '{}', model = '', prompt_version = '', attempt_count = 0,
                       error_message = '', started_at = '', finished_at = '', updated_at = ?
                   WHERE batch_id = ? AND style_index = ?""",
                (now, batch_id, style_index),
            )
            if title_reset.rowcount not in {0, 1}:
                raise PodRepositoryError("POD style title reset failed", 409)
            self._refresh_counts(connection, batch_id, now)
            connection.execute(
                """UPDATE pod_customization_batches SET status = 'generating_patterns', updated_at = ?, error_message = ''
                   WHERE batch_id = ?""", (now, batch_id)
            )
            rows = connection.execute(
                """SELECT results.result_id AS item_id,
                          ((results.style_index - 1) * 4 + results.variant_index) AS item_index,
                          results.style_index, results.variant_index, results.status,
                          results.pattern_asset_id, results.composite_asset_id,
                          results.pattern_fingerprint, results.scene_optimized, results.error_message,
                          results.created_at, results.updated_at,
                          COALESCE(publications.role, '') AS role,
                          COALESCE(publications.public_url, '') AS public_url
                   FROM pod_customization_style_grid_results AS results
                   LEFT JOIN pod_customization_style_grid_publications AS publications
                     ON publications.result_id = results.result_id
                   WHERE results.batch_id = ? AND results.style_index = ?
                   ORDER BY results.variant_index""", (batch_id, style_index)
            ).fetchall()
        return [dict(row) for row in rows]

    def claim_batch_retry(
        self,
        batch_id: str,
        workspace_id: str,
        owner_user_id: str,
        *,
        image_style_indices: tuple[int, ...],
        title_style_indices: tuple[int, ...],
    ) -> None:
        """Atomically reserve selected terminal failures for one batch retry."""
        if not image_style_indices and not title_style_indices:
            raise PodRepositoryError("at least one failed POD style must be selected", 422)
        if (
            len(set(image_style_indices)) != len(image_style_indices)
            or len(set(title_style_indices)) != len(title_style_indices)
        ):
            raise PodRepositoryError("POD retry styles must not contain duplicates", 422)
        if set(image_style_indices).intersection(title_style_indices):
            raise PodRepositoryError("a POD style cannot be retried as both image and title", 422)
        now = _now()
        with self._connect() as connection:
            if not self._is_style_grid_batch(connection, batch_id):
                raise PodRepositoryError("batch retry is only available for new POD batches", 409)
            batch = connection.execute(
                """SELECT status, requested_count FROM pod_customization_batches
                   WHERE batch_id = ? AND workspace_id = ? AND owner_user_id = ?""",
                (batch_id, workspace_id, owner_user_id),
            ).fetchone()
            if batch is None:
                raise PodRepositoryError("POD batch not found", 404)
            if batch["status"] not in {"completed", "partial_failure", "failed", "cancelled", "settlement_pending"}:
                if batch["status"] == "billing_auth_required":
                    raise PodRepositoryError(
                        "POD billing is not recovered; resume billing authorization before retrying failed styles",
                        409,
                    )
                raise PodRepositoryError("POD batch must settle before retrying failed styles", 409)
            requested_count = int(batch["requested_count"])
            if any(not 1 <= index <= requested_count for index in (*image_style_indices, *title_style_indices)):
                raise PodRepositoryError("POD style index is outside the batch range", 422)

            for style_index in image_style_indices:
                rows = connection.execute(
                    """SELECT status FROM pod_customization_style_grid_results
                       WHERE batch_id = ? AND style_index = ? ORDER BY variant_index""",
                    (batch_id, style_index),
                ).fetchall()
                if len(rows) != 4 or any(row["status"] != "failed" for row in rows):
                    raise PodRepositoryError("only styles with all four images failed can be retried", 409)

            for style_index in title_style_indices:
                title = connection.execute(
                    """SELECT status, style_task_id FROM pod_customization_style_titles
                       WHERE batch_id = ? AND style_index = ?""",
                    (batch_id, style_index),
                ).fetchone()
                ready_images = int(connection.execute(
                    """SELECT COUNT(*) FROM pod_customization_style_grid_results AS results
                       INNER JOIN pod_customization_style_grid_publications AS publications
                         ON publications.result_id = results.result_id
                       WHERE results.batch_id = ? AND results.style_index = ?
                         AND results.status = 'completed' AND publications.public_url <> ''""",
                    (batch_id, style_index),
                ).fetchone()[0])
                if (
                    title is None
                    or title["status"] != "failed"
                    or not title["style_task_id"]
                    or ready_images != 4
                ):
                    raise PodRepositoryError(
                        "only a failed POD title with four public images can be retried", 409
                    )

            next_status = "generating_patterns" if image_style_indices else "generating_titles"
            claimed = connection.execute(
                """UPDATE pod_customization_batches
                   SET status = ?, error_message = '', updated_at = ?, finished_at = ''
                   WHERE batch_id = ? AND workspace_id = ? AND owner_user_id = ?
                     AND status IN ('completed', 'partial_failure', 'failed', 'cancelled', 'settlement_pending')""",
                (next_status, now, batch_id, workspace_id, owner_user_id),
            )
            if claimed.rowcount != 1:
                raise PodRepositoryError("POD batch must settle before retrying failed styles", 409)

            for style_index in image_style_indices:
                updated = connection.execute(
                    """UPDATE pod_customization_style_grid_results
                       SET status = 'generating_pattern', error_message = '', updated_at = ?
                       WHERE batch_id = ? AND style_index = ? AND status = 'failed'""",
                    (now, batch_id, style_index),
                )
                if updated.rowcount != 4:
                    raise PodRepositoryError("only styles with all four images failed can be retried", 409)
                title_reset = connection.execute(
                    """UPDATE pod_customization_style_titles
                       SET style_task_id = '', status = 'queued', title = '', normalized_title = NULL,
                           visual_tags_json = '{}', model = '', prompt_version = '', attempt_count = 0,
                           error_message = '', started_at = '', finished_at = '', updated_at = ?
                       WHERE batch_id = ? AND style_index = ?""",
                    (now, batch_id, style_index),
                )
                if title_reset.rowcount != 1:
                    raise PodRepositoryError("POD style title reset failed", 409)
                connection.execute(
                    "DELETE FROM pod_customization_style_copy WHERE batch_id = ? AND style_index = ?",
                    (batch_id, style_index),
                )

            for style_index in title_style_indices:
                updated = connection.execute(
                    """UPDATE pod_customization_style_titles
                       SET status = 'generating', title = '', normalized_title = NULL,
                           visual_tags_json = '{}', model = '', prompt_version = '', attempt_count = 0,
                           error_message = '', started_at = ?, finished_at = '', updated_at = ?
                       WHERE batch_id = ? AND style_index = ? AND style_task_id <> ''
                         AND status = 'failed'""",
                    (now, now, batch_id, style_index),
                )
                if updated.rowcount != 1:
                    raise PodRepositoryError(
                        "only a failed POD title with four public images can be retried", 409
                    )
                connection.execute(
                    "DELETE FROM pod_customization_style_copy WHERE batch_id = ? AND style_index = ?",
                    (batch_id, style_index),
                )
            self._refresh_counts(connection, batch_id, now)

    def finish_item_regeneration(
        self,
        batch: dict[str, Any],
        item_id: str,
        *,
        call_id: str,
        grid_cell: int,
        fingerprint: str,
        pattern_asset_id: str,
        composite_asset_id: str,
    ) -> None:
        now = _now()
        with self._connect() as connection:
            result = connection.execute(
                """UPDATE pod_customization_batch_items
                   SET status = 'completed', pattern_asset_id = ?, composite_asset_id = ?,
                       pattern_fingerprint = ?, scene_optimized = 0, error_message = '', updated_at = ?
                   WHERE batch_id = ? AND item_id = ? AND status = 'generating_pattern'""",
                (pattern_asset_id, composite_asset_id, fingerprint, now, batch["batch_id"], item_id),
            )
            if result.rowcount != 1:
                raise PodRepositoryError("POD item regeneration is not active", 409)
            connection.execute(
                """INSERT INTO pod_customization_pattern_candidates
                   (candidate_id, batch_id, call_id, workspace_id, owner_user_id, grid_cell, status,
                    rejection_reason, fingerprint, pattern_asset_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'accepted', '', ?, ?, ?)""",
                (uuid.uuid4().hex, batch["batch_id"], call_id, batch["workspace_id"], batch["owner_user_id"],
                 grid_cell, fingerprint, pattern_asset_id, now),
            )
            self._refresh_counts(connection, batch["batch_id"], now)

    def fail_item_regeneration(self, batch_id: str, item_id: str, error_message: str) -> None:
        now = _now()
        with self._connect() as connection:
            item = connection.execute(
                """SELECT pattern_asset_id, composite_asset_id FROM pod_customization_batch_items
                   WHERE batch_id = ? AND item_id = ? AND status = 'generating_pattern'""",
                (batch_id, item_id),
            ).fetchone()
            if item is None:
                raise PodRepositoryError("POD item regeneration is not active", 409)
            restored_status = "completed" if item["pattern_asset_id"] and item["composite_asset_id"] else "failed"
            connection.execute(
                """UPDATE pod_customization_batch_items SET status = ?, error_message = ?, updated_at = ?
                   WHERE batch_id = ? AND item_id = ?""",
                (restored_status, _safe_error(error_message), now, batch_id, item_id),
            )
            self._refresh_counts(connection, batch_id, now)

    def finish_scene_optimization(
        self,
        batch_id: str,
        item_id: str,
        *,
        composite_asset_id: str = "",
        error_message: str = "",
    ) -> None:
        succeeded = bool(composite_asset_id)
        now = _now()
        with self._connect() as connection:
            result = connection.execute(
                """UPDATE pod_customization_style_grid_results
                   SET status = 'completed', composite_asset_id = CASE WHEN ? <> '' THEN ? ELSE composite_asset_id END,
                       scene_optimized = CASE WHEN ? <> '' THEN 1 ELSE scene_optimized END,
                       error_message = ?, updated_at = ?
                   WHERE batch_id = ? AND result_id = ? AND status = 'optimizing_scene'"""
                if self._is_style_grid_batch(connection, batch_id) else
                """UPDATE pod_customization_batch_items
                   SET status = 'completed', composite_asset_id = CASE WHEN ? <> '' THEN ? ELSE composite_asset_id END,
                       scene_optimized = CASE WHEN ? <> '' THEN 1 ELSE scene_optimized END,
                       error_message = ?, updated_at = ?
                   WHERE batch_id = ? AND item_id = ? AND status = 'optimizing_scene'""",
                (composite_asset_id, composite_asset_id, composite_asset_id,
                 "" if succeeded else _safe_error(error_message), now, batch_id, item_id),
            )
            self._refresh_counts(connection, batch_id, now)
        if result.rowcount != 1:
            raise PodRepositoryError("POD scene optimization is not active", 409)

    @staticmethod
    def _decode_title_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        visual_tags = json.loads(result.pop("visual_tags_json") or "{}")
        result["visual_theme"] = str(visual_tags.get("visual_theme") or "")
        result["motif_keywords"] = list(visual_tags.get("motif_keywords") or [])
        result["color_keywords"] = list(visual_tags.get("color_keywords") or [])
        result["listing_ready"] = bool(result.get("listing_ready", 0))
        return result

    @classmethod
    def _insert_direct_title(
        cls,
        connection: sqlite3.Connection,
        trial_id: str,
        title_result: dict[str, Any],
        now: str,
    ) -> None:
        title = str(title_result.get("title") or "").strip()
        normalized = _normalize_title(str(title_result.get("normalized_title") or title)) or None
        visual_tags = {
            "visual_theme": str(title_result.get("visual_theme") or ""),
            "motif_keywords": list(title_result.get("motif_keywords") or ()),
            "color_keywords": list(title_result.get("color_keywords") or ()),
            "visual_signature": str(title_result.get("visual_signature") or ""),
        }
        connection.execute(
            """INSERT INTO pod_customization_direct_listing_titles
               (trial_id, style_task_id, status, title, normalized_title, visual_tags_json,
                model, prompt_version, attempt_count, error_message, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trial_id,
                str(title_result.get("style_task_id") or trial_id),
                str(title_result["status"]),
                title,
                normalized,
                json.dumps(visual_tags),
                str(title_result.get("model") or ""),
                str(title_result.get("prompt_version") or ""),
                max(0, int(title_result.get("attempt_count") or 0)),
                str(title_result.get("error_message") or "")[:500],
                now,
                now,
            ),
        )

    @staticmethod
    def _refresh_counts(connection: sqlite3.Connection, batch_id: str, now: str) -> None:
        style_grid = connection.execute(
            "SELECT 1 FROM pod_customization_style_grid_batches WHERE batch_id = ?", (batch_id,)
        ).fetchone() is not None
        if style_grid:
            connection.execute(
                """WITH style_counts AS (
                       SELECT style_index,
                              SUM(CASE WHEN status IN ('completed', 'failed') THEN 1 ELSE 0 END) AS settled,
                              SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed
                       FROM pod_customization_style_grid_results WHERE batch_id = ? GROUP BY style_index
                   )
                   UPDATE pod_customization_batches
                   SET processed_count = (SELECT COUNT(*) FROM style_counts WHERE settled = 4),
                       completed_count = (SELECT COUNT(*) FROM style_counts WHERE completed = 4),
                       failed_count = (SELECT COUNT(*) FROM style_counts WHERE settled = 4 AND completed < 4),
                       updated_at = ?
                   WHERE batch_id = ?""",
                (batch_id, now, batch_id),
            )
            return
        connection.execute(
            """UPDATE pod_customization_batches
               SET processed_count = (SELECT COUNT(*) FROM pod_customization_batch_items
                                      WHERE batch_id = ? AND status IN ('completed', 'failed')),
                   completed_count = (SELECT COUNT(*) FROM pod_customization_batch_items
                                      WHERE batch_id = ? AND status = 'completed'),
                   failed_count = (SELECT COUNT(*) FROM pod_customization_batch_items
                                   WHERE batch_id = ? AND status = 'failed'),
                   updated_at = ?
               WHERE batch_id = ?""",
            (batch_id, batch_id, batch_id, now, batch_id),
        )

    @staticmethod
    def _is_style_grid_batch(connection: sqlite3.Connection, batch_id: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM pod_customization_style_grid_batches WHERE batch_id = ?", (batch_id,)
        ).fetchone() is not None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _normalize_title(value: str) -> str:
    return " ".join(str(value or "").split()).strip().casefold()


def _migration_effect_is_present(connection: sqlite3.Connection, migration_name: str) -> bool:
    return pod_migration_effect_is_present(connection, migration_name)
