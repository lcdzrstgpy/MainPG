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
import logging
import mimetypes
import os
import re
import socket
import ssl
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

import requests

from ..domain.policy import is_safe_external_url, resolve_safe_external_url
from ....media_enhance import EnhanceMetrics, EnhanceOptions, enhance_panel
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

_LOGGER = logging.getLogger(__name__)

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
# 直连模式下模型同样由 URL 路径决定；桌面端在个人中心切换后按模型选路径。
WUYIN_IMAGE_SUBMIT_PATHS = {
    "image_gpt": "/api/async/image_gpt",
    "image_gpt_2.5": "/api/async/image_gpt_2.5",
    # 无印自营的 nanoBanana（Gemini 系）：与 gpt-image 属不同上游，用于「gpt-image 整族
    # 维护/限流」时的替换或兜底。提交协议同属无印异步链路（返回 data.id，轮询走
    # WUYIN_IMAGE_DETAIL_PATH），但 size 是画质档（1K/2K/4K）而非比例，比例用 aspectRatio。
    "nanoBanana_pro": "/api/async/image_nanoBanana_pro",
    # gemini-3.1-flash-lite-image：极快、最便宜，但**只支持 1K**（传 2K/4K 会被拒）。
    "nanoBanana2Lite": "/api/async/image_nanoBanana2Lite",
}
# 仅支持 1K 档的 nanoBanana 型号（键为归一化模型名，见 _normalize_model_key）。
WUYIN_NANO_BANANA_1K_ONLY = ("nanobanana2lite",)
# 火山方舟官方生图（Seedream）：同步 JSON 接口，参考图以 data URI 直传，无需公网 URL。
# 该链路必须显式 watermark=False —— 方舟生图默认会打「豆包AI生成」水印（实测），
# 而我们卖的是商品图，绝不能带第三方水印。域名用于识别链路。
ARK_IMAGE_HOST_SUFFIX = "ark.cn-beijing.volces.com"
ARK_IMAGE_SIZE_DEFAULT = "2K"
WUYIN_IMAGE_DETAIL_PATH = "/api/async/detail"
# image_gpt_2.5 只提供 1K 档，尺寸字段是像素串 aspectRatio（没有 size）。
WUYIN_IMAGE_ASPECT_RATIO_2_5 = {
    "1:1": "1024x1024",
    "16:9": "1280x720",
    "9:16": "720x1280",
    "4:3": "1152x864",
    "3:4": "864x1152",
    "3:2": "1536x1024",
    "2:3": "1024x1536",
    "5:4": "1120x896",
    "4:5": "896x1120",
    "21:9": "1456x624",
    "9:21": "624x1456",
    "1:3": "688x2048",
    "3:1": "2048x688",
    "2:1": "1536x768",
    "1:2": "768x1536",
}
WUYIN_IMAGE_POLL_INTERVAL_SECONDS = 3.0
# 上游异步生图 detail 没有任何进度字段：任务执行期间 status 恒为 3（排队/准备/等待/
# 处理中/发布都无字段变化），因此「无字段变化超过阈值」实际等价于「该任务已运行 N 秒」，
# 停滞阈值 = 变相的整任务超时。达到阈值也不判死，只作为「触发再提交一次」的信号，原任务
# 继续被轮询；唯一硬边界仍是总时长预算与提交次数上限（见下方）。
# 现场实测的四宫格(2K, image_gpt 2048x2048)成功样本耗时分布（n=69）：
#   min 88s / p50 148s / p90 206s / p95 210s / max 216s
# 回退样本（n=13）的整条 pipeline 耗时：p50 293s、p90/p95 668s（= 660s 总预算烧完）。
# 旧值 180s 落在成功分布的 p82 附近 → 约 17% 的正常生成被误判为「停滞」，进而触发重复
# 提交（多付费）、真卡死时烧完 660s 预算、最终回退来源图。2026-10-15 曾按「成功 max
# 216s + 约 40% 余量」定标为 300s。
# 2026-10-16 复测（无印出图全线回归当天）：3 条并发里 2 条首发在上游挂死，300s 阈值下单条
# 要吃满 486s（300s 等阈值 + 追加提交再跑 130s 才出图）。同源同尺寸母图的 POD 链路实测
# 基线（近千次调用 p95 115s、健康日上限 136s，见 pod_customization/ai_runtime.py 的
# SUCHUANG_RESULT_TIMEOUT_SECONDS）与其 240s 分段超时（≈3×p95）交叉印证：240s 更贴近上游
# 真实分布。故 2K 档由 300s 下调到 240s——仍在历史成功 max 216s 之上，又少等 60s。
# 超阈值仍**不判死**，只作为「追加一次提交」的信号；原任务继续被轮询（先到先用），
# 唯一硬边界仍是总时长预算与提交次数上限（见下方）。
# 1K 档（image_gpt_2.5 恒为 1K；旧端点 1024x1024 的槽位重绘等）实测 max 95.5s，用
# 120s（约 26% 余量）已足够，本次维持不变：1K 不用于四宫格主链路（四宫格恒发 2048x2048）。
# 两档阈值均可由配置 ``limits.image_stall_threshold_seconds`` /
# ``limits.image_stall_threshold_2k_seconds`` 覆盖，现场可按上游波动再调。
WUYIN_IMAGE_STALL_THRESHOLD_SECONDS = 120.0
WUYIN_IMAGE_STALL_THRESHOLD_2K_SECONDS = 240.0
MAX_PROVIDER_RESULT_BYTES = 32 * 1024 * 1024
MAX_PROVIDER_JSON_BYTES = 8 * 1024 * 1024
REFERENCE_DOWNLOAD_ATTEMPTS = 3
PROVIDER_RESULT_DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_RETRY_BACKOFF_SECONDS = (0.2, 0.6)
# 参考图私有 COS 中转的带退避重试：中转失败会让无印拿不到参考图而静默卡死，
# 因此发布临时对象允许小步重试，避免一次瞬时抖动就升级为整条回退来源图。
COS_RELAY_PUBLISH_ATTEMPTS = 3
COS_RELAY_PUBLISH_BACKOFF_SECONDS = (0.5, 1.5)
# 四宫格生图对冲（hedge）的提交次数硬上限：单次生成最多向上游提交 3 个异步任务
# （1 个主任务 + 至多 2 个对冲）。达到上限后只等待已提交任务，绝不新增请求，
# 既避免同源重复计费，也杜绝「停滞即无限重试」。
GRID_IMAGE_MAX_SUBMITS = 3
# 无「不同链路」可对冲时退化为「同链路有界重试」的独立更紧上限：总计最多 2 次提交
# （1 个主任务 + 至多 1 次同链路重试）。同一上游重复提交意味着同源重复计费，因此这里
# 比全局 GRID_IMAGE_MAX_SUBMITS=3 更紧；它只约束同链路重试，全局硬顶仍对所有路径生效。
GRID_IMAGE_SINGLE_LINK_MAX_SUBMITS = 2

# 图片 provider 轮巡游标（对齐原项目 native_product_engine._PROVIDER_CURSORS）：
# 进程内全局共享、线程锁保护，按"每次图片请求"取模轮转起始 provider，实现负载均衡。
_PROVIDER_CURSOR_LOCK = threading.Lock()
_PROVIDER_CURSORS: dict[str, int] = {}


