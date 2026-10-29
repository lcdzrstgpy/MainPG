"""货源与利润台账的 SQLite 仓储。

与工作台其它模块共用同一个 ``workbench.sqlite3``：商品主档读
``product_processing_drafts``，历史货源从 ``price_verification_skc_source_links``
一次性迁入，其余都是本模块自己的表。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..product_processing.preview_urls import preview_content_url as _preview_url

from .matching import title_key


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _moq(value: Any) -> int:
    """旧表把 moq 存成 TEXT（'1' / '1.0'），统一收敛为正整数。"""
    try:
        return max(1, int(float(value or 1)))
    except (TypeError, ValueError):
        return 1


_COLLECTION_BATCH_SOURCES: tuple[tuple[str, str], ...] = (
    ("daily_selection_runs", "run_id"),
    ("shop_collection_batches", "batch_id"),
    ("plugin_onebound_capture_batches", "batch_id"),
)


def _batch_display_name(table: str, row: Mapping[str, Any]) -> str:
    """采集批次展示名：已落库的优先，为空时按渠道字段现算（老批次也能显示）。"""
    stored = _text(row.get("display_name"))
    if stored and "\ufffd" not in stored:
        return stored
    from wh_local.data_collection.batch_naming import (
        daily_selection_display_name,
        plugin_display_name,
        shop_display_name,
    )

    def _loads(value: Any) -> Any:
        try:
            return json.loads(value) if isinstance(value, str) and value else {}
        except ValueError:
            return {}

    if table == "daily_selection_runs":
        return daily_selection_display_name(
            _loads(row.get("criteria_json")),
            _loads(row.get("metadata_json")),
            created_at=row.get("created_at"),
        )
    if table == "shop_collection_batches":
        return shop_display_name(
            shop_name=_text(row.get("shop_name")),
            platform=_text(row.get("platform")),
            created_at=row.get("created_at"),
        )
    return plugin_display_name(_text(row.get("page_url")), created_at=row.get("created_at"))


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

    def price_exclusions(self, workspace_id: str) -> set[tuple[str, str]]:
        """用户删除的 (skc, sku_ref)：不参与最低价与利润计算。"""
        with self._session() as connection:
            rows = connection.execute(
                "SELECT skc, sku_ref FROM product_declared_price_exclusions WHERE workspace_id = ?",
                (_text(workspace_id),),
            ).fetchall()
        return {(_text(row["skc"]), _text(row["sku_ref"])) for row in rows}

    def price_selections(self, workspace_id: str) -> dict[str, str]:
        """skc → 选中的 sku_ref（未选择为空，表示默认取最低价）。"""
        with self._session() as connection:
            rows = connection.execute(
                "SELECT skc, sku_ref FROM product_declared_price_selections WHERE workspace_id = ?",
                (_text(workspace_id),),
            ).fetchall()
        return {_text(row["skc"]): _text(row["sku_ref"]) for row in rows if _text(row["skc"])}

    def set_price_selection(self, workspace_id: str, skc: str, sku_ref: str) -> None:
        """指定用哪个 SKU 的成交申报价；sku_ref 传空表示恢复默认（取最低价）。"""
        workspace, code, ref = _text(workspace_id), _text(skc), _text(sku_ref)
        if not code:
            return
        with self._session() as connection:
            if not ref:
                connection.execute(
                    "DELETE FROM product_declared_price_selections "
                    "WHERE workspace_id = ? AND skc = ?",
                    (workspace, code),
                )
                return
            connection.execute(
                "INSERT INTO product_declared_price_selections (workspace_id, skc, sku_ref, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(workspace_id, skc) DO UPDATE SET "
                "sku_ref = excluded.sku_ref, updated_at = excluded.updated_at",
                (workspace, code, ref, _now()),
            )

    def exclude_declared_price(self, workspace_id: str, skc: str, row_id: str) -> str:
        """删除一条申报价（按 sku_ref 登记排除），返回被删除的 sku_ref。"""
        workspace, code, target = _text(workspace_id), _text(skc), _text(row_id)
        if not code or not target:
            raise LookupError("申报价不存在")
        with self._session() as connection:
            row = connection.execute(
                "SELECT sku_ref FROM product_declared_prices "
                "WHERE workspace_id = ? AND skc = ? AND id = ? LIMIT 1",
                (workspace, code, target),
            ).fetchone()
            if row is None:
                raise LookupError("申报价不存在")
            sku_ref = _text(row["sku_ref"])
            if not sku_ref:
                raise LookupError("申报价缺少 SKU 标识，无法删除")
            connection.execute(
                "INSERT INTO product_declared_price_exclusions (workspace_id, skc, sku_ref, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(workspace_id, skc, sku_ref) DO UPDATE SET updated_at = excluded.updated_at",
                (workspace, code, sku_ref, _now()),
            )
            # 删掉的正好是当前选中的 SKU 时，自动回到默认最低价。
            connection.execute(
                "DELETE FROM product_declared_price_selections "
                "WHERE workspace_id = ? AND skc = ? AND sku_ref = ?",
                (workspace, code, sku_ref),
            )
        return sku_ref

    def clear_price_exclusion(self, workspace_id: str, skc: str, sku_ref: str) -> None:
        """手动重新填写申报价时，解除之前的删除标记。"""
        with self._session() as connection:
            connection.execute(
                "DELETE FROM product_declared_price_exclusions "
                "WHERE workspace_id = ? AND skc = ? AND sku_ref = ?",
                (_text(workspace_id), _text(skc), _text(sku_ref)),
            )

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

    def task_batch_map(self, workspace_id: str) -> dict[str, str]:
        """货号 → 采集批次（处理任务子项所属草稿的 selection_run_id）。

        台账里的 PP-xxxxxx 来自处理任务子项，草稿本身存的是平台 SKC，
        因此要经 task_items.product_draft_id 关联到草稿批次。
        """
        with self._session() as connection:
            try:
                rows = connection.execute(
                    "SELECT i.skc AS skc, d.selection_run_id AS batch_id "
                    "FROM product_processing_task_items i "
                    "JOIN product_processing_tasks t ON t.id = i.task_id "
                    "LEFT JOIN product_processing_drafts d ON d.id = i.product_draft_id "
                    "WHERE t.workspace_id = ? AND i.skc <> '' "
                    "ORDER BY i.id DESC",
                    (_text(workspace_id),),
                ).fetchall()
            except sqlite3.OperationalError:
                return {}
        mapping: dict[str, str] = {}
        for row in rows:
            skc = _text(row["skc"])
            if skc and skc not in mapping:
                mapping[skc] = _text(row["batch_id"])
        return mapping

    def batch_display_names(
        self, workspace_id: str, batch_ids: Iterable[str]
    ) -> dict[str, str]:
        """采集批次展示名：依次查每日选品 / 整店采集 / 插件采集三张源表。"""
        ids = [value for value in dict.fromkeys(_text(item) for item in batch_ids) if value]
        if not ids:
            return {}
        resolved: dict[str, str] = {}
        with self._session() as connection:
            for table, key in _COLLECTION_BATCH_SOURCES:
                pending = [item for item in ids if item not in resolved]
                if not pending:
                    break
                placeholders = ",".join("?" for _ in pending)
                try:
                    rows = connection.execute(
                        f"SELECT * FROM {table} WHERE {key} IN ({placeholders})",
                        pending,
                    ).fetchall()
                except sqlite3.OperationalError:
                    continue
                for row in rows:
                    batch_id = _text(row[key])
                    if not batch_id or batch_id in resolved:
                        continue
                    label = _batch_display_name(table, dict(row))
                    if label:
                        resolved[batch_id] = label
        return resolved

    def known_product_keys(self, workspace_id: str) -> set[str]:
        """出现过的货源商品键（含已删除货源）：自动补货只补从没补过的商品。"""
        with self._session() as connection:
            rows = connection.execute(
                "SELECT DISTINCT product_key FROM product_sourcing_sources "
                "WHERE workspace_id = ?",
                (_text(workspace_id),),
            ).fetchall()
        return {_text(row["product_key"]) for row in rows}

    def processed_item_count(self, workspace_id: str) -> int:
        """已生成 PP 货号的处理子项数量，用来判断有没有新商品需要补货源。"""
        with self._session() as connection:
            try:
                row = connection.execute(
                    "SELECT COUNT(*) AS total FROM product_processing_task_items i "
                    "JOIN product_processing_tasks t ON t.id = i.task_id "
                    "WHERE t.workspace_id = ? AND i.skc LIKE 'PP-%'",
                    (_text(workspace_id),),
                ).fetchone()
            except sqlite3.OperationalError:
                return 0
        return int(row["total"] or 0) if row is not None else 0

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
                    _moq(row.get("moq")),
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

    # ---- 店铺覆盖（选填） ----

    def store_overrides(self, workspace_id: str) -> dict[str, str]:
        """货号 → 店铺名：抽屉里手动填写/选择的店铺。"""
        with self._session() as connection:
            try:
                rows = connection.execute(
                    "SELECT skc, store_name FROM product_item_store_overrides "
                    "WHERE workspace_id = ?",
                    (_text(workspace_id),),
                ).fetchall()
            except sqlite3.OperationalError:
                return {}
        return {
            _text(row["skc"]): _text(row["store_name"])
            for row in rows
            if _text(row["skc"])
        }

    def set_store_override(self, workspace_id: str, skc: str, store_name: str) -> None:
        workspace, code, name = _text(workspace_id), _text(skc), _text(store_name)
        if not code:
            return
        with self._session() as connection:
            if not name:
                connection.execute(
                    "DELETE FROM product_item_store_overrides WHERE workspace_id = ? AND skc = ?",
                    (workspace, code),
                )
                return
            connection.execute(
                "INSERT INTO product_item_store_overrides (workspace_id, skc, store_name, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(workspace_id, skc) DO UPDATE SET "
                "store_name = excluded.store_name, updated_at = excluded.updated_at",
                (workspace, code, name, _now()),
            )

    def store_names(self, workspace_id: str) -> list[str]:
        """已有店铺名（产品库 + 台账手动填写），供输入框下拉候选。"""
        names: set[str] = set()
        workspace = _text(workspace_id)
        with self._session() as connection:
            for sql in (
                "SELECT DISTINCT store_name FROM profit_activity_records "
                "WHERE workspace_id = ? AND store_name <> ''",
                "SELECT DISTINCT store_name FROM product_item_store_overrides "
                "WHERE workspace_id = ? AND store_name <> ''",
            ):
                try:
                    rows = connection.execute(sql, (workspace,)).fetchall()
                except sqlite3.OperationalError:
                    continue
                names.update(_text(row["store_name"]) for row in rows if _text(row["store_name"]))
        return sorted(names)

    # ---- 删除台账条目 ----

    def delete_items(self, workspace_id: str, product_keys: Iterable[str]) -> dict[str, int]:
        """把商品从货源与利润台账里移除（货源、图片、申报价）。

        只删台账自己的数据：采集到的原始商品、上架记录、平台绑定都保留。
        删除同时写墓碑：启动补货兜底不能把用户删掉的采集货源重新生成。
        """
        keys = [_text(value) for value in product_keys if _text(value)]
        if not keys:
            return {"sources": 0, "declared": 0, "images": 0}
        placeholders = ",".join("?" for _ in keys)
        now = _now()
        with self._session() as connection:
            connection.executemany(
                "INSERT INTO product_sourcing_deleted (workspace_id, skc, deleted_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(workspace_id, skc) DO UPDATE SET deleted_at = excluded.deleted_at",
                [(_text(workspace_id), key, now) for key in keys],
            )
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

    def deleted_product_keys(self, workspace_id: str) -> set[str]:
        """用户主动删除过的商品货号。"""
        with self._session() as connection:
            rows = connection.execute(
                "SELECT skc FROM product_sourcing_deleted WHERE workspace_id = ?",
                (_text(workspace_id),),
            ).fetchall()
        return {_text(row["skc"]) for row in rows if _text(row["skc"])}

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

    def fill_declared_sku_attributes(
        self, workspace_id: str, rows: Iterable[Mapping[str, Any]]
    ) -> int:
        """只补空白的 SKU 属性：按平台 SKC + SKU ID/货号匹配历史申报价。

        老版本解析不到「SKU属性集」，重跑历史采集后用它回填，不覆盖已有值。
        """
        workspace = _text(workspace_id)
        now = _now()
        updated = 0
        with self._session() as connection:
            for row in rows:
                market_skc_id = _text(row.get("market_skc_id"))
                sku_id = _text(row.get("sku_id"))
                merchant_code = _text(row.get("sku_merchant_code"))
                site = _text(row.get("site"))
                attribute = _text(row.get("sku_attribute_text"))
                if not market_skc_id or not attribute:
                    continue
                match: list[str] = []
                params: list[Any] = []
                if sku_id:
                    match.append("sku_id = ?")
                    params.append(sku_id)
                if merchant_code:
                    match.append("sku_merchant_code = ?")
                    params.append(merchant_code)
                if not match:
                    continue
                clauses = [
                    "workspace_id = ?",
                    "market_skc_id = ?",
                    "sku_attribute_text = ''",
                    f"({' OR '.join(match)})",
                ]
                params = [workspace, market_skc_id, *params]
                if site:
                    clauses.append("site = ?")
                    params.append(site)
                cursor = connection.execute(
                    f"UPDATE product_declared_prices SET sku_attribute_text = ?, updated_at = ? "
                    f"WHERE {' AND '.join(clauses)}",
                    (attribute, now, *params),
                )
                updated += int(cursor.rowcount or 0)
        return updated

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
