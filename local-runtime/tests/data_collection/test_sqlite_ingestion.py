from __future__ import annotations

import json
import socket
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.data_collection.contracts import ApiEvidence  # noqa: E402
from wh_local.data_collection.criteria import DailySelectionCriteria  # noqa: E402
from wh_local.data_collection.provider import ProviderCallResult  # noqa: E402
from wh_local.data_collection.routes import (  # noqa: E402
    DailySelectionActor,
    DailySelectionRouteDependencies,
    register_daily_selection_routes,
)
from wh_local.db import init_db  # noqa: E402


ACTOR_ID = "ingestion-user"
WORKSPACE_ID = "ingestion-workspace"


def _evidence(
    operation: str,
    captured_at: str = "2026-08-04T10:00:00+08:00",
) -> ApiEvidence:
    return ApiEvidence(
        provider="fake-1688",
        operation=operation,
        captured_at=captured_at,
    )


def _search_item(offer_id: str) -> dict[str, Any]:
    return {
        "num_iid": offer_id,
        "title": f"测试商品 {offer_id}",
        "detail_url": f"https://detail.1688.com/{offer_id}.html",
        "pic_url": f"https://images.example.test/{offer_id}/main.jpg",
        "price": "12.30",
        "moq": 2,
        "shop_name": "SQLite 联调测试工厂",
    }


def _detail_item(offer_id: str) -> dict[str, Any]:
    return {
        "num_iid": offer_id,
        "title": f"测试商品 {offer_id}",
        "main_image_url": f"https://images.example.test/{offer_id}/main.jpg",
        "item_imgs": [
            f"https://images.example.test/{offer_id}/main.jpg",
            f"https://images.example.test/{offer_id}/gallery.jpg",
        ],
        "detail_images": [
            f"https://images.example.test/{offer_id}/detail.jpg",
        ],
        "properties": {"材质": "ABS"},
        "price": "12.30",
        "moq": 2,
        "skus": [
            {
                "sku_id": f"{offer_id}-red",
                "attributes": {"颜色": "红色"},
                "image_url": f"https://images.example.test/{offer_id}/sku-red.jpg",
                "price": "12.30",
                "moq": 2,
            }
        ],
    }


class FakeProvider:
    credential_fingerprint = "f" * 64

    def __init__(self) -> None:
        self.keyword_searches = 0

    def search_keyword(
        self, criteria: DailySelectionCriteria
    ) -> ProviderCallResult:
        self.keyword_searches += 1
        return ProviderCallResult(
            response={"data": {"items": [_search_item("keyword-offer")]}},
            audits=(
                _evidence(
                    "item_search",
                    f"2026-08-04T10:00:0{self.keyword_searches}+08:00",
                ),
            ),
        )

    def search_by_image(
        self, criteria: DailySelectionCriteria
    ) -> ProviderCallResult:
        return ProviderCallResult(
            response={"data": {"items": [_search_item("image-offer")]}},
            audits=(
                _evidence("download_reference_image"),
                _evidence("upload_img"),
                _evidence("item_search_img"),
            ),
        )

    def get_item_detail(self, offer_id: str) -> ProviderCallResult:
        return ProviderCallResult(
            response={"data": _detail_item(offer_id)},
            audits=(_evidence("item_get"),),
        )


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("SQLite ingestion tests must not access the network")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    monkeypatch.setattr(socket, "getaddrinfo", reject_network)
    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setattr(socket.socket, "connect_ex", reject_network)


@contextmanager
def _client(database_path: Path, run_ids: Iterator[str]) -> Iterator[TestClient]:
    init_db(database_path)
    provider = FakeProvider()
    router = APIRouter()
    register_daily_selection_routes(
        router,
        DailySelectionRouteDependencies(
            resolve_actor=lambda: DailySelectionActor(
                actor_id=ACTOR_ID,
                workspace_id=WORKSPACE_ID,
            ),
            provider_config_resolver=lambda actor: {"profile": "fake-only"},
            provider_factory=lambda config: provider,
            database_path=database_path,
            run_id_factory=lambda: next(run_ids),
        ),
    )
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client


def _stored_run(database_path: Path, run_id: str) -> tuple[Any, ...]:
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT workspace_id, run_id, status, candidate_count
            FROM daily_selection_runs
            WHERE workspace_id = ? AND run_id = ?
            """,
            (WORKSPACE_ID, run_id),
        ).fetchone()
    assert row is not None
    return row


def _stored_candidate(database_path: Path, run_id: str) -> tuple[Any, ...]:
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT candidate_id, offer_id, source_platform, source_url,
                   source_title, price_cny, raw_candidate_json
            FROM daily_selection_candidates
            WHERE workspace_id = ? AND run_id = ?
            """,
            (WORKSPACE_ID, run_id),
        ).fetchone()
    assert row is not None
    return row


def _stored_draft_source_types(database_path: Path) -> list[str]:
    with sqlite3.connect(database_path) as connection:
        return [
            row[0]
            for row in connection.execute(
                """SELECT source_type FROM product_processing_drafts
                WHERE workspace_id = ? ORDER BY id""",
                (WORKSPACE_ID,),
            )
        ]


