"""combo_kit 成品图文字水印：配置归一化 + 本地 Pillow 渲染。

设计要点：
* 「生成后立即烧进图片」——成品图落盘前把水印直接合成进像素，页面预览 / 下载 /
  导出店小秘 / 预检四处看到的都是同一份带水印的图，无需各处再各自叠加。
* 纯文字水印，支持中文：``combo_kit/generation.py`` 的字体链只有 segoeui/arial，
  渲染中文会得到豆腐块，因此本模块自带 CJK 优先的字体回退链（Windows 微软雅黑 /
  宋体 → macOS 苹方 / 宋体 → Linux Noto CJK → 拉丁字体兜底）。
* 本地渲染不调用任何 provider，不计费。
* 渲染失败（字体缺失、图片损坏）一律返回原图字节，不阻断出图主流程与计费结算。
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

# 位置：四角 + 居中。
POSITIONS = ("top_left", "top_right", "bottom_left", "bottom_right", "center")

# 未配置时的默认水印参数（关闭、右下角、30% 不透明度、字号占图宽 5%、不平铺）。
DEFAULT_WATERMARK: dict[str, Any] = {
    "enabled": False,
    "text": "",
    "position": "bottom_right",
    "opacity": 30,
    "size": 5,
    "tile": False,
}

MAX_TEXT_LENGTH = 60
MIN_FONT_SIZE = 10

# CJK 优先的字体回退链：(路径, ttc face 序号)。按平台常见安装位置依次尝试。
_FONT_CANDIDATES: tuple[tuple[str, int], ...] = (
    ("C:/Windows/Fonts/msyh.ttc", 0),
    ("C:/Windows/Fonts/msyhbd.ttc", 0),
    ("C:/Windows/Fonts/simsun.ttc", 0),
    ("C:/Windows/Fonts/simhei.ttf", 0),
    ("/System/Library/Fonts/PingFang.ttc", 0),
    ("/System/Library/Fonts/Supplemental/Songti.ttc", 4),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
    ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc", 0),
    ("/usr/share/fonts/truetype/arphic/uming.ttc", 0),
    ("C:/Windows/Fonts/arial.ttf", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
)

_resolved_font: tuple[bool, tuple[str, int] | None] = (False, None)


def normalize_watermark_config(raw: Any) -> dict[str, Any]:
    """把任意入参（前端 payload / DB JSON / None）归一化成完整水印配置。"""
    data = raw if isinstance(raw, dict) else {}
    text = str(data.get("text") or "").strip()[:MAX_TEXT_LENGTH]
    position = str(data.get("position") or "").strip().lower().replace("-", "_")
    return {
        "enabled": bool(data.get("enabled")) and bool(text),
        "text": text,
        "position": position if position in POSITIONS else DEFAULT_WATERMARK["position"],
        "opacity": _clamp(data.get("opacity"), 0, 100, DEFAULT_WATERMARK["opacity"]),
        "size": _clamp(data.get("size"), 1, 30, DEFAULT_WATERMARK["size"]),
        "tile": bool(data.get("tile")),
    }


def apply_watermark(content: bytes, config: dict[str, Any], *, suffix: str = ".jpg") -> bytes:
    """把水印烧进图片像素并返回新字节；未启用或渲染失败时原样返回 ``content``。"""
    if not content or not config.get("enabled") or not config.get("text"):
        return content
    try:
        image = Image.open(io.BytesIO(content)).convert("RGBA")
    except Exception:
        return content
    try:
        overlay = _render_overlay(image.size, config)
        merged = Image.alpha_composite(image, overlay).convert("RGB")
        return _encode(merged, suffix)
    except Exception:
        return content


def _render_overlay(size: tuple[int, int], config: dict[str, Any]) -> Image.Image:
    width, height = size
    font = _load_font(max(MIN_FONT_SIZE, int(width * int(config["size"]) / 100)))
    alpha = int(round(int(config["opacity"]) / 100 * 255))
    if alpha <= 0:
        alpha = 1
    fill = (255, 255, 255, alpha)
    stroke_fill = (0, 0, 0, max(1, alpha // 2))
    stroke_width = max(1, int(width / 512))
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    text = str(config["text"])
    if config.get("tile"):
        _draw_tiled(draw, (width, height), text, font, fill, stroke_fill, stroke_width)
    else:
        _draw_positioned(draw, (width, height), text, font, fill, stroke_fill, stroke_width,
                         str(config["position"]))
    return overlay


def _draw_positioned(
    draw: ImageDraw.ImageDraw,
    size: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int, int],
    stroke_fill: tuple[int, int, int, int],
    stroke_width: int,
    position: str,
) -> None:
    width, height = size
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    text_w, text_h = right - left, bottom - top
    margin = max(12, int(width * 0.03))
    if position == "top_left":
        x, y = margin, margin
    elif position == "top_right":
        x, y = width - margin - text_w, margin
    elif position == "bottom_left":
        x, y = margin, height - margin - text_h
    elif position == "center":
        x, y = (width - text_w) // 2, (height - text_h) // 2
    else:  # bottom_right
        x, y = width - margin - text_w, height - margin - text_h
    draw.text((x - left, y - top), text, font=font, fill=fill,
              stroke_width=stroke_width, stroke_fill=stroke_fill)


def _draw_tiled(
    draw: ImageDraw.ImageDraw,
    size: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int, int],
    stroke_fill: tuple[int, int, int, int],
    stroke_width: int,
) -> None:
    """平铺满图：按「文字宽 + 间距 × 行高 + 间距」铺满，奇数行错开半格。"""
    width, height = size
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    text_w, text_h = right - left, bottom - top
    step_x = max(text_w + int(text_h * 1.5), 32)
    step_y = max(text_h + int(text_h * 1.2), 32)
    row = 0
    y = -text_h // 2
    while y < height:
        x = -text_w // 2 + (step_x // 2 if row % 2 else 0)
        while x < width:
            draw.text((x - left, y - top), text, font=font, fill=fill,
                      stroke_width=stroke_width, stroke_fill=stroke_fill)
            x += step_x
        y += step_y
        row += 1


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    entry = _resolve_font_entry()
    if entry is not None:
        path, index = entry
        try:
            return ImageFont.truetype(path, size, index=index)
        except (OSError, ValueError):
            pass
    return ImageFont.load_default(size)


def _resolve_font_entry() -> tuple[str, int] | None:
    global _resolved_font
    if _resolved_font[0]:
        return _resolved_font[1]
    resolved: tuple[str, int] | None = None
    for path, index in _FONT_CANDIDATES:
        if Path(path).is_file():
            resolved = (path, index)
            break
    _resolved_font = (True, resolved)
    return resolved


def _encode(image: Image.Image, suffix: str) -> bytes:
    buffer = io.BytesIO()
    safe_suffix = str(suffix or "").lower()
    if safe_suffix in {".jpg", ".jpeg"}:
        image.save(buffer, format="JPEG", quality=95)
    elif safe_suffix == ".webp":
        image.save(buffer, format="WEBP", quality=95)
    else:
        image.save(buffer, format="PNG")
    return buffer.getvalue()


def _clamp(raw: Any, low: int, high: int, default: int) -> int:
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return int(default)
    return max(low, min(high, value))


__all__ = ["DEFAULT_WATERMARK", "POSITIONS", "apply_watermark", "normalize_watermark_config"]
