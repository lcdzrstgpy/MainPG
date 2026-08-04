from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.data_collection.budget import BudgetState  # noqa: E402
from wh_local.data_collection.contracts import (  # noqa: E402
    ApiEvidence,
    DailySelectionCandidate,
)
from wh_local.data_collection.criteria import DailySelectionCriteria  # noqa: E402
from wh_local.data_collection.provider import ProviderCallResult  # noqa: E402
from wh_local.data_collection.repository import DailySelectionRepository  # noqa: E402
from wh_local.data_collection.routes import (  # noqa: E402
    CachedDailySelectionImage,
    DailySelectionActor,
    DailySelectionRouteDependencies,
    register_daily_selection_routes,
)
from wh_local.data_collection.service import (  # noqa: E402
    DailySelectionImageAccessDenied,
    validate_public_image_target,
)


def _audit(operation: str) -> ApiEvidence:
    return ApiEvidence(
        provider="fake-1688",
        operation=operation,
        captured_at="2026-08-04T09:00:00+08:00",
    )


class FakeProvider:
    """Deterministic provider used by TestClient; it never opens a socket."""

    credential_fingerprint = "a" * 64

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def search_keyword(self, criteria: DailySelectionCriteria) -> ProviderCallResult:
        query = " ".join(criteria.keywords)
        self.calls.append(("search", query))
        return ProviderCallResult(
            response={
                "data": {
                    "items": [
                        {
                            "num_iid": "offer-1",
                            "title": "便携露营灯",
                            "detail_url": "https://detail.1688.com/offer-1.html",
                            "pic_url": "https://images.example.test/main.jpg",
                            "price": "12.30",
                            "moq": "2",
                            "shop_name": "测试工厂",
                        }
                    ]
                }
            },
            audits=(_audit("item_search"),),
        )

    def search_by_image(self, criteria: DailySelectionCriteria) -> ProviderCallResult:
        raise AssertionError("keyword preview must not perform image search")

    def get_item_detail(self, offer_id: str) -> ProviderCallResult:
        self.calls.append(("detail", offer_id))
        return ProviderCallResult(
            response={
                "data": {
                    "num_iid": offer_id,
                    "item_imgs": [
                        "https://images.example.test/main.jpg",
                        "https://images.example.test/gallery.jpg",
                    ],
                    "detail_images": ["https://images.example.test/detail.jpg"],
                    "properties": {"材质": "ABS"},
                    "skus": [
                        {
                            "sku_id": "sku-red",
                            "attributes": {"颜色": "红色"},
                            "image_url": "https://images.example.test/sku-red.jpg",
                            "price": "12.30",
                            "moq": 2,
                        }
                    ],
                    "shop_name": "测试工厂",
                }
            },
            audits=(_audit("item_get"),),
        )


class InMemoryBudget:
    """Budget double keeps route tests independent from task 6 table naming."""

    def __init__(self) -> None:
        self.used = 0

    def state(
        self,
        *,
        workspace_id: str,
        provider_fingerprint: str,
        max_api_calls: int,
        now: object | None = None,
    ) -> BudgetState:
        return self._state(workspace_id, provider_fingerprint, max_api_calls, False)

    def reserve(
        self,
        *,
        workspace_id: str,
        provider_fingerprint: str,
        max_api_calls: int,
        api_calls: int = 1,
        now: object | None = None,
    ) -> BudgetState:
        granted = self.used + api_calls <= max_api_calls
        if granted:
            self.used += api_calls
        return self._state(workspace_id, provider_fingerprint, max_api_calls, granted)

    def release(
        self,
        *,
        workspace_id: str,
        provider_fingerprint: str,
        max_api_calls: int,
        api_calls: int,
        now: object | None = None,
    ) -> BudgetState:
        self.used = max(self.used - api_calls, 0)
        return self._state(workspace_id, provider_fingerprint, max_api_calls, False)

    def _state(
        self,
        workspace_id: str,
        provider_fingerprint: str,
        max_api_calls: int,
        reservation_granted: bool,
    ) -> BudgetState:
        return BudgetState(
            allowed=self.used < max_api_calls,
            workspace_id=workspace_id,
            provider_fingerprint=provider_fingerprint,
            shanghai_date="2026-08-04",
            api_calls_limit=max_api_calls,
            api_calls_used=self.used,
            api_calls_remaining=max(max_api_calls - self.used, 0),
            reservation_granted=reservation_granted,
        )


