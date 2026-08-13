"""Reference-image processing adapters used by Product Processing tasks.

The adapter follows the original workbench's operational shape while remaining
module-local: primary/backup OpenAI-compatible image-edit providers, reference
images from the confirmed collection snapshot, optional COS publication, and no
credential values in task results.
"""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

import requests

from ..domain.policy import is_safe_external_url
from .grid_layout import build_grid_scaffold, extract_grid_panels, locate_split_guides

# 模块级连接池：复用 TCP/TLS 握手与 HTTP 连接，避免每个请求新建连接（图片生成/参考图下载高频调用）。
_SESSION = requests.Session()
_HTTP_ADAPTER = requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=32, max_retries=0)
_SESSION.mount("https://", _HTTP_ADAPTER)
_SESSION.mount("http://", _HTTP_ADAPTER)
_SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        )
    }
)

# 对齐原项目 native_product_engine.DXM_IMAGE_TARGET_SIZE = 800
# 店小秘导入要求图片不小于 800×800，拆分后每格缩放到该尺寸。
DXM_IMAGE_TARGET_SIZE = 800
# 四宫格由 2048px 拆为 1024px 象限后再缩至 800px；使用 94 + 4:4:4，
# 保留商品边缘、透明材质和本地排版细节，避免默认 4:2:0 二次损失。
DXM_IMAGE_JPEG_QUALITY = 94

# 图片 provider 轮巡游标（对齐原项目 native_product_engine._PROVIDER_CURSORS）：
# 进程内全局共享、线程锁保护，按"每次图片请求"取模轮转起始 provider，实现负载均衡。
_PROVIDER_CURSOR_LOCK = threading.Lock()
_PROVIDER_CURSORS: dict[str, int] = {}


class MediaConfigurationError(RuntimeError):
    pass


