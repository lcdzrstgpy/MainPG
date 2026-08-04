from __future__ import annotations

import json
import socket
import sqlite3
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.modules.daily_selection.contracts import ApiEvidence  # noqa: E402
from wh_local.modules.daily_selection.criteria import DailySelectionCriteria  # noqa: E402
from wh_local.modules.daily_selection.provider import ProviderCallResult  # noqa: E402
from wh_local.modules.daily_selection.routes import (  # noqa: E402
    DailySelectionActor,
    DailySelectionRouteDependencies,
    register_daily_selection_routes,
)


WORKSPACE_ID = "workspace-e2e"


def _evidence(operation: str) -> ApiEvidence:
    return ApiEvidence(
        provider="fake-1688",
        operation=operation,
        captured_at="2026-08-04T10:00:00+08:00",
    )


def _search_result(offer_id: str, title: str) -> Mapping[str, Any]:
    return {
        "data": {
            "items": [
                {
                    "num_iid": offer_id,
                    "title": title,
                    "detail_url": f"https://detail.1688.com/{offer_id}.html",
                    "pic_url": f"https://images.example.test/{offer_id}/main.jpg",
                    "price": "12.30",
                    "moq": 2,
                    "shop_name": "端到端测试工厂",
                }
            ]
        }
    }


def _detail_result(offer_id: str) -> Mapping[str, Any]:
    return {
        "data": {
            "num_iid": offer_id,
            "item_imgs": [
                f"https://images.example.test/{offer_id}/main.jpg",
                f"https://images.example.test/{offer_id}/gallery.jpg",
            ],
            "detail_images": [
                f"https://images.example.test/{offer_id}/detail.jpg"
            ],
            "properties": {"材质": "ABS"},
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
    }


class FakeProvider:
    """Deterministic acceptance provider; no method can access the network."""

    credential_fingerprint = "e" * 64

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def search_keyword(self, criteria: DailySelectionCriteria) -> ProviderCallResult:
        query = " ".join(criteria.keywords)
        self.calls.append(("keyword", query))
        return ProviderCallResult(
            response=_search_result("keyword-offer", "便携露营灯"),
            audits=(_evidence("item_search"),),
        )

    def search_by_image(self, criteria: DailySelectionCriteria) -> ProviderCallResult:
        reference = criteria.reference_image_url
        assert reference is not None
        self.calls.extend(
            [
                ("reference_download", reference),
                ("upload", "fake-upload-token"),
                ("image_search", "fake-upload-token"),
            ]
        )
        return ProviderCallResult(
            response=_search_result("image-offer", "极简露营灯"),
            audits=(
                _evidence("download_reference_image"),
                _evidence("upload_img"),
                _evidence("item_search_img"),
            ),
        )

    def get_item_detail(self, offer_id: str) -> ProviderCallResult:
        self.calls.append(("detail", offer_id))
        return ProviderCallResult(
            response=_detail_result(offer_id),
            audits=(_evidence("item_get"),),
        )


def _test_client(
    database_path: Path, provider: FakeProvider, *, run_id: str
) -> TestClient:
    app = FastAPI()
    router = APIRouter()
    register_daily_selection_routes(
        router,
        DailySelectionRouteDependencies(
            resolve_actor=lambda: DailySelectionActor(
                actor_id="acceptance-user", workspace_id=WORKSPACE_ID
            ),
            provider_config_resolver=lambda actor: {"profile": "fake-only"},
            provider_factory=lambda config: provider,
            database_path=database_path,
            run_id_factory=lambda: run_id,
        ),
    )
    app.include_router(router)
    return TestClient(app)


def test_keyword_preview_persists_complete_images_skus_and_one_handoff(
    tmp_path: Path, monkeypatch: Any
) -> None:
    def reject_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("end-to-end tests must not access the network")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    monkeypatch.setattr(socket, "getaddrinfo", reject_network)
    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setattr(socket.socket, "connect_ex", reject_network)
    database_path = tmp_path / "daily-selection-keyword.sqlite3"
    provider = FakeProvider()

    with _test_client(database_path, provider, run_id="run-keyword") as client:
        preview = client.post(
            "/desktop/daily-selection/preview",
            json={
                "keywords": ["露营灯"],
                "selection_scope": "exact",
                "target_count": 1,
                "detail_count": 1,
                "max_api_calls": 2,
            },
        )
        assert preview.status_code == 200, preview.text
        run = preview.json()
        candidate = run["candidates"][0]

        assert run["status"] == "completed"
        assert run["run_id"] == "run-keyword"
        assert candidate["main_image_url"].endswith("/main.jpg")
        assert candidate["source_image_urls"] == [
            "https://images.example.test/keyword-offer/main.jpg",
            "https://images.example.test/keyword-offer/gallery.jpg",
        ]
        assert candidate["source_detail_image_urls"] == [
            "https://images.example.test/keyword-offer/detail.jpg"
        ]
        assert candidate["source_variant_records"] == [
            {
                "sku_id": "keyword-offer-red",
                "attributes": {"颜色": "红色"},
                "image_url": "https://images.example.test/keyword-offer/sku-red.jpg",
                "price_cny": "12.30",
                "min_order_quantity": 2,
            }
        ]

        restored = client.get("/desktop/daily-selection/runs/run-keyword")
        assert restored.status_code == 200
        assert restored.json() == run

        first = client.post(
            "/desktop/daily-selection/runs/run-keyword/confirm",
            json={"candidate_ids": [candidate["candidate_id"]]},
        )
        repeated = client.post(
            "/desktop/daily-selection/runs/run-keyword/confirm",
            json={"candidate_ids": [candidate["candidate_id"]]},
        )

    assert first.status_code == 200
    assert repeated.status_code == 200
    assert first.json() == repeated.json()
    assert len(first.json()) == 1
    payload = json.loads(first.json()[0]["payload_json"])
    assert payload["images"] == {
        "main": candidate["main_image_url"],
        "gallery": candidate["source_image_urls"],
        "detail": candidate["source_detail_image_urls"],
        "sku": [candidate["source_variant_records"][0]["image_url"]],
    }
    assert payload["skus"] == candidate["source_variant_records"]
    assert provider.calls == [
        ("keyword", "露营灯"),
        ("detail", "keyword-offer"),
    ]

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "daily_selection_api_budget",
            "daily_selection_provider_budgets",
        } <= tables
        assert connection.execute(
            "SELECT COUNT(*) FROM daily_selection_runs"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM daily_selection_candidates"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM daily_selection_handoffs"
        ).fetchone()[0] == 1
        assert connection.execute(
            """
            SELECT api_calls_used FROM daily_selection_api_budget
            WHERE workspace_id = ? AND provider_fingerprint = ?
            """,
            (WORKSPACE_ID, provider.credential_fingerprint),
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM daily_selection_provider_budgets"
        ).fetchone()[0] == 0


