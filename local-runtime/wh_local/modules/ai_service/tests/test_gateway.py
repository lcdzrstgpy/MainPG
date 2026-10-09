from __future__ import annotations

import httpx
import json
import pytest

from wh_local.modules.ai_service import gateway as gateway_module
from wh_local.modules.ai_service.gateway import STATION_BASE_URL, StationGateway, StationGatewayError, _validate_public_https_url


def test_image_generation_uses_fixed_station_endpoint_and_never_serializes_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{STATION_BASE_URL}/images/generations"
        assert request.headers["authorization"] == "Bearer test-key"
        assert json.loads(request.content) == {"model": "gpt-image-2-1k", "prompt": "cup", "n": 1, "size": "1024x1024"}
        return httpx.Response(200, json={"data": [{"url": "https://example.invalid/result.png"}]})

    gateway = StationGateway("test-key", client=httpx.Client(transport=httpx.MockTransport(handler)))
    images = gateway.generate_image({"model": "gpt-image-2-1k", "prompt": "cup", "n": 1, "size": "1024x1024"})

    assert images == [{"url": "https://example.invalid/result.png"}]


def test_station_result_download_allows_only_the_local_proxy_benchmark_range(monkeypatch) -> None:
    monkeypatch.setattr(
        gateway_module.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("198.18.0.152", 443))],
    )

    with pytest.raises(StationGatewayError):
        _validate_public_https_url("https://image-cdn.example/result.png")
    _validate_public_https_url("https://image-cdn.example/result.png", allow_benchmark_proxy=True)

    monkeypatch.setattr(
        gateway_module.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("10.0.0.8", 443))],
    )
    with pytest.raises(StationGatewayError):
        _validate_public_https_url("https://image-cdn.example/result.png", allow_benchmark_proxy=True)