def _resolve_reference_limit(config: dict[str, Any], stage: str) -> int:
    """解析某 stage 的参考图上限：配置缺失时 grid_image 取 1、其余取 2。

    ``reference_max_cap`` 为全局硬上限（默认 4）。组合套装多件商品需要把每件
    单品原图都作为参考图送入生图，会显式抬高该上限（不会影响其它模块的默认行为）。
    """
    limits = config.get("limits") or {}
    default_limit = 1 if stage == "grid_image" else 2
    cap = max(1, int(limits.get("reference_max_cap") or 4))
    return max(1, min(int(limits.get(f"{stage}_reference_max_count") or default_limit), cap))


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


@dataclass
class _WuyinImageTask:
    """无印异步生图任务的运行时状态。

    无印没有进度字段：任务执行期间 status 恒为 3，故只能用「距上次字段变化的时间」
    作为间接进展信号。这里把提交后的任务状态独立出来，便于对多个任务做交替轮询
    （对冲请求无需线程）。
    """

    provider: dict[str, str]
    task_id: str
    model: str
    stall_threshold_seconds: float
    started_at: float
    deadline: float
    store: Any
    temporary_references: list[Any]
    last_signature: tuple[str, bool] | None = None
    last_progress_at: float = 0.0
    last_message: str = ""
    last_status_value: str = ""
    last_code: int = 0
    # 该任务对应的「提交事件」记录（可观测性）；直接调用方/非对冲路径为 None。
    event: dict[str, Any] | None = None


