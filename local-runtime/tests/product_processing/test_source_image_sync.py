from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.data_collection.public_image_fetch import FetchedPublicImage  # noqa: E402
from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets  # noqa: E402
from wh_local.modules.product_processing.infrastructure.database import create_database  # noqa: E402
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository  # noqa: E402
from wh_local.modules.product_processing.service import ProductProcessingService  # noqa: E402


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
