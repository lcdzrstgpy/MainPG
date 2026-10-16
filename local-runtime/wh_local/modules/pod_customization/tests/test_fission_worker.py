from __future__ import annotations

from types import SimpleNamespace

import pytest

from wh_local.modules.pod_customization.worker import PodBatchWorker
from wh_local.modules.pod_customization.tests.test_replica_worker import CapturingTitleRuntime


class FissionTitleRuntime(CapturingTitleRuntime):
    def generate_title(self, request, **kwargs):
        from dataclasses import replace
        result = super().generate_title(request, **kwargs)
        motif = "Celestial Moon" if request.style_index == 1 else "Mechanical Sun"
        title = f"{motif} Cotton Bag Pattern Print Product Listing For Home Storage And Daily Organization Use"
        return replace(result, title=title, normalized_title=title,
                       english_title=f"{motif} bag", description=f"{motif} bag for everyday use.")



def test_fission_delivers_frozen_styles_titles_spec_cards_and_export(tmp_path):
    from wh_local.modules.pod_customization.tests.test_fission_api import seed_analysis, request
    from wh_local.modules.pod_customization.tests.test_replica_worker import (
        BillingCoordinator, CapturingReplicaRuntime, CapturingTitleRuntime, _service, _actor,
    )
    runtime, titles, billing = CapturingReplicaRuntime(), FissionTitleRuntime(), BillingCoordinator()
    service = _service(tmp_path, runtime, titles, billing)
    try:
        seed_analysis(service)
        created = service.create_fission_batch(_actor(), request(), enqueue=False)
        frozen = service.repository.get_batch_internal(created['id'])['fission_variants']
        service.worker.process_batch(created['id'])
        batch = service.get_fission_batch(_actor(), created['id'])
        assert batch['status'] == 'completed'
        assert len(runtime.requests) == 2
        assert len(titles.requests) == 2
        for generated in runtime.requests:
            assert [image.role for image in generated.reference_images] == ['style_reference', 'target_product']
            assert generated.prompt in {row['prompt_snapshot'] for row in frozen}
        assert all(title.business_fields.product_name == 'bag' for title in titles.requests)
        assert all(row['image_status'] == 'completed' for row in batch['variants'])
        assert all(row['title_status'] == 'completed' for row in batch['variants'])
        assert batch['dianxiaomi_export']['exportable_style_count'] == 2
        assert service.export_dianxiaomi(_actor(), created['id']).content
        assert billing.settlements
        _, outcomes = billing.settlements[-1]
        assert sum(row.status == 'success' for row in outcomes if row.feature == 'pod.image') == 2
    finally:
        service.close()
        runtime.close()
        titles.close()


def test_fission_invalid_grid_refunds_only_failed_style_and_retry_preserves_plan(tmp_path):
    from wh_local.modules.pod_customization.tests.test_fission_api import seed_analysis, request
    from wh_local.modules.pod_customization.tests.test_replica_worker import (
        BillingCoordinator, CapturingReplicaRuntime, CapturingTitleRuntime, _service, _actor, _parse_trial,
    )
    runtime, titles, billing = CapturingReplicaRuntime(), FissionTitleRuntime(), BillingCoordinator()
    original = runtime.generate_listing_grid

    def generate(generation, *, grant=None, call_id=""):
        media = original(generation, grant=grant, call_id=call_id)
        if _parse_trial(generation.trial_id)[0] == 1:
            from dataclasses import replace
            return replace(media, content=b'invalid-image')
        return media

    runtime.generate_listing_grid = generate
    service = _service(tmp_path, runtime, titles, billing)
    try:
        seed_analysis(service)
        created = service.create_fission_batch(_actor(), request(), enqueue=False)
        service.worker.process_batch(created['id'])
        batch = service.get_fission_batch(_actor(), created['id'])
        assert batch['status'] == 'partial_failure'
        assert batch['dianxiaomi_export']['exportable_style_count'] == 1
        assert service.export_dianxiaomi(_actor(), created['id']).content
        _, outcomes = billing.settlements[-1]
        assert all(row.status == 'no_return' for row in outcomes if row.feature == 'pod.image' and ':style:1:' in row.call_id)
        assert any(row.status == 'success' for row in outcomes if row.feature == 'pod.image' and ':style:2:' in row.call_id)
        before = service.repository.get_batch_internal(created['id'])['fission_variants'][0]
        successful_urls = [item['public_url'] for item in batch['items'] if item['style_index'] == 2]
        runtime.generate_listing_grid = original
        runtime.requests.clear()
        service.retry_fission_style(_actor(), created['id'], 1, enqueue=True)
        with service.worker._futures_lock:
            future = service.worker._futures[('regenerate-style', f"{created['id']}:1")]
        future.result(timeout=15)
        after = service.get_fission_batch(_actor(), created['id'])
        assert after['status'] == 'completed'
        assert all(_parse_trial(row.trial_id)[0] == 1 for row in runtime.requests)
        assert runtime.requests[0].prompt == before['prompt_snapshot']
        assert [item['public_url'] for item in after['items'] if item['style_index'] == 2] == successful_urls
    finally:
        service.close()
        runtime.close()
        titles.close()


