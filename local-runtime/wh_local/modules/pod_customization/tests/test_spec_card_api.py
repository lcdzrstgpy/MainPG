"""POD 第 4 张图「规格卡」HTTP 层测试：同源预览（不落库/不计费）与终态重印。

方案：``docs/superpowers/specs/2026-09-10-pod-spec-card-plan.md`` §7 / §8。
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from wh_local.modules.pod_customization import spec_card
from wh_local.modules.pod_customization.billing_contract import PodExecutionGrant
from wh_local.modules.pod_customization.contracts import (
    SPEC_CARD_MAX_COLUMNS,
    SPEC_CARD_MAX_ROWS,
    BatchCreate,
    BusinessFields,
    Calibration,
    ListingFields,
    NormalizedPoint,
    NormalizedRect,
)
from wh_local.modules.pod_customization.router import create_router
from wh_local.modules.pod_customization.service import PodCustomizationService
from wh_local.modules.product_processing.infrastructure.media import GeneratedMedia
from wh_local.session import Actor


API = "/api/pod-customization"
HEADERS = {"Authorization": "Bearer dev-admin-token"}
ACTOR = Actor(id="local-demo-admin", username="local-demo", role="admin")
CARD_KIND = "direct_listing_panel_card"


def _encode(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _pattern(index: int) -> Image.Image:
    color = ((index * 47 + 40) % 220 + 20, (index * 71 + 60) % 210 + 20, (index * 29 + 90) % 200 + 30)
    image = Image.new("RGB", (96, 96), color)
    draw = ImageDraw.Draw(image)
    for offset in range(-96, 192, 5 + index % 13):
        draw.line((offset, 0, offset - 96, 96), fill="white", width=2)
    return image


def _grid(first_index: int) -> bytes:
    image = Image.new("RGB", (192, 192), "white")
    for offset, position in enumerate(((0, 0), (96, 0), (0, 96), (96, 96))):
        image.paste(_pattern(first_index + offset), position)
    return _encode(image)


class ApiRuntime:
    """参考图锁定链路的假实现：生图 → 本地拆分 → 内容寻址发布。"""

    def __init__(self, grids: list[bytes]) -> None:
        self.grids = list(grids)
        self.publications: list[tuple[str, str]] = []
        self.executor = ThreadPoolExecutor(max_workers=4)

    def submit(self, function, *args, **kwargs):
        return self.executor.submit(function, *args, **kwargs)

    def generate_listing_grid(self, _request, *, grant=None, call_id="") -> GeneratedMedia:
        assert grant is not None and grant.provider_key("wuyin")
        return GeneratedMedia(
            stage="grid_image",
            content=self.grids.pop(0),
            content_type="image/png",
            suffix=".png",
            provider="fake-listing",
            model="fake",
            reference_count=1,
        )

    def split_listing_grid(self, media: GeneratedMedia) -> list[GeneratedMedia]:
        from wh_local.modules.pod_customization.images import split_grid_2x2

        return [
            GeneratedMedia(
                stage=f"grid_image_{index}",
                content=content,
                content_type="image/png",
                suffix=".png",
                provider=media.provider,
                model=media.model,
                reference_count=1,
            )
            for index, content in enumerate(split_grid_2x2(media.content), start=1)
        ]

    def publish_listing_image(self, media: GeneratedMedia, *, namespace: str, role: str) -> str:
        digest = hashlib.sha256(media.content).hexdigest()[:12]
        url = f"https://cos.example.com/{namespace}/{role}/{digest}{media.suffix}"
        self.publications.append((role, url))
        return url

    def close(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=True)


class Billing:
    def __init__(self) -> None:
        self.freezes: list[object] = []
        self.settlements: list[object] = []

    def freeze(self, _actor, plan):
        self.freezes.append(plan)
        return PodExecutionGrant(
            "freeze-1", 1, "2099-01-01T00:00:00Z", {"wuyin": "test-wuyin-key", "ark": "test-ark-key"}
        )

    def settle(self, _actor, _grant, plan, outcomes):
        self.settlements.append((plan, tuple(outcomes)))

    def regrant(self, actor, freeze_id):
        return self.freeze(actor, None)


def _service(tmp_path: Path, runtime: ApiRuntime, billing: Billing) -> PodCustomizationService:
    return PodCustomizationService(
        tmp_path / "workbench.sqlite3",
        tmp_path / "pod-assets",
        runtime,
        billing_coordinator=billing,
        start_workers=True,
    )


def _client(tmp_path: Path, runtime: ApiRuntime, billing: Billing) -> tuple[TestClient, PodCustomizationService]:
    """与 worker 共享同一份 sqlite 与资产目录的 HTTP 客户端（start_workers=False）。"""

    app = FastAPI()
    router = create_router(
        tmp_path / "workbench.sqlite3",
        tmp_path / "pod-assets",
        runtime,
        billing_coordinator=billing,
        start_workers=False,
    )
    app.include_router(router)
    return TestClient(app), router.pod_customization_service


def _batch_request(template_id: str, *, count: int, cells: list[list[str]] | None) -> BatchCreate:
    return BatchCreate(
        template_id=template_id,
        count=count,
        prompt_version="v1",
        business_fields=BusinessFields(product_name="Tote bag", product_category="bags"),
        listing_fields=ListingFields(
            declared_price=18.5,
            suggested_price_usd=29.99,
            category_name="家居收纳 > 包袋",
            skus=[{"name": "Default SKU", "length_cm": 30, "width_cm": 20, "height_cm": 10, "weight_g": 450}],
            spec_card=None if cells is None else {"cells": cells},
        ),
    )


def _upload_template(service: PodCustomizationService, *, side: int = 400) -> str:
    template = service.upload_template(
        ACTOR,
        name="Fixed tote scene",
        filename="scene.png",
        content=_encode(Image.new("RGB", (side, side), "#e9ecef")),
    )
    service.update_template_calibration(
        ACTOR,
        template["id"],
        Calibration(
            mask=NormalizedRect(x=0.25, y=0.2, width=0.5, height=0.6),
            anchor=NormalizedPoint(x=0.5, y=0.5),
        ),
    )
    return template["id"]


def _seed_batch(
    service: PodCustomizationService,
    runtime: ApiRuntime,
    *,
    count: int = 2,
    cells: list[list[str]] | None = None,
    process: bool = True,
) -> dict:
    template_id = _upload_template(service)
    batch = service.create_batch(ACTOR, _batch_request(template_id, count=count, cells=cells), enqueue=False)
    if process:
        service.worker.process_batch(batch["id"])
    return service.get_batch(ACTOR, batch["id"])


def _urls_by_style_role(batch: dict) -> dict[tuple[int, str], str]:
    return {
        (int(item["style_index"]), str(item["role"])): str(item["public_url"])
        for item in batch["items"]
    }


def _asset_ids_by_style_role(service: PodCustomizationService, batch_id: str) -> dict[tuple[int, str], str]:
    rows = service.repository.get_batch_internal(batch_id)["items"]
    return {
        (int(row["style_index"]), str(row["role"])): str(row["pattern_asset_id"]) for row in rows
    }


def _table_counts(service: PodCustomizationService) -> dict[str, int]:
    tables = (
        "pod_customization_batches",
        "pod_customization_assets",
        "pod_customization_billing_runs",
        "pod_customization_generation_calls",
        "pod_customization_style_grid_publications",
    )
    with service.repository._connect() as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }


def _assets_of_kind(service: PodCustomizationService, kind: str) -> list[dict]:
    with service.repository._connect() as connection:
        rows = connection.execute(
            "SELECT * FROM pod_customization_assets WHERE kind = ? ORDER BY created_at, rowid",
            (kind,),
        ).fetchall()
    return [dict(row) for row in rows]


def _published_digest(public_url: str) -> str:
    return public_url.rsplit("/", 1)[-1].split(".", 1)[0]


def _decode_data_url(data_url: str) -> bytes:
    assert data_url.startswith("data:image/jpeg;base64,"), data_url[:40]
    return base64.b64decode(data_url.split(",", 1)[1])


def test_spec_card_preview_returns_a_data_url_without_persisting_or_billing(tmp_path: Path) -> None:
    billing = Billing()
    runtime = ApiRuntime([])
    client, service = _client(tmp_path, runtime, billing)
    before = _table_counts(service)

    response = client.post(
        f"{API}/spec-card/preview",
        headers=HEADERS,
        json={"cells": [["尺寸", "30 × 20 × 10 cm"], ["材质", "帆布"]], "style": "dark", "corner": "top-left"},
    )

    assert response.status_code == 200
    content = _decode_data_url(response.json()["image"])
    assert Image.open(io.BytesIO(content)).size == (800, 800)
    assert _table_counts(service) == before
    assert billing.freezes == []
    assert runtime.publications == []
    service.close()
    runtime.close()


def test_spec_card_preview_uses_the_template_base_when_available(tmp_path: Path) -> None:
    billing = Billing()
    runtime = ApiRuntime([])
    client, service = _client(tmp_path, runtime, billing)
    template_id = _upload_template(service, side=400)

    body = {"cells": [["尺寸", "30cm"]], "style": "light", "corner": "bottom-right"}
    with_template = client.post(f"{API}/spec-card/preview", headers=HEADERS, json={**body, "base_template_id": template_id})
    without_template = client.post(f"{API}/spec-card/preview", headers=HEADERS, json={**body, "base_template_id": "missing-template"})

    assert with_template.status_code == 200
    assert without_template.status_code == 200
    # 有模板时按模板尺寸出图；模板缺失/不可用时退回 800×800 空白示意底图，不报错。
    assert Image.open(io.BytesIO(_decode_data_url(with_template.json()["image"]))).size == (400, 400)
    assert Image.open(io.BytesIO(_decode_data_url(without_template.json()["image"]))).size == (800, 800)
    service.close()
    runtime.close()


def test_batch_create_freezes_the_spec_card_snapshot_without_stripping_cells(tmp_path: Path) -> None:
    billing = Billing()
    runtime = ApiRuntime([])
    client, service = _client(tmp_path, runtime, billing)
    template_id = _upload_template(service)
    listing_fields = {
        "declared_price": 18.5,
        "suggested_price_usd": 29.99,
        "category_name": "家居收纳 > 包袋",
        "skus": [{"name": "Default SKU", "length_cm": 30, "width_cm": 20, "height_cm": 10, "weight_g": 450}],
    }

    with_card = client.post(
        f"{API}/batches",
        headers=HEADERS,
        json={
            "template_id": template_id,
            "count": 1,
            "prompt_version": "v1",
            "business_fields": {"product_category": "bags"},
            "listing_fields": {
                **listing_fields,
                "spec_card": {"style": "dark", "corner": "top-left", "cells": [[" 尺寸 ", "30cm"]]},
            },
        },
    )
    without_card = client.post(
        f"{API}/batches",
        headers=HEADERS,
        json={
            "template_id": template_id,
            "count": 1,
            "prompt_version": "v1",
            "business_fields": {"product_category": "bags"},
            "listing_fields": listing_fields,
        },
    )

    assert with_card.status_code == 200, with_card.text
    assert with_card.json()["listing_fields"]["spec_card"] == {
        "enabled": True,
        "style": "dark",
        "corner": "top-left",
        # 单元格文本原样冻结：不 strip（父模型的 str_strip_whitespace 不传播到嵌套模型）。
        "cells": [[" 尺寸 ", "30cm"]],
    }
    # 没有填表的老请求仍然可用（可选字段）。
    assert without_card.status_code == 200, without_card.text
    assert without_card.json()["listing_fields"]["spec_card"] is None
    service.close()
    runtime.close()


def test_spec_card_preview_rejects_illegal_configuration(tmp_path: Path) -> None:
    billing = Billing()
    runtime = ApiRuntime([])
    client, service = _client(tmp_path, runtime, billing)

    cases = (
        ({"cells": []}, "规格卡至少需要一个非空单元格"),
        ({"cells": [["a"]], "style": "neon"}, "规格卡风格必须是 light 或 dark"),
        ({"cells": [["a"]], "corner": "middle"}, "规格卡位置必须是右下、左下、右上、左上之一"),
        ({"cells": [["a"]] * (SPEC_CARD_MAX_ROWS + 1)}, f"规格卡最多 {SPEC_CARD_MAX_ROWS} 行"),
        ({"cells": [["a"] * (SPEC_CARD_MAX_COLUMNS + 1)]}, f"规格卡最多 {SPEC_CARD_MAX_COLUMNS} 列"),
        ({"cells": [[123]]}, "规格卡表格单元格必须是文本"),
    )
    for body, detail in cases:
        response = client.post(f"{API}/spec-card/preview", headers=HEADERS, json={**body, "style": body.get("style", "light"), "corner": body.get("corner", "bottom-right")})
        assert response.status_code == 400, body
        assert response.json()["detail"] == detail, body
    service.close()
    runtime.close()


def test_spec_card_reprint_rejects_a_batch_that_is_not_terminal(tmp_path: Path) -> None:
    billing = Billing()
    runtime = ApiRuntime([])
    client, service = _client(tmp_path, runtime, billing)
    # 生成中的批次只读冻结：不启 worker，直接留一个 queued 批次（方案 §10.3）。
    template_id = _upload_template(service)
    batch = service.create_batch(
        ACTOR, _batch_request(template_id, count=1, cells=[["尺寸", "30cm"]]), enqueue=False
    )

    response = client.post(
        f"{API}/batches/{batch['id']}/spec-card/reprint",
        headers=HEADERS,
        json={"cells": [["尺寸", "30cm"]]},
    )

    assert batch["status"] == "queued"
    assert response.status_code == 409
    assert response.json()["detail"] == "批次尚未完成，暂不能重新合成标注"
    service.close()
    runtime.close()


def test_spec_card_reprint_rejects_an_empty_table_and_keeps_the_saved_config(tmp_path: Path) -> None:
    billing = Billing()
    runtime = ApiRuntime([_grid(0), _grid(10)])
    seed = _service(tmp_path, runtime, billing)
    batch = _seed_batch(seed, runtime, count=1, cells=[["尺寸", "30cm"]])
    seed.close()
    client, service = _client(tmp_path, runtime, billing)

    response = client.post(
        f"{API}/batches/{batch['id']}/spec-card/reprint",
        headers=HEADERS,
        json={"cells": []},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "规格卡至少需要一个非空单元格"
    stored = client.get(f"{API}/batches/{batch['id']}", headers=HEADERS).json()
    assert stored["listing_fields"]["spec_card"]["cells"] == [["尺寸", "30cm"]]
    service.close()
    runtime.close()


def test_spec_card_reprint_replaces_only_the_hero_publication(tmp_path: Path) -> None:
    billing = Billing()
    runtime = ApiRuntime([_grid(0), _grid(10)])
    seed = _service(tmp_path, runtime, billing)
    batch = _seed_batch(seed, runtime, count=2, cells=[["尺寸", "30cm"]])
    seed.close()
    client, service = _client(tmp_path, runtime, billing)
    before_urls = _urls_by_style_role(batch)
    before_assets = _asset_ids_by_style_role(service, batch["id"])
    before_counts = _table_counts(service)
    freezes_before = len(billing.freezes)
    cards_before = len(_assets_of_kind(service, CARD_KIND))

    response = client.post(
        f"{API}/batches/{batch['id']}/spec-card/reprint",
        headers=HEADERS,
        json={"cells": [["尺寸", "30 × 20 × 10 cm"], ["材质", "12oz 帆布"]], "style": "dark", "corner": "top-left"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "saved": True,
        "reprinted": 2,
        "failed": 0,
        "errors": [],
        "needs_re_export": True,
    }
    assert len(billing.freezes) == freezes_before
    after = client.get(f"{API}/batches/{batch['id']}", headers=HEADERS).json()
    after_urls = _urls_by_style_role(after)
    assert _asset_ids_by_style_role(service, batch["id"]) == before_assets
    for style_index in (1, 2):
        hero_key = (style_index, "hero")
        assert after_urls[hero_key] != before_urls[hero_key]
        for role in ("detail_a", "detail_b", "lifestyle"):
            assert after_urls[(style_index, role)] == before_urls[(style_index, role)]
    # 新卡片图入库（每款一张），并只替换 publications 里的 hero 指针。
    cards = _assets_of_kind(service, CARD_KIND)
    assert len(cards) == cards_before + 2
    assert {_published_digest(after_urls[(index, "hero")]) for index in (1, 2)} == {
        card["sha256"][:12] for card in cards[-2:]
    }
    counts_after = _table_counts(service)
    assert counts_after["pod_customization_generation_calls"] == before_counts["pod_customization_generation_calls"]
    assert counts_after["pod_customization_assets"] == before_counts["pod_customization_assets"] + 2
    assert after["listing_fields"]["spec_card"] == {
        "enabled": True,
        "style": "dark",
        "corner": "top-left",
        "cells": [["尺寸", "30 × 20 × 10 cm"], ["材质", "12oz 帆布"]],
    }
    service.close()
    runtime.close()


def test_spec_card_reprint_keeps_a_failed_style_and_finishes_the_rest(tmp_path: Path, monkeypatch) -> None:
    billing = Billing()
    runtime = ApiRuntime([_grid(0), _grid(10)])
    seed = _service(tmp_path, runtime, billing)
    batch = _seed_batch(seed, runtime, count=2, cells=[["尺寸", "30cm"]])
    seed.close()
    client, service = _client(tmp_path, runtime, billing)
    before_urls = _urls_by_style_role(batch)
    original = spec_card.render_spec_card
    calls = {"count": 0}

    def sometimes_boom(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise spec_card.SpecCardRenderError("no usable font for the spec card renderer")
        return original(*args, **kwargs)

    monkeypatch.setattr(spec_card, "render_spec_card", sometimes_boom)
    response = client.post(
        f"{API}/batches/{batch['id']}/spec-card/reprint",
        headers=HEADERS,
        json={"cells": [["尺寸", "40cm"]]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["reprinted"] == 1
    assert payload["failed"] == 1
    assert payload["saved"] is True and payload["needs_re_export"] is True
    assert [error["style_index"] for error in payload["errors"]] == [2]
    assert "no usable font" in payload["errors"][0]["message"]
    after_urls = _urls_by_style_role(client.get(f"{API}/batches/{batch['id']}", headers=HEADERS).json())
    assert after_urls[(2, "hero")] == before_urls[(2, "hero")]
    assert after_urls[(1, "hero")] != before_urls[(1, "hero")]
    assert len(_assets_of_kind(service, CARD_KIND)) == 3
    service.close()
    runtime.close()


def test_spec_card_reprint_style_index_filter_only_reprints_that_style(tmp_path: Path) -> None:
    billing = Billing()
    runtime = ApiRuntime([_grid(0), _grid(10)])
    seed = _service(tmp_path, runtime, billing)
    batch = _seed_batch(seed, runtime, count=2, cells=[["尺寸", "30cm"]])
    seed.close()
    client, service = _client(tmp_path, runtime, billing)
    before_urls = _urls_by_style_role(batch)

    response = client.post(
        f"{API}/batches/{batch['id']}/spec-card/reprint",
        headers=HEADERS,
        json={"cells": [["尺寸", "45cm"]], "style_index": 2},
    )

    assert response.status_code == 200
    assert response.json() == {
        "saved": True,
        "reprinted": 1,
        "failed": 0,
        "errors": [],
        "needs_re_export": True,
    }
    after_urls = _urls_by_style_role(client.get(f"{API}/batches/{batch['id']}", headers=HEADERS).json())
    assert after_urls[(1, "hero")] == before_urls[(1, "hero")]
    assert after_urls[(2, "hero")] != before_urls[(2, "hero")]
    assert len(_assets_of_kind(service, CARD_KIND)) == 3
    service.close()
    runtime.close()


def test_spec_card_reprint_writes_an_audit_line_per_style(tmp_path: Path, caplog) -> None:
    billing = Billing()
    runtime = ApiRuntime([_grid(0), _grid(10)])
    seed = _service(tmp_path, runtime, billing)
    batch = _seed_batch(seed, runtime, count=2, cells=[["尺寸", "30cm"]])
    seed.close()
    client, service = _client(tmp_path, runtime, billing)

    with caplog.at_level(logging.INFO, logger="business.pod_processing"):
        response = client.post(
            f"{API}/batches/{batch['id']}/spec-card/reprint",
            headers=HEADERS,
            json={"cells": [["尺寸", "50cm"]]},
        )

    assert response.status_code == 200
    lines = [
        record.getMessage()
        for record in caplog.records
        if record.name == "business.pod_processing" and "POD 规格卡重印" in record.getMessage()
    ]
    assert sum("重印开始" in line for line in lines) == 2
    assert sum("重印完成" in line for line in lines) == 2
    assert all(f"batch_id={batch['id']}" in line for line in lines)
    assert all("操作人=local-demo" in line for line in lines)
    assert {line.split("| style=")[1].split(" ")[0] for line in lines if "| style=" in line} == {"1", "2"}
    service.close()
    runtime.close()
