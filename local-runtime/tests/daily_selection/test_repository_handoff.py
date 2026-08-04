from __future__ import annotations

import json
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.modules.daily_selection.contracts import (  # noqa: E402
    ApiEvidence,
    DailySelectionCandidate,
    SourceVariantRecord,
)
from wh_local.modules.daily_selection.repository import (  # noqa: E402
    DailySelectionRepository,
    DailySelectionRunNotFound,
)


def candidate(candidate_id: str = "1688:offer-1") -> DailySelectionCandidate:
    return DailySelectionCandidate(
        candidate_id=candidate_id,
        offer_id="offer-1",
        source_platform="1688",
        source_url="https://detail.1688.com/offer-1.html",
        source_title="便携露营灯",
        main_image_url="https://images.example.test/main.jpg",
        source_image_urls=(
            "https://images.example.test/gallery-1.jpg",
            "https://images.example.test/gallery-2.jpg",
        ),
        source_detail_image_urls=("https://images.example.test/detail-1.jpg",),
        source_variant_records=(
            SourceVariantRecord(
                sku_id="sku-red",
                attributes={"颜色": "红色", "capacity": Decimal("1.50")},
                image_url="https://images.example.test/sku-red.jpg",
                price_cny=Decimal("12.30"),
                min_order_quantity=2,
            ),
        ),
        source_attributes={"材质": "ABS", "voltage": Decimal("3.70")},
        price_cny=Decimal("12.30"),
        min_order_quantity=2,
        selection_score=Decimal("88.50"),
        selection_reasons=("strong_evidence",),
        evidence=(
            ApiEvidence(
                provider="onebound-1688",
                operation="item_get",
                request_id="request-1",
                response_summary={"price": Decimal("12.30")},
            ),
        ),
        shop_name="测试工厂",
        captured_fields=("images", "skus", "attributes"),
        score_components={"evidence": Decimal("40"), "match": Decimal("24.50")},
        raw_payload={"nested": {"price": Decimal("12.30")}},
    )


@pytest.fixture
def repository(tmp_path: Path) -> DailySelectionRepository:
    return DailySelectionRepository(tmp_path / "daily-selection.sqlite3")


def save_run(repository: DailySelectionRepository, *, workspace_id: str = "workspace-a") -> None:
    repository.save_run(
        workspace_id=workspace_id,
        run_id="run-1",
        status="completed",
        candidates=(candidate(),),
        criteria={"keywords": ["露营灯"], "min_price": Decimal("10.00")},
        metadata={"api_calls": 2, "budget_remaining": Decimal("48")},
        created_at="2026-08-04T09:00:00+08:00",
    )


def test_migration_creates_owned_tables_and_workspace_run_indexes(
    repository: DailySelectionRepository,
) -> None:
    with sqlite3.connect(repository.database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }

    assert {
        "daily_selection_runs",
        "daily_selection_candidates",
        "daily_selection_feedback",
        "daily_selection_provider_budgets",
        "daily_selection_handoffs",
    } <= tables
    assert {
        "idx_daily_selection_runs_workspace_created",
        "idx_daily_selection_candidates_workspace_run",
        "idx_daily_selection_feedback_workspace_run",
        "idx_daily_selection_provider_budgets_workspace_date",
        "idx_daily_selection_handoffs_workspace_run",
    } <= indexes


def test_repository_supports_an_in_memory_sqlite_database() -> None:
    repository = DailySelectionRepository(":memory:")

    repository.save_run(
        workspace_id="workspace-a",
        run_id="run-memory",
        status="empty",
        candidates=(),
    )

    assert repository.get_run(
        workspace_id="workspace-a", run_id="run-memory"
    ).candidate_count == 0


