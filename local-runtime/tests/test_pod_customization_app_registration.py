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
    assert app.state.pod_customization_fission_analysis_runtime is not None
    for path in ("fission/batches", "fission/batches/unknown"):
        assert client.get(f"/api/pod-customization/{path}").status_code in (401, 403)
    for path in ("fission/images", "fission/batches",
                 "fission/batches/unknown/styles/1/retry"):
        assert client.post(f"/api/pod-customization/{path}", json={}).status_code in (401, 403)
    # 一键开始裂变后不再有独立的分析入口。
    assert client.post("/api/pod-customization/fission/analyze", json={}).status_code == 404


def test_replica_openapi_contract_drops_legacy_fields(tmp_path: Path) -> None:
    """部署后的 HTTP 契约就是冒烟要用的形状：多字段一律拒绝。"""

    app = create_app(tmp_path / "workbench.sqlite3")
    schemas = app.openapi()["components"]["schemas"]

    analyze = schemas["ReplicaAnalyzeRequest"]
    assert set(analyze["properties"]) == {"source_asset_id", "target_asset_id"}
    assert analyze.get("additionalProperties") is False

    target = schemas["ReplicaTargetCreate"]
    assert "analysis_id" in target["properties"]
    assert not {"fact_overrides", "presentation_preferences"} & set(target["properties"])

    batch = schemas["ReplicaBatchCreate"]
    assert "source_confirmed" not in batch["properties"]
    assert "composition_id" in batch["properties"]
    assert "generation_strategy" in batch["properties"]
    assert batch.get("additionalProperties") is False
