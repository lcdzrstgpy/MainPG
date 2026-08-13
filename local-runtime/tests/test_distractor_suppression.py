from __future__ import annotations

from io import BytesIO

from PIL import Image

from wh_local.price_verification.sourcing import distractor_suppression as module


def _image_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (100, 100), "#f4d35e").save(output, format="PNG")
    return output.getvalue()


def test_missing_optional_model_falls_back_without_breaking_search(monkeypatch) -> None:
    monkeypatch.setattr(module, "_model_path", lambda: None)

    content = _image_bytes()
    output, audit = module.suppress_distractors(content)

    assert output == content
    assert audit["available"] is False
    assert audit["applied"] is False
    assert audit["reason"] == "model_missing"


def test_detected_pet_box_is_suppressed_with_low_resource_session(monkeypatch, tmp_path) -> None:
    model = tmp_path / module.MODEL_FILENAME
    model.write_bytes(b"test")
    monkeypatch.setattr(module, "_model_path", lambda: model)
    monkeypatch.setattr(module, "_session", lambda _path: object())
    monkeypatch.setattr(module, "_detect_boxes", lambda _session, _image: [(0.1, 0.1, 0.55, 0.7)])

    content = _image_bytes()
    output, audit = module.suppress_distractors(content)

    assert output != content
    assert audit["available"] is True
    assert audit["applied"] is True
    assert audit["distractor_count"] == 1
