from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlencode

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
        resolved_address: str | None = None,
    ) -> HttpResponse:
        self.requests.append(
            {
                "method": method,
                "url": url,
                "params": dict(params or {}),
                "body": body,
                "headers": dict(headers or {}),
                "timeout": timeout,
                "resolved_address": resolved_address,
            }
        )
        selected = self.responses[url]
        if isinstance(selected, BaseException):
            raise selected
        assert isinstance(selected, HttpResponse)
        return selected


def response(payload: Mapping[str, Any], status: int = 200) -> HttpResponse:
    return HttpResponse(status=status, body=json.dumps(payload).encode("utf-8"))


class FakeResolver:
    def __init__(self, addresses: Mapping[str, tuple[str, ...]]) -> None:
        self.addresses = dict(addresses)
        self.calls: list[tuple[str, int]] = []

    def resolve(self, hostname: str, port: int) -> tuple[str, ...]:
        self.calls.append((hostname, port))
        return self.addresses.get(hostname, ("93.184.216.34",))


def provider(
    transport: FakeTransport, *, resolver: FakeResolver | None = None, **overrides: Any
) -> OneBound1688Provider:
    config = {
        "base_url": "https://onebound.test/1688",
        "api_key": "test-api-key",
        "api_secret": "test-api-secret",
        "timeout_seconds": 2.5,
        "enabled": True,
        "image_max_bytes": 64,
    }
    config.update(overrides)
    return OneBound1688Provider(config, transport=transport, resolver=resolver or FakeResolver({}))


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


