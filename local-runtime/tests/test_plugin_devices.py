from __future__ import annotations

from pathlib import Path

import pytest

from wh_local.data_collection.plugin_queue import DataCollectionPluginQueue


def _queue(tmp_path: Path) -> DataCollectionPluginQueue:
    return DataCollectionPluginQueue(tmp_path / "queue.sqlite3")


def test_pair_code_exchanges_for_device_token(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    code = queue.create_pair_code(actor_id="acc-a", workspace_id="default")["code"]
    assert len(code) == 6 and code.isdigit()

    paired = queue.pair_device(
        code=code, name="Edge 插件", browser="Edge", extension_version="0.1.144"
    )
    assert paired["device_id"].startswith("dev_")
    assert paired["device_token"].startswith("pd_")
    # 配对码一次性
    with pytest.raises(PermissionError):
        queue.pair_device(code=code)

    device = queue.device_for_token(paired["device_token"])
    assert device["device_id"] == paired["device_id"]
    assert device["name"] == "Edge 插件"


def test_device_session_carries_device_and_shop_scope(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    code = queue.create_pair_code(actor_id="acc-a", workspace_id="default")["code"]
    paired = queue.pair_device(code=code, name="Edge 插件")
    device = queue.device_for_token(paired["device_token"])
    queue.create_session(
        actor_id=device["actor_id"],
        workspace_id=device["workspace_id"],
        capabilities={"temu_capture_all": True},
        device_id=device["device_id"],
    )
    sessions = queue.list_sessions(actor_id="acc-a", workspace_id="default")
    assert sessions[0]["device_name"] == "Edge 插件"
    assert sessions[0]["device_id"] == device["device_id"]

    queue.set_device_shops(
        device_id=device["device_id"],
        actor_id="acc-a",
        workspace_id="default",
        shops=["Little Pony Shop"],
    )
    assert queue.device_for_token(paired["device_token"])["allowed_shops"] == [
        "Little Pony Shop"
    ]
    assert queue.list_devices(actor_id="acc-a", workspace_id="default")[0][
        "allowed_shops"
    ] == ["Little Pony Shop"]


def test_revoked_device_token_is_rejected(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    code = queue.create_pair_code(actor_id="acc-a", workspace_id="default")["code"]
    paired = queue.pair_device(code=code, name="Edge 插件")
    assert queue.revoke_device(
        device_id=paired["device_id"], actor_id="acc-a", workspace_id="default"
    )
    with pytest.raises(PermissionError):
        queue.device_for_token(paired["device_token"])
    # 别的账号不能解绑别人的设备
    code2 = queue.create_pair_code(actor_id="acc-a", workspace_id="default")["code"]
    paired2 = queue.pair_device(code=code2, name="Edge 插件 2")
    assert not queue.revoke_device(
        device_id=paired2["device_id"], actor_id="acc-b", workspace_id="default"
    )
