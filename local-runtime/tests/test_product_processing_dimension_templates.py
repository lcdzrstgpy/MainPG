from __future__ import annotations

import json

from sqlalchemy import select

from wh_local.modules.product_processing.domain.dimension_templates import category_identity
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.dimension_template_orm import (
    DimensionObservationRow,
    DimensionTemplateRefreshRow,
    DimensionTemplateRow,
)
from wh_local.modules.product_processing.infrastructure.dimension_template_repository import (
    DimensionTemplateRepository,
)
from wh_local.modules.product_processing.service import ProductProcessingService
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository


def test_category_identity_prefers_platform_category_id_and_has_profile_fallback() -> None:
    learned, profile, candidates = category_identity(
        {
            "source_platform": "1688",
            "category_id": " 12345 ",
            "category_path": "服装 > 女装 > 衬衫",
        },
        "女士衬衫",
    )

    assert learned == "1688:id:12345"
    assert profile == "flat_soft_item"
    assert candidates[:3] == (
        "1688:id:12345",
        "path:服装/女装/衬衫",
        "path:服装/女装",
    )
    assert candidates[-2:] == ("profile:flat_soft_item", "fallback")


def test_observation_is_idempotent_and_manual_correction_updates_same_product_sample() -> None:
    database = create_database("sqlite:///:memory:")
    repository = DimensionTemplateRepository(database)
    raw = {"source_platform": "1688", "category_id": "99", "category_path": "配件 > 数据线"}

    assert repository.record_observation(
        workspace_id="shop-a",
        observation_key="package:7:sku-red",
        raw=raw,
        title="USB 数据线",
        values={"weight_g": 100},
        provenance={"weight_g": "source_confirmed"},
        source_kind="source_confirmed",
        product_draft_id=7,
        variant_key="sku-red",
    )
    assert not repository.record_observation(
        workspace_id="shop-a",
        observation_key="package:7:sku-red",
        raw=raw,
        title="USB 数据线",
        values={"weight_g": 100},
        provenance={"weight_g": "source_confirmed"},
        source_kind="source_confirmed",
        product_draft_id=7,
        variant_key="sku-red",
    )
    assert repository.record_observation(
        workspace_id="shop-a",
        observation_key="package:7:sku-red",
        raw=raw,
        title="USB 数据线",
        values={"weight_g": 120},
        provenance={"weight_g": "manual_confirmed"},
        source_kind="manual_confirmed",
        product_draft_id=7,
        variant_key="sku-red",
    )
    assert repository.refresh_pending(force=True) == 1

    with database.sessions() as session:
        observations = session.scalars(select(DimensionObservationRow)).all()
        learned = session.scalar(
            select(DimensionTemplateRow).where(
                DimensionTemplateRow.workspace_id == "shop-a",
                DimensionTemplateRow.category_key == "1688:id:99",
            )
        )
        assert len(observations) == 1
        assert observations[0].weight_g == 120
        assert json.loads(observations[0].provenance_json)["weight_g"] == "manual_confirmed"
        assert learned is not None
        assert learned.sample_count == 1
        assert learned.wgt_sample_count == 1
        assert learned.manual_confirmed_n == 1


def test_combined_dimensions_clamps_estimates_but_never_source_measurements() -> None:
    database = create_database("sqlite:///:memory:")
    template = DimensionTemplateRepository(database).resolve(
        {"category_path": "配件 > 数据线"}, "USB 数据线", workspace_id="local"
    )
    assert template is not None

    estimated = ProductProcessingService._combined_dimensions(
        {"length_cm": 999, "weight_g": 0}, {}, template
    )
    assert estimated["length_cm"] == template["known_len_max"]
    assert estimated["width_cm"] == template["known_wid_default"]
    assert estimated["weight_g"] == template["known_wgt_default"]
    assert estimated["field_provenance"]["length_cm"] == "package_estimate"
    assert "length_cm" in estimated["clamped_fields"]
    assert estimated["raw_estimate"]["length_cm"] == 999
    assert estimated["resolution_method"]["length_cm"] == "ai_clamped"
    assert estimated["resolution_method"]["weight_g"] == "known_default"
    assert estimated["confidence"] == "low"
    recombined = ProductProcessingService._combined_dimensions(estimated, {}, template)
    assert recombined["raw_estimate"]["length_cm"] == 999
    assert recombined["resolution_method"]["length_cm"] == "ai_clamped"
    assert recombined["resolution_method"]["weight_g"] == "known_default"

    source = ProductProcessingService._combined_dimensions(
        {"length_cm": 999}, {"length_cm": 500}, template
    )
    assert source["length_cm"] == 500
    assert source["field_provenance"]["length_cm"] == "source_confirmed"
    assert "length_cm" not in source["clamped_fields"]


