"""从商品图里自动框出「牌面」四边形，并采样牌体颜色等参数。

为什么用「孔洞法」而不是找轮廓：
- 电商图常见白底 + 白牌面，牌面与背景几乎同色，直接找轮廓只能靠阴影，
  实测会在斜拍图上退化成一个正方形；
- 但牌体是彩色的（蓝亮片、金亮片、墨绿条纹……），彩色区域会把白色牌面
  **围成一圈边框**；对彩色掩码做孔洞填充，孔洞就是牌面本身，和背景无关。

只依赖 OpenCV / numpy / Pillow，不加载任何模型，普通办公本单图 <0.5s。

用法::

    python tools/frame_faces.py --image <jpg> --out <dir>
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class FaceBox:
    """一张牌面的定位结果。points 为原图坐标下的四个角。"""

    points: np.ndarray  # 4x2 float32
    area_ratio: float
    aspect: float  # 短边 / 长边
    fill: float  # 孔洞面积 / 外接矩形面积
    sharpness: float
    source: str  # quad / rect


def _order_points(points: np.ndarray) -> np.ndarray:
    sums = points.sum(axis=1)
    diffs = np.diff(points, axis=1).reshape(-1)
    return np.array(
        [
            points[np.argmin(sums)],
            points[np.argmin(diffs)],
            points[np.argmax(sums)],
            points[np.argmax(diffs)],
        ],
        dtype="float32",
    )


def _body_mask(image: np.ndarray) -> np.ndarray:
    """彩色牌体掩码：高饱和 / 较暗的像素。白色牌面与白背景都会被排除。"""

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    saturation, value = hsv[..., 1], hsv[..., 2]
    mask = (((saturation > 55) & (value > 45)) | (value < 90)).astype(np.uint8)
    # 亮片纹理是细碎颗粒，先闭再开，把同一块牌体连成整体、去掉孤立噪点。
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8), iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    return mask


def _hole_mask(body: np.ndarray) -> np.ndarray:
    """从彩色牌体里取出「被完全包住」的白色牌面。"""

    height, width = body.shape
    flood = body.copy()
    scratch = np.zeros((height + 2, width + 2), np.uint8)
    # 从四角泛洪背景；泛不到、又不是牌体的地方就是牌面。
    for seed in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
        if flood[seed[1], seed[0]] == 0:
            cv2.floodFill(flood, scratch, seed, 2)
    holes = ((flood != 2) & (body == 0)).astype(np.uint8)
    return cv2.morphologyEx(holes, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))


def _quad_from_contour(contour: np.ndarray) -> tuple[np.ndarray, str]:
    perimeter = cv2.arcLength(contour, True)
    approx = cv2.approxPolyDP(contour, 0.03 * perimeter, True)
    if len(approx) == 4:
        return _order_points(approx.reshape(4, 2).astype("float32")), "quad"
    rect = cv2.minAreaRect(contour)
    return _order_points(cv2.boxPoints(rect)), "rect"


def _sharpness(image: np.ndarray, quad: np.ndarray) -> float:
    """牌面清晰度：拉普拉斯方差，用来在多张图里挑最清楚的那份。"""

    width, height = 200, 280
    dst = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype="float32")
    matrix = cv2.getPerspectiveTransform(quad, dst)
    warped = cv2.warpPerspective(image, matrix, (width, height), flags=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def detect_faces(
    image: np.ndarray,
    *,
    min_area_ratio: float = 0.002,
    aspect_range: tuple[float, float] = (0.42, 0.98),
) -> list[FaceBox]:
    """返回候选牌面，按清晰度从高到低排序。"""

    total = float(image.shape[0] * image.shape[1])
    holes = _hole_mask(_body_mask(image))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(holes, 8)
    faces: list[FaceBox] = []
    for index in range(1, count):
        x, y, w, h, area = stats[index]
        area_ratio = area / total
        if area_ratio < min_area_ratio:
            continue
        component = (labels == index).astype(np.uint8)
        contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        contour = max(contours, key=cv2.contourArea)
        quad, source = _quad_from_contour(contour)
        side_a = float(np.linalg.norm(quad[1] - quad[0]))
        side_b = float(np.linalg.norm(quad[2] - quad[1]))
        long_side, short_side = max(side_a, side_b), min(side_a, side_b)
        if long_side <= 8:
            continue
        aspect = short_side / long_side
        if not aspect_range[0] <= aspect <= aspect_range[1]:
            continue
        fill = area / float(max(w * h, 1))
        if fill < 0.68:
            continue
        faces.append(
            FaceBox(
                points=quad,
                area_ratio=round(area_ratio, 4),
                aspect=round(aspect, 3),
                fill=round(fill, 3),
                sharpness=round(_sharpness(image, quad), 1),
                source=source,
            )
        )
    faces.sort(key=lambda item: -item.sharpness)
    return faces


def warp_face(image: np.ndarray, face: FaceBox, size: tuple[int, int] = (300, 420)) -> np.ndarray:
    width, height = size
    dst = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype="float32")
    matrix = cv2.getPerspectiveTransform(face.points, dst)
    return cv2.warpPerspective(image, matrix, (width, height), flags=cv2.INTER_CUBIC)


def sample_body_color(image: np.ndarray, faces: list[FaceBox]) -> list[int] | None:
    """牌体主色：彩色掩码里、且不落在牌面内的像素取中位色。"""

    body = _body_mask(image)
    faces_mask = np.zeros(body.shape, np.uint8)
    for face in faces:
        cv2.fillPoly(faces_mask, [face.points.astype(np.int32)], 255)
    body[(faces_mask > 0)] = 0
    pixels = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)[body > 0]
    if pixels.size == 0:
        return None
    return [int(value) for value in np.median(pixels, axis=0)]


def sample_face_base(image: np.ndarray, faces: list[FaceBox]) -> list[int] | None:
    """牌面底色：牌面内低饱和高亮像素的中位色。"""

    colors = []
    for face in faces[:8]:
        patch = warp_face(image, face)
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        white = (hsv[..., 1] < 40) & (hsv[..., 2] > 170)
        rgb = cv2.cvtColor(patch, cv2.COLOR_BGR2RGB)
        if white.any():
            colors.append(np.median(rgb[white], axis=0))
    if not colors:
        return None
    return [int(value) for value in np.median(np.array(colors), axis=0)]


def main() -> None:
    parser = argparse.ArgumentParser(description="框出商品图里的牌面并采样参数")
    parser.add_argument("--image", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    image_path = Path(args.image)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    image = cv2.imread(str(image_path))
    if image is None:
        raise SystemExit(f"读不到图片: {image_path}")

    faces = detect_faces(image)
    print(f"detected {len(faces)} faces")
    body_color = sample_body_color(image, faces)
    face_base = sample_face_base(image, faces)
    aspects = sorted(face.aspect for face in faces)
    median_aspect = aspects[len(aspects) // 2] if aspects else None

    overlay = image.copy()
    for index, face in enumerate(faces):
        points = face.points.astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(overlay, [points], True, (0, 0, 255), 2)
        cv2.putText(
            overlay,
            f"{index + 1}",
            tuple(face.points[0].astype(int)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 0, 255),
            2,
            cv2.LINE_AA,
        )
    cv2.imwrite(str(out_dir / "overlay.png"), overlay)

    crops_dir = out_dir / "faces"
    crops_dir.mkdir(exist_ok=True)
    for index, face in enumerate(faces[:24]):
        cv2.imwrite(str(crops_dir / f"{index + 1:02d}_s{face.sharpness:.0f}.png"), warp_face(image, face))

    result = {
        "image": image_path.name,
        "face_count": len(faces),
        "median_aspect_short_over_long": median_aspect,
        "aspect_p10_p90": [aspects[0], aspects[-1]] if aspects else None,
        "body_color_rgb": body_color,
        "face_base_rgb": face_base,
        "faces": [
            {
                "area_ratio": face.area_ratio,
                "aspect": face.aspect,
                "fill": face.fill,
                "sharpness": face.sharpness,
                "source": face.source,
            }
            for face in faces[:24]
        ],
    }
    (out_dir / "measure.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in result.items() if k != "faces"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
