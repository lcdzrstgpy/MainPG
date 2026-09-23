"""POD-only AI runtime with a dedicated SuChuang transport lane.

Legacy pattern and scene features retain the established image-edit adapter.
The direct listing trial uses the same asynchronous SuChuang image protocol as
Product Processing, but keeps its own HTTP pool, permits, and executor.
"""

from __future__ import annotations

import base64
import hashlib
import inspect
import io
import time
from typing import Any, Callable

from PIL import Image, ImageDraw

from wh_local.modules.product_processing.domain.policy import is_safe_external_url
from wh_local.modules.product_processing.infrastructure.media import GeneratedMedia, MediaProcessingError
from wh_local.modules.product_processing.infrastructure.media import ProductImageProcessor
from wh_local.modules.product_processing.service import ProductProcessingService
from wh_local.data_collection.public_image_fetch import (
    FetchedPublicImage,
    PublicImageFetchError,
    fetch_public_image,
)

from .assets import MAX_IMAGE_BYTES, inspect_pod_image
from .billing_contract import PodExecutionGrant
from .billing_contract import PodBillingAuthorizationRequired
from .errors import PodProviderResultReceivedError, safe_error_message
from .runtime import AiRuntime, AiRuntimeConfig, RuntimeClosedError
from .runtime_contracts import (
    SUPPORTED_TEMPLATE_IMAGE_CONTENT_TYPES,
    DirectListingGridRequest,
    PatternGridRequest,
    SceneOptimizationRequest,
)