class FakeImageCache:
    def __init__(self, *, redirect_url: str | None = None) -> None:
        self.redirect_url = redirect_url
        self.calls: list[str] = []

    def get_or_fetch(
        self,
        *,
        workspace_id: str,
        url: str,
        validate_target: Callable[[str, str | None], None],
    ) -> CachedDailySelectionImage:
        self.calls.append(url)
        validate_target(url, "93.184.216.34")
        final_url = self.redirect_url or url
        if self.redirect_url is not None:
            validate_target(self.redirect_url, "127.0.0.1")
        return CachedDailySelectionImage(
            content=b"fake-jpeg",
            media_type="image/jpeg",
            final_url=final_url,
            resolved_address="93.184.216.34",
        )


@pytest.fixture
def route_app() -> tuple[
    TestClient,
    DailySelectionRepository,
    FakeProvider,
    dict[str, DailySelectionActor],
    FakeImageCache,
]:
    repository = DailySelectionRepository(":memory:")
    provider = FakeProvider()
    actor_state = {
        "actor": DailySelectionActor(actor_id="user-a", workspace_id="workspace-a")
    }
    image_cache = FakeImageCache()

    def resolve_actor() -> DailySelectionActor:
        return actor_state["actor"]

    dependencies = DailySelectionRouteDependencies(
        resolve_actor=resolve_actor,
        provider_config_resolver=lambda actor: {"profile": "test"},
        provider_factory=lambda config: provider,
        repository=repository,
        budget=InMemoryBudget(),
        image_cache=image_cache,
        run_id_factory=lambda: "run-1",
    )
    app = FastAPI()
    router = APIRouter()
    register_daily_selection_routes(router, dependencies)
    app.include_router(router)
    with TestClient(app) as client:
        yield client, repository, provider, actor_state, image_cache
    repository.close()


def _preview(client: TestClient) -> dict[str, Any]:
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
    return response.json()


def test_preview_collects_scores_persists_and_returns_decimal_snapshot(
    route_app: tuple[TestClient, DailySelectionRepository, FakeProvider, dict[str, DailySelectionActor], FakeImageCache],
) -> None:
    client, repository, provider, _, _ = route_app

    payload = _preview(client)

    assert payload["run_id"] == "run-1"
    assert payload["workspace_id"] == "workspace-a"
    assert payload["status"] == "completed"
    assert payload["candidate_count"] == 1
    assert payload["candidates"][0]["candidate_id"] == "1688:offer-1"
    assert payload["candidates"][0]["price_cny"] == "12.30"
    assert payload["candidates"][0]["selection_score"] == "100"
    assert payload["candidates"][0]["source_detail_image_urls"] == [
        "https://images.example.test/detail.jpg"
    ]
    assert provider.calls == [("search", "露营灯"), ("detail", "offer-1")]
    assert repository.get_run(workspace_id="workspace-a", run_id="run-1").candidate_count == 1


def test_runs_list_and_detail_are_current_workspace_snapshots(
    route_app: tuple[TestClient, DailySelectionRepository, FakeProvider, dict[str, DailySelectionActor], FakeImageCache],
) -> None:
    client, _, _, _, _ = route_app
    created = _preview(client)

    listed = client.get("/desktop/daily-selection/runs")
    detailed = client.get("/desktop/daily-selection/runs/run-1")

    assert listed.status_code == 200
    assert listed.json() == [
        {
            "run_id": "run-1",
            "workspace_id": "workspace-a",
            "status": "completed",
            "candidate_count": 1,
            "created_at": created["created_at"],
            "updated_at": created["updated_at"],
        }
    ]
    assert detailed.status_code == 200
    assert detailed.json() == created


