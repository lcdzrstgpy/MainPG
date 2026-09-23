from __future__ import annotations

import base64
import io
from threading import Event
from types import SimpleNamespace

import pytest
from PIL import Image

from wh_local.modules.pod_customization.ai_runtime import PodCustomizationAiRuntime, _image_data_url
from wh_local.modules.pod_customization.billing_contract import (
    PodBillingAuthorizationRequired,
    PodExecutionGrant,
)
from wh_local.modules.pod_customization.errors import PodProviderResultReceivedError
from wh_local.modules.pod_customization.runtime_contracts import DirectListingGridRequest
from wh_local.modules.product_processing.infrastructure.media import MediaProcessingError
from wh_local.data_collection.public_image_fetch import (
    PublicImageFetchError,
    PublicImageHttpResponse,
    fetch_public_image,
)


def _grant(**keys: str) -> PodExecutionGrant:
    return PodExecutionGrant("freeze-1", 1, "2099-01-01T00:00:00Z", keys)


def _tiny_png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (64, 64), "navy").save(output, "PNG")
    return output.getvalue()


def test_saturated_product_processing_pool_cannot_block_pod_pool() -> None:
    product_runtime = PodCustomizationAiRuntime(image_workers=1, requests_per_minute=0)
    pod_runtime = PodCustomizationAiRuntime(
        batch_workers=1,
        image_workers=1,
        vision_workers=1,
        text_workers=1,
        composite_workers=1,
        requests_per_minute=0,
    )
    product_started = Event()
    release_product = Event()

    def occupy_product_pool() -> str:
        product_started.set()
        release_product.wait(timeout=2)
        return "product-released"

    product_future = product_runtime.submit(occupy_product_pool)
    try:
        assert product_started.wait(timeout=1)
        assert pod_runtime.submit(lambda: "pod-ready").result(timeout=0.5) == "pod-ready"
    finally:
        release_product.set()
        product_future.result(timeout=1)
        pod_runtime.close()
        product_runtime.close()


def test_pod_suchuang_transport_bypasses_ambient_proxy_configuration() -> None:
    runtime = PodCustomizationAiRuntime(image_workers=1, requests_per_minute=0)
    try:
        assert runtime.session.trust_env is False
    finally:
        runtime.close()


def test_direct_listing_grid_uses_suchuang_async_protocol_and_explicit_grant() -> None:
    grid_buffer = io.BytesIO()
    Image.new("RGB", (32, 32), "#2563eb").save(grid_buffer, "PNG")
    grid_image = grid_buffer.getvalue()

    class Response:
        def __init__(self, *, payload=None, content=b"", content_type="application/json"):
            self.status_code = 200
            self._payload = payload
            self.content = content
            self.headers = {"Content-Type": content_type}

        @property
        def ok(self):
            return True

        def json(self):
            return self._payload

        def close(self):
            return None

    class SuchuangSession:
        def __init__(self):
            self.calls = []

        def post(self, url, **kwargs):
            self.calls.append(("POST", url, kwargs))
            assert url == "https://api.wuyinkeji.com/api/async/image_gpt"
            assert kwargs["params"] == {"key": "fresh-image-key"}
            assert kwargs["headers"] == {
                "Authorization": "fresh-image-key",
                "Content-Type": "application/json",
            }
            assert kwargs["json"] == {
                "prompt": "same shirt",
                "size": "1:1",
                "urls": ["https://bucket.cos.ap-guangzhou.myqcloud.com/pod/reference.jpg"],
            }
            return Response(payload={"code": 200, "data": {"id": "suchuang-task-1"}})

        def get(self, url, **kwargs):
            self.calls.append(("GET", url, kwargs))
            if url == "https://api.wuyinkeji.com/api/async/detail":
                assert kwargs["params"] == {"key": "fresh-image-key", "id": "suchuang-task-1"}
                assert kwargs["headers"] == {"Authorization": "fresh-image-key"}
                return Response(payload={"code": 200, "data": {"status": "success", "url": "https://1.1.1.1/grid.png"}})
            assert url == "https://1.1.1.1/grid.png"
            return Response(content=grid_image, content_type="image/png")

        def close(self):
            return None

    class ReferencePublisher:
        def generate(self, **_kwargs):
            raise AssertionError("direct POD listing must not use ProductImageProcessor edits")

        def upload_content_addressed_to_cos(self, media, *, namespace, collection, content_hash):
            assert media.content == b"reference-image"
            assert media.content_type == "image/jpeg"
            assert namespace == "trial-1"
            assert collection == "pod-direct-listing-reference"
            assert content_hash
            return "https://bucket.cos.ap-guangzhou.myqcloud.com/pod/reference.jpg"

        def is_configured_cos_url(self, url, *, require_public):
            return require_public and url.startswith("https://bucket.cos.")

    session = SuchuangSession()
    runtime = PodCustomizationAiRuntime(
        image_workers=1,
        requests_per_minute=0,
        session=session,
        poll_interval_seconds=0,
        public_image_fetcher=lambda *_args, **_kwargs: SimpleNamespace(
            content=grid_image, content_type="image/png"
        ),
    )
    runtime._media = ReferencePublisher()  # type: ignore[assignment]
    try:
        result = runtime.generate_listing_grid(
            DirectListingGridRequest(
                trial_id="trial-1",
                template_id="template-1",
                template_image=b"reference-image",
                template_content_type="image/jpeg",
                prompt="same shirt",
                attempt=1,
            ),
            grant=_grant(wuyin="fresh-image-key"),
            call_id="trial-1:image:1",
        )
        assert result.content == grid_image
        assert result.reference_count == 1
        assert result.attempt_count == 1
    finally:
        runtime.close()


