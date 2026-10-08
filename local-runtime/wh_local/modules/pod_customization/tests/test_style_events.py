from __future__ import annotations

import hashlib
import io
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw

from wh_local.modules.pod_customization.billing_contract import PodExecutionGrant
from wh_local.modules.pod_customization.contracts import (
    BatchCreate,
    BusinessFields,
    Calibration,
    ListingFields,
    NormalizedPoint,
    NormalizedRect,
)
from wh_local.modules.pod_customization.images import split_grid_2x2
from wh_local.modules.pod_customization.repository import PodCustomizationRepository
from wh_local.modules.pod_customization.service import PodCustomizationService
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
    def __init__(self, grids: list[bytes]) -> None:
        self.grids = list(grids)
        self.executor = ThreadPoolExecutor(max_workers=2)

    def submit(self, function, *args, **kwargs):
        return self.executor.submit(function, *args, **kwargs)

    def generate_listing_grid(self, request, *, grant, call_id):
        assert grant.provider_key("wuyin")
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
        digest = hashlib.sha256(media.content).hexdigest()[:10]
        return f"https://images.example/{namespace}/{role}/{digest}.png"

    def close(self) -> None:
        self.executor.shutdown(wait=True)


class TitleRuntime:
    configured = True

    def __init__(self) -> None:
        self.executor = ThreadPoolExecutor(max_workers=2)

    def submit(self, function, *args, **kwargs):
        return self.executor.submit(function, *args, **kwargs)

    def generate_title(self, request, *, grant, call_id, call_ids=(), on_outcome=None):
        assert grant.provider_key("ark")
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


class BillingCoordinator:
    def freeze(self, actor, plan):
        return PodExecutionGrant(
            "freeze-1", 1, "2099-01-01T00:00:00Z", {"wuyin": "test-wuyin", "ark": "test-ark"}
        )

    def settle(self, actor, grant, plan, outcomes):
        return None

    def regrant(self, actor, freeze_id):
        return self.freeze(actor, None)


def _actor() -> Actor:
    return Actor(id="local-demo-admin", username="local-demo", role="admin")


def _service(tmp_path: Path, images: ImageRuntime, titles: TitleRuntime) -> PodCustomizationService:
    return PodCustomizationService(
        tmp_path / "workbench.sqlite3",
        tmp_path / "pod-assets",
        images,
        title_runtime=titles,
        billing_coordinator=BillingCoordinator(),
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


def _batch_request(template_id: str) -> BatchCreate:
    return BatchCreate(
        template_id=template_id,
        count=1,
        business_fields=BusinessFields(product_name="Canvas Tote", product_category="tote bag"),
        listing_fields=ListingFields(
            suggested_price_usd=29.99,
            category_name="家居收纳 > 包袋",
            skus=[{"name": "Default SKU", "declared_price": 18.5, "weight_g": 450}],
            spec_card={"cells": [["尺寸图", "长", "宽", "高"], ["Default SKU", "30", "20", "10"]]},
        ),
        creative_prompt="coastal botanical ink",
    )


def _events(database: Path, batch_id: str) -> list[sqlite3.Row]:
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            """SELECT event_id, style_index, variant_index, event, status, error
               FROM pod_customization_style_events
               WHERE batch_id = ? ORDER BY event_id""",
            (batch_id,),
        ).fetchall()


def test_batch_records_batch_grid_and_title_events_with_correct_style_index(tmp_path: Path) -> None:
    images = ImageRuntime([_grid(1)])
    titles = TitleRuntime()
    service = _service(tmp_path, images, titles)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request(template["id"]), enqueue=False)

    service.worker.process_batch(batch["id"])
    assert service.get_batch(actor, batch["id"])["status"] == "completed"

    rows = _events(service.database_path, batch["id"])
    by_event: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_event.setdefault(row["event"], []).append(row)

    assert {"batch_status", "style_grid_status", "style_title_status"} <= set(by_event)
    # 批次级事件固定 style_index=0。
    assert all(row["style_index"] == 0 for row in by_event["batch_status"])
    # 每款每图事件带正确的 style_index 与 variant_index。
    grid_rows = by_event["style_grid_status"]
    assert {row["style_index"] for row in grid_rows} == {1}
    assert {row["variant_index"] for row in grid_rows} == {1, 2, 3, 4}
    assert {row["status"] for row in grid_rows} == {"completed"}
    # 标题生命周期：先 generating 再 completed。
    title_rows = by_event["style_title_status"]
    assert {row["style_index"] for row in title_rows} == {1}
    assert "completed" in {row["status"] for row in title_rows}
    assert "generating" in {row["status"] for row in title_rows}

    service.close()
    titles.close()
    images.close()


