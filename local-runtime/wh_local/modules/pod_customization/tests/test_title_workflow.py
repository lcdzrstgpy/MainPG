from __future__ import annotations

import hashlib
import io
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from wh_local.modules.pod_customization.contracts import (
    BatchCreate,
    BusinessFields,
    Calibration,
    DirectListingTrialCreate,
    ListingFields,
    NormalizedPoint,
    NormalizedRect,
)
from wh_local.modules.pod_customization.billing_contract import PodCallOutcome, PodExecutionGrant
from wh_local.modules.pod_customization.images import split_grid_2x2
from wh_local.modules.pod_customization.repository import PodRepositoryError
from wh_local.modules.pod_customization.service import PodCustomizationService
from wh_local.modules.pod_customization.router import create_router
from wh_local.modules.product_processing.infrastructure.media import GeneratedMedia
from wh_local.session import Actor


def _encode(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _pattern(index: int) -> Image.Image:
    color = ((index * 47 + 40) % 220 + 20, (index * 71 + 60) % 210 + 20, (index * 29 + 90) % 200 + 30)
    image = Image.new("RGB", (96, 96), color)
    draw = ImageDraw.Draw(image)
    for offset in range(-96, 192, 7 + index % 11):
        draw.line((offset, 0, offset - 96, 96), fill="white", width=2)
    draw.ellipse((18, 20, 62, 64), outline="black", width=2)
    return image


def _grid(seed: int) -> bytes:
    image = Image.new("RGB", (192, 192), "white")
    for pattern, position in zip(
        [_pattern(seed + index) for index in range(4)],
        ((0, 0), (96, 0), (0, 96), (96, 96)),
        strict=True,
    ):
        image.paste(pattern, position)
    return _encode(image)


class ImageRuntime:
    def __init__(self, grids: list[bytes], *, publish_failures: dict[str, int] | None = None) -> None:
        self.grids = list(grids)
        self.publish_failures = dict(publish_failures or {})
        self.requests: list[object] = []
        self.published_roles: list[str] = []
        self.executor = ThreadPoolExecutor(max_workers=2)

    def submit(self, function, *args, **kwargs):
        return self.executor.submit(function, *args, **kwargs)

    def generate_listing_grid(self, request, *, grant, call_id):
        assert grant.provider_key("wuyin")
        self.requests.append(request)
        return GeneratedMedia(
            stage="grid_image",
            content=self.grids.pop(0),
            content_type="image/png",
            suffix=".png",
            provider="test",
            model="image-model",
            reference_count=1,
        )

    def split_listing_grid(self, media):
        return [
            GeneratedMedia(
                stage=f"panel-{index}",
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
        self.published_roles.append(role)
        remaining = self.publish_failures.get(role, 0)
        if remaining:
            self.publish_failures[role] = remaining - 1
            raise RuntimeError(f"publication failed for {role}")
        digest = hashlib.sha256(media.content).hexdigest()[:10]
        return f"https://images.example/{namespace}/{role}/{digest}.png"

    def close(self) -> None:
        self.executor.shutdown(wait=True)


class TitleRuntime:
    configured = True

    def __init__(self, *, failures: int = 0) -> None:
        self.failures = failures
        self.requests: list[object] = []
        self.executor = ThreadPoolExecutor(max_workers=2)

    def submit(self, function, *args, **kwargs):
        return self.executor.submit(function, *args, **kwargs)

    def generate_title(self, request, *, grant, call_id, call_ids=(), on_outcome=None):
        assert grant.provider_key("ark")
        self.requests.append(request)
        if self.failures:
            self.failures -= 1
            raise RuntimeError("temporary title provider failure")
        title = (
            "Handcrafted coastal botanical illustration for canvas tote bags with layered ocean fern "
            f"details style {request.style_index} for home office studio travel and thoughtful seasonal gifting"
        )
        return SimpleNamespace(
            title=title,
            english_title=f"Coastal Botanical Canvas Tote Style {request.style_index}",
            description=f"Layered ocean fern artwork for style {request.style_index}.",
            normalized_title=" ".join(title.lower().split()),
            visual_theme="coastal botanical",
            motif_keywords=("ocean fern", "layered ink"),
            color_keywords=("navy", "sand"),
            model="title-model",
            prompt_version="pod-title-v1",
            attempt_count=1,
        )

    def close(self) -> None:
        self.executor.shutdown(wait=True)


class UnconfiguredTitleRuntime(TitleRuntime):
    configured = False


class SlowTitleRuntime(TitleRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.active = 0
        self.max_active = 0
        self._active_lock = threading.Lock()

    def generate_title(self, request, *, grant, call_id, call_ids=(), on_outcome=None):
        with self._active_lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.05)
            return super().generate_title(
                request, grant=grant, call_id=call_id, call_ids=call_ids, on_outcome=on_outcome
            )
        finally:
            with self._active_lock:
                self.active -= 1


class SubmitFailTitleRuntime(TitleRuntime):
    def submit(self, function, *args, **kwargs):
        raise RuntimeError("title executor unavailable")


class DuplicateTitleRuntime(TitleRuntime):
    def generate_title(self, request, *, grant, call_id, call_ids=(), on_outcome=None):
        result = super().generate_title(
            request, grant=grant, call_id=call_id, call_ids=call_ids, on_outcome=on_outcome
        )
        duplicate = (
            "Handcrafted coastal botanical illustration for canvas tote bags with layered ocean fern "
            "details for home office studio travel and thoughtful seasonal gifting"
        )
        result.title = duplicate
        result.normalized_title = " ".join(duplicate.lower().split())
        return result


def _actor() -> Actor:
    return Actor(id="local-demo-admin", username="local-demo", role="admin")


class BillingCoordinator:
    def __init__(self) -> None:
        self.settlements = []
        self.freezes = []

    def freeze(self, actor, plan):
        self.freezes.append(plan)
        return PodExecutionGrant(
            "freeze-1", 1, "2099-01-01T00:00:00Z", {"wuyin": "test-wuyin", "ark": "test-ark"}
        )

    def settle(self, actor, grant, plan, outcomes):
        self.settlements.append((plan, tuple(outcomes)))

    def regrant(self, actor, freeze_id):
        return self.freeze(actor, None)


def _service(
    tmp_path: Path,
    images: ImageRuntime,
    titles: TitleRuntime | None,
    billing: BillingCoordinator | None = None,
) -> PodCustomizationService:
    return PodCustomizationService(
        tmp_path / "workbench.sqlite3",
        tmp_path / "pod-assets",
        images,
        title_runtime=titles,
        billing_coordinator=billing or BillingCoordinator(),
        start_workers=True,
    )


def _ready_template(service: PodCustomizationService, actor: Actor) -> dict:
    template = service.upload_template(
        actor,
        name="Canvas tote",
        filename="scene.png",
        content=_encode(Image.new("RGB", (240, 200), "#e9ecef")),
    )
    return service.update_template_calibration(
        actor,
        template["id"],
        Calibration(
            mask=NormalizedRect(x=0.25, y=0.2, width=0.5, height=0.6),
            anchor=NormalizedPoint(x=0.5, y=0.5),
        ),
    )


def _batch_request(template_id: str, *, count: int = 1) -> BatchCreate:
    return BatchCreate(
        template_id=template_id,
        count=count,
        business_fields=BusinessFields(product_name="Canvas Tote", product_category="tote bag"),
        listing_fields=ListingFields(
            declared_price=18.5,
            suggested_price_usd=29.99,
            category_name="家居收纳 > 包袋",
            skus=[{"name": "Default SKU", "length_cm": 30, "width_cm": 20, "height_cm": 10, "weight_g": 450}],
        ),
        creative_prompt="coastal botanical ink",
    )


def test_new_batch_persists_one_queued_title_row_per_style_and_partial_unique_index(tmp_path: Path) -> None:
    images = ImageRuntime([])
    service = _service(tmp_path, images, None)
    actor = _actor()
    template = _ready_template(service, actor)

    batch = service.create_batch(actor, _batch_request(template["id"], count=2), enqueue=False)

    assert [title["status"] for title in batch["style_titles"]] == ["queued", "queued"]
    with sqlite3.connect(service.database_path) as connection:
        rows = connection.execute(
            "SELECT style_index, style_task_id, normalized_title FROM pod_customization_style_titles WHERE batch_id = ? ORDER BY style_index",
            (batch["id"],),
        ).fetchall()
        assert rows == [(1, "", None), (2, "", None)]
        connection.execute(
            "UPDATE pod_customization_style_titles SET normalized_title = 'same title' WHERE batch_id = ? AND style_index = 1",
            (batch["id"],),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE pod_customization_style_titles SET normalized_title = 'same title' WHERE batch_id = ? AND style_index = 2",
                (batch["id"],),
            )
    service.close()
    images.close()


def test_batch_title_uses_generation_call_id_and_listing_ready_statistics(tmp_path: Path) -> None:
    images = ImageRuntime([_grid(1)])
    titles = TitleRuntime()
    service = _service(tmp_path, images, titles)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request(template["id"]), enqueue=False)

    service.worker.process_batch(batch["id"])
    stored = service.get_batch(actor, batch["id"])
    with sqlite3.connect(service.database_path) as connection:
        call_id = connection.execute(
            "SELECT call_id FROM pod_customization_generation_calls WHERE batch_id = ?",
            (batch["id"],),
        ).fetchone()[0]

    assert titles.requests[0].style_task_id == call_id
    assert titles.requests[0].hero_image == split_grid_2x2(_grid(1))[3]
    assert stored["style_titles"][0]["style_task_id"] == call_id
    assert stored["style_titles"][0]["listing_ready"] is True
    assert service.repository.get_style_copies(batch["id"], actor.workspace_id, actor.id) == {
        1: {
            "title": stored["style_titles"][0]["title"],
            "english_title": "Coastal Botanical Canvas Tote Style 1",
            "description": "Layered ocean fern artwork for style 1.",
        }
    }
    assert stored["title_completed_count"] == 1
    assert stored["title_failed_count"] == 0
    assert stored["listing_ready_count"] == 1
    assert stored["dianxiaomi_export"]["ready"] is True
    assert stored["dianxiaomi_export"]["exportable_style_count"] == 1
    assert service.export_dianxiaomi(actor, batch["id"]).exported_style_count == 1
    assert stored["status"] == "completed"
    summary = service.list_batches(actor)["batches"][0]
    assert summary["listing_ready_count"] == 1
    assert summary["style_titles"] == stored["style_titles"]
    service.close()
    titles.close()
    images.close()


def test_title_failure_preserves_all_four_images_and_settles_batch_failed(tmp_path: Path) -> None:
    images = ImageRuntime([_grid(10)])
    titles = TitleRuntime(failures=1)
    service = _service(tmp_path, images, titles)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request(template["id"]), enqueue=False)

    service.worker.process_batch(batch["id"])
    stored = service.get_batch(actor, batch["id"])

    assert len(stored["items"]) == 4
    assert all(item["status"] == "completed" and item["public_url"] for item in stored["items"])
    assert stored["completed_count"] == 1
    assert stored["style_titles"][0]["status"] == "failed"
    assert stored["style_titles"][0]["listing_ready"] is False
    assert stored["title_failed_count"] == 1
    assert stored["listing_ready_count"] == 0
    assert stored["status"] == "failed"
    service.close()
    titles.close()
    images.close()


def test_detail_publication_failure_does_not_discard_generated_title(tmp_path: Path) -> None:
    images = ImageRuntime([_grid(15)], publish_failures={"detail_a": 2})
    titles = TitleRuntime()
    service = _service(tmp_path, images, titles)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request(template["id"]), enqueue=False)

    service.worker.process_batch(batch["id"])
    stored = service.get_batch(actor, batch["id"])

    assert stored["style_titles"][0]["status"] == "completed"
    assert stored["style_titles"][0]["listing_ready"] is False
    assert stored["title_completed_count"] == 1
    assert stored["listing_ready_count"] == 0
    assert stored["status"] == "failed"
    service.close()
    titles.close()
    images.close()