class _GridSubmitBudget:
    """单次四宫格生成的提交次数硬上限（含主提交与 hedge 追发）。"""

    def __init__(self, limit: int = GRID_IMAGE_MAX_SUBMITS) -> None:
        self.limit = max(1, int(limit))
        self.used = 0

    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def consume(self) -> bool:
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


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
    # 每次上游提交的紧凑追踪（相对本次生成开始的时间 + 该次提交的最终结果 + 合计），
    # 让排障时能看出「实际提交了几次、每次是什么结果」。不含任何原始响应体。
    provider_attempt_notes: tuple[str, ...] = ()


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
        reference_limit = _resolve_reference_limit(config, stage)
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
        reference_limit = _resolve_reference_limit(config, stage)
        # 统一先把参考图 URL 归一化：Temu 等平台保存的 imageView2 缩略图地址
        # 只有 180px/AVIF，上游生图服务无法使用；提升为高清 JPEG 版本后再转发/下载。
        reference_values = [
            _normalize_reference_url(str(raw or "").strip())
            for raw in reference_values
            if _normalize_reference_url(str(raw or "").strip())
        ]
        # 客户端固定直连：参考图由本地下载/缓存后随请求体上传，不再经平台网关转发。
        # 直连提供方只能接收可公网访问的参考图 URL（它需要自己下载图片），
        # 本地缓存文件路径无法传递给提供方。采集链路保存的本地文件不带 URL，
        # 若直接取本地路径会导致提交给提供方的 urls=[]，图生图任务静默失败
        # （提供方返回 status=3 且无图无原因）。因此直连模式把远端 URL 排到
        # 前面，保证 _load_references 取到的前 N 个参考都带 URL（元组第 4 项）。
        candidate_values = [
            *[value for value in reference_values if _plausible_public_http_url(value)],
            *[value for value in reference_values if not _plausible_public_http_url(value)],
        ]
        ordinary_references = self._load_references(candidate_values, limit=reference_limit)
        if not ordinary_references:
            raise MediaProcessingError("a confirmed source image is required for image processing")
        ordinary_reference_count = len(ordinary_references)
        # 参考图在两次尝试之间可能被替换（停滞时改走 COS 中转、或换下一张候选图），
        # 这里按「当前普通参考图 + 额外参考图 + 底板」统一组装本次尝试要发的图。
        extra_reference_list = list(extra_references or [])
        scaffold_required = bool(layout_scaffold)

        def assemble_attempt_references(new_ordinary: list[tuple]) -> list[tuple]:
            built = [*new_ordinary, *extra_reference_list]
            if scaffold_required and new_ordinary:
                built.append(
                    (build_grid_scaffold(new_ordinary[0][0]), "fixed-four-grid-layout.png", "image/png")
                )
            return built

        references: list[tuple] = assemble_attempt_references(ordinary_references)
        retries = max(1, min(int((config.get("limits") or {}).get("image_retry_attempts") or 3), 5))
        errors: list[str] = []
        attempt_count = 0
        generation_started_at = time.monotonic()
        generation_deadline = generation_started_at + IMAGE_GENERATION_TOTAL_TIMEOUT_SECONDS
        # 可观测性：记录本次生成内每一次真实上游提交（时间点 + 最终结果），供
        # attempt_count / provider_status_class 反映「真实提交次数」而非外层轮次数。
        submit_events: list[dict[str, Any]] = []
        # provider 轮换：外层按「轮次」、内层遍历所有已配置 provider，保证每个 provider 都
        # 拿到尝试机会。此前四宫格把 max_total_attempts 压到 2，首个 provider 吃满预算后备用
        # provider 从未被使用；一旦首个 provider 停滞就直接回退来源图。现在四宫格改为
        # 「provider 数 × 每 provider 2 次」，其余 stage 沿用 image_retry_attempts。
        # 总时长预算 IMAGE_GENERATION_TOTAL_TIMEOUT_SECONDS 仍为硬上限。
        ordered_providers = self._provider_order(providers, config)
        per_provider_attempts = 2 if stage == "grid_image" else retries
        max_total_attempts = per_provider_attempts * max(1, len(ordered_providers))
        # 四宫格生图对冲（hedge）：命中停滞阈值时不再直接丢弃原任务，而是「保持对原任务的
        # 轮询 + 向不同链路 provider 追发一个任务」并行推进，任一条先出图即采用。
        # 提交次数由 submit_budget 硬封顶（≤ GRID_IMAGE_MAX_SUBMITS），总时长沿用既有生成
        # 预算 generation_deadline，因此 hedge 既不会无限请求，也不会拉长总时长。
        # 非四宫格 stage 不启用 hedge，语义与行为保持原样。
        hedge_enabled = stage == "grid_image"
        hedge_candidates = ordered_providers if hedge_enabled else None
        submit_budget = _GridSubmitBudget() if hedge_enabled else None
        # 停滞升级状态：整次调用最多换一次候选参考图，避免无限重试；换 provider 由轮巡完成。
        reference_offset = 0
        references_rotated = False
        for round_index in range(per_provider_attempts):
            for provider in ordered_providers:
                attempt = round_index + 1
                if attempt_count >= max_total_attempts:
                    break
                remaining_seconds = generation_deadline - time.monotonic()
                if remaining_seconds <= 1.0:
                    break
                request_timeout = min(IMAGE_EDIT_REQUEST_TIMEOUT_SECONDS, remaining_seconds)
                attempt_count += 1
                events_before = len(submit_events)
                try:
                    content, content_type = self._request_edit(
                        provider,
                        prompt,
                        references,
                        timeout_seconds=request_timeout,
                        image_size=image_size,
                        reference_model=model_override,
                        hedge_candidates=hedge_candidates,
                        submit_budget=submit_budget,
                        generation_deadline=generation_deadline,
                        submit_events=submit_events,
                        generation_started_at=generation_started_at,
                    )
                    # 单次 _request_edit 内部（四宫格 hedge）可能向上游提交多个任务：把超出
                    # 1 次的部分补进来，使 attempt_count 反映真实远程提交数而非外层轮次数。
                    attempt_count += max(0, (len(submit_events) - events_before) - 1)
                    self._record_provider_success(provider)
                    suffix = _suffix_for_content_type(content_type)
                    attempt_notes = _submit_trace_notes(submit_events, generation_started_at)
                    return GeneratedMedia(
                        stage=stage,
                        content=content,
                        content_type=content_type,
                        suffix=suffix,
                        provider=provider["name"],
                        model=model_override or provider["reference_model"] or provider["model"],
                        reference_count=ordinary_reference_count,
                        attempt_count=attempt_count,
                        provider_status_class=_submit_trace_status_class("success", attempt_notes),
                        provider_attempt_notes=attempt_notes,
                    )
                except (requests.RequestException, TimeoutError, ValueError, MediaProcessingError) as exc:
                    attempt_count += max(0, (len(submit_events) - events_before) - 1)
                    errors.append(f"{provider['name']} attempt {attempt}: {_safe_error(exc)}")
                    status_class = _retry_class(exc)
                    if status_class in {
                        "rate_limited",
                        "server_error",
                        "unknown_outcome_timeout",
                        "connection_error",
                        # 停滞 / 网关拒绝：都记一次短暂失败，使该 provider 在后续调用里被轮巡
                        # 降权（连续 2 次进入冷却），把流量推向仍健康的备用 provider。
                        "provider_stalled",
                        "gateway_rejected",
                    }:
                        self._record_provider_transient_failure(provider)
                    if status_class == "provider_stalled" and _is_wuyin_image_provider(provider):
                        # 无印四宫格停滞已在 _request_wuyin_image 内部完成「保持轮询 + 不同链路
                        # 对冲」（无不同链路时退化为「同链路有界重试」）：走到这里说明对冲/重试
                        # 与时长预算均已耗尽。继续用外层轮巡重试只会突破提交上限并重复计费，
                        # 故直接抛出，交由上层走既有可见回退（service.py 记 source_fallback +
                        # 失败明细）。消息末尾附上「提交追踪」，让 ai_notes 也能看出真实提交
                        # 次数与每次结果（service.py 会把它转成 four_grid:ai-failed: 备注）。
                        # 例外：池里还有「对冲覆盖不到」的同步协议链路（中转的 /images/edits）
                        # 时，改用外层轮巡把它试掉——直接抛会让这类备用永远没有机会，用户只能
                        # 看到「整批回退来源图」。
                        if attempt_count < max_total_attempts and any(
                            item is not provider and not _is_wuyin_image_provider(item)
                            for item in ordered_providers
                        ):
                            continue
                        raise MediaProcessingError(
                            "; ".join(errors)
                            + _submit_trace_suffix(submit_events, generation_started_at),
                            attempt_count=attempt_count,
                            status_class=status_class,
                        ) from exc
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
                    if status_class in {"provider_stalled", "gateway_rejected", "reference_relay_failed"}:
                        # 停滞 / 网关拒绝 / 参考图中转失败：都不是上游的显式终态失败，重试有
                        # 意义。先尽量把「换候选参考图」做满（换掉可能不合规或抓不到的参考），
                        # 随后 continue 会轮巡到下一个 provider；单 provider 时下一轮原地重试。
                        # 四宫格此前遇到停滞会立即放弃，导致客户看到「处理后仍是原图」；现在按
                        # provider 各试 2 次（受总预算约束），确保「换 provider + 换参考图」可组合。
                        has_budget = (
                            attempt_count < max_total_attempts
                            and generation_deadline - time.monotonic() > 1.0
                        )
                        remaining_candidates = candidate_values[reference_offset + 1 :]
                        if has_budget and not references_rotated and remaining_candidates:
                            try:
                                rotated = self._load_references(
                                    remaining_candidates, limit=reference_limit
                                )
                            except MediaProcessingError:
                                rotated = []
                            if rotated and rotated != ordinary_references:
                                reference_offset += 1
                                references_rotated = True
                                ordinary_references = rotated
                        if has_budget:
                            references = assemble_attempt_references(ordinary_references)
                            continue
                        raise MediaProcessingError(
                            "; ".join(errors),
                            attempt_count=attempt_count,
                            status_class=status_class,
                        ) from exc
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

    def split_four_grid(
        self,
        media: GeneratedMedia,
        *,
        enhance: EnhanceOptions | None = None,
        on_enhance_metrics: Callable[[EnhanceMetrics], None] | None = None,
    ) -> list[GeneratedMedia]:
        """Split a generated 2x2 grid into four listing images plus its summary.

        The original workbench uses this contract for 店小秘 carousel images.
        Splitting happens locally so it does not consume four extra image API calls.

        对齐原项目 native_product_engine._split_4grid_to_jpegs / _square_image_to_jpeg_bytes：
        每格仅 trim 1% 边距后缩放到 DXM_IMAGE_TARGET_SIZE(800×800)，汇总图居中裁方后缩放到 800×800。

        ``enhance`` 为显式 opt-in：默认 ``None`` 时**输出与历史逐字节一致**（product_processing
        与 POD 共用本方法，绝不能默默改变既有两个模块的成品）。传入参数时在编码之前做清晰化
        （默认 ONNX 4× 超分后退回目标尺寸，失败自动回退 USM 锐化）——全流程只编一次码。
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
            if enhance is not None and enhance.enabled:
                # 清晰化在 JPEG 编码之前，且全程只编一次码：ONNX 走"先 4× 超分再降到目标尺寸"，
                # 失败自动回落 USM 锐化（见 wh_local/media_enhance.py）。放缩放之前会被插值抹掉。
                resized = enhance_panel(
                    panel, target_size, enhance, on_metrics=on_enhance_metrics
                )
            else:
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

    def normalize_standalone_image(
        self,
        media: GeneratedMedia,
        *,
        stage: str,
        enhance: EnhanceOptions | None = None,
    ) -> GeneratedMedia:
        """Normalize one independently generated product image for carousel use.

        ``enhance`` 为显式 opt-in：默认 ``None`` 时**输出与历史逐字节一致**。传入参数时在
        JPEG 编码之前做清晰化（与 ``split_four_grid`` 共用同一套能力，见
        wh_local/media_enhance.py），全流程只编一次码。
        """
        try:
            from PIL import Image  # type: ignore

            source = Image.open(BytesIO(media.content)).convert("RGB")
            if min(source.size) < DXM_IMAGE_TARGET_SIZE:
                raise ValueError("standalone source is too small for marketplace output")
            square = _center_crop_to_square(source)
            if enhance is not None and enhance.enabled:
                # 面板已不小于目标尺寸时 enhance_panel 只做 LANCZOS 缩放 + 轻度 USM；
                # 只有在确实放大时才跑 ONNX 超分（失败自动回落），放缩放之前会被插值抹掉。
                resized = enhance_panel(square, DXM_IMAGE_TARGET_SIZE, enhance)
            else:
                resized = square.resize(
                    (DXM_IMAGE_TARGET_SIZE, DXM_IMAGE_TARGET_SIZE), Image.Resampling.LANCZOS
                )
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
        hedge_candidates: list[dict[str, str]] | None = None,
        submit_budget: "_GridSubmitBudget | None" = None,
        generation_deadline: float | None = None,
        submit_events: list[dict[str, Any]] | None = None,
        generation_started_at: float | None = None,
    ) -> tuple[bytes, str]:
        model = str(reference_model or provider.get("reference_model") or provider.get("model") or "").strip()
        if _is_wuyin_image_provider(provider):
            return self._request_wuyin_image(
                provider,
                prompt,
                references,
                timeout_seconds=timeout_seconds,
                image_size=image_size,
                model=model,
                hedge_candidates=hedge_candidates,
                submit_budget=submit_budget,
                generation_deadline=generation_deadline,
                submit_events=submit_events,
                generation_started_at=generation_started_at,
            )
        if _is_ark_image_provider(provider):
            return self._request_ark_image(
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
            # 带上上游原话：中转返回 HTTP 200 但不是可用的 data[0].url（限流 / 审核 /
            # 参数被拒等）时，只报「没有安全图片结果」会让排障无从下手——与无印提交
            # 路径一致地把上游原因带进错误信息。
            # 同时必须给出 status_class：否则 _retry_class 会落到 non_retryable_local，
            # 外层直接放弃（实测：上游 rate_limited 时 17s 就结束、一次退避重试都没有）。
            raise MediaProcessingError(
                "provider response does not contain a safe image result: "
                + _provider_message(payload),
                status_class=_submit_rejection_class(payload),
            )
        return _download_provider_result_image(url)

    def _request_ark_image(
        self,
        provider: dict[str, str],
        prompt: str,
        references: list[tuple[bytes, str, str] | tuple[bytes, str, str, str]],
        *,
        timeout_seconds: float,
        image_size: str | None = None,
    ) -> tuple[bytes, str]:
        """火山方舟官方生图（Seedream）：同步 JSON 一步出图。

        与该链路两个实测踩过的坑：
        - ``watermark`` 必须显式 False：方舟生图默认会打「豆包AI生成」水印，商品图不能带；
        - 参考图用 **Base64 data URI** 直传（``image`` 支持 URL 或 data URI），
          因此不必先把参考图传上公网，天然免疫「上游抓不到参考图」那类问题。
        """
        images: list[str] = []
        for reference in references:
            content, _filename, content_type = reference[:3]
            images.append(
                "data:"
                + (str(content_type) or "image/png")
                + ";base64,"
                + base64.b64encode(content).decode("ascii")
            )
        payload: dict[str, Any] = {
            "model": str(provider.get("reference_model") or provider.get("model") or "").strip(),
            "prompt": prompt,
            "size": _ark_image_size(image_size or provider.get("image_size")),
            "output_format": "png",
            "response_format": "url",
            "watermark": False,
        }
        if images:
            payload["image"] = images
        global_ai_request_limiter().acquire()
        response = _SESSION.post(
            f"{str(provider['base_url']).rstrip('/')}/images/generations",
            headers={
                "Authorization": f"Bearer {provider['api_key']}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=max(1.0, min(float(timeout_seconds), IMAGE_EDIT_REQUEST_TIMEOUT_SECONDS)),
            stream=True,
        )
        try:
            if not response.ok:
                raise MediaProcessingError(
                    f"provider returned HTTP {response.status_code}",
                    status_code=response.status_code,
                )
            response_payload = _bounded_response_json(response)
        finally:
            response.close()
        item = ((response_payload.get("data") or [{}])[0] if isinstance(response_payload, dict) else {})
        if not isinstance(item, dict):
            raise MediaProcessingError("provider response is incompatible")
        url = str(item.get("url") or "").strip()
        if not url or not is_safe_external_url(url):
            raise MediaProcessingError(
                "provider response does not contain a safe image result: "
                + _provider_message(response_payload),
                status_class=_submit_rejection_class(response_payload),
            )
        return _download_provider_result_image(url)

    def _request_wuyin_image(
        self,
        provider: dict[str, str],
        prompt: str,
        references: list[tuple[bytes, str, str] | tuple[bytes, str, str, str]],
        *,
        timeout_seconds: float,
        image_size: str | None = None,
        model: str = "",
        hedge_candidates: list[dict[str, str]] | None = None,
        submit_budget: "_GridSubmitBudget | None" = None,
        generation_deadline: float | None = None,
        submit_events: list[dict[str, Any]] | None = None,
        generation_started_at: float | None = None,
    ) -> tuple[bytes, str]:
        resolved_model = str(model or provider.get("model") or "").strip()
        if hedge_candidates is not None:
            # 四宫格 hedge 模式：由 _generate_with_limits 显式开启。
            return self._request_wuyin_image_hedged(
                provider,
                prompt,
                references,
                resolved_model=resolved_model,
                timeout_seconds=timeout_seconds,
                image_size=image_size,
                hedge_candidates=hedge_candidates,
                submit_budget=submit_budget,
                generation_deadline=generation_deadline,
                submit_events=submit_events,
                generation_started_at=generation_started_at,
            )
        # 既有直连路径（非四宫格 / 直接调用）：单任务提交 + 阻塞轮询。
        urls, temporary_store, temporary_references = self._wuyin_reference_urls(references)
        try:
            task_id = self._submit_wuyin_image_task(
                provider,
                prompt,
                urls,
                model=resolved_model,
                image_size=image_size,
                timeout_seconds=timeout_seconds,
            )
            result_url = self._poll_wuyin_image_result(
                provider,
                task_id,
                timeout_seconds=timeout_seconds,
                stall_threshold_seconds=_wuyin_stall_threshold_seconds(
                    resolved_model, image_size, self._config()
                ),
            )
            return _download_provider_result_image(result_url)
        finally:
            if temporary_store is not None:
                for temporary in temporary_references:
                    temporary_store.delete(temporary)

    def _request_wuyin_image_hedged(
        self,
        provider: dict[str, str],
        prompt: str,
        references: list[tuple[bytes, str, str] | tuple[bytes, str, str, str]],
        *,
        resolved_model: str,
        timeout_seconds: float,
        image_size: str | None,
        hedge_candidates: list[dict[str, str]],
        submit_budget: "_GridSubmitBudget | None",
        generation_deadline: float | None,
        submit_events: list[dict[str, Any]] | None = None,
        generation_started_at: float | None = None,
    ) -> tuple[bytes, str]:
        """四宫格生图对冲：交替轮询多个无印异步任务，先到先用。

        - 达到停滞阈值且尚无结果时，**不放弃原任务**，而是向「不同链路」（base_url 与
          api_key 均不同）的健康 provider 再提交一个任务，两条并行推进（交替轮询即可，
          不需要线程）。
        - 找不到「不同链路」目标（单链路场景）时退化为「同链路有界重试」：向同一条链路
          再提交至多 1 个任务（总计 ≤ GRID_IMAGE_SINGLE_LINK_MAX_SUBMITS = 2），原任务
          继续轮询、先到先用。避免单链路「停滞即干等到总预算耗尽」。
        - 提交总次数受 ``submit_budget`` 硬封顶（≤ GRID_IMAGE_MAX_SUBMITS）；同链路重试
          另有更紧的独立上限，绝不无限新增请求（避免同源重复计费）。
        - 总时长沿用 ``generation_deadline``，不会被 hedge/重试拉长；预算耗尽仍无结果即抛
          ``provider_stalled``，交给上层走既有可见回退。
        - 任一条任务先返回图片 URL 立即采用，并停止对其余任务的轮询与清理。
        - 传入 ``submit_events`` 时，记录每次真实上游提交的时间点与最终结果（出图/停滞/
          被拒/失败/被取代），供上层汇总成真实提交次数与可观测备注。
        """
        stall_threshold = _wuyin_stall_threshold_seconds(resolved_model, image_size, self._config())
        deadline = (
            float(generation_deadline)
            if generation_deadline is not None
            else time.monotonic()
            + max(10.0, min(float(timeout_seconds), IMAGE_EDIT_REQUEST_TIMEOUT_SECONDS))
        )
        hedge_pool = self._hedge_targets(provider, hedge_candidates)
        # hedge 只支持无印异步链路（提交 + detail 轮询）；池里若还有同步协议的中转链路
        # （OpenAI 兼容的 /images/edits），它永远进不了对冲，只能由外层轮巡接手。这里先
        # 标记出来：一旦本链路停滞，就提早把控制权交还外层，别把整段预算烧在必然停滞的
        # 链路上（实测：池里只有无印时，660s 预算会被停滞判定全部烧完，备用一次都没试）。
        cross_protocol_fallback = any(
            item is not provider and not _is_wuyin_image_provider(item)
            for item in (hedge_candidates or [])
        )
        # 单链路回退：没有任何「不同链路」目标时，允许向主链路再提交 1 次。该计数独立于
        # 全局 submit_budget，且更紧（最多 1 次额外提交 → 总计 2 次），避免同源重复计费。
        same_link_retry_remaining = (
            0 if hedge_pool else GRID_IMAGE_SINGLE_LINK_MAX_SUBMITS - 1
        )
        active: list[_WuyinImageTask] = []
        errors: list[str] = []

        def start_task(target: dict[str, str]) -> None:
            urls, store, temporary = self._wuyin_reference_urls(references)
            # 提交前先记一条「提交事件」：时间点即本次上游提交的时刻，结果随后回填。
            event: dict[str, Any] | None = None
            if submit_events is not None:
                event = {
                    "provider": str(target.get("name") or ""),
                    "at": time.monotonic(),
                    "outcome": "processing",
                }
                submit_events.append(event)
            try:
                task_id = self._submit_wuyin_image_task(
                    target,
                    prompt,
                    urls,
                    model=resolved_model,
                    image_size=image_size,
                    timeout_seconds=timeout_seconds,
                )
            except BaseException as exc:
                if store is not None:
                    for created in temporary:
                        store.delete(created)
                if event is not None:
                    event["outcome"] = _submit_outcome_from_error(exc)
                raise
            now = time.monotonic()
            active.append(
                _WuyinImageTask(
                    provider=target,
                    task_id=task_id,
                    model=resolved_model,
                    stall_threshold_seconds=stall_threshold,
                    started_at=now,
                    deadline=deadline,
                    store=store,
                    temporary_references=list(temporary),
                    last_progress_at=now,
                    event=event,
                )
            )

        try:
            if submit_budget is not None and not submit_budget.consume():
                raise MediaProcessingError(
                    "four-grid image submit budget exhausted", status_class="provider_stalled"
                )
            start_task(provider)
            hedge_index = 0
            while True:
                if not active:
                    raise MediaProcessingError(
                        "; ".join(errors) or "image provider request failed",
                        status_class="provider_stalled",
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 1.0:
                    # 预算耗尽仍无结果：仍在轮询的任务，其最终结果记为「停滞」。
                    for task in active:
                        _mark_submit_outcome(task, "stalled")
                    raise MediaProcessingError(
                        "; ".join(errors) or "provider image task stalled",
                        status_class="provider_stalled",
                    )
                time.sleep(min(WUYIN_IMAGE_POLL_INTERVAL_SECONDS, max(0.0, remaining)))
                for task in list(active):
                    try:
                        result_url = self._probe_wuyin_image_task(task)
                    except MediaProcessingError as exc:
                        errors.append(f"{task.provider.get('name', '')} task: {_safe_error(exc)}")
                        _mark_submit_outcome(task, _submit_outcome_from_error(exc))
                        active.remove(task)
                        continue
                    if result_url:
                        # 先到先用：本任务记为「出图」；其余仍在轮询的任务若尚未判停滞则记为
                        # 「被取代」（已判停滞的保留「停滞」——它确实长时间无进展）。
                        _mark_submit_outcome(task, "ok")
                        for other in active:
                            if other is not task and (other.event or {}).get("outcome") == "processing":
                                _mark_submit_outcome(other, "superseded")
                        return _download_provider_result_image(result_url)
                stalled_tasks = [task for task in active if self._wuyin_task_stalled(task)]
                if stalled_tasks:
                    # 达阈值但未判死：仅作为「触发再提交一次」的信号；各停滞任务记为「停滞」
                    # （若之后又出图会被回填为「出图」）。
                    for task in stalled_tasks:
                        _mark_submit_outcome(task, "stalled")
                    # 有跨协议备用可用、且没有任何可对冲的同协议链路时，立即交还控制权：
                    # 继续同链路重试/轮询只会把剩余预算耗尽，让备用彻底失去机会。
                    if cross_protocol_fallback and not hedge_pool:
                        raise MediaProcessingError(
                            "; ".join(errors) or "provider image task stalled",
                            status_class="provider_stalled",
                        )
                    # 优先对冲「不同链路」目标；没有不同链路（单链路场景）时，若同链路重试
                    # 预算尚有余量则退化为「向同一条链路再提交 1 次」（先到先用）。
                    if hedge_index < len(hedge_pool):
                        target: dict[str, str] | None = hedge_pool[hedge_index]
                    elif same_link_retry_remaining > 0:
                        target = provider
                    else:
                        target = None
                    if target is not None and (submit_budget is None or submit_budget.consume()):
                        if target is provider:
                            same_link_retry_remaining -= 1
                        else:
                            hedge_index += 1
                        try:
                            start_task(target)
                        except MediaProcessingError as exc:
                            errors.append(f"{target.get('name', '')} submit: {_safe_error(exc)}")
        finally:
            for task in active:
                if task.store is not None:
                    for created in task.temporary_references:
                        task.store.delete(created)

    def _hedge_targets(
        self,
        primary: dict[str, str],
        candidates: list[dict[str, str]] | None,
    ) -> list[dict[str, str]]:
        """筛选可用的对冲目标：链路（base_url 与 api_key）与主 provider 均不同。

        只挑无印异步链路（``_is_wuyin_image_provider``），因为 hedge 依赖异步提交 +
        detail 轮询；与主 provider 同上游（base_url 相同）或同密钥（api_key 相同）的
        候选一律排除，避免同源重复计费。``candidates`` 已由 _provider_order 过滤掉
        处于冷却中的 provider，因此这里拿到的都是「健康」provider。
        """
        if not candidates:
            return []
        primary_base = str(primary.get("base_url") or "")
        primary_key = str(primary.get("api_key") or "")
        seen: set[tuple[str, str]] = set()
        targets: list[dict[str, str]] = []
        for candidate in candidates:
            if candidate is primary or not _is_wuyin_image_provider(candidate):
                continue
            base = str(candidate.get("base_url") or "")
            key = str(candidate.get("api_key") or "")
            if base == primary_base or key == primary_key:
                continue
            signature = (base, key)
            if signature in seen:
                continue
            seen.add(signature)
            targets.append(candidate)
        return targets

    def _wuyin_task_stalled(self, task: _WuyinImageTask) -> bool:
        """任务是否已超过停滞阈值仍无字段变化（等价于「已运行很久」）。"""
        return bool(
            task.stall_threshold_seconds
            and task.last_signature is not None
            and time.monotonic() - task.last_progress_at >= task.stall_threshold_seconds
        )

    def _submit_wuyin_image_task(
        self,
        provider: dict[str, str],
        prompt: str,
        urls: list[str],
        *,
        model: str,
        image_size: str | None,
        timeout_seconds: float,
    ) -> str:
        """向无印提交一个异步生图任务，返回 task_id。"""
        resolved_model = str(model or provider.get("model") or "").strip()
        submit_path = _wuyin_image_submit_path(resolved_model)
        size_value = _wuyin_size(image_size or provider.get("image_size"))
        if resolved_model == "image_gpt_2.5":
            # 2.5 端点的 urls 要求逗号拼接字符串，传数组上游会返回 500；
            # 且不接受 quality 字段（传了会 500 存在未绑定的参数）。
            payload: dict[str, Any] = {
                "prompt": prompt,
                "aspectRatio": WUYIN_IMAGE_ASPECT_RATIO_2_5.get(size_value, "1024x1024"),
                "urls": ",".join(urls),
            }
        elif _is_wuyin_nano_banana(resolved_model):
            # 无印 nanoBanana 系：size 是画质档（1K/2K/4K），比例单独用 aspectRatio，
            # urls 与 image_gpt 一样传数组（见其接口文档）。
            payload = {
                "prompt": prompt,
                "size": _wuyin_image_tier(
                    image_size or provider.get("image_size"), model=resolved_model
                ),
                "aspectRatio": size_value,
                "urls": urls,
            }
        else:
            payload = {"prompt": prompt, "size": size_value, "urls": urls}
        global_ai_request_limiter().acquire()
        response = _SESSION.post(
            f"{provider['base_url']}{submit_path}",
            params={"key": provider["api_key"]},
            headers={
                "Authorization": provider["api_key"],
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=max(1.0, min(30.0, float(timeout_seconds))),
            stream=True,
        )
        try:
            if not response.ok:
                raise MediaProcessingError(
                    f"provider returned HTTP {response.status_code}",
                    status_code=response.status_code,
                )
            response_payload = _bounded_response_json(response)
        finally:
            response.close()
        if not isinstance(response_payload, dict) or int(response_payload.get("code") or 0) != 200:
            # 提交被上游拒绝：区分「计费/鉴权类硬失败」（重试无意义，可能重复计费）与
            # 「网关容量/参数类拒绝」（可重试）。后者交由上层轮换参考图 / 换 provider 再试，
            # 避免一次拒绝就把四宫格整条推给来源图。
            raise MediaProcessingError(
                f"provider submit failed: {_provider_message(response_payload)}",
                status_class=_submit_rejection_class(response_payload),
            )
        data = response_payload.get("data") or {}
        task_id = str(data.get("id") or data.get("task_id") or "").strip() if isinstance(data, dict) else ""
        if not task_id:
            raise MediaProcessingError("provider response does not contain image task id")
        return task_id

    def _wuyin_reference_urls(
        self,
        references: list[tuple[bytes, str, str] | tuple[bytes, str, str, str]],
    ) -> tuple[list[str], TemporaryCosStore | None, list[TemporaryReference]]:
        """Return provider-fetchable URLs, preferring a private COS relay.

        直连生图提供方需要自己去抓参考图，而 1688 / Temu 等源站图常有防盗链或出网策略
        差异，上游抓不到时会把异步任务静默丢弃（我方只能观测到长时间无进展，最终判停滞
        并回退来源图）。因此只要本地已有参考图字节且 COS 已配置，就优先上传私有 COS 换取
        一个上游一定能抓到的临时 URL，从源头避免这类停滞；COS 不可用时再退回原始公网 URL。
        临时对象由调用方在生成结束后删除（见 ``_request_wuyin_image`` 的 finally）。
        """
        config = self._config()
        relay_available = _cos_configured(config)

        # 先按参考图顺序排好槽位：能中转的走 COS，其余用原始公网 URL 兜底。
        slots: list[dict[str, Any]] = []
        for reference in references:
            candidate = str(reference[3]).strip() if len(reference) >= 4 else ""
            safe_candidate = candidate if candidate and is_safe_external_url(candidate) else ""
            content = bytes(reference[0]) if reference[0] else b""
            if content and relay_available:
                slots.append({
                    "bytes": content,
                    "content_type": str(reference[2] or "image/jpeg"),
                    "fallback": safe_candidate,
                })
            elif safe_candidate:
                slots.append({"url": safe_candidate})
            elif content:
                slots.append({
                    "bytes": content,
                    "content_type": str(reference[2] or "image/jpeg"),
                    "fallback": "",
                })

        relay_slots = [slot for slot in slots if "bytes" in slot]
        store: TemporaryCosStore | None = None
        temporary_references: list[TemporaryReference] = []
        if relay_slots:
            cos = dict(config.get("cos") or {})
            store = TemporaryCosStore(
                RuntimeCosConfig(
                    bucket=str(cos.get("bucket") or "").strip(),
                    region=str(cos.get("region") or "").strip(),
                    secret_id=str(cos.get("secret_id") or "").strip(),
                    secret_key=str(cos.get("secret_key") or "").strip(),
                )
            )
            for slot in relay_slots:
                try:
                    temporary = _publish_reference_with_retry(
                        store, slot["bytes"], slot["content_type"]
                    )
                except TemporaryReferenceError:
                    # 带退避重试仍失败：优先退回原始公网 URL（同一张图的可用替代），
                    # 连兜底都没有才标记为「参考图中转失败」交给上层轮换参考图 / 换 provider，
                    # 而不是一条失败就回退来源图。
                    if slot["fallback"]:
                        slot["url"] = slot["fallback"]
                        continue
                    for created in temporary_references:
                        store.delete(created)
                    raise MediaProcessingError(
                        "failed to relay local reference image to the direct image provider",
                        status_class="reference_relay_failed",
                    )
                temporary_references.append(temporary)
                slot["url"] = temporary.url

        urls = [str(slot.get("url") or "") for slot in slots if str(slot.get("url") or "")]
        if not urls:
            if store is not None:
                for created in temporary_references:
                    store.delete(created)
            raise MediaProcessingError("direct image provider requires a reference image")
        return urls, store, temporary_references

    def _poll_wuyin_image_result(
        self,
        provider: dict[str, str],
        task_id: str,
        *,
        timeout_seconds: float,
        stall_threshold_seconds: float = 0.0,
    ) -> str:
        # 单任务阻塞轮询（既有直连路径 / 直接调用）。上游没有进度字段，只能用「距上次
        # 字段发生变化的时间」当进展信号：阈值内不干预；超过阈值仍无任何变化才提前放弃。
        # 注意：任务执行期间 status 恒为 3，故「无字段变化超过阈值」实际等价于「任务已
        # 运行很久」。四宫格走的 hedge 路径不再据此丢弃任务，而是在此基础上并行追发
        # （见 _request_wuyin_image_hedged）；本方法保留给非四宫格与直接调用方。
        started_at = time.monotonic()
        deadline = started_at + max(10.0, min(float(timeout_seconds), IMAGE_EDIT_REQUEST_TIMEOUT_SECONDS))
        task = _WuyinImageTask(
            provider=provider,
            task_id=task_id,
            model="",
            stall_threshold_seconds=max(0.0, float(stall_threshold_seconds)),
            started_at=started_at,
            deadline=deadline,
            store=None,
            temporary_references=[],
            last_progress_at=started_at,
        )
        while time.monotonic() < deadline:
            time.sleep(WUYIN_IMAGE_POLL_INTERVAL_SECONDS)
            result_url = self._probe_wuyin_image_task(task)
            if result_url:
                return result_url
            if self._wuyin_task_stalled(task):
                raise MediaProcessingError(
                    f"provider image task stalled: no field change for "
                    f"{int(time.monotonic() - task.last_progress_at)}s "
                    f"status={task.last_status_value or 'unknown'} code={task.last_code} "
                    f"{task.last_message}",
                    status_class="provider_stalled",
                )
        raise MediaProcessingError(f"provider image task timed out: {task.last_message}")

    def _probe_wuyin_image_task(self, task: _WuyinImageTask) -> str | None:
        """对单个无印异步任务做一次 detail 探测。

        返回图片 URL 表示任务完成；返回 None 表示仍在处理中（由调用方按 stall 阈值决定
        是否对冲）；遇到上游显式终态失败则抛错。上游没有进度字段，用「status 是否变化」
        当进展信号（签名 ``(status_value, False)``）：任务执行期间 status 恒为 3，因此
        「长时间无字段变化」等价于「任务已运行很久」。
        """
        provider = task.provider
        response = _SESSION.get(
            f"{provider['base_url']}{WUYIN_IMAGE_DETAIL_PATH}",
            params={"key": provider["api_key"], "id": task.task_id},
            headers={"Authorization": provider["api_key"]},
            timeout=30,
            stream=True,
        )
        try:
            if not response.ok:
                task.last_message = f"detail HTTP {response.status_code}"
                return None
            payload = _bounded_response_json(response)
        finally:
            response.close()
        if not isinstance(payload, dict):
            task.last_message = "detail response is incompatible"
            return None
        code = int(payload.get("code") or 0)
        if code and code != 200:
            task.last_message = _provider_message(payload)
            if code in {400, 401, 403, 404}:
                raise MediaProcessingError(f"provider image task failed: {task.last_message}")
            return None
        data = payload.get("data") or {}
        status_value = (
            str(data.get("status") or payload.get("status") or "").strip().lower()
            if isinstance(data, dict)
            else ""
        )
        result_url = _first_image_url(data) or _first_image_url(payload)
        if result_url:
            return result_url
        message = _provider_message(payload)
        task.last_status_value = status_value
        task.last_code = code
        # 进展信号：detail 里 status 变动才算进展并重置计时。
        # 字段长期完全不变 = 大概率卡死（上游抓不到参考图时 worker 会静默丢弃任务）。
        signature = (status_value, False)
        if signature != task.last_signature:
            task.last_signature = signature
            task.last_progress_at = time.monotonic()
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
            task.last_message = message or f"status={status_value}"
            return None
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
        task.last_message = message or f"status={status_value or 'processing'}"
        return None


def _is_ark_image_provider(provider: dict[str, str]) -> bool:
    """是否火山方舟官方生图链路（Seedream）。"""
    host = urlsplit(str(provider.get("base_url") or "")).netloc.lower()
    return host == ARK_IMAGE_HOST_SUFFIX


def _ark_image_size(value: str | None) -> str:
    """像素尺寸 → 方舟支持的档位（1K / 1.5K / 2K / 4K）；默认 2K（四宫格按 2048 生成）。"""
    raw = str(value or "").strip().lower()
    if raw in {"4k", "4096x4096"} or raw.startswith("4096x"):
        return "4K"
    if raw in {"1k", "1024x1024"} or raw.startswith("1024x"):
        return "1K"
    if raw in {"1.5k", "1536x1536"}:
        return "1.5K"
    return ARK_IMAGE_SIZE_DEFAULT


def _is_wuyin_image_provider(provider: dict[str, str]) -> bool:
    return urlsplit(str(provider.get("base_url") or "")).netloc.lower() == "api.wuyinkeji.com"


def _plausible_public_http_url(value: str) -> bool:
    """Structural http(s) URL check that never touches DNS.

    Direct image providers need a publicly reachable source URL; the desktop
    only needs a syntactic sanity check.  Requiring a DNS round-trip here makes
    the request flaky under proxy TUN fake-ip setups.
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


