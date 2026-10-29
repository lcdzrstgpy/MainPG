"""复刻图案迁移方案表：唯一键、幂等写入、跨用户隔离。"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from wh_local.modules.pod_customization.repository import PodRepositoryError
from wh_local.modules.pod_customization.service import PodCustomizationService
from wh_local.session import Actor

_PROMPT_VERSION = "pod-replica-main-prompt-v1"
_MODEL = "doubao-seed-1-6-vision"
_ORDER = "B,A"


class NeverCalledRuntime:
    def submit(self, *_args, **_kwargs):
        raise AssertionError("AI runtime must not run in repository-only tests")


def _png(color: str = "#f5f2ea") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(output, "PNG")
    return output.getvalue()


def _service(tmp_path: Path) -> PodCustomizationService:
    return PodCustomizationService(
        tmp_path / "workbench.sqlite3",
        tmp_path / "pod-assets",
        NeverCalledRuntime(),
        start_workers=False,
    )


def _actor(user_id: str = "operator-1") -> Actor:
    return Actor(id=user_id, username=user_id, role="operator", workspace_id="workspace-a")


def _assets(service: PodCustomizationService) -> tuple[str, str]:
    actor = _actor()
    source_id = service.upload_replica_image(
        actor, role="source", filename="source.png", content=_png("#cc1111")
    )["asset_id"]
    target_id = service.upload_replica_image(
        actor, role="target", filename="target.png", content=_png("#11aa33")
    )["asset_id"]
    return source_id, target_id


def _write(
    service: PodCustomizationService,
    source_id: str,
    target_id: str,
    *,
    plan_id: str,
    main_prompt: str,
    reference_order: str = _ORDER,
    owner_user_id: str = "operator-1",
) -> None:
    service.repository.create_replica_transfer_plan(
        plan_id=plan_id,
        workspace_id="workspace-a",
        owner_user_id=owner_user_id,
        source_asset_id=source_id,
        target_asset_id=target_id,
        prompt_version=_PROMPT_VERSION,
        model=_MODEL,
        reference_order=reference_order,
        main_prompt=main_prompt,
    )


def _find(
    service: PodCustomizationService,
    source_id: str,
    target_id: str,
    *,
    reference_order: str = _ORDER,
    owner_user_id: str = "operator-1",
) -> dict | None:
    return service.repository.find_replica_transfer_plan(
        "workspace-a", owner_user_id, source_id, target_id, _PROMPT_VERSION, _MODEL, reference_order
    )


def test_transfer_plan_roundtrip(tmp_path: Path) -> None:
    service = _service(tmp_path)
    source_id, target_id = _assets(service)

    _write(service, source_id, target_id, plan_id="plan-1", main_prompt="Main prompt body")

    found = _find(service, source_id, target_id)
    assert found is not None
    assert found["plan_id"] == "plan-1"
    assert found["main_prompt"] == "Main prompt body"
    assert found["reference_order"] == "B,A"
    assert service.repository.get_replica_transfer_plan("plan-1", "workspace-a", "operator-1")


def test_same_key_is_idempotent_and_keeps_first_plan_id(tmp_path: Path) -> None:
    service = _service(tmp_path)
    source_id, target_id = _assets(service)

    _write(service, source_id, target_id, plan_id="plan-1", main_prompt="first")
    _write(service, source_id, target_id, plan_id="plan-2", main_prompt="second")

    found = _find(service, source_id, target_id)
    assert found is not None
    assert found["plan_id"] == "plan-1"
    assert found["main_prompt"] == "second"
    with service.repository._connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS n FROM pod_customization_replica_transfer_plans"
        ).fetchone()["n"]
    assert count == 1


def test_reference_order_is_part_of_the_key(tmp_path: Path) -> None:
    service = _service(tmp_path)
    source_id, target_id = _assets(service)

    _write(service, source_id, target_id, plan_id="plan-1", main_prompt="main")

    assert _find(service, source_id, target_id, reference_order="A,B") is None


def test_plan_is_never_reused_across_users(tmp_path: Path) -> None:
    service = _service(tmp_path)
    source_id, target_id = _assets(service)

    _write(service, source_id, target_id, plan_id="plan-1", main_prompt="main")

    assert _find(service, source_id, target_id, owner_user_id="someone-else") is None
    with pytest.raises(PodRepositoryError):
        service.repository.get_replica_transfer_plan("plan-1", "workspace-a", "someone-else")


def test_empty_main_prompt_is_rejected(tmp_path: Path) -> None:
    service = _service(tmp_path)
    source_id, target_id = _assets(service)

    with pytest.raises(PodRepositoryError):
        _write(service, source_id, target_id, plan_id="plan-1", main_prompt="   ")
