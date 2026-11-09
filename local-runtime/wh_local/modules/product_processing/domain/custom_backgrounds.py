"""用户自定义背景底板：本地落盘 + 1:1 裁切 + 缩略图。

与系统模板（``background_presets.SYSTEM_BACKGROUNDS``）的区别：
- 系统模板只用 ``prompt`` 片段影响生图，图只做界面预览；
- 用户背景是真实像素，主图出图后由 ``background_composite`` 把商品合成上去，
  因此必须在选图阶段就保证「够大、1:1、没有水印边」——这里统一裁切成 1200²，
  缩略图 480² 只给选择器用。

存储布局（按 workspace 隔离，避免不同工作区互相看到对方的图）::

    <output_root>/custom-backgrounds/<workspace>/<id>.jpg
    <output_root>/custom-backgrounds/<workspace>/<id>.thumb.jpg
    <output_root>/custom-backgrounds/<workspace>/<id>.json
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import re
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MIN_SOURCE_SIDE = 800
MAIN_SIDE = 1200
THUMB_SIDE = 480
USER_TEMPLATE_PREFIX = "user:"

_ID_RE = re.compile(r"^ub_[0-9a-f]{12}$")
_DATA_URL_RE = re.compile(r"^data:[^;,]+;base64,", re.I)
_SAFE_WORKSPACE_RE = re.compile(r"[^0-9A-Za-z_-]")

_ALLOWED_FORMATS = {"JPEG", "PNG"}


class CustomBackgroundError(ValueError):
    """上传校验失败：消息直接给用户看。"""


def user_background_id(template_id: str) -> str:
    """``user:ub_xxx`` → ``ub_xxx``；不是用户背景返回空串。"""
    raw = str(template_id or "").strip()
    if not raw.lower().startswith(USER_TEMPLATE_PREFIX):
        return ""
    return raw[len(USER_TEMPLATE_PREFIX) :].strip()


def _decode_payload(data: str) -> bytes:
    raw = str(data or "").strip()
    if not raw:
        raise CustomBackgroundError("背景图内容为空")
    raw = _DATA_URL_RE.sub("", raw)
    try:
        content = base64.b64decode(raw, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise CustomBackgroundError("背景图不是合法的图片数据") from exc
    if not content:
        raise CustomBackgroundError("背景图内容为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise CustomBackgroundError("背景图不能超过 15MB")
    return content


def _safe_workspace(workspace_id: str) -> str:
    cleaned = _SAFE_WORKSPACE_RE.sub("_", str(workspace_id or "").strip())
    return cleaned or "local"


class CustomBackgroundStore:
    """按 workspace 存放用户上传的背景底板。"""

    def __init__(self, root: Path):
        self.root = (Path(root) / "custom-backgrounds").resolve()

    def _workspace_root(self, workspace_id: str) -> Path:
        path = (self.root / _safe_workspace(workspace_id)).resolve()
        if self.root != path and self.root not in path.parents:
            raise CustomBackgroundError("workspace 不合法")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save(
        self,
        data: str,
        *,
        name: str = "",
        workspace_id: str = "local",
    ) -> dict[str, Any]:
        content = _decode_payload(data)
        image = self._decode_image(content)
        width, height = image.size
        if min(width, height) < MIN_SOURCE_SIDE:
            raise CustomBackgroundError(
                f"背景图至少需要 {MIN_SOURCE_SIDE}×{MIN_SOURCE_SIDE} 像素（当前 {width}×{height}）"
            )
        background_id = "ub_" + hashlib.sha256(content).hexdigest()[:12]
        directory = self._workspace_root(workspace_id)
        meta_path = directory / f"{background_id}.json"
        if meta_path.is_file():
            # 同一张图重复上传：沿用已有记录，只补名字。
            meta = self._read_meta(meta_path) or {}
            label = str(name or "").strip()
            if label and label != meta.get("name"):
                meta["name"] = label
                self._write_json(meta_path, meta)
            return self._public(meta, background_id)

        main = self._square(image, MAIN_SIDE)
        thumb = self._square(image, THUMB_SIDE)
        self._write_jpeg(directory / f"{background_id}.jpg", main, quality=92)
        self._write_jpeg(directory / f"{background_id}.thumb.jpg", thumb, quality=88)
        meta = {
            "id": background_id,
            "name": str(name or "").strip() or "自定义背景",
            "created_at": int(time.time()),
            "source_width": width,
            "source_height": height,
            "size_bytes": len(content),
        }
        self._write_json(meta_path, meta)
        return self._public(meta, background_id)

    def list(self, *, workspace_id: str = "local") -> list[dict[str, Any]]:
        directory = self._workspace_root(workspace_id)
        items: list[dict[str, Any]] = []
        for meta_path in directory.glob("ub_*.json"):
            meta = self._read_meta(meta_path)
            if not meta:
                continue
            background_id = meta_path.stem
            if not _ID_RE.match(background_id):
                continue
            if not (directory / f"{background_id}.jpg").is_file():
                continue
            items.append(self._public(meta, background_id))
        items.sort(key=lambda item: int(item.get("created_at") or 0), reverse=True)
        return items

    def delete(self, background_id: str, *, workspace_id: str = "local") -> None:
        if not _ID_RE.match(str(background_id or "")):
            raise FileNotFoundError("背景图不存在")
        directory = self._workspace_root(workspace_id)
        removed = False
        for suffix in (".jpg", ".thumb.jpg", ".json"):
            path = directory / f"{background_id}{suffix}"
            if path.is_file():
                path.unlink()
                removed = True
        if not removed:
            raise FileNotFoundError("背景图不存在")

    def file_path(self, background_id: str, *, workspace_id: str = "local", thumb: bool = False) -> Path:
        if not _ID_RE.match(str(background_id or "")):
            raise FileNotFoundError("背景图不存在")
        directory = self._workspace_root(workspace_id)
        name = f"{background_id}.thumb.jpg" if thumb else f"{background_id}.jpg"
        path = directory / name
        if not path.is_file():
            raise FileNotFoundError("背景图不存在")
        return path

    @staticmethod
    def _decode_image(content: bytes) -> Image.Image:
        try:
            opened = Image.open(io.BytesIO(content))
            opened.load()
        except Exception as exc:  # noqa: BLE001 - 任何解码失败都是「图片不合法」
            raise CustomBackgroundError("无法识别这张图片，请上传 JPG 或 PNG") from exc
        if str(opened.format or "").upper() not in _ALLOWED_FORMATS:
            raise CustomBackgroundError("只支持 JPG 或 PNG 格式的背景图")
        return opened.convert("RGB")

    @staticmethod
    def _square(image: Image.Image, side: int) -> Image.Image:
        # ImageOps.fit 默认按中心裁切，正好去掉超出 1:1 的两侧。
        return ImageOps.fit(image, (side, side), method=Image.Resampling.LANCZOS)

    @staticmethod
    def _write_jpeg(path: Path, image: Image.Image, *, quality: int) -> None:
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality, subsampling=0)
        path.write_bytes(buffer.getvalue())

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _read_meta(path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _public(meta: dict[str, Any], background_id: str) -> dict[str, Any]:
        return {
            "id": background_id,
            "template_id": f"{USER_TEMPLATE_PREFIX}{background_id}",
            "name": str(meta.get("name") or "自定义背景"),
            "created_at": int(meta.get("created_at") or 0),
            "width": MAIN_SIDE,
            "height": MAIN_SIDE,
            "source_width": int(meta.get("source_width") or 0),
            "source_height": int(meta.get("source_height") or 0),
        }
