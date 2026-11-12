from __future__ import annotations

from pathlib import Path

import pytest

from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository
from wh_local.modules.product_processing.service import ProductProcessingService


def _service(tmp_path: Path) -> ProductProcessingService:
    return ProductProcessingService(
        ProductProcessingRepository(create_database("sqlite:///:memory:")),
        ProductProcessingAssets(tmp_path / "assets"),
    )


def test_precision_annotation_round_trip(tmp_path: Path) -> None:
    service = _service(tmp_path)
    draft, _ = service.create_draft(
        {
            "source_type": "manual",
            "title": "Mechanical Keyboard",
            "product_name": "Mechanical Keyboard",
        },
        workspace_id="local",
    )

    updated = service.update_draft(
        draft["id"], {"precision_annotation": "precision"}, workspace_id="local"
    )
    assert updated is not None
    assert updated["precision_annotation"] == "precision"
    assert service.get_draft(draft["id"], "local")["precision_annotation"] == "precision"

    cleared = service.update_draft(
        draft["id"], {"precision_annotation": ""}, workspace_id="local"
    )
    assert cleared is not None
    assert cleared["precision_annotation"] == ""


def test_precision_annotation_rejects_unknown_values(tmp_path: Path) -> None:
    service = _service(tmp_path)
    draft, _ = service.create_draft(
        {"source_type": "manual", "title": "Mug", "product_name": "Mug"},
        workspace_id="local",
    )

    with pytest.raises(ValueError, match="precision_annotation"):
        service.update_draft(
            draft["id"], {"precision_annotation": "bogus"}, workspace_id="local"
        )
