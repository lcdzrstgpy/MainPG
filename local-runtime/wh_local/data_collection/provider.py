"""Minimal, auditable OneBound provider for 1688 daily selection.

The transport is deliberately injected so callers can use a deterministic fake in
tests.  ``UrllibTransport`` is only the production default; this module never
retains downloaded image bytes beyond encoding the upload request.
"""

from __future__ import annotations

import base64
import http.client
import ipaddress
import json
import socket
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError
from urllib.parse import unquote, urlencode, urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .contracts import (
    ApiEvidence,
    DailySelectionError,
    is_sensitive_field,
    redact_sensitive_text,
)
from .criteria import DailySelectionCriteria
from .public_image_fetch import (
    FetchedPublicImage,
    PublicImageFetchError,
    fetch_public_image,
)


@dataclass(frozen=True)
class HttpResponse:
    """The small HTTP boundary used by the provider and its test fake."""

    status: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)


class HttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float,
        resolved_address: str | None = None,
    ) -> HttpResponse: ...


class HostResolver(Protocol):
    """Resolve a hostname before each pinned public-image connection."""

    def resolve(self, hostname: str, port: int) -> tuple[str, ...]: ...


class SocketHostResolver:
    """Standard-library resolver that returns only numeric socket addresses."""

    def resolve(self, hostname: str, port: int) -> tuple[str, ...]:
        answers = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        return tuple(dict.fromkeys(answer[4][0] for answer in answers))


class PublicImageFetcher(Protocol):
    """Download one untrusted public image under the safe-fetch contract."""

    def fetch(self, url: str) -> FetchedPublicImage: ...


class DefaultPublicImageFetcher:
    """Production adapter around the isolated public image downloader."""

    def __init__(
        self,
        *,
        max_bytes: int,
        timeout_seconds: float,
        resolver: HostResolver | None = None,
    ) -> None:
        self._max_bytes = max_bytes
        self._timeout_seconds = timeout_seconds
        self._resolver = resolver

    def fetch(self, url: str) -> FetchedPublicImage:
        return fetch_public_image(
            url,
            max_bytes=self._max_bytes,
            timeout_seconds=self._timeout_seconds,
            resolver=self._resolver.resolve if self._resolver is not None else None,
        )


class UrllibTransport:
    """Standard-library production transport; tests inject ``FakeTransport``."""

    def __init__(self, *, max_response_bytes: int | None = None) -> None:
        self._max_response_bytes = max_response_bytes
        self._opener = build_opener(_NoRedirect())

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float,
        resolved_address: str | None = None,
    ) -> HttpResponse:
        query = urlencode({key: value for key, value in (params or {}).items() if value is not None})
        target = f"{url}?{query}" if query else url
        if resolved_address is not None:
            return self._request_pinned(method, target, body, headers, timeout, resolved_address)
        request = Request(target, data=body, headers=dict(headers or {}), method=method)
        try:
            with self._opener.open(request, timeout=timeout) as upstream:  # noqa: S310 - explicitly configured API URL
                return HttpResponse(
                    status=upstream.status,
                    body=self._read_response(upstream),
                    headers=dict(upstream.headers.items()),
                )
        except HTTPError as error:
            return HttpResponse(
                status=error.code,
                body=self._read_response(error),
                headers=dict(error.headers.items()),
            )

    def _request_pinned(
        self,
        method: str,
        target: str,
        body: bytes | None,
        headers: Mapping[str, str] | None,
        timeout: float,
        resolved_address: str,
    ) -> HttpResponse:
        """Connect to the checked numeric address, never re-resolving the hostname."""
        parsed = urlparse(target)
        hostname = parsed.hostname
        if hostname is None:
            raise ValueError("pinned request requires a hostname")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        request_target = urlunparse(("", "", parsed.path or "/", parsed.params, parsed.query, ""))
        host_header = hostname if parsed.port is None else f"{hostname}:{parsed.port}"
        if parsed.scheme == "https":
            connection: http.client.HTTPConnection = _PinnedHTTPSConnection(
                hostname, port, resolved_address, timeout
            )
        elif parsed.scheme == "http":
            connection = http.client.HTTPConnection(resolved_address, port, timeout=timeout)
        else:
            raise ValueError("pinned request requires http or https")
        try:
            connection.putrequest(method, request_target, skip_host=True)
            connection.putheader("Host", host_header)
            for key, value in (headers or {}).items():
                if key.casefold() != "host":
                    connection.putheader(key, value)
            connection.endheaders(body)
            upstream = connection.getresponse()
            try:
                return HttpResponse(
                    status=upstream.status,
                    body=self._read_response(upstream),
                    headers=dict(upstream.getheaders()),
                )
            finally:
                upstream.close()
        finally:
            connection.close()

    def _read_response(self, upstream: Any) -> bytes:
        if self._max_response_bytes is None:
            return upstream.read()
        # Read one extra byte so callers can reject oversize images without
        # materialising the remainder of an untrusted remote response.
        return upstream.read(self._max_response_bytes + 1)


