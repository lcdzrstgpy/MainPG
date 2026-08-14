"""Small disk cache for derived candidate-image features only.

The cache deliberately never stores source image bytes or plaintext image URLs.
Each entry is addressed by a SHA-256 digest of the canonical URL and contains
only numeric perceptual hashes, colour histograms, schema metadata and expiry.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
import time
import uuid
from typing import Any


FEATURE_CACHE_TTL_SECONDS = 3 * 24 * 60 * 60
FEATURE_CACHE_SCHEMA_VERSION = 1


class ImageFeatureCache:
    """Fail-open JSON cache for numeric candidate-image features."""

    def __init__(
        self,
        root: Path,
        *,
        feature_method: str,
        ttl_seconds: int = FEATURE_CACHE_TTL_SECONDS,
    ) -> None:
        if not isinstance(root, Path):
            raise TypeError("root must be a Path")
        if not feature_method.strip():
            raise ValueError("feature_method is required")
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be positive")
        self.root = root
        self.feature_method = feature_method.strip()
        self.ttl_seconds = int(ttl_seconds)

    def load(self, cache_key: str, *, now: float | None = None) -> list[dict[str, Any]] | None:
        """Return a valid numeric feature payload, or a cache miss."""
        path = self._path(cache_key)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, Mapping):
            return None
        current = time.time() if now is None else float(now)
        if (
            payload.get("schema_version") != FEATURE_CACHE_SCHEMA_VERSION
            or payload.get("feature_method") != self.feature_method
            or _finite_number(payload.get("expires_at")) <= current
        ):
            return None
        variants = payload.get("variants")
        if not isinstance(variants, list) or not variants:
            return None
        return [dict(value) for value in variants if isinstance(value, Mapping)] or None

    def store(
        self,
        cache_key: str,
        variants: Sequence[Mapping[str, Any]],
        *,
        now: float | None = None,
    ) -> bool:
        """Atomically persist numeric features; failures never block search."""
        if not variants:
            return False
        current = time.time() if now is None else float(now)
        payload = {
            "schema_version": FEATURE_CACHE_SCHEMA_VERSION,
            "feature_method": self.feature_method,
            "created_at": current,
            "expires_at": current + self.ttl_seconds,
            "variants": [dict(value) for value in variants],
        }
        path = self._path(cache_key)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(payload, ensure_ascii=True, separators=(",", ":"), allow_nan=False),
                encoding="utf-8",
            )
            os.replace(temporary, path)
            return True
        except (OSError, TypeError, ValueError):
            return False
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _path(self, cache_key: str) -> Path:
        digest = hashlib.sha256(cache_key.strip().encode("utf-8")).hexdigest()
        return self.root / digest[:2] / f"{digest}.json"


def _finite_number(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return number if number == number and abs(number) != float("inf") else 0.0
