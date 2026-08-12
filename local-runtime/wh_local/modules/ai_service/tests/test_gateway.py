from __future__ import annotations

import httpx
import json

from wh_local.modules.ai_service.gateway import STATION_BASE_URL, StationGateway


def test_image_generation_uses_fixed_station_endpoint_and_never_serializes_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{STATION_BASE_URL}/images/generations"
        assert request.headers["authorization"] == "Bearer test-key"
        assert json.loads(request.content) == {"model": "gpt-image-2-1k", "prompt": "cup", "n": 1, "size": "1024x1024"}
        return httpx.Response(200, json={"data": [{"url": "https://example.invalid/result.png"}]})

    gateway = StationGateway("test-key", client=httpx.Client(transport=httpx.MockTransport(handler)))
    images = gateway.generate_image({"model": "gpt-image-2-1k", "prompt": "cup", "n": 1, "size": "1024x1024"})

    assert images == [{"url": "https://example.invalid/result.png"}]
