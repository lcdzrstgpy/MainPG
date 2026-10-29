from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from wh_local.modules.pod_customization.billing_contract import (
    POD_BILLING_PROFILE_FREE_FLASH,
    POD_BILLING_PROFILE_FREE_PRO,
    PodCallOutcome,
    PodCallPlan,
    PodExecutionGrant,
)
from wh_local.modules.pod_customization.contracts import (
    FREE_IMAGE_MODEL_FLASH,
    FREE_IMAGE_MODEL_PRO,
    FREE_PANEL_ROLES,
    FreeBatchCreate,
    FreeListingFieldsUpdate,
    FreePanelReorder,
    FreeSpecCardUpdate,
    SpecCardConfig,
)
from wh_local.modules.pod_customization.export import _style_images
from wh_local.modules.pod_customization.remote_billing import (
    build_v2_freeze_payload,
    build_v2_settlement_payload,
)
from wh_local.modules.pod_customization.service import PodCustomizationService
from wh_local.modules.pod_customization.worker import PodBatchWorker
from wh_local.modules.product_processing.infrastructure.media import GeneratedMedia
from wh_local.session import Actor


def _png(index: int) -> bytes:
    color = ((index * 53) % 200 + 30, (index * 97) % 200 + 30, (index * 31) % 200 + 30)
    image = Image.new("RGB", (64, 64), color)
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


class FreeImageRuntime:
    def __init__(self) -> None:
        self.requests = []
        self.publications = []
        self._counter = 0

    def submit(self, *_args, **_kwargs):  # pragma: no cover - free path 不经过 submit
        raise AssertionError("free path must call generate_listing_grid directly")

    def generate_listing_grid(self, request, *, grant=None, call_id=""):
        assert grant is not None and grant.provider_key("ark")
        self._counter += 1
        self.requests.append(request)
        return GeneratedMedia(
            stage="direct_listing_panel",
            content=_png(self._counter),
            content_type="image/png",
            suffix=".png",
            provider="fake-free",
            model=request.model,
            reference_count=len(request.reference_images),
        )

    def publish_listing_image(self, media, *, namespace: str, role: str) -> str:
        self.publications.append((role, media.content[:8]))
        return f"https://cdn.example.com/{namespace}/{role}.png"


class FreeTitleRuntime:
    def __init__(self) -> None:
        self.requests = []

    def generate_title(self, request, *, grant, call_id, call_ids=(), on_outcome=None, on_start=None):
        assert grant.provider_key("ark")
        self.requests.append(request)
        if on_start is not None:
            on_start(call_id)
        if on_outcome is not None:
            on_outcome(call_id, "success")
        title = "Handcrafted rust quilted fabric laundry hamper with hand stitched texture for bathroom bedroom"
        return type("TitleResult", (), {
            "title": title,
            "english_title": "Rust Quilted Laundry Hamper",
            "description": "A rust quilted fabric laundry hamper with hand stitched texture.",
            "visual_theme": "rust quilted textile",
            "motif_keywords": ("quilting", "stitching"),
            "color_keywords": ("rust",),
            "normalized_title": " ".join(title.lower().split()),
            "attempt_count": 1,
            "model": "title-model",
            "prompt_version": "pod-title-v1",
        })()


class FreeBilling:
    def __init__(self) -> None:
        self.plans: list[PodCallPlan] = []
        self.settlements = []

    def freeze(self, actor: Actor, plan: PodCallPlan) -> PodExecutionGrant:  # noqa: ARG002
        self.plans.append(plan)
        return PodExecutionGrant(plan.idempotency_key, 1, "2099-01-01T00:00:00Z", {"ark": "k"})

    def settle(self, actor: Actor, grant, plan, outcomes) -> None:  # noqa: ARG002
        self.settlements.append((plan.idempotency_key, list(outcomes)))

    def regrant(self, actor: Actor, freeze_id: str):  # pragma: no cover - 未使用
        raise AssertionError("unexpected regrant")


def _service(
    tmp_path: Path,
    runtime: FreeImageRuntime,
    billing: FreeBilling,
    title_runtime=None,
) -> PodCustomizationService:
    service = PodCustomizationService(
        tmp_path / "workbench.sqlite3",
        tmp_path / "pod-assets",
        runtime,
        title_runtime=title_runtime,
        billing_coordinator=billing,
        start_workers=False,
    )
    service.worker = PodBatchWorker(
        service.repository, service.assets, runtime, title_runtime=title_runtime
    )
    return service


def _actor() -> Actor:
    return Actor(id="operator-1", username="operator-1", role="operator", workspace_id="workspace-a")


