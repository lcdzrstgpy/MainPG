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


LISTING_ROLES = ("hero", "detail_a", "detail_b", "lifestyle")


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
    def __init__(self, grids: list[bytes]) -> None:
        self.grids = list(grids)
        self.requests: list[object] = []
        self.executor = ThreadPoolExecutor(max_workers=2)

    def submit(self, function, *args, **kwargs):
        return self.executor.submit(function, *args, **kwargs)

    def generate_listing_grid(self, request, *, grant=None, call_id="") -> SimpleNamespace:
        self.requests.append(request)
        return SimpleNamespace(
            stage="grid_image",
            content=self.grids.pop(0),
            content_type="image/png",
            suffix=".png",
            provider="fake",
            model=request.model_id,
            reference_count=1,
        )

    def split_listing_grid(self, media):
        return [
            SimpleNamespace(
                stage=f"grid_image_{index}",
                content=content,
                content_type="image/png",
                suffix=".png",
                provider="split",
                model="pillow",
                reference_count=1,
            )
            for index, content in enumerate(split_grid_2x2(media.content), start=1)
        ]

    def publish_listing_image(self, media, *, namespace: str, role: str) -> str:
        digest = hashlib.sha256(media.content).hexdigest()[:12]
        return f"https://cos.example.com/{namespace}/{role}/{digest}.png"

    def close(self) -> None:
        self.executor.shutdown(wait=True)


class TitleRuntime:
    configured = True

    def __init__(self) -> None:
        self.requests: list[object] = []
        self.executor = ThreadPoolExecutor(max_workers=2)

    def submit(self, function, *args, **kwargs):
        return self.executor.submit(function, *args, **kwargs)

    def generate_title(self, request, *, grant, call_id, call_ids=(), on_outcome=None):
        assert grant.provider_key("ark")
        self.requests.append(request)
        title = f"Coastal Botanical Canvas Tote Style {request.style_index}"
        return SimpleNamespace(
            title=title,
            english_title=f"Style {request.style_index}",
            description=f"Description for style {request.style_index}.",
            normalized_title=title.lower(),
            visual_theme="coastal botanical",
            motif_keywords=("fern",),
            color_keywords=("navy",),
            model="title-model",
            prompt_version="pod-title-v1",
            attempt_count=1,
        )

    def close(self) -> None:
        self.executor.shutdown(wait=True)


class BillingCoordinator:
    def __init__(self) -> None:
        self.settlements = []
        self.freezes = []

    def freeze(self, _actor, _plan):
        self.freezes.append(_plan)
        return PodExecutionGrant(
            "freeze-1", 1, "2099-01-01T00:00:00Z", {"wuyin": "w", "ark": "a"}
        )

    def settle(self, _actor, _grant, plan, outcomes):
        self.settlements.append((plan, tuple(outcomes)))

    def regrant(self, actor, freeze_id):
        return self.freeze(actor, None)


def _actor() -> Actor:
    return Actor(id="designer-1", username="designer", role="admin", workspace_id="workspace-a")


def _service(tmp_path: Path) -> tuple[PodCustomizationService, ImageRuntime, TitleRuntime, BillingCoordinator]:
    runtime = ImageRuntime([_grid([_pattern(i) for i in range(4)])])
    title = TitleRuntime()
    billing = BillingCoordinator()
    service = PodCustomizationService(
        tmp_path / "workbench.sqlite3",
        tmp_path / "pod-assets",
        runtime,
        title_runtime=title,
        billing_coordinator=billing,
        start_workers=True,
    )
    return service, runtime, title, billing


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


def _real_asset(service: PodCustomizationService, actor: Actor) -> dict:
    content = _encode(_pattern(99))
    stored = service.worker.assets.save_image(actor.workspace_id, actor.id, content)
    return service.repository.create_asset(
        workspace_id=actor.workspace_id,
        owner_user_id=actor.id,
        kind="pattern_candidate",
        filename="style.png",
        relative_path=stored.relative_path,
        content_type=stored.content_type,
        byte_size=stored.byte_size,
        sha256=stored.sha256,
        width=stored.width,
        height=stored.height,
    )


