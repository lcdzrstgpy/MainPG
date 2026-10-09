from __future__ import annotations

from wh_local.modules.pod_customization.billing_contract import PodCallPlan, PodExecutionGrant
from wh_local.modules.pod_customization.worker import PodBillingRun
from wh_local.session import Actor


class RecordingCoordinator:
    def __init__(self) -> None:
        self.outcomes = ()

    def freeze(self, actor, plan):
        return PodExecutionGrant("freeze-1", 7, "2099-01-01T00:00:00Z", {"wuyin": "short-image"})

    def settle(self, actor, grant, plan, outcomes):
        self.outcomes = tuple(outcomes)

    def regrant(self, actor, freeze_id):
        return self.freeze(actor, None)


def _actor() -> Actor:
    return Actor(id="designer-1", username="designer", role="admin", workspace_id="workspace-a")


def test_provider_return_is_success_and_unused_prefrozen_calls_are_no_return() -> None:
    coordinator = RecordingCoordinator()
    plan = PodCallPlan.for_batch("batch-1", style_count=1)
    run = PodBillingRun(_actor(), coordinator, plan, coordinator.freeze(_actor(), plan))

    # The provider returned an image. A later local split/quality rejection must
    # not turn this already-consumed call into no_return.
    run.record("batch-1:style:1:image:1", "pod.image", "success")
    run.settle()

    assert [(item.call_id, item.status) for item in coordinator.outcomes] == [
        ("batch-1:style:1:image:1", "success"),
        ("batch-1:style:1:image:2", "no_return"),
        ("batch-1:style:1:title:1", "no_return"),
        ("batch-1:style:1:title:2", "no_return"),
        ("batch-1:style:1:title:3", "no_return"),
        ("batch-1:style:1:title:4", "no_return"),
        ("batch-1:style:1:title:5", "no_return"),
    ]


def test_grant_secrets_are_not_exposed_by_repr() -> None:
    grant = PodExecutionGrant(
        "freeze-1",
        7,
        "2099-01-01T00:00:00Z",
        {"ark": "never-log-this", "wuyin": "nor-this"},
        remote_token="also-secret",
    )

    assert "never-log-this" not in repr(grant)
    assert "nor-this" not in repr(grant)
    assert "also-secret" not in repr(grant)
