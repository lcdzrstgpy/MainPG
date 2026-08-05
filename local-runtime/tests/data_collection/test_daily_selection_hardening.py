from __future__ import annotations

import sys
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.data_collection.contracts import DailySelectionCandidate  # noqa: E402
from wh_local.data_collection.criteria import (  # noqa: E402
    DailySelectionCriteria,
    DailySelectionCriteriaError,
)
from wh_local.data_collection.repository import DailySelectionRepository  # noqa: E402
from wh_local.data_collection.routes import (  # noqa: E402
    DailySelectionActor,
    DailySelectionRouteDependencies,
    register_daily_selection_routes,
)
from wh_local.data_collection.public_image_fetch import FetchedPublicImage  # noqa: E402
from wh_local.data_collection.image_cache import PublicDailySelectionImageCache  # noqa: E402
from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets  # noqa: E402
from wh_local.modules.product_processing.infrastructure.database import create_database  # noqa: E402
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository  # noqa: E402
from wh_local.modules.product_processing.service import ProductProcessingService  # noqa: E402


ACTOR = DailySelectionActor(actor_id="hardening-user", workspace_id="hardening-workspace")


def _candidate(*, candidate_id: str = "candidate-1", status: str = "candidate", risks: tuple[str, ...] = ()) -> DailySelectionCandidate:
    return DailySelectionCandidate(
        candidate_id=candidate_id,
        offer_id="offer-1",
        source_platform="1688",
        source_url="https://detail.1688.com/offer/offer-1.html",
        source_title="安全测试商品",
        main_image_url="https://images.example.test/offer-1.jpg",
        status=status,
        risk_tags=risks,
    )


@contextmanager
def _client(database_path: Path, *, handoff_consumer=None, provider_factory=None) -> Iterator[TestClient]:
    router = APIRouter()
    register_daily_selection_routes(
        router,
        DailySelectionRouteDependencies(
            resolve_actor=lambda: ACTOR,
            provider_config_resolver=lambda _actor: {"provider": "test"},
            provider_factory=provider_factory or (lambda _config: object()),
            database_path=database_path,
            handoff_consumer=handoff_consumer,
        ),
    )
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client


def _save_run(database_path: Path, candidate: DailySelectionCandidate) -> None:
    DailySelectionRepository(database_path).save_run(
        workspace_id=ACTOR.workspace_id,
        run_id="run-1",
        status="completed",
        candidates=(candidate,),
    )


@pytest.mark.parametrize(
    ("status", "risks"),
    (("filtered", ()), ("rejected", ()), ("candidate", ("ip",))),
)
def test_confirm_rejects_non_confirmable_candidates(
    tmp_path: Path, status: str, risks: tuple[str, ...]
) -> None:
    database_path = tmp_path / "workbench.sqlite3"
    _save_run(database_path, _candidate(status=status, risks=risks))

    with _client(database_path) as client:
        response = client.post(
            "/desktop/daily-selection/runs/run-1/confirm",
            json={"candidate_ids": ["candidate-1"]},
        )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "CANDIDATE_NOT_CONFIRMABLE"


def test_confirm_consumes_handoff_and_marks_it_consumed(tmp_path: Path) -> None:
    database_path = tmp_path / "workbench.sqlite3"
    _save_run(database_path, _candidate())
    received = []

    def consume(handoffs):
        received.extend(handoffs)
        return {"consumer_status": "consumed"}

    with _client(database_path, handoff_consumer=consume) as client:
        response = client.post(
            "/desktop/daily-selection/runs/run-1/confirm",
            json={"candidate_ids": ["candidate-1"]},
        )

    assert response.status_code == 200, response.text
    assert response.json()[0]["status"] == "consumed"
    assert [handoff.candidate_id for handoff in received] == ["candidate-1"]