def test_learned_percentiles_require_twenty_values_per_axis_before_default_switches() -> None:
    database = create_database("sqlite:///:memory:")
    repository = DimensionTemplateRepository(database)
    raw = {"source_platform": "1688", "category_id": "cable", "category_path": "配件 > 数据线"}
    for index in range(20):
        repository.record_observation(
            workspace_id="local",
            observation_key=f"package:{index}:sku",
            raw=raw,
            title="USB 数据线",
            values={"weight_g": 100 + index},
            provenance={"weight_g": "source_confirmed"},
            source_kind="source_confirmed",
            product_draft_id=index + 1,
            variant_key="sku",
        )

    assert repository.refresh_pending(force=True) == 1
    template = repository.resolve(raw, "USB 数据线", workspace_id="local")
    assert template is not None
    resolved = ProductProcessingService._combined_dimensions({}, {}, template)
    assert resolved["weight_g"] == 109.5
    # Other axes have no learned samples and still use their human priors.
    assert resolved["length_cm"] == template["known_len_default"]


def test_only_matched_source_package_rows_become_observations(tmp_path) -> None:
    database = create_database("sqlite:///:memory:")
    service = ProductProcessingService(
        ProductProcessingRepository(database), ProductProcessingAssets(tmp_path / "assets")
    )
    service._record_source_shipping_observations(
        {
            "source_platform": "1688",
            "category_id": "tools",
            "category_path": "工具 > 手工具",
            "optimized_title": "扳手",
            "shipping_package_records": [
                {"variant_key": "matched", "match_status": "matched", "weight_g": 800},
                {"variant_key": "unmatched", "match_status": "unmatched", "weight_g": 900},
            ],
        },
        task_id=1,
        product_draft_id=3,
        workspace_id="local",
    )

    with database.sessions() as session:
        observations = session.scalars(select(DimensionObservationRow)).all()
        assert len(observations) == 1
        assert observations[0].variant_key == "matched"
        assert observations[0].weight_g == 800
        assert json.loads(observations[0].provenance_json)["weight_g"] == "source_confirmed"


