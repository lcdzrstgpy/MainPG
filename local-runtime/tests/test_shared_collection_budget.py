from pathlib import Path

from wh_local.data_collection.budget import UnlimitedApiBudget


def test_unlimited_collection_budget_never_rejects_calls() -> None:
    budget = UnlimitedApiBudget()
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
    assert denied.reservation_granted is True
