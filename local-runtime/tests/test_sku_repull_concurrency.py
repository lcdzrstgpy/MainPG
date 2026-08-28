"""SKU 补齐并发化验证：线程池确实并行拉取，且计数/取消语义保持正确。"""

from __future__ import annotations

from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from wh_local.data_collection import normalizer
from wh_local.data_collection.contracts import DailySelectionCandidate, SourceVariantRecord
from wh_local.data_collection.provider import DailySelectionError, ProviderCallResult
from wh_local.data_collection.repository import DailySelectionRepository
from wh_local.data_collection.sku_repull import SkuRepullJob, SkuRepullRunner


class FakeCandidate:
    def __init__(self, offer_id: str) -> None:
        self.offer_id = offer_id


class FakeRepository:
    def __init__(self) -> None:
        self.updated: list[object] = []
        self.run_metadata: dict[str, dict] = {}
        self.run_status: dict[str, str] = {}

    def update_candidate(self, *, workspace_id: str, run_id: str, candidate: object, timestamp: str) -> None:
        self.updated.append(candidate)

    def get_run(self, *, workspace_id: str, run_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            metadata=self.run_metadata.get(run_id, {}),
            status=self.run_status.get(run_id, "partial"),
            candidates=(),
        )

    def update_run_metadata(
        self,
        *,
        workspace_id: str,
        run_id: str,
        metadata: dict,
        status: str | None = None,
    ) -> None:
        self.run_metadata[run_id] = metadata
        if status is not None:
            self.run_status[run_id] = status


class FakeProvider:
    def __init__(
        self,
        *,
        fail_offer_ids: frozenset[str] = frozenset(),
        block: threading.Event | None = None,
    ) -> None:
        self._fail = fail_offer_ids
        self._block = block
        self._lock = threading.Lock()
        self.calls: list[str] = []
        self.active = 0
        self.peak_active = 0

    def get_item_detail(self, offer_id: str) -> ProviderCallResult:
        with self._lock:
            self.calls.append(offer_id)
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
        try:
            if self._block is not None:
                self._block.wait(timeout=5)
            else:
                # 让并发调用在时间上重叠，便于断言真正的并行度。
                time.sleep(0.02)
            if offer_id in self._fail:
                return ProviderCallResult(
                    {},
                    ("evidence",),
                    DailySelectionError(code="upstream_failed", message="boom"),
                )
            return ProviderCallResult(
                {"data": {"offer_id": offer_id}},
                ("evidence",),
                None,
            )
        finally:
            with self._lock:
                self.active -= 1


def fake_enrich(candidate: FakeCandidate, response: dict, evidence: object = None) -> dict:
    return {"offer_id": candidate.offer_id, "from": response.get("data", {}).get("offer_id")}