def test_same_batch_title_generation_serializes_accepted_titles_and_visual_signatures(tmp_path: Path) -> None:
    images = ImageRuntime([_grid(17), _grid(27)])
    titles = SlowTitleRuntime()
    service = _service(tmp_path, images, titles)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request(template["id"], count=2), enqueue=False)

    service.worker.process_batch(batch["id"])

    assert [request.style_index for request in titles.requests] == [1, 2]
    assert titles.max_active == 1
    assert titles.requests[0].accepted_titles == ()
    assert titles.requests[0].accepted_visual_signatures == ()
    assert len(titles.requests[1].accepted_titles) == 1
    assert titles.requests[1].accepted_visual_signatures == (
        "coastal botanical|layered ink|ocean fern",
    )
    service.close()
    titles.close()
    images.close()


def test_duplicate_title_finish_rolls_back_the_style_copy_atomically(tmp_path: Path) -> None:
    images = ImageRuntime([_grid(18), _grid(28)])
    titles = DuplicateTitleRuntime()
    service = _service(tmp_path, images, titles)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request(template["id"], count=2), enqueue=False)

    service.worker.process_batch(batch["id"])
    stored = service.get_batch(actor, batch["id"])
    copies = service.repository.get_style_copies(batch["id"], actor.workspace_id, actor.id)

    assert [title["status"] for title in stored["style_titles"]] == ["completed", "failed"]
    assert set(copies) == {1}
    assert stored["listing_ready_count"] == 1
    assert stored["status"] == "partial_failure"
    service.close()
    titles.close()
    images.close()


