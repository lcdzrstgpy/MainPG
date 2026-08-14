from __future__ import annotations

from typing import Any

import httpx

from wh_local.modules.ai_service.gateway import (
    STATION_REQUEST_TIMEOUT,
    STATION_STREAM_TIMEOUT,
    StationGateway,
)


def test_station_gateway_uses_long_bounded_request_timeout() -> None:
    gateway = StationGateway("test-key")
    try:
        timeout = gateway.client.timeout
        assert timeout.connect == 15.0
        assert timeout.read == 300.0
        assert timeout.write == 60.0
        assert timeout.pool == 30.0
        assert STATION_REQUEST_TIMEOUT.read == 300.0
    finally:
        gateway.close()


def test_station_chat_stream_uses_a_longer_bounded_idle_timeout() -> None:
    captured: dict[str, httpx.Timeout] = {}

    class Response:
        is_success = True

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: Any) -> bool:
            return False

        @staticmethod
        def iter_lines():
            return iter(["data: first", "", "data: second"])

    class Client:
        @staticmethod
        def stream(*_args: Any, timeout: httpx.Timeout, **_kwargs: Any) -> Response:
            captured["timeout"] = timeout
            return Response()

    gateway = StationGateway("test-key", client=Client())
    assert list(gateway.chat_stream([{"role": "user", "content": "hello"}], "gpt-5.6-terra")) == [
        "data: first",
        "data: second",
    ]
    assert captured["timeout"].connect == 15.0
    assert captured["timeout"].read == 600.0
    assert captured["timeout"].write == 60.0
    assert captured["timeout"].pool == 30.0
    assert STATION_STREAM_TIMEOUT.read == 600.0
