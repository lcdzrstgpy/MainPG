from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from wh_local.modules.pod_customization.fission_panel_template import FISSION_PANEL_PROMPT_VERSION
from wh_local.modules.pod_customization.worker import PodBatchWorker
from wh_local.modules.pod_customization.tests.test_fission_api import request
from wh_local.modules.pod_customization.tests.test_replica_worker import (
    BillingCoordinator, CapturingReplicaRuntime, _actor, _parse_trial, _service,
)


class FissionTitleRuntime:
    def __init__(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        self.requests: list = []
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="fission-title")

    def submit(self, function, *args, **kwargs):
        return self._executor.submit(function, *args, **kwargs)

    def generate_title(self, request, *, grant=None, call_id="", call_ids=None, on_start=None, on_outcome=None):
        from wh_local.modules.pod_customization.title_runtime import PodTitleResult
        self.requests.append(request)
        if on_start is not None:
            on_start(call_id)
        if on_outcome is not None:
            on_outcome(call_id, "success")
        name = request.business_fields.product_name or "POD product"
        title = (
            f"{name} Style {request.style_index} Pattern Print Product Listing Title "
            "For Home Storage And Daily Organization Use"
        )
        return PodTitleResult(
            title=title,
            english_title=f"{name} bag",
            description=f"A pattern print {name} for everyday use.",
            visual_theme=f"{name} pattern visual theme",
            motif_keywords=(f"{name}-motif", "pattern"),
            color_keywords=("multicolor",),
            normalized_title=title,
            attempt_count=1,
            model="fake-ark",
            prompt_version="pod-title-v1",
        )

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)


def _worker_service(tmp_path, *, runtime=None, titles=None):
    from wh_local.modules.pod_customization.tests.test_fission_api import FakeFissionPlans, seed_assets
    runtime = runtime or CapturingReplicaRuntime()
    titles = titles or FissionTitleRuntime()
    billing = BillingCoordinator()
    service = _service(tmp_path, runtime, titles, billing)
    service.fission_analysis_runtime = FakeFissionPlans()
    service.fission_test_assets = seed_assets(service)
    return service, runtime, billing, titles


def test_fission_delivers_main_and_derived_panels_with_titles_and_export(tmp_path):
    service, runtime, billing, titles = _worker_service(tmp_path)
    try:
        created = service.create_fission_batch(_actor(), request(service), enqueue=False)
        frozen = service.repository.get_batch_internal(created['id'])['fission_variants']
        frozen_by_style = {row['style_index']: row for row in frozen}
        service.worker.process_batch(created['id'])
        batch = service.get_fission_batch(_actor(), created['id'])
        assert batch['status'] == 'completed'
        assert len(runtime.requests) == 8
        assert len(titles.requests) == 2
        panels_by_style: dict[int, dict[int, object]] = {}
        for generated in runtime.requests:
            style, panel, _attempt = _parse_trial(generated.trial_id)
            panels_by_style.setdefault(style, {})[panel] = generated
        for style, panels in panels_by_style.items():
            assert sorted(panels) == [1, 2, 3, 4]
            main = panels[1]
            assert [image.role for image in main.reference_images] == ['style_reference', 'target_product']
            plan = frozen_by_style[style]['variant_plan']
            assert main.prompt.startswith("=== PART 1 · REFERENCE ROLES ===")
            assert "KEEP A'S DNA" in main.prompt
            assert plan['composition_blueprint'] in main.prompt
            assert plan['scene'].rstrip('.') in main.prompt
            assert frozen_by_style[style]['prompt_snapshot'] in main.prompt
            for index in (2, 3, 4):
                derived = panels[index]
                assert [image.role for image in derived.reference_images] == ['main_view']
                assert "SINGLE ANCHOR BINDING" in derived.prompt
                assert "SINGLE FRAME OUTPUT" in derived.prompt
        assert all(row['image_status'] == 'completed' for row in batch['variants'])
        assert all(row['title_status'] == 'completed' for row in batch['variants'])
        assert batch['dianxiaomi_export']['exportable_style_count'] == 2
        assert service.export_dianxiaomi(_actor(), created['id']).content
        assert billing.settlements
        _, outcomes = billing.settlements[-1]
        assert sum(row.status == 'success' for row in outcomes if row.feature == 'pod.image') == 8
    finally:
        service.close()
        runtime.close()
        titles.close()


def test_fission_panel_failure_is_isolated_and_retry_keeps_frozen_plan(tmp_path):
    runtime = CapturingReplicaRuntime(fail_style_always={1})
    service, runtime, billing, titles = _worker_service(tmp_path, runtime=runtime)
    try:
        created = service.create_fission_batch(_actor(), request(service), enqueue=False)
        frozen = service.repository.get_batch_internal(created['id'])['fission_variants']
        service.worker.process_batch(created['id'])
        batch = service.get_fission_batch(_actor(), created['id'])
        assert batch['status'] == 'partial_failure'
        assert batch['dianxiaomi_export']['exportable_style_count'] == 1
        _, outcomes = billing.settlements[-1]
        assert all(
            row.status == 'no_return'
            for row in outcomes
            if row.feature == 'pod.image' and ':style:1:' in row.call_id
        )
        assert any(
            row.status == 'success'
            for row in outcomes
            if row.feature == 'pod.image' and ':style:2:' in row.call_id
        )
        successful_urls = [item['public_url'] for item in batch['items'] if item['style_index'] == 2]
        runtime.fail_style_always.clear()
        runtime.requests.clear()
        service.retry_fission_style(_actor(), created['id'], 1, enqueue=True)
        with service.worker._futures_lock:
            future = service.worker._futures[('regenerate-style', f"{created['id']}:1")]
        future.result(timeout=15)
        after = service.get_fission_batch(_actor(), created['id'])
        assert after['status'] == 'completed'
        assert {_parse_trial(row.trial_id)[0] for row in runtime.requests} == {1}
        assert {_parse_trial(row.trial_id)[1] for row in runtime.requests} == {1, 2, 3, 4}
        plan = frozen[0]['variant_plan']
        assert plan['composition_blueprint'] in runtime.requests[0].prompt
        assert frozen[0]['prompt_snapshot'] in runtime.requests[0].prompt
        assert [item['public_url'] for item in after['items'] if item['style_index'] == 2] == successful_urls
    finally:
        service.close()
        runtime.close()
        titles.close()