def _valid_temu_product() -> dict[str, Any]:
    return {
        "platform": "temu",
        "product_id": "temu-123",
        "product_link": "https://www.temu.com/goods.html?goods_id=123&utm_source=test",
        "title": "Temu 有效商品",
        "price": "19.90",
        "image_urls": ["https://images.example.test/temu-123/main.jpg"],
    }


def _assert_complete_candidate_snapshot(
    database_path: Path,
    *,
    run_id: str,
    offer_id: str,
) -> None:
    candidate_row = _stored_candidate(database_path, run_id)
    assert candidate_row[0:6] == (
        f"1688:{offer_id}",
        offer_id,
        "1688",
        f"https://detail.1688.com/{offer_id}.html",
        f"测试商品 {offer_id}",
        "12.30",
    )
    snapshot = json.loads(candidate_row[6])
    assert snapshot["source_variant_records"][0]["sku_id"] == f"{offer_id}-red"
    assert snapshot["source_detail_image_urls"] == [
        f"https://images.example.test/{offer_id}/detail.jpg"
    ]


def test_keyword_collection_persists_normalized_candidate_in_shared_sqlite(
    tmp_path: Path,
    no_network: None,
) -> None:
    database_path = tmp_path / "workbench.sqlite3"
    with _client(database_path, iter(("run-keyword",))) as client:
        response = client.post(
            "/desktop/daily-selection/preview",
            json={
                "keywords": ["露营灯"],
                "selection_scope": "exact",
                "target_count": 1,
                "detail_count": 1,
                "max_api_calls": 2,
            },
        )

    assert response.status_code == 200, response.text
    assert _stored_run(database_path, "run-keyword") == (
        WORKSPACE_ID,
        "run-keyword",
        "completed",
        1,
    )
    _assert_complete_candidate_snapshot(
        database_path,
        run_id="run-keyword",
        offer_id="keyword-offer",
    )


def test_image_collection_persists_normalized_candidate_in_shared_sqlite(
    tmp_path: Path,
    no_network: None,
) -> None:
    database_path = tmp_path / "workbench.sqlite3"
    with _client(database_path, iter(("run-image",))) as client:
        response = client.post(
            "/desktop/daily-selection/preview",
            json={
                "collection_mode": "image",
                "reference_image_url": "https://images.example.test/reference.jpg",
                "keywords": ["露营灯"],
                "selection_scope": "exact",
                "target_count": 1,
                "detail_count": 1,
                "max_api_calls": 4,
            },
        )

    assert response.status_code == 200, response.text
    assert _stored_run(database_path, "run-image") == (
        WORKSPACE_ID,
        "run-image",
        "completed",
        1,
    )
    _assert_complete_candidate_snapshot(
        database_path,
        run_id="run-image",
        offer_id="image-offer",
    )


def test_similar_link_collection_persists_normalized_candidate_in_shared_sqlite(
    tmp_path: Path,
    no_network: None,
) -> None:
    database_path = tmp_path / "workbench.sqlite3"
    with _client(database_path, iter(("run-similar",))) as client:
        response = client.post(
            "/desktop/daily-selection/preview-from-1688-link",
            json={
                "source_url": "https://detail.1688.com/offer/123456.html",
                "selection_scope": "exact",
                "target_count": 1,
                "detail_count": 1,
                "max_api_calls": 4,
            },
        )

    assert response.status_code == 200, response.text
    assert _stored_run(database_path, "run-similar") == (
        WORKSPACE_ID,
        "run-similar",
        "completed",
        1,
    )
    _assert_complete_candidate_snapshot(
        database_path,
        run_id="run-similar",
        offer_id="image-offer",
    )
    assert _stored_draft_source_types(database_path) == ["onebound_api"]
    with sqlite3.connect(database_path) as connection:
        metadata_json = connection.execute(
            """
            SELECT metadata_json FROM daily_selection_runs
            WHERE workspace_id = ? AND run_id = ?
            """,
            (WORKSPACE_ID, "run-similar"),
        ).fetchone()[0]
    assert json.loads(metadata_json)["source_link"]["offer_id"] == "123456"


def test_plugin_product_capture_creates_manual_draft(
    tmp_path: Path,
    no_network: None,
) -> None:
    database_path = tmp_path / "workbench.sqlite3"
    with _client(database_path, iter(())) as client:
        session = client.post("/desktop/data-collection/plugin-sessions", json={}).json()
        response = client.post(
            "/plugin/product-capture/draft",
            json={"session_token": session["session_token"], "product": _valid_temu_product()},
        )

    assert response.status_code == 200, response.text
    assert _stored_draft_source_types(database_path) == ["web_manual_capture"]


