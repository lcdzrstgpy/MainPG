"""跨店铺重复数据的标记规则。

同一条订单（父单号 + 子单号 + SKU）出现在两家店时**不删数据**，
只打标记，留给组长在面板上核对；同一家店同一天的上报永远只保留一份，
并且以源数据更新时间较新的为准。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Final

# 同一家店铺被多个账号上报（多账号绑了同一家 Temu 店）。
FLAG_SAME_STORE_MULTI_ACCOUNT: Final = "same_store_multi_account"
# 同一账号的不同店铺出现了相同订单。
FLAG_SAME_ACCOUNT_DUP_ORDER: Final = "same_account_dup_order"

FLAG_LABELS: Final = {
    FLAG_SAME_STORE_MULTI_ACCOUNT: "同一店铺被多个账号上报",
    FLAG_SAME_ACCOUNT_DUP_ORDER: "同一账号的不同店铺存在重复订单",
}

BEIJING = timezone(timedelta(hours=8))


def order_data_key(
    parent_order_sn: object,
    sub_order_sn: object,
    sku_id: object,
) -> str:
    """订单行的数据 ID：父单号 + 子单号 + SKU 的稳定哈希。

    采集侧和服务器都用这个函数算，保证跨店铺比对时键一致。
    """
    raw = "|".join(
        str(part or "").strip().upper()
        for part in (parent_order_sn, sub_order_sn, sku_id)
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _parse_time(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=BEIJING)
    return parsed


def should_overwrite(existing: object, incoming: object) -> bool:
    """判断新上报是否覆盖已存数据。

    同一店铺同一天只保留一份，源更新时间更新的才覆盖；
    时间缺失或无法解析时一律不覆盖，避免空值把好数据冲掉。
    """
    new_time = _parse_time(incoming)
    if new_time is None:
        return False
    old_time = _parse_time(existing)
    return old_time is None or new_time > old_time
