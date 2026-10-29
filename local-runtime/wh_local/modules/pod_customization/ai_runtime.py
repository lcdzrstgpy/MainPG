"""POD-only AI runtime.

四个 POD 模块（全定制 / 半定制 / 爆款复刻 / 爆款裂变）共用同一套生图执行资源：
并发槽位、限流令牌、参考图发布、结果下载都集中在这里；平台各自的协议实现放在
``image_providers``（速创与豆包各一个文件），运行时按「模型 → 平台」自动选择。
"""

from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
from typing import Any, Callable

from wh_local.modules.product_processing.infrastructure.media import GeneratedMedia, MediaProcessingError
from wh_local.modules.product_processing.infrastructure.media import ProductImageProcessor
from wh_local.modules.product_processing.service import ProductProcessingService
from wh_local.media_enhance import EnhanceMetrics, options_from_env
from wh_local.data_collection.public_image_fetch import (
    FetchedPublicImage,
    PublicImageFetchError,
    fetch_public_image,
)

from ...runtime_logs import business_logger

from .assets import MAX_IMAGE_BYTES, inspect_pod_image
from .billing_contract import PodBillingAuthorizationRequired, PodExecutionGrant
from .errors import PodProviderResultReceivedError, safe_error_message
from .image_providers import (
    GridImageProvider,
    GridImageRequest,
    GridReference,
    POD_IMAGE_MODEL_DEFAULT,
    SUCHUANG_PLATFORM,
    SUCHUANG_RESULT_TIMEOUT_SECONDS,
    SuChuangImageProvider,
    build_grid_providers,
    grant_provider_for_model,
    image_data_url,
    normalize_free_model,
    normalize_model,
    provider_for_model,
    suffix_for_content_type,
)
from .runtime import AiRuntime, AiRuntimeConfig, RuntimeClosedError
from .runtime_contracts import DirectListingGridRequest, PatternGridRequest, SceneOptimizationRequest

# 兼容旧调用点：这个工具过去定义在本模块，实现已移到 image_providers.base。
_image_data_url = image_data_url


