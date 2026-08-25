from pathlib import Path

from fastapi.testclient import TestClient

from wh_local.app.main import create_app


def test_app_registers_shop_routes_and_host_owned_worker(tmp_path: Path) -> None:
    app = create_app(tmp_path / "workbench.sqlite3")
    # 新版 starlette 将 include_router 路由聚合为 _IncludedRouter 分组，
    # 用 TestClient 探测：已注册但未鉴权的店铺批次接口应返回 401/403（非 404）。
    client = TestClient(app)
    response = client.get("/desktop/data-collection/shop-batches")
    assert response.status_code in (401, 403)

    assert app.state.shop_collection_worker is not None
    assert app.state.shop_collection_worker._budget is app.state.data_collection_api_budget
