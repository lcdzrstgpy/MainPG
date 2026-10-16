from __future__ import annotations

from pathlib import Path

import pytest

from wh_local.modules.pod_customization.contracts import FissionBatchCreate
from wh_local.modules.pod_customization.repository import PodRepositoryError
from wh_local.modules.pod_customization.tests.test_replica_worker import (
    BillingCoordinator, CapturingReplicaRuntime, _actor, _listing_fields, _service, _solid,
)


def seed_analysis(service, actor=None, *, status='succeeded'):
    actor = actor or _actor()
    source = service.upload_fission_image(actor, role='source_artwork', filename='a.png', content=_solid('yellow'))
    target = service.upload_fission_image(actor, role='target_product', filename='b.png', content=_solid('white'))
    service.repository.create_fission_analysis(
        analysis_id='analysis-1', workspace_id=actor.workspace_id, owner_user_id=actor.id,
        source_asset_id=source['asset_id'], target_asset_id=target['asset_id'], status=status,
        source_spec={'artwork_style': 'metal', 'artwork_elements': ['star'], 'artwork_composition': 'centered'},
        target_spec={'product_name': 'bag', 'product_type': 'bag', 'material': 'cotton', 'component_count': 1,
                     'printable_regions': ['front'], 'non_printable_regions': ['zipper']},
        user_summary={'source_summary': 'metal stars', 'target_summary': 'cotton bag'},
        candidate_payload={
            'style_vocabulary': ['metal', 'mechanical', 'retro'],
            'element_vocabulary': ['star', 'moon', 'sun', 'earth', 'gear', 'orbit'],
            'palette_vocabulary': ['yellow', 'silver', 'blue', 'black'],
            'capabilities': [{'candidate_id': 'cotton_bag', 'capability_type': 'surface',
                              'activation_conditions': [{'field': 'material', 'operator': 'eq', 'value': 'cotton'}],
                              'generated_constraints': ['Preserve zipper']}],
            'product_mappings': [{'product_type': 'bag', 'capability_ids': ['cotton_bag']}],
            'rules': [],
        },
    )
    return source, target


def request(count=2, key='request-1'):
    return FissionBatchCreate(client_request_id=key, analysis_id='analysis-1', requested_count=count,
                              listing_fields=_listing_fields('bag', 'bags', spec_cells=[['SKU', 'Length', 'Width', 'Height'], ['bag', '40', '30', '10']]))


def test_fission_create_freezes_plans_and_hides_internals(tmp_path: Path):
    runtime = CapturingReplicaRuntime()
    service = _service(tmp_path, runtime)
    try:
        source, target = seed_analysis(service)
        batch = service.create_fission_batch(_actor(), request(10), enqueue=False)
        assert batch['mode'] == 'fission'
        assert batch['count'] == 10
        assert len(batch['variants']) == 10
        assert batch['source']['asset_id'] == source['asset_id']
        assert batch['target']['asset_id'] == target['asset_id']
        assert 'prompt_snapshot' not in batch
        assert 'creative_prompt' not in batch
        assert not any('prompt_snapshot' in row or 'variant_plan' in row for row in batch['variants'])
        internal = service.repository.get_batch_internal(batch['id'])
        plans = internal['fission_variants']
        assert len({row['combination_fingerprint'] for row in plans}) == 10
        assert all(row['prompt_snapshot'] for row in plans)
        assert service.get_fission_batch(_actor(), batch['id'])['id'] == batch['id']
        assert service.list_fission_batches(_actor())['total'] == 1
    finally:
        service.close()
        runtime.close()


def test_fission_idempotency_and_ownership(tmp_path: Path):
    runtime = CapturingReplicaRuntime()
    billing = BillingCoordinator()
    service = _service(tmp_path, runtime, billing=billing)
    try:
        seed_analysis(service)
        batch = service.create_fission_batch(_actor(), request(), enqueue=False)
        first_freezes = len(billing.freezes)
        again = service.create_fission_batch(_actor(), request(), enqueue=False)
        assert again['id'] == batch['id']
        assert len(billing.freezes) == first_freezes
        with pytest.raises(PodRepositoryError) as mismatch:
            service.create_fission_batch(_actor(), request(3), enqueue=False)
        assert mismatch.value.status_code == 409
        from wh_local.session import Actor
        actor = Actor(id='other', username='other', role='operator', workspace_id='workspace-a')
        with pytest.raises(PodRepositoryError):
            service.get_fission_batch(actor, batch['id'])
        with pytest.raises(PodRepositoryError):
            service.create_fission_batch(actor, request(key='other'), enqueue=False)
    finally:
        service.close()
        runtime.close()


