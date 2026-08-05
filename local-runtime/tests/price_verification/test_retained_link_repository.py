from __future__ import annotations

from pathlib import Path

import pytest

from wh_local.price_verification.repository import (
    PriceVerificationNotFound,
    PriceVerificationRepository,
)


def _seed_quote_run(repository: PriceVerificationRepository, workspace_id: str = "workspace-A") -> str:
    run = repository.create_quote_run(
        workspace_id=workspace_id,
        command_id="shared-command-1",
        items=[
            {
                "quote_key": "quote-1",
                "skc_id": "SKC-1",
                "sku_id": "SKU-1",
                "official_link_url": "https://www.temu.com/goods.html?goods_id=1001",
                "main_image_url": "https://img.example/1001.jpg",
                "adjusted_declared_price_cny": "19.90",
            }
        ],
    )
    return run.run_id


def test_quote_decisions_append_revisions_and_keep_current_value(tmp_path: Path) -> None:
    repository = PriceVerificationRepository(tmp_path / "runtime.sqlite3")
    quote_run_id = _seed_quote_run(repository)

    first = repository.record_quote_decision(
        workspace_id="workspace-A",
        quote_run_id=quote_run_id,
        quote_key="quote-1",
        decision="retained",
        decided_by="user-1",
        note="首轮保留",
    )
    second = repository.record_quote_decision(
        workspace_id="workspace-A",
        quote_run_id=quote_run_id,
        quote_key="quote-1",
        decision="rejected",
        decided_by="user-2",
        note="复核拒绝",
    )

    assert (first.revision, second.revision) == (1, 2)
    assert first.decision == "retained"
    assert repository.list_current_quote_decisions(
        workspace_id="workspace-A", quote_run_id=quote_run_id
    ) == (second,)


def test_quote_decision_rejects_cross_workspace_quote(tmp_path: Path) -> None:
    repository = PriceVerificationRepository(tmp_path / "runtime.sqlite3")
    quote_run_id = _seed_quote_run(repository, "workspace-A")

    with pytest.raises(PriceVerificationNotFound):
        repository.record_quote_decision(
            workspace_id="workspace-B",
            quote_run_id=quote_run_id,
            quote_key="quote-1",
            decision="retained",
            decided_by="user-2",
        )


def test_sourcing_run_freezes_selected_quote_snapshot(tmp_path: Path) -> None:
    repository = PriceVerificationRepository(tmp_path / "runtime.sqlite3")
    quote_run_id = _seed_quote_run(repository)
    source_quote = {
        "quote_key": "quote-1",
        "official_link_url": "https://www.temu.com/goods.html?goods_id=1001",
        "main_image_url": "https://img.example/1001.jpg",
        "selected_price_cny": "19.90",
        "skc_id": "SKC-1",
        "sku_id": "SKU-1",
    }

    sourcing_run = repository.create_sourcing_run(
        workspace_id="workspace-A",
        quote_run_id=quote_run_id,
        candidates=[],
        source_quotes=[source_quote],
        task_count=1,
    )
    frozen = repository.list_sourcing_run_quotes(
        workspace_id="workspace-A", sourcing_run_id=sourcing_run.run_id
    )

    assert len(frozen) == 1
    assert frozen[0].official_link_url == source_quote["official_link_url"]
    assert frozen[0].selected_price_cny == "19.90"
    assert frozen[0].snapshot == source_quote