class PodCustomizationAiRuntime(AiRuntime):
    """Dedicated POD execution lane shared by the four POD modules."""

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
        result_timeout_seconds: float = SUCHUANG_RESULT_TIMEOUT_SECONDS,
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
        # generation is implemented in image_providers and always receives an
        # explicit grant key.
        self._media = ProductImageProcessor(
            config_provider=ProductProcessingService._media_config_provider,
        )
        # 速创/豆包直连不能继承桌面环境中的 HTTP(S)_PROXY；历史 AI 处理链路
        # 同样固定直连，避免代理在 TLS 握手阶段截断长时间图片请求。
        if hasattr(self.session, "trust_env"):
            self.session.trust_env = False
        self._poll_interval_seconds = max(0.0, float(poll_interval_seconds))
        self._result_timeout_seconds = max(0.0, float(result_timeout_seconds))
        self._public_image_fetcher = public_image_fetcher or fetch_public_image
        self._public_image_timeout_seconds = max(1.0, min(float(public_image_timeout_seconds), 60.0))
        # 平台实现随运行时构建一次，共用同一套槽位/限流/下载资源。
        self._grid_providers = build_grid_providers(
            result_timeout_seconds=self._result_timeout_seconds,
            poll_interval_seconds=self._poll_interval_seconds,
        )

    # ---- 四个 POD 模块共用的生图入口 ----

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
        """Generate one reference-locked grid through the selected platform.

        This remains one provider request per service-level attempt.  The
        service decides whether a malformed grid receives its single retry.
        """
        # 自由定制在批次上冻结 Flash/Pro，必须优先于全局模型；其它链路传空串。
        model = (
            normalize_free_model(request.model)
            if request.model
            else _resolve_pod_image_model()
        )
        provider = self._provider_for(model)
        _required_provider_key(grant, provider.grant_provider)
        references = self._grid_references(request)
        # 参考图准备（速创发布公网地址、豆包转 base64）刻意放在 provider 槽位之前：
        # 这是本地/网络开销，不该占用生图槽位；发布失败也必须在提交上游之前失败。
        prepared = provider.prepare(self, references, namespace=request.trial_id)
        grid_request = GridImageRequest(
            prompt=request.prompt,
            size=request.size,
            trial_id=request.trial_id,
        )
        try:
            with self.provider_slot():
                # 等槽位期间冻结的短期密钥可能已经过期，必须重新校验后再提交上游。
                api_key = _required_provider_key(grant, provider.grant_provider)
                result = provider.generate(
                    self,
                    grid_request,
                    prepared,
                    model=model,
                    api_key=api_key,
                    on_start=on_start,
                )
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
                f"{provider.name} 图片服务暂时不可用：{exc.__class__.__name__}",
                attempt_count=1,
                status_class="transient",
            ) from exc
        return GeneratedMedia(
            stage="grid_image",
            content=result.content,
            content_type=result.content_type,
            suffix=suffix_for_content_type(result.content_type),
            provider=provider.name,
            model=model,
            reference_count=len(references),
            attempt_count=1,
        )

    def _provider_for(self, model: str) -> GridImageProvider:
        return self._grid_providers.get(provider_for_model(model)) or self._grid_providers[
            SUCHUANG_PLATFORM
        ]

    @staticmethod
    def _grid_references(request: DirectListingGridRequest) -> tuple[GridReference, ...]:
        """复刻按传入顺序保留多张参考图；全定制沿用模板单图；半定制零参考图。

        ``anchor_fallback`` 为 True 时丢弃公网 URL、改走本地字节（派生轮回退路径）。
        """
        if request.reference_images:
            return tuple(
                GridReference(
                    content=image.content,
                    content_type=image.content_type,
                    url="" if request.anchor_fallback else image.url,
                )
                for image in request.reference_images
            )
        if request.template_image:
            return (
                GridReference(
                    content=request.template_image,
                    content_type=request.template_content_type,
                ),
            )
        return ()

    # ---- 平台实现可用的执行资源（GridProviderHost） ----

    def ensure_open(self) -> None:
        self._ensure_open()

    def publish_reference(self, content: bytes, content_type: str, *, namespace: str) -> str:
        """把本地参考图发布成上游可访问的公网地址（速创用）。"""
        reference = GeneratedMedia(
            stage="pod_listing_reference",
            content=content,
            content_type=content_type,
            suffix=suffix_for_content_type(content_type),
            provider="local-template",
            model="",
            reference_count=0,
        )
        url = self._media.upload_content_addressed_to_cos(
            reference,
            namespace=namespace,
            collection="pod-direct-listing-reference",
            content_hash=hashlib.sha256(content).hexdigest(),
        )
        if not self._media.is_configured_cos_url(url, require_public=True):
            raise MediaProcessingError(
                "POD 参考图未能发布为速创可访问的公网地址",
                status_class="non_retryable_local",
            )
        return url

    def download_result(self, result_url: str) -> tuple[bytes, str]:
        """下载并校验上游返回的图片地址（原始异常交给平台层包装成收据）。"""
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
        return content, content_type

    def split_listing_grid(self, media):
        """Reuse the established local four-grid splitter; no AI call is made.

        在既有的"裁边 → 放大到 800×800"之前加一层**清晰化**：上游母图实测 1254×1254，
        拆四格后每格原生仅约 627×627，插值放大会发软；这里默认只做轻度 USM 锐化，
        需要 ONNX 4× 超分时显式设 `WH_POD_IMAGE_ENHANCE_MODE=onnx`（超分峰值内存高，
        批量烧图容易把用户电脑的 CPU/内存打满）。`WH_POD_IMAGE_ENHANCE=0` 可一键关闭。
        """

        parts = self._media.split_four_grid(
            media,
            # 豆包 2K 母图拆格后每格约 1024px，已高于 800 交付尺寸：
            # 跳过 4× 超分，直接 LANCZOS 降采样 + USM，避免每格白跑 10s。
            enhance=options_from_env(upscale_when_larger=False),
            # POD 只用四格面板：汇总图要多解码 4 张 JPEG 再建 1600² 画布，纯浪费。
            include_summary=False,
            on_enhance_metrics=self._log_enhance_metrics,
        )
        return [part for part in parts if part.stage.startswith("grid_image_") and part.stage != "grid_image_summary"]

    @staticmethod
    def _log_enhance_metrics(metrics: EnhanceMetrics) -> None:
        """把本地清晰化（ONNX 超分 / USM 回退）单格的耗时与系统消耗写进 POD 日志。"""

        rss = f"{metrics.peak_rss_mb:.0f}MB" if metrics.peak_rss_mb is not None else "-"
        business_logger("pod_processing").info(
            "POD 本地清晰化 | 模式=%s | 输入=%dpx → 输出=%dpx | 墙钟=%.2fs | CPU=%.2fs | 进程峰值内存=%s",
            metrics.mode,
            metrics.input_edge,
            metrics.target_size,
            metrics.wall_seconds,
            metrics.cpu_seconds,
            rss,
        )

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

    # ---- 速创链路的兼容入口（实现已移到 image_providers/suchuang.py） ----

    def _poll_suchuang_grid(self, grant: PodExecutionGrant, task_id: str) -> str:
        provider = self._grid_providers[SUCHUANG_PLATFORM]
        if not isinstance(provider, SuChuangImageProvider):
            raise RuntimeError("POD 速创平台实现未注册")
        return provider.poll(
            self, task_id, resolve_key=lambda: _required_provider_key(grant, "wuyin")
        )

    def _download_suchuang_grid(self, result_url: str) -> tuple[bytes, str]:
        """旧签名：把本地下载/解析失败折叠成一个可重试的媒体错误。"""
        try:
            return self.download_result(result_url)
        except (PublicImageFetchError, ValueError, OSError) as exc:
            raise MediaProcessingError(
                "provider result is not a safe valid image",
                attempt_count=1,
                status_class="transient",
            ) from exc

    def close(self, *, wait: bool = True, cancel_futures: bool = True) -> None:
        super().close(wait=wait, cancel_futures=cancel_futures)