def test_image_search_uploads_form_imgcode_then_uses_legacy_imgid_without_retaining_bytes() -> None:
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
    assert transport.requests[1]["method"] == "POST"
    assert transport.requests[1]["params"] == {
        "key": "test-api-key",
        "secret": "test-api-secret",
        "cache": "no",
    }
    assert transport.requests[1]["body"] == urlencode(
        {"imgcode": base64.b64encode(image_bytes).decode("ascii")}
    ).encode("utf-8")
    assert transport.requests[1]["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert transport.requests[2]["params"]["imgid"] == "img-abc-123"
    assert transport.requests[2]["params"]["cache"] == "no"
    assert result.audit.operation == "item_search_img"
    assert result.audit.response_summary["upload_outcome"] == "success"
    assert [audit.operation for audit in result.audits] == [
        "download_reference_image",
        "upload_img",
        "item_search_img",
    ]
    assert all(not isinstance(value, (bytes, bytearray)) for value in vars(selected).values())
    assert_no_sensitive_values({"response": result.response, "audit": result.audit})


def test_keyword_search_audit_counts_real_top_level_items_item_list() -> None:
    payload = {
        "code": 200,
        "msg": "success",
        "items": {
            "item": [
                {"num_iid": "offer-301", "title": "真实回包商品一"},
                {"num_iid": "offer-302", "title": "真实回包商品二"},
            ]
        },
    }
    transport = FakeTransport({endpoint("item_search"): response(payload)})

    result = provider(transport).search_keyword(DailySelectionCriteria(keywords=["帐篷"]))

    assert result.ok is True
    assert result.audit.response_summary["item_count"] == 2


@pytest.mark.parametrize(
    ("upload_items", "expected_image_id"),
    [
        ({"item": {"imgid": "img-object-456"}}, "img-object-456"),
        ({"item": [{"img_id": "img-list-456"}]}, "img-list-456"),
        ({"item": [{"result": {"url": "img-nested-456"}}]}, "img-nested-456"),
    ],
)
def test_image_search_uses_nested_real_upload_image_id(
    upload_items: Mapping[str, Any], expected_image_id: str
) -> None:
    image_url = "https://images.example.test/reference.jpg"
    image_bytes = b"small-image-content"
    transport = FakeTransport(
        {
            image_url: HttpResponse(status=200, body=image_bytes),
            endpoint("upload_img"): response({"code": 200, "items": upload_items}),
            endpoint("item_search_img"): response(
                {"code": 200, "items": {"item": [{"num_iid": "offer-456", "title": "真实图搜商品"}]}}
            ),
        }
    )
    criteria = DailySelectionCriteria(collection_mode="image", reference_image_url=image_url)

    result = provider(transport).search_by_image(criteria)

    assert result.ok is True
    assert transport.requests[1]["method"] == "POST"
    assert transport.requests[1]["params"] == {
        "key": "test-api-key",
        "secret": "test-api-secret",
        "cache": "no",
    }
    assert transport.requests[1]["body"] == urlencode(
        {"imgcode": base64.b64encode(image_bytes).decode("ascii")}
    ).encode("utf-8")
    assert transport.requests[1]["params"]["cache"] == "no"
    assert transport.requests[2]["params"]["imgid"] == expected_image_id
    assert transport.requests[2]["params"]["cache"] == "no"


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


def test_image_download_does_not_follow_a_redirect_to_an_untrusted_target() -> None:
    image_url = "https://images.example.test/redirect.jpg"
    transport = FakeTransport(
        {image_url: HttpResponse(status=302, body=b"", headers={"Location": "http://127.0.0.1/secret"})}
    )
    criteria = DailySelectionCriteria(collection_mode="image", reference_image_url=image_url)

    result = provider(transport).search_by_image(criteria)

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "upstream_failed"
    assert len(transport.requests) == 1


def test_provider_rejects_credential_bearing_base_url_before_safe_summary_can_leak_it() -> None:
    transport = FakeTransport({})

    with pytest.raises(ValueError, match="base_url"):
        provider(transport, base_url="https://user:secret@onebound.test/1688?token=unsafe")


def test_provider_requires_a_boolean_enabled_configuration() -> None:
    transport = FakeTransport({})

    with pytest.raises(ValueError, match="enabled"):
        provider(transport, enabled="false")


def test_non_success_http_status_cannot_be_reclassified_as_empty_result() -> None:
    transport = FakeTransport(
        {endpoint("item_search"): response(fixture("1688_empty_result.json"), status=500)}
    )

    result = provider(transport).search_keyword(DailySelectionCriteria(keywords=["帐篷"]))

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "upstream_failed"


def test_dns_resolved_private_image_host_is_rejected_before_transport() -> None:
    image_url = "https://images.example.test/reference.jpg"
    transport = FakeTransport({})
    resolver = FakeResolver({"images.example.test": ("10.0.0.7",)})
    criteria = DailySelectionCriteria(collection_mode="image", reference_image_url=image_url)

    result = provider(transport, resolver=resolver).search_by_image(criteria)

    assert result.error is not None
    assert result.error.code == "invalid_request"
    assert resolver.calls == [("images.example.test", 443)]
    assert transport.requests == []


def test_image_download_pins_the_checked_dns_address_for_transport() -> None:
    image_url = "https://images.example.test/reference.jpg"
    image_bytes = b"small-image-content"
    transport = FakeTransport(
        {
            image_url: HttpResponse(status=200, body=image_bytes),
            endpoint("upload_img"): response(fixture("1688_image_upload_success.json")),
            endpoint("item_search_img"): response(fixture("1688_image_search_success.json")),
        }
    )
    resolver = FakeResolver({"images.example.test": ("93.184.216.34",)})
    criteria = DailySelectionCriteria(collection_mode="image", reference_image_url=image_url)

    result = provider(transport, resolver=resolver).search_by_image(criteria)

    assert result.ok is True
    assert transport.requests[0]["resolved_address"] == "93.184.216.34"


@pytest.mark.parametrize("hostname", ["localhost.", "0177.0.0.1"])
def test_noncanonical_local_image_hosts_are_rejected_after_host_validation(hostname: str) -> None:
    image_url = f"http://{hostname}/private.jpg"
    transport = FakeTransport({})
    resolver = FakeResolver({hostname.rstrip("."): ("127.0.0.1",)})
    criteria = DailySelectionCriteria(collection_mode="image", reference_image_url=image_url)

    result = provider(transport, resolver=resolver).search_by_image(criteria)

    assert result.error is not None
    assert result.error.code == "invalid_request"
    assert transport.requests == []


def test_explicit_and_configured_sensitive_values_are_removed_without_redacting_ordinary_words() -> None:
    payload = {
        "code": 500,
        "request_id": "cookie-jar-tokenizer-sessional",
        "ordinary_note": "cookie jar tokenizer sessional secretary",
        "configured_values": "test-api-key and test-api-secret",
        "credentials": "api_key=must-not-escape; Authorization: Bearer token-value",
        "data": {"items": []},
    }
    transport = FakeTransport({endpoint("item_search"): response(payload, status=500)})

    result = provider(transport).search_keyword(DailySelectionCriteria(keywords=["帐篷"]))

    assert result.error is not None
    rendered = json.dumps({"response": result.response, "audit": result.audit, "error": result.error}, default=str)
    assert "cookie jar tokenizer sessional secretary" in rendered
    assert "cookie-jar-tokenizer-sessional" in rendered
    assert "test-api-key" not in rendered
    assert "test-api-secret" not in rendered
    assert "must-not-escape" not in rendered
    assert "token-value" not in rendered


def test_provider_rejects_sensitive_base_url_path_before_safe_summary() -> None:
    transport = FakeTransport({})

    with pytest.raises(ValueError, match="base_url"):
        provider(transport, base_url="https://onebound.test/1688/api_key=unsafe")


def test_provider_rejects_configured_credential_value_in_base_url_path() -> None:
    transport = FakeTransport({})

    with pytest.raises(ValueError, match="base_url"):
        provider(transport, base_url="https://onebound.test/1688/test-api-key")


def test_disabled_image_provider_stops_before_downloading_reference_image() -> None:
    image_url = "https://images.example.test/reference.jpg"
    transport = FakeTransport({})
    criteria = DailySelectionCriteria(collection_mode="image", reference_image_url=image_url)

    result = provider(transport, enabled=False).search_by_image(criteria)

    assert result.error is not None
    assert result.error.code == "provider_disabled"
    assert transport.requests == []
