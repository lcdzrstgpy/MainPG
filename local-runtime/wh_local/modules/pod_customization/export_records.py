from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


class PodExportRecordStore:
    """Persist only export metadata; workbook bytes and credentials stay out of SQLite."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def record_success(
        self,
        *,
        batch_id: str,
        workspace_id: str,
        owner_user_id: str,
        file_name: str,
        format: str,
        exported_count: int,
        skipped_count: int,
    ) -> dict[str, Any]:
        export_id = uuid.uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO pod_customization_export_records (
                       export_id, batch_id, workspace_id, owner_user_id, file_name, format,
                       exported_count, skipped_count, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    export_id,
                    batch_id,
                    workspace_id,
                    owner_user_id,
                    file_name,
                    format,
                    int(exported_count),
                    int(skipped_count),
                    created_at,
                ),
            )
        return {
            "id": export_id,
            "batch_id": batch_id,
            "file_name": file_name,
            "format": format,
            "exported_count": int(exported_count),
            "skipped_count": int(skipped_count),
            "created_at": created_at,
        }

    def list_for_batch(
        self,
        *,
        batch_id: str,
        workspace_id: str,
        owner_user_id: str,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT export_id, batch_id, file_name, format, exported_count,
                          skipped_count, created_at
                   FROM pod_customization_export_records
                   WHERE batch_id = ? AND workspace_id = ? AND owner_user_id = ?
                   ORDER BY created_at DESC, export_id DESC""",
                (batch_id, workspace_id, owner_user_id),
            ).fetchall()
        return [
            {
                "id": row["export_id"],
                "batch_id": row["batch_id"],
                "file_name": row["file_name"],
                "format": row["format"],
                "exported_count": int(row["exported_count"]),
                "skipped_count": int(row["skipped_count"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