def _create(
    service: PodCustomizationService,
    *,
    model: str = FREE_IMAGE_MODEL_FLASH,
    target: int = 4,
    title_enabled: bool = False,
):
    return service.create_free_batch(
        _actor(),
        FreeBatchCreate(
            client_request_id="req-1",
            image_model=model,  # type: ignore[arg-type]
            target_image_count=target,
            prompt="  a red fox in the snow  ",
            title_enabled=title_enabled,
            business_fields={"product_name": "洗衣篓", "product_category": "收纳用品"},  # type: ignore[arg-type]
        ),
        enqueue=False,
    )


def test_free_batch_generates_one_image_with_verbatim_prompt(tmp_path: Path) -> None:
    runtime = FreeImageRuntime()
    billing = FreeBilling()
    service = _service(tmp_path, runtime, billing)
    batch = _create(service)

    plan = billing.plans[0]
    assert plan.billing_profile == POD_BILLING_PROFILE_FREE_FLASH
    assert plan.free_variants == (1,)
    assert plan.product_batch_freeze_payload() == {
        "idempotency_key": plan.idempotency_key,
        "link_count": 1,
        "item_count": 1,
        "scope": ["four_grid"],
        "billing_profile": POD_BILLING_PROFILE_FREE_FLASH,
    }

    service.worker.process_batch(batch["id"])

    assert len(runtime.requests) == 1
    assert runtime.requests[0].prompt == "  a red fox in the snow  "
    stored = service.get_free_batch(_actor(), batch["id"])
    assert stored["target_image_count"] == 4
    assert stored["status"] == "completed"
    assert stored["items"][0]["variant_index"] == 1
    assert stored["items"][0]["prompt"] == "  a red fox in the snow  "
    assert runtime.publications[0][0] == "hero"


def test_free_next_slot_uses_first_image_and_supports_out_of_order(tmp_path: Path) -> None:
    runtime = FreeImageRuntime()
    billing = FreeBilling()
    service = _service(tmp_path, runtime, billing)
    batch = _create(service, model=FREE_IMAGE_MODEL_PRO)
    service.worker.process_batch(batch["id"])

    service.generate_free_panel(_actor(), batch["id"], 4, "fourth", enqueue=False)
    service.worker.process_batch(batch["id"])
    assert runtime.requests[-1].prompt == "fourth"
    assert len(runtime.requests[-1].reference_images) == 1
    assert runtime.requests[-1].model == FREE_IMAGE_MODEL_PRO

    stored = service.get_free_batch(_actor(), batch["id"])
    assert [item["variant_index"] for item in stored["items"]] == [1, 4]
    assert stored["items"][1]["status"] == "completed"


def test_free_regenerate_replaces_old_image_and_bills_a_new_link(tmp_path: Path) -> None:
    runtime = FreeImageRuntime()
    billing = FreeBilling()
    service = _service(tmp_path, runtime, billing)
    batch = _create(service)
    service.worker.process_batch(batch["id"])
    first = service.get_free_batch(_actor(), batch["id"])["items"][0]
    first_asset = first["pattern_preview_url"]
    assert first_asset

    service.generate_free_panel(_actor(), batch["id"], 1, "a new fox", enqueue=False)
    service.worker.process_batch(batch["id"])
    second = service.get_free_batch(_actor(), batch["id"])["items"][0]
    assert second["status"] == "completed"
    assert second["pattern_preview_url"] != first_asset
    assert runtime.requests[-1].prompt == "a new fox"
    # 两次生成 = 两个独立的 1-link 冻结。
    assert [plan.free_panel_count for plan in billing.plans] == [1, 1]


def test_free_spec_card_prints_only_on_target_variant(tmp_path: Path) -> None:
    runtime = FreeImageRuntime()
    billing = FreeBilling()
    service = _service(tmp_path, runtime, billing)
    batch = _create(service, target=4)
    service.update_free_listing_fields(
        _actor(),
        batch["id"],
        FreeListingFieldsUpdate.model_validate({
            "listing_fields": {
                "title_mode": "long",
                "category_name": "收纳篮",
                "skus": [{"name": "S", "declared_price": 9.9, "weight_g": 300}],
                "spec_card": {
                    "enabled": True,
                    "style": "light",
                    "corner": "bottom-right",
                    "display_unit": "cm",
                    "cells": [["SKU", "长", "宽", "高"], ["S", "20", "20", "30"]],
                    "target_variant": 2,
                },
            }
        }),
    )

    service.worker.process_batch(batch["id"])
    service.generate_free_panel(_actor(), batch["id"], 2, "second", enqueue=False)
    service.worker.process_batch(batch["id"])

    # 目标槽位（第 2 张）发布的是叠加尺寸卡后的 JPEG，其他槽位保持原图。
    assert runtime.publications[0][1].startswith(b"\x89PNG")
    assert runtime.publications[1][0] == FREE_PANEL_ROLES[1]
    assert runtime.publications[1][1].startswith(b"\xff\xd8\xff")


