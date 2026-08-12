"""Fail-closed downloader for public raster images used by OneBound image search.

The downloader deliberately has its own HTTP boundary: image URLs are
untrusted input, whereas OneBound endpoint URLs come from application
configuration.  Each redirect is independently resolved and connected through
an approved public IP address to prevent SSRF and DNS rebinding.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import re
import socket
import ssl
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

import certifi


MAX_PUBLIC_IMAGE_BYTES = 5 * 1024 * 1024
MAX_PUBLIC_IMAGE_REDIRECTS = 5
_MAX_URL_LENGTH = 4096
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_GENERIC_MEDIA_TYPES = frozenset({"", "application/octet-stream", "binary/octet-stream"})
_MEDIA_TYPE_ALIASES = {
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/png": "image/png",
    "image/gif": "image/gif",
    "image/webp": "image/webp",
    "image/bmp": "image/bmp",
    "image/x-ms-bmp": "image/bmp",
}
_DOH_HOSTNAME = "cloudflare-dns.com"
_DOH_ADDRESSES = ("1.1.1.1", "1.0.0.1")
_MAX_DOH_RESPONSE_BYTES = 64 * 1024
_PUBLIC_IMAGE_USER_AGENT = "Mozilla/5.0 (compatible; MainPGImageFetcher/1.0)"


class PublicImageFetchError(ValueError):
    """Raised when an untrusted image URL violates the fetch contract."""


@dataclass(frozen=True)
class ValidatedPublicImageUrl:
    url: str
    scheme: str
    hostname: str
    port: int
    request_target: str
    host_header: str
    addresses: tuple[str, ...]


@dataclass(frozen=True)
class PublicImageHttpResponse:
    status: int
    headers: Mapping[str, str]
    content: bytes


@dataclass(frozen=True)
class FetchedPublicImage:
    content: bytes
    media_type: str
    final_url: str


PublicImageResolver = Callable[[str, int], Iterable[str]]
PublicImageTransport = Callable[
    [ValidatedPublicImageUrl, str, float, int], PublicImageHttpResponse
]


def fetch_public_image(
    value: str,
    *,
    max_bytes: int = MAX_PUBLIC_IMAGE_BYTES,
    max_redirects: int = MAX_PUBLIC_IMAGE_REDIRECTS,
    timeout_seconds: float = 10.0,
    resolver: PublicImageResolver | None = None,
    transport: PublicImageTransport | None = None,
) -> FetchedPublicImage:
    """Download one publicly reachable raster image through checked IPs only."""
    if max_bytes < 1 or max_redirects < 0 or timeout_seconds <= 0:
        raise ValueError("public image fetch limits are invalid")
    current_url = str(value or "").strip()
    active_transport = transport or _request_once
    previous_scheme = ""
    retried_avif_transform = False

    for redirect_index in range(max_redirects + 1):
        validated = validate_public_image_url(current_url, resolver=resolver)
        if previous_scheme == "https" and validated.scheme != "https":
            raise PublicImageFetchError("image redirect cannot downgrade https")
        response = _request_from_approved_address(
            validated,
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
            transport=active_transport,
        )
        headers = {str(key).casefold(): str(item).strip() for key, item in response.headers.items()}
        if response.status in _REDIRECT_STATUSES:
            if redirect_index >= max_redirects:
                raise PublicImageFetchError("image has too many redirects")
            location = headers.get("location", "").strip()
            if not location:
                raise PublicImageFetchError("image redirect is missing location")
            previous_scheme = validated.scheme
            current_url = urljoin(validated.url, location)
            continue
        if response.status != 200:
            raise PublicImageFetchError("image request was not successful")
        jpeg_variant = _jpeg_variant_for_avif_transform(validated.url, headers)
        if jpeg_variant is not None and not retried_avif_transform:
            retried_avif_transform = True
            previous_scheme = validated.scheme
            current_url = jpeg_variant
            continue
        _validate_image_response(headers, response.content, max_bytes=max_bytes)
        media_type = _detected_image_type(response.content)
        return FetchedPublicImage(
            content=bytes(response.content),
            media_type=media_type,
            final_url=validated.url,
        )
    raise PublicImageFetchError("image has too many redirects")


def _jpeg_variant_for_avif_transform(url: str, headers: Mapping[str, str]) -> str | None:
    """Retry only explicit CDN `format/avif` transforms as JPEG for image search."""
    declared_media_type = headers.get("content-type", "").split(";", 1)[0].strip().casefold()
    if declared_media_type != "image/avif":
        return None
    parts = urlsplit(url)
    rewritten_query = re.sub(
        r"(?i)(^|[/&])format/avif(?=$|[/?&])",
        r"\1format/jpeg",
        parts.query,
    )
    if rewritten_query == parts.query:
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path, rewritten_query, ""))


def validate_public_image_url(
    value: str,
    *,
    resolver: PublicImageResolver | None = None,
) -> ValidatedPublicImageUrl:
    """Normalize and resolve a URL without allowing private network targets."""
    text = str(value or "").strip()
    if not text or len(text) > _MAX_URL_LENGTH or any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise PublicImageFetchError("image url is invalid")
    try:
        parts = urlsplit(text)
        explicit_port = parts.port
    except ValueError as error:
        raise PublicImageFetchError("image url is invalid") from error
    scheme = parts.scheme.casefold()
    if scheme not in {"http", "https"}:
        raise PublicImageFetchError("image url must be http or https")
    if parts.username is not None or parts.password is not None or parts.fragment:
        raise PublicImageFetchError("image url is invalid")
    raw_hostname = (parts.hostname or "").strip().rstrip(".")
    if not raw_hostname or "%" in raw_hostname:
        raise PublicImageFetchError("image url host is invalid")
    try:
        hostname = raw_hostname.encode("idna").decode("ascii").casefold()
    except UnicodeError as error:
        raise PublicImageFetchError("image url host is invalid") from error
    if hostname in {"local", "localhost", "localhost.localdomain"} or hostname.endswith((".local", ".localhost")):
        raise PublicImageFetchError("image url host is not public")
    expected_port = 443 if scheme == "https" else 80
    if explicit_port not in {None, expected_port}:
        raise PublicImageFetchError("image url port is not allowed")
    bracketed_host = f"[{hostname}]" if ":" in hostname else hostname
    path = parts.path or "/"
    return ValidatedPublicImageUrl(
        url=urlunsplit((scheme, bracketed_host, path, parts.query, "")),
        scheme=scheme,
        hostname=hostname,
        port=expected_port,
        request_target=urlunsplit(("", "", path, parts.query, "")),
        host_header=bracketed_host,
        addresses=_public_addresses(hostname, expected_port, resolver=resolver),
    )


def _public_addresses(
    hostname: str,
    port: int,
    *,
    resolver: PublicImageResolver | None,
) -> tuple[str, ...]:
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            values = (resolver or _default_resolver)(hostname, port)
        except OSError as error:
            raise PublicImageFetchError("image host cannot be resolved") from error
    else:
        values = (str(literal),)
    addresses: list[str] = []
    try:
        for value in values:
            address = ipaddress.ip_address(str(value).strip())
            if not address.is_global:
                raise PublicImageFetchError("image host does not resolve to public addresses")
            normalized = str(address)
            if normalized not in addresses:
                addresses.append(normalized)
    except PublicImageFetchError:
        raise
    except (TypeError, ValueError) as error:
        raise PublicImageFetchError("image host cannot be resolved") from error
    if not addresses:
        raise PublicImageFetchError("image host cannot be resolved")
    return tuple(addresses)


def _default_resolver(hostname: str, port: int) -> Iterable[str]:
    try:
        results = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError:
        return _resolve_via_doh(hostname)
    addresses = tuple(result[4][0] for result in results)
    if _contains_non_public_address(addresses):
        return _resolve_via_doh(hostname)
    return addresses


def _contains_non_public_address(values: Iterable[str]) -> bool:
    try:
        return any(not ipaddress.ip_address(str(value).strip()).is_global for value in values)
    except (TypeError, ValueError):
        return True


def _request_from_approved_address(
    validated: ValidatedPublicImageUrl,
    *,
    timeout_seconds: float,
    max_bytes: int,
    transport: PublicImageTransport,
) -> PublicImageHttpResponse:
    last_error: BaseException | None = None
    for address in validated.addresses:
        try:
            return transport(validated, address, timeout_seconds, max_bytes)
        except PublicImageFetchError:
            raise
        except (OSError, TimeoutError, ssl.SSLError, http.client.HTTPException) as error:
            last_error = error
    raise PublicImageFetchError("image request failed") from last_error


def _request_once(
    validated: ValidatedPublicImageUrl,
    connect_ip: str,
    timeout_seconds: float,
    max_bytes: int,
) -> PublicImageHttpResponse:
    if validated.scheme == "https":
        connection: http.client.HTTPConnection = _PinnedHTTPSConnection(
            validated.hostname, validated.port, connect_ip=connect_ip, timeout=timeout_seconds
        )
    else:
        connection = _PinnedHTTPConnection(
            validated.hostname, validated.port, connect_ip=connect_ip, timeout=timeout_seconds
        )
    response: http.client.HTTPResponse | None = None
    try:
        connection.request(
            "GET",
            validated.request_target,
            headers={
                "Accept": "image/jpeg,image/png,image/gif,image/webp,image/bmp",
                "Connection": "close",
                "Host": validated.host_header,
                # Temu image CDNs reject anonymous HTTP-library requests with
                # 403, while accepting the same public image with a browser-like
                # agent.  This does not weaken URL/IP/redirect/content checks.
                "User-Agent": _PUBLIC_IMAGE_USER_AGENT,
            },
        )
        response = connection.getresponse()
        headers = {str(key): str(value).strip() for key, value in response.getheaders()}
        content = b""
        if response.status == 200:
            _validate_declared_length(headers.get("Content-Length", ""), max_bytes=max_bytes)
            content = response.read(max_bytes + 1)
        return PublicImageHttpResponse(status=response.status, headers=headers, content=content)
    finally:
        if response is not None:
            response.close()
        connection.close()


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, hostname: str, port: int, *, connect_ip: str, timeout: float) -> None:
        self._connect_ip = connect_ip
        super().__init__(hostname, port=port, timeout=timeout)

    def connect(self) -> None:
        self.sock = socket.create_connection((self._connect_ip, self.port), self.timeout, self.source_address)
        _assert_connected_peer(self.sock, self._connect_ip)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, port: int, *, connect_ip: str, timeout: float) -> None:
        self._connect_ip = connect_ip
        # The Windows/Python runtime may not expose the same root store used by
        # the browser.  That made the pinned Cloudflare DoH fallback fail before
        # Temu CDN hosts could be resolved.  Use the maintained CA bundle that
        # ships with the application dependencies for both DoH and image TLS.
        super().__init__(
            hostname,
            port=port,
            timeout=timeout,
            context=ssl.create_default_context(cafile=certifi.where()),
        )

    def connect(self) -> None:
        raw_socket = socket.create_connection((self._connect_ip, self.port), self.timeout, self.source_address)
        try:
            _assert_connected_peer(raw_socket, self._connect_ip)
            self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)
        except BaseException:
            raw_socket.close()
            raise


def _resolve_via_doh(hostname: str) -> tuple[str, ...]:
    """Resolve public image hosts through a pinned Cloudflare DoH endpoint."""
    last_error: BaseException | None = None
    for connect_ip in _DOH_ADDRESSES:
        try:
            payload = _request_doh_json(hostname, connect_ip)
            answers = payload.get("Answer", ())
            addresses = tuple(
                str(answer.get("data", "")).strip()
                for answer in answers
                if isinstance(answer, Mapping) and answer.get("type") in {1, 28}
            )
            if addresses:
                return addresses
        except (
            OSError,
            TimeoutError,
            ssl.SSLError,
            http.client.HTTPException,
            PublicImageFetchError,
            ValueError,
        ) as error:
            last_error = error
    raise PublicImageFetchError("image host cannot be resolved") from last_error


def _request_doh_json(hostname: str, connect_ip: str) -> Mapping[str, Any]:
    connection = _PinnedHTTPSConnection(
        _DOH_HOSTNAME,
        443,
        connect_ip=connect_ip,
        timeout=10.0,
    )
    response: http.client.HTTPResponse | None = None
    try:
        query = urlencode({"name": hostname, "type": "A"})
        connection.request(
            "GET",
            f"/dns-query?{query}",
            headers={
                "Accept": "application/dns-json",
                "Connection": "close",
                "Host": _DOH_HOSTNAME,
            },
        )
        response = connection.getresponse()
        if response.status != 200:
            raise PublicImageFetchError("public dns request was not successful")
        content = response.read(_MAX_DOH_RESPONSE_BYTES + 1)
        if len(content) > _MAX_DOH_RESPONSE_BYTES:
            raise PublicImageFetchError("public dns response is too large")
        payload = json.loads(content.decode("utf-8"))
        if not isinstance(payload, Mapping) or payload.get("Status") != 0:
            raise PublicImageFetchError("public dns returned no answer")
        return payload
    finally:
        if response is not None:
            response.close()
        connection.close()


def _assert_connected_peer(active_socket: Any, expected_ip: str) -> None:
    try:
        actual = ipaddress.ip_address(str(active_socket.getpeername()[0]))
        expected = ipaddress.ip_address(expected_ip)
    except (AttributeError, IndexError, TypeError, ValueError, OSError) as error:
        raise PublicImageFetchError("image connection peer could not be verified") from error
    if actual != expected:
        raise PublicImageFetchError("image connection peer did not match the approved address")


def _validate_image_response(headers: Mapping[str, str], content: bytes, *, max_bytes: int) -> None:
    if headers.get("content-encoding", "").casefold() not in {"", "identity"}:
        raise PublicImageFetchError("encoded image responses are not allowed")
    _validate_declared_length(headers.get("content-length", ""), max_bytes=max_bytes)
    if not content:
        raise PublicImageFetchError("image is empty")
    if len(content) > max_bytes:
        raise PublicImageFetchError("image exceeds the size limit")
    detected = _detected_image_type(content)
    declared = headers.get("content-type", "").split(";", 1)[0].strip().casefold()
    normalized = _MEDIA_TYPE_ALIASES.get(declared)
    if declared not in _GENERIC_MEDIA_TYPES and normalized is None:
        raise PublicImageFetchError("image response content type is not allowed")
    if normalized is not None and normalized != detected:
        raise PublicImageFetchError("image response content type does not match its bytes")


def _validate_declared_length(value: str, *, max_bytes: int) -> None:
    text = str(value or "").strip()
    if not text:
        return
    try:
        size = int(text)
    except ValueError as error:
        raise PublicImageFetchError("image content length is invalid") from error
    if size < 1 or size > max_bytes:
        raise PublicImageFetchError("image content length is outside the size limit")


def _detected_image_type(content: bytes) -> str:
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content[:6] in {b"GIF87a", b"GIF89a"}:
        return "image/gif"
    if len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    if content.startswith(b"BM"):
        return "image/bmp"
    raise PublicImageFetchError("image bytes are not a supported raster image")