def test_title_submit_failure_does_not_interrupt_remaining_image_publications(tmp_path: Path) -> None:
    images = ImageRuntime([_grid(29)])
    titles = SubmitFailTitleRuntime()
    service = _service(tmp_path, images, titles)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request(template["id"]), enqueue=False)

    service.worker.process_batch(batch["id"])
    stored = service.get_batch(actor, batch["id"])

    assert images.published_roles == ["hero", "detail_a", "detail_b", "lifestyle"]
    assert all(item["status"] == "completed" for item in stored["items"])
    assert stored["style_titles"][0]["status"] == "failed"
    assert "title executor unavailable" in stored["style_titles"][0]["error_message"]
    assert stored["listing_ready_count"] == 0
    assert stored["status"] == "failed"
    service.close()
    titles.close()
    images.close()


def test_title_only_regeneration_preserves_task_id_and_does_not_call_image_runtime(tmp_path: Path) -> None:
    images = ImageRuntime([_grid(20)])
    titles = TitleRuntime(failures=1)
    service = _service(tmp_path, images, titles)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request(template["id"]), enqueue=False)
    service.worker.process_batch(batch["id"])
    failed = service.get_batch(actor, batch["id"])["style_titles"][0]
    image_call_count = len(images.requests)

    claimed = service.regenerate_title(actor, batch["id"], 1, enqueue=False)
    service.worker.regenerate_title(batch["id"], 1)
    stored = service.get_batch(actor, batch["id"])

    assert claimed["style_task_id"] == failed["style_task_id"]
    assert len(images.requests) == image_call_count
    assert stored["style_titles"][0]["style_task_id"] == failed["style_task_id"]
    assert [request.hero_image for request in titles.requests] == [
        split_grid_2x2(_grid(20))[3],
        split_grid_2x2(_grid(20))[3],
    ]
    assert stored["style_titles"][0]["status"] == "completed"
    assert stored["listing_ready_count"] == 1
    assert stored["status"] == "completed"
    service.close()
    titles.close()
    images.close()


