#!/usr/bin/env python3
"""真实 ClipForge sidecar 验收：发布产物 -> 启动 -> 健康 -> 停止 -> 重启 -> 产物未变。

用法::

    python devtools/verify_clipforge_sidecar.py --app-root <artifact-root> [--node <node-binary>]

验收内容（任一不满足即以非零码退出）：

1. 记录 artifact 全树 SHA-256；
2. 用临时数据目录启动 ``ClipForgeService``，并确认立刻返回 ``starting``；
3. 在 35 秒内轮询到 ``ready``；
4. 校验 ``/api/health``（instanceId 匹配）与 ``/start?embed=mainpg``（非空 HTML）；
5. ``stop()`` 后子进程必须消失，且没有残留的 Node sidecar；
6. 第二次启动必须拿到不同的 ``instanceId``；
7. 再次 ``stop()`` 后 artifact 全树哈希必须与开始时一致。

它刻意不复用 ``.next/standalone``：被测对象必须是经过发布校验的部署根
（``<publish-root>/artifacts/<artifact-id>``），入口恒为 ``<app-root>/server.js``。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wh_local.modules.clipforge.artifact import ClipForgeBuild  # noqa: E402
from wh_local.modules.clipforge.service import ClipForgeService, ClipForgeStatus  # noqa: E402

READY_TIMEOUT_S = 35.0
STOP_TIMEOUT_S = 5.0
METADATA_FILE = "mainpg-sidecar.json"


# --- 纯辅助函数（单元测试直接调用）-----------------------------------------


def tree_digest(root: Path) -> str:
    """Sorted SHA-256 over every file's relative path + bytes.

    Unlike the publish-time digest this includes ``mainpg-sidecar.json``: the
    published artifact is frozen, so nothing may change while it runs.
    """
    base = Path(root).resolve()
    digest = hashlib.sha256()
    for relative_path in _list_files(base):
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update((base / relative_path).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _list_files(base: Path) -> list[str]:
    found: list[str] = []

    def visit(directory: Path) -> None:
        for entry in os.scandir(directory):
            path = Path(entry.path)
            if entry.is_dir(follow_symlinks=True):
                visit(path)
            elif entry.is_file(follow_symlinks=True):
                found.append(path.relative_to(base).as_posix())

    visit(base)
    return sorted(found)


def assert_status_ready(payload: dict, instance_id: str) -> None:
    """Acceptance rule for a status snapshot: ready, same instance, loopback url."""
    assert payload.get("state") == "ready", f"expected state=ready, got {payload.get('state')!r}"
    assert payload.get("instanceId"), "ready status must carry an instanceId"
    assert payload.get("instanceId") == instance_id, (
        f"instance id mismatch: expected {instance_id!r}, got {payload.get('instanceId')!r}"
    )
    assert payload.get("url"), "ready status must carry a url"


def _status_payload(status: ClipForgeStatus) -> dict:
    return {"state": status.state, "instanceId": status.instance_id, "url": status.url}


# --- 验收流程 ---------------------------------------------------------------


def _get(url: str, timeout: float = 3.0) -> tuple[int, bytes, str]:
    try:
        with urlopen(url, timeout=timeout) as response:  # noqa: S310 - loopback URL built here
            return int(response.status), response.read(), str(response.headers.get("content-type", ""))
    except HTTPError as error:
        return int(error.code), b"", ""


def _wait_until_ready(service: ClipForgeService) -> ClipForgeStatus:
    deadline = time.monotonic() + READY_TIMEOUT_S
    while time.monotonic() < deadline:
        status = service.status()
        if status.state == "ready":
            return status
        if status.state == "failed":
            code = status.error.code if status.error else "unknown"
            diagnostic = status.error.diagnostic_id if status.error else None
            raise AssertionError(f"sidecar failed to start: {code} (diagnostic {diagnostic})")
        time.sleep(0.1)
    raise AssertionError(f"sidecar did not become ready within {READY_TIMEOUT_S:.0f}s")


def _assert_http_contract(base_url: str, instance_id: str) -> None:
    http_status, body, _ = _get(f"{base_url}/api/health")
    assert http_status == 200, f"/api/health returned HTTP {http_status}"
    payload = json.loads(body.decode("utf-8"))
    assert payload.get("service") == "clipforge", "health service mismatch"
    assert payload.get("instanceId") == instance_id, "health instance mismatch"
    assert payload.get("status") == "ok", "health checks failed"

    http_status, body, content_type = _get(f"{base_url}/start?embed=mainpg")
    assert http_status == 200, f"/start returned HTTP {http_status}"
    assert "text/html" in content_type.lower(), f"/start is not HTML ({content_type!r})"
    assert body.strip(), "/start returned an empty body"


def _remaining_process_group(pgid: int) -> list[str]:
    """Processes still alive in the child's process group = leaked sidecar descendants.

    Matching ``ps`` on the ``server.js`` path is not enough: Next's ``server.js``
    rewrites its process title to ``next-server (v<version>)``, so a leaked child
    would not match a path filter. The process group id is inherited by every
    descendant (Node and FFmpeg) and is not rewritten, so the sidecar is started
    in its own session and the whole group is checked instead.
    """
    if os.name == "nt" or pgid <= 0:
        return []
    try:
        result = subprocess.run(["ps", "-eo", "pid=,pgid=,command="], capture_output=True, text=True, check=False)
    except OSError:
        return []
    leaked = []
    for line in result.stdout.splitlines():
        fields = line.split(None, 2)
        if len(fields) < 2:
            continue
        try:
            if int(fields[1]) == pgid:
                leaked.append(line.strip())
        except ValueError:
            continue
    return leaked


def _assert_stopped(service: ClipForgeService, process: subprocess.Popen | None, pgid: int) -> None:
    status = service.status()
    assert status.state == "stopped", f"expected stopped after stop(), got {status.state!r}"
    assert status.url is None, "stopped sidecar must not expose a url"

    if process is not None:
        deadline = time.monotonic() + STOP_TIMEOUT_S
        while time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.05)
        assert process.poll() is not None, "sidecar child process survived stop()"

    leaked = _remaining_process_group(pgid)
    assert not leaked, f"leftover sidecar processes in process group {pgid}: {leaked}"


def _resolve_node_binary(app_root: Path, explicit: str | None) -> str:
    if explicit:
        return explicit
    for name in ("node.exe", "node"):
        bundled = app_root.parent / name
        if bundled.is_file():
            return str(bundled)
    return shutil.which("node") or "node"


def _process_group_of(process: subprocess.Popen | None) -> int:
    """Group id of a spawned sidecar; ``start_new_session=True`` makes it the child's own pid."""
    if process is None:
        return 0
    if os.name == "nt":
        return 0
    try:
        return int(os.getpgid(process.pid))
    except OSError:
        return int(process.pid)