def test_style_events_are_append_only_not_updated_in_place(tmp_path: Path) -> None:
    images = ImageRuntime([_grid(2), _grid(12)])
    titles = TitleRuntime()
    service = _service(tmp_path, images, titles)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request(template["id"]), enqueue=False)
    service.worker.process_batch(batch["id"])

    before = _events(service.database_path, batch["id"])
    before_ids = {row["event_id"] for row in before}
    before_grid_style1 = [r for r in before if r["event"] == "style_grid_status" and r["style_index"] == 1]
    assert len(before_grid_style1) >= 4

    # 同一款再次变化（整款重生成）只追加新行，旧行保持不变。
    service.regenerate_style(actor, batch["id"], 1, enqueue=False)

    after = _events(service.database_path, batch["id"])
    after_ids = {row["event_id"] for row in after}
    assert before_ids < after_ids  # 旧行全部保留，只多不少
    assert {row["event_id"] for row in after if row["event_id"] in before_ids} == before_ids
    # 整款重生成作为新事件追加，而不是改动已有行。
    appended = [row for row in after if row["event_id"] not in before_ids]
    assert {row["event"] for row in appended} == {"style_regenerate"}
    # 同一款的状态多次变化天然产生多行（标题 generating + completed）。
    title_rows_style1 = [r for r in after if r["event"] == "style_title_status" and r["style_index"] == 1]
    assert len(title_rows_style1) >= 2

    # record_style_event 只增不改。
    service.repository.record_style_event(batch["id"], "batch_retry", status="queued")
    final = _events(service.database_path, batch["id"])
    assert {row["event_id"] for row in final} == after_ids | {final[-1]["event_id"]}

    service.close()
    titles.close()
    images.close()


def test_delete_batch_cascades_its_style_events_without_touching_others(tmp_path: Path) -> None:
    images = ImageRuntime([_grid(3), _grid(13)])
    titles = TitleRuntime()
    service = _service(tmp_path, images, titles)
    actor = _actor()
    template = _ready_template(service, actor)
    first = service.create_batch(actor, _batch_request(template["id"]), enqueue=False)
    second = service.create_batch(actor, _batch_request(template["id"]), enqueue=False)
    service.worker.process_batch(first["id"])
    service.worker.process_batch(second["id"])

    assert _events(service.database_path, first["id"])
    assert _events(service.database_path, second["id"])
    second_rows = _events(service.database_path, second["id"])

    service.repository.delete_batch(first["id"], actor.workspace_id, actor.id)

    assert _events(service.database_path, first["id"]) == []
    assert _events(service.database_path, second["id"]) == second_rows

    service.close()
    titles.close()
    images.close()


def test_style_event_write_failure_never_breaks_the_batch_flow(tmp_path: Path, monkeypatch) -> None:
    def _boom(*_args, **_kwargs):
        raise RuntimeError("style event insert exploded")

    monkeypatch.setattr(PodCustomizationRepository, "_insert_style_event", staticmethod(_boom))

    images = ImageRuntime([_grid(4)])
    titles = TitleRuntime()
    service = _service(tmp_path, images, titles)
    actor = _actor()
    template = _ready_template(service, actor)
    batch = service.create_batch(actor, _batch_request(template["id"]), enqueue=False)

    service.worker.process_batch(batch["id"])

    stored = service.get_batch(actor, batch["id"])
    assert stored["status"] == "completed"
    assert stored["style_titles"][0]["status"] == "completed"
    assert _events(service.database_path, batch["id"]) == []

    service.close()
    titles.close()
    images.close()