def test_save_run_and_read_back_equivalent_pydantic_decimal_candidate_snapshot(
    repository: DailySelectionRepository,
) -> None:
    original = candidate()

    repository.save_run(
        workspace_id="workspace-a",
        run_id="run-1",
        status="completed",
        candidates=(original,),
        criteria={"keywords": ["露营灯"], "min_price": Decimal("10.00")},
        metadata={"api_calls": 2, "budget_remaining": Decimal("48")},
        created_at="2026-08-04T09:00:00+08:00",
    )

    restored = repository.get_run(workspace_id="workspace-a", run_id="run-1")

    assert restored.run_id == "run-1"
    assert restored.workspace_id == "workspace-a"
    assert restored.status == "completed"
    assert restored.candidates == (original,)
    assert restored.criteria == {"keywords": ["露营灯"], "min_price": "10.00"}
    assert restored.metadata == {"api_calls": 2, "budget_remaining": "48"}
    assert restored.created_at == "2026-08-04T09:00:00+08:00"

    with sqlite3.connect(repository.database_path) as connection:
        raw_candidate_json = connection.execute(
            "SELECT raw_candidate_json FROM daily_selection_candidates"
        ).fetchone()[0]
    assert json.loads(raw_candidate_json)["selection_score"] == "88.50"


def test_decimal_snapshot_encoding_cannot_collide_with_source_mapping_keys(
    repository: DailySelectionRepository,
) -> None:
    original = candidate().model_copy(
        update={
            "source_attributes": {
                "__daily_selection_decimal__": "provider-value",
            }
        }
    )

    repository.save_run(
        workspace_id="workspace-a",
        run_id="run-collision",
        status="completed",
        candidates=(original,),
    )

    restored = repository.get_run(
        workspace_id="workspace-a", run_id="run-collision"
    ).candidates[0]
    assert restored == original


def test_list_runs_is_workspace_scoped_and_newest_first(
    repository: DailySelectionRepository,
) -> None:
    save_run(repository)
    repository.save_run(
        workspace_id="workspace-a",
        run_id="run-2",
        status="empty",
        candidates=(),
        created_at="2026-08-04T10:00:00+08:00",
    )
    repository.save_run(
        workspace_id="workspace-b",
        run_id="run-3",
        status="completed",
        candidates=(candidate("1688:other"),),
        created_at="2026-08-04T11:00:00+08:00",
    )

    runs = repository.list_runs(workspace_id="workspace-a")

    assert [run.run_id for run in runs] == ["run-2", "run-1"]
    assert [run.candidate_count for run in runs] == [0, 1]
    assert all(run.workspace_id == "workspace-a" for run in runs)


def test_resaving_a_run_replaces_the_candidate_snapshot_without_stale_rows(
    repository: DailySelectionRepository,
) -> None:
    save_run(repository)

    repository.save_run(
        workspace_id="workspace-a",
        run_id="run-1",
        status="empty",
        candidates=(),
        created_at="2026-08-04T09:02:00+08:00",
    )

    restored = repository.get_run(workspace_id="workspace-a", run_id="run-1")
    assert restored.candidate_count == 0
    assert restored.candidates == ()


def test_resaving_cannot_discard_existing_feedback_or_handoffs(
    repository: DailySelectionRepository,
) -> None:
    save_run(repository)
    original_handoffs = repository.confirm_candidates(
        workspace_id="workspace-a",
        run_id="run-1",
        candidate_ids=("1688:offer-1",),
    )

    with pytest.raises(ValueError, match="feedback or handoffs"):
        repository.save_run(
            workspace_id="workspace-a",
            run_id="run-1",
            status="empty",
            candidates=(),
        )

    repeated = repository.confirm_candidates(
        workspace_id="workspace-a",
        run_id="run-1",
        candidate_ids=("1688:offer-1",),
    )
    assert repeated == original_handoffs


def test_feedback_is_saved_without_deleting_candidate_evidence(
    repository: DailySelectionRepository,
) -> None:
    save_run(repository)

    feedback = repository.record_feedback(
        workspace_id="workspace-a",
        run_id="run-1",
        candidate_id="1688:offer-1",
        reason="margin_too_low",
        details={"expected_margin": Decimal("0.25")},
        created_at="2026-08-04T09:05:00+08:00",
    )

    assert feedback.workspace_id == "workspace-a"
    assert feedback.reason == "margin_too_low"
    assert feedback.details == {"expected_margin": "0.25"}
    restored_candidate = repository.get_run(
        workspace_id="workspace-a", run_id="run-1"
    ).candidates[0]
    assert restored_candidate.status == "rejected"
    assert restored_candidate.evidence == candidate().evidence


