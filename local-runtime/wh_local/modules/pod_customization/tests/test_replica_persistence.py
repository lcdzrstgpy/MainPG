from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from wh_local.modules.pod_customization.contracts import (
    BatchCreate,
    BusinessFields,
    ListingFields,
    ReplicaBatchCreate,
    ReplicaTargetCreate,
    SemiBatchCreate,
)
from wh_local.modules.pod_customization.replica_context import (
    ReplicaStyleSnapshotMissingError,
    style_product_context,
)
from wh_local.modules.pod_customization.repository import PodRepositoryError
from wh_local.modules.pod_customization.service import PodCustomizationService
from wh_local.session import Actor


class NeverCalledRuntime:
    def submit(self, *_args, **_kwargs):
        raise AssertionError("AI runtime must not run in persistence-only tests")


def _png(color: str = "#f5f2ea", size: tuple[int, int] = (320, 240)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, "PNG")
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


def _listing_fields(category: str = "家居收纳 > 抱枕") -> ListingFields:
    return ListingFields(
        suggested_price_usd=19.99,
        category_name=category,
        skus=[{"name": "40cm", "declared_price": 6.0, "weight_g": 300.0}],
        spec_card={"cells": [["SKU", "Length", "Width"], ["40cm", "40", "40"]]},
    )


def _upload(service: PodCustomizationService, actor: Actor, role: str, color: str, filename: str) -> str:
    return service.upload_replica_image(actor, role=role, filename=filename, content=_png(color))["asset_id"]


def _target(asset_id: str, product_name: str, category: str = "家居收纳 > 抱枕") -> ReplicaTargetCreate:
    return ReplicaTargetCreate(
        target_asset_id=asset_id,
        product_name=product_name,
        listing_fields=_listing_fields(category),
    )


def _request(source_asset_id: str, targets: list[ReplicaTargetCreate], *, client_request_id: str) -> ReplicaBatchCreate:
    return ReplicaBatchCreate(
        client_request_id=client_request_id,
        source_asset_id=source_asset_id,
        targets=targets,
    )


def test_015_migration_builds_replica_tables(tmp_path: Path) -> None:
    service = _service(tmp_path)
    with service.repository._connect() as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "pod_customization_replica_batches" in tables
        assert "pod_customization_replica_targets" in tables
        batch_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(pod_customization_replica_batches)")
        }
        assert {
            "batch_id", "workspace_id", "owner_user_id", "source_asset_id",
            "client_request_id", "request_hash", "created_at",
        } <= batch_columns
        target_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(pod_customization_replica_targets)")
        }
        assert {"batch_id", "style_index", "asset_id", "business_fields_json", "listing_fields_json"} <= target_columns
        create_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='pod_customization_replica_batches'"
        ).fetchone()[0]
        assert "UNIQUE" in create_sql


def test_create_replica_batch_persists_full_shape(tmp_path: Path) -> None:
    service = _service(tmp_path)
    actor = _actor()
    source_id = _upload(service, actor, "source", "#dd0000", "source.png")
    target_a = _upload(service, actor, "target", "#00aa00", "cushion.png")
    target_b = _upload(service, actor, "target", "#0000cc", "basket.png")

    batch = service.create_replica_batch(
        actor,
        _request(
            source_id,
            [_target(target_a, "抱枕", "家居收纳 > 抱枕"), _target(target_b, "收纳篮", "家居收纳 > 收纳篮")],
            client_request_id="req-full-shape",
        ),
        enqueue=False,
    )

    assert batch["mode"] == "replica"
    assert batch["count"] == 2
    assert len(batch["items"]) == 8
    assert len(batch["style_titles"]) == 2
    assert batch["style_grid"] is True
    assert batch["source"]["asset_id"] == source_id
    assert [target["style_index"] for target in batch["targets"]] == [1, 2]
    assert batch["targets"][0]["product_name"] == "抱枕"
    assert batch["targets"][1]["product_name"] == "收纳篮"

    with service.repository._connect() as connection:
        batches = connection.execute("SELECT * FROM pod_customization_replica_batches").fetchall()
        assert len(batches) == 1
        assert batches[0]["source_asset_id"] == source_id
        assert batches[0]["client_request_id"] == "req-full-shape"
        assert batches[0]["request_hash"]
        targets = connection.execute(
            "SELECT * FROM pod_customization_replica_targets ORDER BY style_index"
        ).fetchall()
        assert [row["style_index"] for row in targets] == [1, 2]
        target_business = json.loads(targets[0]["business_fields_json"])
        assert target_business["product_name"] == "抱枕"
        assert target_business["product_category"] == "家居收纳 > 抱枕"
        # 复刻不生成原创设计的元素分配记录
        elements = connection.execute(
            "SELECT COUNT(*) FROM pod_customization_style_elements WHERE batch_id = ?", (batch["id"],)
        ).fetchone()[0]
        assert elements == 0
        # 内部锚点模板为隐藏状态（deleted_at 非空，不进用户模板库）
        template = connection.execute(
            "SELECT deleted_at FROM pod_customization_templates WHERE template_id = ?", (batch["template_id"],)
        ).fetchone()
        assert template is not None and template["deleted_at"] != ""
        # 父批次镜像首款用于结构兼容
        parent = connection.execute(
            "SELECT requested_count, mode FROM pod_customization_batches WHERE batch_id = ?", (batch["id"],)
        ).fetchone()
        assert parent["requested_count"] == 2
        assert parent["mode"] == "replica"


