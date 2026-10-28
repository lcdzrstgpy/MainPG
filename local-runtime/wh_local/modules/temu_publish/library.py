"""草稿库聚合：把 AI 处理结果与 POD 成品统一成发布可用的条目。
只读聚合，不修改任务/POD 数据。图片保留原始地址（本地媒体路径或公网 URL），
由发布页在生成 payload 前统一上传到 Temu 图床。
"""

from __future__ import annotations

from typing import Any

from ...session import Actor

# 申报价推算与导出表共用一套规则：显式申报价 → max(价, 下限)；否则 成本 × 倍数，下限 150。
# 直接引用 product_processing 的常量与解析函数，避免「导出表有价、草稿库没价」再次分叉。
from ..product_processing.domain.workbooks import (
    DECLARED_PRICE_MIN_CNY,
    DECLARED_PRICE_MULTIPLIER,
    _parse_money,
)

AI_FINISHED_STATUSES = {"completed", "partial_failure", "cancelled"}
POD_FINISHED_STATUSES = {"completed", "partial_failure"}

DEFAULT_PAGE_SIZE = 30
MAX_PAGE_SIZE = 100


def collect_draft_library(
    *,
    product_processing: Any,
    pod_service: Any,
    actor: Actor,
    limit: int = 60,
    task_limit: int = 8,
    batch_limit: int = 6,
    source: str = "",
    query: str = "",
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> dict[str, Any]:
    """聚合 + 过滤 + 分页。

    聚合本身要扫任务和批次（贵），所以 router 会把**全量结果**缓存起来，
    分页只在缓存上切片 —— 翻页不该让后端重扫一遍。
    """
    payload = collect_all_draft_entries(
        product_processing=product_processing,
        pod_service=pod_service,
        actor=actor,
        limit=limit,
        task_limit=task_limit,
        batch_limit=batch_limit,
    )
    return paginate_draft_library(payload, source=source, query=query, page=page, page_size=page_size)


def collect_all_draft_entries(
    *,
    product_processing: Any,
    pod_service: Any,
    actor: Actor,
    limit: int = 60,
    task_limit: int = 8,
    batch_limit: int = 6,
) -> dict[str, Any]:
    warnings: list[str] = []
    ai_entries: list[dict[str, Any]] = []
    pod_entries: list[dict[str, Any]] = []

    if product_processing is not None:
        try:
            ai_entries = _collect_ai_entries(product_processing, actor, task_limit)
        except Exception as exc:  # noqa: BLE001 - 单个来源失败不拖垮草稿库
            warnings.append(f"AI 处理结果读取失败：{exc}")

    if pod_service is not None:
        try:
            pod_entries = _collect_pod_entries(pod_service, actor, batch_limit)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"POD 结果读取失败：{exc}")

    # 两类各占一半配额，避免某一类条目过多把另一类挤出草稿库。
    cap = max(1, int(limit))
    half = max(1, cap // 2)
    ai_entries.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    pod_entries.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    selected = ai_entries[:half] + pod_entries[:half]
    if len(selected) < cap:
        extras = ai_entries[half:] + pod_entries[half:]
        extras.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        selected.extend(extras[: cap - len(selected)])
    entries = sorted(selected, key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return {
        "entries": entries,
        "warnings": warnings,
        "counts": {
            "ai": sum(1 for item in entries if item["source"] == "ai"),
            "pod": sum(1 for item in entries if item["source"] == "pod"),
            "total": len(entries),
        },
    }


def _matches_query(item: dict[str, Any], needle: str) -> bool:
    """关键字匹配标题 / 货号 / 类目 / 批次名（都转小写，中文原样子串匹配）。"""
    haystack = " ".join(
        str(item.get(key) or "")
        for key in ("title", "sku", "category_name", "subtitle")
    ).lower()
    return needle in haystack


def paginate_draft_library(
    payload: dict[str, Any],
    *,
    source: str = "",
    query: str = "",
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> dict[str, Any]:
    """在已聚合的全量结果上做来源过滤 + 关键字搜索 + 分页切片。"""
    all_entries = [item for item in payload.get("entries") or [] if isinstance(item, dict)]
    wanted = str(source or "").strip().lower()
    needle = str(query or "").strip().lower()

    filtered = all_entries
    if wanted in {"ai", "pod"}:
        filtered = [item for item in filtered if item.get("source") == wanted]
    if needle:
        filtered = [item for item in filtered if _matches_query(item, needle)]

    size = max(1, min(MAX_PAGE_SIZE, int(page_size or DEFAULT_PAGE_SIZE)))
    total = len(filtered)
    pages = max(1, (total + size - 1) // size)
    current = max(1, min(pages, int(page or 1)))
    start = (current - 1) * size

    return {
        "entries": filtered[start : start + size],
        "warnings": list(payload.get("warnings") or []),
        # counts 是全量的（筛选按钮上的数字要稳定，不能跟着筛选一起变）
        "counts": dict(payload.get("counts") or {}),
        "total": total,
        "page": current,
        "page_size": size,
        "pages": pages,
        "source": wanted if wanted in {"ai", "pod"} else "all",
        "query": str(query or "").strip(),
    }


def _collect_ai_entries(service: Any, actor: Actor, task_limit: int) -> list[dict[str, Any]]:
    history = service.task_history(max(1, task_limit), actor.workspace_id)
    entries: list[dict[str, Any]] = []
    for task in history.get("tasks") or []:
        if str(task.get("status")) not in AI_FINISHED_STATUSES:
            continue
        if int(task.get("success_count") or 0) <= 0:
            continue
        task_id = int(task["task_id"])
        try:
            preview = service.task_preview(
                task_id,
                workspace_id=actor.workspace_id,
                item_limit=20,
                media_limit=20,
            )
        except Exception:
            continue
        # 预览条目只有 8 个核心字段；描述、来源链接、变种属性都在草稿里。
        # 一次性批量读，避免逐条 get_draft 的 N+1。
        item_list = [
            item
            for item in (preview.get("items") or [])
            if item.get("exportable") and not item.get("excluded") and item.get("product_draft_id") is not None
        ]
        draft_ids = [int(item["product_draft_id"]) for item in item_list]
        drafts_by_id: dict[int, dict[str, Any]] = {}
        if draft_ids:
            try:
                drafts_by_id = {
                    int(draft["id"]): draft
                    for draft in service.repository.get_drafts(
                        draft_ids, workspace_id=actor.workspace_id
                    )
                }
            except Exception:  # noqa: BLE001 - 读不到草稿就退回只用预览字段
                drafts_by_id = {}

        for item in preview.get("items") or []:
            if not item.get("exportable") or item.get("excluded"):
                continue
            carousel = _dedupe(
                list(item.get("carousel_images") or [])
                + ([item.get("main_image")] if item.get("main_image") else [])
            )
            detail = _dedupe(list(item.get("detail_images") or []))
            images = _dedupe(carousel + detail)
            if not images:
                continue
            core = item.get("core_fields") or {}
            draft_row = drafts_by_id.get(int(item.get("product_draft_id") or 0)) or {}
            # 申报价：显式价（预览改过的）> 草稿列 > 导出表同款推算（成本 × 倍数，下限 150）。
            # 之前只读 declared_price 列，而该列绝大多数是 NULL，界面上就成了「—」。
            raw_payload = draft_row.get("raw_payload") if isinstance(draft_row.get("raw_payload"), dict) else {}
            # category_path 可能是字符串，也可能是列表；先判列表再取字符串，
            # 否则 list 会被 _text 变成 "['a', 'b']" 直接显示在界面上。
            raw_category = raw_payload.get("category_path")
            if isinstance(raw_category, list):
                category_name = " > ".join(_text(item) for item in raw_category if _text(item))
            else:
                category_name = _text(raw_category)
            declared_price, declared_estimated = _declared_price_cny(
                explicit=core.get("declared_price") or draft_row.get("declared_price"),
                suggested=core.get("suggested_price"),
                cost=draft_row.get("cost"),
            )
            entries.append(
                {
                    "source": "ai",
                    "id": f"ai:{task_id}:{item.get('item_id')}",
                    "title": str(item.get("title") or "").strip(),
                    "subtitle": str(task.get("title") or ""),
                    "status": str(item.get("status") or ""),
                    "images": images,
                    "carousel_images": carousel,
                    "detail_images": detail,
                    "main_image": str(item.get("main_image") or ""),
                    "updated_at": str(task.get("updated_at") or ""),
                    "task_id": task_id,
                    "draft_id": item.get("product_draft_id"),
                    "sku": _text(core.get("sku")),
                    "declared_price": declared_price,
                    "declared_price_estimated": declared_estimated,
                    # 草稿自带的类目（AI 处理阶段采集到的类目路径，可能是空）
                    "category_name": category_name,
                    # 建议售价：导出表这一列留空，这里也只取显式填过的值，不拿成本冒充
                    "suggested_price": core.get("suggested_price"),
                    "stock": core.get("stock"),
                    "weight_g": core.get("weight_g"),
                    "length_cm": core.get("length_cm"),
                    "width_cm": core.get("width_cm"),
                    "height_cm": core.get("height_cm"),
                    **_draft_extra_fields(drafts_by_id.get(int(item.get("product_draft_id") or 0))),
                }
            )
    return entries


def _collect_pod_entries(service: Any, actor: Actor, batch_limit: int) -> list[dict[str, Any]]:
    batches = service.list_batches(actor, limit=max(1, batch_limit))
    summaries = [
        str(item.get("id") or "")
        for item in batches.get("batches") or []
        if str(item.get("status")) in POD_FINISHED_STATUSES and str(item.get("id") or "")
    ]
    # 并发拉批次详情（原来是串行 for，6 个批次要等 6 遍）。
    # 只读、且 service 内部是短连接，3 个线程足够；任何异常都退回串行，保证不影响可用性。
    details: dict[str, dict[str, Any]] = {}
    if len(summaries) > 1:
        try:
            from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

            with ThreadPoolExecutor(max_workers=min(3, len(summaries))) as pool:
                futures = {batch_id: pool.submit(service.get_batch, actor, batch_id) for batch_id in summaries}
                for batch_id, future in futures.items():
                    try:
                        details[batch_id] = future.result()
                    except Exception:  # noqa: BLE001 - 单个批次失败不影响其它
                        continue
        except Exception:  # noqa: BLE001 - 线程池不可用就串行
            details = {}

    entries: list[dict[str, Any]] = []
    for batch_id in summaries:
        if batch_id in details:
            batch = details[batch_id]
        else:
            try:
                batch = service.get_batch(actor, batch_id)
            except Exception:
                continue
        titles = {
            int(item.get("style_index") or 0): str(item.get("title") or "").strip()
            for item in batch.get("style_titles") or []
            if item.get("title")
        }
        # 一个「款」会有多个变体（variant_index 0..3），以前按 item 逐个出卡片，
        # 导致同一款铺 4 张一模一样的卡、而且 4 张的 id 完全相同。
        # 正确口径：一款一张卡，变体图合并进 images。
        grouped: dict[int, list[dict[str, Any]]] = {}
        for item in batch.get("items") or []:
            if str(item.get("status")) != "completed":
                continue
            if item.get("export_selected") is False:
                continue
            grouped.setdefault(int(item.get("style_index") or 0), []).append(item)

        for style_index, variants in sorted(grouped.items()):
            variants.sort(key=lambda entry: int(entry.get("variant_index") or 0))
            primary = variants[0]
            images = _dedupe(
                [
                    primary.get("public_url"),
                    primary.get("composite_preview_url"),
                    primary.get("pattern_preview_url"),
                ]
                + [variant.get("public_url") for variant in variants[1:]]
            )
            if not images:
                continue
            item = primary
            listing = item.get("listing_fields") if isinstance(item.get("listing_fields"), dict) else {}
            # 申报价 / 重量 / 尺寸都在 listing_fields.skus（导出模板第 9、11-15 列取的就是这里）。
            # ⚠️ pod_customization 的生成结果（batch_items / style_grid_results）**没有 skus 字段**，
            # 以前只读 item["skus"]，于是草稿库里的货号/重量/尺寸永远是空的。
            listing_skus = listing.get("skus") if isinstance(listing.get("skus"), list) else []
            listing_sku = listing_skus[0] if listing_skus else {}
            item_skus = item.get("skus") if isinstance(item.get("skus"), list) else []
            first_sku = item_skus[0] if item_skus else (listing_sku or {})
            # 类目：导出模板「产品分类」列就是 listing_fields.category_name
            category_name = _text(listing.get("category_name"))
            declared_price, declared_estimated = _declared_price_cny(
                explicit=listing_sku.get("declared_price")
                or first_sku.get("declared_price")
                or listing.get("declared_price"),
                suggested=listing.get("suggested_price_usd"),
            )
            # 重量/尺寸同样优先取 listing_fields.skus（导出表第 15/12-14 列的来源）
            if listing_sku.get("weight_g") not in (None, ""):
                first_sku = {**first_sku, "weight_g": listing_sku.get("weight_g")}
            spec_pairs = _spec_pairs(first_sku, listing)
            # 长宽高：规格卡表格里存的就是厘米（display_unit 只影响展示），
            # 不能走 export 的 _sku_dimensions —— 它会把值换算成 display_unit（英寸）再写进标着 cm 的列。
            dim_length, dim_width, dim_height = _spec_card_dimensions(
                listing, str(first_sku.get("name") or "")
            )
            entries.append(
                {
                    "source": "pod",
                    "id": f"pod:{batch_id}:{style_index}",
                    "variant_count": len(variants),
                    "title": titles.get(style_index) or str(batch.get("title") or ""),
                    "subtitle": str(batch.get("title") or ""),
                    "status": str(item.get("status") or ""),
                    "images": images,
                    "carousel_images": images,
                    "detail_images": [],
                    "main_image": images[0],
                    "updated_at": str(batch.get("updated_at") or item.get("updated_at") or ""),
                    "batch_id": batch_id,
                    "style_index": style_index,
                    # 产品货号：POD-001 这种（导出表第 4 列同一规则），不是第 10 列的 "1"
                    "sku": _pod_product_code(listing, first_sku, style_index),
                    "declared_price": declared_price,
                    "declared_price_estimated": declared_estimated,
                    "category_name": category_name,
                    # 导出模板里建议售价统一留空；这里也留空，避免把 USD 金额当成分
                    "suggested_price": None,
                    "stock": first_sku.get("stock") or listing.get("stock"),
                    "weight_g": first_sku.get("weight_g") or listing.get("weight_g"),
                    "length_cm": dim_length
                    or first_sku.get("length_cm")
                    or listing.get("length_cm"),
                    "width_cm": dim_width or first_sku.get("width_cm") or listing.get("width_cm"),
                    "height_cm": dim_height
                    or first_sku.get("height_cm")
                    or listing.get("height_cm"),
                    "description": _text(listing.get("description")),
                    "source_url": _text(listing.get("source_url")),
                    "spec_name_1": spec_pairs[0][0] if len(spec_pairs) > 0 else "",
                    "spec_value_1": spec_pairs[0][1] if len(spec_pairs) > 0 else "",
                    "spec_name_2": spec_pairs[1][0] if len(spec_pairs) > 1 else "",
                    "spec_value_2": spec_pairs[1][1] if len(spec_pairs) > 1 else "",
                }
            )
    return entries


def _spec_card_dimensions(
    listing: dict[str, Any], sku_name: str
) -> tuple[Any, Any, Any]:
    """规格卡表格里这个 SKU 的长宽高。

    表格第一行是表头 `["SKU", "Length", "Width", "Height"]`，之后每行一张图/一个 SKU。
    **单元格里的数字始终是厘米**（display_unit 只决定界面显示成 cm 还是 in），
    所以这里原样取，不要做单位换算。
    """
    spec_card = listing.get("spec_card")
    cells = spec_card.get("cells") if isinstance(spec_card, dict) else None
    if not isinstance(cells, list) or not sku_name:
        return (None, None, None)
    for row in cells:
        if not isinstance(row, list) or len(row) < 4:
            continue
        if str(row[0]).strip() == sku_name:
            return (row[1], row[2], row[3])
    return (None, None, None)


def _pod_product_code(listing: dict[str, Any], sku: dict[str, Any], style_index: int) -> str:
    """产品货号（导出表第 4 列 / 发布页的 ParentSKU）。

    取 `POD-001` 这种，而不是第 10 列那个「SKU 货号」——后者往往就是 "1"、"s"，
    跨批次会撞号，发布时 Temu 会判「货号已存在」。两者都按导出表同一套规则算，
    风格与导出表保持一致。
    """
    # 快照里显式存了货号（老批次）就直接用，别覆盖
    explicit = _text(sku.get("sku_code") or listing.get("sku_code"))
    if explicit:
        return explicit
    try:
        from ..pod_customization.export import (  # noqa: PLC0415 - 避免连带导入整个 POD 包
            _product_code,
            _sku_code,
        )

        code = _product_code(listing, int(style_index or 1))
        if code:
            return code
        return _sku_code(sku, int(style_index or 1), 1)
    except Exception:  # noqa: BLE001 - 取不到就让界面显示「未填」，不要拖垮草稿库
        return ""


def _dedupe(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _declared_price_cny(
    explicit: Any = None, suggested: Any = None, cost: Any = None
) -> tuple[float | None, bool]:
    """按导出表的规则推算申报价（单位：元）。

    返回 (金额, 是否推算)。推算逻辑和 `product_processing.domain.workbooks`
    里的 `_build_dxm_row` 完全一致，保证草稿库看到的价格就是导出表里的那一列。
    """
    parsed = _parse_money(explicit)
    if parsed is not None:
        return max(parsed, DECLARED_PRICE_MIN_CNY), False
    base = _parse_money(suggested)
    if base is None or base <= 0:
        base = _parse_money(cost)
    if base is None or base <= 0:
        return DECLARED_PRICE_MIN_CNY, True
    return round(max(base * DECLARED_PRICE_MULTIPLIER, DECLARED_PRICE_MIN_CNY), 2), True


def _spec_pairs(sku: dict[str, Any], listing: dict[str, Any]) -> list[tuple[str, str]]:
    """POD 快照里的变种属性：导出模板用「尺寸 + sku.name」两列，这里按同口径取。"""
    pairs: list[tuple[str, str]] = []
    name = _text(sku.get("name"))
    if name:
        pairs.append((_text(listing.get("spec_name")) or "尺寸", name))
    attrs = sku.get("attributes")
    if isinstance(attrs, dict):
        for key, value in attrs.items():
            text_key, text_value = _text(key), _text(value)
            if text_key and text_value:
                pairs.append((text_key, text_value))
    return pairs[:2]


def _draft_extra_fields(draft: dict[str, Any] | None) -> dict[str, Any]:
    """从草稿补充导入模板需要、但预览里没有的字段。

    变种属性在 raw_payload.source_variant_records[0].attributes（形如 {"颜色": "黑色"}），
    导出时就是靠它填「变种属性名称一/值一、名称二/值二」。
    """
    if not draft:
        return {}
    raw = draft.get("raw_payload") or {}
    if not isinstance(raw, dict):
        raw = {}
    variants = raw.get("source_variant_records") or []
    attributes: dict[str, str] = {}
    if isinstance(variants, list):
        for record in variants:
            if not isinstance(record, dict):
                continue
            attrs = record.get("attributes")
            if isinstance(attrs, dict) and attrs:
                for key, value in attrs.items():
                    name = _text(key)
                    if name and name not in attributes:
                        attributes[name] = _text(value)
            if len(attributes) >= 2:
                break
    pairs = list(attributes.items())[:2]
    return {
        "description": _text(draft.get("description")),
        "source_url": _text(raw.get("source_url") or draft.get("source_ref")),
        "spec_name_1": pairs[0][0] if len(pairs) > 0 else "",
        "spec_value_1": pairs[0][1] if len(pairs) > 0 else "",
        "spec_name_2": pairs[1][0] if len(pairs) > 1 else "",
        "spec_value_2": pairs[1][1] if len(pairs) > 1 else "",
    }
