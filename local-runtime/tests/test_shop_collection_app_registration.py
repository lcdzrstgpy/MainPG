from pathlib import Path

from wh_local.app.main import create_app


def test_app_registers_shop_routes_and_host_owned_worker(tmp_path: Path) -> None:
    app = create_app(tmp_path / "workbench.sqlite3")
    paths = {route.path for route in app.routes}

    assert "/desktop/data-collection/shop-batches" in paths
    assert app.state.shop_collection_worker is not None
    assert app.state.shop_collection_worker._budget is app.state.data_collection_api_budget
