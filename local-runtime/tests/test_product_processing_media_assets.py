from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import inspect

from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.orm import ProductDraftRow


def _jpeg(color: str = "red") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 64), color).save(buffer, format="JPEG", quality=94)
    return buffer.getvalue()


def test_fresh_database_contains_media_tables_and_indexes() -> None:
    database = create_database("sqlite:///:memory:")
    inspector = inspect(database.engine)
    tables = set(inspector.get_table_names())
    assert "product_processing_media_assets" in tables
    assert "product_processing_media_bindings" in tables
    indexes = {
        index["name"]
        for index in inspector.get_indexes("product_processing_media_assets")
    }
    assert "uq_media_asset_workspace_source_identity" in indexes
    assert "uq_media_asset_workspace_local_content" in indexes


def test_v1_drafts_default_and_v2_drafts_accept_version(tmp_path: Path) -> None:
    database = create_database(f"sqlite:///{(tmp_path / 'media.sqlite3').as_posix()}")
    with database.sessions.begin() as session:
        v1 = ProductDraftRow(workspace_id="local")
        session.add(v1)
        session.flush()
        assert v1.media_contract_version == 1
        v2 = ProductDraftRow(workspace_id="local", media_contract_version=2)
        session.add(v2)
        session.flush()
        assert v2.media_contract_version == 2


def test_media_storage_rejects_urls_and_outside_paths(tmp_path: Path) -> None:
    assets = ProductProcessingAssets(tmp_path / "assets")
    with pytest.raises(ValueError):
        assets.require_workspace_media_asset(
            "https://example.com/a.jpg", workspace_id="local"
        )
    outside = tmp_path / "elsewhere.jpg"
    outside.write_bytes(_jpeg())
    with pytest.raises(ValueError):
        assets.require_workspace_media_asset(str(outside), workspace_id="local")


def test_media_storage_requires_existing_file(tmp_path: Path) -> None:
    assets = ProductProcessingAssets(tmp_path / "assets")
    content = _jpeg("blue")
    digest = hashlib.sha256(content).hexdigest()
    path = assets.save_media_asset(content, digest, ".jpg", workspace_id="local")
    assert path.is_file()
    assert path.read_bytes() == content
    assert assets.require_workspace_media_asset(str(path), workspace_id="local") == path
    path.unlink()
    with pytest.raises(FileNotFoundError):
        assets.require_workspace_media_asset(str(path), workspace_id="local")


def test_media_storage_rejects_wrong_content_hash(tmp_path: Path) -> None:
    assets = ProductProcessingAssets(tmp_path / "assets")
    with pytest.raises(ValueError):
        assets.save_media_asset(
            _jpeg(), "0" * 64, ".jpg", workspace_id="local"
        )