def test_image_preview_uploads_searches_details_persists_and_hands_off_once(
    tmp_path: Path, monkeypatch: Any
) -> None:
    def reject_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("end-to-end tests must not access the network")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    monkeypatch.setattr(socket, "getaddrinfo", reject_network)
    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setattr(socket.socket, "connect_ex", reject_network)
    database_path = tmp_path / "daily-selection-image.sqlite3"
    provider = FakeProvider()
    reference_url = "https://images.example.test/reference.jpg"

    with _test_client(database_path, provider, run_id="run-image") as client:
        preview = client.post(
            "/desktop/daily-selection/preview",
            json={
                "collection_mode": "image",
                "reference_image_url": reference_url,
                "keywords": ["露营灯"],
                "selection_scope": "divergent",
                "target_count": 1,
                "detail_count": 1,
                "max_api_calls": 4,
            },
        )
        assert preview.status_code == 200, preview.text
        run = preview.json()
        candidate = run["candidates"][0]

        assert run["status"] == "completed"
        assert run["run_id"] == "run-image"
        assert run["criteria"]["reference_image_url"] == reference_url
        assert run["metadata"]["search_calls"] == 0
        assert run["metadata"]["image_search_calls"] == 1
        assert run["metadata"]["detail_calls"] == 1
        assert run["metadata"]["api_calls"] == 4
        assert run["metadata"]["derived_image_terms"] == ["极简露营灯"]
        assert candidate["main_image_url"].endswith("/main.jpg")
        assert candidate["source_image_urls"][1].endswith("/gallery.jpg")
        assert candidate["source_detail_image_urls"][0].endswith("/detail.jpg")
        assert candidate["source_variant_records"][0]["sku_id"] == "image-offer-red"
        assert candidate["source_variant_records"][0]["image_url"].endswith(
            "/sku-red.jpg"
        )

        restored = client.get("/desktop/daily-selection/runs/run-image")
        assert restored.status_code == 200
        assert restored.json() == run

        first = client.post(
            "/desktop/daily-selection/runs/run-image/confirm",
            json={"candidate_ids": [candidate["candidate_id"]]},
        )
        repeated = client.post(
            "/desktop/daily-selection/runs/run-image/confirm",
            json={"candidate_ids": [candidate["candidate_id"]]},
        )

    assert first.status_code == 200
    assert repeated.status_code == 200
    assert first.json() == repeated.json()
    assert len(first.json()) == 1
    handoff_payload = json.loads(first.json()[0]["payload_json"])
    assert handoff_payload["images"]["main"] == candidate["main_image_url"]
    assert handoff_payload["images"]["gallery"] == candidate["source_image_urls"]
    assert handoff_payload["images"]["detail"] == candidate[
        "source_detail_image_urls"
    ]
    assert handoff_payload["images"]["sku"] == [
        candidate["source_variant_records"][0]["image_url"]
    ]
    assert handoff_payload["skus"] == candidate["source_variant_records"]
    assert provider.calls == [
        ("reference_download", reference_url),
        ("upload", "fake-upload-token"),
        ("image_search", "fake-upload-token"),
        ("detail", "image-offer"),
    ]

    with sqlite3.connect(database_path) as connection:
        candidate_snapshot = connection.execute(
            "SELECT raw_candidate_json FROM daily_selection_candidates"
        ).fetchone()[0]
        assert json.loads(candidate_snapshot)["source_variant_records"] == candidate[
            "source_variant_records"
        ]
        assert connection.execute(
            "SELECT COUNT(*) FROM daily_selection_handoffs"
        ).fetchone()[0] == 1
        assert connection.execute(
            """
            SELECT api_calls_used FROM daily_selection_api_budget
            WHERE workspace_id = ? AND provider_fingerprint = ?
            """,
            (WORKSPACE_ID, provider.credential_fingerprint),
        ).fetchone()[0] == 4
        assert connection.execute(
            "SELECT COUNT(*) FROM daily_selection_provider_budgets"
        ).fetchone()[0] == 0
