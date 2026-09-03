"""Reference-image processing adapters used by Product Processing tasks.

The adapter follows the original workbench's operational shape while remaining
module-local: primary/backup OpenAI-compatible image-edit providers, reference
images from the confirmed collection snapshot, optional COS publication, and no
credential values in task results.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import mimetypes
import re
import socket
import ssl
import threading
import time
import uuid
from collections import OrderedDict, deque
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator
from urllib.parse import urlsplit

import requests

from ..domain.policy import is_safe_external_url, resolve_safe_external_url
from ..server_ai_proxy import gateway_base_url, remote_token, usage_id
from ...ai_service.temporary_cos import (
    TemporaryCosStore,
    TemporaryReference,
    TemporaryReferenceError,
)
from ...basic_settings.service import RuntimeCosConfig
from .grid_layout import (
    GridLayoutError,
    build_grid_scaffold,
    center_split_guides,
    extract_grid_panels,
    locate_split_guides,
)
from .rate_limit import global_ai_request_limiter

# 模块级连接池：复用 TCP/TLS 握手与 HTTP 连接，避免每个请求新建连接（图片生成/参考图下载高频调用）。
_SESSION = requests.Session()
_SESSION.trust_env = False
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
# 拆分后每格向内裁掉的比例：避开模型实际画出的、比 scaffold 更宽的白线。
# 裁掉后统一缩放到 DXM_IMAGE_TARGET_SIZE(800×800)，不足 800 时自动放大。
FOUR_GRID_EDGE_INSET_FRACTION = 0.04
# Some OpenAI-compatible gateways accept a 4K model/size request but return a
# 2048px square transport image.  That is still sufficient for four native
# 1024px carousel panels, so validate the usable result instead of trusting the
# requested size.  Sources below 1800px remain too small for quality splitting.
PREMIUM_GRID_MIN_SIZE = 1800
PREMIUM_IMAGE_JPEG_QUALITY = 95

# 2K 图像编辑的真实生成时间常超过 120 秒。请求和整条生成流程分别保留充足预算，
# 同时用总预算限制失败后的重试，避免批处理无限占住图片并发槽。
IMAGE_EDIT_REQUEST_TIMEOUT_SECONDS = 600.0
IMAGE_GENERATION_TOTAL_TIMEOUT_SECONDS = 660.0
PROVIDER_TRANSIENT_FAILURE_THRESHOLD = 2
PROVIDER_TRANSIENT_COOLDOWN_SECONDS = 45.0
WUYIN_IMAGE_SUBMIT_PATH = "/api/async/image_gpt"
WUYIN_IMAGE_DETAIL_PATH = "/api/async/detail"
WUYIN_IMAGE_POLL_INTERVAL_SECONDS = 3.0
MAX_PROVIDER_RESULT_BYTES = 32 * 1024 * 1024
MAX_PROVIDER_JSON_BYTES = 8 * 1024 * 1024
REFERENCE_DOWNLOAD_ATTEMPTS = 3
PROVIDER_RESULT_DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_RETRY_BACKOFF_SECONDS = (0.2, 0.6)

# 图片 provider 轮巡游标（对齐原项目 native_product_engine._PROVIDER_CURSORS）：
# 进程内全局共享、线程锁保护，按"每次图片请求"取模轮转起始 provider，实现负载均衡。
_PROVIDER_CURSOR_LOCK = threading.Lock()
_PROVIDER_CURSORS: dict[str, int] = {}

class _FairUsageRequestGate:
    """Serialize provider calls while rotating fairly between billable items.

    One product may enqueue several slot-repair requests at once. A plain
    semaphore can let those repairs reacquire the only provider slot before a
    different product gets its first image. Queues are therefore grouped by
    ``usage_id`` and each group receives at most one turn before moving to the
    back of the rotation.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._waiters_by_key: dict[str, deque[threading.Event]] = {}
        self._ready_keys: deque[str] = deque()
        self._ready_key_set: set[str] = set()
        self._active_key: str | None = None

    @staticmethod
    def _key(value: str) -> str:
        return str(value or "").strip() or "__anonymous__"

    def acquire(self, key: str) -> None:
        normalized = self._key(key)
        waiter = threading.Event()
        with self._lock:
            queue = self._waiters_by_key.setdefault(normalized, deque())
            queue.append(waiter)
            # Further calls belonging to the active product wait for its next
            # round instead of immediately competing with other products.
            if self._active_key != normalized and normalized not in self._ready_key_set:
                self._ready_keys.append(normalized)
                self._ready_key_set.add(normalized)
            self._dispatch_locked()
        waiter.wait()

    def release(self, key: str) -> None:
        normalized = self._key(key)
        with self._lock:
            if self._active_key != normalized:
                raise RuntimeError("fair image gate released by a non-owner")
            self._active_key = None
            # The just-served product goes behind every product that arrived
            # while its request was in flight.
            if self._waiters_by_key.get(normalized) and normalized not in self._ready_key_set:
                self._ready_keys.append(normalized)
                self._ready_key_set.add(normalized)
            self._dispatch_locked()

    @contextmanager
    def hold(self, key: str) -> Iterator[None]:
        self.acquire(key)
        try:
            yield
        finally:
            self.release(key)

    def _dispatch_locked(self) -> None:
        if self._active_key is not None:
            return
        while self._ready_keys:
            key = self._ready_keys.popleft()
            self._ready_key_set.discard(key)
            queue = self._waiters_by_key.get(key)
            if not queue:
                self._waiters_by_key.pop(key, None)
                continue
            waiter = queue.popleft()
            if not queue:
                self._waiters_by_key.pop(key, None)
            self._active_key = key
            waiter.set()
            return


