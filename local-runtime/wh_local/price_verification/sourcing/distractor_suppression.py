"""Optional, low-resource suppression of people and pets in product photos.

The detector is deliberately optional.  When its small INT8 ONNX model or
runtime dependencies are unavailable, callers receive the original image and a
safe audit instead of a failed sourcing run.
"""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from pathlib import Path
import sys
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError


MODEL_FILENAME = "ssd_mobilenet_v1_12-int8.onnx"
DETECTOR_METHOD = "ssd-mobilenetv1-int8-coco"
_DISTRACTOR_CLASS_IDS = frozenset({1, 17, 18})  # person, cat, dog
_DETECTION_THRESHOLD = 0.45
_MAX_DETECTIONS = 5
_MAX_INPUT_EDGE = 320


def suppress_distractors(content: bytes) -> tuple[bytes, dict[str, Any]]:
    """Replace detected person/cat/dog boxes with a local border colour.

    The ONNX detector runs once for the Temu reference image, uses one CPU
    thread, and never runs for the sixty OneBound candidates.  This keeps the
    feature usable on machines without a GPU or with limited memory.
    """
    audit: dict[str, Any] = {
        "method": DETECTOR_METHOD,
        "available": False,
        "applied": False,
        "distractor_count": 0,
        "fallback": "original_image",
    }
    model_path = _model_path()
    if model_path is None:
        audit["reason"] = "model_missing"
        return content, audit
    try:
        session = _session(str(model_path))
        if session is None:
            audit["reason"] = "runtime_unavailable"
            return content, audit
        audit["available"] = True
        with Image.open(BytesIO(content)) as opened:
            original = ImageOps.exif_transpose(opened).convert("RGB")
        inference_image = _bounded_image(original)
        boxes = _detect_boxes(session, inference_image)
        if not boxes:
            audit["fallback"] = "none_needed"
            return content, audit
        output = original.copy()
        applied = 0
        for box in boxes[:_MAX_DETECTIONS]:
            pixel_box = _pixel_box(box, output.size)
            if pixel_box is None:
                continue
            # Never erase nearly the whole reference if the detector is wrong.
            left, top, right, bottom = pixel_box
            if (right - left) * (bottom - top) > output.width * output.height * 0.78:
                continue
            fill = _border_colour(output, pixel_box)
            output.paste(fill, pixel_box)
            applied += 1
        if not applied:
            audit["fallback"] = "unsafe_boxes_skipped"
            return content, audit
        buffer = BytesIO()
        output.save(buffer, format="PNG", optimize=True)
        audit.update(applied=True, distractor_count=applied, fallback="suppressed_reference")
        return buffer.getvalue(), audit
    except (OSError, ValueError, TypeError, UnidentifiedImageError):
        audit["reason"] = "inference_failed"
        return content, audit


def _model_path() -> Path | None:
    roots = [Path(__file__).resolve().parent / "models"]
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        roots.insert(0, Path(bundle_root) / "wh_local" / "price_verification" / "sourcing" / "models")
    for root in roots:
        candidate = root / MODEL_FILENAME
        if candidate.is_file():
            return candidate
    return None


@lru_cache(maxsize=1)
def _session(model_path: str) -> Any | None:
    try:
        import onnxruntime as ort
    except ImportError:
        return None
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(model_path, sess_options=options, providers=["CPUExecutionProvider"])


def _bounded_image(image: Image.Image) -> Image.Image:
    output = image.copy()
    output.thumbnail((_MAX_INPUT_EDGE, _MAX_INPUT_EDGE), Image.Resampling.BILINEAR)
    return output


def _detect_boxes(session: Any, image: Image.Image) -> list[tuple[float, float, float, float]]:
    try:
        import numpy as np
    except ImportError:
        return []
    tensor = np.asarray(image, dtype=np.uint8)[None, ...]
    input_name = session.get_inputs()[0].name
    values = session.run(None, {input_name: tensor})
    if len(values) < 4:
        return []
    # Model Zoo order: num_detections, boxes, scores, classes.  Resolve by
    # output names when present so compatible exports remain usable.
    named = {output.name.casefold(): value for output, value in zip(session.get_outputs(), values)}
    boxes = _named_value(named, "detection_boxes", values[1])
    scores = _named_value(named, "detection_scores", values[2])
    classes = _named_value(named, "detection_classes", values[3])
    count_value = _named_value(named, "num_detections", values[0])
    count = min(int(np.asarray(count_value).reshape(-1)[0]), len(np.asarray(boxes).reshape(-1, 4)))
    flat_boxes = np.asarray(boxes).reshape(-1, 4)
    flat_scores = np.asarray(scores).reshape(-1)
    flat_classes = np.asarray(classes).reshape(-1)
    output: list[tuple[float, float, float, float]] = []
    for index in range(count):
        if float(flat_scores[index]) < _DETECTION_THRESHOLD or int(flat_classes[index]) not in _DISTRACTOR_CLASS_IDS:
            continue
        top, left, bottom, right = (float(value) for value in flat_boxes[index])
        output.append((top, left, bottom, right))
    return output


def _named_value(values: dict[str, Any], fragment: str, default: Any) -> Any:
    return next((value for name, value in values.items() if fragment in name), default)


def _pixel_box(box: tuple[float, float, float, float], size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    width, height = size
    top, left, bottom, right = box
    pad_x = width * 0.015
    pad_y = height * 0.015
    x1 = max(0, round(left * width - pad_x))
    y1 = max(0, round(top * height - pad_y))
    x2 = min(width, round(right * width + pad_x))
    y2 = min(height, round(bottom * height + pad_y))
    return (x1, y1, x2, y2) if x2 > x1 and y2 > y1 else None


def _border_colour(image: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, int]:
    left, top, right, bottom = box
    margin = max(2, round(min(image.size) * 0.015))
    samples: list[tuple[int, int, int]] = []
    regions = (
        (max(0, left - margin), max(0, top - margin), min(image.width, right + margin), top),
        (max(0, left - margin), bottom, min(image.width, right + margin), min(image.height, bottom + margin)),
        (max(0, left - margin), top, left, bottom),
        (right, top, min(image.width, right + margin), bottom),
    )
    for region in regions:
        if region[2] <= region[0] or region[3] <= region[1]:
            continue
        sample = image.crop(region).resize((8, 8), Image.Resampling.BILINEAR)
        flattened = getattr(sample, "get_flattened_data", None)
        samples.extend(flattened() if callable(flattened) else sample.getdata())
    if not samples:
        return (238, 238, 238)
    samples.sort(key=lambda rgb: sum(rgb))
    return samples[len(samples) // 2]
