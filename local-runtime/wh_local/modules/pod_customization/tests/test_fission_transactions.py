"""Transaction fences for the one-click fission batch."""

import sqlite3

import pytest

from wh_local.modules.pod_customization.tests.test_fission_api import (
    _prepared_service, request,
)
from wh_local.modules.pod_customization.tests.test_replica_worker import _actor


def test_variant_write_failure_rolls_back_batch_and_refunds_image_freeze(tmp_path):
    service, runtime, billing, _plans = _prepared_service(tmp_path)
    try:
        with service.repository._connect() as connection:
            connection.execute("""CREATE TRIGGER reject_fission_variant BEFORE INSERT ON
                pod_customization_fission_variants BEGIN SELECT RAISE(ABORT, 'injected'); END""")
        with pytest.raises(sqlite3.IntegrityError, match='injected'):
            service.create_fission_batch(_actor(), request(service), enqueue=False)
        with service.repository._connect() as connection:
            for table in ('pod_customization_fission_batches', 'pod_customization_batches',
                          'pod_customization_learning_events'):
                assert connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
        # 第一次是出方案（零计费），第二次是生图批次；批次失败后立即结算为 no_return。
        assert len(billing.freezes) == 2
        assert len(billing.settlements) == 2
        assert all(row.status == 'no_return' for row in billing.settlements[1][1])
        assert service.list_fission_batches(_actor())['total'] == 0
    finally:
        service.close()
        runtime.close()


def test_analysis_survives_batch_failure_and_can_be_reused_by_retry(tmp_path):
    """批次事务失败后，分析记录保留；同一请求重试会重新出方案并成功建批次。"""
    service, runtime, _billing, plans = _prepared_service(tmp_path)
    try:
        with service.repository._connect() as connection:
            connection.execute("""CREATE TRIGGER reject_fission_variant BEFORE INSERT ON
                pod_customization_fission_variants BEGIN SELECT RAISE(ABORT, 'injected'); END""")
        with pytest.raises(sqlite3.IntegrityError):
            service.create_fission_batch(_actor(), request(service), enqueue=False)
        with service.repository._connect() as connection:
            connection.execute("DROP TRIGGER reject_fission_variant")
        batch = service.create_fission_batch(_actor(), request(service), enqueue=False)
        assert batch['mode'] == 'fission'
        assert len(batch['variants']) == 2
        assert plans.calls == [2, 2]
    finally:
        service.close()
        runtime.close()