class MediaProcessingError(RuntimeError):
    """图片处理失败；``status_code`` 携带中转返回的 HTTP 状态码（429 时用于退避重试）。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        attempt_count: int = 0,
        status_class: str = "",
    ):
        super().__init__(message)
        self.status_code = status_code
        self.attempt_count = attempt_count
        self.status_class = status_class


@dataclass(frozen=True)
class GeneratedMedia:
    stage: str
    content: bytes
    content_type: str
    suffix: str
    provider: str
    model: str
    reference_count: int
    attempt_count: int = 1
    provider_status_class: str = "success"


class ProductImageProcessor:
    """A small OpenAI-compatible image-edit and COS adapter.

    ``config_provider`` is supplied by the application composition root and may
    contain decrypted credentials.  This class never returns those credentials,
    and exceptions deliberately avoid response bodies to prevent leakage.
    """

    def __init__(self, config_provider: Callable[[], dict[str, Any]] | None = None):
        self._config_provider = config_provider or (lambda: {})
        # 图片并发限流信号量（对齐原项目 global_image_request_limit / image_workers）：
        # 批次内多商品并行时限制同时在途的图片生成请求数，避免打爆中转 429。
        config = self._config()
        image_workers = max(1, min(int((config.get("limits") or {}).get("image_workers") or 15), 50))
        self._image_semaphore = threading.Semaphore(image_workers)

    def status(self) -> dict[str, Any]:
        config = self._config()
        providers = self._providers(config)
        cos = dict(config.get("cos") or {})
        return {
            "image_configured": bool(providers),
            "backup_image_configured": len(providers) > 1,
            "cos_configured": bool(
                cos.get("bucket") and cos.get("region") and cos.get("secret_id") and cos.get("secret_key")
            ),
            "image_provider_names": [item["name"] for item in providers],
        }

    def generate(
        self,
        *,
        stage: str,
        prompt: str,
        reference_values: Iterable[str],
        layout_scaffold: bool = False,
    ) -> GeneratedMedia:
        config = self._config()
        providers = self._providers(config)
        if not providers:
            raise MediaConfigurationError("image provider is not configured")
        with self._image_semaphore:
            return self._generate_with_limits(
                stage,
                prompt,
                reference_values,
                providers,
                config,
                layout_scaffold=layout_scaffold,
            )

    def _generate_with_limits(
        self,
        stage: str,
        prompt: str,
        reference_values: Iterable[str],
        providers: list[dict[str, str]],
        config: dict[str, Any],
        extra_references: list[tuple[bytes, str, str]] | None = None,
        layout_scaffold: bool = False,
    ) -> GeneratedMedia:
        default_limit = 1 if stage == "grid_image" else 2
        reference_limit = max(
            1,
            min(
                int((config.get("limits") or {}).get(f"{stage}_reference_max_count") or default_limit),
                4,
            ),
        )
        references = list(extra_references or [])
        references.extend(self._load_references(reference_values, limit=reference_limit))
        if not references:
            raise MediaProcessingError("a confirmed source image is required for image processing")
        ordinary_reference_count = len(references)
        if layout_scaffold:
            scaffold = build_grid_scaffold(references[0][0])
            references = [(scaffold, "fixed-four-grid-layout.png", "image/png"), *references]
        retries = max(1, min(int((config.get("limits") or {}).get("image_retry_attempts") or 3), 5))
        errors: list[str] = []
        attempt_count = 0
        grid_deadline = time.monotonic() + 150.0 if stage == "grid_image" else None
        max_total_attempts = min(retries, 2) if stage == "grid_image" else retries * max(1, len(providers))
        for provider in self._provider_order(providers, config):
            for attempt in range(1, retries + 1):
                if attempt_count >= max_total_attempts:
                    break
                request_timeout = 120.0
                if grid_deadline is not None:
                    remaining_seconds = grid_deadline - time.monotonic()
                    if remaining_seconds <= 1.0:
                        break
                    request_timeout = min(request_timeout, remaining_seconds)
                attempt_count += 1
                try:
                    content, content_type = self._request_edit(
                        provider,
                        prompt,
                        references,
                        timeout_seconds=request_timeout,
                    )
                    suffix = _suffix_for_content_type(content_type)
                    return GeneratedMedia(
                        stage=stage,
                        content=content,
                        content_type=content_type,
                        suffix=suffix,
                        provider=provider["name"],
                        model=provider["reference_model"] or provider["model"],
                        reference_count=ordinary_reference_count,
                        attempt_count=attempt_count,
                    )
                except (requests.RequestException, TimeoutError, ValueError, MediaProcessingError) as exc:
                    errors.append(f"{provider['name']} attempt {attempt}: {_safe_error(exc)}")
                    status_class = _retry_class(exc)
                    if status_class in {"non_retryable_4xx", "unknown_outcome_timeout", "non_retryable_local"}:
                        raise MediaProcessingError(
                            "; ".join(errors),
                            status_code=getattr(exc, "status_code", None),
                            attempt_count=attempt_count,
                            status_class=status_class,
                        ) from exc
                    if status_class == "rate_limited":
                        delay = min(2 ** attempt, 10)
                        if grid_deadline is not None and time.monotonic() + delay >= grid_deadline:
                            break
                        time.sleep(delay)
            if attempt_count >= max_total_attempts:
                break
        raise MediaProcessingError(
            "; ".join(errors) or "image provider request failed",
            attempt_count=attempt_count,
            status_class="retry_budget_exhausted",
        )

    def repair_generated(
        self,
        *,
        stage: str,
        prompt: str,
        prior_content: bytes,
        prior_content_type: str,
        reference_values: Iterable[str],
    ) -> GeneratedMedia:
        """定向重绘：把上一轮生成图作为第一参考回传给模型，仅修文字不换商品。

        OCR 质量门检出中文后调用（对齐原项目 AI 修复语义）：附加的上一轮生成图优先，
        再叠加最多 limit 张来源图保证商品身份一致。
        """
        config = self._config()
        providers = self._providers(config)
        if not providers:
            raise MediaConfigurationError("image provider is not configured")
        prior = (prior_content, "generated_previous.png", prior_content_type or "image/png")
        with self._image_semaphore:
            return self._generate_with_limits(
                stage,
                prompt,
                reference_values,
                providers,
                config,
                extra_references=[prior],
            )

    def split_four_grid(self, media: GeneratedMedia) -> list[GeneratedMedia]:
        """Split a generated 2x2 grid into four listing images plus its summary.

        The original workbench uses this contract for 店小秘 carousel images.
        Splitting happens locally so it does not consume four extra image API calls.

        对齐原项目 native_product_engine._split_4grid_to_jpegs / _square_image_to_jpeg_bytes：
        每格仅 trim 1% 边距后缩放到 DXM_IMAGE_TARGET_SIZE(800×800)，汇总图居中裁方后缩放到 800×800。
        """
        try:
            from PIL import Image  # type: ignore
        except ModuleNotFoundError as exc:
            raise MediaConfigurationError("Pillow is required for four-grid image processing") from exc
        try:
            self.validate_four_grid(media)
            source = Image.open(BytesIO(media.content)).convert("RGB")
            guides = locate_split_guides(source)
            panels = extract_grid_panels(source, guides)
        except Exception as exc:
            raise MediaProcessingError("generated four-grid image cannot be split") from exc

        target_size = DXM_IMAGE_TARGET_SIZE
        result: list[GeneratedMedia] = []
        for index, panel in enumerate(panels, start=1):
            resized = panel.resize((target_size, target_size), Image.Resampling.LANCZOS)
            content = _image_to_jpeg_bytes(resized)
            result.append(
                GeneratedMedia(
                    stage=f"grid_image_{index}",
                    content=content,
                    content_type="image/jpeg",
                    suffix=".jpg",
                    provider="local-split",
                    model="pillow",
                    reference_count=media.reference_count,
                )
            )
        # 汇总图：居中裁方 + 缩放到 800×800（对齐原项目 _square_image_to_jpeg_bytes）
        square = _center_crop_to_square(source)
        summary_resized = square.resize((target_size, target_size), Image.Resampling.LANCZOS)
        summary_content = _image_to_jpeg_bytes(summary_resized)
        result.append(
            GeneratedMedia(
                stage="grid_image_summary",
                content=summary_content,
                content_type="image/jpeg",
                suffix=".jpg",
                provider=media.provider,
                model=media.model,
                reference_count=media.reference_count,
            )
        )
        return result

    @staticmethod
    def validate_four_grid(media: GeneratedMedia) -> None:
        """Fail closed unless a 2K square has validated exact-center divider evidence."""
        try:
            from PIL import Image  # type: ignore

            source = Image.open(BytesIO(media.content)).convert("RGB")
            width, height = source.size
            if min(width, height) < 1800:
                raise ValueError("four-grid source must be at least 1800px on each edge")
            if abs(width - height) / max(width, height) > 0.02:
                raise ValueError("four-grid source must be square")
            locate_split_guides(source)
        except MediaConfigurationError:
            raise
        except Exception as exc:
            raise MediaProcessingError("generated four-grid structure failed validation") from exc

    def upload_to_cos(
        self,
        media: GeneratedMedia,
        *,
        task_id: int,
        draft_id: int,
    ) -> str:
        config = self._config()
        cos = dict(config.get("cos") or {})
        required = ("bucket", "region", "secret_id", "secret_key")
        if not all(str(cos.get(key) or "").strip() for key in required):
            raise MediaConfigurationError("COS is not configured")
        try:
            from qcloud_cos import CosConfig, CosS3Client  # type: ignore
        except ModuleNotFoundError as exc:
            raise MediaConfigurationError("COS SDK is not installed") from exc
        bucket = str(cos["bucket"]).strip()
        region = str(cos["region"]).strip()
        prefix = str((config.get("updates") or {}).get("cos_prefix") or "product-processing").strip("/")
        date_path = datetime.now(timezone.utc).strftime("%Y/%m/%d")
        key = "/".join(
            part for part in (
                prefix or "product-processing",
                f"task_{task_id}",
                f"draft_{draft_id}",
                date_path,
                f"{media.stage}_{uuid.uuid4().hex}{media.suffix}",
            ) if part
        )
        try:
            client = CosS3Client(
                CosConfig(
                    Region=region,
                    SecretId=str(cos["secret_id"]),
                    SecretKey=str(cos["secret_key"]),
                    Timeout=60,
                )
            )
            client.put_object(Bucket=bucket, Key=key, Body=media.content, ContentType=media.content_type)
        except Exception as exc:
            raise MediaProcessingError(f"COS upload failed: {_safe_error(exc)}") from exc
        return f"https://{bucket}.cos.{region}.myqcloud.com/{key}"

    def upload_content_addressed_to_cos(
        self,
        media: GeneratedMedia,
        *,
        namespace: str,
        content_hash: str,
        collection: str = "dimension-canvas",
    ) -> str:
        """Publish one immutable image under a deterministic COS key.

        The caller supplies a stable collection and workspace namespace so repeated
        submit/recovery checks the same object instead of creating UUID-keyed
        duplicates. A timeout with unknown outcome is reconciled with ``HEAD`` before
        it is reported as failed.
        """
        digest = hashlib.sha256(media.content).hexdigest()
        expected = str(content_hash or "").strip().lower()
        if expected and expected != digest:
            raise MediaProcessingError("dimension media content hash mismatch")
        config = self._config()
        cos = dict(config.get("cos") or {})
        required = ("bucket", "region", "secret_id", "secret_key")
        if not all(str(cos.get(key) or "").strip() for key in required):
            raise MediaConfigurationError("COS is not configured")
        try:
            from qcloud_cos import CosConfig, CosS3Client  # type: ignore
        except ModuleNotFoundError as exc:
            raise MediaConfigurationError("COS SDK is not installed") from exc
        bucket = str(cos["bucket"]).strip()
        region = str(cos["region"]).strip()
        prefix = str(
            (config.get("updates") or {}).get("cos_prefix")
            or (config.get("limits") or {}).get("cos_prefix")
            or "product-processing"
        ).strip("/")
        safe_namespace = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(namespace or "dimension")).strip("-")[:48]
        safe_collection = re.sub(
            r"[^a-zA-Z0-9_-]+", "-", str(collection or "preview-final")
        ).strip("-")[:48]
        suffix = media.suffix if media.suffix in {".jpg", ".jpeg", ".png", ".webp"} else ".jpg"
        key = "/".join(
            part
            for part in (
                prefix or "product-processing",
                safe_collection or "preview-final",
                safe_namespace or "workspace",
                digest[:2],
                f"{digest}{suffix}",
            )
            if part
        )
        client = CosS3Client(
            CosConfig(
                Region=region,
                SecretId=str(cos["secret_id"]),
                SecretKey=str(cos["secret_key"]),
                Timeout=60,
            )
        )

        def exists() -> bool:
            try:
                response = client.head_object(Bucket=bucket, Key=key) or {}
                metadata_hash = str(
                    response.get("x-cos-meta-sha256")
                    or response.get("X-Cos-Meta-Sha256")
                    or (response.get("Metadata") or {}).get("sha256")
                    or ""
                ).strip().lower()
                length = str(
                    response.get("Content-Length")
                    or response.get("content-length")
                    or ""
                ).strip()
                media_type = str(
                    response.get("Content-Type")
                    or response.get("content-type")
                    or ""
                ).split(";", 1)[0].strip().lower()
                return (
                    metadata_hash == digest
                    and (not length or length == str(len(media.content)))
                    and (not media_type or media_type == media.content_type.lower())
                )
            except Exception as exc:
                status_getter = getattr(exc, "get_status_code", None)
                status = status_getter() if callable(status_getter) else getattr(exc, "status_code", None)
                if str(status or "") == "404":
                    return False
                raise MediaProcessingError(f"COS object check failed: {_safe_error(exc)}") from exc

        if not exists():
            try:
                client.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=media.content,
                    ContentType=media.content_type,
                    Metadata={"x-cos-meta-sha256": digest},
                )
            except Exception as exc:
                try:
                    if not exists():
                        raise MediaProcessingError(f"COS upload failed: {_safe_error(exc)}") from exc
                except MediaProcessingError:
                    raise
            if not exists():
                raise MediaProcessingError("COS object verification failed after upload")
        return f"https://{bucket}.cos.{region}.myqcloud.com/{key}"

    def is_configured_cos_url(self, url: str, *, require_public: bool = False) -> bool:
        """Return true only for an existing object in this configured COS bucket.

        Merely looking like a public URL is insufficient: legacy URLs are reused by
        the preview finalizer only after the configured bucket and object key have
        both been verified server-side.
        """
        value = str(url or "").strip()
        parsed = urlsplit(value)
        config = self._config()
        cos = dict(config.get("cos") or {})
        bucket = str(cos.get("bucket") or "").strip()
        region = str(cos.get("region") or "").strip()
        secret_id = str(cos.get("secret_id") or "").strip()
        secret_key = str(cos.get("secret_key") or "").strip()
        expected_host = f"{bucket}.cos.{region}.myqcloud.com".lower()
        object_key = parsed.path.lstrip("/")
        if (
            parsed.scheme.lower() != "https"
            or str(parsed.hostname or "").lower() != expected_host
            or not object_key
            or not all((bucket, region, secret_id, secret_key))
        ):
            return False
        try:
            from qcloud_cos import CosConfig, CosS3Client  # type: ignore
        except ModuleNotFoundError as exc:
            raise MediaConfigurationError("COS SDK is not installed") from exc
        client = CosS3Client(
            CosConfig(
                Region=region,
                SecretId=secret_id,
                SecretKey=secret_key,
                Timeout=60,
            )
        )
        try:
            client.head_object(Bucket=bucket, Key=object_key)
        except Exception as exc:
            status_getter = getattr(exc, "get_status_code", None)
            status = status_getter() if callable(status_getter) else getattr(exc, "status_code", None)
            if str(status or "") == "404":
                return False
            raise MediaProcessingError(f"COS object check failed: {_safe_error(exc)}") from exc
        if not require_public:
            return True
        # Dianxiaomi fetches without our COS credentials. Verify that the canonical
        # URL itself is anonymously readable and reject redirects to avoid changing
        # the trusted host after validation.
        try:
            response = _SESSION.head(value, allow_redirects=False, timeout=(3, 8))
        except requests.RequestException as exc:
            raise MediaProcessingError(f"COS public access check failed: {_safe_error(exc)}") from exc
        if not 200 <= response.status_code < 300:
            return False
        media_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        return not media_type or media_type in {"image/jpeg", "image/png", "image/webp"}

    def _config(self) -> dict[str, Any]:
        value = self._config_provider()
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _providers(config: dict[str, Any]) -> list[dict[str, str]]:
        """构造 provider 池：每个图片模型一个条目。

        同一中转的多个图片模型（``image_models`` 池）各自成条，轮巡时在池内取模；
        未配置模型池时退化为单模型条目。primary/backup 两段可各自携带模型池。
        """
        providers: list[dict[str, str]] = []
        for name, section_name in (("primary", "image"), ("backup", "backup_image")):
            section = dict(config.get(section_name) or {})
            base_url = str(section.get("base_url") or "").strip().rstrip("/")
            api_key = str(section.get("api_key") or "").strip()
            if not (base_url and api_key):
                continue
            parsed = urlsplit(base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            models = [
                str(model).strip()
                for model in (section.get("image_models") or ())
                if isinstance(model, str) and str(model).strip()
            ]
            if not models:
                single = str(section.get("model") or "").strip()
                if single:
                    models = [single]
            reference_model = str(section.get("reference_model") or "").strip()
            image_size = _normalized_image_size(section.get("image_size"))
            for model in models:
                providers.append(
                    {
                        "name": f"{name}:{model}",
                        "base_url": base_url,
                        "api_key": api_key,
                        "model": model,
                        "reference_model": reference_model or model,
                        "image_size": image_size,
                    }
                )
        return providers

    @staticmethod
    def _provider_order(providers: list[dict[str, str]], config: dict[str, Any]) -> list[dict[str, str]]:
        """按策略决定本轮 provider 起始顺序。

        balanced / round_robin / load_balance：进程内游标取模轮转起始 provider（对齐原项目）；
        backup_first：backup 段整体前移（failover 偏好）；其余保持配置顺序。
        """
        strategy = str((config.get("limits") or {}).get("image_provider_strategy") or "balanced").strip().lower().replace("-", "_")
        if strategy in {"balanced", "round_robin", "load_balance"} and len(providers) > 1:
            key = "|".join(f"{item['base_url']}:{item['model']}" for item in providers)
            with _PROVIDER_CURSOR_LOCK:
                index = _PROVIDER_CURSORS.get(key, 0) % len(providers)
                _PROVIDER_CURSORS[key] = index + 1
            return providers[index:] + providers[:index]
        if strategy == "backup_first" and len(providers) > 1:
            return [*providers[1:], providers[0]]
        return providers

    def _load_references(self, values: Iterable[str], *, limit: int) -> list[tuple[bytes, str, str]]:
        references: list[tuple[bytes, str, str]] = []
        seen: set[str] = set()
        errors: list[str] = []
        for raw in values:
            if len(references) >= limit:
                break
            value = str(raw or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            if value.startswith("data:image/"):
                try:
                    header, payload = value.split(",", 1)
                    content = base64.b64decode(payload)
                except Exception as exc:
                    errors.append(f"data image invalid: {_safe_error(exc)}")
                    continue
                references.append((content, "reference.png", _data_url_content_type(header)))
            elif Path(value).is_file():
                path = Path(value)
                references.append((path.read_bytes(), path.name, mimetypes.guess_type(path.name)[0] or "image/jpeg"))
            else:
                if not is_safe_external_url(value):
                    errors.append("reference URL is not a safe public HTTP(S) URL")
                    continue
                try:
                    content, content_type = _download_reference_image(value)
                except requests.RequestException as exc:
                    # 1688 来源图偶发防盗链（cbu01.alicdn.com 420）：跳过失败 URL，继续尝试后续来源图
                    errors.append(f"download failed: {_safe_error(exc)}")
                    continue
                if not content or not content_type.startswith("image/"):
                    errors.append("reference URL did not return an image")
                    continue
                references.append((content, _filename_for_url(value), content_type))
        if not references:
            detail = f" ({errors[0]})" if errors else ""
            raise MediaProcessingError(f"reference image download failed{detail}")
        return references

    @staticmethod
    def _request_edit(
        provider: dict[str, str],
        prompt: str,
        references: list[tuple[bytes, str, str]],
        *,
        timeout_seconds: float = 120.0,
    ) -> tuple[bytes, str]:
        files: Any
        if len(references) == 1:
            content, filename, content_type = references[0]
            files = {"image": (filename, BytesIO(content), content_type)}
        else:
            files = [
                ("image[]", (filename, BytesIO(content), content_type))
                for content, filename, content_type in references
            ]
        response = _SESSION.post(
            f"{provider['base_url']}/images/edits",
            headers={"Authorization": f"Bearer {provider['api_key']}"},
            data={
                "model": provider["reference_model"] or provider["model"],
                "prompt": prompt,
                "n": "1",
                "size": _normalized_image_size(provider.get("image_size")),
            },
            files=files,
            timeout=max(1.0, min(float(timeout_seconds), 120.0)),
        )
        try:
            if not response.ok:
                raise MediaProcessingError(
                    f"provider returned HTTP {response.status_code}",
                    status_code=response.status_code,
                )
            payload = response.json()
        finally:
            response.close()
        item = ((payload.get("data") or [{}])[0] if isinstance(payload, dict) else {})
        if not isinstance(item, dict):
            raise MediaProcessingError("provider response is incompatible")
        if item.get("b64_json"):
            try:
                return base64.b64decode(str(item["b64_json"])), "image/png"
            except Exception as exc:
                raise MediaProcessingError("provider returned invalid image data") from exc
        url = str(item.get("url") or "").strip()
        if not url or not is_safe_external_url(url):
            raise MediaProcessingError("provider response does not contain a safe image result")
        downloaded = _SESSION.get(url, timeout=60, allow_redirects=False)
        try:
            downloaded.raise_for_status()
            content = bytes(downloaded.content)
            content_type = str(downloaded.headers.get("Content-Type") or "image/jpeg").split(";", 1)[0]
        finally:
            downloaded.close()
        if not content or not content_type.startswith("image/"):
            raise MediaProcessingError("provider result is not an image")
        return content, content_type


def _safe_error(error: BaseException) -> str:
    message = str(error).replace("\n", " ").strip()
    return message[:180] or error.__class__.__name__


def _retry_class(error: BaseException) -> str:
    status = getattr(error, "status_code", None)
    if status in {400, 401, 403, 404}:
        return "non_retryable_4xx"
    if status == 429:
        return "rate_limited"
    if status is not None and 500 <= status < 600:
        return "server_error"
    if isinstance(error, (requests.Timeout, TimeoutError)):
        return "unknown_outcome_timeout"
    if isinstance(error, requests.ConnectionError):
        return "connection_error"
    return "non_retryable_local"


def _normalized_image_size(value: Any) -> str:
    """Return a provider-safe square image size; product grids default to 2K for readable split panels."""
    normalized = str(value or "").strip().lower()
    if normalized in {"1024x1024", "2048x2048", "4096x4096"}:
        return normalized
    return "2048x2048"


def _has_centered_uniform_dividers(source: Any) -> bool:
    """Require deterministic 50/50 separator evidence before destructive splitting.

    A continuous poster can be text-free yet still look plausible at 2K; blindly cutting it
    produces the exact broken panels reported by users.  The generation contract therefore
    requires two neutral light-gray separators.  We inspect only the narrow center bands and
    fail closed when either band is textured, dark, strongly colored, or discontinuous.
    """
    try:
        from PIL import ImageStat  # type: ignore

        width, height = source.size
        half_band_x = max(2, int(width * 0.002))
        half_band_y = max(2, int(height * 0.002))
        vertical = source.crop((width // 2 - half_band_x, 0, width // 2 + half_band_x, height))
        horizontal = source.crop((0, height // 2 - half_band_y, width, height // 2 + half_band_y))

        def uniform_neutral_light(band: Any) -> bool:
            sample = band.resize((max(4, band.width), 256)) if band.height > band.width else band.resize((256, max(4, band.height)))
            stats = ImageStat.Stat(sample.convert("RGB"))
            means = [float(value) for value in stats.mean]
            deviations = [float(value) for value in stats.stddev]
            return (
                min(means) >= 165
                and max(means) - min(means) <= 28
                and max(deviations) <= 18
            )

        return uniform_neutral_light(vertical) and uniform_neutral_light(horizontal)
    except Exception:
        return False


def _center_crop_to_square(image: Any) -> Any:
    """居中裁切成正方形（对齐原项目 _square_image_to_jpeg_bytes）。"""
    width, height = image.size
    side = min(width, height)
    left = max((width - side) // 2, 0)
    top = max((height - side) // 2, 0)
    return image.crop((left, top, left + side, top + side))


def _image_to_jpeg_bytes(image: Any) -> bytes:
    """PIL Image → high-detail JPEG bytes for 800px marketplace images."""
    buffer = BytesIO()
    image.save(
        buffer,
        format="JPEG",
        quality=DXM_IMAGE_JPEG_QUALITY,
        subsampling=0,
        optimize=True,
    )
    return buffer.getvalue()


def _suffix_for_content_type(content_type: str) -> str:
    suffix = mimetypes.guess_extension(content_type or "") or ".png"
    return suffix if suffix in {".png", ".jpg", ".jpeg", ".webp"} else ".png"


def _data_url_content_type(header: str) -> str:
    value = header.removeprefix("data:").split(";", 1)[0]
    return value if value.startswith("image/") else "image/png"


def _filename_for_url(value: str) -> str:
    name = Path(urlsplit(value).path).name
    return name or "reference.jpg"


def _download_reference_image(url: str) -> tuple[bytes, str]:
    """下载来源参考图，带 UA 头并做防盗链容错。

    1688 来源图（cbu01.alicdn.com 等）偶发 420 防盗链：裸 requests.get 无 UA 易被拦。
    策略：1) 带浏览器 UA 直下；2) 失败追加 ``?__r__=<毫秒时间戳>`` 缓存爆破参数重试；
    3) 仍失败换 ``http`` 再试一次。全部失败抛 requests.RequestException 由调用方转错误。
    """
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
    variants = [url]
    parsed = urlsplit(url)
    if "__r__" not in parsed.query:
        ts = str(int(time.time() * 1000))
        separator = "&" if parsed.query else "?"
        variants.append(f"{url}{separator}__r__={ts}")
    if parsed.scheme == "https":
        variants.append(f"http://{parsed.netloc}{parsed.path}" + (f"?{parsed.query}" if parsed.query else ""))
    last_error: requests.RequestException | None = None
    for variant in variants:
        response = None
        try:
            response = _SESSION.get(variant, timeout=30, allow_redirects=False, headers=headers)
            response.raise_for_status()
            content = bytes(response.content)
            content_type = str(response.headers.get("Content-Type") or "image/jpeg").split(";", 1)[0]
            if content and content_type.startswith("image/"):
                return content, content_type
        except requests.RequestException as exc:
            last_error = exc
        finally:
            if response is not None:
                response.close()
    raise last_error or requests.RequestException("reference image download failed")
