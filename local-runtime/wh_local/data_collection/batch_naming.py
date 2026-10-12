"""采集批次展示名（display_name）的纯函数生成器。

展示名格式统一为 ``{渠道}·{平台}·{内容}·{月-日 时:分}``，用于前端列表里替代
随机 UUID 截断显示；批次 ID（run_id / batch_id）本身保持不变（它是跨表外键）。

本模块只做字符串计算，不依赖数据库与宿主，便于单测与在三处生成点 / 读取兜底复用。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlsplit

# 渠道展示段。
CHANNEL_DAILY_SELECTION = "选品"
CHANNEL_SHOP = "整店"
CHANNEL_PLUGIN = "插件"

# 段落连接符：U+00B7 中点。
_SEPARATOR = "·"

# 内容段最大字符数，超出后截断并追加省略号。
_CONTENT_LIMIT = 24

_PLATFORM_LABELS: dict[str, str] = {
    "1688": "1688",
    "taobao": "淘宝",
    "tmall": "天猫",
    "temu": "Temu",
}

_KEYWORD_FALLBACK = "关键词采集"
_SHOP_UNRESOLVED = "等待识别店铺"
_PLUGIN_FALLBACK = "整页采集"


def daily_selection_display_name(
    criteria: Mapping[str, Any] | Any | None = None,
    metadata: Mapping[str, Any] | Any | None = None,
    *,
    created_at: Any = None,
    now: datetime | None = None,
) -> str:
    """每日选品批次的展示名：``选品·{平台}·{内容}·{时间}``。"""
    criteria_map = _as_mapping(criteria)
    metadata_map = _as_mapping(metadata)
    platform = str(criteria_map.get("collection_platform") or "")
    if not platform:
        platform = str(_as_mapping(metadata_map.get("source_link")).get("platform") or "")
    content = _daily_selection_content(criteria_map, metadata_map)
    return _assemble(
        CHANNEL_DAILY_SELECTION, platform, content, created_at=created_at, now=now
    )


def shop_display_name(
    shop_name: str = "",
    platform: str = "",
    *,
    created_at: Any = None,
    now: datetime | None = None,
) -> str:
    """整店采集批次的展示名：``整店·{平台}·{店铺名}·{时间}``。"""
    content = str(shop_name or "").strip() or _SHOP_UNRESOLVED
    return _assemble(CHANNEL_SHOP, platform, content, created_at=created_at, now=now)


def plugin_display_name(
    page_url: str = "",
    *,
    created_at: Any = None,
    now: datetime | None = None,
) -> str:
    """插件采集批次的展示名：``插件·{平台}·{内容}·{时间}``。

    平台取自 ``page_url`` 域名，内容取自它的 ``keywords`` 查询参数（已 urldecode）。
    """
    url = str(page_url or "").strip()
    content = _plugin_content(url)
    return _assemble(
        CHANNEL_PLUGIN,
        _platform_from_url(url),
        content,
        created_at=created_at,
        now=now,
    )


def format_timestamp(value: Any = None, *, now: datetime | None = None) -> str:
    """把批次创建时间转成本地时间 ``%m-%d %H:%M``。

    兼容两种历史存储：带时区的 ISO 字符串（daily_selection_runs）与不带时区的
    UTC 字符串（shop / plugin 由 SQLite ``datetime('now')`` 产生）。``value`` 为空
    时回落到当前本地时间。
    """
    moment = _local_datetime(value, now=now)
    return moment.strftime("%m-%d %H:%M")


def _assemble(
    channel: str,
    platform: str,
    content: str,
    *,
    created_at: Any,
    now: datetime | None,
) -> str:
    parts = [channel]
    label = _platform_label(platform)
    if label:
        # 未知 / 空平台整段省略，避免出现 ``··``。
        parts.append(label)
    parts.append(_clip(content))
    parts.append(format_timestamp(created_at, now=now))
    return _SEPARATOR.join(parts)


def _daily_selection_content(
    criteria: Mapping[str, Any], metadata: Mapping[str, Any]
) -> str:
    keywords = criteria.get("keywords")
    selected: list[str] = []
    if isinstance(keywords, (list, tuple)):
        for keyword in keywords:
            text = str(keyword or "").strip()
            if text:
                selected.append(text)
            if len(selected) >= 2:
                break
    if selected:
        return "/".join(selected)
    seed_title = str(
        _as_mapping(metadata.get("source_link")).get("seed_title") or ""
    ).strip()
    if seed_title:
        return seed_title
    return _KEYWORD_FALLBACK


def _plugin_content(url: str) -> str:
    if not url:
        return _PLUGIN_FALLBACK
    try:
        query = parse_qs(urlsplit(url).query, keep_blank_values=False)
    except ValueError:
        return _PLUGIN_FALLBACK
    values = query.get("keywords") or []
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return _PLUGIN_FALLBACK


def _platform_label(platform: str) -> str:
    key = str(platform or "").strip().casefold()
    return _PLATFORM_LABELS.get(key, "")


def _platform_from_url(url: str) -> str:
    if not url:
        return ""
    try:
        host = (urlsplit(url).hostname or "").casefold()
    except ValueError:
        return ""
    if not host:
        return ""
    if host == "1688.com" or host.endswith(".1688.com"):
        return "1688"
    if host == "tmall.com" or host.endswith(".tmall.com"):
        return "tmall"
    if host == "taobao.com" or host.endswith(".taobao.com"):
        return "taobao"
    if host == "temu.com" or host.endswith(".temu.com"):
        return "temu"
    return ""


def _clip(text: str) -> str:
    value = str(text or "").strip()
    if len(value) > _CONTENT_LIMIT:
        return value[:_CONTENT_LIMIT] + "…"
    return value


def _local_datetime(value: Any, *, now: datetime | None) -> datetime:
    if value is None or value == "":
        return now or datetime.now()
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value).strip()
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return now or datetime.now()
    if moment.tzinfo is None:
        # shop / plugin 的 created_at 由 SQLite datetime('now') 产生，为 UTC。
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone()


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            result = dump(mode="python")
        except TypeError:
            result = dump()
        if isinstance(result, Mapping):
            return result
    return {}