def test_title_resume_skips_persisted_success_and_uses_only_remaining_calls(tmp_path: Path) -> None:
    images = ImageRuntime([_grid(21)])
    titles = TitleRuntime(failures=1)
    service = _service(tmp_path, images, titles)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request(template["id"]), enqueue=False)
    service.worker.process_batch(batch["id"])
    prior_title_requests = len(titles.requests)

    service.regenerate_title(actor, batch["id"], 1, enqueue=False)
    pending = service.repository.list_pending_billing_runs(actor.workspace_id, actor.id)
    run = next(row for row in pending if row["action_type"] == "title_retry")
    first = run["plan"]["calls"][0]
    service.repository.start_billing_call(run["action_key"], first["call_id"], first["feature"])
    service.repository.record_billing_outcome(
        run["action_key"], PodCallOutcome(first["call_id"], first["feature"], "success")
    )
    service.repository.mark_billing_auth_required(run["action_key"], "restart")

    resumed = service.resume_billing_run(actor, run["run_id"])

    assert resumed["status"] == "settled"
    assert len(titles.requests) == prior_title_requests + 1
    refreshed = service.repository.get_billing_run(run["run_id"], actor.workspace_id, actor.id)
    assert [outcome["status"] for outcome in refreshed["outcomes"]] == [
        "success",
        "success",
        "no_return",
    ]
    service.close()
    titles.close()
    images.close()


