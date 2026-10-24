"""预览图签名地址工具。

预览资产的 URL 带 2 小时签名，数据库里存的是当时的临时地址；直接展示几小时后
就会 403。凡是读取历史记录里的这类地址，都需要按资产表里的 ``access_token``
重新签一次。货源台账与产品库共用这一份逻辑。
"""

from __future__ import annotations

import hashlib
import hmac
import sqlite3
import time
from typing import Any
from urllib.parse import urlencode


def preview_content_url(connection: sqlite3.Connection, raw: Any) -> str:
    """把预览资产地址重新签名；不是预览地址或拿不到资产时原样返回。"""
    text = str(raw if raw is not None else "").strip()
    if not text.startswith("/api/product-processing/preview/assets/"):
        return text
    parts = text.split("?", 1)[0].rstrip("/").split("/")
    if len(parts) < 3 or parts[-1] != "content":
        return text
    asset_id = parts[-2]
    try:
        row = connection.execute(
            "SELECT id, workspace_id, access_token, public_url, managed_path "
            "FROM product_processing_preview_image_assets WHERE id = ? LIMIT 1",
            (asset_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return text
    if row is None:
        return text
    secret = str(row["access_token"] or "").strip()
    # 本地有文件时优先走本机签名地址；COS 公网地址只在没有本地文件时兜底。
    if not str(row["managed_path"] or "").strip():
        return str(row["public_url"] or "").strip() or text
    if not secret:
        return text
    expires = ((int(time.time()) // 3600) + 2) * 3600
    message = "\n".join(
        (str(row["id"] or ""), str(row["workspace_id"] or ""), str(expires))
    ).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    query = urlencode(
        {
            "workspace_id": str(row["workspace_id"] or ""),
            "expires": expires,
            "signature": signature,
        }
    )
    return f"/api/product-processing/preview/assets/{asset_id}/content?{query}"
