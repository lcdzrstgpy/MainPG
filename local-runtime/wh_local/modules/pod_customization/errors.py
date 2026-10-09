from __future__ import annotations

import re


_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_AUTHORIZATION = re.compile(
    r"\bauthorization\s*[:=]\s*(?:bearer\s+)?[^\s,;]+",
    re.IGNORECASE,
)
_BEARER = re.compile(r"\bbearer\s+[^\s,;]+", re.IGNORECASE)
_SECRET_ASSIGNMENT = re.compile(
    r"\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|key)"
    r"\s*[:=]\s*[^\s,;]+",
    re.IGNORECASE,
)


class PodExecutionExpired(RuntimeError):
    """Raised when a worker-side mutation is rejected because the batch epoch has advanced."""


class PodProviderResultReceivedError(RuntimeError):
    def __init__(self, provider: str, message: str) -> None:
        self.provider = str(provider)
        super().__init__(safe_error_message(message))


def safe_error_message(value: object, *, fallback: str = "POD provider request failed") -> str:
    raw = " ".join(str(value or "").split())
    if not raw:
        return fallback
    redacted = _URL.sub("[redacted-url]", raw)
    redacted = _AUTHORIZATION.sub("authorization=[redacted]", redacted)
    redacted = _BEARER.sub("[redacted]", redacted)
    redacted = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[redacted]", redacted)
    return (redacted.strip() or fallback)[:500]


def image_provider_outcome_for_exception(exc: BaseException) -> str:
    return "success" if isinstance(exc, PodProviderResultReceivedError) else "no_return"
