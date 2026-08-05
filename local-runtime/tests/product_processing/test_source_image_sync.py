from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.data_collection.public_image_fetch import FetchedPublicImage  # noqa: E402
from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets  # noqa: E402
from wh_local.modules.product_processing.infrastructure.database import create_database  # noqa: E402
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository  # noqa: E402
from wh_local.modules.product_processing.service import ProductProcessingService  # noqa: E402


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    from wh_local.app.main import create_app

    with TestClient(create_app(tmp_path / "runtime.sqlite3")) as test_client:
        yield test_client


def create_draft(client: TestClient, *, source_type: str, candidate_id: str) -> int:
    response = client.post(
        "/product-processing/drafts",
        json={
            "source_type": source_type,
            "candidate_id": candidate_id,
            "title": candidate_id,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["draft"]["id"]


class FakePublicImageFetcher:
    def __init__(self) -> None:
        self.failures: set[str] = {"https://cdn.example.test/detail.jpg"}

    def __call__(self, url: str) -> FetchedPublicImage:
        if url in self.failures:
            raise ValueError("download was unavailable")
        return FetchedPublicImage(
            content=b"source-image-bytes",
            media_type="image/png",
            final_url="https://images.example.test/redirected.png",
        )


@pytest.fixture
def service(tmp_path: Path) -> ProductProcessingService:
    database = create_database(f"sqlite:///{(tmp_path / 'product-processing.sqlite3').as_posix()}")
    return ProductProcessingService(
        ProductProcessingRepository(database),
        ProductProcessingAssets(tmp_path / "assets"),
    )


@pytest.fixture
def service_with_fetcher(tmp_path: Path) -> ProductProcessingService:
    database = create_database(f"sqlite:///{(tmp_path / 'product-processing.sqlite3').as_posix()}")
    return ProductProcessingService(
        ProductProcessingRepository(database),
        ProductProcessingAssets(tmp_path / "assets"),
        public_image_fetcher=FakePublicImageFetcher(),
    )


def make_pending_draft(service: ProductProcessingService) -> dict:
    draft, _ = service.create_draft(
        {
            "source_type": "web_manual_capture",
            "candidate_id": "plugin:temu:42",
            "title": "杯子",
            "source_ref": "https://www.temu.com/goods.html?goods_id=42",
            "image_url": "https://cdn.example.test/main.jpg",
            "source_image_urls": ["https://cdn.example.test/main.jpg"],
            "source_detail_image_urls": ["https://cdn.example.test/detail.jpg"],
        }
    )
    return draft


def test_draft_seeds_pending_source_and_detail_images(service: ProductProcessingService) -> None:
    draft = make_pending_draft(service)

    images = service.source_images(draft_id=draft["id"])["images"]

    assert [(row["kind"], row["sync_status"]) for row in images] == [
        ("source", "pending"),
        ("detail", "pending"),
    ]


def test_sync_keeps_remote_url_and_makes_failure_retryable(
    service_with_fetcher: ProductProcessingService,
) -> None:
    draft = make_pending_draft(service_with_fetcher)

    assert service_with_fetcher.sync_draft_source_images(draft["id"]) == {"ready": 1, "failed": 1}
    images = service_with_fetcher.source_images(draft_id=draft["id"])["images"]
    assert images[0]["url"] == "https://cdn.example.test/main.jpg"
    assert images[0]["sync_status"] == "ready" and images[0]["local_path"]
    assert images[1]["sync_status"] == "failed" and images[1]["sync_error"]

    service_with_fetcher._public_image_fetcher.failures.clear()  # type: ignore[attr-defined]

    assert service_with_fetcher.retry_draft_source_images(draft["id"]) == {"ready": 1, "failed": 0}
    retried = service_with_fetcher.source_images(draft_id=draft["id"])["images"]
    assert retried[1]["sync_status"] == "ready"
    assert retried[1]["sync_error"] == ""


def test_draft_list_exposes_ready_primary_source_image_without_replacing_remote_url(
    service_with_fetcher: ProductProcessingService,
) -> None:
    draft = make_pending_draft(service_with_fetcher)

    assert service_with_fetcher.sync_draft_source_images(draft["id"]) == {"ready": 1, "failed": 1}
    ready_source = service_with_fetcher.source_images(draft_id=draft["id"])["images"][0]
    listed = service_with_fetcher.list_drafts(None, 20, 0, summary=False)["drafts"]
    projected = next(item for item in listed if item["id"] == draft["id"])

    assert projected["image_path"] == ready_source["local_path"]
    assert projected["image_url"] == "https://cdn.example.test/main.jpg"
    assert service_with_fetcher.draft_image_path(draft["id"]) == Path(ready_source["local_path"])


def test_retry_reclaims_a_stale_syncing_source_image(service_with_fetcher: ProductProcessingService) -> None:
    draft = make_pending_draft(service_with_fetcher)
    claimed = service_with_fetcher.repository.claim_syncable_source_images(draft["id"])
    stale_claimed_at = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()

    with service_with_fetcher.repository.database.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE product_processing_source_images SET sync_claimed_at = ? WHERE id = ?",
            (stale_claimed_at, claimed[0]["id"]),
        )

    assert service_with_fetcher.retry_draft_source_images(draft["id"]) == {"ready": 1, "failed": 0}
    retried = service_with_fetcher.source_images(draft_id=draft["id"])["images"]
    assert retried[0]["url"] == "https://cdn.example.test/main.jpg"
    assert retried[0]["sync_status"] == "ready"


def test_drafts_filter_by_source_type(client: TestClient) -> None:
    create_draft(client, source_type="web_manual_capture", candidate_id="manual-1")
    create_draft(client, source_type="onebound_api", candidate_id="api-1")

    response = client.get("/product-processing/drafts", params={"source_type": "onebound_api"})

    assert response.status_code == 200, response.text
    assert [item["candidate_id"] for item in response.json()["drafts"]] == ["api-1"]


def test_drafts_reject_unknown_source_type(client: TestClient) -> None:
    response = client.get("/product-processing/drafts", params={"source_type": "excel"})

    assert response.status_code == 422


def test_retry_source_images_schedules_existing_draft(client: TestClient) -> None:
    draft_id = create_draft(client, source_type="web_manual_capture", candidate_id="manual-retry")

    response = client.post(f"/product-processing/drafts/{draft_id}/source-images/retry")

    assert response.status_code == 200, response.text
    assert response.json()["draft"]["id"] == draft_id
    assert response.json()["sync"] == {"status": "scheduled"}
