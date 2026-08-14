from __future__ import annotations

import json
import time
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from PIL import Image

from wh_local.data_collection.public_image_fetch import FetchedPublicImage
from wh_local.modules.product_processing.domain.models import DailySelectionHandoffEnvelope
from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.media_asset_repository import MediaAssetRepository
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository
from wh_local.modules.product_processing.media_asset_service import MediaAssetService
from wh_local.modules.product_processing.service import ProductProcessingService


def _jpeg(color: str = "red") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 64), color).save(buffer, format="JPEG", quality=94)
    return buffer.getvalue()


def _handoff() -> DailySelectionHandoffEnvelope:
    return DailySelectionHandoffEnvelope(
        handoff_id="h-1",
        run_id="run-1",
        candidate_id="c-1",
        workspace_id="ws",
        payload_json=json.dumps(
            {
                "candidate": {
                    "candidate_id": "c-1",
                    "source_url": "https://detail.example.com/x.html",
                    "source_title": "Test product",
                },
                "images": {
                    "main": "https://img.example.com/main.jpg",
                    "gallery": ["https://img.example.com/g1.jpg"],
                    "detail": [],
                    "sku": [],
                },
                "skus": [],
                "attributes": {},
                "selection_metadata": {},
            },
            ensure_ascii=False,
        ),
        status="pending",
        idempotency_key="idem-1",
        created_at="2026-08-13T00:00:00+00:00",
    )


def test_draft_media_groups_without_managed_path(tmp_path: Path) -> None:
    service = ProductProcessingService(
        ProductProcessingRepository(create_database("sqlite:///:memory:")),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    result = service.consume_daily_selection_handoffs([_handoff()])
    draft = result["drafts"][0]
    assert draft["media_contract_version"] == 2

    media = service.draft_media(draft["id"], workspace_id="ws")
    assert media["contract_version"] == 2
    groups = media["groups"]
    assert len(groups["main"]) == 1
    assert len(groups["gallery"]) == 1
    assert groups["main"][0]["status"] == "pending"
    assert groups["main"][0]["preview_url"] == ""
    for group in groups.values():
        for item in group:
            assert "managed_path" not in item
            assert "access_token" not in item


def test_media_content_signature_and_retry(tmp_path: Path) -> None:
    database = create_database(f"sqlite:///{(tmp_path / 'media.sqlite3').as_posix()}")
    assets = ProductProcessingAssets(tmp_path / "assets")
    service = MediaAssetService(MediaAssetRepository(database), assets)

    content = _jpeg("green")
    asset = service.register_local_asset("ws", "preview_upload", content, "image/jpeg")
    assert asset["status"] == "ready"

    query = parse_qs(urlsplit(asset["preview_url"]).query)
    expires = int(query["expires"][0])
    signature = query["signature"][0]
    path, media_type = service.media_asset_content(
        asset["id"], workspace_id="ws", expires=expires, signature=signature
    )
    assert path.read_bytes() == content
    assert media_type == "image/jpeg"
    with pytest.raises(LookupError):
        service.media_asset_content(
            asset["id"], workspace_id="ws", expires=expires, signature="0" * 64
        )

    remote = service.register_remote_asset("ws", "https://img.example.com/r.jpg")
    service.public_image_fetcher = lambda url: FetchedPublicImage(
        b"not an image", "application/octet-stream", url
    )
    service.materialize_pending(workspace_id="ws")
    assert service.get_asset(remote["id"], "ws")["status"] == "failed"
    retried = service.retry_asset(remote["id"], workspace_id="ws")
    assert retried["status"] == "pending"


def test_retry_media_asset_starts_background_materialization(tmp_path: Path) -> None:
    service = ProductProcessingService(
        ProductProcessingRepository(
            create_database(f"sqlite:///{(tmp_path / 'media-retry.sqlite3').as_posix()}")
        ),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    remote = service.media_assets.register_remote_asset("ws", "https://img.example.com/retry.jpg")
    service.media_assets.public_image_fetcher = lambda url: FetchedPublicImage(
        b"not an image", "application/octet-stream", url
    )
    service.media_assets.materialize_pending(workspace_id="ws")
    assert service.media_assets.get_asset(remote["id"], "ws")["status"] == "failed"

    service.media_assets.public_image_fetcher = lambda url: FetchedPublicImage(
        _jpeg("blue"), "image/jpeg", url
    )
    service.retry_media_asset(remote["id"], workspace_id="ws")
    for _ in range(20):
        if service.media_assets.get_asset(remote["id"], "ws")["status"] == "ready":
            break
        time.sleep(0.02)

    assert service.media_assets.get_asset(remote["id"], "ws")["status"] == "ready"