def test_fission_inputs_use_frozen_prompt_and_anchor_for_derived_panels() -> None:
    worker = object.__new__(PodBatchWorker)
    worker.assets = SimpleNamespace(read=lambda path: path.encode())
    batch = {
        "mode": "fission",
        "creative_prompt": "must never enter prompt",
        "fission_source": {"relative_path": "source-A", "content_type": "image/png"},
        "fission_target": {"relative_path": "product-B", "content_type": "image/jpeg"},
        "composition_json": json.dumps({
            "panels": [
                {"slot_id": "panel_1", "shot": "main shot"},
                {"slot_id": "panel_2", "shot": "macro shot"},
                {"slot_id": "panel_3", "shot": "three-quarter shot"},
                {"slot_id": "panel_4", "shot": "clean shot"},
            ]
        }),
        "fission_variants": [{
            "style_index": 1,
            "prompt_snapshot": "frozen-main-prompt",
            "variant_plan": {"style_family": "retro marine"},
        }],
    }
    prompt, inputs = worker._style_generation_inputs(
        batch, 1, 1, template_content=b"", template_content_type="", panel_index=1
    )
    assert prompt == "frozen-main-prompt"
    assert [(ref.role, ref.content) for ref in inputs["reference_images"]] == [
        ("style_reference", b"source-A"), ("target_product", b"product-B")
    ]
    retry_prompt, _ = worker._style_generation_inputs(
        batch, 1, 2, template_content=b"", template_content_type="", panel_index=1
    )
    assert retry_prompt.startswith(prompt)
    assert "must never enter prompt" not in retry_prompt

    anchor = SimpleNamespace(content=b"anchor-bytes", content_type="image/png")
    derived_prompt, derived_inputs = worker._style_generation_inputs(
        batch, 1, 1, template_content=b"", template_content_type="", panel_index=2, anchor=anchor
    )
    assert "macro shot" in derived_prompt
    assert "retro marine" in derived_prompt
    assert [(ref.role, ref.content) for ref in derived_inputs["reference_images"]] == [
        ("main_view", b"anchor-bytes")
    ]
    assert FISSION_PANEL_PROMPT_VERSION == "pod-fission-panel-v1"


def test_fission_missing_variant_is_hard_failure() -> None:
    worker = object.__new__(PodBatchWorker)
    with pytest.raises(RuntimeError, match="冻结方案"):
        worker._style_generation_inputs(
            {"mode": "fission", "fission_variants": []}, 1, 1,
            template_content=b"", template_content_type="", panel_index=1,
        )


def test_fission_resume_keeps_frozen_plans_and_uses_fission_billing(tmp_path):
    service, runtime, billing, titles = _worker_service(tmp_path)
    try:
        created = service.create_fission_batch(_actor(), request(service), enqueue=False)
        frozen = service.repository.get_batch_internal(created['id'])['fission_variants']
        service.repository.request_pause(created['id'])
        service.repository.mark_batch_paused(created['id'], 'paused for resume test')
        service.resume_batch(_actor(), created['id'])
        with service.worker._futures_lock:
            future = service.worker._futures[('batch', created['id'])]
        future.result(timeout=15)
        result = service.get_fission_batch(_actor(), created['id'])
        assert result['status'] == 'completed'
        assert len(runtime.requests) == 8
        assert billing.freezes[-1].billing_profile == 'pod_fission_v1'
        assert sum(call.feature == 'pod.image' for call in billing.freezes[-1].calls) == 16
        after = service.repository.get_batch_internal(created['id'])['fission_variants']
        assert [row['variant_plan'] for row in after] == [row['variant_plan'] for row in frozen]
    finally:
        service.close()
        runtime.close()
        titles.close()


def test_fission_outcome_is_attribution_only(tmp_path):
    from wh_local.modules.pod_customization.fission_registry import FissionRegistryService
    from wh_local.modules.pod_customization.repository import PodCustomizationRepository
    worker = object.__new__(PodBatchWorker)
    worker.repository = PodCustomizationRepository(tmp_path / 'outcomes.sqlite3')
    registry = FissionRegistryService(worker.repository)
    batch = {'mode': 'fission', 'batch_id': 'b'}
    for index in (1, 2, 3, 4):
        worker._record_fission_image_outcome(batch, index, 'failed', 'artifact_validation')
    assert registry.get_current_snapshot().capability_version == 0
    with worker.repository._connect() as connection:
        rows = connection.execute(
            "SELECT style_index, outcome, failure_kind FROM pod_customization_learning_events ORDER BY style_index"
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        (1, 'failed', 'artifact_validation'),
        (2, 'failed', 'artifact_validation'),
        (3, 'failed', 'artifact_validation'),
        (4, 'failed', 'artifact_validation'),
    ]
