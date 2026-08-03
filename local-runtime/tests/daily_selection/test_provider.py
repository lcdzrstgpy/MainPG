from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any, Mapping

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.modules.daily_selection.criteria import DailySelectionCriteria  # noqa: E402
from wh_local.modules.daily_selection.provider import (  # noqa: E402
    HttpResponse,
    OneBound1688Provider,
)


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> Mapping[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeTransport:
    """In-memory deterministic transport; it never opens a network connection."""

    def __init__(self, responses: Mapping[str, object]) -> None:
        self.responses = dict(responses)
        self.requests: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float,
    ) -> HttpResponse:
        self.requests.append(
            {
                "method": method,
                "url": url,
                "params": dict(params or {}),
                "body": body,
                "headers": dict(headers or {}),
                "timeout": timeout,
            }
        )
        selected = self.responses[url]
        if isinstance(selected, BaseException):
            raise selected
        assert isinstance(selected, HttpResponse)
        return selected


def response(payload: Mapping[str, Any], status: int = 200) -> HttpResponse:
    return HttpResponse(status=status, body=json.dumps(payload).encode("utf-8"))


def provider(transport: FakeTransport, **overrides: Any) -> OneBound1688Provider:
    config = {
        "base_url": "https://onebound.test/1688",
        "api_key": "test-api-key",
        "api_secret": "test-api-secret",
        "timeout_seconds": 2.5,
        "enabled": True,
        "image_max_bytes": 64,
    }
    config.update(overrides)
    return OneBound1688Provider(config, transport=transport)


def endpoint(operation: str) -> str:
    return f"https://onebound.test/1688/{operation}/"


def assert_no_sensitive_values(value: Any) -> None:
    rendered = json.dumps(value, ensure_ascii=False, default=str)
    for secret in (
        "test-api-key",
        "test-api-secret",
        "upstream-token-must-not-escape",
        "upstream-secret-must-not-escape",
        "upstream-authorization-must-not-escape",
        "upstream-key-must-not-escape",
    ):
        assert secret not in rendered


def test_keyword_search_returns_sanitized_result_and_audit_record() -> None:
    transport = FakeTransport(
        {endpoint("item_search"): response(fixture("1688_keyword_search_success.json"))}
    )

    result = provider(transport).search_keyword(DailySelectionCriteria(keywords=["露营灯"]))

    assert result.ok is True
    assert result.response["data"]["items"][0]["num_iid"] == "offer-100"
    assert result.audit.provider == "onebound-1688"
    assert result.audit.operation == "item_search"
    assert result.audit.captured_at
    assert result.audit.response_summary["outcome"] == "success"
    assert transport.requests[0]["params"]["q"] == "露营灯"
    assert_no_sensitive_values({"response": result.response, "audit": result.audit})


def test_image_search_downloads_uploads_then_uses_imgid_without_retaining_bytes() -> None:
    image_url = "https://images.example.test/reference.jpg"
    image_bytes = b"small-image-content"
    transport = FakeTransport(
        {
            image_url: HttpResponse(status=200, body=image_bytes),
            endpoint("upload_img"): response(fixture("1688_image_upload_success.json")),
            endpoint("item_search_img"): response(fixture("1688_image_search_success.json")),
        }
    )
    criteria = DailySelectionCriteria(
        collection_mode="image",
        reference_image_url=image_url,
        keywords=["露营风"],
    )

    selected = provider(transport)
    result = selected.search_by_image(criteria)

    assert result.ok is True
    assert result.response["data"]["items"][0]["num_iid"] == "offer-200"
    assert [request["url"] for request in transport.requests] == [
        image_url,
        endpoint("upload_img"),
        endpoint("item_search_img"),
    ]
    upload_payload = json.loads(transport.requests[1]["body"].decode("utf-8"))
    assert upload_payload["img"] == base64.b64encode(image_bytes).decode("ascii")
    assert transport.requests[2]["params"]["imgid"] == "img-abc-123"
    assert result.audit.operation == "item_search_img"
    assert result.audit.response_summary["upload_outcome"] == "success"
    assert [audit.operation for audit in result.audits] == [
        "download_reference_image",
        "upload_img",
        "item_search_img",
    ]
    assert all(not isinstance(value, (bytes, bytearray)) for value in vars(selected).values())
    assert_no_sensitive_values({"response": result.response, "audit": result.audit})