class PodCustomizationAiRuntime(AiRuntime):
    """Dedicated POD execution lane which reuses the tested image-edit chain."""

    def __init__(
        self,
        *,
        batch_workers: int = 1,
        image_workers: int = 4,
        vision_workers: int = 2,
        text_workers: int = 1,
        composite_workers: int = 4,
        requests_per_minute: float = 0.0,
        session: Any | None = None,
        poll_interval_seconds: float = 3.0,
        public_image_fetcher: Callable[..., FetchedPublicImage] | None = None,
        public_image_timeout_seconds: float = 30.0,
    ) -> None:
        # The named fields document the independent POD capacity budget.  Image
        # calls use the provider semaphore; other work stays in the POD executor.
        self.batch_workers = max(1, int(batch_workers))
        self.vision_workers = max(1, int(vision_workers))
        self.text_workers = max(1, int(text_workers))
        self.composite_workers = max(1, int(composite_workers))
        super().__init__(
            AiRuntimeConfig(
                name="pod-customization",
                executor_workers=max(1, int(image_workers)),
                pool_connections=max(4, int(image_workers)),
                pool_maxsize=max(4, int(image_workers) * 2),
                provider_concurrency=max(1, int(image_workers)),
                requests_per_minute=max(0.0, float(requests_per_minute)),
                user_agent="MainPG-PodCustomization/1.0",
            ),
            session=session,
        )
        # Reuse only the local splitter and existing COS publisher. Provider
        # generation is implemented above and always receives an explicit grant.
        self._media = ProductImageProcessor(
            config_provider=ProductProcessingService._media_config_provider,
        )
        # 速创直连不能继承桌面环境中的 HTTP(S)_PROXY；历史 AI 处理链路
        # 同样固定直连，避免代理在 TLS 握手阶段截断长时间图片请求。
        if hasattr(self.session, "trust_env"):
            self.session.trust_env = False
        self._poll_interval_seconds = max(0.0, float(poll_interval_seconds))
        self._public_image_fetcher = public_image_fetcher or fetch_public_image
        self._public_image_timeout_seconds = max(1.0, min(float(public_image_timeout_seconds), 60.0))

    def generate_pattern_grid(self, request: PatternGridRequest, *, grant: PodExecutionGrant, call_id: str) -> bytes:
        raise MediaProcessingError(
            "legacy POD pattern generation is disabled; use reference-locked listing generation",
            status_class="non_retryable_local",
        )

    def optimize_scene(self, request: SceneOptimizationRequest, *, grant: PodExecutionGrant, call_id: str) -> bytes:
        raise MediaProcessingError(
            "legacy POD scene optimization is disabled; regenerate the reference-locked style",
            status_class="non_retryable_local",
        )

    def generate_listing_grid(
        self,
        request: DirectListingGridRequest,
        *,
        grant: PodExecutionGrant,
        call_id: str,
        on_start: Callable[[], None] | None = None,
    ) -> GeneratedMedia:
        """Generate one reference-locked grid through POD's SuChuang client.

        This remains one provider request per service-level attempt.  The
        service decides whether a malformed grid receives its single retry.
        """
        _required_provider_key(grant, "wuyin")
        reference_url = self._publish_listing_reference(request)
        try:
            with self.provider_slot():
                _required_provider_key(grant, "wuyin")
                submit_kwargs = {"on_start": on_start} if on_start is not None else {}
                task_id = self._submit_suchuang_grid(grant, request, reference_url, **submit_kwargs)
                result_url = self._poll_suchuang_grid(grant, task_id)
                try:
                    content, content_type = self._download_suchuang_grid(result_url)
                except Exception as exc:
                    raise PodProviderResultReceivedError(
                        "wuyin",
                        f"速创已返回结果，但本地下载或解析失败：{safe_error_message(exc)}",
                    ) from exc
        except PodProviderResultReceivedError:
            raise
        except PodBillingAuthorizationRequired:
            raise
        except RuntimeClosedError:
            raise
        except MediaProcessingError:
            raise
        except Exception as exc:
            raise MediaProcessingError(
                f"速创图片服务暂时不可用：{exc.__class__.__name__}",
                attempt_count=1,
                status_class="transient",
            ) from exc
        return GeneratedMedia(
            stage="grid_image",
            content=content,
            content_type=content_type,
            suffix=_suffix_for_content_type(content_type),
            provider="suchuang",
            model=request.model_id,
            reference_count=1,
            attempt_count=1,
        )

    def _publish_listing_reference(self, request: DirectListingGridRequest) -> str:
        reference = GeneratedMedia(
            stage="pod_listing_reference",
            content=request.template_image,
            content_type=request.template_content_type,
            suffix=_suffix_for_content_type(request.template_content_type),
            provider="local-template",
            model="",
            reference_count=0,
        )
        url = self._media.upload_content_addressed_to_cos(
            reference,
            namespace=request.trial_id,
            collection="pod-direct-listing-reference",
            content_hash=hashlib.sha256(request.template_image).hexdigest(),
        )
        if not self._media.is_configured_cos_url(url, require_public=True):
            raise MediaProcessingError(
                "POD 参考图未能发布为速创可访问的公网地址",
                status_class="non_retryable_local",
            )
        return url

    def _submit_suchuang_grid(
        self,
        grant: PodExecutionGrant,
        request: DirectListingGridRequest,
        reference_url: str,
        *,
        on_start: Callable[[], None] | None = None,
    ) -> str:
        self.acquire_request_token()
        with self.connection_slot(timeout_seconds=30.0):
            image_key = _required_provider_key(grant, "wuyin")
            self._ensure_open()
            if on_start is not None:
                on_start()
            self._ensure_open()
            response = self.session.post(
                "https://api.wuyinkeji.com/api/async/image_gpt",
                params={"key": image_key},
                headers={"Authorization": image_key, "Content-Type": "application/json"},
                json={"prompt": request.prompt, "size": _suchuang_size(request.size), "urls": [reference_url]},
                timeout=30.0,
                allow_redirects=False,
            )
            try:
                if not bool(response.ok):
                    raise MediaProcessingError(
                        f"速创提交返回 HTTP {int(response.status_code)}",
                        status_code=int(response.status_code),
                        attempt_count=1,
                        status_class=_suchuang_status_class(int(response.status_code)),
                    )
                payload = response.json()
            finally:
                response.close()
        if not isinstance(payload, dict) or int(payload.get("code") or 0) != 200:
            raise MediaProcessingError(
                f"速创提交失败：{_suchuang_message(payload)}",
                attempt_count=1,
                status_class="non_retryable_4xx" if _suchuang_code_is_auth_or_request_error(payload) else "transient",
            )
        data = payload.get("data") or {}
        task_id = str(data.get("id") or data.get("task_id") or "").strip() if isinstance(data, dict) else ""
        if not task_id:
            raise MediaProcessingError("速创返回中没有生图任务 ID", attempt_count=1, status_class="transient")
        return task_id

    def _poll_suchuang_grid(self, grant: PodExecutionGrant, task_id: str) -> str:
        deadline = time.monotonic() + 600.0
        last_message = ""
        while time.monotonic() < deadline:
            if self._poll_interval_seconds:
                self.interruptible_wait(self._poll_interval_seconds)
            self.acquire_request_token()
            with self.connection_slot(timeout_seconds=30.0):
                image_key = _required_provider_key(grant, "wuyin")
                response = self.session.get(
                    "https://api.wuyinkeji.com/api/async/detail",
                    params={"key": image_key, "id": task_id},
                    headers={"Authorization": image_key},
                    timeout=30.0,
                    allow_redirects=False,
                )
                try:
                    if not bool(response.ok):
                        status = int(response.status_code)
                        if 400 <= status < 500:
                            raise MediaProcessingError(
                                f"速创轮询返回 HTTP {status}",
                                status_code=status,
                                attempt_count=1,
                                status_class="non_retryable_4xx",
                            )
                        last_message = f"detail HTTP {status}"
                        continue
                    payload = response.json()
                finally:
                    response.close()
            if not isinstance(payload, dict):
                last_message = "detail response is incompatible"
                continue
            code = int(payload.get("code") or 0)
            if code and code != 200:
                last_message = _suchuang_message(payload)
                if code in {400, 401, 403, 404}:
                    raise MediaProcessingError(
                        f"速创图片任务失败：{last_message}",
                        attempt_count=1,
                        status_class="non_retryable_4xx",
                    )
                continue
            data = payload.get("data") or {}
            result_url = _first_suchuang_image_url(data) or _first_suchuang_image_url(payload)
            if result_url:
                return result_url
            status_value = str(data.get("status") or payload.get("status") or "").strip().lower() if isinstance(data, dict) else ""
            message = _suchuang_message(payload)
            # 上游 status 为异步任务状态码（纯数字或文本）：≥0 为排队/准备/等待/处理中/发布，
            # 1<0 为失败，1/成功文本才表示完成。3/4/5 数字实为“处理中”，不能按文本失败集误判成失败。
            try:
                numeric_status = int(float(status_value)) if status_value else None
            except (TypeError, ValueError):
                numeric_status = None
            if numeric_status is not None:
                if numeric_status < 0:
                    raise MediaProcessingError(
                        f"速创图片任务失败：{message}",
                        attempt_count=1,
                        status_class="transient",
                    )
                last_message = message or f"status={status_value}"
                continue
            if status_value in {"success", "succeeded", "finish", "finished", "completed", "done"}:
                raise MediaProcessingError(
                    f"速创已完成但没有图片地址：{message}",
                    attempt_count=1,
                    status_class="transient",
                )
            if status_value in {"fail", "failed", "error", "cancelled", "canceled"}:
                raise MediaProcessingError(
                    f"速创图片任务失败：{message}",
                    attempt_count=1,
                    status_class="transient",
                )
            last_message = message or f"status={status_value or 'processing'}"
        raise MediaProcessingError(
            f"速创图片任务超时：{last_message}",
            attempt_count=1,
            status_class="transient",
        )

    def _download_suchuang_grid(self, result_url: str) -> tuple[bytes, str]:
        try:
            self._ensure_open()
            fetch_kwargs: dict[str, Any] = {
                "max_bytes": MAX_IMAGE_BYTES,
                "max_redirects": 0,
                "timeout_seconds": self._public_image_timeout_seconds,
            }
            if _accepts_keyword(self._public_image_fetcher, "shutdown_event"):
                fetch_kwargs["shutdown_event"] = self.shutdown_event
            fetched = self._public_image_fetcher(result_url, **fetch_kwargs)
            self._ensure_open()
            content = bytes(fetched.content)
            content_type, _suffix, _width, _height = inspect_pod_image(content)
        except (PublicImageFetchError, ValueError, OSError) as exc:
            raise MediaProcessingError(
                "provider result is not a safe valid image",
                attempt_count=1,
                status_class="transient",
            ) from exc
        return content, content_type

    def split_listing_grid(self, media):
        """Reuse the established local four-grid splitter; no AI call is made."""
        parts = self._media.split_four_grid(media)
        return [part for part in parts if part.stage.startswith("grid_image_") and part.stage != "grid_image_summary"]

    def publish_listing_image(self, media, *, namespace: str, role: str) -> str:
        url = self._media.upload_content_addressed_to_cos(
            media,
            namespace=namespace,
            collection=f"pod-direct-listing-{role}",
            content_hash=hashlib.sha256(media.content).hexdigest(),
        )
        if not self._media.is_configured_cos_url(url, require_public=True):
            raise RuntimeError("published POD listing image is not publicly accessible")
        return url

    def calibrate_template(self, _template_image: bytes) -> dict[str, object]:
        """Provide a safe editable first calibration for newly uploaded scenes.

        The initial release deliberately never mutates the source scene while
        guessing a garment boundary.  Operators can adjust this normalized
        draft in the Konva calibration canvas before submitting a batch.
        """
        return {
            "mask": {"x": 0.2, "y": 0.2, "width": 0.6, "height": 0.6},
            "anchor": {"x": 0.5, "y": 0.5},
        }

    def close(self, *, wait: bool = True, cancel_futures: bool = True) -> None:
        super().close(wait=wait, cancel_futures=cancel_futures)


