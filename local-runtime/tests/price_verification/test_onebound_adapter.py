from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.price_verification.contracts import PriceVerificationActor  # noqa: E402
from wh_local.price_verification.repository import PriceVerificationRepository  # noqa: E402
from wh_local.price_verification.sourcing.contracts import SourceSearchTask  # noqa: E402
from wh_local.price_verification.sourcing.onebound_adapter import OneBoundSourceAdapter  # noqa: E402


@dataclass(frozen=True)
class Audit:
    provider: str = "onebound-1688"
    operation: str = "item_search_img"
    request_id: str | None = "request-1"
    captured_at: str | None = "2026-08-04T00:00:00Z"
    request_summary: Mapping[str, Any] | None = None
    response_summary: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ProviderResult:
    response: Mapping[str, Any]
    audits: tuple[Audit, ...] = ()
    error: object | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class FakeProvider:
    def __init__(self, *, fail_images: set[str] | None = None, fail_details: bool = False) -> None:
        self.fail_images = fail_images or set()
        self.fail_details = fail_details
        self.calls: list[str] = []

    def upload_reference_image(self, image_url: str) -> ProviderResult:
        self.calls.append(f"upload:{image_url}")
        return ProviderResult({"image_id": "cached-image"})

    def search_by_image(self, criteria: object) -> ProviderResult:
        image_url = str(getattr(criteria, "reference_image_url"))
        self.calls.append(f"search:{image_url}")
        if image_url in self.fail_images:
            return ProviderResult({}, (Audit(operation="item_search_img"),), error=RuntimeError("upstream failed"))
        return ProviderResult(
            {
                "items": [
                    {
                        "num_iid": "1688001",
                        "detail_url": "https://detail.1688.com/offer/1688001.html",
                        "title": "同款收纳盒 红色",
                        "price": "10",
                        "freight": "2",
                        "moq": 1,
                        "variants": [{"name": "红色"}],
                    }
                ]
            },
            (Audit(operation="item_search_img", request_summary={"api_key": "should-not-leak"}),),
        )

    def get_item_detail(self, offer_id: str) -> ProviderResult:
        self.calls.append(f"detail:{offer_id}")
        if self.fail_details:
            return ProviderResult({}, (Audit(operation="item_get"),), error=RuntimeError("upstream failed"))
        return ProviderResult(
            {
                "item": {
                    "num_iid": offer_id,
                    "title": "同款收纳盒 红色",
                    "price": "10",
                    "freight": "2",
                    "moq": 1,
                    "variants": [{"name": "红色"}],
                }
            },
            (Audit(operation="item_get", response_summary={"token": "should-not-leak"}),),
        )


def actor(workspace_id: str) -> PriceVerificationActor:
    return PriceVerificationActor(actor_id="tester", workspace_id=workspace_id)


def source_task(*, key: str = "SKC-1", image_url: str = "https://images.example/box.jpg") -> SourceSearchTask:
    return SourceSearchTask(
        task_key=key,
        skc_id=key,
        main_image_url=image_url,
        source_quote_keys=(f"{key}:SKU-1",),
    )


def test_adapter_uses_its_own_budget_table(tmp_path: Path) -> None:
    repository = PriceVerificationRepository(tmp_path / "runtime.sqlite3")
    provider = FakeProvider()

    result = OneBoundSourceAdapter(repository, lambda: provider).search_by_image(actor("A"), [source_task()])

    assert result["counts"]["processed_quotes"] == 1
    assert _budget_used(repository, "A") == 2
    assert provider.calls == [
        "upload:https://images.example/box.jpg",
        "search:https://images.example/box.jpg",
        "detail:1688001",
    ]


def test_adapter_preserves_success_when_another_task_fails(tmp_path: Path) -> None:
    repository = PriceVerificationRepository(tmp_path / "runtime.sqlite3")
    provider = FakeProvider(fail_images={"https://images.example/fail.jpg"})

    result = OneBoundSourceAdapter(repository, lambda: provider).search_by_image(
        actor("A"),
        [source_task(key="SKC-good"), source_task(key="SKC-failed", image_url="https://images.example/fail.jpg")],
    )

    assert result["counts"] == {"processed_quotes": 2, "failed_quotes": 1, "candidate_count": 1}
    assert result["items"][0]["status"] == "succeeded"
    assert result["items"][1] == {
        "task_key": "SKC-failed",
        "skc_id": "SKC-failed",
        "source_quote_keys": ["SKC-failed:SKU-1"],
        "status": "failed",
        "error": "provider request failed",
        "candidates": [],
        "evidence": [{"provider": "onebound-1688", "operation": "item_search_img", "request_id": "request-1", "captured_at": "2026-08-04T00:00:00Z", "request_summary": {}, "response_summary": {}}],
    }


def test_adapter_redacts_provider_audits_before_returning_evidence(tmp_path: Path) -> None:
    repository = PriceVerificationRepository(tmp_path / "runtime.sqlite3")

    result = OneBoundSourceAdapter(repository, lambda: FakeProvider()).search_by_image(actor("A"), [source_task()])

    evidence = result["items"][0]["evidence"]
    assert "should-not-leak" not in repr(evidence)
    assert evidence[0]["request_summary"] == {"api_key": "[REDACTED]"}
    assert evidence[-1]["response_summary"] == {"token": "[REDACTED]"}


def test_adapter_returns_a_failed_item_when_detail_lookup_fails(tmp_path: Path) -> None:
    repository = PriceVerificationRepository(tmp_path / "runtime.sqlite3")

    result = OneBoundSourceAdapter(repository, lambda: FakeProvider(fail_details=True)).search_by_image(
        actor("A"), [source_task()]
    )

    assert result["items"][0]["status"] == "failed"
    assert result["items"][0]["candidates"] == []
    assert result["counts"] == {"processed_quotes": 1, "failed_quotes": 1, "candidate_count": 0}


def test_adapter_settles_against_the_reservation_date_across_shanghai_midnight(tmp_path: Path) -> None:
    repository = PriceVerificationRepository(tmp_path / "runtime.sqlite3")
    times = iter((
        datetime(2026, 8, 4, 15, 59, tzinfo=UTC),
        datetime(2026, 8, 4, 16, 0, tzinfo=UTC),
    ))
    adapter = OneBoundSourceAdapter(repository, FakeProvider, clock=lambda: next(times))

    result = adapter.search_by_image(actor("A"), [source_task()])

    assert result["counts"]["processed_quotes"] == 1
    assert _budget_used(repository, "A") == 2


def _budget_used(repository: PriceVerificationRepository, workspace_id: str) -> int:
    with sqlite3.connect(repository.database_path) as connection:
        row = connection.execute(
            "SELECT used_count FROM price_verification_provider_budgets WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()
    assert row is not None
    return int(row[0])