def test_failed_analysis_cannot_freeze_or_create(tmp_path: Path):
    runtime = CapturingReplicaRuntime()
    billing = BillingCoordinator()
    service = _service(tmp_path, runtime, billing=billing)
    try:
        seed_analysis(service, status='failed')
        with pytest.raises(PodRepositoryError):
            service.create_fission_batch(_actor(), request(), enqueue=False)
        assert not billing.freezes
    finally:
        service.close()
        runtime.close()


def test_analysis_returns_only_readonly_summary_and_does_not_publish_candidates(tmp_path):
    from types import SimpleNamespace
    from wh_local.modules.pod_customization.contracts import FissionAnalyzeRequest
    from wh_local.modules.pod_customization.fission_registry import FissionRegistryService

    class Analysis:
        def analyze_assets(self, request, source, target, *, grant, call_ids, on_start, on_outcome):
            assert source.asset_id == request.source_asset_id
            assert target.asset_id == request.target_asset_id
            on_start(call_ids[0])
            on_outcome(call_ids[0], 'success')
            return SimpleNamespace(
                source_spec={'artwork_style': 'metal'}, target_spec={'product_name': 'bag'},
                user_summary={'source_summary': '金属星星图案', 'target_summary': '帆布包'},
                style_vocabulary=['hidden-style'], element_vocabulary=['hidden-element'],
                palette_vocabulary=['hidden-palette'], capability_candidates=(), rule_candidates=(),
                product_mapping_candidates=(), raw_response={'internal': 'hidden'},
                model_version='fake', schema_version='v1',
            )

    runtime = CapturingReplicaRuntime()
    service = _service(tmp_path, runtime)
    service.fission_analysis_runtime = Analysis()
    try:
        source, target = seed_analysis(service)
        result = service.analyze_fission_assets(_actor(), FissionAnalyzeRequest(
            source_asset_id=source['asset_id'], target_asset_id=target['asset_id']))
        assert set(result) == {'analysis_id', 'status', 'product_summary', 'artwork_summary'}
        assert result['product_summary'] == '帆布包'
        assert FissionRegistryService(service.repository).get_current_snapshot().capability_version == 0
        stored = service.repository.get_fission_analysis_for_actor(result['analysis_id'], _actor().workspace_id, _actor().id)
        assert stored['raw_response'] == {'internal': 'hidden'}
    finally:
        service.close()
        runtime.close()


def test_custom_fission_composition_is_frozen_and_other_pools_do_not_leak(tmp_path):
    from wh_local.modules.pod_customization.contracts import COMPOSITION_PANEL_KEYS
    runtime = CapturingReplicaRuntime()
    service = _service(tmp_path, runtime)
    actor = _actor()
    try:
        seed_analysis(service)
        for scope in ('full', 'replica', 'fission'):
            service.repository.create_composition(
                workspace_id=actor.workspace_id, owner_user_id=actor.id,
                composition_id=f'{scope}-composition', name=scope, raw_input=scope,
                panels={key: {'zh': f'{scope}中文', 'en': f'{scope} shot {key}'} for key in COMPOSITION_PANEL_KEYS},
                model='fake', prompt_version='v1', scope=scope,
            )
        batch = service.create_fission_batch(actor, request(1), enqueue=False)
        assert batch['composition']['scope'] == 'fission'
        assert batch['composition']['composition_id'] == 'fission-composition'
        frozen = service.repository.get_batch_internal(batch['id'])['fission_variants'][0]['prompt_snapshot']
        assert 'fission shot' in frozen
        assert 'full shot' not in frozen and 'replica shot' not in frozen
        service.repository.update_composition_panels(actor.workspace_id, actor.id, 'fission-composition',
            panels={key: {'zh': '新构图', 'en': 'changed shot'} for key in COMPOSITION_PANEL_KEYS},
            model='fake', prompt_version='v2')
        assert service.repository.get_batch_internal(batch['id'])['fission_variants'][0]['prompt_snapshot'] == frozen
    finally:
        service.close()
        runtime.close()
