from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from wh_local.app import main as app_main
from wh_local.customer.collect_credentials import CollectCredentialsError
from wh_local.data_collection import DailySelectionActor


def test_provider_config_never_falls_back_to_local_onebound_credentials(
    monkeypatch,
) -> None:
    config = SimpleNamespace(
        customer_auth_base_url="https://auth.example.test",
        onebound_1688_api_key="local-key",
        onebound_1688_api_secret="local-secret",
        onebound_1688_base_url="https://api-gw.onebound.cn/1688",
        onebound_1688_enabled=True,
    )

    def _unreachable(**_kwargs):
        raise CollectCredentialsError("cannot reach collect-key service: SSL EOF")

    monkeypatch.setattr(app_main, "default_config", lambda: config)
    monkeypatch.setattr(app_main, "request_collect_credentials", _unreachable)

    with pytest.raises(HTTPException) as caught:
        app_main._provider_config(
            DailySelectionActor(actor_id="account-1", workspace_id="default")
        )

    assert caught.value.status_code == 403
    assert "cannot reach collect-key service" in str(caught.value.detail)
    assert "local-key" not in str(caught.value.detail)
    assert "local-secret" not in str(caught.value.detail)
