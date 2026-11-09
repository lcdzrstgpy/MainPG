from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from wh_local.modules.product_processing.domain.background_composite import (
    compose_on_background,
)
from wh_local.modules.product_processing.domain.custom_backgrounds import (
    MAIN_SIDE,
    THUMB_SIDE,
    CustomBackgroundStore,
    user_background_id,
)


def _encode(image: Image.Image, fmt: str = "JPEG") -> str:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return base64.b64encode(buffer.getvalue()).decode()


def _solid_insert(size: int = 1000) -> Image.Image:
    image = Image.new("RGB", (size, size), (236, 232, 226))
    draw = ImageDraw.Draw(image)
    for y in range(size):
        shade = 210 - int(y * 50 / size)
        draw.line([(0, y), (size, y)], fill=(shade, shade - 4, shade - 10))
    return image


@pytest.fixture()
def store(tmp_path: Path) -> CustomBackgroundStore:
    return CustomBackgroundStore(tmp_path)


def test_save_crops_to_square_and_builds_thumbnail(store: CustomBackgroundStore) -> None:
    record = store.save(_encode(Image.new("RGB", (1600, 900), (200, 190, 180))), name="木桌")
    assert record["id"].startswith("ub_")
    assert record["template_id"] == f"user:{record['id']}"
    assert record["name"] == "木桌"
    assert Image.open(store.file_path(record["id"])).size == (MAIN_SIDE, MAIN_SIDE)
    assert Image.open(store.file_path(record["id"], thumb=True)).size == (THUMB_SIDE, THUMB_SIDE)


def test_save_rejects_small_and_non_image_payloads(store: CustomBackgroundStore) -> None:
    with pytest.raises(ValueError):
        store.save(_encode(Image.new("RGB", (799, 1200))), workspace_id="local")
    with pytest.raises(ValueError):
        store.save(base64.b64encode(b"not an image").decode(), workspace_id="local")
    with pytest.raises(ValueError):
        store.save("", workspace_id="local")


def test_reupload_same_bytes_reuses_record(store: CustomBackgroundStore) -> None:
    payload = _encode(_solid_insert())
    first = store.save(payload, name="底板 A")
    second = store.save(payload, name="底板 A")
    assert first["id"] == second["id"]
    assert len(store.list()) == 1


def test_workspaces_are_isolated(store: CustomBackgroundStore) -> None:
    record = store.save(_encode(_solid_insert()), workspace_id="shop_a")
    assert store.list(workspace_id="shop_a")
    assert store.list(workspace_id="shop_b") == []
    with pytest.raises(FileNotFoundError):
        store.file_path(record["id"], workspace_id="shop_b")


def test_delete_removes_files_and_meta(store: CustomBackgroundStore) -> None:
    record = store.save(_encode(_solid_insert()))
    store.delete(record["id"])
    assert store.list() == []
    with pytest.raises(FileNotFoundError):
        store.file_path(record["id"])
    with pytest.raises(FileNotFoundError):
        store.delete(record["id"])


def test_user_background_id_parses_prefix() -> None:
    assert user_background_id("user:ub_0123456789ab") == "ub_0123456789ab"
    assert user_background_id("bg_oak_light") == ""
    assert user_background_id("") == ""


def test_compose_returns_square_jpeg_or_none(store: CustomBackgroundStore) -> None:
    record = store.save(_encode(_solid_insert()))
    product = Image.new("RGB", (900, 900), (246, 246, 246))
    draw = ImageDraw.Draw(product)
    draw.rounded_rectangle([250, 180, 650, 720], radius=40, fill=(58, 92, 152))
    buffer = io.BytesIO()
    product.save(buffer, format="JPEG", quality=95)

    out = compose_on_background(buffer.getvalue(), store.file_path(record["id"]))
    if out is None:
        # rembg 未安装/权重缺失时按设计回退，调用方保留 AI 原图。
        pytest.skip("rembg unavailable in this environment")
    composed = Image.open(io.BytesIO(out))
    # 未指定 side 时跟随输入图边长，保证同批轮播图尺寸一致。
    assert composed.size == (900, 900)
    assert composed.format == "JPEG"

    sized = compose_on_background(
        buffer.getvalue(), store.file_path(record["id"]), side=MAIN_SIDE
    )
    assert sized is not None
    assert Image.open(io.BytesIO(sized)).size == (MAIN_SIDE, MAIN_SIDE)


def test_compose_returns_none_for_missing_background(tmp_path: Path) -> None:
    product = Image.new("RGB", (600, 600), (250, 250, 250))
    buffer = io.BytesIO()
    product.save(buffer, format="JPEG")
    assert compose_on_background(buffer.getvalue(), tmp_path / "missing.jpg") is None
    assert compose_on_background(b"", tmp_path / "missing.jpg") is None