# 服务器托管图片仍保持全局单并发，但按商品 usage_id 轮转。这样不放大
# 上游 image_gpt 压力，同时避免一个商品的多张修复图连续占满闸口。
_SERVER_MANAGED_IMAGE_GATE = _FairUsageRequestGate()


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


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to one validated IP while authenticating the original hostname."""

    def __init__(self, hostname: str, pinned_address: str, port: int, timeout: float):
        super().__init__(
            hostname,
            port=port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
        self._pinned_address = pinned_address

    def connect(self) -> None:
        sock = socket.create_connection(
            (self._pinned_address, self.port),
            self.timeout,
            self.source_address,
        )
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """Plain HTTP variant that still avoids a second DNS lookup."""

    def __init__(self, hostname: str, pinned_address: str, port: int, timeout: float):
        super().__init__(hostname, port=port, timeout=timeout)
        self._pinned_address = pinned_address

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._pinned_address, self.port),
            self.timeout,
            self.source_address,
        )


def _download_pinned_public_image(
    url: str,
    *,
    timeout_seconds: float = 20.0,
) -> tuple[bytes, str]:
    """Resolve once, pin the connection, reject redirects, and bound the body."""

    try:
        resolved = resolve_safe_external_url(url)
    except ValueError as exc:
        raise MediaProcessingError("provider result URL is not a safe public URL") from exc
    if resolved is None:
        raise MediaProcessingError("provider result URL is not a safe public URL")
    parsed = urlsplit(resolved.url)
    connection_type = _PinnedHTTPSConnection if parsed.scheme == "https" else _PinnedHTTPConnection
    connection = connection_type(
        resolved.hostname,
        resolved.addresses[0],
        resolved.port,
        max(1.0, float(timeout_seconds)),
    )
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    try:
        connection.request(
            "GET",
            path,
            headers={"Host": resolved.hostname, "Accept": "image/*"},
        )
        response = connection.getresponse()
        if 300 <= int(response.status) < 400:
            raise MediaProcessingError("provider result redirected the request")
        if not 200 <= int(response.status) < 300:
            raise MediaProcessingError(
                "provider result download failed",
                status_code=int(response.status),
            )
        content = bytes(response.read(MAX_PROVIDER_RESULT_BYTES + 1))
        if len(content) > MAX_PROVIDER_RESULT_BYTES:
            raise MediaProcessingError("provider result exceeded the download limit")
        content_type = str(response.getheader("Content-Type", "image/jpeg") or "image/jpeg").split(";", 1)[0]
        if not content or not content_type.startswith("image/"):
            raise MediaProcessingError("provider result is not an image")
        return content, content_type
    except MediaProcessingError:
        raise
    except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
        raise MediaProcessingError("provider result download is temporarily unavailable") from exc
    finally:
        connection.close()


def _bounded_response_json(response: requests.Response) -> Any:
    """Decode one streamed JSON response without buffering an unbounded body."""

    chunks: list[bytes] = []
    total = 0
    iterator = getattr(response, "iter_content", None)
    if not callable(iterator):
        raise MediaProcessingError("provider returned an invalid response")
    try:
        for chunk in iterator(chunk_size=64 * 1024):
            if not chunk:
                continue
            raw = bytes(chunk)
            total += len(raw)
            if total > MAX_PROVIDER_JSON_BYTES:
                raise MediaProcessingError("provider response exceeded the JSON limit")
            chunks.append(raw)
        return json.loads(b"".join(chunks).decode("utf-8"))
    except MediaProcessingError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise MediaProcessingError("provider returned invalid JSON") from exc


def _gateway_image_status_error(status_code: int) -> MediaProcessingError:
    mapping = {
        402: ("server image gateway requires sufficient points", "billing_payment_required"),
        403: ("server image gateway permission denied", "billing_forbidden"),
        409: ("server image gateway request is still in progress", "gateway_in_progress"),
        502: ("server image gateway returned an invalid response", "gateway_bad_response"),
        503: ("server image gateway is temporarily unavailable", "gateway_unavailable"),
    }
    message, status_class = mapping.get(
        status_code,
        (
            "server image gateway rejected the request",
            "server_error" if status_code >= 500 else "non_retryable_4xx",
        ),
    )
    return MediaProcessingError(
        message,
        status_code=status_code,
        status_class=status_class,
    )


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
        image_workers = max(1, min(int((config.get("limits") or {}).get("image_workers") or 4), 50))
        self._image_semaphore = threading.Semaphore(image_workers)
        self._reference_cache_limit = max(
            1,
            min(int((config.get("limits") or {}).get("reference_download_cache_entries") or 64), 256),
        )
        self._reference_cache: OrderedDict[str, tuple[bytes, str]] = OrderedDict()
        self._reference_downloads: dict[str, threading.Event] = {}
        self._reference_cache_lock = threading.Lock()
        self._provider_health_lock = threading.Lock()
        self._provider_transient_failures: dict[str, int] = {}
        self._provider_cooldown_until: dict[str, float] = {}

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

    def prime_references(self, reference_values: Iterable[str], stage: str = "grid_image") -> int:
        """Warm validated reference bytes through the existing bounded single-flight cache.

        This performs no provider/model call.  It deliberately shares the same
        stage-specific reference limit and URL safety/download path as ``generate``.
        """
        config = self._config()
        default_limit = 1 if stage == "grid_image" else 2
        reference_limit = max(
            1,
            min(
                int((config.get("limits") or {}).get(f"{stage}_reference_max_count") or default_limit),
                4,
            ),
        )
        return len(self._load_references(reference_values, limit=reference_limit))

    def generate(
        self,
        *,
        stage: str,
        prompt: str,
        reference_values: Iterable[str],
        layout_scaffold: bool = False,
        image_size: str | None = None,
        model_override: str | None = None,
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
                image_size=image_size,
                model_override=model_override,
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
        image_size: str | None = None,
        model_override: str | None = None,
    ) -> GeneratedMedia:
        default_limit = 1 if stage == "grid_image" else 2
        reference_limit = max(
            1,
            min(
                int((config.get("limits") or {}).get(f"{stage}_reference_max_count") or default_limit),
                4,
            ),
        )
        # 统一先把参考图 URL 归一化：Temu 等平台保存的 imageView2 缩略图地址
        # 只有 180px/AVIF，上游生图服务无法使用；提升为高清 JPEG 版本后再转发/下载。
        reference_values = [
            _normalize_reference_url(str(raw or "").strip())
            for raw in reference_values
            if _normalize_reference_url(str(raw or "").strip())
        ]
        server_managed_only = bool(providers) and all(
            _is_server_managed_wuyin_provider(provider) for provider in providers
        )
        if server_managed_only:
            # 服务器统一走中转：本地不下载参考图，只做结构校验后把 URL 交给网关，
            # 由网关自行下载。避免本地代理 TUN fake-ip 下 DNS/下载不稳定导致失败。
            url_references = [
                (b"", "reference.png", "image/jpeg", str(raw).strip())
                for raw in reference_values
                if _plausible_public_http_url(str(raw or "").strip())
            ][:reference_limit]
            if not url_references:
                raise MediaProcessingError("a confirmed source image URL is required for image processing")
            references = url_references
        else:
            # 直连提供方只能接收可公网访问的参考图 URL（它需要自己下载图片），
            # 本地缓存文件路径无法传递给提供方。采集链路保存的本地文件不带 URL，
            # 若直接取本地路径会导致提交给提供方的 urls=[]，图生图任务静默失败
            # （提供方返回 status=3 且无图无原因）。因此直连模式把远端 URL 排到
            # 前面，保证 _load_references 取到的前 N 个参考都带 URL（元组第 4 项）。
            direct_values = [
                *[value for value in reference_values if _plausible_public_http_url(value)],
                *[value for value in reference_values if not _plausible_public_http_url(value)],
            ]
            references = self._load_references(direct_values, limit=reference_limit)
            if not references:
                raise MediaProcessingError("a confirmed source image is required for image processing")
        ordinary_reference_count = len(references)
        references.extend(extra_references or [])
        if layout_scaffold and not server_managed_only:
            scaffold = build_grid_scaffold(references[0][0])
            references = [*references, (scaffold, "fixed-four-grid-layout.png", "image/png")]
        retries = max(1, min(int((config.get("limits") or {}).get("image_retry_attempts") or 3), 5))
        errors: list[str] = []
        attempt_count = 0
        generation_deadline = time.monotonic() + IMAGE_GENERATION_TOTAL_TIMEOUT_SECONDS
        # 四宫格单次生成成本高：串行闸下一条一条发，失败最多重试一次即放行下一条商品
        # （provider 轮巡叠加时按 provider 数放行）。
        max_total_attempts = min(retries, 2) if stage == "grid_image" else retries * max(1, len(providers))
        for provider in self._provider_order(providers, config):
            for attempt in range(1, retries + 1):
                if attempt_count >= max_total_attempts:
                    break
                remaining_seconds = generation_deadline - time.monotonic()
                if remaining_seconds <= 1.0:
                    break
                request_timeout = min(IMAGE_EDIT_REQUEST_TIMEOUT_SECONDS, remaining_seconds)
                attempt_count += 1
                try:
                    content, content_type = self._request_edit(
                        provider,
                        prompt,
                        references,
                        timeout_seconds=request_timeout,
                        image_size=image_size,
                        reference_model=model_override,
                    )
                    self._record_provider_success(provider)
                    suffix = _suffix_for_content_type(content_type)
                    return GeneratedMedia(
                        stage=stage,
                        content=content,
                        content_type=content_type,
                        suffix=suffix,
                        provider=provider["name"],
                        model=model_override or provider["reference_model"] or provider["model"],
                        reference_count=ordinary_reference_count,
                        attempt_count=attempt_count,
                    )
                except (requests.RequestException, TimeoutError, ValueError, MediaProcessingError) as exc:
                    errors.append(f"{provider['name']} attempt {attempt}: {_safe_error(exc)}")
                    status_class = _retry_class(exc)
                    if status_class in {
                        "rate_limited",
                        "server_error",
                        "unknown_outcome_timeout",
                        "connection_error",
                    }:
                        self._record_provider_transient_failure(provider)
                    if status_class in {"non_retryable_4xx", "unknown_outcome_timeout", "non_retryable_local"}:
                        raise MediaProcessingError(
                            "; ".join(errors),
                            status_code=getattr(exc, "status_code", None),
                            attempt_count=attempt_count,
                            status_class=status_class,
                        ) from exc
                    if status_class == "rate_limited":
                        delay = min(2 ** attempt, 10)
                        if time.monotonic() + delay >= generation_deadline:
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
        image_size: str | None = None,
        model: str | None = None,
    ) -> GeneratedMedia:
        """定向重绘：原始商品图保持第一参考，上一轮生成图仅用于指示待修内容。

        OCR 质量门检出中文后调用：先传最多 limit 张来源图锁定商品身份，
        再附加上一轮生成图指示需要修复的画面。
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
                image_size=image_size,
                model_override=model,
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
            # 自适应：非正方形时居中裁方，保证 2x2 拆分几何正确。
            source = _center_crop_to_square(source)
            try:
                guides = locate_split_guides(source)
            except GridLayoutError:
                # 无精确分隔线证据时回退正中切分；面板独立性校验仍由
                # extract_grid_panels 强制执行，明显跨区内容不会通过。
                guides = center_split_guides(source)
            panels = extract_grid_panels(source, guides)
        except Exception as exc:
            detail = str(exc).strip()
            message = "generated four-grid image cannot be split"
            if detail:
                message = f"{message}: {detail}"
            raise MediaProcessingError(message) from exc

        target_size = DXM_IMAGE_TARGET_SIZE
        result: list[GeneratedMedia] = []
        for index, panel in enumerate(panels, start=1):
            # 裁掉拆分后边缘残留的白色分隔线（模型实际画的分隔线常宽于 scaffold）。
            panel = _inset_grid_panel(panel)
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
        result.append(self.compose_grid_summary(result))
        return result

    def split_premium_four_grid(self, media: GeneratedMedia) -> list[GeneratedMedia]:
        """Split one 2K-or-4K square grid into four native-resolution panels."""
        try:
            from PIL import Image  # type: ignore

            source = Image.open(BytesIO(media.content)).convert("RGB")
            width, height = source.size
            if min(width, height) < PREMIUM_GRID_MIN_SIZE:
                raise ValueError("premium four-grid source must be at least 1800px on each edge")
            if abs(width - height) / max(width, height) > 0.02:
                raise ValueError("premium four-grid source must be square")
            # The 2x2 scaffold fixes panel geometry before generation.  Always cut
            # the returned square at exactly 50/50; a model-painted divider is only
            # decoration and must never trigger another paid 4K request.
            center_x = width // 2
            center_y = height // 2
            boxes = (
                (0, 0, center_x, center_y),
                (center_x, 0, width, center_y),
                (0, center_y, center_x, height),
                (center_x, center_y, width, height),
            )
            panels = [source.crop(box) for box in boxes]
            if any(min(panel.size) < 900 for panel in panels):
                raise ValueError("premium four-grid panels must be at least 900px on each edge")
            # A repeated quadrant is a structurally invalid transport grid.  Use a
            # small exact visual fingerprint so legitimate similar product angles
            # are retained while byte-identical copied panels fail closed.
            fingerprints = {
                hashlib.sha256(
                    panel.resize((32, 32)).convert("RGB").tobytes()
                ).hexdigest()
                for panel in panels
            }
            if len(fingerprints) != 4:
                raise ValueError("premium four-grid contains repeated panels")

            def encode_panel(index_and_box: tuple[int, tuple[int, int, int, int]]) -> GeneratedMedia:
                index, _box = index_and_box
                # Premium panels are already cut at the exact 50/50 transport-grid
                # boundary.  Keep their full native size so 4K grids yield true 2K
                # carousel images and 2K gateway fallbacks yield true 1K images.
                panel = panels[index - 1]
                if min(panel.size) < DXM_IMAGE_TARGET_SIZE:
                    panel = panel.resize(
                        (DXM_IMAGE_TARGET_SIZE, DXM_IMAGE_TARGET_SIZE),
                        Image.Resampling.LANCZOS,
                    )
                return GeneratedMedia(
                    stage=f"premium_image_{index}",
                    content=_image_to_jpeg_bytes(panel, quality=PREMIUM_IMAGE_JPEG_QUALITY),
                    content_type="image/jpeg",
                    suffix=".jpg",
                    provider="local-premium-split",
                    model="pillow",
                    reference_count=media.reference_count,
                    attempt_count=media.attempt_count,
                    provider_status_class=media.provider_status_class,
                )

            from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="pp-premium-encode") as pool:
                result = list(pool.map(encode_panel, enumerate(boxes, start=1)))
            result.append(self.compose_premium_grid_summary(result))
            return result
        except MediaConfigurationError:
            raise
        except Exception as exc:
            raise MediaProcessingError("generated premium four-grid image cannot be split") from exc

    @staticmethod
    def compose_premium_grid_summary(parts: Iterable[GeneratedMedia]) -> GeneratedMedia:
        """Create only the 800px preview summary; premium carousel parts stay high-resolution."""
        try:
            from PIL import Image  # type: ignore

            normalized = list(parts)
            if len(normalized) != 4:
                raise ValueError("exactly four premium grid slots are required")
            canvas = Image.new("RGB", (DXM_IMAGE_TARGET_SIZE * 2, DXM_IMAGE_TARGET_SIZE * 2))
            for index, part in enumerate(normalized):
                with Image.open(BytesIO(part.content)) as opened:
                    square = _center_crop_to_square(opened.convert("RGB"))
                    image = square.resize(
                        (DXM_IMAGE_TARGET_SIZE, DXM_IMAGE_TARGET_SIZE),
                        Image.Resampling.LANCZOS,
                    )
                canvas.paste(
                    image,
                    (
                        (index % 2) * DXM_IMAGE_TARGET_SIZE,
                        (index // 2) * DXM_IMAGE_TARGET_SIZE,
                    ),
                )
            summary = canvas.resize(
                (DXM_IMAGE_TARGET_SIZE, DXM_IMAGE_TARGET_SIZE),
                Image.Resampling.LANCZOS,
            )
            return GeneratedMedia(
                stage="premium_image_summary",
                content=_image_to_jpeg_bytes(summary),
                content_type="image/jpeg",
                suffix=".jpg",
                provider="local-compose",
                model="pillow",
                reference_count=max((part.reference_count for part in normalized), default=0),
                attempt_count=max((part.attempt_count for part in normalized), default=1),
            )
        except Exception as exc:
            raise MediaProcessingError("premium grid summary cannot be composed") from exc

    def compose_grid_summary(self, parts: Iterable[GeneratedMedia]) -> GeneratedMedia:
        """Compose four normalized 800px slots into a matching 800px 2x2 summary."""
        try:
            from PIL import Image  # type: ignore

            normalized = list(parts)
            if len(normalized) != 4:
                raise ValueError("exactly four normalized grid slots are required")
            canvas = Image.new("RGB", (DXM_IMAGE_TARGET_SIZE * 2, DXM_IMAGE_TARGET_SIZE * 2))
            for index, part in enumerate(normalized):
                with Image.open(BytesIO(part.content)) as opened:
                    image = opened.convert("RGB")
                    if image.size != (DXM_IMAGE_TARGET_SIZE, DXM_IMAGE_TARGET_SIZE):
                        raise ValueError("grid slot must be normalized to 800x800")
                    x = (index % 2) * DXM_IMAGE_TARGET_SIZE
                    y = (index // 2) * DXM_IMAGE_TARGET_SIZE
                    canvas.paste(image, (x, y))
            summary = canvas.resize(
                (DXM_IMAGE_TARGET_SIZE, DXM_IMAGE_TARGET_SIZE),
                Image.Resampling.LANCZOS,
            )
            return GeneratedMedia(
                stage="grid_image_summary",
                content=_image_to_jpeg_bytes(summary),
                content_type="image/jpeg",
                suffix=".jpg",
                provider="local-compose",
                model="pillow",
                reference_count=max((part.reference_count for part in normalized), default=0),
                attempt_count=max((part.attempt_count for part in normalized), default=1),
            )
        except MediaConfigurationError:
            raise
        except Exception as exc:
            raise MediaProcessingError("normalized grid summary cannot be composed") from exc

    def split_two_grid(self, media: GeneratedMedia, *, start_index: int = 1) -> list[GeneratedMedia]:
        """Split one landscape two-panel transport image into two marketplace squares.

        Unlike the legacy four-grid path, this deliberately uses a light structural
        contract: a compliant landscape canvas and a deterministic center cut. The
        two-image mode exists to improve output yield, so it must not inherit the
        stricter four-grid OCR and divider gates.
        """
        try:
            from PIL import Image  # type: ignore

            source = Image.open(BytesIO(media.content)).convert("RGB")
            width, height = source.size
            center = width // 2
            aspect_ratio = width / max(height, 1)
            if (
                height < DXM_IMAGE_TARGET_SIZE
                or center < DXM_IMAGE_TARGET_SIZE
                or not 1.8 <= aspect_ratio <= 2.2
            ):
                raise ValueError("two-image source must be a landscape canvas with two usable square halves")
            panels = (
                source.crop((0, 0, center, height)),
                source.crop((center, 0, width, height)),
            )
            result: list[GeneratedMedia] = []
            for offset, panel in enumerate(panels):
                square = _center_crop_to_square(panel)
                resized = square.resize((DXM_IMAGE_TARGET_SIZE, DXM_IMAGE_TARGET_SIZE), Image.Resampling.LANCZOS)
                result.append(
                    GeneratedMedia(
                        stage=f"grid_image_{start_index + offset}",
                        content=_image_to_jpeg_bytes(resized),
                        content_type="image/jpeg",
                        suffix=".jpg",
                        provider="local-split",
                        model="pillow",
                        reference_count=media.reference_count,
                        attempt_count=media.attempt_count,
                        provider_status_class=media.provider_status_class,
                    )
                )
            return result
        except MediaConfigurationError:
            raise
        except Exception as exc:
            raise MediaProcessingError("generated two-image layout cannot be split") from exc

    def normalize_standalone_image(self, media: GeneratedMedia, *, stage: str) -> GeneratedMedia:
        """Normalize one independently generated product image for carousel use."""
        try:
            from PIL import Image  # type: ignore

            source = Image.open(BytesIO(media.content)).convert("RGB")
            if min(source.size) < DXM_IMAGE_TARGET_SIZE:
                raise ValueError("standalone source is too small for marketplace output")
            square = _center_crop_to_square(source)
            resized = square.resize((DXM_IMAGE_TARGET_SIZE, DXM_IMAGE_TARGET_SIZE), Image.Resampling.LANCZOS)
            return GeneratedMedia(
                stage=stage,
                content=_image_to_jpeg_bytes(resized),
                content_type="image/jpeg",
                suffix=".jpg",
                provider="local-normalize",
                model="pillow",
                reference_count=media.reference_count,
                attempt_count=media.attempt_count,
                provider_status_class=media.provider_status_class,
            )
        except MediaConfigurationError:
            raise
        except Exception as exc:
            raise MediaProcessingError("generated standalone image cannot be normalized") from exc

    @staticmethod
    def validate_four_grid(media: GeneratedMedia) -> None:
        """Require a decodable grid; size is soft-gated so a 2K size wobble never blocks splitting.

        普通 2K 四宫格偶发返回非正方形或缩水图；这里仅要求最小边 >=1024，
        非正方形交由 split 阶段居中裁方后再切 4 格。
        """
        try:
            from PIL import Image  # type: ignore

            source = Image.open(BytesIO(media.content)).convert("RGB")
            width, height = source.size
            if min(width, height) < 1024:
                raise ValueError(
                    f"four-grid source is {width}x{height}; at least 1024px is required on each edge"
                )
        except MediaConfigurationError:
            raise
        except Exception as exc:
            detail = str(exc).strip()
            message = "generated four-grid structure failed validation"
            if detail:
                message = f"{message}: {detail}"
            raise MediaProcessingError(message) from exc

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
        # 网络抖动容忍：连接/读取超时放宽到 6s/10s，减少 COS 公开访问校验偶发超时导致的生成失败。
        try:
            response = _SESSION.head(value, allow_redirects=False, timeout=(6, 10))
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
        sections = [dict(config.get(name) or {}) for name in ("image", "backup_image")]
        server_managed = any(
            str(section.get("base_url") or "").strip().rstrip("/") == "server-managed-wuyin"
            for section in sections
        )
        for name, section_name in (("primary", "image"), ("backup", "backup_image")):
            section = dict(config.get(section_name) or {})
            base_url = str(section.get("base_url") or "").strip().rstrip("/")
            api_key = str(section.get("api_key") or "").strip()
            if not (base_url and api_key):
                continue
            if server_managed and base_url != "server-managed-wuyin":
                continue
            parsed = urlsplit(base_url)
            if base_url != "server-managed-wuyin" and (
                parsed.scheme not in {"http", "https"} or not parsed.netloc
            ):
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

    def _provider_order(self, providers: list[dict[str, str]], config: dict[str, Any]) -> list[dict[str, str]]:
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
            ordered = providers[index:] + providers[:index]
        elif strategy == "backup_first" and len(providers) > 1:
            ordered = [*providers[1:], providers[0]]
        else:
            ordered = providers
        if len(ordered) <= 1:
            return ordered
        now = time.monotonic()
        with self._provider_health_lock:
            available = [
                provider
                for provider in ordered
                if self._provider_cooldown_until.get(self._provider_health_key(provider), 0.0) <= now
            ]
        # Never turn an all-cooling or single-provider pool into immediate failure.
        return available or ordered

    @staticmethod
    def _provider_health_key(provider: dict[str, str]) -> str:
        return f"{provider.get('base_url', '')}|{provider.get('name', '')}|{provider.get('reference_model', '')}"

    def _record_provider_transient_failure(self, provider: dict[str, str]) -> None:
        key = self._provider_health_key(provider)
        with self._provider_health_lock:
            failures = self._provider_transient_failures.get(key, 0) + 1
            self._provider_transient_failures[key] = failures
            if failures >= PROVIDER_TRANSIENT_FAILURE_THRESHOLD:
                self._provider_cooldown_until[key] = time.monotonic() + PROVIDER_TRANSIENT_COOLDOWN_SECONDS

    def _record_provider_success(self, provider: dict[str, str]) -> None:
        key = self._provider_health_key(provider)
        with self._provider_health_lock:
            self._provider_transient_failures.pop(key, None)
            self._provider_cooldown_until.pop(key, None)

    def _load_references(self, values: Iterable[str], *, limit: int) -> list[tuple[bytes, str, str] | tuple[bytes, str, str, str]]:
        references: list[tuple[bytes, str, str] | tuple[bytes, str, str, str]] = []
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
                    detail = f"data image invalid: {_safe_error(exc)}"
                    errors.append(detail)
                    continue
                references.append((content, "reference.png", _data_url_content_type(header)))
            elif Path(value).is_file():
                path = Path(value)
                try:
                    content, content_type = _read_local_reference(path)
                except (MediaProcessingError, OSError) as exc:
                    errors.append(f"local image failed: {_safe_error(exc)}")
                    continue
                references.append((content, path.name, content_type))
            elif _plausible_public_http_url(value):
                try:
                    content, content_type = self._download_reference_image_cached(value)
                except (requests.RequestException, MediaProcessingError) as exc:
                    detail = f"download failed: {_safe_error(exc)}"
                    errors.append(detail)
                    # 打印具体失败值，便于在客户机器上定位是哪个参考图 URL 出问题。
                    print(
                        f"[reference-skip] stage=reference_input branch=url "
                        f"value={value[:160]!r} detail={detail}",
                        flush=True,
                    )
                    continue
                if not content or not content_type.startswith("image/"):
                    detail = "reference URL did not return an image"
                    errors.append(detail)
                    print(
                        f"[reference-skip] stage=reference_input branch=url "
                        f"value={value[:160]!r} detail={detail}",
                        flush=True,
                    )
                    continue
                references.append((content, _filename_for_url(value), content_type, value))
            else:
                # 既不是 data URI 也不是本机存在文件，也不满足公网 http(s) URL 结构：
                # 说明该参考值是一段脏字符串/失效的本地路径（例如 windows 绝对路径、
                # 裸 token），不能当作图片 URL 去下载，否则会以
                # "provider result URL is not a safe public URL" 硬失败并拖垮整条图生图。
                # 这里直接跳过，交给后续仍可用的参考，避免一个坏值拖垮整条生成。
                errors.append(f"reference value is not a usable source: {value[:120]!r}")
                # 打印被跳过的具体脏值，便于在客户机器上确认是哪个引用值污损。
                print(
                    f"[reference-skip] stage=reference_input branch=non-url "
                    f"value={value[:160]!r} reason=not-a-public-http-url",
                    flush=True,
                )
                continue
        if not references:
            detail = f" ({errors[0]})" if errors else ""
            raise MediaProcessingError(f"reference image download failed{detail}")
        return references

    def _download_reference_image_cached(self, url: str) -> tuple[bytes, str]:
        """Single-flight, bounded URL-byte cache scoped to this processor instance."""
        while True:
            with self._reference_cache_lock:
                cached = self._reference_cache.get(url)
                if cached is not None:
                    self._reference_cache.move_to_end(url)
                    return cached
                pending = self._reference_downloads.get(url)
                if pending is None:
                    pending = threading.Event()
                    self._reference_downloads[url] = pending
                    break
            pending.wait()

        try:
            downloaded = _download_reference_image(url)
        except Exception:
            with self._reference_cache_lock:
                self._reference_downloads.pop(url, None)
                pending.set()
            raise

        with self._reference_cache_lock:
            self._reference_cache[url] = downloaded
            self._reference_cache.move_to_end(url)
            while len(self._reference_cache) > self._reference_cache_limit:
                self._reference_cache.popitem(last=False)
            self._reference_downloads.pop(url, None)
            pending.set()
        return downloaded

    def _request_edit(
        self,
        provider: dict[str, str],
        prompt: str,
        references: list[tuple[bytes, str, str] | tuple[bytes, str, str, str]],
        *,
        timeout_seconds: float = IMAGE_EDIT_REQUEST_TIMEOUT_SECONDS,
        image_size: str | None = None,
        reference_model: str | None = None,
    ) -> tuple[bytes, str]:
        if _is_server_managed_wuyin_provider(provider):
            return self._request_server_managed_wuyin_image(
                provider,
                prompt,
                references,
                timeout_seconds=timeout_seconds,
                image_size=image_size,
            )
        if _is_wuyin_image_provider(provider):
            return self._request_wuyin_image(
                provider,
                prompt,
                references,
                timeout_seconds=timeout_seconds,
                image_size=image_size,
            )
        files: Any
        if len(references) == 1:
            content, filename, content_type = references[0][:3]
            files = {"image": (filename, BytesIO(content), content_type)}
        else:
            files = [
                ("image[]", (filename, BytesIO(content), content_type))
                for content, filename, content_type in (ref[:3] for ref in references)
            ]
        # 与文本请求共享全局速率限制：图片生成最重且最容易被供应商限流。
        global_ai_request_limiter().acquire()
        response = _SESSION.post(
            f"{provider['base_url']}/images/edits",
            headers={"Authorization": f"Bearer {provider['api_key']}"},
            data={
                "model": reference_model or provider["reference_model"] or provider["model"],
                "prompt": prompt,
                "n": "1",
                "size": _normalized_image_size(image_size or provider.get("image_size")),
            },
            files=files,
            timeout=max(1.0, min(float(timeout_seconds), IMAGE_EDIT_REQUEST_TIMEOUT_SECONDS)),
            stream=True,
        )
        try:
            if not response.ok:
                raise MediaProcessingError(
                    f"provider returned HTTP {response.status_code}",
                    status_code=response.status_code,
                )
            payload = _bounded_response_json(response)
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
        return _download_provider_result_image(url)

    def _request_server_managed_wuyin_image(
        self,
        provider: dict[str, str],
        prompt: str,
        references: list[tuple[bytes, str, str] | tuple[bytes, str, str, str]],
        *,
        timeout_seconds: float,
        image_size: str | None = None,
    ) -> tuple[bytes, str]:
        token = remote_token()
        reservation = usage_id("image_grid")
        if not token or not reservation:
            raise MediaProcessingError("server-managed image usage is not reserved")
        urls = [
            str(ref[3]).strip()
            for ref in references
            if len(ref) >= 4 and _plausible_public_http_url(str(ref[3]).strip())
        ]
        response: requests.Response | None = None
        try:
            with _SERVER_MANAGED_IMAGE_GATE.hold(reservation):
                response = _SESSION.post(
                    f"{gateway_base_url()}/api/customer/ai/image",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "usage_id": reservation,
                        "prompt": prompt,
                        "size": _wuyin_size(image_size or provider.get("image_size")),
                        **({"urls": urls} if urls else {}),
                    },
                    timeout=max(30.0, min(float(timeout_seconds), 660.0)),
                    allow_redirects=False,
                    stream=True,
                )
                status_code = int(response.status_code)
                if not 200 <= status_code < 300:
                    raise _gateway_image_status_error(status_code)
                payload = _bounded_response_json(response)
        except MediaProcessingError:
            raise
        except requests.RequestException as exc:
            print(
                f"[image-gateway-diag] RequestException: {type(exc).__name__}: {exc}",
                flush=True,
            )
            raise MediaProcessingError("server image gateway is temporarily unavailable") from exc
        finally:
            if response is not None:
                response.close()
        if not isinstance(payload, dict) or not bool(payload.get("ok")):
            print(
                f"[image-gateway-diag] non-ok payload: {str(payload)[:300]}",
                flush=True,
            )
            raise MediaProcessingError("server image gateway rejected the request")
        result_url = str(payload.get("result_url") or "").strip()
        if not result_url or not is_safe_external_url(result_url):
            raise MediaProcessingError("server image gateway returned no safe image result")
        return _download_provider_result_image(result_url)

    def _request_wuyin_image(
        self,
        provider: dict[str, str],
        prompt: str,
        references: list[tuple[bytes, str, str] | tuple[bytes, str, str, str]],
        *,
        timeout_seconds: float,
        image_size: str | None = None,
    ) -> tuple[bytes, str]:
        urls, temporary_store, temporary_references = self._wuyin_reference_urls(references)
        try:
            global_ai_request_limiter().acquire()
            response = _SESSION.post(
                f"{provider['base_url']}{WUYIN_IMAGE_SUBMIT_PATH}",
                params={"key": provider["api_key"]},
                headers={
                    "Authorization": provider["api_key"],
                    "Content-Type": "application/json",
                },
                json={
                    "prompt": prompt,
                    "size": _wuyin_size(image_size or provider.get("image_size")),
                    "urls": urls,
                },
                timeout=max(1.0, min(30.0, float(timeout_seconds))),
                stream=True,
            )
            try:
                if not response.ok:
                    raise MediaProcessingError(
                        f"provider returned HTTP {response.status_code}",
                        status_code=response.status_code,
                    )
                payload = _bounded_response_json(response)
            finally:
                response.close()
            if not isinstance(payload, dict) or int(payload.get("code") or 0) != 200:
                raise MediaProcessingError(f"provider submit failed: {_provider_message(payload)}")
            data = payload.get("data") or {}
            task_id = str(data.get("id") or data.get("task_id") or "").strip() if isinstance(data, dict) else ""
            if not task_id:
                raise MediaProcessingError("provider response does not contain image task id")
            result_url = self._poll_wuyin_image_result(provider, task_id, timeout_seconds=timeout_seconds)
            return _download_provider_result_image(result_url)
        finally:
            if temporary_store is not None:
                for temporary in temporary_references:
                    temporary_store.delete(temporary)

    def _wuyin_reference_urls(
        self,
        references: list[tuple[bytes, str, str] | tuple[bytes, str, str, str]],
    ) -> tuple[list[str], TemporaryCosStore | None, list[TemporaryReference]]:
        """Return provider-fetchable URLs, relaying local-only references through private COS."""
        urls: list[str] = []
        local_references: list[tuple[bytes, str]] = []
        for reference in references:
            candidate = str(reference[3]).strip() if len(reference) >= 4 else ""
            if candidate and is_safe_external_url(candidate):
                urls.append(candidate)
            else:
                local_references.append((bytes(reference[0]), str(reference[2] or "image/jpeg")))

        if not local_references:
            if not urls:
                raise MediaProcessingError("direct image provider requires a reference image")
            return urls, None, []

        cos = dict(self._config().get("cos") or {})
        store = TemporaryCosStore(
            RuntimeCosConfig(
                bucket=str(cos.get("bucket") or "").strip(),
                region=str(cos.get("region") or "").strip(),
                secret_id=str(cos.get("secret_id") or "").strip(),
                secret_key=str(cos.get("secret_key") or "").strip(),
            )
        )
        temporary_references: list[TemporaryReference] = []
        try:
            for content, content_type in local_references:
                temporary = store.publish(content, content_type)
                if not _plausible_public_http_url(temporary.url):
                    raise TemporaryReferenceError(
                        "temporary COS reference did not return a provider-fetchable URL"
                    )
                temporary_references.append(temporary)
                urls.append(temporary.url)
        except TemporaryReferenceError as exc:
            for temporary in temporary_references:
                store.delete(temporary)
            raise MediaProcessingError(
                "failed to relay local reference image to the direct image provider"
            ) from exc

        return urls, store, temporary_references

    def _poll_wuyin_image_result(
        self,
        provider: dict[str, str],
        task_id: str,
        *,
        timeout_seconds: float,
    ) -> str:
        deadline = time.monotonic() + max(10.0, min(float(timeout_seconds), IMAGE_EDIT_REQUEST_TIMEOUT_SECONDS))
        last_message = ""
        while time.monotonic() < deadline:
            time.sleep(WUYIN_IMAGE_POLL_INTERVAL_SECONDS)
            response = _SESSION.get(
                f"{provider['base_url']}{WUYIN_IMAGE_DETAIL_PATH}",
                params={"key": provider["api_key"], "id": task_id},
                headers={"Authorization": provider["api_key"]},
                timeout=30,
                stream=True,
            )
            try:
                if not response.ok:
                    last_message = f"detail HTTP {response.status_code}"
                    continue
                payload = _bounded_response_json(response)
            finally:
                response.close()
            if not isinstance(payload, dict):
                last_message = "detail response is incompatible"
                continue
            code = int(payload.get("code") or 0)
            if code and code != 200:
                last_message = _provider_message(payload)
                if code in {400, 401, 403, 404}:
                    raise MediaProcessingError(f"provider image task failed: {last_message}")
                continue
            data = payload.get("data") or {}
            status_value = str(data.get("status") or payload.get("status") or "").strip().lower() if isinstance(data, dict) else ""
            result_url = _first_image_url(data) or _first_image_url(payload)
            if result_url:
                return result_url
            message = _provider_message(payload)
            message_lower = message.lower()
            # 上游 status 为异步任务状态码（含纯数字、英文与中文文本三种表达）。语义：
            #   1      任务处理完成（成功）；≥0 且命中文末（0/2/3/4/5/6）= 排队/准备/等待/处理中/发布
            #   <0     任务处理失败；fail/failed/error/cancelled / 失败= 明确失败
            # 数字状态无法安全按文本失败集归类（3/4/5 实为“处理中”，会误判成失败终态），
            # 先尝试转数字：≥0 一律视为处理中继续轮询等图片；<0 才判失败。
            try:
                numeric_status = int(float(status_value)) if status_value else None
            except (TypeError, ValueError):
                numeric_status = None
            if numeric_status is not None:
                if numeric_status < 0:
                    raise MediaProcessingError(
                        f"provider image task failed: status={status_value} code={code} {message}",
                        status_class="transient",
                    )
                # status ∈ {0,2,3,4,5,6}: 处理中，等图片就绪后返回
                last_message = message or f"status={status_value}"
                continue
            # 上游可能返回中文状态（如“成功/失败”）；`.lower()` 不影响中文，需单独归并。
            # 命中成功终态却无图片 URL 时立即报错，避免一直轮询到超时。
            if status_value in {
                "success", "succeeded", "finish", "finished", "completed", "done",
                "成功", "已完成", "完成", "处理完成",
            }:
                raise MediaProcessingError(
                    f"provider image task succeeded without image url: status={status_value} code={code} {message}"
                )
            if status_value in {
                "fail", "failed", "error", "cancelled", "canceled",
                "失败", "处理失败", "生成失败", "已完成失败",
            }:
                raise MediaProcessingError(
                    f"provider image task failed: status={status_value} code={code} {message}",
                    status_class="transient",
                )
            last_message = message or f"status={status_value or 'processing'}"
        raise MediaProcessingError(f"provider image task timed out: {last_message}")


def _is_wuyin_image_provider(provider: dict[str, str]) -> bool:
    return urlsplit(str(provider.get("base_url") or "")).netloc.lower() == "api.wuyinkeji.com"


def _is_server_managed_wuyin_provider(provider: dict[str, str]) -> bool:
    return str(provider.get("base_url") or "") == "server-managed-wuyin"


def _plausible_public_http_url(value: str) -> bool:
    """Structural http(s) URL check that never touches DNS.

    Server-managed image generation forwards source URLs to the platform
    gateway, which downloads them itself.  The local desktop only needs a
    syntactic sanity check; requiring a DNS round-trip here makes the request
    flaky under proxy TUN fake-ip setups.
    """
    text = str(value or "").strip()
    if not text or len(text) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in text):
        return False
    try:
        parts = urlsplit(text)
    except ValueError:
        return False
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return False
    if parts.username is not None or parts.password is not None or parts.fragment:
        return False
    hostname = parts.hostname.lower().rstrip(".")
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith((".local", ".localhost")):
        return False
    return True