def _spec_card(target_variant: int) -> SpecCardConfig:
    return SpecCardConfig(
        enabled=True,
        style="light",
        corner="bottom-right",
        display_unit="cm",
        cells=(("SKU", "长", "宽", "高"), ("S", "20", "20", "30")),
        target_variant=target_variant,
    )


def test_free_spec_card_update_reprints_existing_images(tmp_path: Path) -> None:
    runtime = FreeImageRuntime()
    billing = FreeBilling()
    service = _service(tmp_path, runtime, billing)
    batch = _create(service, target=4)
    service.worker.process_batch(batch["id"])
    service.generate_free_panel(_actor(), batch["id"], 2, "second", enqueue=False)
    service.worker.process_batch(batch["id"])
    before = {
        item["variant_index"]: item for item in service.get_free_batch(_actor(), batch["id"])["items"]
    }
    assert all(item["composite_preview_url"] == item["pattern_preview_url"] for item in before.values())

    # 保存尺寸卡（目标第 2 张）：只补印这一张，不重新生图。
    service.update_free_spec_card(
        _actor(), batch["id"], FreeSpecCardUpdate(spec_card=_spec_card(2))
    )
    after = {
        item["variant_index"]: item for item in service.get_free_batch(_actor(), batch["id"])["items"]
    }
    assert after[2]["composite_preview_url"] != after[2]["pattern_preview_url"]
    assert after[1]["composite_preview_url"] == after[1]["pattern_preview_url"]
    assert runtime.publications[-1][1].startswith(b"\xff\xd8\xff")

    # 把目标改回第 1 张：第 2 张应还原成干净母版，第 1 张补印。
    service.update_free_spec_card(
        _actor(), batch["id"], FreeSpecCardUpdate(spec_card=_spec_card(1))
    )
    restored = {
        item["variant_index"]: item for item in service.get_free_batch(_actor(), batch["id"])["items"]
    }
    assert restored[2]["composite_preview_url"] == restored[2]["pattern_preview_url"]
    assert restored[1]["composite_preview_url"] != restored[1]["pattern_preview_url"]


def test_free_reorder_moves_panels_and_follows_spec_card_slot(tmp_path: Path) -> None:
    runtime = FreeImageRuntime()
    billing = FreeBilling()
    service = _service(tmp_path, runtime, billing)
    batch = _create(service, target=4)
    service.worker.process_batch(batch["id"])
    for variant, prompt in ((2, "second"), (3, "third")):
        service.generate_free_panel(_actor(), batch["id"], variant, prompt, enqueue=False)
        service.worker.process_batch(batch["id"])
    # 尺寸卡印在第 2 张。
    service.update_free_spec_card(
        _actor(), batch["id"], FreeSpecCardUpdate(spec_card=_spec_card(2))
    )

    service.reorder_free_panels(
        _actor(), batch["id"], FreePanelReorder(from_variant=2, to_variant=3)
    )

    items = {
        item["variant_index"]: item for item in service.get_free_batch(_actor(), batch["id"])["items"]
    }
    # 图与提示词一起换位：第 3 张现在是原第 2 张。
    assert items[3]["prompt"] == "second"
    assert items[2]["prompt"] == "third"
    # 换序本身不转印：卡片仍跟着原图片（现第 3 张）。
    assert items[3]["composite_preview_url"] != items[3]["pattern_preview_url"]
    assert items[2]["composite_preview_url"] == items[2]["pattern_preview_url"]

    # 点「重印本批次」后才按新顺序把卡印到第 2 张，旧图还原。
    service.reprint_free_spec_card(_actor(), batch["id"])
    reprinted = {
        item["variant_index"]: item for item in service.get_free_batch(_actor(), batch["id"])["items"]
    }
    assert reprinted[2]["composite_preview_url"] != reprinted[2]["pattern_preview_url"]
    assert reprinted[3]["composite_preview_url"] == reprinted[3]["pattern_preview_url"]


