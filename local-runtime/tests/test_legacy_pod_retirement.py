from pathlib import Path

from wh_local.modules.ai_service import create_router


def test_legacy_ai_service_pod_routes_can_be_disabled_without_removing_chat(
    tmp_path: Path,
) -> None:
    router = create_router(
        tmp_path / "workbench.sqlite3",
        tmp_path / "assets",
        legacy_pod_enabled=False,
    )
    paths = {route.path for route in router.routes}

    assert "/api/ai-service/bootstrap" in paths
    assert "/api/ai-service/conversations" in paths
    assert not any("pod-creations" in path for path in paths)