def _normalize_model_key(value: str) -> str:
    """模型名归一化（小写、去掉 -/_/空格）：容忍 NanoBanana-2-Lite 这类书写差异。"""
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _wuyin_image_submit_path(model: str) -> str:
    """按模型名解析无印异步提交路径；未登记的模型回落 image_gpt 路径（保持旧行为）。"""
    raw = str(model or "").strip()
    direct = WUYIN_IMAGE_SUBMIT_PATHS.get(raw)
    if direct:
        return direct
    key = _normalize_model_key(raw)
    for name, path in WUYIN_IMAGE_SUBMIT_PATHS.items():
        if _normalize_model_key(name) == key:
            return path
    return WUYIN_IMAGE_SUBMIT_PATH


def _is_wuyin_nano_banana_1k_only(model: str) -> bool:
    """该 nanoBanana 型号是否只支持 1K 档（传 2K/4K 会被上游拒绝）。"""
    return _normalize_model_key(model) in WUYIN_NANO_BANANA_1K_ONLY


def _is_wuyin_nano_banana(model: str) -> bool:
    """是否无印 nanoBanana 系（Gemini 图像模型）。

    该系的请求体与 image_gpt 不同：size 传画质档（1K/2K/4K）而不是比例，比例用
    aspectRatio；URL 路径也不同（见 WUYIN_IMAGE_SUBMIT_PATHS）。大小写与分隔符
    以文档的 ``nanoBanana_pro`` 为准，这里做宽松匹配。
    """
    return str(model or "").strip().lower().replace("-", "_").startswith("nanobanana")


