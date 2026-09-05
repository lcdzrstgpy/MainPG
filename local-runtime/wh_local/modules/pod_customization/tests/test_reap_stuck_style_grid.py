from __future__ import annotations

import hashlib
import io
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from wh_local.modules.pod_customization.contracts import (
    BatchCreate,
    BusinessFields,
    Calibration,
    ListingFields,
    NormalizedPoint,
    NormalizedRect,
)
from wh_local.modules.pod_customization.billing_contract import PodExecutionGrant
from wh_local.modules.pod_customization.images import split_grid_2x2
from wh_local.modules.pod_customization.service import PodCustomizationService
from wh_local.session import Actor


def _encode(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _pattern(index: int) -> Image.Image:
    color = ((index * 47 + 40) % 220 + 20, (index * 71 + 60) % 210 + 20, (index * 29 + 90) % 200 + 30)
    return Image.new("RGB", (96, 96), color)


def _grid(patterns: list[Image.Image]) -> bytes:
    image = Image.new("RGB", (192, 192), "white")
    for pattern, position in zip(patterns, ((0, 0), (96, 0), (0, 96), (96, 96)), strict=True):
        image.paste(pattern, position)
    return _encode(image)


class ImageRuntime:
    def __init__(self) -> None:
        self.executor = ThreadPoolExecutor(max_workers=2)

    def submit(self, function, *args, **kwargs):
        return self.executor.submit(function, *args, **kwargs)

    def generate_listing_grid(self, request, *, grant=None, call_id="") -> SimpleNamespace:
        raise RuntimeError("not used in reaper test")

    def split_listing_grid(self, media):
        return [SimpleNamespace() for _ in split_grid_2x2(media.content)]

    def publish_listing_image(self, _media, *, namespace: str, role: str) -> str:
        return f"https://example.test/{namespace}/{role}"

    def close(self) -> None:
        self.executor.shutdown(wait=True)


class TitleRuntime:
    configured = True

    def __init__(self) -> None:
        self.executor = ThreadPoolExecutor(max_workers=2)

    def submit(self, function, *args, **kwargs):
        return self.executor.submit(function, *args, **kwargs)

    def generate_title(self, _request, *, grant, call_id, call_ids=(), on_outcome=None):
        raise RuntimeError("not used in reaper test")

    def close(self) -> None:
        self.executor.shutdown(wait=True)


class BillingCoordinator:
    def freeze(self, _actor, _plan):
        return PodExecutionGrant("f", 1, "2099-01-01T00:00:00Z", {"wuyin": "w", "ark": "a"})

    def settle(self, *_args):
        pass

    def regrant(self, actor, freeze_id):
        return self.freeze(actor, None)


def _actor() -> Actor:
    return Actor(id="designer-1", username="designer", role="admin", workspace_id="workspace-a")


def _service(tmp_path: Path) -> tuple[PodCustomizationService, TitleRuntime]:
    runtime = ImageRuntime()
    title = TitleRuntime()
    service = PodCustomizationService(
        tmp_path / "workbench.sqlite3",
        tmp_path / "pod-assets",
        runtime,
        title_runtime=title,
        billing_coordinator=BillingCoordinator(),
        start_workers=True,
    )
    return service, title


def _ready_template(service: PodCustomizationService, actor: Actor) -> dict:
    scene = Image.new("RGB", (240, 200), "#e9ecef")
    template = service.upload_template(actor, name="Fixed tote scene", filename="scene.png", content=_encode(scene))
    return service.update_template_calibration(
        actor,
        template["id"],
        Calibration(
            mask=NormalizedRect(x=0.25, y=0.2, width=0.5, height=0.6),
            anchor=NormalizedPoint(x=0.5, y=0.5),
        ),
    )


def test_reap_stuck_style_grid_batch_marks_grid_results_terminal(tmp_path: Path) -> None:
    """A stale style-grid batch must have its style_grid_results (not the legacy
    batch_items table) marked failed on reap.  Previously the reaper updated
    batch_items only, leaving the stuck style's grid results queued and the batch
    unretryable after reap.
    """
    service, title = _service(tmp_path)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(
        actor,
        BatchCreate(
            template_id=template["id"],
            count=2,
            prompt_version="v1",
            business_fields=BusinessFields(product_name="Tote bag", product_category="bags"),
            listing_fields=ListingFields(
                declared_price=18.5, suggested_price_usd=29.99, category_name="家居收纳 > 包袋",
                skus=[{"name": "Default", "length_cm": 30, "width_cm": 20, "height_cm": 10, "weight_g": 450}],
            ),
        ),
        enqueue=False,
    )
    batch_id = batch["id"]

    # Make batch stale: all items queued, batch compositing with a very old
    # last_progress_at so the reaper reaps it.
    with service.repository._connect() as connection:
        connection.execute(
            """UPDATE pod_customization_batches
               SET status = 'compositing', last_progress_at = '2020-01-01T00:00:00.000+00:00',
                   started_at = '2020-01-01T00:00:00.000+00:00', execution_epoch = 1
               WHERE batch_id = ?""",
            (batch_id,),
        )
        # Emulate one stuck generation call and a queued title for style 2.
        connection.execute(
            """UPDATE pod_customization_generation_calls
               SET status = 'running' WHERE batch_id = ? AND call_kind = 'initial' AND call_index = 2""",
            (batch_id,),
        )
        connection.execute(
            """UPDATE pod_customization_style_titles
               SET status = 'queued' WHERE batch_id = ? AND style_index = 2""",
            (batch_id,),
        )

    reaped = service.reap_stuck_batches_once()

    assert [r["batch_id"] for r in reaped] == [batch_id], f"expected batch reaped, got {reaped}"
    with service.repository._connect() as connection:
        status = connection.execute(
            "SELECT status FROM pod_customization_batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()[0]
        assert status in {"failed", "partial_failure"}, f"batch should be terminal, got {status}"
        # The style-grid batch's items live in style_grid_results, not batch_items.
        leftovers = connection.execute(
            """SELECT COUNT(*) FROM pod_customization_style_grid_results
               WHERE batch_id = ? AND status NOT IN ('completed', 'failed')""",
            (batch_id,),
        ).fetchone()[0]
        assert leftovers == 0, f"stale style-grid results must be terminal, {leftovers} left"
        calls = connection.execute(
            """SELECT COUNT(*) FROM pod_customization_generation_calls
               WHERE batch_id = ? AND status IN ('queued', 'running')""",
            (batch_id,),
        ).fetchone()[0]
        assert calls == 0, f"stale generation calls must be interrupted, {calls} left"

    service.close()
    title.close()