def test_suchuang_status_five_with_success_message_keeps_polling_for_result_url() -> None:
    class Response:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        @property
        def ok(self):
            return True

        def json(self):
            return self._payload

        def close(self):
            return None

    class SuchuangSession:
        def __init__(self):
            self.responses = [
                Response({"code": 200, "msg": "成功", "data": {"status": "5"}}),
                Response(
                    {
                        "code": 200,
                        "data": {
                            "status": "5",
                            "url": "https://1.1.1.1/result.png",
                        },
                    }
                ),
            ]

        def get(self, _url, **_kwargs):
            return self.responses.pop(0)

        def close(self):
            return None

    runtime = PodCustomizationAiRuntime(
        image_workers=1,
        requests_per_minute=0,
        session=SuchuangSession(),
        poll_interval_seconds=0,
    )
    try:
        assert runtime._poll_suchuang_grid(
            _grant(wuyin="fresh-image-key"), "suchuang-task-1"
        ) == "https://1.1.1.1/result.png"
    finally:
        runtime.close()


def test_direct_listing_grid_fails_closed_without_wuyin_grant() -> None:
    runtime = PodCustomizationAiRuntime(image_workers=1, requests_per_minute=0)
    try:
        try:
            runtime.generate_listing_grid(
                DirectListingGridRequest(
                    trial_id="trial-1",
                    template_id="template-1",
                    template_image=b"reference-image",
                    template_content_type="image/jpeg",
                    prompt="same shirt",
                    attempt=1,
                ),
                grant=_grant(),
                call_id="trial-1:image:1",
            )
        except Exception as exc:
            assert "grant" in str(exc).lower()
        else:
            raise AssertionError("missing short-lived grant must fail closed")
    finally:
        runtime.close()


def test_direct_listing_grid_rejects_grant_without_provider_key_even_if_remote_token_exists() -> None:
    runtime = PodCustomizationAiRuntime(image_workers=1, requests_per_minute=0)
    try:
        with pytest.raises(Exception, match="grant"):
            runtime.generate_listing_grid(
                DirectListingGridRequest(
                    trial_id="trial-1",
                    template_id="template-1",
                    template_image=b"reference-image",
                    template_content_type="image/jpeg",
                    prompt="same shirt",
                    attempt=1,
                ),
                grant=PodExecutionGrant(
                    "freeze-1", 1, "2099-01-01T00:00:00Z", {}, remote_token="remote-token"
                ),
                call_id="trial-1:image:1",
            )
    finally:
        runtime.close()


def test_wuyin_result_url_receipt_survives_local_download_failure_and_redacts_detail() -> None:
    runtime = PodCustomizationAiRuntime(image_workers=1, requests_per_minute=0, poll_interval_seconds=0)
    runtime._publish_listing_reference = lambda _request: "https://cos.example.test/reference.png"  # type: ignore[method-assign]
    runtime._submit_suchuang_grid = lambda *_args: "task-1"  # type: ignore[method-assign]
    runtime._poll_suchuang_grid = lambda *_args: "https://provider.example.test/result?token=URL-SECRET"  # type: ignore[method-assign]

    def fail_download(_url: str):
        raise MediaProcessingError(
            "download failed Authorization: Bearer HEADER-SECRET api_key=KEY-SECRET",
            status_class="transient",
        )

    runtime._download_suchuang_grid = fail_download  # type: ignore[method-assign]
    try:
        try:
            runtime.generate_listing_grid(
                DirectListingGridRequest(
                    trial_id="trial-receipt",
                    template_id="template-1",
                    template_image=b"reference-image",
                    template_content_type="image/jpeg",
                    prompt="same shirt",
                    attempt=1,
                ),
                grant=_grant(wuyin="fresh-image-key"),
                call_id="trial-receipt:image:1",
            )
        except PodProviderResultReceivedError as exc:
            rendered = str(exc)
            assert exc.provider == "wuyin"
            assert "HEADER-SECRET" not in rendered
            assert "KEY-SECRET" not in rendered
            assert "provider.example" not in rendered
        else:
            raise AssertionError("local result download failure must preserve a typed provider receipt")
    finally:
        runtime.close()