def test_completed_title_retry_is_rejected_without_freezing_or_deleting_copy(tmp_path: Path) -> None:
    images = ImageRuntime([_grid(22)])
    titles = TitleRuntime()
    billing = BillingCoordinator()
    service = _service(tmp_path, images, titles, billing)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request(template["id"]), enqueue=False)
    service.worker.process_batch(batch["id"])
    copies_before = service.repository.get_style_copies(batch["id"], actor.workspace_id, actor.id)
    freezes_before = len(billing.freezes)

    with pytest.raises(PodRepositoryError, match="only a failed POD title") as captured:
        service.regenerate_title(actor, batch["id"], 1, enqueue=False)
    stored = service.get_batch(actor, batch["id"])

    assert captured.value.status_code == 409
    assert len(billing.freezes) == freezes_before
    assert stored["style_titles"][0]["status"] == "completed"
    assert service.repository.get_style_copies(batch["id"], actor.workspace_id, actor.id) == copies_before
    service.close()
    titles.close()
    images.close()


def test_completed_historical_title_without_full_copy_is_not_listing_ready(tmp_path: Path) -> None:
    images = ImageRuntime([_grid(23)])
    titles = TitleRuntime()
    service = _service(tmp_path, images, titles)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request(template["id"]), enqueue=False)
    service.worker.process_batch(batch["id"])
    with sqlite3.connect(service.database_path) as connection:
        connection.execute(
            "DELETE FROM pod_customization_style_copy WHERE batch_id = ? AND style_index = 1",
            (batch["id"],),
        )
        connection.commit()

    stored = service.get_batch(actor, batch["id"])
    assert stored["style_titles"][0]["status"] == "completed"
    assert stored["style_titles"][0]["listing_ready"] is False
    assert stored["listing_ready_count"] == 0
    assert service.repository.settle_batch_by_listing_readiness(batch["id"]) == "failed"
    service.close()
    titles.close()
    images.close()


def test_concurrent_completed_title_retries_are_both_rejected_without_freezing(tmp_path: Path) -> None:
    images = ImageRuntime([_grid(21), _grid(31)])
    titles = TitleRuntime()
    billing = BillingCoordinator()
    service = _service(tmp_path, images, titles, billing)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request(template["id"], count=2), enqueue=False)
    service.worker.process_batch(batch["id"])
    barrier = threading.Barrier(2)

    def claim(style_index: int):
        barrier.wait()
        try:
            return service.regenerate_title(actor, batch["id"], style_index, enqueue=False)
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed = list(executor.map(claim, (1, 2)))

    assert claimed == [None, None]
    assert len(billing.freezes) == 1
    active = service.get_batch(actor, batch["id"])
    assert active["status"] == "completed"
    assert all(title["status"] == "completed" for title in active["style_titles"])
    service.close()
    titles.close()
    images.close()


def test_completed_style_retry_preserves_title_and_does_not_freeze(tmp_path: Path) -> None:
    images = ImageRuntime([_grid(30), _grid(40)])
    titles = TitleRuntime()
    billing = BillingCoordinator()
    service = _service(tmp_path, images, titles, billing)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request(template["id"]), enqueue=False)
    service.worker.process_batch(batch["id"])
    before = service.get_batch(actor, batch["id"])["style_titles"][0]

    freezes_before = len(billing.freezes)
    with pytest.raises(PodRepositoryError, match="only a failed POD style") as captured:
        service.regenerate_style(actor, batch["id"], 1, enqueue=False)
    after = service.get_batch(actor, batch["id"])["style_titles"][0]

    assert captured.value.status_code == 409
    assert len(billing.freezes) == freezes_before
    assert after == before
    service.close()
    titles.close()
    images.close()