def _mark_style_image_done(service: PodCustomizationService, actor: Actor, batch_id: str, style_index: int) -> None:
    """Style has 4 durable published images but an interrupted/failed title."""
    asset = _real_asset(service, actor)
    with service.repository._connect() as connection:
        connection.execute(
            """UPDATE pod_customization_style_grid_results
               SET status = 'completed', pattern_asset_id = ?, composite_asset_id = ?,
                   pattern_fingerprint = 'fp'
               WHERE batch_id = ? AND style_index = ?""",
            (asset["asset_id"], asset["asset_id"], batch_id, style_index),
        )
        rows = connection.execute(
            """SELECT result_id, variant_index FROM pod_customization_style_grid_results
               WHERE batch_id = ? AND style_index = ?""",
            (batch_id, style_index),
        ).fetchall()
        connection.executemany(
            """INSERT INTO pod_customization_style_grid_publications
               (result_id, role, public_url, updated_at) VALUES (?, ?, ?, datetime('now'))""",
            [(row["result_id"], LISTING_ROLES[row["variant_index"] - 1], f"https://example.test/{row['variant_index']}")
             for row in rows],
        )
        connection.execute(
            """UPDATE pod_customization_style_titles
               SET status = 'failed', error_message = 'title interrupted' WHERE batch_id = ? AND style_index = ?""",
            (batch_id, style_index),
        )


def test_mixed_resume_regenerates_image_title_and_keeps_title_only_style(tmp_path: Path) -> None:
    """Regression: a paused batch resumes with BOTH remaining images and a
    title-only style.  The image style must reserve its OWN title calls (so it
    never drains the title-only style's reserved calls via the fallback), and the
    title-only style must still be resumed.  Without this, the title-only style
    is stranded non-terminal and the batch never settles -> export/retry blocked.
    """
    service, runtime, title, billing = _service(tmp_path)
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

    # Style 1: 4 durable images, title interrupted (title-only on resume).
    _mark_style_image_done(service, actor, batch_id, 1)
    # Style 2: images still queued (image-style on resume). Status paused.
    with service.repository._connect() as connection:
        connection.execute(
            "UPDATE pod_customization_batches SET status = 'paused' WHERE batch_id = ?", (batch_id,)
        )

    resumed = service.resume_batch(actor, batch_id)
    assert resumed["status"] in {"queued", "generating_patterns", "generating_titles"}

    # The resume plan must reserve title calls for the image style (style 2 too),
    # not just the title-only style.
    resume_plan = [p for p in billing.freezes if p.idempotency_key.startswith(f"pod:batch:{batch_id}:resume:")]
    assert resume_plan, "no resume freeze was created"
    resume_calls = {c.call_id for c in resume_plan[0].calls}
    assert any(call_id.endswith("style:2:image:1") for call_id in resume_calls)
    assert any("style:2:title:" in call_id for call_id in resume_calls), (
        "image style must reserve its own title calls so it does not drain the title-only style"
    )
    assert any("style:1:title:" in call_id for call_id in resume_calls)

    deadline = time.monotonic() + 5
    final = None
    while time.monotonic() < deadline:
        final = service.get_batch(actor, batch_id)
        if final["status"] not in {"queued", "generating_patterns", "compositing", "generating_titles"}:
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"batch did not settle; final status = {final['status']}")

    statuses = {t["style_index"]: t["status"] for t in final["style_titles"]}
    print(f"\nFINAL batch status = {final['status']}")
    print(f"style_titles = {statuses}")

    # The batch must reach a terminal status and both titles must be completed.
    assert final["status"] in {"completed", "partial_failure"}, f"got {final['status']} titles={statuses}"
    assert statuses.get(1) == "completed", f"title-only style 1 title stuck: {statuses.get(1)}"
    assert statuses.get(2) == "completed", f"image style 2 title stuck: {statuses.get(2)}"

    service.close()
    runtime.close()
    title.close()
