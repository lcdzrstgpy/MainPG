"""Strict internal Doubao listing-text client."""

from __future__ import annotations

import json
import math
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
        base_prompt = str(prompt)
        retry_feedback = ""
        last_contract_error: DoubaoTextError | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self.last_attempt_count = attempt
            try:
                attempt_prompt = _prompt_for_attempt(
                    base_prompt,
                    attempt=attempt,
                    retry_feedback=retry_feedback,
                )
                content = self._ark.complete(
                    [{"role": "user", "content": attempt_prompt}]
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
                    retry_feedback = _safe_retry_feedback(exc)
                    last_contract_error = exc
                elif (
                    exc.status_code == 409
                    and retry_feedback
                    and last_contract_error is not None
                    and attempt >= MAX_ATTEMPTS
                ):
                    # Rolling deployments may still have the old two-distinct-request
                    # gateway limit.  Preserve the real contract failure instead of
                    # replacing it with a misleading final HTTP 409 diagnostic.
                    last_contract_error.attempt_count = attempt
                    raise last_contract_error from exc
                if not exc.retryable or attempt >= MAX_ATTEMPTS:
                    raise
            except ValueError as exc:
                # 校验失败（如语言契约拒绝中文输出）的真实原因不能丢，
                # 拼进 message 供前端/错误报告直接展示，避免只看到通用文案。
                error = DoubaoTextError(
                    f"Doubao text output failed the listing contract: {exc}",
                    error_kind="invalid_response",
                    retryable=True,
                    attempt_count=attempt,
                )
                if attempt >= MAX_ATTEMPTS:
                    raise error from exc
                retry_feedback = _safe_retry_feedback(exc)
                last_contract_error = error
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SECONDS)
        raise AssertionError("unreachable")


def _safe_retry_feedback(exc: Exception) -> str:
    """Keep retry guidance useful without echoing prompts or provider bodies."""
    message = " ".join(str(exc).split()).strip()
    return message[:240] or "the previous response failed the required output contract"


def _prompt_for_attempt(base_prompt: str, *, attempt: int, retry_feedback: str) -> str:
    """Change invalid-response retries so the managed gateway generates fresh output.

    The managed gateway caches completed responses by usage id and request hash.  Replaying
    the identical prompt therefore replays the same invalid response instead of retrying the
    model.  A compact repair instruction makes each contract retry a distinct request while
    transient transport retries continue to reuse the original request.
    """
    if attempt <= 1 or not retry_feedback:
        return base_prompt
    return (
        f"{base_prompt.rstrip()}\n\n"
        "NON-OVERRIDABLE RETRY CORRECTION:\n"
        f"The previous response was rejected because: {retry_feedback}.\n"
        f"This is repair attempt {attempt}. Generate a fresh response and correct that problem. "
        "Return only the exact JSON object required above; do not mention this retry."
    )


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
        # Variant translations are auxiliary rows inside an otherwise useful
        # title/description/dimension response.  Providers occasionally append
        # a blank row, repeat one mapping, or serialize a numeric option as a
        # JSON number.  Ignore malformed/duplicate extras here; the service
        # later checks completeness against the original product's exact option
        # list and retries only the missing values.
        if not isinstance(item, dict):
            continue
        raw_value = _variant_scalar_text(item.get("raw_value"))
        export_value = _variant_scalar_text(item.get("export_value"))
        normalized_key = raw_value.casefold()
        if not raw_value or not export_value or normalized_key in seen_values:
            continue
        seen_values.add(normalized_key)
        translations.append((raw_value[:200], export_value[:200]))

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


def _variant_scalar_text(value: Any) -> str:
    """Normalize safe scalar variant values without accepting booleans/objects."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        if value.is_integer():
            return str(int(value))
    return str(value).strip()