def test_targets_keep_input_order_and_derived_business_fields(tmp_path: Path) -> None:
    service = _service(tmp_path)
    actor = _actor()
    source_id = _upload(service, actor, "source", "#dd0000", "source.png")
    target_a = _upload(service, actor, "target", "#00aa00", "a.png")
    target_b = _upload(service, actor, "target", "#0000cc", "b.png")

    batch = service.create_replica_batch(
        actor,
        _request(
            source_id,
            [_target(target_a, "甲", "类目甲"), _target(target_b, "乙", "类目乙")],
            client_request_id="req-order",
        ),
        enqueue=False,
    )
    stored = service.repository.get_batch(batch["id"], actor.workspace_id, actor.id)
    assert [item["style_index"] for item in stored["replica_targets"]] == [1, 2]
    assert stored["replica_targets"][0]["business_fields"]["product_name"] == "甲"
    assert stored["replica_targets"][0]["business_fields"]["product_category"] == "类目甲"
    assert stored["replica_targets"][1]["business_fields"]["product_category"] == "类目乙"


def test_list_and_get_replica_batch_contract(tmp_path: Path) -> None:
    service = _service(tmp_path)
    actor = _actor()
    source_id = _upload(service, actor, "source", "#dd0000", "source.png")
    target_id = _upload(service, actor, "target", "#00aa00", "a.png")
    service.create_replica_batch(
        actor,
        _request(source_id, [_target(target_id, "抱枕")], client_request_id="req-list"),
        enqueue=False,
    )

    listed = service.list_replica_batches(actor)
    assert listed["total"] == 1
    assert listed["batches"][0]["mode"] == "replica"

    detail = service.get_replica_batch(actor, listed["batches"][0]["id"])
    assert detail["mode"] == "replica"
    assert detail["source"]["asset_id"] == source_id
    assert len(detail["targets"]) == 1
    assert detail["targets"][0]["style_index"] == 1
    context = style_product_context(
        service.repository.get_batch(detail["id"], actor.workspace_id, actor.id), 1
    )
    assert context["business_fields"]["product_name"] == "抱枕"


def test_role_mismatch_and_cross_account_assets_rejected(tmp_path: Path) -> None:
    service = _service(tmp_path)
    actor = _actor()
    source_id = _upload(service, actor, "source", "#dd0000", "source.png")
    target_id = _upload(service, actor, "target", "#00aa00", "a.png")

    # 角色错误：把目标图当样图
    with pytest.raises(ValueError, match="样图资产角色错误"):
        service.create_replica_batch(
            actor,
            _request(target_id, [_target(target_id, "抱枕")], client_request_id="req-role-src"),
            enqueue=False,
        )
    # 角色错误：把样图当目标图
    with pytest.raises(ValueError, match="目标产品资产角色错误"):
        service.create_replica_batch(
            actor,
            _request(source_id, [_target(source_id, "抱枕")], client_request_id="req-role-tgt"),
            enqueue=False,
        )
    # 跨账号资产（同工作区另一用户）：拒绝且不扣费
    outsider = _actor(user_id="operator-2")
    with pytest.raises(PodRepositoryError) as exc_info:
        service.create_replica_batch(
            outsider,
            _request(source_id, [_target(target_id, "抱枕")], client_request_id="req-cross"),
            enqueue=False,
        )
    assert exc_info.value.status_code == 404


def test_client_request_id_idempotency_and_conflict(tmp_path: Path) -> None:
    service = _service(tmp_path)
    actor = _actor()
    source_id = _upload(service, actor, "source", "#dd0000", "source.png")
    target_id = _upload(service, actor, "target", "#00aa00", "a.png")

    request = _request(source_id, [_target(target_id, "抱枕")], client_request_id="req-idem")
    first = service.create_replica_batch(actor, request, enqueue=False)
    second = service.create_replica_batch(actor, request, enqueue=False)
    assert first["id"] == second["id"]

    with service.repository._connect() as connection:
        total = connection.execute("SELECT COUNT(*) FROM pod_customization_replica_batches").fetchone()[0]
        assert total == 1

    # 同 ID 不同请求 → 409
    conflicting = _request(
        source_id, [_target(target_id, "换了名字")], client_request_id="req-idem"
    )
    with pytest.raises(PodRepositoryError) as exc_info:
        service.create_replica_batch(actor, conflicting, enqueue=False)
    assert exc_info.value.status_code == 409