def _grid_scaffold_data_url() -> str:
    image = Image.new("RGB", (1024, 1024), "#ffffff")
    drawing = ImageDraw.Draw(image)
    drawing.line((512, 0, 512, 1024), fill="#d9d9d9", width=3)
    drawing.line((0, 512, 1024, 512), fill="#d9d9d9", width=3)
    output = io.BytesIO()
    image.save(output, "PNG", optimize=True)
    return _image_data_url(output.getvalue())


def _image_data_url(content: bytes, content_type: str = "image/png") -> str:
    normalized_content_type = str(content_type or "").strip().lower()
    if normalized_content_type not in SUPPORTED_TEMPLATE_IMAGE_CONTENT_TYPES:
        raise ValueError("POD reference image must be JPEG, PNG, or WEBP")
    return f"data:{normalized_content_type};base64," + base64.b64encode(content).decode("ascii")


def _accepts_keyword(function: Callable[..., Any], name: str) -> bool:
    try:
        parameters = inspect.signature(function).parameters
    except (TypeError, ValueError):
        return False
    return name in parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


def _suchuang_size(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"1024x1024", "2048x2048", "4096x4096"}:
        return "1:1"
    if raw in {"auto", "1:1", "3:2", "2:3", "16:9", "9:16", "4:3", "3:4", "21:9", "9:21", "1:3", "3:1", "2:1", "1:2"}:
        return raw
    return "1:1"


