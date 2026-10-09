from __future__ import annotations

from pathlib import Path

from wh_local.modules.profit_activity.infrastructure.database import create_database
from wh_local.modules.profit_activity.infrastructure.repository import ProfitActivityRepository
from wh_local.modules.profit_activity.service import ProfitActivityService


def test_product_library_exposes_creation_date_and_keeps_note_image_separate_from_sources(tmp_path: Path) -> None:
    database = create_database(tmp_path / "profit.sqlite3")
    service = ProfitActivityService(ProfitActivityRepository(database.sessions), database)
    service._asset_root = lambda _settings: tmp_path / "assets"  # type: ignore[method-assign]
    source_groups = [{"source_url": "https://example.test/1688", "image_paths": ["source.png"]}]
    try:
        created = service.upsert_product({
            "site": "US",
            "skc": "US-SKC-1",
            "selling_price": "20",
            "cost_price": "5",
            "weight_kg": "0.5",
            "source_groups_json": str(source_groups).replace("'", '"'),
        })
        saved = service.upsert_product({
            "site": "US",
            "skc": "US-SKC-1",
            "selling_price": "20",
            "cost_price": "5",
            "weight_kg": "0.5",
        }, attachment_image=("note.png", b"note-image"))
        cleared = service.upsert_product({
            "site": "US",
            "skc": "US-SKC-1",
            "selling_price": "20",
            "cost_price": "5",
            "weight_kg": "0.5",
            "clear_attachment_image": "true",
        })

        assert created["library_created_at"]
        assert created["attachment_image_path"] == ""
        assert saved["attachment_image_path"].endswith(".png")
        assert saved["source_groups"][0]["source_url"] == source_groups[0]["source_url"]
        assert saved["source_groups"][0]["image_paths"] == source_groups[0]["image_paths"]
        assert cleared["attachment_image_path"] == ""
        assert cleared["source_groups"][0]["source_url"] == source_groups[0]["source_url"]
        assert cleared["source_groups"][0]["image_paths"] == source_groups[0]["image_paths"]
    finally:
        service.close()
