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
import sqlite3
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
                updated_ts REAL NOT NULL,
                draft_id TEXT NOT NULL DEFAULT '',
                leaf_cat_id INTEGER NOT NULL DEFAULT 0,
                image TEXT NOT NULL DEFAULT '',
                cat_name TEXT NOT NULL DEFAULT '',
                price_min INTEGER NOT NULL DEFAULT 0,
                price_max INTEGER NOT NULL DEFAULT 0,
                shop_name TEXT NOT NULL DEFAULT '',
                error_code TEXT NOT NULL DEFAULT ''
            )
            """
        )
        # 老库补列：发布历史要显示「来自哪条草稿 / 哪个类目 / 哪张图」，
        # 并且成功后要把那条草稿从草稿库里去掉 —— 都依赖 draft_id。
        existing = {
            str(row[1]) for row in conn.execute(f"PRAGMA table_info({TABLE})").fetchall()
        }
        for column, ddl in (
            ("draft_id", "TEXT NOT NULL DEFAULT ''"),
            ("leaf_cat_id", "INTEGER NOT NULL DEFAULT 0"),
            ("image", "TEXT NOT NULL DEFAULT ''"),
            # 发布历史要「看得懂」：只有一串 cat id / 一个错误码，用户没法判断
            # 这是哪件商品、发到哪个店、卖多少钱、错在哪一步。
            ("cat_name", "TEXT NOT NULL DEFAULT ''"),
            ("price_min", "INTEGER NOT NULL DEFAULT 0"),
            ("price_max", "INTEGER NOT NULL DEFAULT 0"),
            ("shop_name", "TEXT NOT NULL DEFAULT ''"),
            ("error_code", "TEXT NOT NULL DEFAULT ''"),
        ):
            if column not in existing:
                conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN {column} {ddl}")
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


def begin(
    database_path: Path,
    owner_id: str,
    fingerprint: str,
    title: str,
    sku_count: int,
    *,
    draft_id: str = "",
    leaf_cat_id: int = 0,
    image: str = "",
    cat_name: str = "",
    price_min: int = 0,
    price_max: int = 0,
    shop_name: str = "",
) -> int:
    """提交前落一笔 pending（先写再提交：请求超时也算「可能已经建了」）。

    ``draft_id`` 是草稿库里的条目 id（``ai:任务:项`` / ``pod:批次:款式``）：
    成功之后靠它把那条草稿从草稿库里去掉，也从这里生成发布历史。
    ``cat_name`` / ``price_min`` / ``price_max`` / ``shop_name`` 只是给历史页看的
    展示字段（类目路径、人民币分、店铺名），不参与任何判定。
    """
    now = time.time()
    with transaction(database_path) as conn:
        cursor = conn.execute(
            f"""
            INSERT INTO {TABLE}
                (owner_id, fingerprint, title, sku_count, status, created_ts, updated_ts,
                 draft_id, leaf_cat_id, image, cat_name, price_min, price_max, shop_name)
            VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(owner_id or ""),
                fingerprint,
                str(title or "")[:200],
                int(sku_count),
                now,
                now,
                str(draft_id or "")[:160],
                int(leaf_cat_id or 0),
                str(image or "")[:500],
                str(cat_name or "")[:200],
                int(price_min or 0),
                int(price_max or 0),
                str(shop_name or "")[:120],
            ),
        )
        return int(cursor.lastrowid)


