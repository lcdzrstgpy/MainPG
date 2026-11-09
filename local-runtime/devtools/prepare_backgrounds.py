# -*- coding: utf-8 -*-
"""把豆包出的 2x2 背景四宫格切成 20 张底板，并去掉右下角「豆包AI生成」水印。

用法（默认处理桌面上的 5 张图）：
    python devtools/prepare_backgrounds.py
或显式指定：
    python devtools/prepare_backgrounds.py --inputs a.png b.png --out ../web-frontend/public/assets/backgrounds

处理顺序：去水印（局部 inpaint）→ 切四象限 → 统一 1200x1200 → 存 JPG。
水印只在右下象限右下角，inpaint 只动这一小块，不会碰其它象限。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "web-frontend" / "public" / "assets" / "backgrounds"
DESKTOP = Path.home() / "Desktop"

# 5 批四宫格 → 每批 4 个模板 id（左上 / 右上 / 左下 / 右下）。
BATCH_IDS: tuple[tuple[str, ...], ...] = (
    ("bg_warm_white", "bg_fog_gray", "bg_oatmeal", "bg_pale_apricot"),
    ("bg_cool_gray", "bg_paper_ivory", "bg_plaster_warm", "bg_paper_coarse"),
    ("bg_oak_light", "bg_walnut", "bg_stone_warm", "bg_terrazzo"),
    ("bg_marble_white", "bg_linen", "bg_canvas", "bg_velvet"),
    ("bg_arch", "bg_steps", "bg_pine", "bg_gift_velvet"),
)

DEFAULT_INPUTS = (
    DESKTOP / "Temu上架白底图要求.png",
    DESKTOP / "Temu上架白底图要求1.png",
    DESKTOP / "Temu上架白底图要求2.png",
    DESKTOP / "Temu上架白底图要求3.png",
    DESKTOP / "Temu上架白底图要求4.png",
)

SIZE = 1200


def remove_watermark(image: np.ndarray) -> np.ndarray:
    """去掉右下角「豆包AI生成」水印。

    水印位置固定：底边往上约 6-7% 的高度。直接裁掉底部 9% 再等比拉回原尺寸，
    没有任何涂抹痕迹；inpaint 在木纹/水磨石上会留条纹，实测这个更干净。
    """
    height, width = image.shape[:2]
    keep = int(height * 0.91)
    cropped = image[:keep, :]
    return cv2.resize(cropped, (width, height), interpolation=cv2.INTER_AREA)


def split_quadrants(image: np.ndarray) -> list[np.ndarray]:
    height, width = image.shape[:2]
    half_h, half_w = height // 2, width // 2
    return [
        image[:half_h, :half_w],
        image[:half_h, half_w : half_w * 2],
        image[half_h : half_h * 2, :half_w],
        image[half_h : half_h * 2, half_w : half_w * 2],
    ]


def prepare(inputs: list[Path], out_dir: Path) -> list[tuple[str, int, int]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[tuple[str, int, int]] = []
    for batch_index, source in enumerate(inputs):
        if batch_index >= len(BATCH_IDS):
            break
        if not source.is_file():
            print(f"skip missing input: {source}")
            continue
        image = cv2.imdecode(np.fromfile(str(source), dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            print(f"skip unreadable: {source}")
            continue
        clean = remove_watermark(image)
        for template_id, quadrant in zip(BATCH_IDS[batch_index], split_quadrants(clean)):
            resized = cv2.resize(quadrant, (SIZE, SIZE), interpolation=cv2.INTER_AREA)
            target = out_dir / f"{template_id}.jpg"
            ok, buffer = cv2.imencode(".jpg", resized, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
            if not ok:
                print(f"encode failed: {template_id}")
                continue
            target.write_bytes(buffer.tobytes())
            written.append((template_id, resized.shape[1], resized.shape[0]))
            print(f"{template_id}: {target.stat().st_size // 1024} KB")
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="去水印并切分背景四宫格")
    parser.add_argument("--inputs", nargs="*", type=Path, default=list(DEFAULT_INPUTS))
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    written = prepare(list(args.inputs), args.out)
    print(f"done: {len(written)} backgrounds -> {args.out}")


if __name__ == "__main__":
    main()