def test_preview_core_defaults_do_not_learn_until_operator_changes_a_value(tmp_path) -> None:
    database = create_database("sqlite:///:memory:")
    service = ProductProcessingService(
        ProductProcessingRepository(database), ProductProcessingAssets(tmp_path / "assets")
    )
    draft, _created = service.create_draft(
        {"source_type": "manual", "title": "USB 数据线", "product_name": "USB 数据线"},
        workspace_id="local",
    )
    task = service.repository.create_task(
        title="preview",
        preflight_only=False,
        settings={},
        drafts=[draft],
        idempotency_key=None,
        workspace_id="local",
    )
    task_item = task["items"][0]
    service.repository.finish_task(
        task["id"],
        [{
            "item_id": task_item["id"],
            "status": "completed",
            "reason": "",
            "result": {
                "product_draft_id": draft["id"],
                "optimized_title": "USB 数据线",
                "category_path": "配件 > 数据线",
                "category_id": "cable",
                "source_platform": "1688",
                "product_dimensions": {
                    "length_cm": 15,
                    "width_cm": 10,
                    "height_cm": 4,
                    "weight_g": 120,
                    "field_provenance": {field: "package_estimate" for field in ("length_cm", "width_cm", "height_cm", "weight_g")},
                    "field_confidence": {field: "low" for field in ("length_cm", "width_cm", "height_cm", "weight_g")},
                },
            },
        }],
        output_file="",
        error_report_file="",
        video_manifest_file="",
        workspace_id="local",
    )

    before = service.task_preview(task["id"], workspace_id="local")["items"][0]
    assert before["dimension_provenance"]["weight_g"] == "ai"
    assert before["dimension_confidence"]["weight_g"] == "low"
    service.save_task_preview(
        task["id"],
        [{
            "product_draft_id": draft["id"],
            "expected_preview_revision": before["preview_revision"],
            "overrides": {"core_fields": dict(before["core_fields"])},
        }],
        workspace_id="local",
    )
    with database.sessions() as session:
        assert session.scalars(select(DimensionObservationRow)).all() == []

    after = service.task_preview(task["id"], workspace_id="local")["items"][0]
    changed_core = dict(after["core_fields"])
    changed_core["weight_g"] = 150
    service.save_task_preview(
        task["id"],
        [{
            "product_draft_id": draft["id"],
            "expected_preview_revision": after["preview_revision"],
            "overrides": {"core_fields": changed_core},
        }],
        workspace_id="local",
    )
    with database.sessions() as session:
        observations = session.scalars(select(DimensionObservationRow)).all()
        assert len(observations) == 1
        assert observations[0].variant_key == "__default__"
        assert observations[0].weight_g == 150
        assert json.loads(observations[0].provenance_json)["weight_g"] == "manual_confirmed"
    final_preview = service.task_preview(task["id"], workspace_id="local")["items"][0]
    assert final_preview["dimension_provenance"]["weight_g"] == "manual"
    assert final_preview["dimension_confidence"]["weight_g"] == "high"


def test_extreme_axis_is_quarantined_but_other_real_axes_still_learn() -> None:
    database = create_database("sqlite:///:memory:")
    repository = DimensionTemplateRepository(database)
    raw = {"source_platform": "1688", "category_id": "cable", "category_path": "配件 > 数据线"}
    repository.record_observation(
        workspace_id="local",
        observation_key="package:1:sku",
        raw=raw,
        title="USB 数据线",
        values={"length_cm": 999, "weight_g": 120},
        provenance={"length_cm": "source_confirmed", "weight_g": "source_confirmed"},
        source_kind="source_confirmed",
        product_draft_id=1,
        variant_key="sku",
    )
    assert repository.refresh_pending(force=True) == 1

    with database.sessions() as session:
        observation = session.scalar(select(DimensionObservationRow))
        learned = session.scalar(
            select(DimensionTemplateRow).where(DimensionTemplateRow.workspace_id == "local")
        )
        quality = json.loads(observation.quality_json)
        assert observation.length_cm == 999
        assert quality["length_cm"]["status"] == "quarantined"
        assert "extreme_above_prior" in quality["length_cm"]["flags"]
        assert quality["weight_g"]["status"] == "accepted"
        assert learned.len_sample_count == 0
        assert learned.wgt_sample_count == 1
        assert learned.quarantined_axis_count == 1


def test_matched_sku_replaces_existing_default_observation_for_same_draft(tmp_path) -> None:
    database = create_database("sqlite:///:memory:")
    service = ProductProcessingService(
        ProductProcessingRepository(database), ProductProcessingAssets(tmp_path / "assets")
    )
    raw = {"source_platform": "1688", "category_id": "tools", "category_path": "工具 > 手工具"}
    service.dimension_templates.record_observation(
        workspace_id="local",
        observation_key="package:3:__default__",
        raw=raw,
        title="扳手",
        values={"weight_g": 700},
        provenance={"weight_g": "manual_confirmed"},
        source_kind="manual_confirmed",
        product_draft_id=3,
        variant_key="__default__",
    )

    service._record_source_shipping_observations(
        {
            **raw,
            "optimized_title": "扳手",
            "product_dimensions": {"weight_g": 650, "raw_estimate": {"weight_g": 600}},
            "shipping_package_records": [
                {"variant_key": "sku-one", "match_status": "matched", "weight_g": 720}
            ],
        },
        task_id=2,
        product_draft_id=3,
        workspace_id="local",
    )

    with database.sessions() as session:
        observations = session.scalars(select(DimensionObservationRow)).all()
        assert len(observations) == 1
        assert observations[0].variant_key == "sku-one"
        assert observations[0].observation_key == "package:3:sku-one"