def _suchuang_status_class(status_code: int) -> str:
    return "non_retryable_4xx" if 400 <= status_code < 500 else "transient"


def _suchuang_code_is_auth_or_request_error(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    return int(payload.get("code") or 0) in {400, 401, 403, 404}


def _first_suchuang_image_url(value: Any) -> str:
    if isinstance(value, str):
        candidate = value.strip()
        if is_safe_external_url(candidate):
            return candidate
        return ""
    if isinstance(value, dict):
        for key in ("url", "image_url", "image", "src", "href"):
            found = _first_suchuang_image_url(value.get(key))
            if found:
                return found
        for key in ("result", "results", "images", "urls", "output", "outputs"):
            found = _first_suchuang_image_url(value.get(key))
            if found:
                return found
    if isinstance(value, (list, tuple)):
        for item in value:
            found = _first_suchuang_image_url(item)
            if found:
                return found
    return ""


def _suchuang_message(payload: object) -> str:
    if not isinstance(payload, dict):
        return "响应格式不兼容"
    for key in ("msg", "message", "error", "reason"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return safe_error_message(value, fallback="未提供错误说明")[:180]
    return "未提供错误说明"


def _suffix_for_content_type(content_type: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(content_type, ".png")


def _required_provider_key(grant: PodExecutionGrant, provider: str) -> str:
    key = grant.provider_key(provider)
    if not key:
        raise PodBillingAuthorizationRequired(
            f"POD {provider} grant expired before the provider request started"
        )
    return key
