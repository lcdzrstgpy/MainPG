from __future__ import annotations

import hashlib
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from wh_local.modules.pod_customization import ai_runtime as pod_ai_runtime
from wh_local.modules.pod_customization.ai_runtime import PodCustomizationAiRuntime
from wh_local.modules.pod_customization.billing_contract import PodExecutionGrant
from wh_local.modules.pod_customization.runtime_contracts import (
    DirectListingGridRequest,
    ListingReferenceImage,
)
from wh_local.modules.product_processing.infrastructure.media import MediaProcessingError

SOURCE = b"source-image"
TARGET = b"target-image"


def _grant(**keys: str) -> PodExecutionGrant:
    return PodExecutionGrant("freeze-1", 1, "2099-01-01T00:00:00Z", keys)


def _url(content: bytes) -> str:
    digest = hashlib.sha256(content).hexdigest()
    return f"https://bucket.cos.ap-guangzhou.myqcloud.com/pod/{digest[:8]}.jpg"


def _png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (64, 64), "#2563eb").save(output, "PNG")
    return output.getvalue()


def _replica_request(**kwargs) -> DirectListingGridRequest:
    defaults = dict(
        trial_id="replica-1",
        prompt="replica prompt",
        attempt=1,
        reference_images=(
            ListingReferenceImage(role="source", content=SOURCE, content_type="image/png"),
            ListingReferenceImage(role="target", content=TARGET, content_type="image/jpeg"),
        ),
    )
    defaults.update(kwargs)
    return DirectListingGridRequest(**defaults)


class Response:
    status_code = 200

    def __init__(self, *, payload=None, content=b"", content_type="application/json"):
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


class CapturingSession:
    def __init__(self, grid_image: bytes):
        self.grid_image = grid_image
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return Response(payload={"code": 200, "data": {"id": "suchuang-task-x"}})

    def get(self, url, **kwargs):
        if url == "https://api.wuyinkeji.com/api/async/detail":
            return Response(
                payload={"code": 200, "data": {"status": "success", "url": "https://1.1.1.1/grid.png"}}
            )
        return Response(content=self.grid_image, content_type="image/png")

    def close(self):
        return None


class RecordingPublisher:
    def __init__(self, *, fail_content: bytes | None = None):
        self.published = []
        self.fail_content = fail_content

    def upload_content_addressed_to_cos(self, media, *, namespace, collection, content_hash):
        self.published.append(media.content)
        assert collection == "pod-direct-listing-reference"
        if media.content == self.fail_content:
            raise MediaProcessingError(
                "POD 参考图未能发布为速创可访问的公网地址",
                status_class="non_retryable_local",
            )
        return _url(media.content)

    def is_configured_cos_url(self, url, *, require_public):
        return require_public and url.startswith("https://bucket.cos.")


def _runtime(monkeypatch, *, model: str, publisher):
    monkeypatch.setattr(pod_ai_runtime, "_resolve_pod_image_model", lambda: model)
    grid_image = _png_bytes()
    session = CapturingSession(grid_image)
    runtime = PodCustomizationAiRuntime(
        image_workers=1,
        requests_per_minute=0,
        session=session,
        poll_interval_seconds=0,
        public_image_fetcher=lambda *_args, **_kwargs: SimpleNamespace(
            content=grid_image, content_type="image/png"
        ),
    )
    runtime._media = publisher  # type: ignore[assignment]
    return runtime, session


def test_replica_normal_model_posts_two_urls_in_order_and_counts_references(monkeypatch) -> None:
    publisher = RecordingPublisher()
    runtime, session = _runtime(monkeypatch, model="image_gpt", publisher=publisher)
    try:
        result = runtime.generate_listing_grid(
            _replica_request(), grant=_grant(wuyin="fresh-image-key"), call_id="replica:image:1"
        )
    finally:
        runtime.close()

    url, kwargs = session.posts[0]
    assert url == "https://api.wuyinkeji.com/api/async/image_gpt"
    assert isinstance(kwargs["json"]["urls"], list)
    assert kwargs["json"]["urls"] == [_url(SOURCE), _url(TARGET)]
    assert [bytes(value) for value in publisher.published] == [SOURCE, TARGET]
    assert result.reference_count == 2


def test_replica_2_5_posts_comma_joined_string_in_order(monkeypatch) -> None:
    publisher = RecordingPublisher()
    runtime, session = _runtime(monkeypatch, model="image_gpt_2.5", publisher=publisher)
    try:
        result = runtime.generate_listing_grid(
            _replica_request(), grant=_grant(wuyin="fresh-image-key"), call_id="replica:image:1"
        )
    finally:
        runtime.close()

    url, kwargs = session.posts[0]
    assert url == "https://api.wuyinkeji.com/api/async/image_gpt_2.5"
    body = kwargs["json"]
    assert isinstance(body["urls"], str)
    assert body["urls"] == f"{_url(SOURCE)},{_url(TARGET)}"
    assert "size" not in body
    assert body["aspectRatio"] == "1024x1024"
    assert result.reference_count == 2


def test_replica_publish_failure_does_not_submit_generation(monkeypatch) -> None:
    publisher = RecordingPublisher(fail_content=TARGET)
    runtime, session = _runtime(monkeypatch, model="image_gpt", publisher=publisher)
    try:
        with pytest.raises(MediaProcessingError, match="公网地址"):
            runtime.generate_listing_grid(
                _replica_request(), grant=_grant(wuyin="fresh-image-key"), call_id="replica:image:1"
            )
    finally:
        runtime.close()

    # 先发布的样图已成功，但目标图发布失败，因此绝不能提交生图。
    assert [bytes(value) for value in publisher.published] == [SOURCE, TARGET]
    assert session.posts == []


def test_full_customization_keeps_single_template_reference_path(monkeypatch) -> None:
    publisher = RecordingPublisher()
    runtime, session = _runtime(monkeypatch, model="image_gpt", publisher=publisher)
    request = DirectListingGridRequest(
        trial_id="full-1",
        prompt="full prompt",
        attempt=1,
        template_id="template-1",
        template_image=SOURCE,
        template_content_type="image/png",
    )
    try:
        result = runtime.generate_listing_grid(
            request, grant=_grant(wuyin="fresh-image-key"), call_id="full:image:1"
        )
    finally:
        runtime.close()

    assert session.posts[0][1]["json"]["urls"] == [_url(SOURCE)]
    assert [bytes(value) for value in publisher.published] == [SOURCE]
    assert result.reference_count == 1


def test_semi_customization_keeps_zero_reference_path(monkeypatch) -> None:
    publisher = RecordingPublisher()
    runtime, session = _runtime(monkeypatch, model="image_gpt", publisher=publisher)
    request = DirectListingGridRequest(
        trial_id="semi-1",
        prompt="semi prompt",
        attempt=1,
    )
    try:
        result = runtime.generate_listing_grid(
            request, grant=_grant(wuyin="fresh-image-key"), call_id="semi:image:1"
        )
    finally:
        runtime.close()

    assert "urls" not in session.posts[0][1]["json"]
    assert publisher.published == []
    assert result.reference_count == 0