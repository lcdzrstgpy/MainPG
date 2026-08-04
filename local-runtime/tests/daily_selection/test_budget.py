from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.modules.daily_selection.budget import (  # noqa: E402
    SQLiteDailyApiBudget,
    credential_fingerprint,
)


def test_sqlite_budget_never_overspends_concurrent_same_workspace_provider_and_day(tmp_path: Path) -> None:
    database_path = tmp_path / "budget.sqlite3"
    provider_fingerprint = credential_fingerprint({"api_key": "only-in-memory", "secret": "also-in-memory"})
    moment = datetime(2026, 8, 4, 9, 0, 0)

    def reserve_one_call() -> bool:
        state = SQLiteDailyApiBudget(database_path).reserve(
            workspace_id="workspace-a",
            provider_fingerprint=provider_fingerprint,
            max_api_calls=1,
            api_calls=1,
            now=moment,
        )
        return state.allowed

    with ThreadPoolExecutor(max_workers=2) as executor:
        allowed = list(executor.map(lambda _: reserve_one_call(), range(2)))

    final_state = SQLiteDailyApiBudget(database_path).state(
        workspace_id="workspace-a",
        provider_fingerprint=provider_fingerprint,
        max_api_calls=1,
        now=moment,
    )

    assert allowed.count(True) == 1
    assert final_state.api_calls_used == 1
    assert final_state.api_calls_remaining == 0
