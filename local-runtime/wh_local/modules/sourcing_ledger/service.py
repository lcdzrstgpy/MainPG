"""货源与利润台账服务：采集匹配、多货源、利润预览与入产品库。"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Sequence

from .matching import code_key, title_candidates, title_key
from .repository import SourcingLedgerRepository

MARKET = "temu"
# 历史货源迁移的一次性标记（状态表是库级，用 workspace 后缀区分）。
_MIGRATION_KEY = "links_migrated"
DEFAULT_WEIGHT_KG = Decimal("0.5")
# 可入库门槛：净利润 ≥ 10 元（用户口径）。
READY_MIN_NET_PROFIT = Decimal("10")

# Temu 站点名 → 利润引擎站点代码（内置站点）。
_SITE_ALIASES = {
    "美国": "US",
    "美区": "US",
    "哥伦比亚": "CO",
    "厄瓜多尔": "EC",
    "秘鲁": "PE",
}
_BUILTIN_SITES = (
    {"site_code": "US", "display_name": "美区"},
    {"site_code": "CO", "display_name": "哥伦比亚"},
    {"site_code": "EC", "display_name": "厄瓜多尔"},
    {"site_code": "PE", "display_name": "秘鲁"},
)

# 采集批次筛选的哨兵值：单品采集没有 selection_run_id。
UNASSIGNED_BATCH_ID = "__unassigned__"
UNASSIGNED_BATCH_LABEL = "未分组（单品采集）"

# 列表排序与状态计数顺序（也是前端胶囊的展示顺序）。
_STATUS_ORDER = (
    "ready",
    "low_profit",
    "needs_price",
    "needs_source",
    "needs_site",
    "unmatched_source",
    "in_library",
)


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _timestamp(value: Any) -> datetime:
    """ISO 时间字符串 → 可比较时间；空值/脏值排在最旧。"""
    text = _text(value)
    if not text:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _decimal_text(value: Any) -> str:
    text = _text(value).replace(",", "").replace("¥", "").replace("￥", "")
    if not text:
        return ""
    try:
        return f"{Decimal(text).quantize(Decimal('0.0001'))}"
    except (InvalidOperation, ValueError):
        return ""


def landed_cost(price_cny: Any, domestic_freight_cny: Any, moq: Any = 1) -> str:
    """到手成本 = 1688 单价 + 国内运费（按一件计，MOQ 字段仅为兼容旧数据保留）。"""
    price = _decimal_text(price_cny)
    if not price:
        return ""
    freight = Decimal(_decimal_text(domestic_freight_cny) or "0")
    total = Decimal(price) + freight
    return f"{total.quantize(Decimal('0.0001'))}"


@dataclass(frozen=True)
class MatchDecision:
    status: str  # matched | ambiguous | unmatched
    method: str  # binding | code | title | title_similarity
    skc: str = ""
    candidates: list[dict[str, Any]] = field(default_factory=list)


class SourcingLedgerService:
    def __init__(self, repository: SourcingLedgerRepository, profit_service: Any | None = None) -> None:
        if not isinstance(repository, SourcingLedgerRepository):
            raise TypeError("repository must be SourcingLedgerRepository")
        self._repository = repository
        # 利润活动服务（提供 calculate / upsert_product / delete_product）。
        # 允许为空：没有它时仍可做采集匹配与货源管理，只是不展示利润。
        self._profit_service = profit_service
        # 各 workspace 上次补采集货源时的处理子项数量（避免每次列表都全量扫描 payload）。
        self._source_sync: dict[str, int] = {}

    # ---- 采集入库 ----

    def ingest_capture(
        self,
        *,
        workspace_id: str,
        batch_id: str,
        items: Iterable[Any],
        captured_at: str = "",
    ) -> dict[str, int]:
        """把一次采集的报价按 SKC 落表并完成匹配（幂等）。"""
        workspace = _text(workspace_id)
        batch = _text(batch_id)
        if not workspace or not batch:
            return {"groups": 0, "matched": 0, "pending": 0, "rows": 0}

        groups: dict[tuple[str, str], list[Any]] = {}
        for item in items:
            market_skc_id = _text(getattr(item, "skc_id", ""))
            if not market_skc_id:
                continue
            site = _text(getattr(item, "site", ""))
            groups.setdefault((market_skc_id, site), []).append(item)
        if not groups:
            return {"groups": 0, "matched": 0, "pending": 0, "rows": 0}
        groups = self._merge_siteless_groups(groups)

        title_options = self._repository.title_options(workspace)
        summary = {"groups": 0, "matched": 0, "pending": 0, "rows": 0}
        declared_rows: list[dict[str, Any]] = []
        for (market_skc_id, site), members in groups.items():
            market_skc_code = next(
                (_text(getattr(item, "market_skc_code", "")) for item in members
                 if _text(getattr(item, "market_skc_code", ""))),
                "",
            )
            product_title = next(
                (_text(getattr(item, "product_title", "")) for item in members
                 if _text(getattr(item, "product_title", ""))),
                "",
            )
            decision = self._decide(
                workspace,
                market_skc_id=market_skc_id,
                site=site,
                market_skc_code=market_skc_code,
                product_title=product_title,
                title_options=title_options,
            )
            summary["groups"] += 1
            if decision.status == "matched":
                summary["matched"] += 1
                self._repository.upsert_market_link(
                    workspace_id=workspace,
                    skc=decision.skc,
                    market=MARKET,
                    site=site,
                    market_skc_id=market_skc_id,
                    market_skc_code=market_skc_code,
                    title_snapshot=product_title,
                    match_method=decision.method,
                )
                # 之前以 temu:<id> 占位的历史货源，命中后立即改挂到商品货号。
                self._repository.rekey_sources(
                    workspace, f"{MARKET}:{market_skc_id}", decision.skc
                )
                # 采集本身就带 1688 链接与成本：命中商品后立刻生成一条采集货源。
                # 注意：不解除删除墓碑，用户删掉的商品不能因为核价同步又回来。
                self.ensure_collected_sources(workspace, [decision.skc])
            else:
                summary["pending"] += 1

            for index, item in enumerate(members):
                summary["rows"] += 1
                declared_rows.append(
                    {
                        "workspace_id": workspace,
                        "batch_id": batch,
                        "market": MARKET,
                        "market_skc_id": market_skc_id,
                        "market_skc_code": market_skc_code,
                        "skc": decision.skc,
                        "site": site,
                        "sku_id": _text(getattr(item, "sku_id", "")),
                        "sku_merchant_code": _text(getattr(item, "sku_merchant_code", "")),
                        "sku_attribute_text": _text(getattr(item, "sku_attribute_text", "")),
                        "sku_ref": self._sku_ref(item, index),
                        "original_price_cny": _decimal_text(
                            getattr(item, "original_declared_price_cny", "")
                        ),
                        "adjusted_price_cny": _decimal_text(
                            getattr(item, "adjusted_declared_price_cny", "")
                        ),
                        "product_title": product_title,
                        "match_status": decision.status,
                        "match_method": decision.method,
                        "match_candidates": decision.candidates,
                        "captured_at": _text(captured_at)
                        or _text(getattr(item, "captured_at", "")),
                    }
                )
        # 整批一次事务写入：一次采集几百个 SKU，逐行提交会让同步慢好几秒。
        self._repository.upsert_declared_prices(declared_rows)
        return summary

    def _decide(
        self,
        workspace_id: str,
        *,
        market_skc_id: str,
        site: str,
        market_skc_code: str,
        product_title: str,
        title_options: Sequence[tuple[str, str]],
    ) -> MatchDecision:
        # 1) 已绑定过的市场商品：直接复用，避免人工确认结果被下一次采集覆盖。
        link = self._repository.market_link(workspace_id, MARKET, market_skc_id, site)
        if link is not None and self._repository.product_exists(workspace_id, link["skc"]):
            return MatchDecision(status="matched", method="binding", skc=_text(link["skc"]))

        # 2) 平台货号 == 我们的 PP 货号（唯一可靠的确定性匹配）。
        code = code_key(market_skc_code)
        if code and self._repository.product_exists(workspace_id, code):
            return MatchDecision(status="matched", method="code", skc=code)

        # 3) 上架标题规范化后完全相等：唯一命中才算匹配，多个同名进待确认。
        key = title_key(product_title)
        if key:
            exact = [skc for skc, title in title_options if title_key(title) == key]
            if len({code_key(value) for value in exact}) == 1:
                return MatchDecision(status="matched", method="title", skc=exact[0])
            if exact:
                candidates = [
                    {"skc": skc, "title": product_title, "similarity": 1.0} for skc in exact[:3]
                ]
                return MatchDecision(
                    status="ambiguous", method="title", candidates=candidates
                )
            # 4) 相似标题只作待确认候选，绝不自动匹配。
            similar = title_candidates(product_title, list(title_options))
            if similar:
                return MatchDecision(
                    status="ambiguous", method="title_similarity", candidates=similar
                )

        return MatchDecision(status="unmatched", method="")

    @staticmethod
    def _merge_siteless_groups(
        groups: dict[tuple[str, str], list[Any]]
    ) -> dict[tuple[str, str], list[Any]]:
        """把缺站点的 SKU 行并回同 SKC 唯一确定站点的分组。

        弹窗的 SKU 子行没有站点列；若每行各自成组，同一 SKC 会被拆成几十组，
        商品行会出现重复。只有一个站点时缺站点行直接归入该站点。
        """
        sites_by_skc: dict[str, set[str]] = {}
        for market_skc_id, site in groups:
            if site:
                sites_by_skc.setdefault(market_skc_id, set()).add(site)
        merged: dict[tuple[str, str], list[Any]] = {}
        for (market_skc_id, site), members in groups.items():
            resolved = site
            if not resolved:
                candidates = sites_by_skc.get(market_skc_id) or set()
                if len(candidates) == 1:
                    resolved = next(iter(candidates))
            merged.setdefault((market_skc_id, resolved), []).extend(members)
        return merged

    @staticmethod
    def _sku_ref(item: Any, index: int) -> str:
        for value in (
            _text(getattr(item, "sku_id", "")),
            _text(getattr(item, "sku_merchant_code", "")),
            _text(getattr(item, "sku_attribute_text", "")),
        ):
            if value:
                return value
        return f"row-{index}"

    # ---- 人工确认 / 待办 ----

    def pending_matches(self, workspace_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        return self._repository.pending_match_groups(_text(workspace_id), limit=limit)

    def confirm_match(
        self,
        *,
        workspace_id: str,
        market_skc_id: str,
        site: str,
        skc: str,
        market_skc_code: str = "",
        product_title: str = "",
    ) -> dict[str, int]:
        workspace, code = _text(workspace_id), _text(skc)
        if not self._repository.product_exists(workspace, code):
            raise LookupError(f"商品库中不存在货号 {code or '-'}")
        self._repository.upsert_market_link(
            workspace_id=workspace,
            skc=code,
            market=MARKET,
            site=_text(site),
            market_skc_id=_text(market_skc_id),
            market_skc_code=_text(market_skc_code),
            title_snapshot=_text(product_title),
            match_method="manual",
        )
        rows = self._repository.update_declared_match(
            workspace_id=workspace,
            market_skc_id=_text(market_skc_id),
            site=_text(site),
            skc=code,
            status="matched",
            method="manual",
        )
        moved = self._repository.rekey_sources(workspace, f"{MARKET}:{_text(market_skc_id)}", code)
        return {"rows": rows, "sources_moved": moved}

    # ---- 上架标题 ----

    def record_listed_titles(
        self, workspace_id: str, entries: Iterable[tuple[str, str]], *, listed_at: str = ""
    ) -> int:
        count = 0
        codes: list[str] = []
        for skc, title in entries:
            if not _text(skc) or not _text(title):
                continue
            self._repository.upsert_listing_title(
                _text(workspace_id), _text(skc), _text(title), listed_at=_text(listed_at)
            )
            codes.append(_text(skc))
            count += 1
        if codes:
            # 导出即代表商品链路已跑通：顺手补上采集货源（幂等）。
            self.ensure_collected_sources(_text(workspace_id), codes)
        return count

    # ---- 历史货源迁移 ----

    def migrate_legacy_sources(self, workspace_id: str) -> dict[str, int]:
        """把 price_verification_skc_source_links 一次性迁入多货源表（幂等）。"""
        workspace = _text(workspace_id)
        marker = f"{_MIGRATION_KEY}:{workspace}"
        if self._repository.state_get(marker):
            return {"migrated": 0, "skipped": 1}
        migrated = 0
        for row in self._repository.legacy_source_links(workspace):
            market_skc_id = _text(row.get("skc_id"))
            if not market_skc_id:
                continue
            site = _text(row.get("site"))
            target = self._repository.legacy_link_target(workspace, market_skc_id, site)
            product_key = target or f"{MARKET}:{market_skc_id}"
            inserted = self._repository.insert_source(
                {
                    "workspace_id": workspace,
                    "product_key": product_key,
                    "key_kind": "product_code" if target else "market_skc",
                    "offer_id": _text(row.get("offer_id")),
                    "source_url": _text(row.get("source_url")),
                    "source_title": _text(row.get("source_title")),
                    "main_image_url": _text(row.get("main_image_url")),
                    "price_cny": _text(row.get("price_cny")),
                    "moq": row.get("moq") or 1,
                    "domestic_freight_cny": _text(row.get("domestic_freight_cny")),
                    "weight_kg": _text(row.get("weight_kg")),
                    "landed_cost_cny": landed_cost(
                        row.get("price_cny"), row.get("domestic_freight_cny"), row.get("moq")
                    ),
                    "is_primary": False,
                    "source_kind": "image_search",
                    "source_decision": _text(row.get("source_decision")),
                    "note": _text(row.get("note")),
                    "status": _text(row.get("status")) or "active",
                }
            )
            migrated += 1 if inserted else 0
        self._repository.state_set(marker, "1")
        return {"migrated": migrated, "skipped": 0}

    def sources_for(self, workspace_id: str, product_key: str) -> list[dict[str, Any]]:
        return self._repository.sources_for(_text(workspace_id), _text(product_key))

    # ---- 采集货源自动入库 ----

    def ensure_collected_sources(
        self, workspace_id: str, skcs: Iterable[str] | None = None
    ) -> int:
        """把采集时已有的 1688 链接与成本落成一条「采集货源」。

        采集完成后商品本来就有货源链接和价格，台账不应该再显示「待补货源」。
        只在该商品还没有任何 active 货源时创建，用户手工补过货源就不重复添加。
        用户明确删除过的商品写有墓碑，启动兜底不再重新生成。
        """
        workspace = _text(workspace_id)
        wanted = sorted({_text(code) for code in (skcs or []) if _text(code)})
        existing = self._repository.known_product_keys(workspace)
        deleted = self._repository.deleted_product_keys(workspace)
        if wanted:
            pairs = [
                (skc, self._repository.draft_payload_for(workspace, skc))
                for skc in wanted
            ]
        else:
            pairs = list(self._repository.collected_draft_payloads(workspace).items())
        created = 0
        for skc, raw in pairs:
            if not raw or skc in existing or skc in deleted:
                continue
            row = _source_from_draft_payload(workspace, skc, raw)
            if row and self._repository.insert_source(row):
                created += 1
        return created

    def sync_collected_sources(self, workspace_id: str) -> int:
        """列表加载前增量补货源：定向采集/整店采集处理完即时进入台账。

        三种采集渠道（定向、整店、插件）的草稿都带 1688 链接与成本，
        但只有插件会走核价同步；这里按处理子项数量变化触发一次补货，
        没有新增商品时只做一次 COUNT，不重复扫描大 payload。
        """
        workspace = _text(workspace_id)
        if not workspace:
            return 0
        signature = self._repository.processed_item_count(workspace)
        if self._source_sync.get(workspace) == signature:
            return 0
        created = self.ensure_collected_sources(workspace)
        self._source_sync[workspace] = signature
        return created

    # ---- 历史采集补录 ----

    def backfill_existing_captures(self, workspace_id: str) -> dict[str, int]:
        """把已落库、但还没进台账的采集分片补进来（按分片幂等）。

        老数据重新采集前也能在页面看到申报价；单项失败不影响其它分片。
        """
        workspace = _text(workspace_id)
        if not workspace:
            return {"chunks": 0}
        from wh_local.price_verification.quote_normalizer import (
            normalize_price_quote_discovery,
        )

        ingested = 0
        for chunk in self._repository.capture_chunks(workspace):
            chunk_id = _text(chunk.get("chunk_id"))
            marker = f"chunk_ingested:{chunk_id}"
            if not chunk_id or self._repository.state_get(marker):
                continue
            try:
                payload = json.loads(chunk.get("capture_json") or "{}")
                preview = normalize_price_quote_discovery(payload)
            except Exception:  # noqa: BLE001 - 旧格式解析失败就跳过
                continue
            self.ingest_capture(
                workspace_id=workspace,
                batch_id=_text(chunk.get("batch_id")),
                items=preview.quotes,
                captured_at=_text(chunk.get("captured_at")),
            )
            self._repository.state_set(marker, "1")
            ingested += 1
        return {"chunks": ingested}

    # ---- 页面聚合 ----

    def sites(self, workspace_id: str) -> list[dict[str, str]]:
        """利润引擎可用站点：内置 + 工作台自定义。"""
        options = [dict(site) for site in _BUILTIN_SITES]
        existing = {site["site_code"] for site in options}
        for site in self._repository.custom_sites(_text(workspace_id)):
            if site["site_code"] not in existing:
                options.append(site)
        return options

    def resolve_site_code(self, workspace_id: str, site_label: str) -> str:
        """Temu 站点名（美国站 / 自定义站点名）→ 利润引擎站点代码。"""
        text = _text(site_label)
        if not text:
            return ""
        key = text[:-1] if text.endswith("站") else text
        if key.upper() in {"US", "CO", "EC", "PE"}:
            return key.upper()
        mapped = _SITE_ALIASES.get(key) or _SITE_ALIASES.get(text)
        if mapped:
            return mapped
        for site in self._repository.custom_sites(_text(workspace_id)):
            name = _text(site["display_name"] or site["site_code"]).rstrip("站")
            if name.casefold() == key.casefold():
                return site["site_code"]
        return ""

    def _profit(
        self,
        site_code: str,
        selling_price: Any,
        cost_price: Any,
        weight_kg: Any = None,
    ) -> dict[str, Any]:
        """按工作台已保存的站点费率算利润（与产品库口径一致）。"""
        if not (self._profit_service and site_code and selling_price and cost_price):
            return {}
        try:
            result = self._profit_service.calculate(
                site_code,
                Decimal(_decimal_text(selling_price) or "0"),
                Decimal(_decimal_text(cost_price) or "0"),
                Decimal(_decimal_text(weight_kg) or str(DEFAULT_WEIGHT_KG)),
            )
        except Exception:  # noqa: BLE001 - 站点缺失/费率异常时只隐藏利润
            return {}
        preview = result.get("preview") if isinstance(result, Mapping) else None
        if preview is None:
            return {}
        data = asdict(preview)
        return {
            key: (f"{value}" if isinstance(value, Decimal) else value)
            for key, value in data.items()
        }

    def list_items(
        self,
        workspace_id: str,
        *,
        status: str = "",
        site: str = "",
        keyword: str = "",
        batch: str = "",
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        """商品行：商品 + 货源数/最低到手价 + 最新申报价 + 利润 + 入库状态。"""
        workspace = _text(workspace_id)
        # 定向/整店采集不经过核价同步，处理完成后靠这里补上采集货源。
        self.sync_collected_sources(workspace)
        titles = self._repository.product_titles(workspace)
        library = self._repository.library_records(workspace)
        sources_by_key: dict[str, list[dict[str, Any]]] = {}
        for source in self._repository.all_active_sources(workspace):
            sources_by_key.setdefault(_text(source["product_key"]), []).append(source)

        groups: dict[tuple[str, str], dict[str, Any]] = {}
        for row in self._repository.declared_rows(workspace):
            skc = _text(row.get("skc"))
            if not skc:
                continue
            site_label = _text(row.get("site"))
            key = (skc, site_label)
            agg = groups.get(key)
            if agg is None:
                agg = {
                    "skc": skc,
                    "site": site_label,
                    "market_skc_id": _text(row.get("market_skc_id")),
                    "market_skc_code": _text(row.get("market_skc_code")),
                    "sku_count": 0,
                    "original_min": None,
                    "adjusted_min": None,
                    "captured_at": _text(row.get("captured_at")),
                }
                groups[key] = agg
            agg["sku_count"] += 1
            agg["original_min"] = _min_decimal(
                agg["original_min"], _num(row.get("original_price_cny"))
            )
            agg["adjusted_min"] = _min_decimal(
                agg["adjusted_min"], _num(row.get("adjusted_price_cny"))
            )

        items: list[dict[str, Any]] = []
        covered: set[str] = set()
        overrides = self._repository.site_overrides(workspace)
        for (skc, site_label), agg in groups.items():
            covered.add(skc)
            items.append(
                self._item_payload(
                    workspace, skc, site_label, agg,
                    sources_by_key.get(skc, []), titles.get(skc, {}), library,
                    overrides.get(skc),
                )
            )
        for skc, sources in sources_by_key.items():
            if skc in covered:
                continue
            item = self._item_payload(
                workspace, skc, "", None, sources, titles.get(skc, {}), library,
                overrides.get(skc),
            )
            if skc.startswith(f"{MARKET}:"):
                item["status"] = "unmatched_source"
            items.append(item)

        # 采集批次：货号 → 处理草稿所属批次；单品采集没有批次，归入「未分组」。
        batch_map = self._repository.task_batch_map(workspace)
        for item in items:
            item["batch_id"] = _text(batch_map.get(item["skc"]))
        batch_labels = self._repository.batch_display_names(
            workspace, {item["batch_id"] for item in items if item["batch_id"]}
        )
        batch_counts: dict[str, int] = {}
        for item in items:
            batch_id = item["batch_id"]
            item["batch_label"] = batch_labels.get(batch_id, batch_id)
            batch_counts[batch_id] = batch_counts.get(batch_id, 0) + 1
        batch_options = [
            {
                "batch_id": batch_id or UNASSIGNED_BATCH_ID,
                "label": (
                    batch_labels.get(batch_id)
                    or (UNASSIGNED_BATCH_LABEL if not batch_id else batch_id)
                ),
                "count": count,
            }
            for batch_id, count in sorted(
                batch_counts.items(), key=lambda pair: (bool(pair[0]), -pair[1])
            )
        ]

        wanted_site = _text(site)
        wanted_batch = _text(batch)
        wanted_status = [_text(value) for value in _text(status).split(",") if _text(value)]
        keyword_key = _text(keyword).casefold()
        if wanted_site:
            items = [item for item in items if item["site_code"] == wanted_site]
        if wanted_batch == UNASSIGNED_BATCH_ID:
            items = [item for item in items if not item["batch_id"]]
        elif wanted_batch:
            items = [item for item in items if item["batch_id"] == wanted_batch]
        if keyword_key:
            items = [
                item
                for item in items
                if keyword_key in item["skc"].casefold()
                or keyword_key in item["title"].casefold()
                or keyword_key in item["market_skc_code"].casefold()
                or keyword_key in item["market_skc_id"].casefold()
            ]
        counts = {key: 0 for key in _STATUS_ORDER}
        for item in items:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        if wanted_status:
            items = [item for item in items if item["status"] in wanted_status]
        # 默认按最近更新时间倒序：改过货源/重量/站点或刚采过申报价的排前面。
        items.sort(key=lambda item: (_timestamp(item.get("updated_at")), item["skc"]), reverse=True)
        total = len(items)
        page = items[max(0, offset): max(0, offset) + max(1, limit)]
        return {
            "items": page,
            "total": total,
            "counts": counts,
            "batches": batch_options,
            "offset": offset,
            "limit": limit,
        }

    def _item_payload(
        self,
        workspace_id: str,
        skc: str,
        site_label: str,
        aggregate: Mapping[str, Any] | None,
        sources: Sequence[Mapping[str, Any]],
        title_meta: Mapping[str, str],
        library: set[tuple[str, str]],
        site_override: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        # 手选的站点优先于插件采集到的站点，利润按手选站点费率重算。
        override_label = _text((site_override or {}).get("site_label"))
        if override_label:
            site_label = override_label
        site_code = _text((site_override or {}).get("site_code")) or self.resolve_site_code(
            workspace_id, site_label
        )
        best = _pick_source(sources)
        adjusted = aggregate.get("adjusted_min") if aggregate else None
        cost = _num(best.get("landed_cost_cny")) if best else None
        profit = self._profit(
            site_code, adjusted, cost, best.get("weight_kg") if best else None
        )
        in_library = bool(site_code) and any(
            (key, site_code) in library
            for key in {skc, _text((aggregate or {}).get("market_skc_id"))}
            if key
        )
        if in_library:
            status = "in_library"
        elif not sources:
            status = "needs_source"
        elif aggregate is None:
            status = "needs_price"
        elif not profit:
            status = "needs_site"
        elif Decimal(_decimal_text(profit.get("net_profit")) or "0") >= READY_MIN_NET_PROFIT:
            status = "ready"
        else:
            status = "low_profit"
        return {
            "skc": skc,
            "title": _text(title_meta.get("title")),
            "image_url": _text(title_meta.get("image_url")),
            "source_image_url": _text(title_meta.get("source_image_url")),
            "site": site_label,
            "site_code": site_code,
            "market_skc_id": _text((aggregate or {}).get("market_skc_id")),
            "market_skc_code": _text((aggregate or {}).get("market_skc_code")),
            "sku_count": int((aggregate or {}).get("sku_count") or 0),
            "original_min": _money(aggregate.get("original_min") if aggregate else None),
            "adjusted_min": _money(adjusted),
            "captured_at": _text((aggregate or {}).get("captured_at")),
            "updated_at": max(
                (
                    _timestamp(value)
                    for value in (
                        *[_text(source.get("updated_at")) for source in sources],
                        _text((aggregate or {}).get("captured_at")),
                        _text((site_override or {}).get("updated_at")),
                    )
                    if _text(value)
                ),
                default=_timestamp(""),
            ).isoformat(),
            "source_count": len(sources),
            "best_source": _source_summary(best),
            "weight_estimated": bool(best) and not _text(best.get("weight_kg")),
            "profit": profit,
            "status": status,
            "in_library": in_library,
        }

    def item_detail(self, workspace_id: str, skc: str, *, site: str = "") -> dict[str, Any]:
        workspace, code = _text(workspace_id), _text(skc)
        if not code:
            raise LookupError("缺少商品货号")
        # 打开详情时兜底补一条采集货源（老商品/启动后才处理完的商品也能看到）。
        self.ensure_collected_sources(workspace, [code])
        titles = self._repository.product_titles(workspace)
        sources = self._repository.sources_for(workspace, code)
        declared = [
            row for row in self._repository.declared_rows(workspace) if _text(row.get("skc")) == code
        ]
        declared_sites = sorted({_text(row.get("site")) for row in declared if _text(row.get("site"))})
        override = self._repository.site_overrides(workspace).get(code) or {}
        override_label = _text(override.get("site_label"))
        # 申报价仍按采集时的站点筛选；手选站点只影响展示与利润费率。
        declared_site = _text(site) or (declared_sites[0] if declared_sites else "")
        current_site = override_label or declared_site
        site_code = _text(override.get("site_code")) or self.resolve_site_code(workspace, declared_site)
        site_labels = list(declared_sites)
        if override_label and override_label not in site_labels:
            site_labels.append(override_label)
        latest = [
            row for row in declared if not declared_site or _text(row.get("site")) == declared_site
        ][:200]
        adjusted = None
        original = None
        for row in latest:
            adjusted = _min_decimal(adjusted, _num(row.get("adjusted_price_cny")))
            original = _min_decimal(original, _num(row.get("original_price_cny")))
        library = self._repository.library_records(workspace)
        images_by_source = self._repository.images_for(workspace, [s["id"] for s in sources])
        enriched = []
        for source in sources:
            profit = self._profit(
                site_code,
                adjusted,
                source.get("landed_cost_cny"),
                source.get("weight_kg"),
            )
            source_id = _text(source.get("id"))
            enriched.append(
                {
                    **source,
                    "images": images_by_source.get(source_id, []),
                    "weight_estimated": not _text(source.get("weight_kg")),
                    "profit": profit,
                }
            )
        return {
            "skc": code,
            "market_skc_id": _text(next((row.get("market_skc_id") for row in latest if row.get("market_skc_id")), "")),
            "title": _text((titles.get(code) or {}).get("title")),
            "image_url": _text((titles.get(code) or {}).get("image_url")),
            "source_image_url": _text((titles.get(code) or {}).get("source_image_url")),
            "site": current_site,
            "site_code": site_code,
            "site_labels": site_labels,
            "sites": self.sites(workspace),
            "sources": enriched,
            "declared_prices": latest,
            "original_min": _money(original),
            "adjusted_min": _money(adjusted),
            "in_library": bool(site_code) and any(
                (key, site_code) in library
                for key in {code, _text(next((row.get("market_skc_id") for row in latest if row.get("market_skc_id")), ""))}
                if key
            ),
        }

    # ---- 多货源维护 ----

    def save_source(
        self,
        workspace_id: str,
        skc: str,
        payload: Mapping[str, Any],
        *,
        source_id: str = "",
    ) -> dict[str, Any]:
        workspace, code = _text(workspace_id), _text(skc)
        if not self._repository.product_exists(workspace, code):
            raise LookupError(f"商品库中不存在货号 {code or '-'}")
        price = _decimal_text(payload.get("price_cny"))
        freight = _decimal_text(payload.get("domestic_freight_cny"))
        # 起订量不再参与计算：一件成本 = 单价 + 国内运费。
        moq = 1
        if source_id:
            existing = self._repository.source_by_id(workspace, source_id)
            if existing is None:
                raise LookupError("货源不存在")
            self._repository.update_source(
                workspace,
                source_id,
                {
                    "source_url": _text(payload.get("source_url")) or existing["source_url"],
                    "source_title": _text(payload.get("source_title")),
                    "shop_name": _text(payload.get("shop_name")),
                    "main_image_url": _text(payload.get("main_image_url")),
                    "price_cny": price,
                    "moq": moq,
                    "domestic_freight_cny": freight,
                    "weight_kg": _decimal_text(payload.get("weight_kg")),
                    "landed_cost_cny": landed_cost(price, freight, moq),
                    "note": _text(payload.get("note")),
                },
            )
        else:
            source_url = _text(payload.get("source_url"))
            if not source_url:
                raise ValueError("货源链接不能为空")
            self._repository.insert_source(
                {
                    "workspace_id": workspace,
                    "product_key": code,
                    "key_kind": "product_code",
                    "offer_id": _text(payload.get("offer_id")) or _offer_id(source_url),
                    "source_url": source_url,
                    "source_title": _text(payload.get("source_title")),
                    "shop_name": _text(payload.get("shop_name")),
                    "main_image_url": _text(payload.get("main_image_url")),
                    "price_cny": price,
                    "moq": moq,
                    "domestic_freight_cny": freight,
                    "weight_kg": _decimal_text(payload.get("weight_kg")),
                    "landed_cost_cny": landed_cost(price, freight, moq),
                    "source_kind": "manual",
                    "note": _text(payload.get("note")),
                }
            )
        # 不自动设默认货源：利润默认取「到手成本最低」的一条，用户点「设为默认」才固定选择。
        return self.item_detail(workspace, code)

    def remove_source(self, workspace_id: str, skc: str, source_id: str) -> dict[str, Any]:
        workspace, code = _text(workspace_id), _text(skc)
        self._repository.remove_source(workspace, source_id)
        return self.item_detail(workspace, code)

    def set_primary_source(self, workspace_id: str, skc: str, source_id: str) -> dict[str, Any]:
        workspace, code = _text(workspace_id), _text(skc)
        self._repository.set_primary(workspace, code, source_id)
        return self.item_detail(workspace, code)

    # ---- 站点手动指定 ----

    def set_item_site(
        self, workspace_id: str, skc: str, site_code: str
    ) -> dict[str, Any]:
        """手动指定商品站点（传空站点代码则恢复按采集站点自动判断）。"""
        workspace, code = _text(workspace_id), _text(skc)
        wanted = _text(site_code).upper()
        if not wanted:
            self._repository.clear_site_override(workspace, code)
            return self.item_detail(workspace, code)
        options = {_text(site["site_code"]).upper(): site for site in self.sites(workspace)}
        site = options.get(wanted)
        if site is None:
            raise ValueError(f"不存在的站点：{site_code}")
        self._repository.set_site_override(
            workspace, code, _text(site["display_name"]), _text(site["site_code"]).upper()
        )
        return self.item_detail(workspace, code)

    # ---- 货源图片 ----

    def add_source_image(
        self, workspace_id: str, skc: str, source_id: str, image_url: str
    ) -> dict[str, Any]:
        workspace, code = _text(workspace_id), _text(skc)
        source = self._repository.source_by_id(workspace, source_id)
        if source is None or _text(source.get("status")) != "active":
            raise LookupError("货源不存在")
        if _text(source.get("product_key")) != code:
            raise LookupError("货源不属于该商品")
        url = _text(image_url)
        # 支持外链，也支持前端上传后转成的 data URL（本地台账不引入对象存储）。
        if not url.startswith(("http://", "https://", "data:image/")):
            raise ValueError("图片地址必须是 http(s) 链接或本地上传的图片")
        if len(url) > 4_000_000:
            raise ValueError("图片太大，请压缩后再上传")
        self._repository.add_image(
            workspace_id=workspace, product_key=code, source_id=_text(source_id), image_url=url
        )
        return self.item_detail(workspace, code)

    def remove_source_image(
        self, workspace_id: str, skc: str, source_id: str, image_id: str
    ) -> dict[str, Any]:
        workspace, code = _text(workspace_id), _text(skc)
        self._repository.remove_image(workspace, _text(source_id), _text(image_id))
        return self.item_detail(workspace, code)

    def clear_source_main_image(
        self, workspace_id: str, skc: str, source_id: str
    ) -> dict[str, Any]:
        """删除货源主图（采集带过来的那张）；自定义图片用 remove_source_image。"""
        workspace, code = _text(workspace_id), _text(skc)
        source = self._repository.source_by_id(workspace, source_id)
        if source is None or _text(source.get("product_key")) != code:
            raise LookupError("货源不存在")
        self._repository.update_source(workspace, _text(source_id), {"main_image_url": ""})
        return self.item_detail(workspace, code)

    # ---- 批量重量 ----

    def set_source_weights(
        self, workspace_id: str, skcs: Iterable[str], weight_text: str
    ) -> dict[str, Any]:
        """批量给选中商品的货源填重量；重量参与首程/尾程费，直接影响利润。"""
        workspace = _text(workspace_id)
        codes = sorted({_text(code) for code in skcs if _text(code)})
        if not codes:
            raise ValueError("请先选择商品")
        weight = _parse_weight_kg(weight_text)
        if weight is None or weight <= 0:
            raise ValueError("重量必须是大于 0 的数字（单位 kg，可写 500g）")
        updated = self._repository.bulk_set_weight(workspace, codes, f"{weight.normalize():f}")
        return {"updated": updated, "skcs": codes, "weight_kg": f"{weight.normalize():f}"}

    def delete_items(self, workspace_id: str, skcs: Iterable[str]) -> dict[str, Any]:
        """从台账删除商品（货源、图片、申报价）；已入产品库的必须先移出。"""
        workspace = _text(workspace_id)
        codes = sorted({_text(code) for code in skcs if _text(code)})
        if not codes:
            raise ValueError("请先选择商品")
        market_ids: dict[str, str] = {}
        for row in self._repository.declared_rows(workspace):
            skc = _text(row.get("skc"))
            market_id = _text(row.get("market_skc_id"))
            if skc and market_id and skc not in market_ids:
                market_ids[skc] = market_id
        library = self._repository.library_records(workspace)
        blocked = [
            code
            for code in codes
            if any(entry[0] in {code, market_ids.get(code, "")} for entry in library)
        ]
        if blocked:
            preview = "、".join(blocked[:3])
            more = f" 等 {len(blocked)} 个" if len(blocked) > 3 else ""
            raise ValueError(f"{preview}{more} 已入产品库，请先在抽屉里「从产品库移除」后再删除")
        result = self._repository.delete_items(workspace, codes)
        return {"deleted": codes, **result}

    # ---- 入产品库 ----

    def enter_library(
        self,
        workspace_id: str,
        skc: str,
        *,
        site: str = "",
        source_id: str = "",
    ) -> dict[str, Any]:
        """显式入产品库：按选中（默认最优）货源与调整后申报价写一条产品档案。"""
        if self._profit_service is None:
            raise RuntimeError("利润服务未就绪，暂不能入产品库")
        workspace, code = _text(workspace_id), _text(skc)
        detail = self.item_detail(workspace, code, site=site)
        site_code = detail["site_code"]
        if not site_code:
            raise ValueError("该商品还没有可识别的站点，请先选择站点")
        adjusted = detail["adjusted_min"]
        if not adjusted:
            raise ValueError("该商品还没有采集到申报价")
        sources = detail["sources"]
        if not sources:
            raise ValueError("该商品还没有货源")
        chosen = next(
            (source for source in sources if _text(source.get("id")) == _text(source_id)),
            None,
        ) or _pick_source(sources)
        groups = [
            {
                "source_url": _text(source.get("source_url")),
                "source_title": _text(source.get("source_title")),
                "main_image_url": _text(source.get("main_image_url")),
                "offer_id": _text(source.get("offer_id")),
                "price_cny": _text(source.get("price_cny")),
                "weight_kg": _text(source.get("weight_kg")) or str(DEFAULT_WEIGHT_KG),
                "moq": int(source.get("moq") or 1),
                "domestic_freight_cny": _text(source.get("domestic_freight_cny")),
                "note": _text(source.get("note")),
                "profit": source.get("profit") or {},
            }
            for source in sources
        ]
        profit = chosen.get("profit") or {}
        # 产品库以平台 SKC 为主键（与核价入库的记录一致）；没有平台 SKC 时退回商品货号。
        library_key = _text(detail.get("market_skc_id")) or code
        record = self._profit_service.upsert_product(
            {
                "site": site_code,
                "skc": library_key,
                "selling_price": adjusted,
                "cost_price": _text(chosen.get("landed_cost_cny")),
                "weight_kg": _text(chosen.get("weight_kg")) or str(DEFAULT_WEIGHT_KG),
                "note": "来自货源与利润台账",
                "source_url": _text(chosen.get("source_url")),
                "source_groups_json": json.dumps(groups, ensure_ascii=False),
                "source_type": "price_verification",
                "source_main_image_url": detail.get("source_image_url") or detail["image_url"],
                "visibility": "shared",
                "store_name": "",
            },
            actor=_LedgerActor(workspace_id=workspace),
            allow_company_write=True,
        )
        return {"library": record, "profit": profit, "site_code": site_code}

    def leave_library(self, workspace_id: str, skc: str, *, site: str) -> dict[str, Any]:
        if self._profit_service is None:
            raise RuntimeError("利润服务未就绪")
        workspace, code = _text(workspace_id), _text(skc)
        site_code = self.resolve_site_code(workspace, site)
        if not site_code:
            raise ValueError("站点无法识别")
        detail = self.item_detail(workspace, code, site=site)
        keys = [key for key in (detail.get("market_skc_id"), code) if _text(key)]
        deleted = False
        for key in keys:
            deleted = self._profit_service.delete_product(
                key,
                site_code,
                actor=_LedgerActor(workspace_id=workspace),
                allow_company_delete=True,
            )
            if deleted:
                break
        return {"deleted": bool(deleted), "site_code": site_code}


@dataclass(frozen=True)
class _LedgerActor:
    workspace_id: str
    actor_id: str = "sourcing-ledger"
    username: str = "sourcing-ledger"


def _num(value: Any) -> Decimal | None:
    text = _decimal_text(value)
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _min_decimal(current: Any, candidate: Decimal | None) -> Decimal | None:
    if candidate is None:
        return current if isinstance(current, Decimal) else None
    if not isinstance(current, Decimal):
        return candidate
    return min(current, candidate)


def _money(value: Any) -> str:
    if isinstance(value, Decimal):
        return f"{value.quantize(Decimal('0.01'))}"
    return _decimal_text(value)


def _pick_source(sources: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """默认货源：标记了主货源就取它，否则取到手成本最低的一条。"""
    if not sources:
        return None
    primary = next((source for source in sources if source.get("is_primary")), None)
    if primary is not None:
        return primary
    return min(
        sources,
        key=lambda source: _num(source.get("landed_cost_cny")) or Decimal("999999"),
    )


def _source_summary(source: Mapping[str, Any] | None) -> dict[str, Any]:
    if not source:
        return {}
    return {
        "id": _text(source.get("id")),
        "source_url": _text(source.get("source_url")),
        "source_title": _text(source.get("source_title")),
        "price_cny": _text(source.get("price_cny")),
        "landed_cost_cny": _text(source.get("landed_cost_cny")),
        "weight_kg": _text(source.get("weight_kg")),
        "weight_estimated": not _text(source.get("weight_kg")),
        "moq": int(source.get("moq") or 1),
        "domestic_freight_cny": _text(source.get("domestic_freight_cny")),
        "is_primary": bool(source.get("is_primary")),
    }


def _offer_id(url: str) -> str:
    match = re.search(r"(?:offer/|offerId=|offer_id=)(\d{3,})", url)
    return match.group(1) if match else ""


def _source_from_draft_payload(
    workspace_id: str, skc: str, raw_json: str
) -> dict[str, Any] | None:
    """采集 payload → 一条采集货源（链接、单价、运费、MOQ、重量）。"""
    try:
        payload = json.loads(raw_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, Mapping):
        return None
    source_url = _text(
        payload.get("source_url") or payload.get("source_ref") or payload.get("product_link")
    )
    if not source_url.startswith(("http://", "https://")):
        return None
    variants = [
        item for item in (payload.get("source_variant_records") or []) if isinstance(item, Mapping)
    ]
    price = _decimal_text(payload.get("price_cny"))
    if not price:
        prices = [
            value
            for value in (_num(item.get("price_cny")) for item in variants)
            if value is not None and value > 0
        ]
        if prices:
            price = f"{min(prices).quantize(Decimal('0.0001'))}"
    freight = _decimal_text(payload.get("freight_cny"))
    # 起订量不再参与计算：一条货源按一件成本（单价 + 国内运费）核算。
    moq = 1
    return {
        "workspace_id": _text(workspace_id),
        "product_key": _text(skc),
        "key_kind": "product_code",
        "offer_id": _text(payload.get("offer_id")) or _offer_id(source_url),
        "source_url": source_url,
        "source_title": _text(payload.get("source_title") or payload.get("title")),
        "shop_name": _text(payload.get("shop_name")),
        "main_image_url": _text(payload.get("main_image_url") or payload.get("image_url")),
        "price_cny": price,
        "moq": moq,
        "domestic_freight_cny": freight,
        "weight_kg": _weight_kg_from_payload(payload, variants),
        "landed_cost_cny": landed_cost(price, freight, moq),
        "source_kind": "collect",
        "note": "采集自动带入",
    }


def _parse_weight_kg(text: Any) -> Decimal | None:
    """用户输入的重量 → kg；支持 "1.2"、"1.2kg"、"500g"。"""
    raw = _text(text).replace("，", ".").strip()
    if not raw:
        return None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(kg|千克|公斤|g|克)?", raw, re.IGNORECASE)
    if not match:
        return None
    try:
        value = Decimal(match.group(1))
    except InvalidOperation:
        return None
    if (match.group(2) or "").casefold() in {"g", "克"}:
        value = value / Decimal(1000)
    return value


def _weight_kg_from_payload(
    payload: Mapping[str, Any], variants: Sequence[Mapping[str, Any]]
) -> str:
    direct = _decimal_text(payload.get("weight_kg"))
    if direct:
        return direct
    text = _text(payload.get("weight_text"))
    match = re.search(r"(\d+(?:\.\d+)?)\s*(kg|千克|公斤|g|克)", text, re.IGNORECASE)
    if match:
        value = Decimal(match.group(1))
        if match.group(2).casefold() in {"g", "克"}:
            value = value / Decimal(1000)
        return f"{value.quantize(Decimal('0.001'))}"
    for variant in variants:
        weight = _decimal_text(variant.get("weight_kg"))
        if weight:
            return weight
    return ""
