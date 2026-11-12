"""草稿库聚合：把 AI 处理结果与 POD 成品统一成发布可用的条目。
只读聚合，不修改任务/POD 数据。图片保留原始地址（本地媒体路径或公网 URL），
由发布页在生成 payload 前统一上传到 Temu 图床。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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

# 全量搜索的扫描上限：单个任务最多返回多少条命中。
# 正常任务几十到几百个；真出现几千个的异常任务时搜索不能一直读，扫到这里就停并提示。
SEARCH_MAX_ITEMS = 2000
# 搜索要扫多少个 AI 批次。列表为了首屏快只看最近 8 个批次，
# 但「搜自己的商品」不能只管这 8 个 —— 用户 28 个批次里的老商品也必须搜得到。
# 40 个批次 × 每批几毫秒 SQL（匹配在库里做），实测仍在一百多毫秒。
SEARCH_TASK_LIMIT = 40


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
    item_limit: int = 20,
    item_offset: int = 0,
) -> dict[str, Any]:
    warnings: list[str] = []
    ai_entries: list[dict[str, Any]] = []
    pod_entries: list[dict[str, Any]] = []
    ai_has_more = False

    if product_processing is not None:
        try:
            ai_entries, ai_has_more = _collect_ai_entries(
                product_processing,
                actor,
                task_limit,
                item_limit=item_limit,
                item_offset=item_offset,
            )
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
        # 还有没取出来的商品（某个任务的 item_count > offset + limit）→ 前端给「加载更多」
        "has_more": ai_has_more,
        "item_offset": max(0, int(item_offset)),
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
        # 是否还有未加载的商品（跨任务汇总）：True 时前端显示「加载更多」
        "has_more": bool(payload.get("has_more")),
        "item_offset": int(payload.get("item_offset") or 0),
        "source": wanted if wanted in {"ai", "pod"} else "all",
        "query": str(query or "").strip(),
    }


def scan_draft_library(
    *,
    product_processing: Any,
    pod_service: Any,
    actor: Actor,
    query: str,
    task_limit: int = SEARCH_TASK_LIMIT,
    batch_limit: int = 6,
) -> dict[str, Any]:
    """全量搜索的第一趟：在**整个库**里按关键字筛，先不投影图片。

    普通浏览只聚合最近 8 个批次、每个批次前 20 个商品（一屏够用、成本恒定），
    所以搜索不能复用那份结果 —— 用户搜「蓝色桌布」，这条可能在某个任务的第 480 个，
    甚至在列表根本没显示的老批次里。这里把每个任务的任务项全部扫一遍（匹配在库里做），
    命中之后再按页补图片。

    返回的 AI 条目 **images 为空**，由 `materialize_draft_search` 补。
    """
    needle = str(query or "").strip().lower()
    warnings: list[str] = []
    ai_entries: list[dict[str, Any]] = []
    pod_entries: list[dict[str, Any]] = []

    if product_processing is not None and needle:
        try:
            ai_entries = _scan_ai_matches(
                product_processing, actor, task_limit, needle, warnings
            )
        except Exception as exc:  # noqa: BLE001 - 搜索失败要能看懂原因，别整页炸掉
            warnings.append(f"AI 处理结果搜索失败：{exc}")

    if pod_service is not None:
        try:
            pod_entries = _collect_pod_entries(pod_service, actor, batch_limit)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"POD 结果读取失败：{exc}")
        if needle:
            pod_entries = [item for item in pod_entries if _matches_query(item, needle)]

    ai_entries.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    pod_entries.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return {
        "query": str(query or "").strip(),
        "ai": ai_entries,
        "pod": pod_entries,
        "warnings": warnings,
        # 搜索时计数跟着命中走：tab 上的数字必须和列表里能看到的条数对得上
        "counts": {
            "ai": len(ai_entries),
            "pod": len(pod_entries),
            "total": len(ai_entries) + len(pod_entries),
        },
    }


def materialize_draft_search(
    scan: dict[str, Any],
    *,
    product_processing: Any,
    actor: Actor,
    source: str = "",
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    exclude_ids: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """全量搜索的第二趟：切页 + 只给这一页的 AI 条目补图片投影。

    图片投影（`task_preview_media`）是这条链路上唯一贵的一步，
    所以只在真正要显示的那一页做，翻页照样快。
    """
    wanted = str(source or "").strip().lower()
    if wanted not in {"ai", "pod"}:
        wanted = ""
    excluded = set(exclude_ids or ())

    def _keep(item: dict[str, Any]) -> bool:
        if excluded and str(item.get("id") or "") in excluded:
            return False
        return not wanted or item.get("source") == wanted

    merged = [item for item in (scan.get("ai") or []) + (scan.get("pod") or []) if _keep(item)]
    merged.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)

    size = max(1, min(MAX_PAGE_SIZE, int(page_size or DEFAULT_PAGE_SIZE)))
    total = len(merged)
    pages = max(1, (total + size - 1) // size)
    current = max(1, min(pages, int(page or 1)))
    start = (current - 1) * size
    window = merged[start : start + size]

    _failed = _fill_ai_images(product_processing, actor, window)
    # 补完还是没有图（草稿图片全丢）的条目仍按普通路径的口径剔掉：
    # 卡片上没有图就等于没法发布，列出来反而让人以为能点。
    entries = [item for item in window if item.get("images")]
    warnings = list(scan.get("warnings") or [])
    if _failed:
        # 别静默返回空列表：用户会以为「库里没有」，其实是图没读出来
        warnings.append(
            f"{_failed} 个批次的图片读取失败，这些商品暂时不在搜索结果里，稍后刷新重试"
        )

    return {
        "entries": entries,
        "warnings": warnings,
        "counts": dict(scan.get("counts") or {}),
        "total": total,
        "page": current,
        "page_size": size,
        "pages": pages,
        # 搜索是把全量扫完再切页的，没有「还没加载」的东西
        "has_more": False,
        "item_offset": 0,
        "source": wanted or "all",
        "query": str(scan.get("query") or ""),
    }


def _task_summaries(service: Any, workspace_id: str, task_limit: int) -> tuple[list[dict[str, Any]], int]:
    """取最近若干个批次的轻量摘要（不读任务条目）。

    有轻量接口就用它：`task_history` 会把每个任务的条目一起读出来，
    搜全库时那是纯浪费。测试里的假服务没有这个方法，退回 `task_history`。
    """
    limit = max(1, task_limit)
    summaries = getattr(service.repository, "list_task_summaries", None)
    if callable(summaries):
        try:
            tasks, total = summaries(limit, workspace_id)
            return list(tasks or []), int(total or 0)
        except Exception:  # noqa: BLE001 - 退回历史接口，别让搜索直接失败
            pass
    history = service.task_history(limit, workspace_id)
    return list(history.get("tasks") or []), int(history.get("total") or 0)


def _scan_ai_matches(
    service: Any,
    actor: Actor,
    task_limit: int,
    needle: str,
    warnings: list[str],
) -> list[dict[str, Any]]:
    """并发扫最近若干个任务的全部任务项，返回关键字命中的条目（无图片）。"""
    tasks, total_tasks = _task_summaries(service, actor.workspace_id, task_limit)
    # 批次比上限还多 → 更早的批次没搜到，必须说出来，别让用户以为「搜不到＝没有」
    if total_tasks > max(1, task_limit):
        warnings.append(f"批次太多，只搜了最近 {len(tasks)} 个批次里的商品")
    finished: list[dict[str, Any]] = []
    for task in tasks:
        if str(task.get("status")) not in AI_FINISHED_STATUSES:
            continue
        if int(task.get("success_count") or 0) <= 0:
            continue
        finished.append(task)
    if not finished:
        return []

    # 匹配一次查完所有批次（每个批次开一次会话的话，会话开销比查询本身还贵）。
    # 「搜批次名」的批次单独走 match_all：整个批次都算命中。
    title_hits = [task for task in finished if needle in str(task.get("title") or "").lower()]
    others = [task for task in finished if task not in title_hits]
    matched: dict[int, list[int]] = {}
    try:
        if others:
            found = service.repository.search_task_items_bulk(
                [int(task["task_id"]) for task in others],
                needle,
                limit_per_task=SEARCH_MAX_ITEMS,
                cancelled_task_ids=[
                    int(task["task_id"])
                    for task in others
                    if str(task.get("status")) == "cancelled"
                ],
            )
            # 没命中的批次也要留个空列表：「查过、没中」和「还没查」必须分得开，
            # 否则每个批次都要再单独查一次库（25 个批次多花 0.15 秒）。
            for task in others:
                matched[int(task["task_id"])] = found.get(int(task["task_id"]), [])
        if title_hits:
            found = service.repository.search_task_items_bulk(
                [int(task["task_id"]) for task in title_hits],
                needle,
                limit_per_task=SEARCH_MAX_ITEMS,
                match_all=True,
            )
            for task in title_hits:
                matched[int(task["task_id"])] = found.get(int(task["task_id"]), [])
    except Exception:  # noqa: BLE001 - 查询挂掉就让下面逐个任务自己查，别整页空
        matched = {}

    def scan_one(task: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            return _scan_ai_task(
                service,
                actor,
                task,
                needle,
                warnings,
                matched.get(int(task["task_id"])),
            )
        except Exception:  # noqa: BLE001 - 单个任务扫挂了只跳过它自己
            return []

    with ThreadPoolExecutor(max_workers=min(4, len(finished))) as pool:
        batches = list(pool.map(scan_one, finished))
    return [entry for batch in batches for entry in batch]


def _scan_ai_task(
    service: Any,
    actor: Actor,
    task: dict[str, Any],
    needle: str,
    warnings: list[str],
    matched_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """扫一个任务：匹配在库里做（JSON1 + LIKE），只为命中的条目建条目。"""
    # 批量匹配已经明确「这个批次一条都没中」→ 连任务元数据都不用读
    if matched_ids is not None and not matched_ids:
        return []
    task_id = int(task["task_id"])
    task_title = str(task.get("title") or "")
    # 搜的是批次名 → 整个任务都算命中（列表里 subtitle 也参与匹配）
    match_all = bool(needle) and needle in task_title.lower()
    preview = service.task_search_preview(
        task_id,
        needle,
        workspace_id=actor.workspace_id,
        limit=SEARCH_MAX_ITEMS,
        match_all=match_all,
        matched_ids=matched_ids,
    )
    items = preview.get("items") or []
    if preview.get("truncated"):
        warnings.append(
            f"任务「{task_title or task_id}」命中超过 {SEARCH_MAX_ITEMS} 条，"
            f"只返回了前 {SEARCH_MAX_ITEMS} 条"
        )
    if not items:
        return []
    draft_ids = [
        int(item["product_draft_id"])
        for item in items
        if item.get("product_draft_id") is not None
    ]
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
    entries: list[dict[str, Any]] = []
    for item in items:
        entry = _build_ai_entry(
            task,
            item,
            drafts_by_id.get(int(item.get("product_draft_id") or 0)),
            task_id,
            require_images=False,
        )
        if entry is not None:
            entries.append(entry)
    return entries


def _fill_ai_images(
    product_processing: Any,
    actor: Actor,
    entries: list[dict[str, Any]],
) -> int:
    """给这一页里还没图的 AI 条目补图片投影（按任务分组，一次批量取）。

    返回**投影失败的任务数**：一路都失败时上层要提示用户，而不是安静地空列表。
    """
    if product_processing is None:
        return 0
    failed = 0
    pending: dict[int, list[dict[str, Any]]] = {}
    for item in entries:
        if item.get("source") != "ai" or item.get("images"):
            continue
        draft_id = item.get("draft_id")
        task_id = item.get("task_id")
        try:
            draft_id = int(draft_id or 0)
            task_id = int(task_id or 0)
        except (TypeError, ValueError):
            continue
        if draft_id <= 0 or task_id <= 0:
            continue
        pending.setdefault(task_id, []).append(item)

    for task_id, items in pending.items():
        by_draft: dict[int, dict[str, Any]] = {}
        try:
            payload = product_processing.task_preview_media(
                task_id,
                [int(item["draft_id"]) for item in items],
                workspace_id=actor.workspace_id,
            )
            raw = payload.get("items") if isinstance(payload, dict) else {}
            if isinstance(raw, dict):
                by_draft = {int(key): value for key, value in raw.items() if str(key).isdigit()}
        except Exception:  # noqa: BLE001 - 图取不到就当这条没图，下面会剔掉
            failed += 1
            by_draft = {}
        for item in items:
            projected = by_draft.get(int(item.get("draft_id") or 0))
            if not isinstance(projected, dict):
                continue
            carousel = _dedupe(
                list(projected.get("carousel_images") or [])
                + ([projected.get("main_image")] if projected.get("main_image") else [])
            )
            detail = _dedupe(list(projected.get("detail_images") or []))
            item["carousel_images"] = carousel
            item["detail_images"] = detail
            item["images"] = _dedupe(carousel + detail)
            item["main_image"] = str(projected.get("main_image") or "")
    return failed


def _collect_ai_entries(
    service: Any,
    actor: Actor,
    task_limit: int,
    *,
    item_limit: int = 20,
    item_offset: int = 0,
) -> tuple[list[dict[str, Any]], bool]:
    """聚合最近若干个任务里「可导出」的商品。

    `item_limit` / `item_offset` 透传给 `task_preview`（它对任务项是真 LIMIT/OFFSET），
    所以「一个任务里有 500 个商品」不再被截成前 20 个 —— 前端点「加载更多」时
    调大 offset 就能把后面的取出来，而成本恒定（每批只取一页）。

    返回 ``(entries, has_more)``：调用方是按二元组解包的，
    这里**任何一个出口都必须返回两个值** —— 空库时只 return 一个 list 会让
    草稿库报「not enough values to unpack (expected 2, got 0）」，
    看起来像 AI 处理出了错，其实只是还没有任何产物。
    """
    history = service.task_history(max(1, task_limit), actor.workspace_id)
    entries: list[dict[str, Any]] = []
    finished: list[dict[str, Any]] = []
    for task in history.get("tasks") or []:
        if str(task.get("status")) not in AI_FINISHED_STATUSES:
            continue
        if int(task.get("success_count") or 0) <= 0:
            continue
        finished.append(task)
    if not finished:
        # 还没有跑完的 AI 处理任务（新装的库就是这样）：正常空态，不是错误
        return entries, False

    # 每个任务的预览是一次独立的只读查询：串行跑 8 个实测约 1 秒，
    # 并发 4 路能压到三分之一（本地库是 WAL，多读互不阻塞）。
    # 单个任务失败只跳过它自己，和原来逐个 try/except 的语义一致。
    #
    # 图片只投影前 60 条：再多就是给收件箱里的缩略图付全尺寸的代价，
    # 而一屏也就看 10 条左右。
    page_limit = max(1, int(item_limit))
    page_offset = max(0, int(item_offset))
    media_limit = min(page_limit, 60)

    def preview_one(task: dict[str, Any]) -> dict[str, Any] | None:
        try:
            return service.task_preview(
                int(task["task_id"]),
                workspace_id=actor.workspace_id,
                item_limit=page_limit,
                item_offset=page_offset,
                media_limit=media_limit,
            )
        except Exception:  # noqa: BLE001 - 单个任务读不出来不该拖垮整库
            return None

    with ThreadPoolExecutor(max_workers=min(4, len(finished))) as pool:
        previews = list(pool.map(preview_one, finished))

    has_more = False
    for task, preview in zip(finished, previews):
        if not preview:
            continue
        # 这个任务还有没取出来的商品（item_count 是任务内总数）→ 前端可以「加载更多」
        if preview.get("has_more"):
            has_more = True
        task_id = int(task["task_id"])
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
            entry = _build_ai_entry(
                task,
                item,
                drafts_by_id.get(int(item.get("product_draft_id") or 0)),
                task_id,
            )
            if entry is not None:
                entries.append(entry)
    return entries, has_more


def _build_ai_entry(
    task: dict[str, Any],
    item: dict[str, Any],
    draft_row: dict[str, Any] | None,
    task_id: int,
    *,
    require_images: bool = True,
) -> dict[str, Any] | None:
    """把一条任务项转成草稿库条目。

    ``require_images=False``：全量搜索的第一趟只取元数据（``media_limit=0`` 时
    预检把图片字段清空），命中之后才单独补图片投影，所以这一趟不能因为「没图」丢掉。
    """
    if not item.get("exportable") or item.get("excluded"):
        return None
    carousel = _dedupe(
        list(item.get("carousel_images") or [])
        + ([item.get("main_image")] if item.get("main_image") else [])
    )
    detail = _dedupe(list(item.get("detail_images") or []))
    images = _dedupe(carousel + detail)
    if require_images and not images:
        return None
    core = item.get("core_fields") or {}
    draft_row = draft_row or {}
    # 申报价：显式价（预览改过的）> 草稿列 > 导出表同款推算（成本 × 倍数，下限 150）。
    # 之前只读 declared_price 列，而该列绝大多数是 NULL，界面上就成了「—」。
    raw_payload = draft_row.get("raw_payload") if isinstance(draft_row.get("raw_payload"), dict) else {}
    # category_path 可能是字符串，也可能是列表；先判列表再取字符串，
    # 否则 list 会被 _text 变成 "['a', 'b']" 直接显示在界面上。
    raw_category = raw_payload.get("category_path")
    if isinstance(raw_category, list):
        category_name = " > ".join(_text(value) for value in raw_category if _text(value))
    else:
        category_name = _text(raw_category)
    declared_price, declared_estimated = _declared_price_cny(
        explicit=core.get("declared_price") or draft_row.get("declared_price"),
        suggested=core.get("suggested_price"),
        cost=draft_row.get("cost"),
    )
    return {
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
        "stock": _trusted_stock(core.get("stock")),
        "weight_g": core.get("weight_g"),
        "length_cm": core.get("length_cm"),
        "width_cm": core.get("width_cm"),
        "height_cm": core.get("height_cm"),
        **_draft_extra_fields(draft_row or None),
    }


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
            # ⚠️ listing_fields 挂在**批次**上（get_batch 顶层），items 里没有这个键。
            # 以前读 item["listing_fields"]，所以 category_name / skus / spec_card 全是空 ——
            # 草稿库的价格只能退到下限 ¥150，货号/重量/尺寸一律显示「未填」。
            listing = batch.get("listing_fields") if isinstance(batch.get("listing_fields"), dict) else {}
            if not listing and isinstance(item.get("listing_fields"), dict):
                listing = item["listing_fields"]
            # replica（爆款复刻）模式每个款有自己的一份 listing_fields，优先用款级的
            for target in batch.get("replica_targets") or []:
                if not isinstance(target, dict):
                    continue
                if int(target.get("style_index") or 0) != int(style_index):
                    continue
                if isinstance(target.get("listing_fields"), dict):
                    listing = target["listing_fields"]
                break
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
                    # 0 是合法库存（先建品后补货），不能用 `or` 兜成 listing 的值
                    "stock": (
                        first_sku.get("stock")
                        if first_sku.get("stock") is not None
                        else listing.get("stock")
                    ),
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


# 上游「库存」超过这个数就当它没给：1688 上大量商家把库存填成
# 99999999 / 100005820 这种占位值，原样带到 Temu 后台毫无意义（半托管也吃不下）。
MAX_TRUSTED_STOCK = 99999


def _trusted_stock(value: Any) -> int | None:
    """把上游库存收敛成「可信整数」：超范围/负数/非数字一律当没给。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or number > MAX_TRUSTED_STOCK:
        return None
    return number


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
        # 结构保持一致：下游（前端 draft-library → 发布页）按固定字段读，
        # 少几个键会在别处炸成 undefined.map / undefined.length
        return {"spec_values_1": [], "spec_values_2": [], "variants": []}
    raw = draft.get("raw_payload") or {}
    if not isinstance(raw, dict):
        raw = {}
    variants = raw.get("source_variant_records") or []
    # 一条草稿往往对应多个 SKU（一个颜色一条），价格/库存/图都按条不同。
    # 以前这里只取「第一条记录的第一个值」，11 个颜色只剩 1 个 —— 用户进发布页
    # 还得手填其余 10 个，而数据明明就在 raw_payload 里。
    name_order: list[str] = []
    values_by_name: dict[str, list[str]] = {}
    variant_rows: list[dict[str, Any]] = []
    if isinstance(variants, list):
        for record in variants:
            if not isinstance(record, dict):
                continue
            attrs = record.get("attributes")
            pairs: list[tuple[str, str]] = []
            if isinstance(attrs, dict):
                for key, value in attrs.items():
                    name, text_value = _text(key), _text(value)
                    if not name:
                        continue
                    if name not in values_by_name:
                        values_by_name[name] = []
                        name_order.append(name)
                    if text_value and text_value not in values_by_name[name]:
                        values_by_name[name].append(text_value)
                    if text_value:
                        pairs.append((name, text_value))
            cost = record.get("price_cny")
            price: float | None = None
            if _parse_money(cost) not in (None, 0):
                # 每个变体的成本可能不同（13.20 / 16.90），申报价按同一套规则各自推算
                price, _estimated = _declared_price_cny(cost=cost)
            quantity = _trusted_stock(record.get("quantity"))
            variant_rows.append(
                {
                    "sku": _text(record.get("sku_id")),
                    "color": pairs[0][1] if len(pairs) > 0 else "",
                    "size": pairs[1][1] if len(pairs) > 1 else "",
                    "price": price,
                    "stock": quantity if isinstance(quantity, int) else None,
                    "image": _text(record.get("image_url")),
                }
            )
    names = name_order[:2]
    first_values = values_by_name.get(names[0], []) if names else []
    second_values = values_by_name.get(names[1], []) if len(names) > 1 else []
    return {
        "description": _text(draft.get("description")),
        "source_url": _text(raw.get("source_url") or draft.get("source_ref")),
        "spec_name_1": names[0] if len(names) > 0 else "",
        "spec_value_1": first_values[0] if first_values else "",
        "spec_name_2": names[1] if len(names) > 1 else "",
        "spec_value_2": second_values[0] if second_values else "",
        # 整列表：发布页要按这些值铺满变种，而不是只铺第一行
        "spec_values_1": first_values[:20],
        "spec_values_2": second_values[:20],
        "variants": variant_rows[:20],
    }
