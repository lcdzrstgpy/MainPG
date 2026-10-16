"""豆包 Seedream（火山方舟）生图平台。

与速创的差异（因此单独成文件）：
- **同步**接口：一次 ``POST /api/v3/images/generations`` 直接返回结果，没有 task_id
  与轮询；
- 参考图走 ``image`` 字段（单图字符串、多图数组），并且**支持 base64 data URL**，
  所以本地参考图不需要先发布到 COS 公网；
- ``size`` 用分辨率档位（1K/1.5K/2K）或显式「宽x高」，不是速创的 ``aspectRatio`` 比值。

凭据复用 POD 冻结时下发的 ``ark`` provider key（标题/识图链路本来就用它）。
"""

from __future__ import annotations

import base64
from typing import Any, Callable

from wh_local.modules.product_processing.infrastructure.media import MediaProcessingError

from ....runtime_logs import business_logger

from ..errors import PodProviderResultReceivedError, safe_error_message
from .base import (
    GridImageProvider,
    GridImageRequest,
    GridImageResult,
    GridProviderHost,
    GridReference,
    image_data_url,
)

ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
ARK_IMAGE_PATH = "/images/generations"
# Seedream 5.0 pro/flash 显式像素的总量区间（宽高各自还要满足宽高比 [1/16, 16]）。
ARK_MIN_TOTAL_PIXELS = 921_600
ARK_MAX_TOTAL_PIXELS = 4_624_220
# 四宫格母图必须是方阵（本地要切成 2×2），档位模式下比例交给模型判断容易跑偏；
# 显式给 2048×2048 既保证方形、又落在合法像素区间内。
# 选 2K 而非 1K 的两个理由：
#   1) 拆格后每格 1024px：速创现网母图实测约 1254×1254（每格约 627px），1K 会退回 512px 更糊；
#   2) 1024 ≥ 本地 800 交付尺寸，后处理**直接降采样、跳过 ONNX 4× 超分**（见
#      media_enhance._should_upscale），比 1K/1.5K 反而更省 CPU。
# 想省钱可改成 "1536x1536"（1.5K，官方口径与 1K 同价）。
ARK_SQUARE_SIZE = "2048x2048"
# 非方形请求交给档位 + 提示词描述比例。
ARK_RESOLUTION_TIER = "2K"
# 同步调用只有一次往返：2K 出图实测可能上百秒，单次请求给足上限。
ARK_RESULT_TIMEOUT_SECONDS = 180.0
# 单次上游调用的错误再试语义由 worker 决定，这里只负责分类。
ARK_RETRYABLE_STATUS = frozenset({408, 409, 425, 429})

# ---- 提示词净化（只作用于豆包） ----
# Ark 会对「输入文本」做敏感内容审核。POD 提示词为了约束模型，逐字枚举了品牌/版权/
# 违禁内容的排除清单（见 brief_runtime._SAFETY_EXCLUDED_BASELINE），审核会把这串
# 「禁止生成」的词当成违规输入，直接返回 400 InputTextSensitiveContentDetected
# （实测同一款重试也可能继续被拦，靠重试不可靠）。
# 所以豆包链路在提交前把这一行净化：命中敏感词的条目换成一句中性表述，非敏感的
# 自定义排除项（如「深色高饱和色块」）原样保留。速创链路一字不动。
_ARK_EXCLUDED_LINE_LABEL = "Excluded elements:"
# 替代文案本身必须**不含**任何敏感词，否则净化就白做了。
_ARK_NEUTRAL_EXCLUSIONS = "（其余项按平台合规清单排除）"
_ARK_SENSITIVE_EXCLUSION_TOKENS: tuple[str, ...] = (
    "品牌", "logo", "商标", "版权", "球队", "联盟", "影视", "动漫", "游戏角色",
    "卡通", "肖像", "签名", "奢侈", "老花", "水印", "海报", "封面", "钞票", "货币",
    "证件", "身份证", "武器", "弹药", "刀具", "爆炸", "毒品", "吸毒", "赌博", "老虎机",
    "烟草", "电子烟", "酒精", "暴力", "血腥", "恐怖", "仇恨", "纳粹", "宗教", "政治",
    "国旗", "国徽", "色情", "裸露", "虐待", "二维码", "条形码", "真人", "医疗", "药品",
)