def _wuyin_image_tier(value: str | None, *, model: str = "") -> str:
    """像素尺寸 → 无印 nanoBanana 的画质档（1K/2K/4K）。

    默认 2K：四宫格固定按 2048x2048 生成，档位应与之一致；未知值也给 2K，
    避免误落到 1K 让四宫格每格只有 512px。但只支持 1K 的型号必须压到 1K，
    否则上游直接拒绝（400）。
    """
    if _is_wuyin_nano_banana_1k_only(model):
        return "1K"
    raw = str(value or "").strip().lower()
    if raw in {"4k", "4096x4096"} or raw.startswith("4096x"):
        return "4K"
    if raw in {"1k", "1024x1024"} or raw.startswith("1024x"):
        return "1K"
    return "2K"


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


def _positive_float(value: Any, default: float) -> float:
    """把可选的数值配置解析为正浮点；非法/缺省时回落到默认阈值。"""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


# 一次上游提交的最终结果标签（可观测性用，固定取值避免自由文本）。
#   ok         出图成功
#   stalled    停滞（达阈值/预算耗尽仍无图）
#   rejected   提交被上游网关/鉴权/参数拒绝（重试无意义）
#   failed     任务显式失败
#   superseded 先到先用：被其它提交的结果取代
_SUBMIT_OUTCOME_BY_STATUS: dict[str, str] = {
    "gateway_rejected": "rejected",
    "non_retryable_4xx": "rejected",
    "non_retryable_local": "rejected",
}