def test_feedback_rejects_candidate_without_discarding_evidence(
    route_app: tuple[TestClient, DailySelectionRepository, FakeProvider, dict[str, DailySelectionActor], FakeImageCache],
) -> None:
    client, _, _, _, _ = route_app
    _preview(client)

    response = client.post(
        "/desktop/daily-selection/runs/run-1/feedback",
        json={
            "candidate_id": "1688:offer-1",
            "reason": "同质化严重",
            "details": {"source": "reviewer"},
        },
    )

    assert response.status_code == 200
    assert response.json()["reason"] == "同质化严重"
    detail = client.get("/desktop/daily-selection/runs/run-1").json()
    assert detail["candidates"][0]["status"] == "rejected"
    assert detail["candidates"][0]["evidence"]
    assert detail["candidates"][0]["source_detail_image_urls"]


def test_confirm_is_idempotent_and_returns_one_pending_handoff(
    route_app: tuple[TestClient, DailySelectionRepository, FakeProvider, dict[str, DailySelectionActor], FakeImageCache],
) -> None:
    client, _, _, _, _ = route_app
    _preview(client)

    first = client.post(
        "/desktop/daily-selection/runs/run-1/confirm",
        json={"candidate_ids": ["1688:offer-1"]},
    )
    second = client.post(
        "/desktop/daily-selection/runs/run-1/confirm",
        json={"candidate_ids": ["1688:offer-1", "1688:offer-1"]},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert len(first.json()) == 1
    assert first.json()[0]["status"] == "pending"
    assert json.loads(first.json()[0]["payload_json"])["selection_metadata"]["status"] == "confirmed"
    assert client.get("/desktop/daily-selection/runs/run-1").json()["candidates"][0]["status"] == "confirmed"


def test_cross_workspace_run_read_and_mutations_are_not_disclosed(
    route_app: tuple[TestClient, DailySelectionRepository, FakeProvider, dict[str, DailySelectionActor], FakeImageCache],
) -> None:
    client, _, _, actor_state, image_cache = route_app
    created = _preview(client)
    recorded_image = created["candidates"][0]["main_image_url"]
    actor_state["actor"] = DailySelectionActor(
        actor_id="user-b", workspace_id="workspace-b"
    )

    responses = (
        client.get("/desktop/daily-selection/runs/run-1"),
        client.post(
            "/desktop/daily-selection/runs/run-1/feedback",
            json={"candidate_id": "1688:offer-1", "reason": "reject"},
        ),
        client.post(
            "/desktop/daily-selection/runs/run-1/confirm",
            json={"candidate_ids": ["1688:offer-1"]},
        ),
        client.get(
            "/desktop/daily-selection/image",
            params={"run_id": "run-1", "url": recorded_image},
        ),
    )

    assert [response.status_code for response in responses] == [404, 404, 404, 404]
    assert image_cache.calls == []


def test_image_proxy_serves_only_a_recorded_workspace_image_from_cache_adapter(
    route_app: tuple[TestClient, DailySelectionRepository, FakeProvider, dict[str, DailySelectionActor], FakeImageCache],
) -> None:
    client, _, _, _, image_cache = route_app
    created = _preview(client)
    recorded_image = created["candidates"][0]["source_image_urls"][0]

    response = client.get(
        "/desktop/daily-selection/image",
        params={"run_id": "run-1", "url": recorded_image},
    )

    assert response.status_code == 200
    assert response.content == b"fake-jpeg"
    assert response.headers["content-type"] == "image/jpeg"
    assert image_cache.calls == [recorded_image]


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "http://127.0.0.1/private.jpg",
        "http://10.0.0.1/private.jpg",
        "http://localhost/private.jpg",
    ],
)
def test_image_proxy_rejects_recorded_loopback_and_private_targets_before_cache_access(
    route_app: tuple[TestClient, DailySelectionRepository, FakeProvider, dict[str, DailySelectionActor], FakeImageCache],
    unsafe_url: str,
) -> None:
    client, repository, _, _, image_cache = route_app
    item = DailySelectionCandidate(
        candidate_id="1688:unsafe",
        offer_id="unsafe",
        source_platform="1688",
        source_url="https://detail.1688.com/unsafe.html",
        source_title="unsafe image",
        main_image_url=unsafe_url,
    )
    repository.save_run(
        workspace_id="workspace-a",
        run_id="unsafe-run",
        status="completed",
        candidates=(item,),
    )

    response = client.get(
        "/desktop/daily-selection/image",
        params={"run_id": "unsafe-run", "url": unsafe_url},
    )

    assert response.status_code == 403
    assert image_cache.calls == []