class DoubaoSeedreamImageProvider:
    """豆包 Seedream 同步生图：一次请求直接拿结果。"""

    name = "doubao"
    grant_provider = "ark"

    def __init__(self, *, result_timeout_seconds: float = ARK_RESULT_TIMEOUT_SECONDS) -> None:
        self._result_timeout_seconds = max(1.0, float(result_timeout_seconds))

    def prepare(
        self,
        host: GridProviderHost,
        references: tuple[GridReference, ...],
        *,
        namespace: str,
    ) -> list[str]:
        """豆包支持 base64 内嵌，直接把本地字节转成 data URL，不经过 COS。"""
        try:
            return [
                image_data_url(reference.content, reference.content_type)
                for reference in references
            ]
        except ValueError as exc:
            raise MediaProcessingError(
                "POD 参考图必须是 JPEG/PNG/WEBP", status_class="non_retryable_local"
            ) from exc

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
        body: dict[str, Any] = {
            "model": model,
            "prompt": request.prompt,
            "size": ark_size(request.size),
            "response_format": "url",
            # 成品图要上架，不能带官方的「AI生成」水印。
            "watermark": False,
            # 官方示例对这个模型带 stream=false；如上游后续报「不支持该参数」再去掉。
            "stream": False,
        }
        inline_references = [str(value) for value in (prepared or [])]
        if inline_references:
            # 单图传字符串、多图传数组，与官方示例一致。
            body["image"] = (
                inline_references[0] if len(inline_references) == 1 else inline_references
            )
        body["prompt"] = sanitize_prompt_for_ark(request.prompt)
        business_logger("pod_processing").info(
            "POD 豆包生图提交 | trial_id=%s | model=%s | endpoint=%s | size=%s | 参考图=%d",
            request.trial_id,
            model,
            f"{ARK_BASE_URL}{ARK_IMAGE_PATH}",
            body["size"],
            len(inline_references),
        )

        host.acquire_request_token()
        with host.connection_slot(timeout_seconds=30.0):
            host.ensure_open()
            if on_start is not None:
                on_start()
            host.ensure_open()
            response = host.session.post(
                f"{ARK_BASE_URL}{ARK_IMAGE_PATH}",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=self._result_timeout_seconds,
                allow_redirects=False,
            )
            try:
                status_code = int(response.status_code)
                detail = _response_detail(response)
                if not 200 <= status_code < 300:
                    raise MediaProcessingError(
                        f"豆包生图返回 HTTP {status_code}" + (f"：{detail}" if detail else ""),
                        status_code=status_code,
                        attempt_count=1,
                        status_class=_ark_status_class(status_code),
                    )
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise MediaProcessingError(
                        "豆包生图返回的不是合法 JSON",
                        attempt_count=1,
                        status_class="invalid_response",
                    ) from exc
            finally:
                response.close()
        outcome = _ark_image_outcome(payload)
        if isinstance(outcome, MediaProcessingError):
            raise outcome
        if outcome.b64_json:
            return _b64_result(outcome)
        try:
            content, content_type = host.download_result(outcome.url)
        except Exception as exc:
            raise PodProviderResultReceivedError(
                "ark",
                f"豆包已返回结果，但本地下载或解析失败：{safe_error_message(exc)}",
            ) from exc
        return GridImageResult(content=content, content_type=content_type)


class _ArkImageOutcome:
    """Ark 单图结果：要么是下载地址，要么是内联 base64。"""

    __slots__ = ("url", "b64_json", "output_format")

    def __init__(self, *, url: str, b64_json: str, output_format: str) -> None:
        self.url = url
        self.b64_json = b64_json
        self.output_format = output_format


def _ark_image_outcome(payload: Any) -> "_ArkImageOutcome | MediaProcessingError":
    """从 Ark 响应里取第一张图；失败时返回可直接抛出的分类错误。"""
    if not isinstance(payload, dict):
        return MediaProcessingError(
            "豆包生图返回格式不兼容", attempt_count=1, status_class="invalid_response"
        )
    top_error = _ark_error_message(payload.get("error"))
    if top_error:
        return MediaProcessingError(
            f"豆包生图失败：{top_error}", attempt_count=1, status_class="non_retryable_4xx"
        )
    items = payload.get("data")
    first = items[0] if isinstance(items, list) and items else None
    if not isinstance(first, dict):
        return MediaProcessingError(
            "豆包生图返回中没有图片数据", attempt_count=1, status_class="invalid_response"
        )
    item_error = _ark_error_message(first.get("error"))
    if item_error:
        return MediaProcessingError(
            f"豆包生图失败：{item_error}", attempt_count=1, status_class="non_retryable_4xx"
        )
    url = str(first.get("url") or "").strip()
    b64_json = str(first.get("b64_json") or "").strip()
    if not url and not b64_json:
        return MediaProcessingError(
            "豆包生图返回中没有图片地址", attempt_count=1, status_class="invalid_response"
        )
    return _ArkImageOutcome(
        url=url,
        b64_json=b64_json,
        output_format=str(first.get("output_format") or "").strip().lower(),
    )