def test_free_main_regeneration_clears_downstream_panels(tmp_path: Path) -> None:
    runtime = FreeImageRuntime()
    billing = FreeBilling()
    service = _service(tmp_path, runtime, billing)
    batch = _create(service, target=4)
    service.worker.process_batch(batch["id"])
    service.generate_free_panel(_actor(), batch["id"], 2, "second", enqueue=False)
    service.worker.process_batch(batch["id"])
    assert [item["variant_index"] for item in service.get_free_batch(_actor(), batch["id"])["items"]] == [1, 2]

    service.generate_free_panel(_actor(), batch["id"], 1, "new main", enqueue=False)

    stored = service.get_free_batch(_actor(), batch["id"])
    # 重做主图：第 2 张衍生图连同结果行一起清除，只留重做中的主图。
    assert [item["variant_index"] for item in stored["items"]] == [1]
    assert stored["items"][0]["status"] == "queued"
    # 提示词保留，方便用户稍后重新生成同一槽位。
    assert service.repository.get_free_panel(batch["id"], 2) is not None


def test_free_title_runs_only_after_all_target_images_complete(tmp_path: Path) -> None:
    runtime = FreeImageRuntime()
    titles = FreeTitleRuntime()
    billing = FreeBilling()
    service = _service(tmp_path, runtime, billing, title_runtime=titles)
    batch = _create(service, target=4, title_enabled=True)
    service.worker.process_batch(batch["id"])
    assert not titles.requests
    assert service.get_free_batch(_actor(), batch["id"])["style_titles"][0]["status"] == "queued"

    for variant, prompt in ((2, "second"), (3, "third")):
        service.generate_free_panel(_actor(), batch["id"], variant, prompt, enqueue=False)
        service.worker.process_batch(batch["id"])
        assert not titles.requests

    service.generate_free_panel(_actor(), batch["id"], 4, "fourth", enqueue=False)
    assert billing.plans[-1].product_batch_freeze_payload()["scope"] == ["title", "four_grid"]
    service.worker.process_batch(batch["id"])

    stored = service.get_free_batch(_actor(), batch["id"])
    assert stored["style_titles"][0]["status"] == "completed"
    assert titles.requests and titles.requests[0].style_index == 1


def test_free_settlement_folds_each_image_to_one_link(tmp_path: Path) -> None:
    plan = PodCallPlan.for_free_panels(
        "batch-1",
        variants=(2, 3),
        billing_profile=POD_BILLING_PROFILE_FREE_FLASH,
    )
    outcomes = [
        PodCallOutcome(
            call.call_id,
            call.feature,
            "success" if index == 0 else "no_return",
        )
        for index, call in enumerate(plan.calls)
    ]
    payload = plan.product_batch_settlement_payload(tuple(outcomes))
    assert payload == {
        "items": [
            {"link_idx": 1, "subitems": [{"feature": "four_grid", "status": "success"}]},
            {"link_idx": 2, "subitems": [{"feature": "four_grid", "status": "no_return"}]},
        ]
    }


def test_free_v2_payload_uses_panel_units_and_image_feature() -> None:
    plan = PodCallPlan.for_free_panels(
        "batch-1",
        variants=(1,),
        billing_profile=POD_BILLING_PROFILE_FREE_FLASH,
        title_enabled=True,
    )
    freeze = build_v2_freeze_payload(plan)
    assert freeze["module"] == "pod_free"
    assert freeze["unit_kind"] == "panel"
    assert freeze["link_count"] == 1
    assert freeze["scope"] == {"image": True, "title": True}
    assert freeze["unit_ids"] == ["panel-0"]

    outcomes = [
        PodCallOutcome(call.call_id, call.feature, "success") for call in plan.calls
    ]
    settlement = build_v2_settlement_payload(plan, tuple(outcomes))
    assert len(settlement["links"]) == 1
    link = settlement["links"][0]
    assert link["link_idx"] == 0
    assert link["unit_id"] == "panel-0"
    assert {item["feature"] for item in link["subitems"]} == {"image", "title"}


def test_free_export_maps_five_or_six_images_to_fixed_roles_plus_extras() -> None:
    items = [
        {
            "variant_index": variant,
            "status": "completed",
            "public_url": f"https://cdn.example.com/{variant}.png",
        }
        for variant in range(1, 7)
    ]
    images = _style_images({"mode": "free"}, items)
    assert images == {
        "hero": "https://cdn.example.com/1.png",
        "detail_a": "https://cdn.example.com/2.png",
        "detail_b": "https://cdn.example.com/3.png",
        "lifestyle": "https://cdn.example.com/4.png",
        "extra_1": "https://cdn.example.com/5.png",
        "extra_2": "https://cdn.example.com/6.png",
    }
    assert _style_images({"mode": "free"}, items[:3]) is None