def test_delete_batch_cleans_replica_references_and_assets(tmp_path: Path) -> None:
    service = _service(tmp_path)
    actor = _actor()
    source_id = _upload(service, actor, "source", "#dd0000", "source.png")
    target_id = _upload(service, actor, "target", "#00aa00", "a.png")
    batch = service.create_replica_batch(
        actor,
        _request(source_id, [_target(target_id, "抱枕")], client_request_id="req-delete"),
        enqueue=False,
    )
    template_id = batch["template_id"]
    with service.repository._connect() as connection:
        connection.execute("UPDATE pod_customization_batches SET status = 'completed' WHERE batch_id = ?", (batch["id"],))

    service.delete_batch(actor, batch["id"])

    with service.repository._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM pod_customization_replica_batches WHERE batch_id = ?", (batch["id"],)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM pod_customization_replica_targets WHERE batch_id = ?", (batch["id"],)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM pod_customization_templates WHERE template_id = ?", (template_id,)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM pod_customization_template_snapshots WHERE template_id = ?", (template_id,)
        ).fetchone()[0] == 0
        remaining_assets = connection.execute(
            "SELECT COUNT(*) FROM pod_customization_assets WHERE asset_id IN (?, ?)", (source_id, target_id)
        ).fetchone()[0]
        assert remaining_assets == 0


def test_create_rolls_back_on_missing_target_asset(tmp_path: Path) -> None:
    service = _service(tmp_path)
    actor = _actor()
    source_id = _upload(service, actor, "source", "#dd0000", "source.png")
    request = _request(source_id, [_target("missing-asset", "抱枕")], client_request_id="req-rollback")

    with pytest.raises(PodRepositoryError):
        service.repository.create_replica_batch(
            actor.workspace_id, actor.id, request, request_hash="h", batch_id="rollback-batch"
        )

    with service.repository._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM pod_customization_batches WHERE batch_id = 'rollback-batch'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM pod_customization_replica_batches WHERE batch_id = 'rollback-batch'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM pod_customization_replica_targets WHERE batch_id = 'rollback-batch'"
        ).fetchone()[0] == 0


def test_full_and_semi_payloads_keep_backward_compatible_mode(tmp_path: Path) -> None:
    service = _service(tmp_path)
    actor = _actor()

    semi = service.create_semi_batch(actor, SemiBatchCreate(count=4), enqueue=False)
    assert semi["mode"] == "semi"
    assert semi["listing_fields"] is None
    assert "targets" not in semi

    template = service.upload_template(actor, name="T", filename="t.png", content=_png())
    full = service.create_batch(
        actor,
        BatchCreate(
            template_id=template["id"],
            count=1,
            business_fields=BusinessFields(product_name="帆布袋", product_category="包袋"),
            listing_fields=_listing_fields("包袋"),
        ),
        enqueue=False,
    )
    assert full["mode"] == "full"
    assert "targets" not in full
    assert "source" not in full

    # 通用 list_batches 只列 full，复刻批次不会被混入全定制历史。
    source_id = _upload(service, actor, "source", "#dd0000", "source.png")
    target_id = _upload(service, actor, "target", "#00aa00", "a.png")
    service.create_replica_batch(
        actor,
        _request(source_id, [_target(target_id, "抱枕")], client_request_id="req-mode-filter"),
        enqueue=False,
    )
    assert service.list_batches(actor)["total"] == 1
    assert service.list_replica_batches(actor)["total"] == 1


def test_replica_spec_card_reprint_requires_style_index(tmp_path: Path) -> None:
    service = _service(tmp_path)
    actor = _actor()
    source_id = _upload(service, actor, "source", "#dd0000", "source.png")
    target_id = _upload(service, actor, "target", "#00aa00", "a.png")
    batch = service.create_replica_batch(
        actor,
        _request(source_id, [_target(target_id, "抱枕")], client_request_id="req-reprint"),
        enqueue=False,
    )
    with service.repository._connect() as connection:
        connection.execute("UPDATE pod_customization_batches SET status = 'completed' WHERE batch_id = ?", (batch["id"],))

    with pytest.raises(PodRepositoryError) as exc_info:
        service.reprint_batch_spec_card(
            actor,
            batch["id"],
            {"cells": [["SKU", "Length"], ["40cm", "40"]], "style": "light", "corner": "bottom-right"},
            style_index=None,
        )
    assert exc_info.value.status_code == 422