def test_manual_product_default_is_not_learned_when_matched_sku_exists(tmp_path) -> None:
    database = create_database("sqlite:///:memory:")
    service = ProductProcessingService(
        ProductProcessingRepository(database), ProductProcessingAssets(tmp_path / "assets")
    )
    result = {
        "product_draft_id": 4,
        "optimized_title": "扳手",
        "category_path": "工具 > 手工具",
        "product_dimensions": {"weight_g": 650},
        "shipping_package_records": [
            {"variant_key": "sku-one", "match_status": "matched", "weight_g": 720}
        ],
    }
    service._record_manual_shipping_observations(
        {"id": 2, "items": [{"product_draft_id": 4, "result": result}]},
        [{"product_draft_id": 4, "overrides": {"core_fields": {"weight_g": 800}}}],
        [{"product_draft_id": 4}],
        {4: {}},
        workspace_id="local",
    )

    with database.sessions() as session:
        assert session.scalars(select(DimensionObservationRow)).all() == []


def test_accuracy_tracks_raw_and_resolved_error_and_improvement() -> None:
    database = create_database("sqlite:///:memory:")
    repository = DimensionTemplateRepository(database)
    raw = {"source_platform": "1688", "category_id": "tools", "category_path": "工具 > 手工具"}
    repository.record_observation(
        workspace_id="local",
        observation_key="package:1:sku",
        raw=raw,
        title="扳手",
        values={"length_cm": 50},
        provenance={"length_cm": "source_confirmed"},
        source_kind="source_confirmed",
        estimate_context={"length_cm": 60, "raw_estimate": {"length_cm": 100}},
        product_draft_id=1,
        variant_key="sku",
    )
    assert repository.refresh_pending(force=True) == 1

    with database.sessions() as session:
        observation = session.scalar(select(DimensionObservationRow))
        learned = session.scalar(
            select(DimensionTemplateRow).where(DimensionTemplateRow.workspace_id == "local")
        )
        metrics = json.loads(observation.error_metrics_json)["length_cm"]
        accuracy = json.loads(learned.accuracy_json)["length_cm"]
        assert metrics["raw_abs_error"] == 50
        assert metrics["resolved_abs_error"] == 10
        assert metrics["resolution_improved"] is True
        assert accuracy["raw_mae"] == 50
        assert accuracy["resolved_mae"] == 10
        assert accuracy["improvement_rate"] == 1


def test_observation_writes_coalesce_and_exact_refresh_is_deferred() -> None:
    database = create_database("sqlite:///:memory:")
    repository = DimensionTemplateRepository(database)
    raw = {"source_platform": "1688", "category_id": "tools", "category_path": "工具 > 手工具"}
    for index, weight in enumerate((700, 900), start=1):
        assert repository.record_observation(
            workspace_id="local",
            observation_key=f"package:{index}:sku",
            raw=raw,
            title="扳手",
            values={"weight_g": weight},
            provenance={"weight_g": "source_confirmed"},
            source_kind="source_confirmed",
            product_draft_id=index,
            variant_key="sku",
        )

    with database.sessions() as session:
        assert session.scalar(
            select(DimensionTemplateRow).where(DimensionTemplateRow.workspace_id == "local")
        ) is None
        queue = session.scalars(select(DimensionTemplateRefreshRow)).all()
        assert len(queue) == 1
        assert queue[0].pending_changes == 2

    assert repository.pending_refresh_count() == 1
    assert repository.refresh_pending(force=True, max_categories=1) == 1
    assert repository.pending_refresh_count() == 0
    with database.sessions() as session:
        learned = session.scalar(
            select(DimensionTemplateRow).where(DimensionTemplateRow.workspace_id == "local")
        )
        assert learned is not None
        assert learned.wgt_sample_count == 2
        assert learned.stat_wgt_p50 == 800
