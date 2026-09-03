from __future__ import annotations

import pytest

from wh_local.customer.remote_client import CustomerAuthClient
from wh_local.modules.pod_customization.remote_billing import RemotePodBillingCoordinator


def test_pod_billing_coordinator_has_no_server_managed_client_mode() -> None:
    with pytest.raises(TypeError):
        RemotePodBillingCoordinator(
            object(), lambda _actor: "customer-session-token", server_managed=True
        )


def test_pod_client_has_no_server_managed_gateway_routes() -> None:
    assert not hasattr(CustomerAuthClient, "gateway_pod_title")
    assert not hasattr(CustomerAuthClient, "gateway_pod_image")
    assert not hasattr(CustomerAuthClient, "freeze_pod_points")
    assert not hasattr(CustomerAuthClient, "settle_pod_points")
    assert not hasattr(CustomerAuthClient, "pod_freeze_status")
    assert not hasattr(CustomerAuthClient, "regrant_pod_keys")