class _NoRedirect(HTTPRedirectHandler):
    """Return redirects to the provider so an image target is never silently changed."""

    def redirect_request(self, request: Request, fp: Any, code: int, message: str, headers: Any, newurl: str) -> None:
        return None


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection with TLS verification for hostname but a fixed IP peer."""

    def __init__(self, host: str, port: int, resolved_address: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._resolved_address = resolved_address

    def connect(self) -> None:
        self.sock = self._create_connection((self._resolved_address, self.port), self.timeout, self.source_address)
        if self._tunnel_host:
            self._tunnel()
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


@dataclass(frozen=True)
class ProviderCallResult:
    """Sanitized API response plus one audit item per HTTP call performed."""

    response: Mapping[str, Any]
    audits: tuple[ApiEvidence, ...]
    error: DailySelectionError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def audit(self) -> ApiEvidence:
        """The terminal call audit, retained for single-call consumers."""
        return self.audits[-1]


class OneBound1688Provider:
    """OneBound's 1688-only keyword, image, and item-detail operations."""

    _provider_name = "onebound-1688"

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        transport: HttpTransport | None = None,
        resolver: HostResolver | None = None,
        image_fetcher: PublicImageFetcher | None = None,
    ) -> None:
        self._api_key = self._required_text(config, "api_key")
        self._api_secret = self._required_text(config, "api_secret")
        self._base_url = self._required_url(
            config,
            "base_url",
            sensitive_values=(self._api_key, self._api_secret),
        )
        self._timeout_seconds = self._positive_number(config.get("timeout_seconds", 10), "timeout_seconds")
        enabled = config.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        self._enabled = enabled
        self._image_max_bytes = self._positive_integer(config.get("image_max_bytes", 5 * 1024 * 1024), "image_max_bytes")
        self._transport = transport or UrllibTransport(max_response_bytes=self._image_max_bytes)
        self._resolver = resolver or SocketHostResolver()
        self._image_fetcher = image_fetcher or DefaultPublicImageFetcher(
            max_bytes=self._image_max_bytes,
            timeout_seconds=self._timeout_seconds,
        )

    def safe_summary(self) -> Mapping[str, Any]:
        """Return diagnostic configuration without credentials or credential hints."""
        return {
            "provider": self._provider_name,
            "platform": "1688",
            "base_url": self._base_url,
            "timeout_seconds": self._timeout_seconds,
            "enabled": self._enabled,
            "image_max_bytes": self._image_max_bytes,
        }

    def search_keyword(self, criteria: DailySelectionCriteria) -> ProviderCallResult:
        if criteria.collection_mode != "keyword":
            return self._local_error("item_search", "invalid_request", "keyword criteria are required")
        return self._api_call(
            "item_search",
            {"q": " ".join(criteria.keywords), "page_size": criteria.target_count},
            request_metadata={"query_count": len(criteria.keywords)},
        )

    def upload_reference_image(self, reference_image_url: str) -> ProviderCallResult:
        if not self._enabled:
            return self._local_error("upload_img", "provider_disabled", "provider is disabled")
        downloaded, download_audit, error = self._download_reference_image(reference_image_url)
        if error is not None:
            return ProviderCallResult(response={}, audits=(download_audit,), error=error)
        try:
            encoded_image = base64.b64encode(downloaded).decode("ascii")
        finally:
            # Drop the raw bytes before any API response, audit, or error is created.
            del downloaded
        return self._api_call(
            "upload_img",
            {"cache": "no"},
            body=urlencode({"imgcode": encoded_image}).encode("utf-8"),
            http_method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            request_metadata={"image_size_bytes": None},
            prior_audits=(download_audit,),
        )

    def search_by_image(self, criteria: DailySelectionCriteria) -> ProviderCallResult:
        if criteria.collection_mode != "image" or criteria.reference_image_url is None:
            return self._local_error("item_search_img", "invalid_request", "image criteria are required")
        if not self._enabled:
            return self._local_error("item_search_img", "provider_disabled", "provider is disabled")
        uploaded = self.upload_reference_image(criteria.reference_image_url)
        if not uploaded.ok:
            return uploaded
        image_id = self._image_id_from_upload_response(uploaded.response)
        if not isinstance(image_id, str) or not image_id.strip():
            return self._result_with_error(
                uploaded.audits,
                "item_search_img",
                "upstream_failed",
                "image upload response did not include an image id",
            )
        return self._api_call(
            "item_search_img",
            {"imgid": image_id.strip(), "page_size": criteria.target_count, "cache": "no"},
            request_metadata={"image_id_present": True, "keyword_tag_count": len(criteria.keyword_tags)},
            prior_audits=uploaded.audits,
            extra_response_summary={"upload_outcome": uploaded.audit.response_summary.get("outcome")},
        )

    def get_item_detail(self, offer_id: str) -> ProviderCallResult:
        if not isinstance(offer_id, str) or not offer_id.strip():
            return self._local_error("item_get", "invalid_request", "offer_id is required")
        return self._api_call(
            "item_get",
            {"num_iid": offer_id.strip()},
            request_metadata={"offer_id_present": True},
        )

    def _download_reference_image(
        self, reference_image_url: str
    ) -> tuple[bytes | None, ApiEvidence, DailySelectionError | None]:
        safe_url = self._validated_remote_image_url(reference_image_url)
        try:
            image = self._image_fetcher.fetch(reference_image_url)
        except PublicImageFetchError:
            audit = self._audit(
                "download_reference_image",
                "invalid_request",
                request_summary={"http_method": "GET", "image_url": safe_url or "[invalid]"},
                response_summary={"fetch_policy": "rejected"},
            )
            return None, audit, self._error("invalid_request", "reference image could not be fetched safely")
        except Exception:
            audit = self._audit(
                "download_reference_image",
                "upstream_failed",
                request_summary={"http_method": "GET", "image_url": safe_url or "[invalid]"},
            )
            return None, audit, self._error("upstream_failed", "reference image download failed")
        audit = self._audit(
            "download_reference_image",
            "success",
            request_summary={"http_method": "GET", "image_url": safe_url or "[validated by fetcher]"},
            response_summary={
                "media_type": image.media_type,
                "image_size_bytes": len(image.content),
                "final_url": self._validated_remote_image_url(image.final_url) or "[validated]",
            },
        )
        return image.content, audit, None

    def _api_call(
        self,
        operation: str,
        parameters: Mapping[str, Any],
        *,
        body: bytes | None = None,
        http_method: str = "GET",
        headers: Mapping[str, str] | None = None,
        request_metadata: Mapping[str, Any],
        prior_audits: tuple[ApiEvidence, ...] = (),
        extra_response_summary: Mapping[str, Any] | None = None,
    ) -> ProviderCallResult:
        if not self._enabled:
            return self._result_with_error(prior_audits, operation, "provider_disabled", "provider is disabled")
        request_summary = {"http_method": http_method, "operation": operation, **request_metadata}
        try:
            upstream = self._transport.request(
                http_method,
                self._endpoint(operation),
                params={"key": self._api_key, "secret": self._api_secret, **parameters},
                body=body,
                headers=dict(headers) if headers is not None else None,
                timeout=self._timeout_seconds,
            )
        except (TimeoutError, socket.timeout):
            audit = self._audit(operation, "timeout", request_summary=request_summary)
            return ProviderCallResult({}, prior_audits + (audit,), self._error("timeout", "OneBound request timed out"))
        except Exception:
            audit = self._audit(operation, "upstream_failed", request_summary=request_summary)
            return ProviderCallResult({}, prior_audits + (audit,), self._error("upstream_failed", "OneBound request failed"))

        payload = self._json_mapping(upstream.body)
        outcome = self._outcome_for_status(upstream.status, payload)
        response_summary: dict[str, Any] = {
            "http_status": upstream.status,
            "outcome": outcome,
        }
        code = payload.get("code")
        if isinstance(code, (str, int, float)):
            response_summary["upstream_code"] = code
        request_id = payload.get("request_id")
        if isinstance(request_id, str):
            response_summary["request_id"] = request_id
        item_count = self._item_count(payload)
        if item_count is not None:
            response_summary["item_count"] = item_count
        if extra_response_summary:
            response_summary.update(extra_response_summary)
        audit = self._audit(
            operation,
            outcome,
            request_summary=request_summary,
            response_summary=response_summary,
            request_id=request_id if isinstance(request_id, str) else None,
        )
        sanitized = self._sanitize(payload)
        if outcome in {"success", "no_results"}:
            return ProviderCallResult(sanitized, prior_audits + (audit,))
        return ProviderCallResult(
            sanitized,
            prior_audits + (audit,),
            self._error(outcome, "OneBound returned an unsuccessful response", upstream.status, request_id),
        )

    def _local_error(self, operation: str, code: str, message: str) -> ProviderCallResult:
        return self._result_with_error((), operation, code, message)

    def _result_with_error(
        self,
        prior_audits: tuple[ApiEvidence, ...],
        operation: str,
        code: str,
        message: str,
    ) -> ProviderCallResult:
        audit = self._audit(operation, code, request_summary={"operation": operation})
        return ProviderCallResult({}, prior_audits + (audit,), self._error(code, message))

    def _audit(
        self,
        operation: str,
        outcome: str,
        *,
        request_summary: Mapping[str, Any],
        response_summary: Mapping[str, Any] | None = None,
        request_id: str | None = None,
    ) -> ApiEvidence:
        return ApiEvidence(
            provider=self._provider_name,
            operation=operation,
            request_id=self._redact_text(request_id) if request_id else None,
            captured_at=datetime.now(UTC).isoformat(),
            request_summary=self._sanitize(dict(request_summary)),
            response_summary=self._sanitize({"outcome": outcome, **dict(response_summary or {})}),
        )

    def _error(
        self, code: str, message: str, http_status: int | None = None, request_id: str | None = None
    ) -> DailySelectionError:
        context: dict[str, Any] = {}
        if http_status is not None:
            context["http_status"] = http_status
        if request_id:
            context["request_id"] = request_id
        return DailySelectionError(code=code, message=self._redact_text(message), context=self._sanitize(context))

    def _outcome_for_status(self, status: int, payload: Mapping[str, Any]) -> str:
        code = str(payload.get("code", "")).casefold()
        message = str(payload.get("msg", payload.get("message", ""))).casefold()
        signal = f"{code} {message}"
        if 200 <= status < 300 and code == "2000":
            return "no_results"
        if 200 <= status < 300 and code in {"", "0", "200"}:
            return "success"
        if status in {401, 403} or any(marker in signal for marker in ("auth", "api key", "secret", "permission")):
            return "authentication_failed"
        if status == 429 or any(marker in signal for marker in ("rate", "too many", "frequent")):
            return "rate_limited"
        if status == 402 or any(marker in signal for marker in ("quota", "balance", "exhausted", "insufficient")):
            return "quota_exhausted"
        if status == 400 or any(marker in signal for marker in ("parameter", "param", "invalid", "missing")):
            return "invalid_request"
        return "upstream_failed"

    @staticmethod
    def _item_count(payload: Mapping[str, Any]) -> int | None:
        data = payload.get("data")
        if isinstance(data, Mapping):
            items = data.get("items")
            if isinstance(items, list):
                return len(items)
        items = payload.get("items")
        if not isinstance(items, Mapping):
            return None
        collection = items.get("item")
        if isinstance(collection, list):
            return len(collection)
        return 1 if isinstance(collection, Mapping) else None

    @staticmethod
    def _image_id_from_upload_response(payload: Mapping[str, Any]) -> Any:
        items = payload.get("items")
        image_id = OneBound1688Provider._nested_image_id(items)
        if image_id is not None:
            return image_id
        data = payload.get("data")
        return data.get("imgid") if isinstance(data, Mapping) else None

    @staticmethod
    def _nested_image_id(value: Any) -> str | None:
        if isinstance(value, Mapping):
            for name in ("imgid", "img_id", "url"):
                candidate = value.get(name)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate
            for child in value.values():
                image_id = OneBound1688Provider._nested_image_id(child)
                if image_id is not None:
                    return image_id
        elif isinstance(value, (list, tuple)):
            for child in value:
                image_id = OneBound1688Provider._nested_image_id(child)
                if image_id is not None:
                    return image_id
        return None

    @staticmethod
    def _json_mapping(body: bytes) -> Mapping[str, Any]:
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, Mapping) else {}

    def _sanitize(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): self._sanitize(item)
                for key, item in value.items()
                if not self._sensitive_key(key)
            }
        if isinstance(value, (list, tuple)):
            return tuple(self._sanitize(item) for item in value)
        if isinstance(value, (bytes, bytearray, memoryview)):
            return "[binary omitted]"
        return self._redact_text(value) if isinstance(value, str) else value

    def _redact_text(self, value: str) -> str:
        return redact_sensitive_text(value, (self._api_key, self._api_secret))

    @staticmethod
    def _sensitive_key(key: object) -> bool:
        return is_sensitive_field(key)

    def _endpoint(self, operation: str) -> str:
        return f"{self._base_url}/{operation}/"

    @staticmethod
    def _validated_remote_image_url(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        hostname = parsed.hostname.rstrip(".").casefold()
        if hostname == "localhost":
            return None
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            return None
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))

    def _resolve_public_address(self, hostname: str | None, port: int) -> str | None:
        if not hostname or port < 1:
            return None
        normalized_host = hostname.rstrip(".")
        try:
            answers = self._resolver.resolve(normalized_host, port)
        except (OSError, ValueError):
            return None
        if not answers:
            return None
        try:
            addresses = tuple(ipaddress.ip_address(answer) for answer in answers)
        except ValueError:
            return None
        # Reject mixed answers as well as each non-public address; the selected
        # first address is passed to the transport, preventing DNS rebinding.
        if any(not address.is_global for address in addresses):
            return None
        return str(addresses[0])

    @staticmethod
    def _required_text(config: Mapping[str, Any], name: str) -> str:
        value = config.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be configured")
        return value.strip()

    @classmethod
    def _required_url(
        cls,
        config: Mapping[str, Any],
        name: str,
        *,
        sensitive_values: tuple[str, ...] = (),
    ) -> str:
        value = cls._required_text(config, name)
        parsed = urlparse(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or redact_sensitive_text(
                unquote(parsed.path), sensitive_values
            )
            != unquote(parsed.path)
        ):
            raise ValueError(f"{name} must be an http or https URL")
        return value.rstrip("/")

    @staticmethod
    def _positive_number(value: object, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ValueError(f"{name} must be a positive number")
        return float(value)

    @staticmethod
    def _positive_integer(value: object, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
        return value
