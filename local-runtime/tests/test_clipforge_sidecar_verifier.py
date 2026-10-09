from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from devtools.verify_clipforge_sidecar import (
    _remaining_process_group,
    assert_status_ready,
    tree_digest,
)


def test_tree_digest_changes_when_artifact_is_mutated(tmp_path: Path) -> None:
    root = tmp_path / "artifact"
    root.mkdir()
    (root / "server.js").write_text("a", encoding="utf-8")
    before = tree_digest(root)

    (root / "server.js").write_text("b", encoding="utf-8")

    assert tree_digest(root) != before


def test_tree_digest_is_order_independent(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    for root, names in ((first, ("a.js", "b.js")), (second, ("b.js", "a.js"))):
        (root / "nested").mkdir(parents=True)
        for name in names:
            (root / name).write_text(name, encoding="utf-8")
            (root / "nested" / name).write_text(name, encoding="utf-8")

    assert tree_digest(first) == tree_digest(second)


def test_acceptance_requires_ready_and_matching_instance() -> None:
    assert_status_ready({"state": "ready", "instanceId": "a", "url": "http://127.0.0.1:1"}, "a")

    with pytest.raises(AssertionError):
        assert_status_ready({"state": "ready", "instanceId": "old", "url": "http://127.0.0.1:1"}, "new")


@pytest.mark.parametrize(
    "payload",
    [
        {"state": "starting", "instanceId": "a", "url": None},
        {"state": "failed", "instanceId": "a", "url": "http://127.0.0.1:1"},
        {"state": "ready", "instanceId": None, "url": "http://127.0.0.1:1"},
        {"state": "ready", "instanceId": "a", "url": None},
    ],
)
def test_acceptance_rejects_every_non_ready_snapshot(payload: dict) -> None:
    with pytest.raises(AssertionError):
        assert_status_ready(payload, "a")


@pytest.mark.skipif(os.name == "nt", reason="process-group scan is POSIX only")
def test_process_group_scan_detects_and_clears_leaked_children() -> None:
    # 进程组检测必须能发现真实子进程：Next 的 server.js 会改写进程标题，
    # 因此按路径匹配 ps 会漏检（假阴性），只有 pgid 能覆盖整棵进程树。
    process = subprocess.Popen(["/bin/sleep", "30"], start_new_session=True)
    try:
        leaked = _remaining_process_group(process.pid)
        assert any(str(process.pid) == line.split()[0] for line in leaked)
    finally:
        process.kill()
        process.wait()

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and _remaining_process_group(process.pid):
        time.sleep(0.05)
    assert _remaining_process_group(process.pid) == []