def test_image_target_validator_rejects_legacy_numeric_loopback_host() -> None:
    with pytest.raises(DailySelectionImageAccessDenied):
        validate_public_image_target("http://127.1/private.jpg", None)


@pytest.mark.parametrize(
    "unrecorded_url",
    ["https://images.example.test/not-recorded.jpg", "file:///etc/passwd"],
)
def test_image_proxy_rejects_arbitrary_and_file_urls_that_were_not_recorded(
    route_app: tuple[TestClient, DailySelectionRepository, FakeProvider, dict[str, DailySelectionActor], FakeImageCache],
    unrecorded_url: str,
) -> None:
    client, _, _, _, image_cache = route_app
    _preview(client)

    response = client.get(
        "/desktop/daily-selection/image",
        params={"run_id": "run-1", "url": unrecorded_url},
    )

    assert response.status_code == 404
    assert image_cache.calls == []


def test_image_proxy_rejects_private_redirect_target(
    route_app: tuple[TestClient, DailySelectionRepository, FakeProvider, dict[str, DailySelectionActor], FakeImageCache],
) -> None:
    client, _, _, _, _ = route_app
    created = _preview(client)
    recorded_image = created["candidates"][0]["main_image_url"]
    redirecting_cache = FakeImageCache(redirect_url="http://127.0.0.1/internal.jpg")

    # Replace only the image adapter through a separately registered test app.
    repository = DailySelectionRepository(":memory:")
    repository.save_run(
        workspace_id="workspace-a",
        run_id="run-1",
        status="completed",
        candidates=(
            DailySelectionCandidate.model_validate(created["candidates"][0]),
        ),
    )
    app = FastAPI()
    router = APIRouter()
    register_daily_selection_routes(
        router,
        DailySelectionRouteDependencies(
            resolve_actor=lambda: DailySelectionActor(
                actor_id="user-a", workspace_id="workspace-a"
            ),
            provider_config_resolver=lambda actor: {"profile": "test"},
            provider_factory=lambda config: FakeProvider(),
            repository=repository,
            budget=InMemoryBudget(),
            image_cache=redirecting_cache,
        ),
    )
    app.include_router(router)

    with TestClient(app) as isolated_client:
        response = isolated_client.get(
            "/desktop/daily-selection/image",
            params={"run_id": "run-1", "url": recorded_image},
        )

    repository.close()
    assert response.status_code == 403
    assert redirecting_cache.calls == [recorded_image]


def test_preview_does_not_echo_host_provider_config_errors() -> None:
    repository = DailySelectionRepository(":memory:")

    def unsafe_config_error(actor: DailySelectionActor) -> Mapping[str, Any]:
        raise ValueError("api_key=top-secret-must-not-escape")

    app = FastAPI()
    router = APIRouter()
    register_daily_selection_routes(
        router,
        DailySelectionRouteDependencies(
            resolve_actor=lambda: DailySelectionActor(
                actor_id="user-a", workspace_id="workspace-a"
            ),
            provider_config_resolver=unsafe_config_error,
            provider_factory=lambda config: FakeProvider(),
            repository=repository,
            budget=InMemoryBudget(),
        ),
    )
    app.include_router(router)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/desktop/daily-selection/preview",
            json={"keywords": ["露营灯"]},
        )

    repository.close()
    assert response.status_code == 500
    assert "top-secret-must-not-escape" not in response.text
