from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from sqlalchemy import func, select, text

from wh_local.modules.product_processing.domain.models import DailySelectionHandoffEnvelope
from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.orm import ProductDraftRow
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository
from wh_local.modules.product_processing.service import ProductProcessingService


def _service(tmp_path: Path, database_url: str = "sqlite:///:memory:") -> ProductProcessingService:
    return ProductProcessingService(
        ProductProcessingRepository(create_database(database_url)),
        ProductProcessingAssets(tmp_path / "assets"),
    )


def _handoff(run_id: str, handoff_id: str) -> DailySelectionHandoffEnvelope:
    candidate_id = "1688:shared-offer"
    return DailySelectionHandoffEnvelope(
        handoff_id=handoff_id,
        run_id=run_id,
        candidate_id=candidate_id,
        workspace_id="local",
        payload_json=json.dumps(
            {
                "candidate": {
                    "candidate_id": candidate_id,
                    "offer_id": "shared-offer",
                    "source_platform": "1688",
                    "source_url": "https://detail.1688.com/offer/shared-offer.html",
                    "source_title": "Repeated product selected again",
                },
                "images": {"main": "", "gallery": [], "detail": []},
                "skus": [],
                "attributes": {},
                "selection_metadata": {},
            },
            ensure_ascii=False,
        ),
        status="pending",
        idempotency_key=f"idempotency-{handoff_id}",
        created_at="2026-08-13T00:00:00+00:00",
    )


def test_new_run_creates_a_new_draft_but_same_handoff_replay_does_not(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = _handoff("run-1", "handoff-1")
    second = _handoff("run-2", "handoff-2")

    first_result = service.consume_daily_selection_handoffs([first])
    replay_result = service.consume_daily_selection_handoffs([first])
    second_result = service.consume_daily_selection_handoffs([second])

    assert first_result["created"] == 1
    assert replay_result["created"] == 0
    assert replay_result["replayed"] == 1
    assert second_result["created"] == 1
    assert first_result["drafts"][0]["id"] == replay_result["drafts"][0]["id"]
    assert second_result["drafts"][0]["id"] != first_result["drafts"][0]["id"]
    with service.repository.database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(ProductDraftRow)) == 2


def test_legacy_sqlite_candidate_unique_constraint_is_removed_without_data_loss(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE product_processing_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL DEFAULT 'local',
            source_type TEXT NOT NULL DEFAULT 'manual',
            source_ref TEXT NOT NULL DEFAULT '',
            candidate_id TEXT,
            selection_run_id TEXT,
            handoff_id TEXT UNIQUE,
            handoff_idempotency_key TEXT UNIQUE,
            skc TEXT,
            sku TEXT,
            product_name TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            image_url TEXT NOT NULL DEFAULT '',
            image_path TEXT NOT NULL DEFAULT '',
            cost REAL,
            declared_price REAL,
            status TEXT NOT NULL DEFAULT 'draft',
            raw_payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            preview_revision INTEGER NOT NULL DEFAULT 0,
            preview_overrides_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE (workspace_id, candidate_id)
        );
        CREATE INDEX idx_product_processing_drafts_candidate
            ON product_processing_drafts (candidate_id);
        INSERT INTO product_processing_drafts
            (workspace_id, candidate_id, title, product_name, created_at, updated_at)
        VALUES ('local', '1688:shared-offer', 'Historical draft', 'Historical draft', 'old', 'old');
        """
    )
    connection.commit()
    connection.close()

    database = create_database(f"sqlite:///{database_path.as_posix()}")
    with database.sessions.begin() as session:
        session.add(
            ProductDraftRow(
                workspace_id="local",
                candidate_id="1688:shared-offer",
                title="New draft",
                product_name="New draft",
            )
        )
    with database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(ProductDraftRow)) == 2
        schema = session.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='product_processing_drafts'")
        ).scalar_one()
        assert "UNIQUE (workspace_id, candidate_id)" not in schema
        assert session.execute(text("PRAGMA foreign_key_check")).all() == []
