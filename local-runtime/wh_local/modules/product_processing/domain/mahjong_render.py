"""麻将 3D 渲染的进程封装：找到 Blender、跑 ``tools/render_tile.py``、拿回 PNG。

为什么要单独一层：
- 渲染是「外部进程 + 长得慢」的动作，不能塞进出图主流程的像素代码里；
- Blender 的位置随安装方式变化（winget / 便携版 / 用户自定义），统一在这里探测；
- 渲染失败必须能安静地退回 AI 生图，不能把整批任务拖挂。

本模块不依赖 Blender 的 Python 环境，只在真实调用时才起子进程。
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

_LOGGER = logging.getLogger(__name__)

# tools/render_tile.py 相对本文件的路径：domain -> product_processing -> modules -> wh_local -> local-runtime
_TOOLS_DIR = Path(__file__).resolve().parents[4] / "tools"
_SCRIPT = _TOOLS_DIR / "render_tile.py"
_PRESETS_DIR = _TOOLS_DIR / "presets"


def preset_path(name: str) -> Path | None:
    """按牌型预设名取参数文件；不存在返回 None。"""

    cleaned = str(name or "").strip()
    if not cleaned:
        return None
    path = _PRESETS_DIR / f"{cleaned}.json"
    return path if path.is_file() else None


def art_dir(name: str) -> Path | None:
    """插画类牌面资产目录（抠图或 AI 生成），不存在返回 None。"""

    cleaned = str(name or "").strip()
    if not cleaned:
        return None
    path = _PRESETS_DIR / "art" / cleaned
    return path if path.is_dir() else None


def blender_executable() -> str | None:
    """按「显式配置 → PATH → Windows 默认安装目录」的顺序找 blender.exe。"""

    for key in ("WH_MAHJONG_BLENDER", "WH_BLENDER"):
        explicit = str(os.environ.get(key) or "").strip()
        if explicit and Path(explicit).is_file():
            return explicit
    found = shutil.which("blender")
    if found:
        return found
    roots: list[Path] = []
    for env_key in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        root = str(os.environ.get(env_key) or "").strip()
        if root:
            roots.append(Path(root) / "Blender Foundation")
    candidates: list[Path] = []
    for root in roots:
        try:
            candidates.extend(sorted(root.glob("Blender */blender.exe")))
        except OSError:
            continue
    # 版本目录按名字排序，取最后一个（版本号最高）。
    return str(candidates[-1]) if candidates else None


def render_available() -> bool:
    return bool(blender_executable()) and _SCRIPT.is_file()


def render_cluster(
    patterns: Sequence[Path],
    out_path: Path,
    *,
    layout: str = "row",
    res: int = 1024,
    samples: int = 48,
    transparent: bool = False,
    elevation: float = 42.0,
    azimuth: float = 18.0,
    cover: float = 0.70,
    margin: float = 1.45,
    params: Path | None = None,
    timeout_s: float = 240.0,
) -> Path | None:
    """把若干牌面图案渲成一张（单牌或一撮牌）PNG；失败返回 None。

    调用方负责决定 ``layout``：``single`` / ``row`` / ``fan``。
    """

    blender = blender_executable()
    if not blender:
        _LOGGER.warning("mahjong render: blender not found")
        return None
    if not _SCRIPT.is_file():
        _LOGGER.warning("mahjong render: script missing at %s", _SCRIPT)
        return None
    paths = [Path(item).resolve() for item in patterns if item]
    if not paths:
        return None
    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        blender,
        "-b",
        "--factory-startup",
        "--python",
        str(_SCRIPT),
        "--",
    ]
    if params is not None:
        # 参数文件决定尺寸/倒角/分层/配色；给了它就不要再靠命令行猜这些值。
        command += ["--params", str(Path(params).resolve())]
    command += [
        "--patterns",
        ",".join(str(item) for item in paths),
        "--layout",
        layout,
        "--out",
        str(out_path),
        "--res",
        str(int(res)),
        "--samples",
        str(int(samples)),
        "--elevation",
        f"{float(elevation):g}",
        "--azimuth",
        f"{float(azimuth):g}",
        "--pattern-cover",
        f"{float(cover):g}",
        "--margin",
        f"{float(margin):g}",
    ]
    if transparent:
        command.append("--transparent")
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _LOGGER.warning("mahjong render failed: %s", exc)
        return None
    if completed.returncode != 0 or not out_path.is_file():
        tail = (completed.stderr or completed.stdout or "").strip().splitlines()[-6:]
        _LOGGER.warning("mahjong render exit=%s: %s", completed.returncode, " | ".join(tail))
        return None
    return out_path


def write_pattern(asset_content: bytes, directory: Path, *, name: str) -> Path:
    """把图案资产的 PNG 落到磁盘（Blender 只能读文件，不能吃 bytes）。"""

    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(asset_content).hexdigest()[:16]
    slug = "".join(ch for ch in name if ch.isalnum())[:24] or "pattern"
    path = directory / f"{slug}-{digest}.png"
    if not path.is_file():
        path.write_bytes(asset_content)
    return path.resolve()


def _extractor():
    """加载通用提取器（tools/extract_tiles.py 是唯一实现，这里按需引入）。"""

    if str(_TOOLS_DIR) not in sys.path:
        sys.path.insert(0, str(_TOOLS_DIR))
    import extract_tiles  # noqa: PLC0415 — 延迟导入：没有 OpenCV 时不拖垮整个模块

    return extract_tiles


def extract_profile(
    image_paths: Sequence[Path], out_dir: Path
) -> tuple[Path, list[Path], list[Path]] | None:
    """从商品原图提取「真实牌面 + 实测牌型参数」。

    返回 ``(tile_params.json, 墨迹牌面, 高清墨迹牌面)``。高清牌面是从特写/散落图里
    挑出的完整大图，只给单牌主图用——平铺图的单牌只有一百多像素，放大会糊。
    失败返回 None，调用方退回程序生成的牌面。结果按目录缓存：同一批原图不重复分析。
    """

    out_dir = Path(out_dir)
    params = out_dir / "tile_params.json"
    cached_faces = sorted((out_dir / "faces_ink").glob("*.png"))
    if params.is_file() and cached_faces:
        return params, cached_faces, sorted((out_dir / "faces_hi_ink").glob("*.png"))
    paths = [Path(item) for item in image_paths if Path(item).is_file()]
    if not paths:
        return None
    try:
        extractor = _extractor()
        profile = extractor.build_profile(paths, out_dir)
        if not profile.faces:
            return None
        extractor.write_outputs(profile, out_dir)
    except Exception as exc:  # 提取只是增强，失败不能拖垮出图
        _LOGGER.info("mahjong extract failed: %s", exc)
        return None
    faces = sorted((out_dir / "faces_ink").glob("*.png"))
    if not faces or not params.is_file():
        return None
    return params, faces, sorted((out_dir / "faces_hi_ink").glob("*.png"))
