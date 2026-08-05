from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def _quote_result() -> dict[str, object]:
    return {
        "records": [
            {
                "url": "https://seller.temu.com/bargain-no-bom/batch/info/query",
                "method": "POST",
                "status": 200,
                "capturedAt": "2026-08-05T00:00:00Z",
                "responseJson": {
                    "priceReviewItemList": [
                        {
                            "skcId": "SKC-1",
                            "productName": "保留商品",
                            "skuInfoList": [
                                {
                                    "skuId": "SKU-1",
                                    "goodsId": "1001",
                                    "originalSupplyPrice": "22.00",
                                    "suggestSupplyPrice": "19.90",
                                    "imageUrl": "https://img.example/1001.jpg",
                                }
                            ],
                        },
                        {
                            "skcId": "SKC-2",
                            "productName": "拒绝商品",
                            "skuInfoList": [
                                {
                                    "skuId": "SKU-2",
                                    "goodsId": "1002",
                                    "originalSupplyPrice": "32.00",
                                    "suggestSupplyPrice": "29.90",
                                    "imageUrl": "https://img.example/1002.jpg",
                                }
                            ],
                        },
                    ]
                },
            }
        ]
    }


def test_connected_plugin_quote_decision_and_retained_source_flow(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    monkeypatch.setenv("WH_LOCAL_DATABASE_PATH", str(database_path))
    monkeypatch.setenv("WH_LOCAL_DATA_DIR", str(tmp_path / "data"))
    from wh_local.app.main import create_app

    app = create_app(database_path)
    client = TestClient(app)
    auth = {"Authorization": "Bearer dev-admin-token"}

    for path in ("/plugin/connect", "/plugin/poll", "/plugin/result"):
        routes = [
            route
            for route in app.routes
            if getattr(route, "path", None) == path and "POST" in getattr(route, "methods", set())
        ]
        assert len(routes) == 1, [(route.endpoint.__module__, route.endpoint.__name__) for route in routes]

    session_response = client.post(
        "/plugin/connect",
        headers=auth,
        json={
            "capabilities": {
                "temu_price_quote_discovery": True,
                "source_browser_image_search": True,
            }
        },
    )
    assert session_response.status_code == 200, session_response.text
    session = session_response.json()

    quote_command_response = client.post(
        "/api/v1/price-verification/quote-runs",
        headers=auth,
        json={
            "session_id": str(session["session_id"]),
            "idempotency_key": "quote-http-1",
            "payload": {},
        },
    )
    assert quote_command_response.status_code == 200, quote_command_response.text
    quote_command = quote_command_response.json()
    polled = client.post(
        "/plugin/poll", json={"session_token": session["session_token"], "limit": 10}
    ).json()["commands"]
    assert [item["command_type"] for item in polled] == ["temu_price_quote_discovery"]
    result_response = client.post(
        "/plugin/result",
        json={
            "session_token": session["session_token"],
            "command_id": int(quote_command["command_id"]),
            "status": "succeeded",
            "result": _quote_result(),
        },
    )
    assert result_response.status_code == 200, result_response.text

    quote_run_response = client.post(
        "/api/v1/price-verification/quote-runs",
        headers=auth,
        json={"command_id": quote_command["command_id"]},
    )
    assert quote_run_response.status_code == 200, quote_run_response.text
    quote_run_id = quote_run_response.json()["run_id"]
    quote_items = client.get(
        f"/api/v1/price-verification/quote-runs/{quote_run_id}/items", headers=auth
    ).json()["quotes"]
    quote_keys = {item["sku_id"]: item["quote_key"] for item in quote_items}

    for sku_id, decision in (("SKU-1", "retained"), ("SKU-2", "rejected")):
        response = client.post(
            f"/api/v1/price-verification/quote-runs/{quote_run_id}/decisions",
            headers=auth,
            json={"quote_key": quote_keys[sku_id], "decision": decision, "note": "人工核价"},
        )
        assert response.status_code == 200, response.text

    sourcing_response = client.post(
        "/api/v1/price-verification/sourcing-runs",
        headers=auth,
        json={
            "session_id": str(session["session_id"]),
            "quote_run_id": quote_run_id,
            "idempotency_key": "source-http-1",
            "max_quotes": 50,
        },
    )
    assert sourcing_response.status_code == 200, sourcing_response.text
    source_polled = client.post(
        "/plugin/poll", json={"session_token": session["session_token"], "limit": 10}
    ).json()["commands"]

    assert [item["command_type"] for item in source_polled] == ["source_browser_image_search"]
    tasks = source_polled[0]["payload"]["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["quote_key"] == quote_keys["SKU-1"]
    assert tasks[0]["official_link_url"].endswith("goods_id=1001")