def test_timeout_is_a_sanitized_error_with_audit_record() -> None:
    transport = FakeTransport({endpoint("item_search"): TimeoutError("test-api-key timed out")})

    result = provider(transport).search_keyword(DailySelectionCriteria(keywords=["帐篷"]))

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "timeout"
    assert result.audit.response_summary["outcome"] == "timeout"
    assert_no_sensitive_values({"error": result.error, "audit": result.audit})


def test_onebound_rate_limit_is_a_sanitized_error_with_audit_record() -> None:
    transport = FakeTransport(
        {endpoint("item_search"): response(fixture("1688_rate_limit.json"), status=429)}
    )

    result = provider(transport).search_keyword(DailySelectionCriteria(keywords=["帐篷"]))

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "rate_limited"
    assert result.audit.response_summary["outcome"] == "rate_limited"
    assert_no_sensitive_values({"response": result.response, "error": result.error, "audit": result.audit})


def test_empty_result_is_successful_no_results_and_item_detail_uses_1688_only() -> None:
    transport = FakeTransport(
        {
            endpoint("item_search"): response(fixture("1688_empty_result.json")),
            endpoint("item_get"): response(fixture("1688_item_get_success.json")),
        }
    )
    selected = provider(transport)

    empty = selected.search_keyword(DailySelectionCriteria(keywords=["不存在的商品"]))
    detail = selected.get_item_detail("offer-100")

    assert empty.ok is True
    assert empty.response["code"] == 2000
    assert empty.audit.response_summary["outcome"] == "no_results"
    assert detail.ok is True
    assert detail.response["data"]["title"] == "便携露营灯"
    assert all("taobao" not in request["url"] for request in transport.requests)


@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (401, {"code": 401, "msg": "authentication failed"}, "authentication_failed"),
        (400, {"code": 400, "msg": "invalid parameter q"}, "invalid_request"),
        (402, {"code": 402, "msg": "quota exhausted"}, "quota_exhausted"),
        (500, {"code": 500, "msg": "unexpected upstream failure"}, "upstream_failed"),
    ],
)
def test_known_and_unknown_upstream_errors_have_stable_safe_codes(
    status: int, payload: Mapping[str, Any], expected: str
) -> None:
    transport = FakeTransport({endpoint("item_search"): response(payload, status=status)})

    result = provider(transport).search_keyword(DailySelectionCriteria(keywords=["帐篷"]))

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == expected
    assert result.audit.response_summary["outcome"] == expected


def test_safe_summary_excludes_credentials_and_disabled_provider_does_not_call_transport() -> None:
    transport = FakeTransport({})
    selected = provider(transport, enabled=False)

    summary = selected.safe_summary()
    result = selected.search_keyword(DailySelectionCriteria(keywords=["帐篷"]))

    assert summary == {
        "provider": "onebound-1688",
        "platform": "1688",
        "base_url": "https://onebound.test/1688",
        "timeout_seconds": 2.5,
        "enabled": False,
        "image_max_bytes": 64,
    }
    assert result.error is not None
    assert result.error.code == "provider_disabled"
    assert transport.requests == []
    assert_no_sensitive_values({"summary": summary, "error": result.error, "audit": result.audit})


def test_image_download_rejects_payload_larger_than_configured_limit() -> None:
    image_url = "https://images.example.test/too-large.jpg"
    transport = FakeTransport({image_url: HttpResponse(status=200, body=b"x" * 65)})
    criteria = DailySelectionCriteria(collection_mode="image", reference_image_url=image_url)

    result = provider(transport).search_by_image(criteria)

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "image_too_large"
    assert len(transport.requests) == 1


def test_image_download_rejects_local_or_private_image_hosts_without_calling_transport() -> None:
    transport = FakeTransport({})
    criteria = DailySelectionCriteria(
        collection_mode="image", reference_image_url="http://127.0.0.1/private.jpg"
    )

    result = provider(transport).search_by_image(criteria)

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "invalid_request"
    assert transport.requests == []
