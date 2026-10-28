from __future__ import annotations

from pathlib import Path

import pytest

from wh_local.modules.pod_customization.billing_contract import PodExecutionGrant
from wh_local.modules.pod_customization.contracts import FissionBatchCreate
from wh_local.modules.pod_customization.fission_runtime import (
    FISSION_PLAN_SCHEMA_VERSION,
    FissionPlan,
    FissionPlanResult,
    plan_fingerprint,
)
from wh_local.modules.pod_customization.repository import PodRepositoryError
from wh_local.modules.pod_customization.tests.test_replica_worker import (
    BillingCoordinator, CapturingReplicaRuntime, _actor, _listing_fields, _service, _solid,
)

_LONG_TAIL = (
    " The single square frame shows reference image 2 (a cotton tote bag) printed edge to edge with "
    "reference image 1 motifs, continuing across the front and both side panels, on a soft studio "
    "backdrop with warm directional key light and gentle contact shadow."
)


def _plan(index: int) -> dict[str, object]:
    family = f"family-{index}"
    motifs = [f"motif-{index}a", f"motif-{index}b"]
    palette = {"primary": f"colour-{index}", "secondary": "warm sand", "accent": "brass"}
    prompt = (
        f"Design {index}: a {family} all-over print using {motifs[0]} and {motifs[1]}, palette "
        f"{palette['primary']} with {palette['secondary']} and {palette['accent']}." + _LONG_TAIL
    )
    return {
        "style_family": family,
        "motifs": motifs,
        "palette": palette,
        "composition_blueprint": (
            f"A diagonal cascade of {motifs[0]} clusters rising from the lower-left corner, with "
            f"{motifs[1]} sprigs filling the upper-right field at half scale."
        ),
        "scene": (
            "A sunlit whitewashed studio table with a plain linen runner, one small ceramic vase of dried "
            "grass at the far edge and clean window light from the left."
        ),
        "variation_note": f"differs by family-{index} and motif set",
        "main_prompt": prompt,
        "combination_fingerprint": plan_fingerprint(family, motifs, palette),
    }


class FakeFissionPlans:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[int] = []

    def generate_plans(self, request, source, target, *, grant, call_ids, on_start=None, on_outcome=None):
        assert isinstance(grant, PodExecutionGrant)
        assert source.asset_id == request.source_asset_id
        assert target.asset_id == request.target_asset_id
        self.calls.append(int(request.requested_count))
        if self.fail:
            raise RuntimeError("plan call failed")
        if on_start is not None:
            on_start(call_ids[0])
        if on_outcome is not None:
            on_outcome(call_ids[0], "success")
        plans = tuple(
            FissionPlan(
                style_family=str(item["style_family"]),
                motifs=tuple(item["motifs"]),  # type: ignore[arg-type]
                palette=dict(item["palette"]),  # type: ignore[arg-type]
                composition_blueprint=str(item["composition_blueprint"]),
                scene=str(item["scene"]),
                variation_note=str(item["variation_note"]),
                main_prompt=str(item["main_prompt"]),
                fingerprint=str(item["combination_fingerprint"]),
            )
            for item in (_plan(index) for index in range(1, request.requested_count + 1))
        )
        return FissionPlanResult(
            source_summary="metal star artwork",
            target_summary="cotton tote bag",
            product_name="cotton tote bag",
            plans=plans,
            model_version="fake",
            schema_version=FISSION_PLAN_SCHEMA_VERSION,
            raw_response={"internal": "hidden"},
        )


def seed_assets(service, actor=None) -> tuple[str, str]:
    actor = actor or _actor()
    source = service.upload_fission_image(actor, role='source_artwork', filename='a.png', content=_solid('yellow'))
    target = service.upload_fission_image(actor, role='target_product', filename='b.png', content=_solid('white'))
    return source['asset_id'], target['asset_id']


def request(service, count=2, key='request-1') -> FissionBatchCreate:
    source_id, target_id = service.fission_test_assets
    return FissionBatchCreate(
        client_request_id=key,
        source_asset_id=source_id,
        target_asset_id=target_id,
        requested_count=count,
        listing_fields=_listing_fields(
            'bag', 'bags', spec_cells=[['SKU', 'Length', 'Width', 'Height'], ['bag', '40', '30', '10']]
        ),
    )


def _prepared_service(tmp_path: Path, *, fail: bool = False):
    runtime, billing = CapturingReplicaRuntime(), BillingCoordinator()
    service = _service(tmp_path, runtime, billing=billing)
    plans = FakeFissionPlans(fail=fail)
    service.fission_analysis_runtime = plans
    service.fission_test_assets = seed_assets(service)
    return service, runtime, billing, plans