def _b64_result(outcome: _ArkImageOutcome) -> GridImageResult:
    try:
        content = base64.b64decode(outcome.b64_json)
    except (ValueError, TypeError) as exc:
        raise MediaProcessingError(
            "豆包返回的图片数据无法解码", attempt_count=1, status_class="invalid_response"
        ) from exc
    return GridImageResult(
        content=content,
        content_type="image/png" if outcome.output_format == "png" else "image/jpeg",
    )


def _ark_error_message(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    parts = [
        str(value.get("message") or "").strip(),
        str(value.get("code") or "").strip(),
    ]
    return " ".join(part for part in parts if part)[:200]


def _response_detail(response: Any) -> str:
    """尽力取一小段响应正文用于报错（测试替身没有 text 时静默跳过）。"""
    try:
        return str(getattr(response, "text", "") or "")[:240]
    except Exception:  # noqa: BLE001 - 诊断字段永不阻断主流程
        return ""


def _ark_status_class(status_code: int) -> str:
    """Ark HTTP 状态 → 重试语义：限流/瞬时抖动可重试，其余 4xx 直接判死，5xx 属瞬时。"""
    if status_code in ARK_RETRYABLE_STATUS:
        return "rate_limited" if status_code == 429 else "transient"
    if 400 <= status_code < 500:
        return "non_retryable_4xx"
    return "transient"


def _is_ark_sensitive_exclusion(item: str) -> bool:
    lowered = str(item or "").strip().casefold()
    if not lowered:
        return True
    return any(token in lowered for token in _ARK_SENSITIVE_EXCLUSION_TOKENS)


def sanitize_prompt_for_ark(prompt: str) -> str:
    """净化提示词中会触发 Ark 输入审核的违禁词枚举。

    只动 ``Excluded elements:`` 这一行：命中敏感词的条目剔除，换成一句中性表述；
    非敏感的自定义排除项（如「深色高饱和色块」）原样保留。其余内容逐字不改。
    """
    out: list[str] = []
    for line in str(prompt or "").split("\n"):
        stripped = line.strip()
        if not stripped.startswith(_ARK_EXCLUDED_LINE_LABEL):
            out.append(line)
            continue
        items = [
            item.strip()
            for item in stripped[len(_ARK_EXCLUDED_LINE_LABEL):].strip().rstrip(".").split(",")
            if item.strip()
        ]
        kept = [item for item in items if not _is_ark_sensitive_exclusion(item)]
        dropped = len(items) - len(kept)
        if not dropped:
            # 这一行本来就是干净的：一字不动。
            out.append(line)
            continue
        kept.append(_ARK_NEUTRAL_EXCLUSIONS)
        business_logger("pod_processing").info(
            "POD 豆包提示词净化 | 剔除敏感排除项=%d | 保留自定义项=%d", dropped, len(kept) - 1
        )
        out.append(f"{_ARK_EXCLUDED_LINE_LABEL} {', '.join(kept)}.")
    return "\n".join(out)


def ark_size(value: str | None) -> str:
    """把 POD 的尺寸意图映射成 Ark 的 ``size``。

    POD 只关心「方阵母图」（本地切 2×2 再统一交付 800），所以：
    - 方阵/未指定（含 POD 的默认 ``1024x1024``）→ **统一给 :data:`ARK_SQUARE_SIZE`**：
      Ark 的 1K 是实打实的 1024×1024，照字面透传会让拆格后面板从 627px 缩到 512px、
      比速创现网更糊；
    - 显式非方形宽高且在合法像素区间内 → 原样透传；
    - 比值字符串（``4:3`` 等）→ 交给档位，比例由提示词描述。
    """
    raw = str(value or "").strip().lower()
    if raw in {"", "auto", "1:1"}:
        return ARK_SQUARE_SIZE
    width_height = raw.split("x", 1)
    if len(width_height) != 2:
        return ARK_RESOLUTION_TIER
    try:
        width, height = int(width_height[0]), int(width_height[1])
    except ValueError:
        return ARK_RESOLUTION_TIER
    if width <= 0 or height <= 0:
        return ARK_SQUARE_SIZE
    if width == height:
        return ARK_SQUARE_SIZE
    if ARK_MIN_TOTAL_PIXELS <= width * height <= ARK_MAX_TOTAL_PIXELS:
        return f"{width}x{height}"
    return ARK_RESOLUTION_TIER


__all__ = [
    "ARK_BASE_URL",
    "ARK_RESULT_TIMEOUT_SECONDS",
    "DoubaoSeedreamImageProvider",
    "ark_size",
    "sanitize_prompt_for_ark",
]
