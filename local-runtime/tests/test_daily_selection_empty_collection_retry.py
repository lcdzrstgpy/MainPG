"""每日采集空批次自动重试逻辑测试。

验证 EmptyCollectionRetryRunner：
- 0 候选时自动启动后台重采，采到即原位替换该批次
- 所有轮次为空则标记失败并持久化
- 有候选 / 已重试过 / 重试关闭时不启动
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from wh_local.data_collection import empty_collection as module


class FakeRepository:
    def __init__(self, run: SimpleNamespace) -> None:
        self.run = run
        self.replaced: list[dict] = []
        self.metadata_updates: list[dict] = []

    def get_run(self, *, workspace_id: str, run_id: str) -> SimpleNamespace:
        return self.run

    def replace_run_collection(
        self, *, workspace_id: str, run_id: str, status: str, candidates: tuple, metadata: dict
    ) -> None:
        self.replaced.append({"status": status, "count": len(candidates), "metadata": metadata})
        self.run = SimpleNamespace(
            run_id=self.run.run_id,
            workspace_id=self.run.workspace_id,
            status=status,
            candidate_count=len(candidates),
            candidates=tuple(candidates),
            criteria=self.run.criteria,
            metadata=metadata,
        )

    def update_run_metadata(self, *, workspace_id: str, run_id: str, metadata: dict) -> None:
        self.metadata_updates.append(metadata)
        self.run = SimpleNamespace(**{**vars(self.run), "metadata": metadata})


def _make_run(candidates: tuple = ()) -> SimpleNamespace:
    return SimpleNamespace(
        run_id="run-1",
        workspace_id="ws-1",
        status="completed" if candidates else "empty",
        candidate_count=len(candidates),
        candidates=candidates,
        criteria={"keywords": ("测试",)},
        metadata={},
    )


def _collect_result(candidates: tuple) -> SimpleNamespace:
    return SimpleNamespace(
        status="completed" if candidates else "empty",
        candidates=[SimpleNamespace(candidate=item) for item in candidates],
    )


def _make_runner(
    repository: FakeRepository,
    collect_results: list[SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
    *,
    rounds: int | None = 2,
    block_event: threading.Event | None = None,
    on_recovered: object | None = None,
) -> module.EmptyCollectionRetryRunner:
    calls: list[str] = []

    class FakeCollector:
        def __init__(self, **kwargs: object) -> None:
            pass

        def collect(self, criteria: object) -> SimpleNamespace:
            calls.append("collect")
            result = collect_results.pop(0) if collect_results else _collect_result(())
            if block_event is not None:
                block_event.wait(timeout=10)
            return result

    monkeypatch.setattr(module, "DailySelectionCollector", FakeCollector)
    monkeypatch.setattr(
        module,
        "filter_and_score_candidates",
        lambda candidates, criteria: SimpleNamespace(
            candidates=list(candidates), filtered=()
        ),
    )
    monkeypatch.setattr(module, "_RETRY_DELAY_SECONDS", 0)
    if rounds is not None:
        monkeypatch.setattr(module, "_retry_rounds", lambda: rounds)
    runner = module.EmptyCollectionRetryRunner(
        repository=repository,
        budget=object(),
        provider_config_resolver=lambda actor: {"api_key": "x"},
        provider_factory=lambda config: object(),
        on_recovered=on_recovered,
    )
    runner._calls = calls  # type: ignore[attr-defined]
    return runner


def _wait_until(predicate: object, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("wait_until timed out")


def test_retry_recovers_when_later_round_has_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _make_run()
    repository = FakeRepository(run)
    gate = threading.Event()
    runner = _make_runner(
        repository,
        [_collect_result(()), _collect_result(("c1", "c2"))],
        monkeypatch,
        block_event=gate,
    )
    actor = SimpleNamespace(workspace_id="ws-1", actor_id="ws-1")

    state = runner.maybe_start(actor=actor, run=run)
    # 等首次 collect 进入阻塞，确认启动时处于 running 再放行
    _wait_until(lambda: len(runner._calls) >= 1)  # type: ignore[attr-defined]
    assert state["status"] == "running"
    gate.set()

    _wait_until(lambda: runner.state(actor=actor, run=run)["status"] == "completed")
    final = runner.state(actor=actor, run=run)
    assert final["status"] == "completed"
    assert final["round"] == 2
    assert repository.replaced and repository.replaced[0]["count"] == 2
    assert repository.replaced[0]["metadata"]["collection_retry"]["status"] == "completed"
    assert runner._calls == ["collect", "collect"]  # type: ignore[attr-defined]


def test_retry_success_restarts_sku_repull_for_recovered_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _make_run()
    repository = FakeRepository(run)
    recovered: list[tuple[str, str]] = []
    runner = _make_runner(
        repository,
        [_collect_result(("c1",))],
        monkeypatch,
        on_recovered=lambda actor, run_id: recovered.append((actor.workspace_id, run_id)),
    )
    actor = SimpleNamespace(workspace_id="ws-1", actor_id="actor-1")

    runner.maybe_start(actor=actor, run=run)

    _wait_until(lambda: runner.state(actor=actor, run=run)["status"] == "completed")
    assert recovered == [("ws-1", "run-1")]


def test_retry_marks_failed_when_all_rounds_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _make_run()
    repository = FakeRepository(run)
    runner = _make_runner(
        repository,
        [_collect_result(()), _collect_result(())],
        monkeypatch,
    )
    actor = SimpleNamespace(workspace_id="ws-1", actor_id="ws-1")

    runner.maybe_start(actor=actor, run=run)
    _wait_until(lambda: runner.state(actor=actor, run=run)["status"] == "failed")
    final = runner.state(actor=actor, run=run)
    assert final["status"] == "failed"
    assert "仍未采集到商品" in final["message"]
    assert repository.metadata_updates[-1]["collection_retry"]["status"] == "failed"
    assert repository.replaced == []


def test_no_retry_when_run_has_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _make_run(("c1",))
    repository = FakeRepository(run)
    runner = _make_runner(repository, [], monkeypatch)
    actor = SimpleNamespace(workspace_id="ws-1", actor_id="ws-1")

    state = runner.maybe_start(actor=actor, run=run)
    assert state["status"] == "idle"
    assert runner._calls == []  # type: ignore[attr-defined]


def test_no_retry_when_rounds_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _make_run()
    repository = FakeRepository(run)
    runner = _make_runner(repository, [], monkeypatch, rounds=0)
    actor = SimpleNamespace(workspace_id="ws-1", actor_id="ws-1")

    state = runner.maybe_start(actor=actor, run=run)
    assert state["status"] == "idle"
    assert runner._calls == []  # type: ignore[attr-defined]


def test_no_duplicate_round_when_already_running(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _make_run()
    repository = FakeRepository(run)
    gate = threading.Event()
    runner = _make_runner(
        repository, [_collect_result(("c1",))], monkeypatch, block_event=gate
    )
    actor = SimpleNamespace(workspace_id="ws-1", actor_id="ws-1")

    runner.maybe_start(actor=actor, run=run)
    # 等首次 collect 已进入阻塞，确保 job 仍处于 running
    _wait_until(lambda: len(runner._calls) >= 1)  # type: ignore[attr-defined]
    # 第二次调用：不应再启动新线程，直接观察现有 job
    state2 = runner.maybe_start(actor=actor, run=run)
    assert state2["status"] == "running"
    gate.set()
    _wait_until(lambda: runner.state(actor=actor, run=run)["status"] == "completed")
    assert runner._calls == ["collect"]  # type: ignore[attr-defined]
