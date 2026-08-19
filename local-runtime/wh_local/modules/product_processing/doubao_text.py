"""Strict internal Doubao listing-text client."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .doubao_ark import MODEL_ID, DoubaoArkClient, DoubaoArkError


PROMPT_VERSION = "doubao-text-v1"
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 0.5
_RESULT_KEYS = {
    "optimized_title",
    "description",
    "variant_translations",
    "product_dimensions",
}
_DIMENSION_KEYS = {"length_cm", "width_cm", "height_cm", "weight_g"}

DoubaoTextError = DoubaoArkError


@dataclass(frozen=True)
class DoubaoTextResult:
    optimized_title: str
    description: str
    variant_translations: tuple[tuple[str, str], ...]
    product_dimensions: Mapping[str, float | int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "optimized_title": self.optimized_title,
            "description": self.description,
            "variant_translations": [
                {"raw_value": raw_value, "export_value": export_value}
                for raw_value, export_value in self.variant_translations
            ],
            "product_dimensions": dict(self.product_dimensions),
        }


class DoubaoTextClient:
    def __init__(self) -> None:
        self._ark = DoubaoArkClient()
        self.api_key = self._ark.api_key
        self.last_attempt_count = 0

    def generate_listing_text(
        self,
        prompt: str,
        *,
        validator: Callable[[DoubaoTextResult], None] | None = None,
    ) -> DoubaoTextResult:
        if not str(prompt or "").strip():
            raise DoubaoTextError(
                "Doubao text prompt is empty",
                error_kind="invalid_input",
                retryable=False,
            )
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self.last_attempt_count = attempt
            try:
                content = self._ark.complete(
                    [{"role": "user", "content": str(prompt)}]
                )
                result = _parse_text_result(content)
                if validator is not None:
                    validator(result)
                return result
            except DoubaoTextError as exc:
                exc.attempt_count = attempt
                # Text contract treats malformed Ark envelopes/content as an
                # invalid JSON attempt within the same three-call budget.
                if exc.error_kind == "invalid_response":
                    exc.retryable = True
                if not exc.retryable or attempt >= MAX_ATTEMPTS:
                    raise
            except ValueError as exc:
                error = DoubaoTextError(
                    "Doubao text output failed the listing contract",
                    error_kind="invalid_response",
                    retryable=True,
                    attempt_count=attempt,
                )
                if attempt >= MAX_ATTEMPTS:
                    raise error from exc
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SECONDS)
        raise AssertionError("unreachable")


def text_result_from_dict(payload: Mapping[str, Any]) -> DoubaoTextResult:
    return _result_from_payload(dict(payload))


def _parse_text_result(content: str) -> DoubaoTextResult:
    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise DoubaoTextError(
            "Doubao text response did not contain strict JSON",
            error_kind="invalid_response",
            retryable=True,
        ) from exc
    return _result_from_payload(payload)


def _result_from_payload(payload: Any) -> DoubaoTextResult:
    if not isinstance(payload, dict) or set(payload) != _RESULT_KEYS:
        raise DoubaoTextError(
            "Doubao text output fields failed validation",
            error_kind="invalid_response",
            retryable=True,
        )
    title = payload.get("optimized_title")
    description = payload.get("description")
    if not isinstance(title, str) or not isinstance(description, str):
        raise DoubaoTextError(
            "Doubao text title or description failed validation",
            error_kind="invalid_response",
            retryable=True,
        )

    raw_translations = payload.get("variant_translations")
    if not isinstance(raw_translations, list):
        raise DoubaoTextError(
            "Doubao text variant translations failed validation",
            error_kind="invalid_response",
            retryable=True,
        )
    translations: list[tuple[str, str]] = []
    seen_values: set[str] = set()
    for item in raw_translations:
        if not isinstance(item, dict) or set(item) != {"raw_value", "export_value"}:
            raise DoubaoTextError(
                "Doubao text variant mapping failed validation",
                error_kind="invalid_response",
                retryable=True,
            )
        raw_value = item.get("raw_value")
        export_value = item.get("export_value")
        if (
            not isinstance(raw_value, str)
            or not raw_value.strip()
            or not isinstance(export_value, str)
            or not export_value.strip()
            or raw_value.strip() in seen_values
        ):
            raise DoubaoTextError(
                "Doubao text variant mapping value failed validation",
                error_kind="invalid_response",
                retryable=True,
            )
        seen_values.add(raw_value.strip())
        translations.append((raw_value.strip()[:200], export_value.strip()[:200]))

    raw_dimensions = payload.get("product_dimensions")
    if not isinstance(raw_dimensions, dict) or not set(raw_dimensions).issubset(
        _DIMENSION_KEYS
    ):
        raise DoubaoTextError(
            "Doubao text product dimensions failed validation",
            error_kind="invalid_response",
            retryable=True,
        )
    dimensions: dict[str, float | int] = {}
    for key, value in raw_dimensions.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise DoubaoTextError(
                "Doubao text product dimension value failed validation",
                error_kind="invalid_response",
                retryable=True,
            )
        dimensions[key] = value

    return DoubaoTextResult(
        optimized_title=" ".join(title.split())[:500],
        description=description.strip()[:3000],
        variant_translations=tuple(translations),
        product_dimensions=dimensions,
    )