def test_wuyin_grant_is_rechecked_after_waiting_for_provider_slot() -> None:
    class BlockingGate:
        def __init__(self) -> None:
            self.entered = Event()
            self.allow = Event()

        def acquire(self, *_args, **_kwargs) -> bool:
            self.entered.set()
            return self.allow.wait(timeout=1)

        def release(self) -> None:
            return None

    class ClockGrant:
        freeze_id = "freeze-clock"
        rule_version = 1
        expires_at = "fake-clock"

        def __init__(self) -> None:
            self.clock = 0

        def provider_key(self, provider: str) -> str:
            assert provider == "wuyin"
            return "short-lived-key" if self.clock < 1 else ""

    runtime = PodCustomizationAiRuntime(image_workers=1, requests_per_minute=0, poll_interval_seconds=0)
    grant = ClockGrant()
    reference_published = Event()
    provider_submitted = Event()
    runtime._publish_listing_reference = (  # type: ignore[method-assign]
        lambda _request: reference_published.set() or "https://cos.example.test/reference.png"
    )
    runtime._submit_suchuang_grid = (  # type: ignore[method-assign]
        lambda *_args: provider_submitted.set() or "task-should-not-run"
    )
    runtime._poll_suchuang_grid = lambda *_args: "https://1.1.1.1/result.png"  # type: ignore[method-assign]
    runtime._download_suchuang_grid = lambda _url: (b"image", "image/png")  # type: ignore[method-assign]
    gate = BlockingGate()
    runtime._providers = gate  # type: ignore[assignment]
    future = runtime.submit(
        runtime.generate_listing_grid,
        DirectListingGridRequest(
            trial_id="trial-expiring",
            template_id="template-1",
            template_image=b"reference-image",
            template_content_type="image/jpeg",
            prompt="same shirt",
            attempt=1,
        ),
        grant=grant,
        call_id="trial-expiring:image:1",
    )
    try:
        assert reference_published.wait(timeout=1)
        assert gate.entered.wait(timeout=1)
        grant.clock = 2
        gate.allow.set()
        with pytest.raises(PodBillingAuthorizationRequired, match="expired"):
            future.result(timeout=1)
        assert not provider_submitted.is_set()
    finally:
        gate.allow.set()
        runtime.close()


def test_pod_result_download_uses_pinned_bounded_fetcher_and_decodes_image() -> None:
    captured: dict[str, object] = {}

    def fetcher(url: str, **kwargs):
        captured.update({"url": url, **kwargs})
        return SimpleNamespace(content=_tiny_png(), media_type="image/png")

    runtime = PodCustomizationAiRuntime(
        image_workers=1,
        requests_per_minute=0,
        public_image_fetcher=fetcher,
    )
    try:
        content, content_type = runtime._download_suchuang_grid(
            "https://images.example.com/result.png"
        )
    finally:
        runtime.close()

    assert content == _tiny_png()
    assert content_type == "image/png"
    assert captured["max_bytes"] == 20 * 1024 * 1024
    assert captured["max_redirects"] == 0


def test_pod_result_download_rejects_invalid_image_after_safe_fetch() -> None:
    runtime = PodCustomizationAiRuntime(
        image_workers=1,
        requests_per_minute=0,
        public_image_fetcher=lambda *_args, **_kwargs: SimpleNamespace(
            content=b"not-an-image",
            media_type="image/png",
        ),
    )
    try:
        with pytest.raises(MediaProcessingError, match="valid image"):
            runtime._download_suchuang_grid("https://images.example.com/result.png")
    finally:
        runtime.close()


def test_shared_public_image_fetcher_rejects_oversize_and_dns_rebinding() -> None:
    public_url = "https://images.example.com/result.png"
    public_resolver = lambda _host, _port: ("93.184.216.34",)

    with pytest.raises(PublicImageFetchError, match="size limit"):
        fetch_public_image(
            public_url,
            max_bytes=20,
            resolver=public_resolver,
            transport=lambda *_args: PublicImageHttpResponse(
                status=200,
                headers={"Content-Type": "image/png"},
                content=b"x" * 21,
            ),
        )

    transport_called = Event()
    with pytest.raises(PublicImageFetchError):
        fetch_public_image(
            public_url,
            resolver=lambda _host, _port: ("93.184.216.34", "127.0.0.1"),
            transport=lambda *_args: transport_called.set(),
        )
    assert not transport_called.is_set()


def test_template_reference_data_url_keeps_png_mime_type() -> None:
    assert _image_data_url(b"png-reference", "image/png") == (
        "data:image/png;base64," + base64.b64encode(b"png-reference").decode("ascii")
    )