def test_confirmation_reuses_immediately_ingressed_api_draft(tmp_path: Path) -> None:
    database_path = tmp_path / "workbench.sqlite3"
    candidate = _candidate()
    _save_run(database_path, candidate)
    product_processing = ProductProcessingService(
        ProductProcessingRepository(create_database(f"sqlite:///{database_path}")),
        ProductProcessingAssets(tmp_path / "product-processing-assets"),
    )
    product_processing.create_draft(
        {
            **candidate.model_dump(mode="json"),
            "source_type": "onebound_api",
            "selection_run_id": "run-1",
            "collection_mode": "keyword",
        },
        selection_run_id="run-1",
        workspace_id=ACTOR.workspace_id,
    )

    with _client(database_path) as client:
        response = client.post(
            "/desktop/daily-selection/runs/run-1/confirm",
            json={"candidate_ids": ["candidate-1"]},
        )

    assert response.status_code == 200, response.text
    assert response.json()[0]["status"] == "consumed"
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT source_type, candidate_id, selection_run_id
            FROM product_processing_drafts
            WHERE workspace_id = ?
            """,
            (ACTOR.workspace_id,),
        ).fetchone()
    assert row == ("onebound_api", "candidate-1", "run-1")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM product_processing_drafts WHERE workspace_id = ?",
            (ACTOR.workspace_id,),
        ).fetchone() == (1,)


def test_failed_product_processing_leaves_a_retryable_pending_handoff(tmp_path: Path) -> None:
    database_path = tmp_path / "workbench.sqlite3"
    _save_run(database_path, _candidate())

    def unavailable(_handoffs):
        raise RuntimeError("product processing is offline")

    with _client(database_path, handoff_consumer=unavailable) as client:
        response = client.post(
            "/desktop/daily-selection/runs/run-1/confirm",
            json={"candidate_ids": ["candidate-1"]},
        )

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "PRODUCT_PROCESSING_UNAVAILABLE"
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT status FROM daily_selection_handoffs"
        ).fetchone() == ("pending",)


def test_missing_provider_configuration_returns_safe_503(tmp_path: Path) -> None:
    database_path = tmp_path / "workbench.sqlite3"

    def missing_provider(_config):
        raise ValueError("api_secret is required")

    with _client(database_path, provider_factory=missing_provider) as client:
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

    assert response.status_code == 503, response.text
    assert response.json() == {
        "detail": {
            "code": "PROVIDER_NOT_CONFIGURED",
            "message": "1688 采集服务尚未配置",
        }
    }


def test_criteria_caps_result_and_detail_work() -> None:
    with pytest.raises(DailySelectionCriteriaError, match="target_count"):
        DailySelectionCriteria(keywords=("露营灯",), target_count=101)

    with pytest.raises(DailySelectionCriteriaError, match="detail_count"):
        DailySelectionCriteria(
            collection_mode="image",
            reference_image_url="https://images.example.test/reference.jpg",
            target_count=1,
            detail_count=58,
            max_api_calls=60,
        )


def test_public_image_cache_reuses_only_safely_fetched_bytes() -> None:
    fetched_urls: list[str] = []
    checked_targets: list[tuple[str, str | None]] = []

    def fetch(url: str) -> FetchedPublicImage:
        fetched_urls.append(url)
        return FetchedPublicImage(
            content=b"\x89PNG\r\n\x1a\nimage",
            media_type="image/png",
            final_url="https://cdn.example.test/offer-1.png",
        )

    cache = PublicDailySelectionImageCache(fetcher=fetch, max_entries=2, max_total_bytes=1024)
    first = cache.get_or_fetch(
        workspace_id=ACTOR.workspace_id,
        url="https://images.example.test/offer-1.png",
        validate_target=lambda url, address: checked_targets.append((url, address)),
    )
    second = cache.get_or_fetch(
        workspace_id=ACTOR.workspace_id,
        url="https://images.example.test/offer-1.png",
        validate_target=lambda url, address: checked_targets.append((url, address)),
    )

    assert fetched_urls == ["https://images.example.test/offer-1.png"]
    assert first == second
    assert first.media_type == "image/png"
    assert checked_targets == [
        ("https://images.example.test/offer-1.png", None),
        ("https://cdn.example.test/offer-1.png", None),
        ("https://images.example.test/offer-1.png", None),
    ]


def test_run_list_is_bounded_and_supports_offset(tmp_path: Path) -> None:
    database_path = tmp_path / "workbench.sqlite3"
    repository = DailySelectionRepository(database_path)
    for index in range(3):
        repository.save_run(
            workspace_id=ACTOR.workspace_id,
            run_id=f"run-{index}",
            status="completed",
            candidates=(_candidate(candidate_id=f"candidate-{index}"),),
            created_at=f"2026-08-05T0{index}:00:00+00:00",
        )

    with _client(database_path) as client:
        response = client.get("/desktop/daily-selection/runs", params={"limit": 2, "offset": 1})

    assert response.status_code == 200, response.text
    assert [item["run_id"] for item in response.json()] == ["run-1", "run-0"]


def test_host_app_registers_the_secured_data_collection_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    monkeypatch.setenv("WH_LOCAL_DATABASE_PATH", str(database_path))
    monkeypatch.setenv("WH_LOCAL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WH_LOCAL_DEV_ADMIN_TOKEN", "test-admin-token")
    _save_host_run = DailySelectionRepository(database_path)
    candidate = _candidate()
    _save_host_run.save_run(
        workspace_id="default",
        run_id="host-run",
        status="completed",
        candidates=(candidate,),
    )

    from wh_local.app.main import create_app

    app = create_app(database_path)
    product_processing = ProductProcessingService(
        ProductProcessingRepository(create_database(f"sqlite:///{database_path}")),
        ProductProcessingAssets(tmp_path / "product-processing-assets"),
    )
    product_processing.create_draft(
        {
            **candidate.model_dump(mode="json"),
            "source_type": "onebound_api",
            "selection_run_id": "host-run",
            "collection_mode": "keyword",
        },
        selection_run_id="host-run",
        workspace_id="default",
    )

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer test-admin-token"}
        response = client.post(
            "/desktop/daily-selection/runs/host-run/confirm",
            headers=headers,
            json={"candidate_ids": ["candidate-1"]},
        )
        schema = client.get("/openapi.json").json()

    assert response.status_code == 200, response.text
    assert response.json()[0]["status"] == "consumed"
    assert "/desktop/daily-selection/image" in schema["paths"]