def test_fission_inputs_use_frozen_prompt_and_ordered_references() -> None:
    worker = object.__new__(PodBatchWorker)
    worker.assets = SimpleNamespace(read=lambda path: path.encode())
    batch = {
        "mode": "fission", "creative_prompt": "must never enter prompt",
        "fission_source": {"relative_path": "source-A", "content_type": "image/png"},
        "fission_target": {"relative_path": "product-B", "content_type": "image/jpeg"},
        "fission_variants": [{"style_index": 1, "prompt_snapshot": "frozen-grid-prompt"}],
    }
    prompt, inputs = worker._style_generation_inputs(
        batch, 1, 1, template_content=b"", template_content_type=""
    )
    assert prompt == "frozen-grid-prompt"
    assert [(ref.role, ref.content) for ref in inputs["reference_images"]] == [
        ("style_reference", b"source-A"), ("target_product", b"product-B")
    ]
    retry_prompt, _ = worker._style_generation_inputs(
        batch, 1, 2, template_content=b"", template_content_type=""
    )
    assert retry_prompt.startswith(prompt)
    assert "must never enter prompt" not in retry_prompt


def test_fission_resume_keeps_frozen_plan_and_uses_fission_billing(tmp_path):
    from wh_local.modules.pod_customization.tests.test_fission_api import seed_analysis, request
    from wh_local.modules.pod_customization.tests.test_replica_worker import (
        BillingCoordinator, CapturingReplicaRuntime, CapturingTitleRuntime, _service, _actor,
    )
    runtime, titles, billing = CapturingReplicaRuntime(), FissionTitleRuntime(), BillingCoordinator()
    service = _service(tmp_path, runtime, titles, billing)
    try:
        seed_analysis(service)
        created = service.create_fission_batch(_actor(), request(), enqueue=False)
        frozen = service.repository.get_batch_internal(created['id'])['fission_variants']
        service.repository.request_pause(created['id'])
        service.repository.mark_batch_paused(created['id'], 'paused for resume test')
        service.resume_batch(_actor(), created['id'])
        with service.worker._futures_lock:
            future = service.worker._futures[('batch', created['id'])]
        future.result(timeout=15)
        result = service.get_fission_batch(_actor(), created['id'])
        assert result['status'] == 'completed'
        assert all(generation.prompt in {row['prompt_snapshot'] for row in frozen}
                   for generation in runtime.requests)
        assert billing.freezes[-1].billing_profile == 'pod_fission_v1'
        after = service.repository.get_batch_internal(created['id'])['fission_variants']
        assert [row['variant_plan'] for row in after] == [row['variant_plan'] for row in frozen]
    finally:
        service.close()
        runtime.close()
        titles.close()


def test_fission_missing_variant_is_hard_failure() -> None:
    worker = object.__new__(PodBatchWorker)
    with pytest.raises(RuntimeError, match="冻结方案"):
        worker._style_generation_inputs(
            {"mode": "fission", "fission_variants": []}, 1, 1,
            template_content=b"", template_content_type="",
        )


def test_fission_terminal_image_outcome_reaches_shared_registry(tmp_path):
    from wh_local.modules.pod_customization.fission_registry import FissionRegistryService
    from wh_local.modules.pod_customization.repository import PodCustomizationRepository
    from wh_local.modules.pod_customization.tests.test_fission_registry import _candidate
    worker = object.__new__(PodBatchWorker)
    worker.repository = PodCustomizationRepository(tmp_path / 'outcomes.sqlite3')
    registry = FissionRegistryService(worker.repository)
    snapshot = registry.publish_candidates(registry.validate_candidates(_candidate()))
    batch = {'mode': 'fission', 'batch_id': 'b',
             'capability_registry_version': snapshot.capability_version,
             'rule_registry_version': snapshot.rule_version}
    for index in (1, 2):
        worker._record_fission_image_outcome(batch, index, 'failed', 'artifact_validation')
    assert registry.get_current_snapshot().capability_version == snapshot.capability_version
    worker._record_fission_image_outcome(batch, 3, 'failed', 'artifact_validation')
    assert registry.get_current_snapshot().capability_version == 0


def test_fission_infrastructure_failure_does_not_rollback(tmp_path):
    from wh_local.modules.pod_customization.fission_registry import FissionRegistryService
    from wh_local.modules.pod_customization.repository import PodCustomizationRepository
    from wh_local.modules.pod_customization.tests.test_fission_registry import _candidate
    worker = object.__new__(PodBatchWorker)
    worker.repository = PodCustomizationRepository(tmp_path / 'infra.sqlite3')
    registry = FissionRegistryService(worker.repository)
    snapshot = registry.publish_candidates(registry.validate_candidates(_candidate()))
    batch = {'mode': 'fission', 'batch_id': 'b',
             'capability_registry_version': snapshot.capability_version,
             'rule_registry_version': snapshot.rule_version}
    for index in range(1, 5):
        worker._record_fission_image_outcome(batch, index, 'failed', 'infrastructure')
    assert registry.get_current_snapshot() == snapshot
