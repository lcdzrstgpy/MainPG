"""电商/POD 图片“清晰化”：ONNX 超分重建细节，失败自动回落轻度锐化。

默认走 **ONNX Real-ESRGAN**（`models/realesr-general-x4v3.onnx`，4× 超分后降采样回
目标尺寸）：上游母图 1254×1254 拆四格后每格原生约 627×627，插值放大到 800×800 会
发软，超分能把插值丢掉的纹理边界重建回来，观感明显比单纯锐化干净。

**失败兜底**：缺模型 / onnxruntime 不可用 / 输入过大 / 推理异常时，自动回退经典 USM
（`UnsharpMask`），保证生图永不中断。`WH_POD_IMAGE_ENHANCE_MODE=usm` 可强制只做锐化。

能力边界：
- ONNX 超分是**生成式重建**，会改变像素内容（对 POD 展示图实测忠实）；
- USM 是纯滤波：`原图 − 高斯模糊 = 边缘`，再按强度加回，只提**边缘对比度**、不产生新细节。

环境变量（非法值一律回退默认并告警，**绝不抛错阻断生图**）：
- `WH_POD_IMAGE_ENHANCE`           1/0，默认 1（开）
- `WH_POD_IMAGE_ENHANCE_MODE`      onnx/usm，默认 onnx
- `WH_POD_IMAGE_ENHANCE_POST`      超分后的细结构补偿强度，默认 50，设 0 关闭
- `WH_POD_IMAGE_ENHANCE_POST_RADIUS` 默认 0.8（半径越大越容易出白边）
- `WH_POD_IMAGE_ENHANCE_RADIUS`    默认 1.0（usm 模式用）
- `WH_POD_IMAGE_ENHANCE_PERCENT`   默认 70（usm 模式用）
- `WH_POD_IMAGE_ENHANCE_THRESHOLD` 默认 3（USM 共用）
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter

import numpy as np

logger = logging.getLogger(__name__)

ENV_ENABLED = "WH_POD_IMAGE_ENHANCE"
ENV_MODE = "WH_POD_IMAGE_ENHANCE_MODE"
ENV_RADIUS = "WH_POD_IMAGE_ENHANCE_RADIUS"
ENV_PERCENT = "WH_POD_IMAGE_ENHANCE_PERCENT"
ENV_THRESHOLD = "WH_POD_IMAGE_ENHANCE_THRESHOLD"
ENV_POST_PERCENT = "WH_POD_IMAGE_ENHANCE_POST"
ENV_POST_RADIUS = "WH_POD_IMAGE_ENHANCE_POST_RADIUS"

MODE_ONNX = "onnx"
MODE_USM = "usm"

MODEL_FILENAME = "realesr-general-x4v3.onnx"

# 超过该边长就不跑超分：4× 输出的显存/内存按平方增长，宁可回退 USM 也不能把生图拖垮。
MODEL_MAX_INPUT_EDGE = 1024

# USM 默认「轻度」：只补回插值放大造成的边缘发软。细线/雕刻类图案过锐会出白边光晕，
# 因此强度刻意保守；需要更强时用环境变量单独调，不改代码。
DEFAULT_RADIUS = 1.0
DEFAULT_PERCENT = 70
DEFAULT_THRESHOLD = 3

# 超分之后的「细结构补偿」：4× → 目标尺寸的降采样会把提手/织带/细线这类细结构再次磨软，
# 大块面不受影响。这里用**小半径**（0.8）中低强度（50）的 USM 只补细边缘对比度，
# 半径留大就会在绗缝鼓起处出白边。设为 0 即关闭。
DEFAULT_POST_RADIUS = 0.8
DEFAULT_POST_PERCENT = 50

# 视为“关闭”的取值；空值按未设置处理（即保持默认开启）。
_FALSY = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class EnhanceOptions:
    """清晰化参数；`enabled=False` 时为完全无副作用的空操作。"""

    enabled: bool = True
    mode: str = MODE_ONNX
    radius: float = DEFAULT_RADIUS
    percent: int = DEFAULT_PERCENT
    threshold: int = DEFAULT_THRESHOLD
    post_radius: float = DEFAULT_POST_RADIUS
    post_percent: int = DEFAULT_POST_PERCENT


def enhance_panel(panel: Image.Image, target_size: int, options: EnhanceOptions) -> Image.Image:
    """把一格面板加工成交付尺寸的成品图。

    - ``onnx`` 且**确实需要放大**（面板比目标尺寸小）时：4× 超分 → 降采样到 ``target_size``
      → 细结构补偿锐化。
    - 其余情况（usm 模式 / 面板已不小于目标 / 超分不可用）：LANCZOS 缩放 → USM 锐化。
    """

    if options.mode == MODE_ONNX and _should_upscale(panel, target_size):
        upscaled = _upscale(panel)
        if upscaled is not None:
            resized = upscaled.resize((target_size, target_size), Image.Resampling.LANCZOS)
            return _post_sharpen(resized, options)
    resized = panel.resize((target_size, target_size), Image.Resampling.LANCZOS)
    return sharpen(resized, options)


def _post_sharpen(image: Image.Image, options: EnhanceOptions) -> Image.Image:
    """超分后的细结构补偿：只补提手/织带/细线这类细边缘的对比度。

    超分负责重建大块面纹理，但 4× → 目标尺寸的降采样会再次把细结构磨软；小半径 USM
    专门补这一层。``post_percent=0`` 即关闭。
    """

    if not options.enabled or options.post_percent <= 0:
        return image
    return image.filter(
        ImageFilter.UnsharpMask(
            radius=options.post_radius,
            percent=options.post_percent,
            threshold=options.threshold,
        )
    )


def _should_upscale(panel: Image.Image, target_size: int) -> bool:
    """仅在"确实在放大"（面板小于目标）且输入不超过安全上限时才跑超分。

    面板已不小于目标时没有"放大发软"问题，直接降采样更省，也避开 4× 的内存平方增长。
    """

    edge = max(panel.size)
    return edge < target_size and edge <= MODEL_MAX_INPUT_EDGE


def _upscale(panel: Image.Image) -> Image.Image | None:
    """跑一次 4× 超分；任何问题都返回 None（交给调用方回退），不抛错。"""

    session = _session()
    if session is None:
        return None
    try:
        tensor = np.asarray(panel.convert("RGB"), dtype=np.float32) / 255.0
        tensor = np.transpose(tensor, (2, 0, 1))[None, ...]
        input_name = session.get_inputs()[0].name
        values = session.run(None, {input_name: tensor})
        output = np.squeeze(values[0], axis=0)
        output = np.clip(output * 255.0, 0.0, 255.0).astype(np.uint8)
        return Image.fromarray(np.transpose(output, (1, 2, 0)), "RGB")
    except Exception as exc:  # noqa: BLE001 - 任何推理异常都要回退，不能中断生图
        logger.warning("ONNX 超分推理失败，回退 USM：%s", exc)
        return None


@lru_cache(maxsize=1)
def _session() -> Any | None:
    """惰性创建并复用 onnxruntime 会话（创建有开销，全程只建一次）。"""

    path = _model_path()
    if path is None:
        logger.warning("未找到超分模型 %s，回退 USM", MODEL_FILENAME)
        return None
    try:
        import onnxruntime as ort
    except ImportError:
        logger.warning("onnxruntime 不可用，回退 USM")
        return None
    try:
        session_options = ort.SessionOptions()
        session_options.log_severity_level = 3
        session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        return ort.InferenceSession(
            str(path), sess_options=session_options, providers=["CPUExecutionProvider"]
        )
    except Exception as exc:  # noqa: BLE001 - 加载失败同样回退，不影响生图
        logger.warning("加载超分模型失败，回退 USM：%s", exc)
        return None


def _model_path() -> Path | None:
    roots = [Path(__file__).resolve().parent / "models"]
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        roots.insert(0, Path(bundle_root) / "wh_local" / "models")
    for root in roots:
        candidate = root / MODEL_FILENAME
        if candidate.is_file():
            return candidate
    return None


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


def _read_non_negative_int(name: str, default: int) -> int:
    """与正数版同构，但允许 0（用于"设为 0 即关闭"的开关项）。"""

    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(round(float(raw)))
    except (TypeError, ValueError):
        logger.warning("%s=%r 不是合法数字，已回退默认 %s", name, raw, default)
        return default
    if value < 0:
        logger.warning("%s=%r 不能为负数，已回退默认 %s", name, raw, default)
        return default
    return value


def options_from_env() -> EnhanceOptions:
    """读取清晰化配置。任何非法输入都回退默认，绝不因配置问题阻断生图。"""

    raw_enabled = (os.environ.get(ENV_ENABLED) or "").strip().lower()
    raw_mode = (os.environ.get(ENV_MODE) or "").strip().lower()
    return EnhanceOptions(
        enabled=raw_enabled not in _FALSY,
        mode=MODE_USM if raw_mode == MODE_USM else MODE_ONNX,
        radius=_read_positive_float(ENV_RADIUS, DEFAULT_RADIUS),
        percent=_read_positive_int(ENV_PERCENT, DEFAULT_PERCENT),
        threshold=_read_positive_int(ENV_THRESHOLD, DEFAULT_THRESHOLD),
        post_radius=_read_positive_float(ENV_POST_RADIUS, DEFAULT_POST_RADIUS),
        post_percent=_read_non_negative_int(ENV_POST_PERCENT, DEFAULT_POST_PERCENT),
    )