def _normalize_reference_url(value: str) -> str:
    """把带 kwcdn imageView2 缩略参数的参考图 URL 提升为高清版本。

    Temu 采集保存的图片 URL 常形如
    ``https://img.kwcdn.com/product/open/xxx.jpeg?imageView2/2/w/180/q/70/format/avif``，
    实际只返回 180px 的 AVIF 缩略图，上游生图服务（image_gpt）无法据此生成
    2K 商品图而直接失败。这里丢弃缩略管道并替换为 1200px JPEG 规格
    （kwcdn 原图通常为 1500x2000+ 的 JPEG）。仅处理纯 imageView2 查询串，
    带其他参数的 URL 保持原样以免破坏鉴权等语义。
    """
    text = str(value or "").strip()
    if not text:
        return text
    try:
        parts = urlsplit(text)
    except ValueError:
        return text
    hostname = str(parts.hostname or "").lower()
    query = parts.query or ""
    if not hostname.endswith("kwcdn.com") or "imageView2" not in query or "&" in query:
        return text
    if not query.startswith("imageView2/"):
        return text
    return parts._replace(query="imageView2/2/w/1200/q/90").geturl()


def _wuyin_size(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"1024x1024", "2048x2048", "4096x4096"}:
        return "1:1"
    if raw in {
        "auto",
        "1:1",
        "3:2",
        "2:3",
        "16:9",
        "9:16",
        "4:3",
        "3:4",
        "21:9",
        "9:21",
        "1:3",
        "3:1",
        "2:1",
        "1:2",
    }:
        return raw
    return "1:1"


