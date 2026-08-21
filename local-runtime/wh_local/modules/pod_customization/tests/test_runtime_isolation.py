from __future__ import annotations

import base64
from threading import Event

from wh_local.modules.pod_customization.ai_runtime import PodCustomizationAiRuntime, _image_data_url
from wh_local.modules.pod_customization.billing_contract import PodExecutionGrant
from wh_local.modules.pod_customization.runtime_contracts import DirectListingGridRequest


def _grant(**keys: str) -> PodExecutionGrant:
    return PodExecutionGrant("freeze-1", 1, "2099-01-01T00:00:00Z", keys)


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
    grid_image = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0c"
        b"IDAT\x08\xd7c\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x89\xc1\x00\x00\x00\x00IEND\xaeB`\x82"
    )

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


def test_template_reference_data_url_keeps_png_mime_type() -> None:
    assert _image_data_url(b"png-reference", "image/png") == (
        "data:image/png;base64," + base64.b64encode(b"png-reference").decode("ascii")
    )