def _submit_outcome_from_error(error: BaseException) -> str:
    """把一次提交/轮询异常的 status_class 归类为最终结果标签。"""
    status_class = str(getattr(error, "status_class", "") or "")
    return _SUBMIT_OUTCOME_BY_STATUS.get(status_class, "failed")


def _mark_submit_outcome(task: Any, outcome: str) -> None:
    """回填某个任务的提交事件最终结果（无事件记录时为 no-op）。"""
    event = getattr(task, "event", None)
    if event is not None:
        event["outcome"] = outcome


def _submit_trace_notes(
    submit_events: list[dict[str, Any]] | None, generation_started_at: float
) -> tuple[str, ...]:
    """把一次生成的多次提交压成紧凑备注；仅当确实发生多次提交时才返回非空。

    每条备注形如 ``s1@0ms=stalled``、``submits=2``、``total=419880ms``，不含任何原始响应体。
    """
    events = list(submit_events or [])
    if len(events) <= 1:
        return ()
    notes: list[str] = []
    for index, event in enumerate(events, start=1):
        at_ms = max(
            0,
            round(
                (float(event.get("at", generation_started_at)) - generation_started_at) * 1000
            ),
        )
        notes.append(f"s{index}@{at_ms}ms={event.get('outcome') or 'processing'}")
    notes.append(f"submits={len(events)}")
    notes.append(f"total={max(0, round((time.monotonic() - generation_started_at) * 1000))}ms")
    return tuple(notes)


