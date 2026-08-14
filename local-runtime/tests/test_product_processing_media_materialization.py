from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from wh_local.data_collection.public_image_fetch import FetchedPublicImage
from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.media_asset_orm import MediaAssetRow
from wh_local.modules.product_processing.infrastructure.media_asset_repository import (
    MediaAssetRepository,
)
from wh_local.modules.product_processing.infrastructure.orm import ProductDraftRow
from wh_local.modules.product_processing.media_asset_service import MediaAssetService


def _jpeg(color: str = "red") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 64), color).save(buffer, format="JPEG", quality=94)
    return buffer.getvalue()


def _service(tmp_path: Path, fetcher=None):
    database = create_database(f"sqlite:///{(tmp_path / 'media.sqlite3').as_posix()}")
    assets = ProductProcessingAssets(tmp_path / "assets")
    repository = MediaAssetRepository(database)
    service = MediaAssetService(repository, assets, public_image_fetcher=fetcher)
    return service, database


def _draft_id(database, workspace_id: str) -> int:
    with database.sessions.begin() as session:
        draft = ProductDraftRow(workspace_id=workspace_id, media_contract_version=2)
        session.add(draft)
        session.flush()
        return int(draft.id)


def test_remote_asset_materializes_to_ready(tmp_path: Path) -> None:
    content = _jpeg("green")

    def fetcher(url: str) -> FetchedPublicImage:
        return FetchedPublicImage(content, "image/jpeg", url)

    service, _db = _service(tmp_path, fetcher=fetcher)
    asset = service.register_remote_asset("ws", "https://example.com/a.jpg")
    assert asset["status"] == "pending"
    result = service.materialize_pending(workspace_id="ws")
    assert result["ready"] == 1
    row = service.get_asset(asset["id"], "ws")
    assert row["status"] == "ready"
    assert row["content_hash"]
    assert service.read_ready_asset(asset["id"], workspace_id="ws") == content


def test_materialize_until_idle_drains_assets_beyond_one_batch(tmp_path: Path) -> None:
    content = _jpeg("green")

    def fetcher(url: str) -> FetchedPublicImage:
        return FetchedPublicImage(content, "image/jpeg", url)

    service, _db = _service(tmp_path, fetcher=fetcher)
    assets = [
        service.register_remote_asset("ws", f"https://example.com/{index}.jpg")
        for index in range(21)
    ]

    result = service.materialize_until_idle(workspace_id="ws", batch_size=20)

    assert result == {"claimed": 21, "ready": 21, "retryable": 0, "failed": 0}
    assert all(service.get_asset(asset["id"], "ws")["status"] == "ready" for asset in assets)


def test_transient_fetch_failure_is_retryable(tmp_path: Path) -> None:
    def fetcher(url: str) -> FetchedPublicImage:
        raise OSError("network down")

    service, _db = _service(tmp_path, fetcher=fetcher)
    asset = service.register_remote_asset("ws", "https://example.com/a.jpg")
    result = service.materialize_pending(workspace_id="ws")
    assert result["retryable"] == 1
    row = service.get_asset(asset["id"], "ws")
    assert row["status"] == "retryable"


def test_invalid_image_is_failed(tmp_path: Path) -> None:
    def fetcher(url: str) -> FetchedPublicImage:
        return FetchedPublicImage(b"not an image", "application/octet-stream", url)

    service, _db = _service(tmp_path, fetcher=fetcher)
    asset = service.register_remote_asset("ws", "https://example.com/a.jpg")
    result = service.materialize_pending(workspace_id="ws")
    assert result["failed"] == 1
    row = service.get_asset(asset["id"], "ws")
    assert row["status"] == "failed"


def test_expired_materialization_lease_is_reclaimed(tmp_path: Path) -> None:
    service, database = _service(tmp_path)
    asset = service.register_remote_asset("ws", "https://example.com/a.jpg")
    first = service.repository.claim_materialization("ws", limit=1)
    assert len(first) == 1
    assert service.repository.claim_materialization("ws", limit=1) == []
    expired = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    with database.sessions.begin() as session:
        row = session.get(MediaAssetRow, first[0]["id"])
        row.claimed_at = expired
    reclaimed = service.repository.claim_materialization("ws", limit=1)
    assert len(reclaimed) == 1
    assert reclaimed[0]["id"] == first[0]["id"]


def test_two_bindings_share_one_source_asset(tmp_path: Path) -> None:
    service, database = _service(tmp_path)
    draft_id = _draft_id(database, "ws")
    asset = service.register_remote_asset("ws", "https://example.com/a.jpg")
    first = service.bind_asset(
        workspace_id="ws", asset_id=asset["id"], product_draft_id=draft_id,
        role="gallery", sort_order=0,
    )
    second = service.bind_asset(
        workspace_id="ws", asset_id=asset["id"], product_draft_id=draft_id,
        role="sku", sku_id="S-1", sort_order=0,
    )
    assert first["asset_id"] == asset["id"]
    assert second["asset_id"] == asset["id"]
    assert first["id"] != second["id"]
    replay = service.bind_asset(
        workspace_id="ws", asset_id=asset["id"], product_draft_id=draft_id,
        role="gallery", sort_order=0,
    )
    assert replay["id"] == first["id"]
    bindings = service.list_bindings("ws", product_draft_id=draft_id)
    assert len(bindings) == 2
