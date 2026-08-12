from __future__ import annotations

import ipaddress
import socket
from typing import Any, Iterator
from urllib.parse import urlsplit

import httpx


STATION_BASE_URL = "https://station-88.aicoming.top/v1"
MAX_RESULT_BYTES = 12 * 1024 * 1024
_BENCHMARK_PROXY_NETWORK = ipaddress.ip_network("198.18.0.0/15")


class StationGatewayError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class StationGateway:
    """Small, fixed-endpoint adapter for the team's station API."""

    def __init__(self, api_key: str, *, client: httpx.Client | None = None) -> None:
        if not api_key.strip():
            raise StationGatewayError("AI service API key is not configured", 503)
        self.api_key = api_key.strip()
        self.client = client or httpx.Client(timeout=httpx.Timeout(90.0, connect=15.0))
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def list_models(self) -> list[str]:
        data = self._request_json("GET", "/models")
        return [str(item.get("id") or "") for item in data.get("data", []) if isinstance(item, dict) and item.get("id")]

    def generate_image(self, payload: dict[str, Any]) -> list[dict[str, str]]:
        data = self._request_json("POST", "/images/generations", payload)
        images: list[dict[str, str]] = []
        for item in data.get("data", []):
            if not isinstance(item, dict):
                continue
            if item.get("url"):
                images.append({"url": str(item["url"])})
            elif item.get("b64_json"):
                images.append({"b64_json": str(item["b64_json"])})
        if not images:
            raise StationGatewayError("station returned no image result")
        return images

    def chat_stream(self, messages: list[dict[str, Any]], model: str) -> Iterator[str]:
        headers = self._headers()
        with self.client.stream(
            "POST",
            f"{STATION_BASE_URL}/chat/completions",
            headers=headers,
            json={"model": model, "messages": messages, "stream": True},
        ) as response:
            self._raise_for_status(response)
            for line in response.iter_lines():
                if line:
                    yield line

    def download_image(self, url: str) -> tuple[bytes, str]:
        # The local desktop network proxy maps external image CDNs into the
        # RFC 2544 benchmark range. This exception is deliberately scoped to
        # result URLs returned by the configured station, not user-provided URLs.
        _validate_public_https_url(url, allow_benchmark_proxy=True)
        try:
            with self.client.stream("GET", url, follow_redirects=False, timeout=30.0) as response:
                self._raise_for_status(response)
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type and not content_type.startswith("image/"):
                    raise StationGatewayError("generated result did not declare an image content type")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_RESULT_BYTES:
                        raise StationGatewayError("generated image exceeds the 12 MB local limit")
                    chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise StationGatewayError("failed to download generated image") from exc
        return b"".join(chunks), content_type

    def _request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = self.client.request(method, f"{STATION_BASE_URL}{path}", headers=self._headers(), json=payload)
        except httpx.HTTPError as exc:
            raise StationGatewayError("AI station is unavailable") from exc
        self._raise_for_status(response)
        try:
            data = response.json()
        except ValueError as exc:
            raise StationGatewayError("AI station returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise StationGatewayError("AI station returned invalid payload")
        return data

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "User-Agent": "MainPG-AiService/1.0"}

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return
        try:
            detail = response.read()[:240].decode("utf-8", errors="replace").replace("\n", " ")
        except httpx.HTTPError:
            detail = ""
        raise StationGatewayError(f"AI station HTTP {response.status_code}: {detail}", response.status_code)


def _validate_public_https_url(url: str, *, allow_benchmark_proxy: bool = False) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise StationGatewayError("generated image URL is not a safe HTTPS URL")
    try:
        addresses = {record[4][0] for record in socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise StationGatewayError("generated image host could not be resolved") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global and not (allow_benchmark_proxy and ip in _BENCHMARK_PROXY_NETWORK):
            raise StationGatewayError("generated image URL resolves to a private network")