def test_fission_create_freezes_ai_plans_and_hides_internals(tmp_path: Path):
    service, runtime, _billing, plans = _prepared_service(tmp_path)
    try:
        batch = service.create_fission_batch(_actor(), request(service, 3), enqueue=False)
        assert batch['mode'] == 'fission'
        assert batch['count'] == 3
        assert batch['generation_strategy'] == 'independent_panels'
        assert len(batch['variants']) == 3
        assert 'prompt_snapshot' not in batch
        assert not any('prompt_snapshot' in row or 'variant_plan' in row for row in batch['variants'])
        internal = service.repository.get_batch_internal(batch['id'])
        frozen = internal['fission_variants']
        assert len({row['combination_fingerprint'] for row in frozen}) == 3
        assert all(row['prompt_snapshot'] for row in frozen)
        assert internal['fission_generation_strategy'] == 'independent_panels'
        assert plans.calls == [3]
        assert not runtime.requests
        analysis = service.repository.get_fission_analysis_for_actor(
            internal['fission_analysis_id'], _actor().workspace_id, _actor().id
        )
        assert analysis['status'] == 'succeeded'
        assert len(analysis['plans']) == 3
    finally:
        service.close()
        runtime.close()


def test_fission_idempotency_and_ownership(tmp_path: Path):
    service, runtime, billing, plans = _prepared_service(tmp_path)
    try:
        batch = service.create_fission_batch(_actor(), request(service), enqueue=False)
        freezes = len(billing.freezes)
        again = service.create_fission_batch(_actor(), request(service), enqueue=False)
        assert again['id'] == batch['id']
        assert len(billing.freezes) == freezes
        assert plans.calls == [2]
        with pytest.raises(PodRepositoryError) as mismatch:
            service.create_fission_batch(_actor(), request(service, 3), enqueue=False)
        assert mismatch.value.status_code == 409
        from wh_local.session import Actor
        actor = Actor(id='other', username='other', role='operator', workspace_id='workspace-a')
        with pytest.raises(PodRepositoryError):
            service.get_fission_batch(actor, batch['id'])
        with pytest.raises(PodRepositoryError):
            service.create_fission_batch(actor, request(service, key='other'), enqueue=False)
    finally:
        service.close()
        runtime.close()


def test_failed_plan_call_does_not_create_batch_or_image_freeze(tmp_path: Path):
    service, runtime, billing, _plans = _prepared_service(tmp_path, fail=True)
    try:
        with pytest.raises(RuntimeError):
            service.create_fission_batch(_actor(), request(service), enqueue=False)
        assert len(billing.freezes) == 1
        assert all(row.status == 'no_return' for row in billing.settlements[0][1])
        assert service.list_fission_batches(_actor())['total'] == 0
        with service.repository._connect() as connection:
            row = connection.execute(
                "SELECT status FROM pod_customization_fission_analyses ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        assert row[0] == 'failed'
    finally:
        service.close()
        runtime.close()


def test_legacy_grid_fission_batch_is_read_only(tmp_path: Path):
    service, runtime, _billing, _plans = _prepared_service(tmp_path)
    try:
        batch = service.create_fission_batch(_actor(), request(service, 1), enqueue=False)
        with service.repository._connect() as connection:
            connection.execute(
                "UPDATE pod_customization_fission_batches SET generation_strategy = 'grid' WHERE batch_id = ?",
                (batch['id'],),
            )
        with pytest.raises(PodRepositoryError) as rejected:
            service.retry_fission_style(_actor(), batch['id'], 1, enqueue=False)
        assert rejected.value.status_code == 409
        with pytest.raises(PodRepositoryError) as retry_failed:
            service._freeze_batch_retry(
                _actor(), f"{batch['id']}:batch-retry:test", batch['id'], (1,), ()
            )
        assert retry_failed.value.status_code == 409
    finally:
        service.close()
        runtime.close()


def test_custom_fission_composition_is_frozen(tmp_path):
    from wh_local.modules.pod_customization.contracts import COMPOSITION_PANEL_KEYS
    service, runtime, _billing, _plans = _prepared_service(tmp_path)
    actor = _actor()
    try:
        for scope in ('full', 'replica', 'fission'):
            service.repository.create_composition(
                workspace_id=actor.workspace_id, owner_user_id=actor.id,
                composition_id=f'{scope}-composition', name=scope, raw_input=scope,
                panels={key: {'zh': f'{scope}中文', 'en': f'{scope} shot {key}'} for key in COMPOSITION_PANEL_KEYS},
                model='fake', prompt_version='v1', scope=scope,
            )
        batch = service.create_fission_batch(actor, request(service, 1), enqueue=False)
        assert batch['composition']['scope'] == 'fission'
        assert batch['composition']['composition_id'] == 'fission-composition'
        internal = service.repository.get_batch_internal(batch['id'])
        assert internal['fission_variants'][0]['prompt_snapshot'] == _plan(1)['main_prompt']
        assert 'fission shot' in internal['composition_json']
        service.repository.update_composition_panels(
            actor.workspace_id, actor.id, 'fission-composition',
            panels={key: {'zh': '新构图', 'en': 'changed shot'} for key in COMPOSITION_PANEL_KEYS},
            model='fake', prompt_version='v2',
        )
        assert service.repository.get_batch_internal(batch['id'])['fission_variants'][0]['prompt_snapshot'] == _plan(1)['main_prompt']
    finally:
        service.close()
        runtime.close()