def test_direct_trial_title_failure_keeps_completed_images(tmp_path: Path) -> None:
    images = ImageRuntime([_grid(50)])
    titles = TitleRuntime(failures=1)
    service = _service(tmp_path, images, titles)
    actor = _actor()
    template = _ready_template(service, actor)

    result = service.run_direct_listing_trial(
        actor,
        DirectListingTrialCreate(
            template_id=template["id"],
            business_fields=BusinessFields(product_name="Canvas Tote", product_category="tote bag"),
            creative_prompt="coastal botanical ink",
        ),
    )

    assert result["status"] == "completed"
    assert len(result["images"]) == 4
    assert all(image["public_url"] for image in result["images"])
    assert result["title"]["style_task_id"] == result["id"]
    assert result["title"]["status"] == "failed"
    assert result["error_message"] == ""
    assert "temporary title provider failure" in result["title"]["error_message"]
    service.close()
    titles.close()
    images.close()


def test_direct_trial_generates_title_after_hero_even_if_a_detail_publication_fails(tmp_path: Path) -> None:
    images = ImageRuntime([_grid(55)], publish_failures={"detail_a": 1})
    titles = TitleRuntime()
    service = _service(tmp_path, images, titles)
    actor = _actor()
    template = _ready_template(service, actor)

    result = service.run_direct_listing_trial(
        actor,
        DirectListingTrialCreate(
            template_id=template["id"],
            business_fields=BusinessFields(product_name="Canvas Tote", product_category="tote bag"),
        ),
    )

    assert result["status"] == "failed"
    assert [image["role"] for image in result["images"]] == ["hero", "detail_a", "detail_b", "lifestyle"]
    assert result["images"][0]["public_url"]
    assert result["title"]["status"] == "completed"
    assert result["title"]["listing_ready"] is False
    service.close()
    titles.close()
    images.close()


def test_old_batch_without_title_rows_returns_empty_compatible_statistics(tmp_path: Path) -> None:
    images = ImageRuntime([])
    service = _service(tmp_path, images, None)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request(template["id"]), enqueue=False)
    with sqlite3.connect(service.database_path) as connection:
        connection.execute("DELETE FROM pod_customization_style_titles WHERE batch_id = ?", (batch["id"],))
        connection.commit()

    stored = service.get_batch(actor, batch["id"])

    assert stored["style_titles"] == []
    assert stored["title_completed_count"] == 0
    assert stored["title_failed_count"] == 0
    assert stored["listing_ready_count"] == 0
    service.close()
    images.close()


def test_title_runtime_does_not_use_local_configuration_as_a_credential_gate(tmp_path: Path) -> None:
    images = ImageRuntime([_grid(60)])
    titles = UnconfiguredTitleRuntime()
    service = _service(tmp_path, images, titles)
    actor = _actor()
    template = _ready_template(service, actor)

    batch = service.create_batch(actor, _batch_request(template["id"]), enqueue=False)
    assert batch["status"] == "queued"
    assert images.requests == []
    assert service.list_batches(actor)["total"] == 1
    assert service.list_direct_listing_trials(actor)["total"] == 0
    service.close()
    titles.close()
    images.close()


def test_title_regeneration_api_returns_claimed_title_without_starting_an_image_call(tmp_path: Path) -> None:
    images = ImageRuntime([_grid(70)])
    titles = TitleRuntime(failures=1)
    app = FastAPI()
    router = create_router(
        tmp_path / "workbench.sqlite3",
        tmp_path / "pod-assets",
        images,
        title_runtime=titles,
        billing_coordinator=BillingCoordinator(),
        start_workers=True,
    )
    app.include_router(router)
    service = getattr(router, "pod_customization_service")
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request(template["id"]), enqueue=False)
    service.worker.process_batch(batch["id"])
    before = service.get_batch(actor, batch["id"])["style_titles"][0]
    image_call_count = len(images.requests)

    response = TestClient(app).post(
        f"/api/pod-customization/batches/{batch['id']}/styles/1/title/regenerate",
        headers={"Authorization": "Bearer dev-admin-token"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["style_task_id"] == before["style_task_id"]
    assert response.json()["status"] == "generating"
    assert len(images.requests) == image_call_count
    service.close()
    titles.close()
    images.close()
