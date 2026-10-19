"""Strict internal Doubao listing-text client."""

from __future__ import annotations

import json
import math
import re
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
            retry_delay = RETRY_BACKOFF_SECONDS
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
                # 网关给出 Retry-After 时按其退避：0.5s 抢跑往往在服务端仍在处理时再次
                # 发起请求，只会白白消耗重试次数。
                if exc.retry_after:
                    retry_delay = float(exc.retry_after)
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
                if not exc.retryable or attempt >= min(
                    MAX_ATTEMPTS, exc.max_attempts or MAX_ATTEMPTS
                ):
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
                time.sleep(retry_delay)
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
        "Return only the exact JSON object required above: no Markdown code fences, no "
        "preamble or closing commentary, and do not mention this retry."
    )


def text_result_from_dict(payload: Mapping[str, Any]) -> DoubaoTextResult:
    return _result_from_payload(dict(payload))


def _parse_text_result(content: str) -> DoubaoTextResult:
    payload = _load_json_payload(content)
    if payload is None:
        raise DoubaoTextError(
            "Doubao text response did not contain strict JSON"
            f" (response shape: {_response_shape(content)})",
            error_kind="invalid_response",
            retryable=True,
        )
    return _result_from_payload(payload)


_FENCE_PATTERN = re.compile(r"```[A-Za-z0-9_+-]*[ \t]*\r?\n?(.*?)```", re.DOTALL)


def _load_json_payload(content: Any) -> Any | None:
    """从模型返回中取出 JSON 负载；取不到返回 None。

    这里只做「搬运」不做「放宽」：候选内容仍需通过 ``_result_from_payload`` 的严格结构
    校验（键集合、类型、数值范围），所以少字段、多字段、类型错误的响应依旧会被拒绝。
    """
    if not isinstance(content, str):
        return None
    for candidate in _json_candidates(content):
        try:
            return json.loads(candidate)
        except (ValueError, TypeError):
            continue
    return None


def _json_candidates(content: str) -> list[str]:
    """按「最可能正确」的顺序给出候选：原文 → 去围栏 → 首个配平的花括号对象。

    实测豆包会把契约要求的 JSON 包进 ```json 代码块，或在前后补一句说明文字；这两种
    都只是包装问题，不应该让整条商品判失败。
    """
    candidates: list[str] = [content]
    stripped = _strip_markdown_fences(content)
    if stripped and stripped != content:
        candidates.append(stripped)
    for source in (stripped or content, content):
        extracted = _first_balanced_object(source)
        if extracted and extracted not in candidates:
            candidates.append(extracted)
    return candidates


def _strip_markdown_fences(text: str) -> str:
    match = _FENCE_PATTERN.search(text)
    return match.group(1).strip() if match else ""


def _first_balanced_object(text: str) -> str:
    """返回文本中第一个花括号配平的 ``{...}`` 片段（跳过字符串字面量与转义）。

    只处理对象，因为本模块契约始终是单个 JSON 对象；配平失败（典型是响应被截断）时返回
    空串，由调用方按失败处理——绝不猜测或补全缺失内容。
    """
    start = text.find("{")
    if start < 0:
        return ""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return ""


def _response_shape(content: Any) -> str:
    """失败的「形状」指纹：只有长度与结构特征，不含任何模型正文。

    写入任务诊断后即可区分「被 Markdown 围栏/夹带说明」与「响应被截断」这两类完全不同
    的病因，且不会把商品数据带进日志。
    """
    if not isinstance(content, str):
        return f"non-text ({type(content).__name__})"
    text = content.strip()
    fenced = bool(_FENCE_PATTERN.search(text))
    body = _strip_markdown_fences(text) or text
    return (
        f"length={len(text)}, fenced={fenced},"
        f" object_balanced={bool(_first_balanced_object(body))}"
    )


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
