"""白底主图后处理：把「近白」背景拉成真正的 255 白，并按商品轮廓居中。

为什么不做抠图：
- rembg 权重需要联网下载（本机 GitHub 下载被限速），客户机首次出图会卡住；
- OpenCV GrabCut 在真实产品图上会把工作台残影和刀口一起吃进去（实测）。

这里只做「不改商品像素」的白底归一化：
- 只有三个通道都接近白的像素才按比例提到 255（商品、投影原样保留）；
- 背景本身不是白底（边框不够白）时直接返回 None，宁缺毋滥；
- 按非白像素的外接框重新居中，四周留 7% 安全边，方便尺寸画布画标注线。
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image

# 边框像素里至少这么多比例要「足够白」，否则认为这张图不是白底。
_BORDER_WHITE_RATIO = 0.7
# 判定「背景像素」的最低通道阈值：低于它的像素保持原样（商品/深色投影）。
_WHITE_FLOOR = 232
# 边框白度门槛：低于它的边框像素不计入白底比例。
_BORDER_MIN = 215
# 单像素最多提亮 12%，避免把浅灰商品面（如银色刀身）一起漂白。
_MAX_GAIN = 1.12
# 商品外接框占画面的上限；超过说明几乎全是商品，背景判断不可信。
_MAX_SUBJECT_COVERAGE = 0.98
_SUBJECT_FRAME_RATIO = 0.86


def white_background_main(
    content: bytes,
    *,
    side: int = 1600,
) -> bytes | None:
    """近白背景 → 纯白 1:1；背景不像白底时返回 None。"""
    try:
        import numpy as np

        with Image.open(BytesIO(bytes(content or b""))) as opened:
            image = opened.convert("RGB")
    except Exception:
        return None
    if max(image.size) > 2048:
        image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)

    pixels = np.asarray(image, dtype=np.float32)
    if pixels.ndim != 3 or pixels.shape[2] != 3:
        return None
    height, width = pixels.shape[:2]
    if height < 32 or width < 32:
        return None

    # 1) 边框采样：不是白底就别处理（否则会把暗背景「提亮」成脏灰）。
    band_x = max(2, int(width * 0.02))
    band_y = max(2, int(height * 0.02))
    border = np.concatenate(
        [
            pixels[:band_y, :, :].reshape(-1, 3),
            pixels[-band_y:, :, :].reshape(-1, 3),
            pixels[:, :band_x, :].reshape(-1, 3),
            pixels[:, -band_x:, :].reshape(-1, 3),
        ],
        axis=0,
    )
    border_min_channel = border.min(axis=1)
    if float((border_min_channel >= _BORDER_MIN).mean()) < _BORDER_WHITE_RATIO:
        return None

    # 2) 只提亮「三通道都接近白」的像素，商品与深色投影保持原样。
    channel_min = pixels.min(axis=2)
    background = channel_min >= _WHITE_FLOOR
    if not bool(background.any()):
        return None
    gains = np.clip(255.0 / np.maximum(channel_min, 1.0), 1.0, _MAX_GAIN)
    lifted = pixels * gains[:, :, None]
    pixels = np.where(background[:, :, None], lifted, pixels)
    pixels = np.clip(pixels, 0, 255)

    # 3) 按「非白像素」外接框重新居中（含投影），四周留 7% 安全边。
    subject = pixels.min(axis=2) < 245
    rows = np.where(subject.any(axis=1))[0]
    cols = np.where(subject.any(axis=0))[0]
    if rows.size == 0 or cols.size == 0:
        return None
    top, bottom = int(rows[0]), int(rows[-1]) + 1
    left, right = int(cols[0]), int(cols[-1]) + 1
    if (bottom - top) * (right - left) > width * height * _MAX_SUBJECT_COVERAGE:
        return None
    pad_x = max(4, round((right - left) * 0.07))
    pad_y = max(4, round((bottom - top) * 0.07))
    top = max(0, top - pad_y)
    bottom = min(height, bottom + pad_y)
    left = max(0, left - pad_x)
    right = min(width, right + pad_x)

    subject_image = Image.fromarray(pixels[top:bottom, left:right].astype("uint8"), mode="RGB")
    subject_w, subject_h = subject_image.size
    frame_side = max(1, round(max(subject_w, subject_h) / _SUBJECT_FRAME_RATIO))
    frame = Image.new("RGB", (frame_side, frame_side), (255, 255, 255))
    frame.paste(subject_image, ((frame_side - subject_w) // 2, (frame_side - subject_h) // 2))
    if frame.size != (side, side):
        frame = frame.resize((side, side), Image.Resampling.LANCZOS)

    # 4) 缩放会带来极轻微的振铃，再兜一次：≥250 的像素直接归 255。
    final_pixels = np.asarray(frame, dtype=np.uint8).copy()
    near_white = final_pixels.min(axis=2) >= 250
    final_pixels[near_white] = 255
    buffer = BytesIO()
    Image.fromarray(final_pixels, mode="RGB").save(
        buffer, format="JPEG", quality=94, subsampling=0
    )
    return buffer.getvalue()
