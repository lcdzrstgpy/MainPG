from __future__ import annotations

import io

import pytest
from PIL import Image

from wh_local.modules.product_processing.domain.mahjong_faces import (
    CANVAS,
    DEFAULT_HAND,
    STANDARD_FACES,
    render_face,
    standard_hand,
    write_standard_faces,
)


def test_standard_set_covers_all_tiles():
    ids = [spec.face_id for spec in STANDARD_FACES]
    assert len(ids) == 34
    for number in range(1, 10):
        assert f"{number}筒" in ids
        assert f"{number}条" in ids
        assert f"{number}万" in ids
    for honour in ("東", "南", "西", "北", "中", "發", "白"):
        assert honour in ids


def test_render_face_keeps_face_aspect_and_ink():
    content = render_face("中")
    image = Image.open(io.BytesIO(content))
    assert image.size == CANVAS
    assert image.mode == "RGBA"
    alpha = image.getchannel("A")
    # 牌面画在透明画布上：有墨迹，但四周留白。
    assert alpha.getextrema()[1] > 200
    assert alpha.getpixel((2, 2)) == 0
    # 内容不能铺满整张画布，否则贴到牌上会顶边。
    bbox = alpha.getbbox()
    assert bbox is not None
    assert bbox[0] > CANVAS[0] * 0.05
    assert bbox[3] < CANVAS[1] * 0.98


def test_standard_hand_matches_default_and_is_stable():
    hand = standard_hand()
    assert [face_id for face_id, _ in hand] == list(DEFAULT_HAND)
    assert render_face("發") == render_face("發")


def test_write_standard_faces_returns_absolute_paths(tmp_path):
    paths = write_standard_faces(tmp_path / "faces")
    assert len(paths) == len(DEFAULT_HAND)
    assert all(path.is_absolute() and path.is_file() for path in paths)
    # 二次调用不重写，直接复用已有文件。
    assert write_standard_faces(tmp_path / "faces") == paths


def test_unknown_face_raises():
    with pytest.raises(KeyError):
        render_face("十筒")
