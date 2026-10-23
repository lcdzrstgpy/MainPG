"""货源与利润台账的 SQLite 仓储。

与工作台其它模块共用同一个 ``workbench.sqlite3``：商品主档读
``product_processing_drafts``，历史货源从 ``price_verification_skc_source_links``
一次性迁入，其余都是本模块自己的表。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .matching import title_key


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _preview_url(connection: sqlite3.Connection, raw: Any) -> str:
    """处理后的预览图地址带 2 小时签名，重新签名后再返回。

    result_json 里存的是当时的临时签名 URL，直接展示几小时后就会 403；
    这里按预览资产表里的 access_token 重新签一次，拿不到资产就原样返回。
    """
    text = _text(raw)
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
    secret = _text(row["access_token"])
    # 本地有文件时优先走本机签名地址；COS 公网地址只在没有本地文件时兜底。
    if not _text(row["managed_path"]):
        return _text(row["public_url"]) or text
    if not secret:
        return text
    expires = ((int(time.time()) // 3600) + 2) * 3600
    message = "\n".join((_text(row["id"]), _text(row["workspace_id"]), str(expires))).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    query = urlencode(
        {
            "workspace_id": _text(row["workspace_id"]),
            "expires": expires,
            "signature": signature,
        }
    )
    return f"/api/product-processing/preview/assets/{asset_id}/content?{query}"


class SourcingLedgerRepository:
    """Own the sourcing-ledger tables; never touches other modules' write paths."""

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = str(database_path)
        self.initialize()

    @property
    def database_path(self) -> str:
        return self._database_path

    @contextmanager
    def _session(self):
        connection = sqlite3.connect(self._database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        migrations = Path(__file__).with_name("migrations")
        with self._session() as connection:
            for migration in sorted(migrations.glob("*.sql")):
                connection.executescript(migration.read_text(encoding="utf-8"))

    # ---- 商品主档 / 上架标题 ----

    def product_exists(self, workspace_id: str, skc: str) -> bool:
        if not _text(skc):
            return False
        workspace, code = _text(workspace_id), _text(skc)
        with self._session() as connection:
            # 商品货号 PP-xxxxxx 在处理任务的子项上（草稿表大多没有 skc）。
            for sql in (
                "SELECT 1 FROM product_processing_task_items i "
                "JOIN product_processing_tasks t ON t.id = i.task_id "
                "WHERE t.workspace_id = ? AND i.skc = ? LIMIT 1",
                "SELECT 1 FROM product_processing_drafts "
                "WHERE workspace_id = ? AND skc = ? AND status <> 'deleted' LIMIT 1",
            ):
                try:
                    row = connection.execute(sql, (workspace, code)).fetchone()
                except sqlite3.OperationalError:
                    continue
                if row is not None:
                    return True
        return False

    def title_options(self, workspace_id: str, *, limit: int = 5000) -> list[tuple[str, str]]:
        """``(skc, title)`` 候选：上架标题优先，其次处理结果里的英文标题。"""
        workspace = _text(workspace_id)
        options: list[tuple[str, str]] = []
        seen: set[str] = set()
        with self._session() as connection:
            for sql, params in (
                (
                    "SELECT skc, listed_title AS title FROM product_listing_states "
                    "WHERE workspace_id = ? AND listed_title <> ''",
                    (workspace,),
                ),
                (
                    "SELECT i.skc AS skc, "
                    "COALESCE(NULLIF(i.title, ''), json_extract(i.result_json, '$.optimized_title')) AS title "
                    "FROM product_processing_task_items i "
                    "JOIN product_processing_tasks t ON t.id = i.task_id "
                    "WHERE t.workspace_id = ? AND i.skc LIKE 'PP-%' AND i.status = 'completed' "
                    "ORDER BY i.id DESC LIMIT ?",
                    (workspace, limit),
                ),
                (
                    "SELECT skc, title FROM product_processing_drafts "
                    "WHERE workspace_id = ? AND status <> 'deleted' AND skc IS NOT NULL "
                    "AND skc <> '' AND title <> '' LIMIT ?",
                    (workspace, limit),
                ),
            ):
                try:
                    rows = connection.execute(sql, params).fetchall()
                except sqlite3.OperationalError:
                    continue
                for row in rows:
                    skc = _text(row["skc"])
                    title = _text(row["title"])
                    if not skc or not title or skc in seen:
                        continue
                    seen.add(skc)
                    options.append((skc, title))
                if len(options) >= limit:
                    return options[:limit]
        return options[:limit]

    def upsert_listing_title(
        self, workspace_id: str, skc: str, title: str, *, listed_at: str = ""
    ) -> None:
        workspace, code, text_value = _text(workspace_id), _text(skc), _text(title)
        if not workspace or not code or not text_value:
            return
        now = _now()
        with self._session() as connection:
            connection.execute(
                """INSERT INTO product_listing_states
                   (workspace_id, skc, listed_title, title_key, listed_at, listing_status, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'listed', ?)
                   ON CONFLICT(workspace_id, skc) DO UPDATE SET
                     listed_title = excluded.listed_title,
                     title_key = excluded.title_key,
                     listed_at = CASE WHEN excluded.listed_at <> '' THEN excluded.listed_at
                                      ELSE product_listing_states.listed_at END,
                     listing_status = 'listed',
                     updated_at = excluded.updated_at""",
                (workspace, code, text_value, title_key(text_value), _text(listed_at), now),
            )

    # ---- 市场绑定 ----

    def market_link(
        self, workspace_id: str, market: str, market_skc_id: str, site: str
    ) -> dict[str, Any] | None:
        with self._session() as connection:
            row = connection.execute(
                "SELECT * FROM product_market_links WHERE workspace_id = ? AND market = ? "
                "AND market_skc_id = ? AND site = ? LIMIT 1",
                (_text(workspace_id), _text(market), _text(market_skc_id), _text(site)),
            ).fetchone()
        return dict(row) if row is not None else None

    def upsert_market_link(
        self,
        *,
        workspace_id: str,
        skc: str,
        market: str,
        site: str,
        market_skc_id: str,
        market_skc_code: str = "",
        title_snapshot: str = "",
        match_method: str = "manual",
    ) -> None:
        now = _now()
        with self._session() as connection:
            connection.execute(
                """INSERT INTO product_market_links
                   (workspace_id, skc, market, site, market_skc_id, market_skc_code,
                    title_snapshot, match_method, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(workspace_id, skc, market, site) DO UPDATE SET
                     market_skc_id = excluded.market_skc_id,
                     market_skc_code = excluded.market_skc_code,
                     title_snapshot = excluded.title_snapshot,
                     match_method = excluded.match_method,
                     updated_at = excluded.updated_at""",
                (
                    _text(workspace_id),
                    _text(skc),
                    _text(market),
                    _text(site),
                    _text(market_skc_id),
                    _text(market_skc_code),
                    _text(title_snapshot),
                    _text(match_method),
                    now,
                    now,
                ),
            )

    # ---- 申报价 ----

    def upsert_declared_price(self, row: Mapping[str, Any]) -> None:
        self.upsert_declared_prices([row])

    def upsert_declared_prices(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """一次事务写入整批 SKU 申报价：每次采集几百行，逐行提交会慢一个数量级。"""
        if not rows:
            return 0
        now = _now()
        payloads = []
        for row in rows:
            payloads.append(
                (
                    _text(row.get("id")) or _new_id(),
                    _text(row.get("workspace_id")),
                    _text(row.get("batch_id")),
                    _text(row.get("market")) or "temu",
                    _text(row.get("market_skc_id")),
                    _text(row.get("market_skc_code")),
                    _text(row.get("skc")),
                    _text(row.get("site")),
                    _text(row.get("sku_id")),
                    _text(row.get("sku_merchant_code")),
                    _text(row.get("sku_attribute_text")),
                    _text(row.get("sku_ref")),
                    _text(row.get("original_price_cny")),
                    _text(row.get("adjusted_price_cny")),
                    _text(row.get("product_title")),
                    _text(row.get("match_status")) or "pending",
                    _text(row.get("match_method")),
                    json.dumps(row.get("match_candidates") or [], ensure_ascii=False),
                    _text(row.get("captured_at")) or now,
                    now,
                    now,
                )
            )
        with self._session() as connection:
            connection.executemany(
                """INSERT INTO product_declared_prices
                   (id, workspace_id, batch_id, market, market_skc_id, market_skc_code, skc,
                    site, sku_id, sku_merchant_code, sku_attribute_text, sku_ref,
                    original_price_cny, adjusted_price_cny, product_title,
                    match_status, match_method, match_candidates_json,
                    captured_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(workspace_id, batch_id, market_skc_id, sku_ref) DO UPDATE SET
                     market_skc_code = excluded.market_skc_code,
                     skc = excluded.skc,
                     site = excluded.site,
                     sku_merchant_code = excluded.sku_merchant_code,
                     sku_attribute_text = excluded.sku_attribute_text,
                     original_price_cny = excluded.original_price_cny,
                     adjusted_price_cny = excluded.adjusted_price_cny,
                     product_title = excluded.product_title,
                     match_status = excluded.match_status,
                     match_method = excluded.match_method,
                     match_candidates_json = excluded.match_candidates_json,
                     captured_at = excluded.captured_at,
                     updated_at = excluded.updated_at""",
                payloads,
            )
        return len(payloads)

    def update_declared_match(
        self,
        *,
        workspace_id: str,
        market_skc_id: str,
        site: str,
        skc: str,
        status: str,
        method: str,
        candidates: Sequence[Mapping[str, Any]] | None = None,
    ) -> int:
        with self._session() as connection:
            cursor = connection.execute(
                """UPDATE product_declared_prices
                   SET skc = ?, match_status = ?, match_method = ?,
                       match_candidates_json = ?, updated_at = ?
                   WHERE workspace_id = ? AND market_skc_id = ? AND site = ?""",
                (
                    _text(skc),
                    _text(status),
                    _text(method),
                    json.dumps(list(candidates or []), ensure_ascii=False),
                    _now(),
                    _text(workspace_id),
                    _text(market_skc_id),
                    _text(site),
                ),
            )
            return int(cursor.rowcount or 0)

    def pending_match_groups(self, workspace_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        with self._session() as connection:
            rows = connection.execute(
                """SELECT market_skc_id, market_skc_code, site, product_title, skc,
                          match_status, match_method, match_candidates_json,
                          COUNT(*) AS sku_count, MAX(captured_at) AS captured_at
                   FROM product_declared_prices
                   WHERE workspace_id = ? AND match_status IN ('pending', 'ambiguous', 'unmatched')
                   GROUP BY market_skc_id, site
                   ORDER BY captured_at DESC
                   LIMIT ?""",
                (_text(workspace_id), limit),
            ).fetchall()
        groups: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["match_candidates"] = json.loads(item.pop("match_candidates_json") or "[]")
            except ValueError:
                item["match_candidates"] = []
                item.pop("match_candidates_json", None)
            groups.append(item)
        return groups

    # ---- 多货源 ----

    def product_titles(self, workspace_id: str, *, limit: int = 20000) -> dict[str, dict[str, str]]:
        """货号 → {title, image_url}：标题取处理结果，主图优先用处理后的第一张轮播图。"""
        workspace = _text(workspace_id)
        titles: dict[str, dict[str, str]] = {}
        with self._session() as connection:
            try:
                rows = connection.execute(
                    "SELECT i.skc AS skc, "
                    "COALESCE(NULLIF(i.title, ''), json_extract(i.result_json, '$.optimized_title')) AS title, "
                    "json_extract(i.result_json, '$.carousel_image_paths[0]') AS processed_image, "
                    "COALESCE(NULLIF(i.image_url, ''), json_extract(i.result_json, '$.image_url')) AS source_image "
                    "FROM product_processing_task_items i "
                    "JOIN product_processing_tasks t ON t.id = i.task_id "
                    "WHERE t.workspace_id = ? AND i.skc LIKE 'PP-%' AND i.status = 'completed' "
                    "ORDER BY i.id DESC LIMIT ?",
                    (workspace, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            for row in rows:
                skc = _text(row["skc"])
                if not skc or skc in titles:
                    continue
                titles[skc] = {
                    "title": _text(row["title"]),
                    # 处理后的主图（带新签名）；没有处理结果时回退采集原图。
                    "image_url": _preview_url(connection, row["processed_image"])
                    or _text(row["source_image"]),
                    # 采集原图（外链稳定），入产品库等需要长期有效的场景用它。
                    "source_image_url": _text(row["source_image"]),
                }
        return titles

    def declared_rows(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._session() as connection:
            rows = connection.execute(
                "SELECT * FROM product_declared_prices WHERE workspace_id = ? "
                "ORDER BY captured_at DESC",
                (_text(workspace_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def library_records(self, workspace_id: str) -> set[tuple[str, str]]:
        """已入产品库的 (skc, site_code)。"""
        with self._session() as connection:
            try:
                rows = connection.execute(
                    "SELECT skc, site_code FROM profit_activity_records WHERE workspace_id = ?",
                    (_text(workspace_id),),
                ).fetchall()
            except sqlite3.OperationalError:
                return set()
        return {(_text(row["skc"]), _text(row["site_code"])) for row in rows}

    def custom_sites(self, workspace_id: str) -> list[dict[str, str]]:
        with self._session() as connection:
            try:
                rows = connection.execute(
                    "SELECT site_code, display_name FROM profit_activity_sites WHERE workspace_id = ?",
                    (_text(workspace_id),),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        return [
            {"site_code": _text(row["site_code"]), "display_name": _text(row["display_name"])}
            for row in rows
        ]

    def collected_draft_payloads(
        self, workspace_id: str, *, limit: int = 5000
    ) -> dict[str, str]:
        """货号 → 最近一次采集的原始 payload（采集即带 1688 链接、价格、运费、MOQ）。

        商品货号在任务子项上、原始数据在草稿上，这里一次 JOIN 取回，供台账自动
        生成「采集货源」——采集完就应该有货源和成本，不需要用户再手工添加。
        """
        workspace = _text(workspace_id)
        payloads: dict[str, str] = {}
        with self._session() as connection:
            try:
                rows = connection.execute(
                    "SELECT i.skc AS skc, d.raw_payload_json AS raw "
                    "FROM product_processing_task_items i "
                    "JOIN product_processing_tasks t ON t.id = i.task_id "
                    "JOIN product_processing_drafts d ON d.id = i.product_draft_id "
                    "WHERE t.workspace_id = ? AND i.skc LIKE 'PP-%' "
                    "ORDER BY i.id DESC LIMIT ?",
                    (workspace, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                return {}
        for row in rows:
            skc = _text(row["skc"])
            if skc and skc not in payloads and _text(row["raw"]):
                payloads[skc] = _text(row["raw"])
        return payloads

    def draft_payload_for(self, workspace_id: str, skc: str) -> str:
        """单个商品最近一次采集的原始 payload。"""
        if not _text(skc):
            return ""
        with self._session() as connection:
            try:
                row = connection.execute(
                    "SELECT d.raw_payload_json AS raw "
                    "FROM product_processing_task_items i "
                    "JOIN product_processing_tasks t ON t.id = i.task_id "
                    "JOIN product_processing_drafts d ON d.id = i.product_draft_id "
                    "WHERE t.workspace_id = ? AND i.skc = ? ORDER BY i.id DESC LIMIT 1",
                    (_text(workspace_id), _text(skc)),
                ).fetchone()
            except sqlite3.OperationalError:
                return ""
        return _text(row["raw"]) if row is not None else ""

    def active_product_keys(self, workspace_id: str) -> set[str]:
        with self._session() as connection:
            rows = connection.execute(
                "SELECT DISTINCT product_key FROM product_sourcing_sources "
                "WHERE workspace_id = ? AND status = 'active'",
                (_text(workspace_id),),
            ).fetchall()
        return {_text(row["product_key"]) for row in rows}

    def insert_source(self, row: Mapping[str, Any]) -> bool:
        """按 (workspace, product_key, source_url) 幂等写入；已存在返回 False。"""
        now = _now()
        with self._session() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO product_sourcing_sources
                   (id, workspace_id, product_key, key_kind, offer_id, source_url,
                    source_title, shop_name, main_image_url, price_cny, moq,
                    domestic_freight_cny, weight_kg, landed_cost_cny, is_primary,
                    source_kind, source_decision, note, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    _text(row.get("id")) or _new_id(),
                    _text(row.get("workspace_id")),
                    _text(row.get("product_key")),
                    _text(row.get("key_kind")) or "product_code",
                    _text(row.get("offer_id")),
                    _text(row.get("source_url")),
                    _text(row.get("source_title")),
                    _text(row.get("shop_name")),
                    _text(row.get("main_image_url")),
                    _text(row.get("price_cny")),
                    int(row.get("moq") or 1),
                    _text(row.get("domestic_freight_cny")),
                    _text(row.get("weight_kg")),
                    _text(row.get("landed_cost_cny")),
                    1 if row.get("is_primary") else 0,
                    _text(row.get("source_kind")) or "manual",
                    _text(row.get("source_decision")),
                    _text(row.get("note")),
                    _text(row.get("status")) or "active",
                    now,
                    now,
                ),
            )
            return bool(cursor.rowcount)

    def sources_for(self, workspace_id: str, product_key: str) -> list[dict[str, Any]]:
        with self._session() as connection:
            rows = connection.execute(
                "SELECT * FROM product_sourcing_sources "
                "WHERE workspace_id = ? AND product_key = ? AND status = 'active' "
                "ORDER BY is_primary DESC, landed_cost_cny ASC",
                (_text(workspace_id), _text(product_key)),
            ).fetchall()
        return [dict(row) for row in rows]

    def all_active_sources(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._session() as connection:
            rows = connection.execute(
                "SELECT * FROM product_sourcing_sources "
                "WHERE workspace_id = ? AND status = 'active' "
                "ORDER BY landed_cost_cny ASC",
                (_text(workspace_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def source_by_id(self, workspace_id: str, source_id: str) -> dict[str, Any] | None:
        with self._session() as connection:
            row = connection.execute(
                "SELECT * FROM product_sourcing_sources WHERE workspace_id = ? AND id = ? LIMIT 1",
                (_text(workspace_id), _text(source_id)),
            ).fetchone()
        return dict(row) if row is not None else None

    def update_source(self, workspace_id: str, source_id: str, fields: Mapping[str, Any]) -> bool:
        allowed = {
            "source_url",
            "source_title",
            "shop_name",
            "main_image_url",
            "price_cny",
            "moq",
            "domestic_freight_cny",
            "weight_kg",
            "landed_cost_cny",
            "note",
        }
        assignments = [f"{name} = ?" for name in fields if name in allowed]
        values = [fields[name] for name in fields if name in allowed]
        if not assignments:
            return False
        with self._session() as connection:
            cursor = connection.execute(
                f"UPDATE product_sourcing_sources SET {', '.join(assignments)}, updated_at = ? "
                "WHERE workspace_id = ? AND id = ? AND status = 'active'",
                (*values, _now(), _text(workspace_id), _text(source_id)),
            )
            return bool(cursor.rowcount)

    def remove_source(self, workspace_id: str, source_id: str) -> bool:
        with self._session() as connection:
            cursor = connection.execute(
                "UPDATE product_sourcing_sources SET status = 'removed', is_primary = 0, "
                "updated_at = ? WHERE workspace_id = ? AND id = ?",
                (_now(), _text(workspace_id), _text(source_id)),
            )
            removed = bool(cursor.rowcount)
            if removed:
                connection.execute(
                    "DELETE FROM product_sourcing_source_images "
                    "WHERE workspace_id = ? AND source_id = ?",
                    (_text(workspace_id), _text(source_id)),
                )
            return removed

    def set_primary(self, workspace_id: str, product_key: str, source_id: str) -> bool:
        with self._session() as connection:
            connection.execute(
                "UPDATE product_sourcing_sources SET is_primary = 0, updated_at = ? "
                "WHERE workspace_id = ? AND product_key = ?",
                (_now(), _text(workspace_id), _text(product_key)),
            )
            cursor = connection.execute(
                "UPDATE product_sourcing_sources SET is_primary = 1, updated_at = ? "
                "WHERE workspace_id = ? AND id = ? AND status = 'active'",
                (_now(), _text(workspace_id), _text(source_id)),
            )
            return bool(cursor.rowcount)

    # ---- 货源图片 ----

    def images_for(
        self, workspace_id: str, source_ids: Iterable[str]
    ) -> dict[str, list[dict[str, Any]]]:
        """按货源 ID 批量取图片，保持录入顺序。"""
        wanted = [_text(value) for value in source_ids if _text(value)]
        if not wanted:
            return {}
        placeholders = ",".join("?" for _ in wanted)
        with self._session() as connection:
            rows = connection.execute(
                f"SELECT * FROM product_sourcing_source_images "
                f"WHERE workspace_id = ? AND source_id IN ({placeholders}) "
                f"ORDER BY position, created_at",
                (_text(workspace_id), *wanted),
            ).fetchall()
        images: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            images.setdefault(_text(row["source_id"]), []).append(dict(row))
        return images

    def add_image(
        self, *, workspace_id: str, product_key: str, source_id: str, image_url: str
    ) -> dict[str, Any] | None:
        image_url = _text(image_url)
        if not image_url:
            return None
        with self._session() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 AS next FROM product_sourcing_source_images "
                "WHERE workspace_id = ? AND source_id = ?",
                (_text(workspace_id), _text(source_id)),
            ).fetchone()
            position = int(row["next"] or 0) if row is not None else 0
            image_id = _new_id()
            cursor = connection.execute(
                """INSERT OR IGNORE INTO product_sourcing_source_images
                   (id, workspace_id, product_key, source_id, image_url, position, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    image_id,
                    _text(workspace_id),
                    _text(product_key),
                    _text(source_id),
                    image_url,
                    position,
                    _now(),
                ),
            )
            if not cursor.rowcount:
                existing = connection.execute(
                    "SELECT * FROM product_sourcing_source_images "
                    "WHERE workspace_id = ? AND source_id = ? AND image_url = ? LIMIT 1",
                    (_text(workspace_id), _text(source_id), image_url),
                ).fetchone()
                return dict(existing) if existing is not None else None
        return {
            "id": image_id,
            "workspace_id": _text(workspace_id),
            "product_key": _text(product_key),
            "source_id": _text(source_id),
            "image_url": image_url,
            "position": position,
        }

    def remove_image(self, workspace_id: str, source_id: str, image_id: str) -> bool:
        with self._session() as connection:
            cursor = connection.execute(
                "DELETE FROM product_sourcing_source_images "
                "WHERE workspace_id = ? AND source_id = ? AND id = ?",
                (_text(workspace_id), _text(source_id), _text(image_id)),
            )
            return bool(cursor.rowcount)

    # ---- 站点覆盖 ----

    def site_overrides(self, workspace_id: str) -> dict[str, dict[str, str]]:
        """货号 → {site_label, site_code}：用户在页面上手动指定的站点。"""
        with self._session() as connection:
            try:
                rows = connection.execute(
                    "SELECT skc, site_label, site_code, updated_at FROM product_item_site_overrides "
                    "WHERE workspace_id = ?",
                    (_text(workspace_id),),
                ).fetchall()
            except sqlite3.OperationalError:
                return {}
        return {
            _text(row["skc"]): {
                "site_label": _text(row["site_label"]),
                "site_code": _text(row["site_code"]),
                "updated_at": _text(row["updated_at"]),
            }
            for row in rows
        }

    def set_site_override(
        self, workspace_id: str, skc: str, site_label: str, site_code: str
    ) -> None:
        with self._session() as connection:
            connection.execute(
                """INSERT INTO product_item_site_overrides
                   (workspace_id, skc, site_label, site_code, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(workspace_id, skc) DO UPDATE SET
                     site_label = excluded.site_label,
                     site_code = excluded.site_code,
                     updated_at = excluded.updated_at""",
                (_text(workspace_id), _text(skc), _text(site_label), _text(site_code), _now()),
            )

    def clear_site_override(self, workspace_id: str, skc: str) -> None:
        with self._session() as connection:
            connection.execute(
                "DELETE FROM product_item_site_overrides WHERE workspace_id = ? AND skc = ?",
                (_text(workspace_id), _text(skc)),
            )

    # ---- 删除台账条目 ----

    def delete_items(self, workspace_id: str, product_keys: Iterable[str]) -> dict[str, int]:
        """把商品从货源与利润台账里移除（货源、图片、申报价）。

        只删台账自己的数据：采集到的原始商品、上架记录、平台绑定都保留。
        """
        keys = [_text(value) for value in product_keys if _text(value)]
        if not keys:
            return {"sources": 0, "declared": 0, "images": 0}
        placeholders = ",".join("?" for _ in keys)
        with self._session() as connection:
            images = connection.execute(
                f"DELETE FROM product_sourcing_source_images "
                f"WHERE workspace_id = ? AND product_key IN ({placeholders})",
                (_text(workspace_id), *keys),
            ).rowcount
            sources = connection.execute(
                f"DELETE FROM product_sourcing_sources "
                f"WHERE workspace_id = ? AND product_key IN ({placeholders})",
                (_text(workspace_id), *keys),
            ).rowcount
            declared = connection.execute(
                f"DELETE FROM product_declared_prices "
                f"WHERE workspace_id = ? AND skc IN ({placeholders})",
                (_text(workspace_id), *keys),
            ).rowcount
        return {
            "sources": int(sources or 0),
            "declared": int(declared or 0),
            "images": int(images or 0),
        }

    # ---- 批量重量 ----

    def bulk_set_weight(self, workspace_id: str, product_keys: Iterable[str], weight_kg: str) -> int:
        keys = [_text(value) for value in product_keys if _text(value)]
        if not keys:
            return 0
        placeholders = ",".join("?" for _ in keys)
        with self._session() as connection:
            cursor = connection.execute(
                f"UPDATE product_sourcing_sources SET weight_kg = ?, updated_at = ? "
                f"WHERE workspace_id = ? AND status = 'active' AND product_key IN ({placeholders})",
                (_text(weight_kg), _now(), _text(workspace_id), *keys),
            )
            return int(cursor.rowcount or 0)

    def rekey_sources(self, workspace_id: str, from_key: str, to_key: str) -> int:
        """把占位键（temu:<id>）下的货源改挂到商品货号；冲突行按重复处理。"""
        workspace, source_key, target_key = _text(workspace_id), _text(from_key), _text(to_key)
        if not source_key or not target_key or source_key == target_key:
            return 0
        moved = 0
        with self._session() as connection:
            rows = connection.execute(
                "SELECT * FROM product_sourcing_sources WHERE workspace_id = ? AND product_key = ?",
                (workspace, source_key),
            ).fetchall()
            for row in rows:
                exists = connection.execute(
                    "SELECT 1 FROM product_sourcing_sources "
                    "WHERE workspace_id = ? AND product_key = ? AND source_url = ?",
                    (workspace, target_key, row["source_url"]),
                ).fetchone()
                if exists is not None:
                    connection.execute(
                        "UPDATE product_sourcing_sources SET status = 'removed', updated_at = ? "
                        "WHERE id = ?",
                        (_now(), row["id"]),
                    )
                    continue
                connection.execute(
                    "UPDATE product_sourcing_sources SET product_key = ?, key_kind = 'product_code', "
                    "updated_at = ? WHERE id = ?",
                    (target_key, _now(), row["id"]),
                )
                moved += 1
        return moved

    # ---- 历史数据迁移 ----

    def distinct_workspaces(self) -> list[str]:
        """已存在商品数据的 workspace（启动时逐个跑历史货源迁移）。"""
        workspaces: list[str] = []
        with self._session() as connection:
            for sql in (
                "SELECT DISTINCT workspace_id FROM product_processing_tasks",
                "SELECT DISTINCT workspace_id FROM product_processing_drafts",
                "SELECT DISTINCT workspace_id FROM price_verification_skc_source_links",
            ):
                try:
                    rows = connection.execute(sql).fetchall()
                except sqlite3.OperationalError:
                    continue
                for row in rows:
                    value = _text(row[0])
                    if value and value not in workspaces:
                        workspaces.append(value)
        return workspaces

    def capture_chunks(self, workspace_id: str) -> list[dict[str, Any]]:
        """已落库的采集分片（启动时把历史采集补进台账）。"""
        with self._session() as connection:
            try:
                rows = connection.execute(
                    "SELECT chunk_id, batch_id, capture_json, captured_at "
                    "FROM price_verification_quote_capture_chunks WHERE workspace_id = ? "
                    "ORDER BY captured_at",
                    (_text(workspace_id),),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        return [dict(row) for row in rows]

    def state_get(self, key: str) -> str:
        with self._session() as connection:
            row = connection.execute(
                "SELECT value FROM sourcing_ledger_state WHERE key = ?", (_text(key),)
            ).fetchone()
        return _text(row["value"]) if row is not None else ""

    def state_set(self, key: str, value: str) -> None:
        with self._session() as connection:
            connection.execute(
                """INSERT INTO sourcing_ledger_state (key, value, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                (_text(key), _text(value), _now()),
            )

    def legacy_source_links(self, workspace_id: str) -> list[dict[str, Any]]:
        """读取旧表；表不存在（全新库）时返回空列表。"""
        with self._session() as connection:
            try:
                rows = connection.execute(
                    "SELECT * FROM price_verification_skc_source_links WHERE workspace_id = ?",
                    (_text(workspace_id),),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        return [dict(row) for row in rows]

    def legacy_link_target(self, workspace_id: str, market_skc_id: str, site: str) -> str:
        """旧链接的 skc_id 是 Temu SKC ID，尽量通过绑定表换成商品货号。"""
        with self._session() as connection:
            row = connection.execute(
                "SELECT skc FROM product_market_links WHERE workspace_id = ? AND market = 'temu' "
                "AND market_skc_id = ? AND site = ? LIMIT 1",
                (_text(workspace_id), _text(market_skc_id), _text(site)),
            ).fetchone()
        return _text(row["skc"]) if row is not None else ""
