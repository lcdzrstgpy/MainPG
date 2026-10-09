"""电商/POD 图片"清晰化"：重采样之后的轻度锐化（反锐化掩模 USM）。

**能力边界（务必理解）**：这是经典图像处理，不是 AI 超分。
- `UnsharpMask` = 「原图 − 高斯模糊版 = 边缘」再把边缘按强度加回去，只提高**边缘对比度**；
- 它**不产生新细节**，信息量上限等于源图信息量。

典型用途：上游母图 1254×1254 拆四格后每格原生约 627×627，被插值放大到
800×800 会发软；本模块把这种"放大发软"在观感上补回来（默认轻度，避免细线
稿出现白边光晕）。

环境变量（非法值一律回退默认并告警，**绝不抛错阻断生图**）：
- `WH_POD_IMAGE_ENHANCE`          1/0，默认 1（开）
- `WH_POD_IMAGE_ENHANCE_RADIUS`   默认 1.0
- `WH_POD_IMAGE_ENHANCE_PERCENT`  默认 70
- `WH_POD_IMAGE_ENHANCE_THRESHOLD` 默认 3
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from PIL import Image, ImageFilter

import numpy as np

logger = logging.getLogger(__name__)

ENV_ENABLED = "WH_POD_IMAGE_ENHANCE"
ENV_RADIUS = "WH_POD_IMAGE_ENHANCE_RADIUS"
ENV_PERCENT = "WH_POD_IMAGE_ENHANCE_PERCENT"
ENV_THRESHOLD = "WH_POD_IMAGE_ENHANCE_THRESHOLD"

# 默认「轻度」：只补回插值放大造成的边缘发软。细线/雕刻类图案过锐会出白边光晕，
# 因此强度刻意保守；需要更强时用环境变量单独调，不改代码。
DEFAULT_RADIUS = 1.0
DEFAULT_PERCENT = 70
DEFAULT_THRESHOLD = 3

# 视为"关闭"的取值；空值按未设置处理（即保持默认开启）。
_FALSY = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class EnhanceOptions:
    """清晰化参数；`enabled=False` 时为完全无副作用的空操作。"""

    enabled: bool = True
    radius: float = DEFAULT_RADIUS
    percent: int = DEFAULT_PERCENT
    threshold: int = DEFAULT_THRESHOLD


def sharpen(image: Image.Image, options: EnhanceOptions) -> Image.Image:
    """对**已重采样到目标尺寸**的图片做锐化（务必在缩放之后、编码之前调用）。"""

    if not options.enabled:
        return image
    return image.filter(
        ImageFilter.UnsharpMask(
            radius=options.radius,
            percent=options.percent,
            threshold=options.threshold,
        )
    )


def sharpness_score(image: Image.Image) -> float:
    """锐度指标：拉普拉斯方差（教科书式对焦/清晰度度量）。

    取灰度图的 4 邻域拉普拉斯响应后求方差——细节越多、边缘越陡，方差越大。
    只用于前后对比与测试断言，不参与生产决策。
    """

    values = np.asarray(image.convert("L"), dtype=np.float64)
    if values.shape[0] < 3 or values.shape[1] < 3:
        return 0.0
    laplacian = (
        values[:-2, 1:-1]
        + values[2:, 1:-1]
        + values[1:-1, :-2]
        + values[1:-1, 2:]
        - 4.0 * values[1:-1, 1:-1]
    )
    return float(laplacian.var())


def _read_positive_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("%s=%r 不是合法数字，已回退默认 %s", name, raw, default)
        return default
    if value <= 0:
        logger.warning("%s=%r 必须为正数，已回退默认 %s", name, raw, default)
        return default
    return value


def _read_positive_int(name: str, default: int) -> int:
    return int(round(_read_positive_float(name, float(default))))


def options_from_env() -> EnhanceOptions:
    """读取清晰化配置。任何非法输入都回退默认，绝不因配置问题阻断生图。"""

    raw_enabled = (os.environ.get(ENV_ENABLED) or "").strip().lower()
    return EnhanceOptions(
        enabled=raw_enabled not in _FALSY,
        radius=_read_positive_float(ENV_RADIUS, DEFAULT_RADIUS),
        percent=_read_positive_int(ENV_PERCENT, DEFAULT_PERCENT),
        threshold=_read_positive_int(ENV_THRESHOLD, DEFAULT_THRESHOLD),
    )
