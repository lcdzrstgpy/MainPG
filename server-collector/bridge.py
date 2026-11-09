"""成本桥接接口（/api/bridge/*）。

给工作台客户端提供：服务器账号列表、绑定、链接级数据拉取、成本读写。
鉴权：共享头 X-Bridge-Key；服务端未配置 BRIDGE_KEY 时只允许本机访问。
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request

from store import CollectorStore

BRIDGE_KEY = os.environ.get("BRIDGE_KEY", "").strip()

# 已通过真实订单页确认的状态码：2=待发货、5=已签收；其余按文案匹配。
STATUS_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "pending": {"codes": ("2",), "texts": ("待发货", "待处理")},
    "shipped": {"codes": (), "texts": ("已发货", "运输中", "待收货")},
    "signed": {"codes": ("5",), "texts": ("已签收",)},
    "canceled": {"codes": (), "texts": ("取消", "已关闭")},
}


def _authorize(
    request: Request, x_bridge_key: str | None = Header(default=None)
) -> None:
    if BRIDGE_KEY:
        if str(x_bridge_key or "") != BRIDGE_KEY:
            raise HTTPException(401, "bridge key 无效")
        return
    host = str(getattr(request.client, "host", "") or "")
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(401, "未配置 bridge key，仅允许本机访问")


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _date_of(value: Any) -> str:
    text = str(value or "").strip().replace("/", "-")
    return text[:10]


def _status_matches(item: dict[str, Any], keys: list[str]) -> bool:
    raw = str(item.get("order_status") or "").strip()
    code = str(item.get("order_status_code") or "").strip()
    for key in keys:
        aliases = STATUS_ALIASES.get(key)
        if aliases is None:
            if (raw and key in raw) or (code and key == code):
                return True
            continue
        if code and code in aliases["codes"]:
            return True
        if raw and any(word in raw for word in aliases["texts"]):
            return True
    return False


def _shop_name(account: dict[str, Any]) -> str:
    return (
        str(account.get("shop_name") or "")
        or str(account.get("alias") or "")
        or str(account.get("phone_tail") or "")
    )


def _aggregate(
    store: CollectorStore,
    account_ids: list[str],
    accounts_meta: dict[str, dict[str, Any]],
    status_keys: list[str],
    date_from: str,
    date_to: str,
    keyword: str,
) -> tuple[list[dict[str, Any]], int, int]:
    """按「店铺 + SKC」聚合链接级数据；状态与日期在行级先过滤再聚合。"""
    costs = {
        (row["account_id"], row["skc_id"]): row
        for row in store.list_costs(account_ids)
    }
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    scanned = 0
    skipped = 0
    needle = keyword.strip().lower()
    for account_id in account_ids:
        snapshot = store.latest_snapshot(account_id, "orders")
        items = ((snapshot or {}).get("payload") or {}).get("items") or []
        shop = _shop_name(accounts_meta.get(account_id) or {})
        for item in items:
            if not isinstance(item, dict):
                continue
            scanned += 1
            skc_id = str(item.get("skc_id") or "").strip()
            if not skc_id:
                skipped += 1
                continue
            if status_keys and not _status_matches(item, status_keys):
                continue
            day = _date_of(item.get("order_time"))
            if date_from or date_to:
                if not day:
                    continue
                if date_from and day < date_from:
                    continue
                if date_to and day > date_to:
                    continue
            if needle:
                blob = " ".join(
                    str(item.get(key) or "")
                    for key in (
                        "goods_name",
                        "skc_id",
                        "sku_id",
                        "ext_code",
                        "parent_order_sn",
                        "sub_order_sn",
                    )
                ).lower()
                if needle not in blob:
                    continue
            key = (account_id, skc_id)
            group = groups.get(key)
            if group is None:
                group = {
                    "account_id": account_id,
                    "shop_name": shop,
                    "skc_id": skc_id,
                    "sku_id": "",
                    "goods_name": "",
                    "image_url": "",
                    "quantity": 0,
                    "declared_price": 0.0,
                    "gmv": 0.0,
                    "currency": "",
                    "order_status": "",
                    "last_order_time": "",
                    "_orders": set(),
                    "_skus": set(),
                }
                groups[key] = group
            if not group["goods_name"] and item.get("goods_name"):
                group["goods_name"] = str(item["goods_name"])
            if not group["image_url"] and item.get("image_url"):
                group["image_url"] = str(item["image_url"])
            sku = str(item.get("sku_id") or "").strip()
            if sku:
                group["_skus"].add(sku)
                if not group["sku_id"]:
                    group["sku_id"] = sku
            group["quantity"] += int(_num(item.get("quantity")))
            group["declared_price"] += _num(item.get("declared_price"))
            group["gmv"] += _num(item.get("gmv"))
            if not group["currency"] and item.get("currency"):
                group["currency"] = str(item["currency"])
            order_time = str(item.get("order_time") or "")
            if order_time >= group["last_order_time"]:
                group["last_order_time"] = order_time
                group["order_status"] = str(item.get("order_status") or "")
            order_key = str(
                item.get("parent_order_sn") or item.get("sub_order_sn") or ""
            )
            if order_key:
                group["_orders"].add(order_key)

    rows: list[dict[str, Any]] = []
    for group in groups.values():
        cost = costs.get((group["account_id"], group["skc_id"])) or {}
        rows.append(
            {
                "account_id": group["account_id"],
                "shop_name": group["shop_name"],
                "skc_id": group["skc_id"],
                "sku_id": group["sku_id"],
                "sku_count": len(group["_skus"]),
                "goods_name": group["goods_name"],
                "image_url": group["image_url"],
                "order_count": len(group["_orders"]),
                "quantity": group["quantity"],
                "declared_price": round(group["declared_price"], 2),
                "gmv": round(group["gmv"], 2),
                "currency": group["currency"] or "CNY",
                "order_status": group["order_status"],
                "last_order_time": group["last_order_time"],
                "unit_cost": cost.get("unit_cost"),
                "cost_currency": cost.get("currency", ""),
                "cost_updated_at": cost.get("updated_at", ""),
            }
        )
    rows.sort(key=lambda row: str(row["last_order_time"] or ""), reverse=True)
    return rows, scanned, skipped


def create_bridge_router(store: CollectorStore) -> APIRouter:
    router = APIRouter(
        prefix="/api/bridge", tags=["bridge"], dependencies=[Depends(_authorize)]
    )

    def _bound(client_id: str) -> list[str]:
        return store.binding_account_ids(client_id)

    def _selected(client_id: str, account_ids: str) -> list[str]:
        bound = set(_bound(client_id))
        wanted = [value.strip() for value in account_ids.split(",") if value.strip()]
        return [value for value in (wanted or list(bound)) if value in bound]

    @router.get("/bindings")
    def list_bindings(client_id: str = Query(...)) -> dict[str, Any]:
        accounts = {account["id"]: account for account in store.list_accounts()}
        rows: list[dict[str, Any]] = []
        for binding in store.list_bindings(client_id):
            account = accounts.get(binding["account_id"]) or {}
            rows.append(
                {
                    "account_id": binding["account_id"],
                    "shop_name": _shop_name(account),
                    "phone_tail": account.get("phone_tail", ""),
                    "site": account.get("site", ""),
                    "bound_at": binding["bound_at"],
                }
            )
        return {"client_id": client_id, "accounts": rows}

    @router.post("/bindings")
    def save_bindings(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        client_id = str(payload.get("client_id") or "").strip()
        phone = str(payload.get("phone") or "").strip()
        password = str(payload.get("password") or "")
        if not client_id:
            raise HTTPException(400, "缺少 client_id")
        result = store.bind_with_credentials(client_id, phone, password)
        if not result.get("ok"):
            reason = result.get("reason")
            if reason == "missing":
                raise HTTPException(400, "请输入 Temu 账号和密码")
            if reason == "account_not_found":
                raise HTTPException(404, "服务器上没有该 Temu 账号，请先在服务器端登录采集")
            raise HTTPException(401, "Temu 账号或密码不正确")
        return result

    @router.delete("/bindings")
    def delete_binding(
        client_id: str = Query(...), account_id: str = Query(default="")
    ) -> dict[str, Any]:
        return {"removed": store.delete_binding(client_id, account_id)}

    @router.get("/items")
    def items(
        client_id: str = Query(...),
        account_ids: str = Query(default=""),
        status: str = Query(default=""),
        date_from: str = Query(default=""),
        date_to: str = Query(default=""),
        keyword: str = Query(default=""),
        missing_cost: int = Query(default=0),
        page: int = Query(default=1),
        page_size: int = Query(default=50),
    ) -> dict[str, Any]:
        selected = _selected(client_id, account_ids)
        accounts_meta = {account["id"]: account for account in store.list_accounts()}
        status_keys = [key.strip() for key in status.split(",") if key.strip()]
        rows, scanned, skipped = _aggregate(
            store,
            selected,
            accounts_meta,
            status_keys,
            date_from.strip(),
            date_to.strip(),
            keyword,
        )
        if missing_cost:
            rows = [row for row in rows if row.get("unit_cost") is None]
        size = max(1, min(200, int(page_size)))
        current = max(1, int(page))
        start = (current - 1) * size
        return {
            "items": rows[start : start + size],
            "total": len(rows),
            "page": current,
            "page_size": size,
            "scanned": scanned,
            "skipped_no_skc": skipped,
            "accounts": [
                {"account_id": account_id, "shop_name": _shop_name(accounts_meta.get(account_id) or {})}
                for account_id in selected
            ],
        }

    @router.get("/costs")
    def costs(
        client_id: str = Query(...), account_ids: str = Query(default="")
    ) -> dict[str, Any]:
        selected = _selected(client_id, account_ids)
        return {"items": store.list_costs(selected)}

    @router.post("/costs")
    def save_costs(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        client_id = str(payload.get("client_id") or "").strip()
        account_id = str(payload.get("account_id") or "").strip()
        if not client_id or not account_id:
            raise HTTPException(400, "缺少 client_id 或 account_id")
        if account_id not in _bound(client_id):
            raise HTTPException(403, "该店铺未绑定到此客户端")
        items = payload.get("items") or []
        if not isinstance(items, list):
            raise HTTPException(400, "items 必须是数组")
        return store.save_costs(account_id, items, updated_by=client_id)

    return router