def test_run_json_metadata_is_redacted_before_persistence(
    repository: DailySelectionRepository,
) -> None:
    repository.save_run(
        workspace_id="workspace-a",
        run_id="run-safe",
        status="completed",
        candidates=(),
        criteria={"keywords": ["露营灯"], "api_key": "must-not-persist"},
        metadata={"note": "Authorization=Bearer must-not-persist"},
    )

    restored = repository.get_run(workspace_id="workspace-a", run_id="run-safe")

    assert "api_key" not in restored.criteria
    assert restored.metadata == {"note": "Authorization=[redacted]"}
    with sqlite3.connect(repository.database_path) as connection:
        stored = " ".join(
            connection.execute(
                "SELECT criteria_json, metadata_json FROM daily_selection_runs"
            ).fetchone()
        )
    assert "must-not-persist" not in stored


def test_cross_workspace_run_feedback_and_confirmation_are_rejected(
    repository: DailySelectionRepository,
) -> None:
    save_run(repository, workspace_id="workspace-a")

    with pytest.raises(DailySelectionRunNotFound):
        repository.get_run(workspace_id="workspace-b", run_id="run-1")
    with pytest.raises(DailySelectionRunNotFound):
        repository.record_feedback(
            workspace_id="workspace-b",
            run_id="run-1",
            candidate_id="1688:offer-1",
            reason="not_for_this_workspace",
        )
    with pytest.raises(DailySelectionRunNotFound):
        repository.confirm_candidates(
            workspace_id="workspace-b",
            run_id="run-1",
            candidate_ids=("1688:offer-1",),
        )


def test_repeated_confirmation_creates_one_complete_handoff_and_no_product_drafts(
    repository: DailySelectionRepository,
) -> None:
    save_run(repository)

    first = repository.confirm_candidates(
        workspace_id="workspace-a",
        run_id="run-1",
        candidate_ids=("1688:offer-1",),
        created_at="2026-08-04T09:10:00+08:00",
    )
    second = repository.confirm_candidates(
        workspace_id="workspace-a",
        run_id="run-1",
        candidate_ids=("1688:offer-1",),
        created_at="2026-08-04T09:11:00+08:00",
    )

    assert second == first
    assert len(first) == 1
    handoff = first[0]
    assert handoff.run_id == "run-1"
    assert handoff.candidate_id == "1688:offer-1"
    assert handoff.workspace_id == "workspace-a"
    assert handoff.status == "pending"
    assert handoff.idempotency_key
    payload = json.loads(handoff.payload_json)
    assert payload["images"] == {
        "main": "https://images.example.test/main.jpg",
        "gallery": [
            "https://images.example.test/gallery-1.jpg",
            "https://images.example.test/gallery-2.jpg",
        ],
        "detail": ["https://images.example.test/detail-1.jpg"],
        "sku": ["https://images.example.test/sku-red.jpg"],
    }
    assert payload["skus"][0]["sku_id"] == "sku-red"
    assert payload["skus"][0]["price_cny"] == "12.30"
    assert payload["attributes"] == {"材质": "ABS", "voltage": "3.70"}
    assert payload["source_evidence"][0]["operation"] == "item_get"
    assert payload["selection_metadata"]["selection_score"] == "88.50"
    assert payload["selection_metadata"]["score_components"]["match"] == "24.50"

    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM daily_selection_handoffs"
        ).fetchone()[0] == 1
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "product_drafts" not in tables


def test_confirm_candidates_is_atomic_when_any_candidate_is_missing(
    repository: DailySelectionRepository,
) -> None:
    save_run(repository)

    with pytest.raises(ValueError, match="candidate"):
        repository.confirm_candidates(
            workspace_id="workspace-a",
            run_id="run-1",
            candidate_ids=("1688:offer-1", "1688:missing"),
        )

    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM daily_selection_handoffs"
        ).fetchone()[0] == 0
