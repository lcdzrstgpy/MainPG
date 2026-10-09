from pathlib import Path

from fastapi.testclient import TestClient

from wh_local.app.main import create_app


def test_app_registers_new_pod_router_and_service(tmp_path: Path) -> None:
    app = create_app(tmp_path / "workbench.sqlite3")
    # 新版 starlette 会把 include_router 的路由聚合成 _IncludedRouter 分组，
    # app.routes 上不再平铺路径，这里用 TestClient 探测：已注册但未鉴权的
    # POD 模板接口应返回 401/403（而非 404），以此证明路由已挂载。
    client = TestClient(app)
    response = client.get("/api/pod-customization/templates")
    assert response.status_code in (401, 403)

    assert app.state.pod_customization_service is not None
    assert app.state.pod_customization_ai_runtime is not None
    assert app.state.pod_customization_title_runtime is not None