@pytest.fixture()
def runner_factory(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(normalizer, "enrich_candidate_with_detail", fake_enrich)

    def build(provider: FakeProvider) -> tuple[SkuRepullRunner, FakeRepository]:
        repository = FakeRepository()
        runner = SkuRepullRunner(
            repository=repository,
            provider_config_resolver=lambda actor: {"api_key": "fake"},
            provider_factory=lambda config: provider,
        )
        return runner, repository

    return build


def _wait_until(predicate: object, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("wait_until timed out")


def test_sku_repull_runs_concurrently(runner_factory) -> None:
    provider = FakeProvider()
    runner, repository = runner_factory(provider)
    actor = SimpleNamespace(workspace_id="ws-1")
    run = SimpleNamespace(run_id="run-1", metadata={})
    targets = [FakeCandidate(f"offer-{index}") for index in range(8)]

    state = runner.start(actor=actor, run=run, targets=targets, previous_round=0)
    assert state["status"] == "running"
    assert state["total"] == 8

    _wait_until(lambda: provider.peak_active >= 2, timeout=5.0)
    _wait_until(lambda: runner.state(actor=actor, run=run)["status"] == "completed")

    final = runner.state(actor=actor, run=run)
    assert final["succeeded"] == 8
    assert final["done"] == 8
    assert final["status"] == "completed"
    assert len(repository.updated) == 8
    assert repository.run_metadata["run-1"]["sku_repull"]["status"] == "completed"


def test_sku_repull_failures_are_counted(runner_factory) -> None:
    provider = FakeProvider(fail_offer_ids=frozenset({"offer-1", "offer-2"}))
    runner, repository = runner_factory(provider)
    actor = SimpleNamespace(workspace_id="ws-1")
    run = SimpleNamespace(run_id="run-2", metadata={})
    targets = [FakeCandidate("offer-1"), FakeCandidate("offer-2"), FakeCandidate("offer-3")]

    runner.start(actor=actor, run=run, targets=targets, previous_round=0)
    _wait_until(lambda: runner.state(actor=actor, run=run)["status"] == "completed")

    final = runner.state(actor=actor, run=run)
    assert (final["succeeded"], final["failed"], final["done"]) == (1, 2, 3)
    assert len(repository.updated) == 1


def test_sku_repull_success_reconciles_partial_run_status_and_errors(runner_factory) -> None:
    provider = FakeProvider()
    runner, repository = runner_factory(provider)
    actor = SimpleNamespace(workspace_id="ws-1")
    detail_error = {"code": "timeout", "message": "timed out", "context": {}}
    repository.run_status["run-recovered"] = "partial"
    repository.run_metadata["run-recovered"] = {
        "errors": [detail_error],
        "detail_errors": {"offer-1": detail_error},
    }
    run = SimpleNamespace(run_id="run-recovered", metadata=repository.run_metadata["run-recovered"])

    runner.start(
        actor=actor,
        run=run,
        targets=[FakeCandidate("offer-1")],
        previous_round=0,
    )
    _wait_until(lambda: repository.run_status.get("run-recovered") == "completed")

    assert repository.run_status["run-recovered"] == "completed"
    assert repository.run_metadata["run-recovered"]["errors"] == []
    assert repository.run_metadata["run-recovered"]["detail_errors"] == {}


def test_sku_repull_reconciliation_is_persisted_atomically(tmp_path: Path) -> None:
    repository = DailySelectionRepository(tmp_path / "selection.sqlite3")
    detail_error = {"code": "timeout", "message": "timed out", "context": {}}
    repository.save_run(
        workspace_id="ws-1",
        run_id="run-persisted",
        status="partial",
        candidates=(
            DailySelectionCandidate(
                candidate_id="candidate-1",
                offer_id="offer-1",
                source_platform="1688",
                source_url="https://detail.1688.com/offer/offer-1.html",
                source_title="收纳盒",
                main_image_url=None,
                source_variant_records=(SourceVariantRecord(sku_id="sku-1"),),
            ),
        ),
        metadata={
            "errors": [detail_error],
            "detail_errors": {"offer-1": detail_error},
        },
    )
    runner = SkuRepullRunner(
        repository=repository,
        provider_config_resolver=lambda actor: {},
        provider_factory=lambda config: None,
    )
    job = SkuRepullJob(
        run_id="run-persisted",
        workspace_id="ws-1",
        round=1,
        total=1,
        done=1,
        succeeded=1,
        status="completed",
    )

    runner._persist(SimpleNamespace(workspace_id="ws-1"), job)

    recovered = repository.get_run(workspace_id="ws-1", run_id="run-persisted")
    assert recovered.status == "completed"
    assert recovered.metadata["errors"] == []
    assert recovered.metadata["detail_errors"] == {}
    assert recovered.metadata["sku_repull"]["status"] == "completed"


def test_reading_an_old_completed_repull_reconciles_legacy_partial_run(tmp_path: Path) -> None:
    repository = DailySelectionRepository(tmp_path / "selection.sqlite3")
    detail_error = {"code": "timeout", "message": "timed out", "context": {}}
    repository.save_run(
        workspace_id="ws-1",
        run_id="run-legacy",
        status="partial",
        candidates=(
            DailySelectionCandidate(
                candidate_id="candidate-1",
                offer_id="offer-1",
                source_platform="1688",
                source_url="https://detail.1688.com/offer/offer-1.html",
                source_title="收纳盒",
                main_image_url=None,
                source_variant_records=(SourceVariantRecord(sku_id="sku-1"),),
            ),
        ),
        metadata={
            "errors": [detail_error],
            "detail_errors": {"offer-1": detail_error},
            "sku_repull": {
                "status": "completed",
                "round": 1,
                "total": 1,
                "done": 1,
                "succeeded": 1,
                "failed": 0,
            },
        },
    )
    runner = SkuRepullRunner(
        repository=repository,
        provider_config_resolver=lambda actor: {},
        provider_factory=lambda config: None,
    )
    actor = SimpleNamespace(workspace_id="ws-1")
    legacy = repository.get_run(workspace_id="ws-1", run_id="run-legacy")

    assert runner.state(actor=actor, run=legacy)["status"] == "completed"

    recovered = repository.get_run(workspace_id="ws-1", run_id="run-legacy")
    assert recovered.status == "completed"
    assert recovered.metadata["errors"] == []
    assert recovered.metadata["detail_errors"] == {}


def test_sku_repull_cancel_marks_round_interrupted(runner_factory) -> None:
    block = threading.Event()
    provider = FakeProvider(block=block)
    runner, _ = runner_factory(provider)
    actor = SimpleNamespace(workspace_id="ws-1")
    run = SimpleNamespace(run_id="run-3", metadata={})
    targets = [FakeCandidate(f"offer-{index}") for index in range(8)]

    runner.start(actor=actor, run=run, targets=targets, previous_round=0)
    _wait_until(lambda: provider.active >= 1, timeout=5.0)
    cancelled = runner.cancel(actor=actor, run=run)
    assert cancelled["status"] == "running"
    block.set()

    _wait_until(lambda: runner.state(actor=actor, run=run)["status"] == "cancelled")
    final = runner.state(actor=actor, run=run)
    assert final["status"] == "cancelled"
    assert final["done"] < final["total"]
    assert "已中断" in final["message"]
