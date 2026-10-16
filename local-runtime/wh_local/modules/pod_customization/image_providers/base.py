"""POD 生图平台的公共协议与共享工具。

各平台实现（速创 / 豆包）只负责「把一次四宫格生图请求打出去、把图片字节拿回来」。
并发槽位、限流令牌、参考图发布、结果下载等执行资源由 host
（:class:`~wh_local.modules.pod_customization.ai_runtime.PodCustomizationAiRuntime`）
提供，因此两个平台共用同一套容量预算、错误分类与计费口径。
"""

from __future__ import annotations

import base64
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from ..runtime_contracts import SUPPORTED_TEMPLATE_IMAGE_CONTENT_TYPES


@dataclass(frozen=True)
class GridReference:
    """一张本地参考图。

    怎么用由平台决定：速创要求公网 URL（host 负责发布到 COS），
    豆包支持 base64 data URL（直接内嵌，不经过 COS）。
    """

    content: bytes
    content_type: str


@dataclass(frozen=True)
class GridImageRequest:
    """一次四宫格生图请求（平台无关部分）。

    参考图不在其中：它由 :meth:`GridImageProvider.prepare` 单独准备，因为速创要先
    把本地图发布到 COS，而这一步刻意发生在占用生图槽位之前。
    """

    prompt: str
    size: str
    trial_id: str


@dataclass(frozen=True)
class GridImageResult:
    content: bytes
    content_type: str


class GridProviderHost(Protocol):
    """平台实现可用的执行资源（由 POD 运行时提供）。"""

    @property
    def session(self) -> Any: ...

    def ensure_open(self) -> None: ...

    def acquire_request_token(self) -> None: ...

    def connection_slot(self, timeout_seconds: float) -> AbstractContextManager[None]: ...

    def interruptible_wait(self, timeout_seconds: float) -> None: ...

    def publish_reference(self, content: bytes, content_type: str, *, namespace: str) -> str:
        """把本地参考图发布成上游可访问的公网地址（速创需要）。"""
        ...

    def download_result(self, result_url: str) -> tuple[bytes, str]:
        """下载并校验上游返回的图片地址。"""
        ...


class GridImageProvider(Protocol):
    """一个生图平台的最小接口。"""

    #: 平台标识（写进 GeneratedMedia.provider，也是前端选项值）。
    name: str
    #: 该平台使用冻结下发的哪个 provider key（速创=wuyin，豆包=ark）。
    grant_provider: str

    def prepare(
        self,
        host: GridProviderHost,
        references: tuple[GridReference, ...],
        *,
        namespace: str,
    ) -> Any:
        """把本地参考图变成该平台能直接用的形式（返回值对本协议不透明）。

        速创 → 公网 URL 列表；豆包 → base64 data URL 列表。
        """
        ...

    def generate(
        self,
        host: GridProviderHost,
        request: GridImageRequest,
        prepared: Any,
        *,
        model: str,
        api_key: str,
        on_start: Callable[[], None] | None = None,
    ) -> GridImageResult:
        """执行一次生图。``on_start`` 在真正发起上游请求前回调（计费起点）。"""
        ...


def image_data_url(content: bytes, content_type: str = "image/png") -> str:
    """把图片字节转成上游可接受的 base64 data URL（格式必须小写）。"""
    normalized = str(content_type or "").strip().lower()
    if normalized not in SUPPORTED_TEMPLATE_IMAGE_CONTENT_TYPES:
        raise ValueError("POD reference image must be JPEG, PNG, or WEBP")
    return f"data:{normalized};base64," + base64.b64encode(content).decode("ascii")


def suffix_for_content_type(content_type: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(str(content_type or "").strip().lower(), ".png")