def test_successful_temu_link_result_creates_manual_draft(
    tmp_path: Path,
    no_network: None,
) -> None:
    database_path = tmp_path / "workbench.sqlite3"
    with _client(database_path, iter(())) as client:
        session = client.post(
            "/desktop/data-collection/plugin-sessions", json={"temu_link_capture": True}
        ).json()
        queued = client.post(
            "/desktop/data-collection/temu-link/collect",
            json={
                "session_id": session["session_id"],
                "source_url": "https://www.temu.com/goods.html?goods_id=123",
            },
        ).json()
        response = client.post(
            "/plugin/result",
            json={
                "session_token": session["session_token"],
                "command_id": queued["command_id"],
                "status": "succeeded",
                "result": {"product": _valid_temu_product()},
            },
        )

    assert response.status_code == 200, response.text
    assert _stored_draft_source_types(database_path) == ["web_manual_capture"]


def test_preview_immediately_creates_api_drafts(
    tmp_path: Path,
    no_network: None,
) -> None:
    database_path = tmp_path / "workbench.sqlite3"
    with _client(database_path, iter(("run-api-ingress",))) as client:
        response = client.post(
            "/desktop/daily-selection/preview",
            json={
                "keywords": ["露营灯"],
                "selection_scope": "exact",
                "target_count": 1,
                "detail_count": 1,
                "max_api_calls": 2,
            },
        )

    assert response.status_code == 200, response.text
    assert _stored_draft_source_types(database_path) == ["onebound_api"]
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            """SELECT selection_run_id, raw_payload_json
            FROM product_processing_drafts WHERE workspace_id = ?""",
            (WORKSPACE_ID,),
        ).fetchone()
    assert row is not None
    assert row[0] == "run-api-ingress"
    payload = json.loads(row[1])
    assert payload["collection_mode"] == "keyword"
    assert payload["source_evidence"][0]["operation"] == "item_search"


def test_repeated_preview_refreshes_existing_api_draft_with_current_run_provenance(
    tmp_path: Path,
    no_network: None,
) -> None:
    database_path = tmp_path / "workbench.sqlite3"
    with _client(database_path, iter(("run-first", "run-second"))) as client:
        first = client.post(
            "/desktop/daily-selection/preview",
            json={
                "keywords": ["第一次关键词"],
                "selection_scope": "exact",
                "target_count": 1,
                "detail_count": 1,
                "max_api_calls": 2,
            },
        )
        second = client.post(
            "/desktop/daily-selection/preview",
            json={
                "keywords": ["第二次关键词"],
                "selection_scope": "exact",
                "target_count": 1,
                "detail_count": 1,
                "max_api_calls": 2,
            },
        )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT id, selection_run_id, raw_payload_json
            FROM product_processing_drafts
            WHERE workspace_id = ?
            """,
            (WORKSPACE_ID,),
        ).fetchall()
    assert len(rows) == 1
    _, selection_run_id, raw_payload_json = rows[0]
    assert selection_run_id == "run-second"
    payload = json.loads(raw_payload_json)
    assert payload["collection_mode"] == "keyword"
    assert payload["selection_criteria"]["keywords"] == ["第二次关键词"]
    assert payload["source_evidence"][0]["captured_at"] == "2026-08-04T10:00:02+08:00"


def test_temu_plugin_result_persists_sanitized_json_in_shared_sqlite(
    tmp_path: Path,
    no_network: None,
) -> None:
    database_path = tmp_path / "workbench.sqlite3"
    with _client(database_path, iter(())) as client:
        session_response = client.post(
            "/desktop/data-collection/plugin-sessions",
            json={"temu_link_capture": True},
        )
        assert session_response.status_code == 200, session_response.text
        session = session_response.json()

        queued_response = client.post(
            "/desktop/data-collection/temu-link/collect",
            json={
                "session_id": session["session_id"],
                "source_url": "https://www.temu.com/goods.html?goods_id=123",
            },
        )
        assert queued_response.status_code == 200, queued_response.text
        queued = queued_response.json()

        poll_response = client.get(
            "/desktop/data-collection/plugin/poll",
            params={"session_token": session["session_token"]},
        )
        assert poll_response.status_code == 200, poll_response.text
        assert poll_response.json()[0]["status"] == "sent"

        result_response = client.post(
            "/desktop/data-collection/plugin/results",
            json={
                "session_token": session["session_token"],
                "command_id": queued["command_id"],
                "status": "succeeded",
                "result": {
                    "source_url": "https://www.temu.com/goods.html?goods_id=123",
                    "title": "Temu 测试商品",
                    "price": "19.90",
                    "currency": "CNY",
                    "authorization": "Bearer must-not-persist",
                },
            },
        )

    assert result_response.status_code == 200, result_response.text
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT status, result_json
            FROM data_collection_plugin_commands
            WHERE id = ?
            """,
            (queued["command_id"],),
        ).fetchone()
    assert row is not None
    assert row[0] == "succeeded"
    stored = json.loads(row[1])
    assert stored["title"] == "Temu 测试商品"
    assert stored["price"] == "19.90"
    assert "authorization" not in stored
    assert "must-not-persist" not in row[1]
