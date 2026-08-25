# -*- coding: utf-8 -*-
"""采集完成后剔除「已入池/已处理」商品的回归测试。"""
from __future__ import annotations

from pathlib import Path

from wh_local.data_collection.contracts import DailySelectionCandidate
from wh_local.data_collection.repository import DailySelectionRepository
from wh_local.data_collection.service import DailySelectionService


def _candidate(
    offer_id: str,
    *,
    status: str = "candidate",
    url_suffix: str | None = None,
) -> DailySelectionCandidate:
    suffix = url_suffix or offer_id
    return DailySelectionCandidate(
        candidate_id=f"cand:{offer_id}",
        offer_id=offer_id,
        source_platform="1688",
        source_url=f"https://detail.1688.com/offer/{suffix}.html?spm=a2b0",
        source_title=f"商品标题-{offer_id}",
        main_image_url=f"https://cbu01.alicdn.com/img/{offer_id}.jpg",
        status=status,
    )


def _service(database_path: Path) -> DailySelectionService:
    repository = DailySelectionRepository(database_path)
    return DailySelectionService(
        repository=repository,
        budget=object(),
        provider_config_resolver=lambda actor: {},
        provider_factory=lambda config: None,
    )


def test_confirmed_offer_ids_only_returns_confirmed_history(tmp_path: Path) -> None:
    repo = DailySelectionRepository(tmp_path / "selection.sqlite3")
    repo.save_run(
        workspace_id="ws-a",
        run_id="run-1",
        status="completed",
        candidates=(
            _candidate("111", status="confirmed"),
            _candidate("222", status="candidate"),
            _candidate("333", status="rejected"),
        ),
    )
    assert repo.confirmed_offer_ids(workspace_id="ws-a") == frozenset({"111"})
    # 不同工作区互不影响
    assert repo.confirmed_offer_ids(workspace_id="ws-b") == frozenset()


def test_deduplicate_removes_confirmed_and_draft_pool_items(tmp_path: Path) -> None:
    repo = DailySelectionRepository(tmp_path / "selection.sqlite3")
    # 历史批次：商品 111 已确认入池
    repo.save_run(
        workspace_id="ws-a",
        run_id="run-old",
        status="completed",
        candidates=(_candidate("111", status="confirmed"),),
    )
    service = _service(tmp_path / "selection.sqlite3")
    # 草稿池里已存在商品 333 的链接（含 query，应归一化后匹配）
    service._existing_source_refs = lambda workspace: frozenset(
        {"https://detail.1688.com/offer/333.html"}
    )

    candidates = (
        _candidate("111"),  # 历史已确认 → 剔除
        _candidate("333"),  # 草稿池已有 → 剔除
        _candidate("444"),  # 全新 → 保留
    )
    kept, removed = service._deduplicate("ws-a", candidates)

    assert {c.offer_id for c in kept} == {"444"}
    assert removed == frozenset({"111", "333"})
    # 历史确认按工作区隔离，但草稿池去重不区分工作区（resolver 与 workspace 无关）
    kept_b, removed_b = service._deduplicate("ws-b", candidates)
    assert {c.offer_id for c in kept_b} == {"111", "444"}
    assert removed_b == frozenset({"333"})


def test_deduplicate_without_injected_pool_only_uses_confirmed_history(
    tmp_path: Path,
) -> None:
    repo = DailySelectionRepository(tmp_path / "selection.sqlite3")
    service = _service(tmp_path / "selection.sqlite3")
    candidates = (
        _candidate("111"),
        _candidate("333", url_suffix="333"),
    )
    # 未注入 existing_source_refs：草稿池商品不剔除，只保留全部
    kept, removed = service._deduplicate("ws-a", candidates)
    assert {c.offer_id for c in kept} == {"111", "333"}
    assert removed == frozenset()
    # 但历史确认仍生效
    repo.save_run(
        workspace_id="ws-a",
        run_id="run-old",
        status="completed",
        candidates=(_candidate("111", status="confirmed"),),
    )
    service2 = _service(tmp_path / "selection.sqlite3")
    kept2, removed2 = service2._deduplicate("ws-a", candidates)
    assert {c.offer_id for c in kept2} == {"333"}
    assert removed2 == frozenset({"111"})


def test_preview_metadata_records_deduplicated_count(tmp_path: Path) -> None:
    repo = DailySelectionRepository(tmp_path / "selection.sqlite3")
    repo.save_run(
        workspace_id="ws-a",
        run_id="run-old",
        status="completed",
        candidates=(_candidate("111", status="confirmed"),),
    )
    service = _service(tmp_path / "selection.sqlite3")
    kept, removed = service._deduplicate(
        "ws-a", (_candidate("111"), _candidate("222"))
    )
    metadata = {"deduplicated": {"count": len(removed), "removed_identifiers": sorted(removed)}}
    run = repo.save_run(
        workspace_id="ws-a",
        run_id="run-new",
        status="completed",
        candidates=kept,
        metadata=metadata,
    )
    assert run.candidate_count == 1
    assert run.metadata["deduplicated"]["count"] == 1
    assert run.metadata["deduplicated"]["removed_identifiers"] == ["111"]
