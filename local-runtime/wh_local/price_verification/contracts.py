"""Security-focused contracts for read-only price verification work."""

from __future__ import annotations

import json
import math
import re
from decimal import Decimal
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_JSON_DEPTH = 20
ALLOWED_PLUGIN_COMMAND_TYPES = frozenset(
    {"temu_price_quote_discovery", "source_browser_image_search"}
)
_SENSITIVE_FIELD_NAMES = frozenset(
    {
        "access_token",
        "access_key",
        "api_token",
        "api_key",
        "api_secret",
        "apikey",
        "authorization",
        "auth_token",
        "bearer_token",
        "client_secret",
        "client_token",
        "cookie",
        "credential",
        "credentials",
        "id_token",
        "key",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "session",
        "session_token",
        "token",
    }
)
_INLINE_CREDENTIAL = re.compile(
    r"(?i)(\b(?:access[_-]?(?:key|token)|api[_-]?(?:key|secret|token)|auth(?:orization|[_-]?token)|"
    r"bearer[_-]?token|client[_-]?(?:secret|token)|cookie|credential(?:s)?|id[_-]?token|"
    r"key|password|private[_-]?key|refresh[_-]?token|secret|session(?:[_-]?token)?|token)\b\s*[=:]\s*)"
    r"(?:bearer\s+)?[^\s,;]+"
)
_BEARER_CREDENTIAL = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_WRITE_ACTION = re.compile(
    r"(?i)(?:^|[^a-z])(?:accept|reject|approve|cancel|create|delete|"
    r"modify|publish|purchase|save|submit|update|write|cart|order|"
    r"(?:change|modify|set|update)[\s_-]?price|price[\s_-]?(?:change|modify|set|update))"
    r"(?:[^a-z]|$)"
)


class PriceVerificationContractError(ValueError):
    """Raised when an input cannot safely cross the module boundary."""


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PriceVerificationContractError(f"{field_name} is required")
    return value.strip()


def _is_sensitive_field(name: object) -> bool:
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(name).strip())
    normalized = value.replace("-", "_").casefold()
    if normalized in _SENSITIVE_FIELD_NAMES:
        return True
    return bool(set(part for part in normalized.split("_") if part) & {
        "credential",
        "credentials",
        "password",
        "secret",
        "token",
    })


def redact_sensitive_text(value: str) -> str:
    """Remove credentials written inline in otherwise safe diagnostic text."""
    value = _BEARER_CREDENTIAL.sub("Bearer [REDACTED]", value)
    return _INLINE_CREDENTIAL.sub(lambda match: f"{match.group(1)}[REDACTED]", value)


def redact_sensitive(value: Any) -> Any:
    """Recursively preserve useful JSON while replacing credential values."""
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if _is_sensitive_field(key) else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


def _reject_platform_write(value: Any, *, key: str | None = None) -> None:
    if isinstance(value, Mapping):
        for child_key, child_value in value.items():
            _reject_platform_write(child_value, key=str(child_key))
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_platform_write(child, key=key)
    elif isinstance(value, str) and (
        key is not None or _WRITE_ACTION.search(value) is not None
    ):
        if _WRITE_ACTION.search(value) is not None:
            raise PriceVerificationContractError("platform write actions are forbidden")


def _safe_json_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= MAX_JSON_DEPTH:
        raise PriceVerificationContractError("JSON payload exceeds maximum depth of 20")
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise PriceVerificationContractError("JSON payload cannot contain binary data")
    if isinstance(value, BaseModel):
        return _safe_json_value(value.model_dump(mode="json"), depth=depth)
    if isinstance(value, Mapping):
        return {
            str(key): _safe_json_value(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_safe_json_value(item, depth=depth + 1) for item in value]
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        raise PriceVerificationContractError("JSON payload numbers must be finite")
    if isinstance(value, Decimal):
        if value.is_finite():
            return str(value)
        raise PriceVerificationContractError("JSON payload numbers must be finite")
    raise PriceVerificationContractError("JSON payload contains a non-serializable value")


def safe_json_dumps(value: Any) -> str:
    """Serialize redacted JSON only, with deterministic depth and size limits."""
    _reject_platform_write(value)
    safe = _safe_json_value(redact_sensitive(value))
    encoded = json.dumps(safe, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_JSON_BYTES:
        raise PriceVerificationContractError("JSON payload exceeds maximum size of 16 MiB")
    return encoded


def safe_json_value(value: Any) -> Any:
    """Return a JSON-safe, recursively redacted snapshot."""
    return json.loads(safe_json_dumps(value))


class _ContractModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except ValidationError as error:
            raise PriceVerificationContractError(str(error)) from error


class PriceVerificationActor(_ContractModel):
    actor_id: str
    workspace_id: str

    @field_validator("actor_id", "workspace_id", mode="before")
    @classmethod
    def _non_empty_ids(cls, value: object, info: Any) -> str:
        return _required_text(value, info.field_name)


class PluginCommandRequest(_ContractModel):
    command_type: Literal["temu_price_quote_discovery", "source_browser_image_search"]
    payload: Mapping[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("payload", mode="before")
    @classmethod
    def _safe_payload(cls, value: object) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise PriceVerificationContractError("payload must be a mapping")
        _reject_platform_write(value)
        return safe_json_value(value)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def _valid_idempotency_key(cls, value: object) -> str:
        return _required_text(value, "idempotency_key")