def finish(
    database_path: Path,
    submission_id: int,
    status: str,
    *,
    gid: str = "",
    error: str = "",
    error_code: str = "",
) -> None:
    with transaction(database_path) as conn:
        conn.execute(
            f"""
            UPDATE {TABLE}
            SET status = ?, gid = ?, error = ?, error_code = ?, updated_ts = ?
            WHERE id = ?
            """,
            (
                str(status),
                str(gid or ""),
                str(error or "")[:500],
                str(error_code or "")[:32],
                time.time(),
                int(submission_id),
            ),
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


STATUSES = ("success", "failed")


def list_history(
    database_path: Path,
    owner_id: str,
    *,
    status: str = "",
    q: str = "",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """发布历史（成功 / 失败分开取）。status 留空 = 全部，q = 标题/商品 id/草稿 id 模糊搜。"""
    wanted = str(status or "").strip()
    where = "owner_id = ?"
    params: list[Any] = [str(owner_id or "")]
    if wanted in STATUSES:
        where += " AND status = ?"
        params.append(wanted)
    else:
        # pending 不算历史（正在提交的那一条）
        where += " AND status IN ('success', 'failed')"
    keyword = str(q or "").strip()
    if keyword:
        like = f"%{keyword}%"
        where += " AND (title LIKE ? OR gid LIKE ? OR draft_id LIKE ? OR cat_name LIKE ?)"
        params.extend([like, like, like, like])
    with transaction(database_path) as conn:
        total = int(
            conn.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE {where}", params).fetchone()[0]
        )
        rows = conn.execute(
            f"""
            SELECT id, title, sku_count, status, gid, error, error_code, created_ts,
                   draft_id, leaf_cat_id, cat_name, price_min, price_max, shop_name, image
            FROM {TABLE}
            WHERE {where}
            ORDER BY created_ts DESC
            LIMIT ? OFFSET ?
            """,
            [*params, max(1, int(limit)), max(0, int(offset))],
        ).fetchall()
    counts = {"success": 0, "failed": 0}
    with transaction(database_path) as conn:
        for row in conn.execute(
            f"SELECT status, COUNT(*) FROM {TABLE} WHERE owner_id = ? GROUP BY status",
            (str(owner_id or ""),),
        ).fetchall():
            if str(row[0]) in counts:
                counts[str(row[0])] = int(row[1])
    items = [
        {
            "id": int(row["id"]),
            "title": str(row["title"] or ""),
            "sku_count": int(row["sku_count"] or 0),
            "status": str(row["status"] or ""),
            "goods_id": str(row["gid"] or ""),
            "error": str(row["error"] or ""),
            "error_code": str(row["error_code"] or ""),
            "draft_id": str(row["draft_id"] or ""),
            "leaf_cat_id": int(row["leaf_cat_id"] or 0),
            "cat_name": str(row["cat_name"] or ""),
            "price_min": int(row["price_min"] or 0),
            "price_max": int(row["price_max"] or 0),
            "shop_name": str(row["shop_name"] or ""),
            "image": str(row["image"] or ""),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(row["created_ts"] or 0))),
            "created_ts": float(row["created_ts"] or 0),
        }
        for row in rows
    ]
    return {"items": items, "total": total, "counts": counts}


def history_rows_missing_image(
    database_path: Path, owner_id: str, *, limit: int = 200
) -> list[dict[str, Any]]:
    """还没记主图的历史记录（只挑有商品 id 的，没 id 无从补起）。"""
    with transaction(database_path) as conn:
        rows = conn.execute(
            f"""
            SELECT id, gid, cat_name, image, created_ts
            FROM {TABLE}
            WHERE owner_id = ?
              AND status IN ('success', 'failed')
              AND gid <> ''
              AND (image = '' OR cat_name = '')
            ORDER BY created_ts DESC
            LIMIT ?
            """,
            (str(owner_id or ""), max(1, int(limit))),
        ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "gid": str(row["gid"] or ""),
            "cat_name": str(row["cat_name"] or ""),
            "image": str(row["image"] or ""),
            "created_ts": float(row["created_ts"] or 0),
        }
        for row in rows
    ]


def update_display_fields(
    database_path: Path,
    submission_id: int,
    *,
    image: str = "",
    cat_name: str = "",
) -> None:
    """补写展示字段（主图 / 类目名）。传空串表示「这条字段没查到」，不覆盖旧值。"""
    with transaction(database_path) as conn:
        conn.execute(
            f"""
            UPDATE {TABLE}
            SET image = CASE WHEN ? <> '' THEN ? ELSE image END,
                cat_name = CASE WHEN ? <> '' THEN ? ELSE cat_name END
            WHERE id = ?
            """,
            (
                str(image or "")[:500],
                str(image or "")[:500],
                str(cat_name or "")[:200],
                str(cat_name or "")[:200],
                int(submission_id),
            ),
        )


def published_draft_ids(database_path: Path, owner_id: str) -> set[str]:
    """已经成功发布的草稿 id：草稿库要把这些条去掉，别让人重复发同一件。

    表还不存在（这台机器从没发布过）时返回空集合 —— 草稿库不该因为
    「台账还没建」而报错。
    """
    try:
        with transaction(database_path) as conn:
            rows = conn.execute(
                f"""
                SELECT DISTINCT draft_id FROM {TABLE}
                WHERE owner_id = ? AND status = 'success' AND draft_id <> ''
                """,
                (str(owner_id or ""),),
            ).fetchall()
    except sqlite3.OperationalError:
        return set()
    return {str(row[0]) for row in rows}