def verify(app_root: Path, node_binary: str | None = None) -> str:
    app_root = Path(app_root).resolve()
    metadata = json.loads((app_root / METADATA_FILE).read_text(encoding="utf-8"))
    artifact_id = metadata["artifactId"]
    binary = _resolve_node_binary(app_root, node_binary)
    digest_before = tree_digest(app_root)

    spawned: list[subprocess.Popen] = []

    def process_factory(args, **kwargs) -> Any:
        process = subprocess.Popen(args, **kwargs)
        spawned.append(process)
        return process

    with tempfile.TemporaryDirectory(prefix="clipforge-verify-") as temporary:
        data_root = Path(temporary) / "clipforge"
        build = ClipForgeBuild("available", app_root, artifact_id, "verification build")
        service = ClipForgeService(
            build_resolver=lambda: build,
            data_root=data_root,
            node_binary=binary,
            process_factory=process_factory,
        )

        first = service.start()
        assert first.state == "starting", f"start() must return starting immediately, got {first.state!r}"
        assert first.instance_id, "starting status must carry an instanceId"
        first_ready = _wait_until_ready(service)
        assert first_ready.url is not None
        assert_status_ready(_status_payload(first_ready), first.instance_id)
        _assert_http_contract(first_ready.url, first.instance_id)

        first_process = spawned[-1] if spawned else None
        assert first_process is not None, "sidecar child process was never spawned"
        first_pgid = _process_group_of(first_process)
        # 自成进程组是"能回收整棵进程树"的前提：pgid 必须等于子进程自身 pid。
        if os.name != "nt":
            assert first_pgid == first_process.pid, "sidecar must lead its own process group"

        service.stop()
        _assert_stopped(service, first_process, first_pgid)

        second = service.start()
        assert second.instance_id, "restart must carry an instanceId"
        assert second.instance_id != first.instance_id, "restart must create a new instanceId"
        second_ready = _wait_until_ready(service)
        assert second_ready.url is not None
        assert_status_ready(_status_payload(second_ready), second.instance_id)
        _assert_http_contract(second_ready.url, second.instance_id)

        second_process = spawned[-1] if spawned else None
        assert second_process is not None, "restarted sidecar child process was never spawned"
        second_pgid = _process_group_of(second_process)
        if os.name != "nt":
            assert second_pgid == second_process.pid, "restarted sidecar must lead its own process group"

        service.stop()
        _assert_stopped(service, second_process, second_pgid)

    digest_after = tree_digest(app_root)
    assert digest_after == digest_before, "artifact tree changed while the sidecar was running"

    return (
        f"PASS clipforge-sidecar artifact={artifact_id} node={binary} "
        f"instance1={first.instance_id} instance2={second.instance_id} digest={digest_before[:12]}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--app-root", required=True, help="published artifact root (contains server.js)")
    parser.add_argument("--node", default=None, help="node binary to use (default: bundled or PATH node)")
    args = parser.parse_args(argv)

    try:
        line = verify(Path(args.app_root), args.node)
    except Exception as error:  # noqa: BLE001 - CLI 汇总所有失败
        print(f"FAIL clipforge-sidecar: {error}", file=sys.stderr)
        return 1
    print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