def _accepts_keyword(function: Callable[..., Any], name: str) -> bool:
    try:
        parameters = inspect.signature(function).parameters
    except (TypeError, ValueError):
        return False
    return name in parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


def _resolve_pod_image_model() -> str:
    """按调用读取 POD 生图模型配置；任何本地配置故障都安全回退到默认模型。"""
    try:
        from wh_local.modules.basic_settings.service import SystemConfigService
        from wh_local.modules.product_processing.provider_config import registered_system_config_db_path

        database_path = registered_system_config_db_path()
        if not database_path:
            return POD_IMAGE_MODEL_DEFAULT
        selection = SystemConfigService(Path(database_path)).get_pod_image_model()
        return normalize_model(selection.get("model"))
    except Exception:
        pass
    return POD_IMAGE_MODEL_DEFAULT


def resolve_pod_image_model() -> str:
    """公开入口：当前 POD 生图模型版本（用于随计划冻结，便于事后归因）。"""
    return _resolve_pod_image_model()


def pod_image_grant_provider(model: str = "") -> str:
    """POD 生图平台需要冻结下发的 provider key 名（速创=wuyin，豆包=ark）。

    ``model`` 非空时按批次冻结的模型判定；自由定制用它保证 Pro/Flash 都拿 ark。
    平台的密钥偏好只能由这里给出：把 wuyin 写死会让切到豆包后的批次卡在
    「授权不可用」并无限暂停。
    """
    selected = normalize_free_model(model) if model else _resolve_pod_image_model()
    return grant_provider_for_model(selected)


def _required_provider_key(grant: PodExecutionGrant, provider: str) -> str:
    key = grant.provider_key(provider)
    if not key:
        raise PodBillingAuthorizationRequired(
            f"POD {provider} grant expired before the provider request started"
        )
    return key
