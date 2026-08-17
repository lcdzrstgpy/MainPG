"""Fail-closed local visual verification for OneBound image-search hits.

OneBound returns a relevance order, but not a documented similarity score.  This
module downloads the reference and candidate thumbnails through the existing
SSRF-safe public-image boundary and computes deterministic perceptual features
locally.  Raw image bytes are never persisted or returned to the caller.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
import os
import threading
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from ...config import default_config
from ...data_collection.public_image_fetch import FetchedPublicImage, fetch_public_image
from .distractor_suppression import suppress_distractors
from .image_feature_cache import ImageFeatureCache


IMAGE_SEARCH_RECALL_LIMIT = 60
IMAGE_SIMILARITY_THRESHOLD = 0.50
IMAGE_DISPLAY_LIMIT = 5
IMAGE_SIMILARITY_METHOD = "local-phash-dhash-color-v1"
IMAGE_FEATURE_CACHE_TTL_DAYS = 3
_FETCH_TIMEOUT_SECONDS = 8.0
_MAX_IMAGE_BYTES = 5 * 1024 * 1024
# Two SKCs may now verify candidates concurrently. Keep each SKC at six image
# workers or fewer so a packaged low-memory client is not flooded with threads.
_MAX_WORKERS = min(6, max(2, os.cpu_count() or 2))
_CANDIDATE_IMAGE_DOWNLOAD_CAPACITY = threading.BoundedSemaphore(12)


ImageFetcher = Callable[[str], FetchedPublicImage]


@dataclass(frozen=True)
class _VisualFeatures:
    average_hash: int
    average_hash_bits: int
    difference_hash: int
    difference_hash_bits: int
    perceptual_hash: int
    perceptual_hash_bits: int
    colour_histogram: tuple[float, ...]


def verify_visual_candidates(
    reference_image_url: str,
    candidates: Sequence[Mapping[str, Any]],
    *,
    reference_content: bytes | None = None,
    fetcher: ImageFetcher | None = None,
    threshold: float = IMAGE_SIMILARITY_THRESHOLD,
    minimum_results: int = IMAGE_DISPLAY_LIMIT,
    feature_cache: ImageFeatureCache | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return scored candidates, filling to the required display count when possible.

    Threshold-passing matches always rank first.  If there are fewer than the
    required display count, the strongest same-category, successfully decoded
    candidates below threshold fill the remaining slots and are explicitly
    marked as fallbacks. Download/decode failures remain excluded.
    """
    started_at = time.monotonic()
    active_fetcher = fetcher or _safe_fetch
    reference_reused = reference_content is not None
    input_candidates = [dict(candidate) for candidate in candidates]
    audit: dict[str, Any] = {
        "method": IMAGE_SIMILARITY_METHOD,
        "threshold": round(float(threshold), 4),
        "input_count": len(input_candidates),
        "verified_count": 0,
        "fallback_count": 0,
        "rejected_count": 0,
        "unavailable_count": 0,
        "reference_available": False,
        "reference_reused": reference_reused,
        "feature_cache_hit_count": 0,
        "feature_cache_miss_count": 0,
    }
    if not reference_image_url or not input_candidates:
        audit["unavailable_count"] = len(input_candidates)
        return [], _with_elapsed_ms(audit, started_at)

    try:
        if reference_content is None:
            reference_content = active_fetcher(reference_image_url).content
        suppressed_content, distractor_audit = suppress_distractors(reference_content)
        # When suppression succeeds, do not keep the original dog/person-heavy
        # representation in the max-score pool; otherwise the distractor could
        # still dominate and undo the whole purpose of the detector.
        reference = list(_feature_variants(suppressed_content))
        audit["distractor_suppression"] = distractor_audit
    except (OSError, ValueError, UnidentifiedImageError):
        audit["unavailable_count"] = len(input_candidates)
        return [], _with_elapsed_ms(audit, started_at)
    audit["reference_available"] = True

    passing: list[dict[str, Any]] = []
    fallback: list[dict[str, Any]] = []
    results: dict[int, tuple[float, str, bool] | None] = {}
    active_cache = feature_cache if feature_cache is not None else _default_feature_cache()
    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(input_candidates))) as executor:
        futures = {
            executor.submit(
                _score_candidate,
                reference_image_url,
                tuple(reference),
                _candidate_image_url(candidate),
                active_fetcher,
                active_cache,
            ): index
            for index, candidate in enumerate(input_candidates)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except (OSError, ValueError, UnidentifiedImageError):
                results[index] = None

    for index, candidate in enumerate(input_candidates):
        result = results.get(index)
        if result is None:
            audit["unavailable_count"] += 1
            continue
        score, method, cache_hit = result
        audit["feature_cache_hit_count" if cache_hit else "feature_cache_miss_count"] += 1
        candidate["image_similarity_score"] = round(score, 4)
        candidate["image_similarity_method"] = method
        candidate["image_similarity_verified"] = score >= threshold
        candidate["image_similarity_fallback"] = score < threshold
        candidate["image_similarity_selected"] = True
        if score >= threshold:
            passing.append(candidate)
        else:
            audit["rejected_count"] += 1
            fallback.append(candidate)
    passing.sort(key=lambda item: -float(item.get("image_similarity_score") or 0))
    fallback.sort(key=lambda item: -float(item.get("image_similarity_score") or 0))
    needed = max(0, int(minimum_results) - len(passing))
    selected_fallback = fallback[:needed]
    retained = [*passing, *selected_fallback]
    audit["verified_count"] = len(passing)
    audit["fallback_count"] = len(selected_fallback)
    return retained, _with_elapsed_ms(audit, started_at)


def similarity_score(reference_content: bytes, candidate_content: bytes) -> float:
    """Calculate a deterministic score for two raster images (testable pure API)."""
    reference = _feature_variants(reference_content)
    candidate = _feature_variants(candidate_content)
    return max(_feature_similarity(left, right) for left in reference for right in candidate)


def _score_candidate(
    reference_url: str,
    reference: tuple[_VisualFeatures, ...],
    candidate_url: str,
    fetcher: ImageFetcher,
    feature_cache: ImageFeatureCache | None,
) -> tuple[float, str, bool] | None:
    if not candidate_url:
        return None
    if _canonical_image_url(reference_url) == _canonical_image_url(candidate_url):
        return 1.0, "exact-image-url", False
    canonical_url = _canonical_image_url(candidate_url)
    cached = feature_cache.load(canonical_url) if feature_cache is not None else None
    cache_hit = cached is not None
    if cached is not None:
        try:
            candidate = _deserialize_feature_variants(cached)
        except (KeyError, TypeError, ValueError, OverflowError):
            cached = None
            cache_hit = False
    if cached is None:
        with _CANDIDATE_IMAGE_DOWNLOAD_CAPACITY:
            content = fetcher(candidate_url).content
        candidate = _feature_variants(content)
        if feature_cache is not None:
            feature_cache.store(canonical_url, _serialize_feature_variants(candidate))
    score = max(_feature_similarity(left, right) for left in reference for right in candidate)
    return score, IMAGE_SIMILARITY_METHOD, cache_hit


def _feature_variants(content: bytes) -> tuple[_VisualFeatures, ...]:
    if not content:
        raise ValueError("image content is empty")
    with Image.open(BytesIO(content)) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        width, height = image.size
        if width < 8 or height < 8:
            raise ValueError("image is too small")
        variants = [image.copy()]
        # Marketplace images often add a narrow text/logo frame.  A centred
        # crop keeps the product body comparable without accepting arbitrary
        # crops that happen to share one small object.
        for ratio in (0.90, 0.80):
            dx = round(width * (1.0 - ratio) / 2)
            dy = round(height * (1.0 - ratio) / 2)
            variants.append(image.crop((dx, dy, width - dx, height - dy)))
    return tuple(_features(image) for image in variants)


def _features(image: Image.Image) -> _VisualFeatures:
    grayscale = ImageOps.grayscale(image)
    average_pixels = _pixel_values(grayscale.resize((16, 16), Image.Resampling.LANCZOS))
    average_mean = sum(average_pixels) / len(average_pixels)
    average_hash = _bits_to_int(pixel >= average_mean for pixel in average_pixels)

    difference_pixels = _pixel_values(grayscale.resize((17, 16), Image.Resampling.LANCZOS))
    difference_bits = []
    for row in range(16):
        offset = row * 17
        difference_bits.extend(
            difference_pixels[offset + column] > difference_pixels[offset + column + 1]
            for column in range(16)
        )
    difference_hash = _bits_to_int(difference_bits)

    dct_pixels = _pixel_values(grayscale.resize((32, 32), Image.Resampling.LANCZOS))
    low_frequency = _low_frequency_dct(dct_pixels, size=32, output_size=8)
    median = sorted(low_frequency[1:])[len(low_frequency[1:]) // 2]
    perceptual_hash = _bits_to_int(value >= median for value in low_frequency)

    histogram = [0] * 64
    for red, green, blue in _pixel_values(image.resize((64, 64), Image.Resampling.BILINEAR)):
        histogram[(red // 64) * 16 + (green // 64) * 4 + (blue // 64)] += 1
    total = float(sum(histogram)) or 1.0
    colour_histogram = tuple(value / total for value in histogram)
    return _VisualFeatures(
        average_hash=average_hash,
        average_hash_bits=256,
        difference_hash=difference_hash,
        difference_hash_bits=256,
        perceptual_hash=perceptual_hash,
        perceptual_hash_bits=64,
        colour_histogram=colour_histogram,
    )


def _serialize_feature_variants(values: Sequence[_VisualFeatures]) -> list[dict[str, Any]]:
    return [
        {
            "average_hash": str(value.average_hash),
            "average_hash_bits": value.average_hash_bits,
            "difference_hash": str(value.difference_hash),
            "difference_hash_bits": value.difference_hash_bits,
            "perceptual_hash": str(value.perceptual_hash),
            "perceptual_hash_bits": value.perceptual_hash_bits,
            "colour_histogram": list(value.colour_histogram),
        }
        for value in values
    ]


def _deserialize_feature_variants(values: Sequence[Mapping[str, Any]]) -> tuple[_VisualFeatures, ...]:
    output: list[_VisualFeatures] = []
    for value in values:
        histogram = value.get("colour_histogram")
        if not isinstance(histogram, list) or len(histogram) != 64:
            raise ValueError("invalid cached colour histogram")
        output.append(
            _VisualFeatures(
                average_hash=int(value["average_hash"]),
                average_hash_bits=int(value["average_hash_bits"]),
                difference_hash=int(value["difference_hash"]),
                difference_hash_bits=int(value["difference_hash_bits"]),
                perceptual_hash=int(value["perceptual_hash"]),
                perceptual_hash_bits=int(value["perceptual_hash_bits"]),
                colour_histogram=tuple(float(item) for item in histogram),
            )
        )
    if not output:
        raise ValueError("cached feature variants are empty")
    return tuple(output)


def _low_frequency_dct(pixels: list[int], *, size: int, output_size: int) -> list[float]:
    image = np.asarray(pixels, dtype=np.float64).reshape(size, size)
    cosines = _dct_cosines(size, output_size)
    # Equivalent to the former nested-loop formula C @ image @ C.T, but NumPy
    # executes it in optimized native code instead of Python for every pixel.
    return (cosines @ image @ cosines.T).reshape(-1).tolist()


@lru_cache(maxsize=4)
def _dct_cosines(size: int, output_size: int) -> np.ndarray:
    positions = np.arange(size, dtype=np.float64)
    frequencies = np.arange(output_size, dtype=np.float64)[:, None]
    return np.cos(np.pi * (2.0 * positions + 1.0) * frequencies / (2.0 * size))


def _feature_similarity(left: _VisualFeatures, right: _VisualFeatures) -> float:
    average = _hash_similarity(left.average_hash, right.average_hash, left.average_hash_bits)
    difference = _hash_similarity(left.difference_hash, right.difference_hash, left.difference_hash_bits)
    perceptual = _hash_similarity(left.perceptual_hash, right.perceptual_hash, left.perceptual_hash_bits)
    colour = sum(min(a, b) for a, b in zip(left.colour_histogram, right.colour_histogram))
    raw_score = 0.20 * average + 0.25 * difference + 0.45 * perceptual + 0.10 * colour
    # Unrelated perceptual hashes naturally agree on roughly half their bits.
    # Normalize that random baseline to zero so a user-facing 40% threshold is
    # meaningful instead of admitting arbitrary same-category images.
    return max(0.0, min(1.0, (raw_score - 0.5) / 0.5))


def _hash_similarity(left: int, right: int, bit_count: int) -> float:
    return 1.0 - ((left ^ right).bit_count() / bit_count)


def _pixel_values(image: Image.Image) -> list[Any]:
    flattened = getattr(image, "get_flattened_data", None)
    return list(flattened() if callable(flattened) else image.getdata())


def _bits_to_int(values: Any) -> int:
    output = 0
    for value in values:
        output = (output << 1) | int(bool(value))
    return output


def _candidate_image_url(candidate: Mapping[str, Any]) -> str:
    for name in ("main_image_url", "image", "image_url", "pic_url", "pic", "picUrl"):
        value = candidate.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _canonical_image_url(value: str) -> str:
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return value.strip()
    return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), parts.path, "", ""))


def _safe_fetch(url: str) -> FetchedPublicImage:
    return fetch_public_image(
        url,
        max_bytes=_MAX_IMAGE_BYTES,
        timeout_seconds=_FETCH_TIMEOUT_SECONDS,
    )


def _with_elapsed_ms(audit: dict[str, Any], started_at: float) -> dict[str, Any]:
    audit["elapsed_ms"] = max(0, round((time.monotonic() - started_at) * 1000))
    return audit


@lru_cache(maxsize=1)
def _default_feature_cache() -> ImageFeatureCache | None:
    try:
        root = default_config().data_dir / "price-verification" / "image-feature-cache"
        return ImageFeatureCache(
            root,
            feature_method=IMAGE_SIMILARITY_METHOD,
            ttl_seconds=IMAGE_FEATURE_CACHE_TTL_DAYS * 24 * 60 * 60,
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
