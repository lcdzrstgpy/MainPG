"""营销图参考图挑选：白底、单一主体、少文字/少拼贴的图先给。

1688 来源图常混着「白底单品图」和「带大字促销的拼贴图」。后者喂给生图模型会被
当成画面内容抄进结果（文字、色块、多余产品），是出图崩坏的主要来源之一。
这里用确定性的像素启发式打分，不调用任何模型：

- 白底比例高（边框一圈亮度高、低饱和）→ 加分
- 整体边缘密度低（干净、无大字）→ 加分
- 四角颜色均匀（纯色背景）→ 加分
下载失败/解码失败直接跳过。
"""

from __future__ import annotations

import io
from typing import Callable, Iterable

from PIL import Image, ImageFilter, ImageStat


_SCORE_SIDE = 256


def pick_clean_references(
    urls: Iterable[str],
    fetch: Callable[[str], bytes],
    *,
    limit: int = 2,
) -> list[str]:
    """按干净程度排序返回参考图 URL（保持原顺序作为同分时的次序）。"""
    items: list[str] = [str(url) for url in urls if str(url or "").strip()]
    if len(items) <= limit:
        return items[:limit] if len(items) <= 1 else items[:limit]
    scored: list[tuple[float, int, str]] = []
    for index, url in enumerate(items):
        try:
            content = fetch(url)
        except Exception:  # noqa: BLE001
            continue
        score = _score(content)
        if score is None:
            continue
        scored.append((score, index, url))
    if not scored:
        return items[:limit]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [url for _, _, url in scored[:limit]]


def _score(content: bytes) -> float | None:
    try:
        image = Image.open(io.BytesIO(content)).convert("RGB")
    except Exception:  # noqa: BLE001
        return None
    image.thumbnail((_SCORE_SIDE, _SCORE_SIDE))
    width, height = image.size
    if width < 32 or height < 32:
        return None

    # 1) 白底比例：亮度 > 238 且低饱和的像素占比
    pixels = list(image.resize((64, 64)).getdata())
    bright = 0
    for r, g, b in pixels:
        if min(r, g, b) > 238 and max(r, g, b) - min(r, g, b) < 12:
            bright += 1
    white_ratio = bright / max(1, len(pixels))

    # 2) 边缘密度：整图平均梯度（大字/拼贴/杂乱道具会拉高）
    edges = image.convert("L").filter(ImageFilter.FIND_EDGES)
    edge_mean = ImageStat.Stat(edges).mean[0]

    # 3) 四角均匀度：四个 24x24 角块的标准差均值（纯背景≈0）
    corner = max(8, width // 8)
    boxes = [
        (0, 0, corner, corner),
        (width - corner, 0, width, corner),
        (0, height - corner, corner, height),
        (width - corner, height - corner, width, height),
    ]
    stds = []
    for box in boxes:
        stat = ImageStat.Stat(image.crop(box))
        stds.append(sum(stat.stddev) / 3)
    corner_std = sum(stds) / len(stds)

    return white_ratio * 200 - edge_mean * 0.8 - corner_std * 1.2
