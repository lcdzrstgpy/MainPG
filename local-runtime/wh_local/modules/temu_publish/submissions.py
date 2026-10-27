"""发布提交台账：Temu 的 `bg.glo.goods.add` 没有幂等键。

同一个请求发两次，店铺里就多两个商品；网络超时（请求其实到了）再点一次也一样。
这里只在**本地**记一笔「谁在什么时候用哪份草稿提交过」，用途只有一个：

- 提交前查最近 24 小时有没有「成功」或「还在提交中」的同一条草稿；
- 有就抛 `DuplicateSubmission` 让前端弹二次确认（用户确认后可以 `force=True` 再提）；
- 提交后回写状态（成功/失败），失败的不挡下一次。

台账**不做对账**：商品在 Temu 后台被删了这里也不会改；它只是防手滑/防超时重试，
不是「商品库存表」。
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from ...db import transaction

TABLE = "temu_publish_submissions"
# 多久之内的同一条草稿算「重复提交」。太短挡不住「超时→隔几分钟再点」，
# 太长又会让「改回原样重新发」的用户被误挡。
WINDOW_SECONDS = 24 * 3600
# 进行中的提交最多占用窗口里的这么久：进程被杀掉时也不会永远挡住
PENDING_MAX_AGE_SECONDS = 30 * 60


class DuplicateSubmission(Exception):
    """最近已经提交过同一条草稿（或正在提交中）。"""

    def __init__(self, message: str, *, previous_status: str, previous_at: str) -> None:
        super().__init__(message)
        self.previous_status = previous_status
        self.previous_at = previous_at


def ensure_table(database_path: Path) -> None:
    with transaction(database_path) as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id TEXT NOT NULL DEFAULT '',
                fingerprint TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                sku_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                gid TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                created_ts REAL NOT NULL,
                updated_ts REAL NOT NULL
            )
            """
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_owner ON {TABLE}(owner_id, fingerprint, created_ts)"
        )


def draft_fingerprint(draft: Any) -> str:
    """取「决定店铺里是不是同一件商品」的字段做指纹。

    标题 + 类目 + 每个 SKU 的货号/价格：改了颜色或价格就是另一件商品，不该被挡；
    只改了图片顺序、描述之类的不影响判定（这些不参与指纹）。
    """
    skus: list[str] = []
    for skc in getattr(draft, "skcs", []) or []:
        for sku in getattr(skc, "skus", []) or []:
            skus.append(
                "|".join(
                    [
                        str(getattr(skc, "ext_code", "")),
                        str(getattr(sku, "ext_code", "")),
                        str(getattr(sku, "supplier_price", "")),
                        str(getattr(sku, "stock", "")),
                    ]
                )
            )
    payload = {
        "title": " ".join(str(getattr(draft, "title", "")).split()),
        "category": [int(value) for value in getattr(draft, "category_ids", []) or []],
        "skus": sorted(skus),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def find_recent(
    database_path: Path, owner_id: str, fingerprint: str, *, window_seconds: int = WINDOW_SECONDS
) -> dict[str, Any] | None:
    """最近窗口内同一条草稿的提交记录（成功 / 进行中都算）。"""
    now = time.time()
    with transaction(database_path) as conn:
        row = conn.execute(
            f"""
            SELECT id, status, gid, error, created_ts
            FROM {TABLE}
            WHERE owner_id = ?
              AND fingerprint = ?
              AND status IN ('success', 'pending')
              AND created_ts >= ?
            ORDER BY created_ts DESC
            LIMIT 1
            """,
            (str(owner_id or ""), fingerprint, now - max(1, int(window_seconds))),
        ).fetchone()
    if row is None:
        return None
    status = str(row["status"] or "")
    created = float(row["created_ts"] or 0)
    # 进程被杀留下的 pending 不能一直挡着
    if status == "pending" and now - created > PENDING_MAX_AGE_SECONDS:
        return None
    return {
        "id": int(row["id"]),
        "status": status,
        "gid": str(row["gid"] or ""),
        "error": str(row["error"] or ""),
        "created_ts": created,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(created)),
    }


def begin(database_path: Path, owner_id: str, fingerprint: str, title: str, sku_count: int) -> int:
    """提交前落一笔 pending（先写再提交：请求超时也算「可能已经建了」）。"""
    now = time.time()
    with transaction(database_path) as conn:
        cursor = conn.execute(
            f"""
            INSERT INTO {TABLE}
                (owner_id, fingerprint, title, sku_count, status, created_ts, updated_ts)
            VALUES (?, ?, ?, ?, 'pending', ?, ?)
            """,
            (str(owner_id or ""), fingerprint, str(title or "")[:200], int(sku_count), now, now),
        )
        return int(cursor.lastrowid)


def finish(
    database_path: Path,
    submission_id: int,
    status: str,
    *,
    gid: str = "",
    error: str = "",
) -> None:
    with transaction(database_path) as conn:
        conn.execute(
            f"UPDATE {TABLE} SET status = ?, gid = ?, error = ?, updated_ts = ? WHERE id = ?",
            (str(status), str(gid or ""), str(error or "")[:500], time.time(), int(submission_id)),
        )


def goods_id_from_result(result: Any) -> str:
    """从 goods.add 的返回里挑出商品 id（不同网关字段名不一样，按常见顺序取）。"""
    if isinstance(result, dict):
        for key in ("goodsId", "goods_id", "productId", "product_id", "gid", "id"):
            value = result.get(key)
            if value not in (None, ""):
                return str(value)
        # 有的网关把结果再包一层
        for key in ("result", "data"):
            nested = result.get(key)
            if isinstance(nested, dict):
                found = goods_id_from_result(nested)
                if found:
                    return found
    return ""