def _submit_trace_status_class(status_class: str, attempt_notes: tuple[str, ...]) -> str:
    """把多次提交的追踪并入 provider_status_class（result_json 唯一可控的字符串通道）。

    单次提交（或未产生追踪的普通路径）保持原类名，不改变既有语义；只有真正发生多次提交
    时才追加 ``;s1@...=...`` 形式的追踪，便于排障时看出真实提交次数与每次结果。
    """
    if not attempt_notes:
        return status_class
    return ";".join([str(status_class), *attempt_notes])


def _submit_trace_suffix(
    submit_events: list[dict[str, Any]] | None, generation_started_at: float
) -> str:
    """失败路径用：把提交追踪拼进异常消息，使其随 ``ai-failed`` 备注进入 ai_notes。"""
    notes = _submit_trace_notes(submit_events, generation_started_at)
    if not notes:
        return ""
    return f" [提交追踪:{';'.join(notes)}]"


def _wuyin_stall_threshold_seconds(
    model: str, image_size: str | None, config: dict[str, Any] | None = None
) -> float:
    """按端点与尺寸档位选停滞阈值：1K 与 2K/4K 的真实生成耗时差异明显。

    2.5 端点恒为 1K；旧端点 image_gpt 的 2K/4K transport 更慢，用更宽的阈值避免误杀。
    两档阈值均可由 ``limits.image_stall_threshold_seconds`` /
    ``limits.image_stall_threshold_2k_seconds`` 覆盖（现场按上游波动调节）。
    """
    limits = (config or {}).get("limits") or {}
    if str(model or "").strip() == "image_gpt_2.5":
        return _positive_float(
            limits.get("image_stall_threshold_seconds"), WUYIN_IMAGE_STALL_THRESHOLD_SECONDS
        )
    raw_size = str(image_size or "").strip().lower()
    if raw_size in {"2048x2048", "4096x4096", "2048x1024", "1024x2048"}:
        return _positive_float(
            limits.get("image_stall_threshold_2k_seconds"), WUYIN_IMAGE_STALL_THRESHOLD_2K_SECONDS
        )
    return _positive_float(
        limits.get("image_stall_threshold_seconds"), WUYIN_IMAGE_STALL_THRESHOLD_SECONDS
    )


