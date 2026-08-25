from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import pytest
from PIL import Image

from wh_local.modules.pod_customization.contracts import (
    BatchCreate,
    BusinessFields,
    Calibration,
    ListingFields,
    NormalizedPoint,
    NormalizedRect,
)
from wh_local.modules.pod_customization.service import PodCustomizationService
from wh_local.session import Actor


class NeverCalledRuntime:
    def submit(self, *_args, **_kwargs):
        raise AssertionError("AI runtime must not run in persistence-only tests")


def _png(color: str = "#f5f2ea", size: tuple[int, int] = (320, 240)) -> bytes:
    output = io.BytesIO()
    Image.new("RGBA", size, color).save(output, "PNG")
    return output.getvalue()


def _service(tmp_path: Path) -> PodCustomizationService:
    return PodCustomizationService(
        tmp_path / "workbench.sqlite3",
        tmp_path / "pod-assets",
        NeverCalledRuntime(),
        start_workers=False,
    )


def _actor(user_id: str = "operator-1", workspace_id: str = "workspace-a") -> Actor:
    return Actor(id=user_id, username=user_id, role="operator", workspace_id=workspace_id)


def _listing_fields() -> ListingFields:
    return ListingFields(
        declared_price=18.5,
        suggested_price_usd=29.99,
        category_name="家居收纳 > 杯具",
        skus=[{"name": "Default SKU", "length_cm": 30, "width_cm": 20, "height_cm": 10, "weight_g": 450}],
    )


def test_template_upload_and_calibration_create_immutable_versioned_snapshot(tmp_path: Path) -> None:
    service = _service(tmp_path)
    actor = _actor()
    uploaded = service.upload_template(actor, name="Tote fixed scene", filename="scene.png", content=_png())

    assert uploaded["source"] == "personal"
    assert uploaded["calibration_status"] == "pending"
    assert uploaded["width"] == 320
    assert uploaded["height"] == 240

    ready = service.update_template_calibration(
        actor,
        uploaded["id"],
        Calibration(
            mask=NormalizedRect(x=0.25, y=0.2, width=0.5, height=0.6),
            anchor=NormalizedPoint(x=0.5, y=0.5),
        ),
    )

    snapshots = service.repository.list_template_snapshots(uploaded["id"], actor.workspace_id, actor.id)
    assert ready["calibration_status"] == "ready"
    assert [snapshot["version"] for snapshot in snapshots] == [1, 2]
    assert snapshots[0]["calibration_json"] == "null"
    assert '"width":0.5' in snapshots[1]["calibration_json"]


def test_batch_keeps_template_and_builtin_prompt_snapshot_after_template_changes(tmp_path: Path) -> None:
    service = _service(tmp_path)
    actor = _actor()
    template = service.upload_template(actor, name="Mug scene", filename="mug.png", content=_png())
    service.update_template_calibration(
        actor,
        template["id"],
        Calibration(
            mask=NormalizedRect(x=0.2, y=0.2, width=0.6, height=0.6),
            anchor=NormalizedPoint(x=0.5, y=0.5),
        ),
    )
    batch = service.create_batch(
        actor,
        BatchCreate(
            template_id=template["id"],
            count=20,
            prompt_version="v1",
            business_fields=BusinessFields(
                product_name="Stoneware mug",
                product_category="drinkware",
                target_market="US",
                excluded_elements=["logos"],
            ),
            creative_prompt="quiet coastal geometry",
            listing_fields=_listing_fields(),
        ),
        enqueue=False,
    )

    service.update_template_calibration(
        actor,
        template["id"],
        Calibration(
            mask=NormalizedRect(x=0.1, y=0.1, width=0.8, height=0.8),
            anchor=NormalizedPoint(x=0.5, y=0.5),
        ),
    )
    stored = service.get_batch(actor, batch["id"])

    assert stored["template_snapshot_id"] == batch["template_snapshot_id"]
    assert stored["template"]["calibration"]["mask"]["width"] == 0.6
    assert stored["prompt_version"] == "v1"
    assert "Stoneware mug" in stored["prompt_snapshot"]
    assert "quiet coastal geometry" in stored["prompt_snapshot"]
    assert stored["style_grid"] is True
    assert len(stored["items"]) == 80


def test_templates_are_workspace_shared_while_batches_remain_owner_private(tmp_path: Path) -> None:
    service = _service(tmp_path)
    owner = _actor()
    teammate = _actor(user_id="operator-2")
    outsider = _actor(user_id="operator-3", workspace_id="workspace-b")
    template = service.upload_template(owner, name="Shared template", filename="shared.png", content=_png())

    assert service.list_templates(owner)["templates"][0]["id"] == template["id"]
    assert service.list_templates(teammate)["templates"][0]["id"] == template["id"]
    assert service.list_templates(outsider)["templates"] == []


