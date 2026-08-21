from pathlib import Path

from wh_local.app.main import create_app


def test_app_registers_new_pod_router_and_service(tmp_path: Path) -> None:
    app = create_app(tmp_path / "workbench.sqlite3")
    paths = {route.path for route in app.routes}

    assert "/api/pod-customization/templates" in paths
    assert app.state.pod_customization_service is not None
    assert app.state.pod_customization_ai_runtime is not None
    assert app.state.pod_customization_title_runtime is not None