def _publish_reference_with_retry(
    store: TemporaryCosStore, content: bytes, content_type: str
) -> TemporaryReference:
    """把参考图发布到私有 COS，换取上游可抓取的临时 URL；带小步退避重试。

    COS 发布是瞬时抖动敏感的网络动作，且失败会直接让无印抓不到参考图而静默卡死，
    因此允许少量带退避重试，避免一次抖动升级为整条生成失败。
    """
    attempts = max(1, int(COS_RELAY_PUBLISH_ATTEMPTS))
    for attempt in range(1, attempts + 1):
        try:
            temporary = store.publish(content, content_type)
            if not _plausible_public_http_url(temporary.url):
                raise TemporaryReferenceError(
                    "temporary COS reference did not return a provider-fetchable URL"
                )
            return temporary
        except TemporaryReferenceError:
            if attempt >= attempts:
                raise
            backoff = COS_RELAY_PUBLISH_BACKOFF_SECONDS
            time.sleep(backoff[min(attempt - 1, len(backoff) - 1)])
    raise TemporaryReferenceError("failed to publish temporary reference image")


def _cos_configured(config: dict[str, Any]) -> bool:
    cos = dict(config.get("cos") or {})
    return bool(
        cos.get("bucket") and cos.get("region") and cos.get("secret_id") and cos.get("secret_key")
    )


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
    # 400 字：上游错误体常是嵌套 JSON（如 {"error":{"attempts":3,"code":"upstream_..."}}），
    # 180 字会把 code 截在半截，排障时看不出真实原因。
    limit = 400
    if not isinstance(payload, dict):
        return "provider response is incompatible"
    for key in ("msg", "message", "error", "reason"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:limit]
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("msg", "message", "error", "reason"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:limit]
    try:
        return json.dumps(payload, ensure_ascii=False)[:limit]
    except Exception:
        return "provider returned an error"


def _submit_rejection_class(payload: Any) -> str:
    """区分「计费/鉴权类硬失败」与「网关容量/参数类可重试拒绝」。

    上游（如 ``server image gateway rejected the request``）在提交阶段返回非 200 时，
    若属欠费、密钥无效、无权限等业务性拒绝则重试无意义（且存在重复计费风险），判不可重试；
    其余拒绝多为网关过载 / 参考图格式或参数不被接受，标记为可重试，交由上层轮换参考图 /
    换 provider 再试一次，而不是一条拒绝就回退来源图。
    """
    message = _provider_message(payload).lower()
    for keyword in (
        "欠费",
        "余额",
        "额度",
        "无权限",
        "未授权",
        "密钥",
        "payment",
        "insufficient",
        "forbidden",
        "unauthorized",
        "invalid key",
        "api key",
        "api_key",
        # 内容审核（如方舟 InputTextSensitiveContentDetected）：同一份内容重试必然再被拒，
        # 归为不可重试，避免把预算耗在注定失败的请求上。
        "sensitive",
        "敏感",
    ):
        if keyword in message:
            return "non_retryable_4xx"
    # 上游限流（如中转返回 {"error":{"code":"upstream_rate_limited"}}）：退避重试有意义，
    # 必须与普通网关拒绝区分开，否则会落到「不可重试」而被直接放弃。
    for keyword in ("rate limit", "rate_limit", "ratelimit", "限流", "too many requests", "并发", "繁忙", "busy"):
        if keyword in message:
            return "rate_limited"
    return "gateway_rejected"


def _safe_error(error: BaseException) -> str:
    # 400 字：这里截断后的字符串会进入 ai_notes / 失败诊断上报，是排障唯一的证据来源。
    # 180 字会把上游嵌套错误体（如 {"error":{"attempts":3,"code":"upstream_..."}}）截断在
    # code 中间，导致看不到真实原因。
    message = str(error).replace("\n", " ").strip()
    return message[:400] or error.__class__.__name__


def _retry_class(error: BaseException) -> str:
    status = getattr(error, "status_code", None)
    explicit_class = str(getattr(error, "status_class", "") or "")
    if explicit_class == "provider_stalled":
        # 停滞不是上游显式失败，可通过 COS 中转参考图 / 换候选参考图后重试一次。
        return "provider_stalled"
    if explicit_class in {"gateway_rejected", "reference_relay_failed"}:
        # 网关拒绝 / 参考图中转失败：先轮换参考图或换 provider 再试，避免一次失败就回退来源图。
        return explicit_class
    if explicit_class == "rate_limited":
        # 显式标记的限流同样要退避重试；不显式处理会掉进末尾的 non_retryable_local。
        return "rate_limited"
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
    try:
        return _save_jpeg(image, quality=quality, optimize=True)
    except OSError:
        # 既有 Pillow/libjpeg 缺陷：极高频内容（1px 细线、密集雕刻纹理）下
        # optimize=True 的霍夫曼优化会抛 "broken data stream when writing image
        # file"（stderr: "Suspension not allowed here"）。optimize 只影响编码体积
        # 与耗时，**不影响画质**（quality / subsampling 均不变），故回退到
        # optimize=False 重编一次，避免生图被编码器 bug 整批打断。实测 393 张
        # 真实母图中 2 张会命中，锐化后高频更强、命中面更大，必须兜底。
        return _save_jpeg(image, quality=quality, optimize=False)


def _save_jpeg(image: Any, *, quality: int, optimize: bool) -> bytes:
    buffer = BytesIO()
    image.save(
        buffer,
        format="JPEG",
        quality=quality,
        subsampling=0,
        optimize=optimize,
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