def _first_image_url(value: Any) -> str:
    if isinstance(value, str):
        candidate = value.strip()
        if is_safe_external_url(candidate):
            return candidate
        if candidate.startswith(("{", "[")):
            try:
                return _first_image_url(json.loads(candidate))
            except json.JSONDecodeError:
                return ""
        return ""
    if isinstance(value, dict):
        for key in ("url", "image_url", "image", "src", "href"):
            found = _first_image_url(value.get(key))
            if found:
                return found
        for key in ("result", "results", "images", "urls", "output", "outputs"):
            found = _first_image_url(value.get(key))
            if found:
                return found
    if isinstance(value, (list, tuple)):
        for item in value:
            found = _first_image_url(item)
            if found:
                return found
    return ""


def _provider_message(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "provider response is incompatible"
    for key in ("msg", "message", "error", "reason"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:180]
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("msg", "message", "error", "reason"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:180]
    try:
        return json.dumps(payload, ensure_ascii=False)[:180]
    except Exception:
        return "provider returned an error"


def _safe_error(error: BaseException) -> str:
    message = str(error).replace("\n", " ").strip()
    return message[:180] or error.__class__.__name__


def _retry_class(error: BaseException) -> str:
    status = getattr(error, "status_code", None)
    explicit_class = str(getattr(error, "status_class", "") or "")
    if explicit_class in {"billing_payment_required", "billing_forbidden", "non_retryable_4xx"}:
        return "non_retryable_4xx"
    if explicit_class in {
        "reference_input_download_failed",
        "provider_result_download_failed",
    }:
        return "non_retryable_local"
    if explicit_class in {"gateway_in_progress", "gateway_bad_response", "gateway_unavailable", "server_error"}:
        return "server_error"
    if status in {400, 401, 402, 403, 404}:
        return "non_retryable_4xx"
    if status == 409:
        return "server_error"
    if status == 429:
        return "rate_limited"
    if status is not None and 500 <= status < 600:
        return "server_error"
    if isinstance(error, (requests.Timeout, TimeoutError)):
        return "unknown_outcome_timeout"
    if isinstance(error, (requests.ConnectionError, requests.exceptions.ChunkedEncodingError)):
        # ChunkedEncodingError 常见于「Response ended prematurely」：服务端声明了
        # 分块传输但流提前中断，属于网络瞬断，应重试而不是当作本地不可重试错误。
        return "connection_error"
    return "non_retryable_local"


def _normalized_image_size(value: Any) -> str:
    """Return a provider-safe square image size; product grids default to 2K for readable split panels."""
    normalized = str(value or "").strip().lower()
    if normalized in {"1024x1024", "2048x2048", "4096x4096", "2048x1024", "1024x2048"}:
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


def _inset_grid_panel(image: Any) -> Any:
    """向内裁掉固定比例边缘，避开模型画出的过宽白线。

    偏移量刻意取得小，只舍弃边缘一点点商品图；裁小后由调用方统一缩放到
    800×800（不足 800 时自动放大），尺寸保持达标。
    """
    width, height = image.size
    inset_x = max(1, round(width * FOUR_GRID_EDGE_INSET_FRACTION))
    inset_y = max(1, round(height * FOUR_GRID_EDGE_INSET_FRACTION))
    return image.crop((inset_x, inset_y, width - inset_x, height - inset_y))


def _image_to_jpeg_bytes(image: Any, *, quality: int = DXM_IMAGE_JPEG_QUALITY) -> bytes:
    """PIL Image → high-detail JPEG bytes for 800px marketplace images."""
    buffer = BytesIO()
    image.save(
        buffer,
        format="JPEG",
        quality=quality,
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


def _read_local_reference(path: Path) -> tuple[bytes, str]:
    content = path.read_bytes()
    if not content:
        raise MediaProcessingError("local reference image is empty")
    content_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    try:
        from PIL import Image  # type: ignore

        with Image.open(BytesIO(content)) as opened:
            detected_type = str(Image.MIME.get(str(opened.format or "").upper()) or "")
            opened.verify()
    except ImportError:
        return content, content_type
    except Exception as exc:
        raise MediaProcessingError("local reference image is unreadable") from exc
    if detected_type.startswith("image/"):
        content_type = detected_type
    return content, content_type


def _root_download_error(error: BaseException) -> BaseException:
    current = error
    seen: set[int] = set()
    while current.__cause__ is not None and id(current) not in seen:
        seen.add(id(current))
        current = current.__cause__
    return current


def _download_failure_reason(error: BaseException) -> str:
    root = _root_download_error(error)
    detail = _safe_error(root).casefold()
    if (
        isinstance(root, socket.gaierror)
        or "cannot be resolved" in detail
        or "name resolution" in detail
    ):
        return "DNS lookup failed"
    if isinstance(root, ssl.SSLError) or "ssl" in detail or "tls" in detail or "certificate" in detail:
        return "TLS handshake failed"
    if isinstance(root, (TimeoutError, socket.timeout)) or "timed out" in detail or "timeout" in detail:
        return "connection timed out"
    if isinstance(root, ConnectionResetError) or "connection reset" in detail or "forcibly closed" in detail:
        return "connection was reset"
    return "connection failed"


def _download_image_with_retries(
    url: str,
    *,
    timeout_seconds: float,
    stage: str,
    attempts: int,
) -> tuple[bytes, str]:
    total_attempts = max(1, int(attempts))
    last_error: MediaProcessingError | None = None
    host = str(urlsplit(str(url or "")).hostname or "unknown")
    for attempt in range(1, total_attempts + 1):
        try:
            return _download_pinned_public_image(url, timeout_seconds=timeout_seconds)
        except MediaProcessingError as exc:
            last_error = exc
            root = _root_download_error(exc)
            print(
                "[image-download-diag] "
                f"stage={stage} host={host} attempt={attempt}/{total_attempts} "
                f"error_type={type(root).__name__} detail={_safe_error(root)}",
                flush=True,
            )
            retryable = "temporarily unavailable" in str(exc).casefold()
            if not retryable:
                raise
            if attempt < total_attempts:
                delay_index = min(attempt - 1, len(DOWNLOAD_RETRY_BACKOFF_SECONDS) - 1)
                time.sleep(DOWNLOAD_RETRY_BACKOFF_SECONDS[delay_index])
    assert last_error is not None
    label = (
        "reference image download failed"
        if stage == "reference_input"
        else "generated image download failed"
    )
    raise MediaProcessingError(
        f"{label}: {_download_failure_reason(last_error)}",
        status_class=f"{stage}_download_failed",
    ) from last_error


def _download_provider_result_image(url: str) -> tuple[bytes, str]:
    return _download_image_with_retries(
        url,
        timeout_seconds=360,
        stage="provider_result",
        attempts=PROVIDER_RESULT_DOWNLOAD_ATTEMPTS,
    )


def _download_reference_image(url: str) -> tuple[bytes, str]:
    """Download one reference through the shared DNS-pinned bounded transport."""

    return _download_image_with_retries(
        url,
        timeout_seconds=30,
        stage="reference_input",
        attempts=REFERENCE_DOWNLOAD_ATTEMPTS,
    )
