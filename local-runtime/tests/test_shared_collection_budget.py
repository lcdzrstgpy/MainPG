from pathlib import Path

from wh_local.data_collection.budget import SQLiteDailyApiBudget


def test_persistent_collection_budget_start_does_not_reset_daily_usage(tmp_path: Path) -> None:
    budget = SQLiteDailyApiBudget(tmp_path / "budget.sqlite3")
    fingerprint = "a" * 64
    first = budget.reserve(
        workspace_id="workspace-1",
        provider_fingerprint=fingerprint,
        max_api_calls=2,
    )

    budget.start()
    second = budget.reserve(
        workspace_id="workspace-1",
        provider_fingerprint=fingerprint,
        max_api_calls=2,
    )
    denied = budget.reserve(
        workspace_id="workspace-1",
        provider_fingerprint=fingerprint,
        max_api_calls=2,
    )

    assert first.reservation_granted is True
    assert second.reservation_granted is True
    assert denied.reservation_granted is False
