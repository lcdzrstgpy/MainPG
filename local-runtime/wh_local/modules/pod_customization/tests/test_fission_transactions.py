"""Transaction fences for confirmation, registry learning, and billing compensation."""

import sqlite3

import pytest

from wh_local.modules.pod_customization.fission_registry import FissionRegistryService
from wh_local.modules.pod_customization.repository import PodRepositoryError, RegistryVersionConflict
from wh_local.modules.pod_customization.tests.test_fission_api import request, seed_analysis
from wh_local.modules.pod_customization.tests.test_replica_worker import (
    BillingCoordinator, CapturingReplicaRuntime, _actor, _service,
)


def test_variant_write_failure_rolls_back_registry_batch_and_learning_event(tmp_path):
    runtime, billing = CapturingReplicaRuntime(), BillingCoordinator()
    service = _service(tmp_path, runtime, billing=billing)
    try:
        seed_analysis(service)
        with service.repository._connect() as connection:
            connection.execute("""CREATE TRIGGER reject_fission_variant BEFORE INSERT ON
                pod_customization_fission_variants BEGIN SELECT RAISE(ABORT, 'injected'); END""")
        with pytest.raises(sqlite3.IntegrityError, match='injected'):
            service.create_fission_batch(_actor(), request(), enqueue=False)
        assert FissionRegistryService(service.repository).get_current_snapshot().capability_version == 0
        with service.repository._connect() as connection:
            for table in ('pod_customization_fission_batches', 'pod_customization_batches',
                          'pod_customization_learning_events', 'pod_customization_capability_registry_versions',
                          'pod_customization_rule_registry_versions'):
                assert connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
        assert len(billing.freezes) == len(billing.settlements) == 1
        assert all(row.status == 'no_return' for row in billing.settlements[0][1])
    finally:
        service.close()
        runtime.close()


def test_registry_conflict_replans_without_another_analysis_or_freeze(tmp_path, monkeypatch):
    runtime, billing = CapturingReplicaRuntime(), BillingCoordinator()
    service = _service(tmp_path, runtime, billing=billing)
    try:
        seed_analysis(service)
        repository = service.repository
        original = repository.create_fission_batch_with_registry
        submissions = []

        def submit(**kwargs):
            submissions.append(kwargs)
            if len(submissions) == 1:
                # A different process publishes between preview and the commit fence.
                registry = FissionRegistryService(repository)
                registry.publish_candidates(registry.validate_candidates({
                    'capabilities': [{'candidate_id': 'parallel', 'capability_type': 'surface'}],
                }), analysis_id='parallel-analysis')
            return original(**kwargs)

        monkeypatch.setattr(repository, 'create_fission_batch_with_registry', submit)
        batch = service.create_fission_batch(_actor(), request(), enqueue=False)
        assert len(submissions) == 2
        assert len(billing.freezes) == 1
        assert not runtime.requests
        frozen = repository.get_batch_internal(batch['id'])
        snapshot = FissionRegistryService(repository).get_current_snapshot()
        assert frozen['capability_registry_version'] == snapshot.capability_version
        assert all(row['variant_plan']['capability_registry_version'] == snapshot.capability_version
                   for row in frozen['fission_variants'])
    finally:
        service.close()
        runtime.close()


def test_persistent_registry_conflict_refunds_unused_freeze(tmp_path, monkeypatch):
    runtime, billing = CapturingReplicaRuntime(), BillingCoordinator()
    service = _service(tmp_path, runtime, billing=billing)
    try:
        seed_analysis(service)
        attempts = []

        def conflict(**kwargs):
            attempts.append(kwargs)
            raise RegistryVersionConflict((0, 0), (1, 1))

        monkeypatch.setattr(service.repository, 'create_fission_batch_with_registry', conflict)
        with pytest.raises(PodRepositoryError, match='能力库正在更新'):
            service.create_fission_batch(_actor(), request(), enqueue=False)
        assert len(attempts) == 3
        assert len(billing.freezes) == len(billing.settlements) == 1
        assert all(row.status == 'no_return' for row in billing.settlements[0][1])
        assert service.list_fission_batches(_actor())['total'] == 0
    finally:
        service.close()
        runtime.close()