def test_calibration_has_a_deterministic_fallback_when_runtime_has_no_vision_calibrator(tmp_path: Path) -> None:
    service = _service(tmp_path)
    actor = _actor()
    template = service.upload_template(actor, name="Fallback scene", filename="scene.png", content=_png())

    calibrated = service.calibrate_template(actor, template["id"])

    assert calibrated["calibration_status"] == "ready"
    assert calibrated["calibration"] == {
        "mask": {"x": 0.2, "y": 0.2, "width": 0.6, "height": 0.6},
        "anchor": {"x": 0.5, "y": 0.5},
    }


def test_startup_recovery_requires_a_fresh_billing_grant(tmp_path: Path) -> None:
    service = _service(tmp_path)
    actor = _actor()
    template = service.upload_template(actor, name="Recovery scene", filename="scene.png", content=_png())
    service.update_template_calibration(
        actor,
        template["id"],
        Calibration(
            mask=NormalizedRect(x=0.2, y=0.2, width=0.6, height=0.6),
            anchor=NormalizedPoint(x=0.5, y=0.5),
        ),
    )
    batch = service.create_batch(
        actor,
        BatchCreate(
            template_id=template["id"],
            count=20,
            business_fields=BusinessFields(product_name="Bag", product_category="bags"),
            listing_fields=_listing_fields(),
        ),
        enqueue=False,
    )
    assert service.repository.claim_batch(batch["id"]) is True

    recovered = service.recover_interrupted_work()
    stored = service.get_batch(actor, batch["id"])

    assert recovered == 1
    assert stored["status"] == "billing_auth_required"
    assert stored["failed_count"] == 20
    assert "重启" in stored["error_message"]


def test_additive_pod_schema_does_not_delete_legacy_ai_service_pod_history(tmp_path: Path) -> None:
    database = tmp_path / "workbench.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE ai_service_pod_groups (group_id TEXT PRIMARY KEY, payload_json TEXT)")
        connection.execute("INSERT INTO ai_service_pod_groups VALUES ('legacy-group', '{\"kept\":true}')")

    _service(tmp_path)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT payload_json FROM ai_service_pod_groups WHERE group_id = 'legacy-group'").fetchone()[0] == '{"kept":true}'


def test_listing_snapshot_and_style_copy_are_persisted_with_historical_null_compatibility(tmp_path: Path) -> None:
    service = _service(tmp_path)
    actor = _actor()
    template = service.upload_template(actor, name="Mug scene", filename="mug.png", content=_png())
    service.update_template_calibration(
        actor,
        template["id"],
        Calibration(
            mask=NormalizedRect(x=0.2, y=0.2, width=0.6, height=0.6),
            anchor=NormalizedPoint(x=0.5, y=0.5),
        ),
    )
    batch = service.create_batch(
        actor,
        BatchCreate(
            template_id=template["id"],
            count=1,
            business_fields=BusinessFields(product_name="Mug", product_category="drinkware"),
            listing_fields=_listing_fields(),
        ),
        enqueue=False,
    )

    assert batch["listing_fields"] == _listing_fields().model_dump()
    service.repository.upsert_style_copy(
        batch["id"], actor.workspace_id, actor.id, 1,
        title="Coastal Mug", english_title="Coastal Stoneware Mug", description="A calm mug.",
    )
    assert service.repository.get_style_copies(batch["id"], actor.workspace_id, actor.id) == {
        1: {"title": "Coastal Mug", "english_title": "Coastal Stoneware Mug", "description": "A calm mug."}
    }

    with sqlite3.connect(service.database_path) as connection:
        connection.execute(
            "UPDATE pod_customization_batches SET listing_fields_json = 'null' WHERE batch_id = ?",
            (batch["id"],),
        )
    assert service.repository.get_batch(batch["id"], actor.workspace_id, actor.id)["listing_fields"] is None


def test_style_copy_repository_preserves_batch_ownership(tmp_path: Path) -> None:
    service = _service(tmp_path)
    actor = _actor()
    stranger = _actor(user_id="operator-2")
    template = service.upload_template(actor, name="Scene", filename="scene.png", content=_png())
    service.update_template_calibration(
        actor,
        template["id"],
        Calibration(
            mask=NormalizedRect(x=0.2, y=0.2, width=0.6, height=0.6),
            anchor=NormalizedPoint(x=0.5, y=0.5),
        ),
    )
    batch = service.create_batch(
        actor,
        BatchCreate(
            template_id=template["id"], count=1,
            business_fields=BusinessFields(product_name="Bag", product_category="bags"),
            listing_fields=_listing_fields(),
        ),
        enqueue=False,
    )

    with pytest.raises(Exception) as raised:
        service.repository.upsert_style_copy(
            batch["id"], stranger.workspace_id, stranger.id, 1,
            title="No", english_title="No", description="No",
        )
    assert getattr(raised.value, "status_code", None) == 404
