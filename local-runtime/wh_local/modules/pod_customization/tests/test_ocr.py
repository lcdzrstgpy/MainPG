from __future__ import annotations

import io

from PIL import Image

from wh_local.modules.pod_customization.ocr import PodTextInspector


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(output, "PNG")
    return output.getvalue()


def test_pod_text_inspector_uses_its_own_lazy_engine_and_returns_confident_text() -> None:
    engines = []

    class FakeEngine:
        def __call__(self, image):
            assert max(image.shape[:2]) <= 384
            return [
                [[[1, 1], [180, 1], [180, 60], [1, 60]], "SALE", 0.98],
                [[[1, 80], [20, 80], [20, 90], [1, 90]], "noise", 0.2],
            ], 0.01

    def factory():
        engine = FakeEngine()
        engines.append(engine)
        return engine

    first = PodTextInspector(engine_factory=factory)
    second = PodTextInspector(engine_factory=factory)

    assert first(_png()) == ["SALE"]
    assert first(_png()) == ["SALE"]
    assert second(_png()) == ["SALE"]
    assert len(engines) == 2
