from __future__ import annotations

import io
import threading
from collections.abc import Callable
from typing import Any

from PIL import Image


class PodTextInspector:
    """Instance-scoped RapidOCR gate for generated POD pattern tiles."""

    def __init__(
        self,
        *,
        engine_factory: Callable[[], Any] | None = None,
        max_edge: int = 384,
    ) -> None:
        self._engine_factory = engine_factory or _rapidocr_engine
        self._max_edge = max(128, min(int(max_edge), 768))
        self._engine: Any | None = None
        self._engine_error: Exception | None = None
        self._engine_lock = threading.Lock()
        self._inference_slot = threading.BoundedSemaphore(1)

    def __call__(self, content: bytes) -> list[str]:
        engine = self._get_engine()
        try:
            import numpy as np

            with Image.open(io.BytesIO(content)) as opened:
                image = opened.convert("RGB")
                if max(image.size) > self._max_edge:
                    scale = self._max_edge / max(image.size)
                    image = image.resize(
                        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                        Image.Resampling.LANCZOS,
                    )
                with self._inference_slot:
                    result, _elapsed = engine(np.asarray(image))
        except Exception as exc:
            raise RuntimeError("POD OCR inspection failed") from exc
        visible: list[str] = []
        for line in result or []:
            if not isinstance(line, (list, tuple)) or len(line) < 2:
                continue
            text = str(line[1] or "").strip()
            confidence = float(line[2]) if len(line) > 2 and isinstance(line[2], (float, int)) else 1.0
            if text and confidence >= 0.55:
                visible.append(text)
        return visible

    def _get_engine(self) -> Any:
        if self._engine is None and self._engine_error is None:
            with self._engine_lock:
                if self._engine is None and self._engine_error is None:
                    try:
                        self._engine = self._engine_factory()
                    except Exception as exc:
                        self._engine_error = exc
        if self._engine is None:
            raise RuntimeError("POD OCR engine is unavailable") from self._engine_error
        return self._engine


def _rapidocr_engine() -> Any:
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()
