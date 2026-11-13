"""服务端 TEMU 采集器 HTTP 服务。

一套最小的界面 + 后端：录入店铺账号 → 在服务器上登录 → 采集订单 → 结果落库。
不依赖工作台前端、浏览器插件或远程桌面。
"""

from __future__ import annotations

import os
import re
import math
import threading
from datetime import date, datetime
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from bridge import create_bridge_router
from dxm import DxmRunner
from freight import FreightRunner
from freight_schedule import FreightSchedule
from runner import CollectorRunner
from rotator import AutoRotator
from store import CollectorStore, now_iso
from translate import translate_many

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("COLLECTOR_DATA_DIR", ROOT / "data"))
CHROME_PATH = os.environ.get("WH_CHROME_PATH", "").strip()
MAX_PAGES = int(os.environ.get("COLLECTOR_MAX_PAGES", "20"))
MAX_DETAILS = int(os.environ.get("COLLECTOR_MAX_DETAILS", "2000"))
DETAIL_BUDGET = int(os.environ.get("COLLECTOR_DETAIL_BUDGET", "2400"))
# 详情页在页面内并发请求；批次越大越快，但同时对平台的瞬时压力也越大。
DETAIL_BATCH = int(os.environ.get("COLLECTOR_DETAIL_BATCH", "24"))
DETAIL_HTTP = os.environ.get("COLLECTOR_DETAIL_HTTP", "1").strip() != "0"
INGEST_KEY = os.environ.get("COLLECTOR_INGEST_KEY", "").strip()
# 店小秘默认账号：首次启动写入，登录页可直接自动填入。
DXM_DEFAULT_USERNAME = os.environ.get("DXM_DEFAULT_USERNAME", "Eric030524").strip()
DXM_DEFAULT_PASSWORD = os.environ.get("DXM_DEFAULT_PASSWORD", "Xxt123456").strip()
DXM_DEFAULT_LABEL = os.environ.get("DXM_DEFAULT_LABEL", "店小秘南京").strip()
# 发货平台默认账号（先接无界 ERP）：首次启动写入，登录页可直接自动填入。
WUJIE_DEFAULT_USERNAME = os.environ.get("WUJIE_DEFAULT_USERNAME", "NJXXT").strip()
WUJIE_DEFAULT_PASSWORD = os.environ.get("WUJIE_DEFAULT_PASSWORD", "123456").strip()
WUJIE_DEFAULT_LABEL = os.environ.get("WUJIE_DEFAULT_LABEL", "南京旺财-无界").strip()
# 无界 ERP 第二个子账号（客户李雪&顺丰Y2），同一个站点，只有商家账号不同。
WUJIE_SFDM_USERNAME = os.environ.get("WUJIE_SFDM_USERNAME", "SFDM").strip()
WUJIE_SFDM_PASSWORD = os.environ.get("WUJIE_SFDM_PASSWORD", "123456").strip()
WUJIE_SFDM_LABEL = os.environ.get("WUJIE_SFDM_LABEL", "客户李雪&顺丰Y2").strip()
# 浩远国际快递（雪轩亭）账号：登录后从「运费明细」按 XM 取总费用。
HY_DEFAULT_USERNAME = os.environ.get("HY_DEFAULT_USERNAME", "10772").strip()
HY_DEFAULT_PASSWORD = os.environ.get("HY_DEFAULT_PASSWORD", "213532").strip()
HY_DEFAULT_LABEL = os.environ.get("HY_DEFAULT_LABEL", "雪轩亭浩远").strip()
FREIGHT_ADAPTER_NAMES = {
    "wujie": "无界 ERP",
    "hy": "浩远国际快递",
    "rtb": "全球物流查询系统",
}
WUJIE_LOGIN_URL = "https://erp.wujie-erp.com/login"
HY_LOGIN_URL = "https://www.hy-express.com/"

store = CollectorStore(DATA_DIR)
runner = CollectorRunner(
    data_dir=DATA_DIR,
    order_script=ROOT / "temu_order_profit.js",
    chrome_path=CHROME_PATH,
    max_pages=MAX_PAGES,
    max_details=MAX_DETAILS,
    detail_budget_seconds=DETAIL_BUDGET,
    detail_batch=DETAIL_BATCH,
    detail_http=DETAIL_HTTP,
    store=store,
    temu_workflow=True,
)

dxm = DxmRunner(data_dir=DATA_DIR, chrome_path=CHROME_PATH, store=store)
freight = FreightRunner(data_dir=DATA_DIR, chrome_path=CHROME_PATH, store=store)

app = FastAPI(title="TEMU 采集器", docs_url=None, redoc_url=None)
app.include_router(create_bridge_router(store))


def _on_done(account_id: str, result: dict[str, Any]) -> None:
    # 联动流程的各阶段结果已由 worker 逐段落库，这里只跳过汇总结果，避免覆盖账号状态。
    if result.get("action") == "workflow":
        return

    # 探活只更新登录态，不动采集结果。
    if result.get("action") == "probe":
        if str(result.get("state") or "") == "guest":
            store.update_account(
                account_id, status="expired", last_error="登录已失效，请重新登录"
            )
        else:
            fields: dict[str, Any] = {"status": "ready", "last_error": ""}
            if result.get("shop_name"):
                fields["shop_name"] = result["shop_name"]
            store.update_account(account_id, **fields)
        return

    if result.get("action") == "ads":
        attempt = {"ok": bool(result.get("ok")), "message": str(result.get("message") or ""), "fetched_at": now_iso()}
        if result.get("ok") and result.get("ads"):
            store.save_snapshot(account_id, "ads", result["ads"])
        store.save_snapshot(account_id, "ads_attempt", attempt)
        return

    if result.get("action") == "settlement":
        # 结算抓取只更新结算快照，不覆盖账号的登录态 / 采集状态。
        if result.get("ok") and result.get("settlement"):
            store.save_snapshot(account_id, "settlement", result["settlement"])
            # 能抓到结算就说明登录态有效，顺手把失效状态修正回来。
            store.update_account(account_id, status="ready", last_error="")
        else:
            store.save_snapshot(
                account_id,
                "settlement",
                {"ok": False, "error": str(result.get("message") or "结算抓取失败")},
            )
        return

    ok = bool(result.get("ok"))
    fields: dict[str, Any] = {
        "status": "ready" if ok else "error",
        "last_error": "" if ok else str(result.get("message") or "执行失败"),
    }
    if ok and result.get("shop_name"):
        fields["shop_name"] = result["shop_name"]
    if ok and result.get("action", "login") == "login":
        fields["last_login_at"] = now_iso()
    if ok and result.get("items") is not None:
        fields["last_capture_at"] = now_iso()
        fields["order_count"] = len(result.get("items") or [])
        store.save_snapshot(
            account_id,
            "orders",
            {
                "items": result.get("items") or [],
                "summary": result.get("summary") or {},
                "pages": result.get("pages", 0),
                # 详情已经在行里合并过，快照只留数量，避免 500+ 单时快照过大。
                "details_count": len(result.get("details") or {}),
            },
        )
    store.update_account(account_id, **fields)
    if INGEST_KEY:
        _push_to_team(account_id, result)


def _push_to_team(account_id: str, result: dict[str, Any]) -> None:
    """把采集结果推给团队服务；失败只记日志，不影响采集本身。"""
    import json
    import urllib.request

    base = os.environ.get("COLLECTOR_TEAM_BASE", "http://127.0.0.1:8015/api/team")
    account = store.get_account(account_id) or {}
    items = result.get("items") or []
    if not items:
        return
    payload = {
        "account_id": account_id,
        "store_name": account.get("shop_name") or account.get("alias") or account.get("phone", ""),
        "site": account.get("site", "us"),
        "items": items,
        "summary": result.get("summary") or {},
    }
    request = urllib.request.Request(
        f"{base.rstrip('/')}/internal/ingest",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Ingest-Key": INGEST_KEY,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response.read()
    except Exception as error:  # noqa: BLE001
        print(f"[collector] push to team failed: {error}", flush=True)


# ---------- 店小秘 ----------

def _dxm_on_done(account_id: str, result: dict[str, Any]) -> None:
    action = str(result.get("action") or "")
    if action == "align":
        if result.get("ok"):
            pairs = result.get("pairs") or {}
            logistics = result.get("logistics") or {}
            for po, xms in pairs.items():
                store.save_dxm_pairs(
                    po, list(xms or []), account_id=account_id, logistics=logistics
                )
            fields: dict[str, Any] = {"status": "ready", "last_error": "", "last_align_at": now_iso()}
            missing = result.get("missing") or []
            if missing:
                fields["last_error"] = f"{len(missing)} 个 PO 没找到 XM"
            store.update_dxm_account(account_id, **fields)
        else:
            store.update_dxm_account(
                account_id,
                status="error",
                last_error=str(result.get("message") or "对齐失败"),
            )
        return

    if result.get("ok"):
        fields = {"status": "ready", "last_error": ""}
        if action == "login":
            fields["last_login_at"] = now_iso()
        store.update_dxm_account(account_id, **fields)
    else:
        store.update_dxm_account(
            account_id,
            status="expired" if action == "probe" else "error",
            last_error=str(result.get("message") or "执行失败"),
        )


def _freight_on_done(platform: str, result: dict[str, Any]) -> None:
    action = str(result.get("action") or "")
    if result.get("ok"):
        fields: dict[str, Any] = {"status": "ready", "last_error": ""}
        if action == "login":
            fields["last_login_at"] = now_iso()
        elif action == "query":
            fields["last_query_at"] = now_iso()
        store.update_freight_account(platform, **fields)
    else:
        message = str(result.get("message") or "执行失败")
        store.update_freight_account(
            platform,
            status="expired" if "登录" in message else "error",
            last_error=message,
        )


# ---------- 自动轮换 ----------

rotator = AutoRotator(store=store, runner=runner, on_done=_on_done)


@app.on_event("startup")
def _startup() -> None:
    rotator.start()
    try:
        store.ensure_dxm_default(
            DXM_DEFAULT_USERNAME, DXM_DEFAULT_PASSWORD, DXM_DEFAULT_LABEL
        )
    except Exception as error:  # noqa: BLE001 - 默认账号写不进去不影响主流程
        print(f"[collector] seed dxm account failed: {error}", flush=True)
    try:
        store.ensure_freight_default(
            "wujie",
            WUJIE_DEFAULT_USERNAME,
            WUJIE_DEFAULT_PASSWORD,
            WUJIE_DEFAULT_LABEL,
            site_url=WUJIE_LOGIN_URL,
            adapter="wujie",
        )
        store.ensure_freight_default(
            "wujie_sfdm",
            WUJIE_SFDM_USERNAME,
            WUJIE_SFDM_PASSWORD,
            WUJIE_SFDM_LABEL,
            site_url=WUJIE_LOGIN_URL,
            adapter="wujie",
        )
        store.ensure_freight_default(
            "hy",
            HY_DEFAULT_USERNAME,
            HY_DEFAULT_PASSWORD,
            HY_DEFAULT_LABEL,
            site_url=HY_LOGIN_URL,
            adapter="hy",
        )
        # 旧库补站点和适配器：早期行没有这两列，默认值会把浩远误认成无界。
        store.update_freight_account("wujie", site_url=WUJIE_LOGIN_URL, adapter="wujie")
        store.update_freight_account("wujie_sfdm", site_url=WUJIE_LOGIN_URL, adapter="wujie")
        store.update_freight_account("hy", site_url=HY_LOGIN_URL, adapter="hy")
    except Exception as error:  # noqa: BLE001
        print(f"[collector] seed freight account failed: {error}", flush=True)


# ---------- 查询 ----------

@app.get("/api/state")
def state() -> dict[str, Any]:
    accounts = store.list_accounts()
    jobs = {item["account_id"]: item for item in runner.jobs()}
    for account in accounts:
        job = jobs.get(account["id"])
        account["job"] = job
        latest = store.latest_snapshot(account["id"], "orders")
        account["last_summary"] = (latest or {}).get("payload", {}).get("summary") or {}
        account["last_captured_at"] = (latest or {}).get("captured_at", "")
        latest_settlement = store.latest_snapshot(account["id"], "settlement")
        account["last_settlement"] = (latest_settlement or {}).get("payload") or {}
        account["settlement_at"] = (latest_settlement or {}).get("captured_at", "")
        ads = (store.latest_snapshot(account["id"], "ads") or {}).get("payload") or {}
        account["last_ads"] = {k: v for k, v in ads.items() if k not in ("rows", "source")}
        account["ads_attempt"] = (store.latest_snapshot(account["id"], "ads_attempt") or {}).get("payload") or {}
        account["temu_workflow"] = store.latest_temu_workflow(account["id"]) or {}
    return {
        "accounts": accounts,
        "dxm_accounts": _dxm_accounts_with_jobs(),
        "dxm_pair_count": store.count_dxm_pairs(),
        "freight_accounts": _freight_accounts_with_jobs(),
        "rotation": rotator.state(),
        "server_time": now_iso(),
    }


def _freight_accounts_with_jobs() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    jobs = {item["account_id"]: item for item in freight.jobs()}
    for account in store.list_freight_accounts():
        platform = str(account["platform"])
        adapter = str(account.get("adapter") or "wujie")
        account["name"] = FREIGHT_ADAPTER_NAMES.get(adapter, "发货平台")
        account["label"] = account.get("label") or account["name"]
        account["job"] = jobs.get(platform)
        account["quote_count"] = store.count_freight_quotes(platform)
        items.append(account)
    return items


def _dxm_accounts_with_jobs() -> list[dict[str, Any]]:
    accounts = store.list_dxm_accounts()
    jobs = {item["account_id"]: item for item in dxm.jobs()}
    for account in accounts:
        account["job"] = jobs.get(account["id"])
    return accounts


@app.get("/api/rotation")
def read_rotation() -> dict[str, Any]:
    return rotator.state()


@app.put("/api/rotation")
def save_rotation(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        rotator.update(payload)
    except (TypeError, ValueError) as error:
        raise HTTPException(400, str(error)) from error
    return rotator.state()


@app.post("/api/rotation/run")
def run_rotation_now() -> dict[str, Any]:
    return rotator.trigger_now()


@app.get("/api/summary")
def summary(accounts: str = Query(default="")) -> dict[str, Any]:
    """按所选店铺汇总最新一次采集结果；不传就汇总全部店铺。"""
    all_accounts = store.list_accounts()
    wanted = [item for item in str(accounts or "").split(",") if item.strip()]
    selected = [a for a in all_accounts if not wanted or a["id"] in wanted]

    def num(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    fields = (
        "declared_price",
        "sales_payback",
        "sales_reversal",
        "freight_payback",
        "freight_reversal",
        "estimated_income",
        "order_payment",
        "second_payment",
    )
    stores: list[dict[str, Any]] = []
    totals = {key: 0.0 for key in fields}
    totals.update({"order_count": 0, "item_count": 0, "quantity": 0, "store_count": 0})
    for account in selected:
        snapshot = store.latest_snapshot(account["id"], "orders")
        payload = (snapshot or {}).get("payload") or {}
        items = payload.get("items") or []
        parents = {str(row.get("parent_order_sn") or "") for row in items}
        row = {
            "account_id": account["id"],
            "shop_name": account.get("shop_name") or account.get("alias") or account.get("phone_tail"),
            "captured_at": (snapshot or {}).get("captured_at", ""),
            "order_count": len([p for p in parents if p]),
            "item_count": len(items),
            "quantity": 0,
        }
        for key in fields:
            row[key] = 0.0
        for item in items:
            row["quantity"] += int(num(item.get("quantity")))
            for key in fields:
                row[key] += num(item.get(key))
        stores.append(row)
        totals["store_count"] += 1
        totals["order_count"] += row["order_count"]
        totals["item_count"] += row["item_count"]
        totals["quantity"] += row["quantity"]
        for key in fields:
            totals[key] += row[key]
    return {"stores": stores, "totals": totals, "currency": "CNY"}


@app.get("/api/ranking")
def ranking(
    accounts: str = Query(default=""),
    by: str = Query(default="skc"),
    limit: int = Query(default=15),
) -> dict[str, Any]:
    """销量排行：按链接（SKC）或按 SKU 聚合所选店铺的订单明细。"""
    all_accounts = store.list_accounts()
    wanted = [item for item in str(accounts or "").split(",") if item.strip()]
    selected = [a for a in all_accounts if not wanted or a["id"] in wanted]
    key_field = "sku_id" if by == "sku" else "skc_id"

    def num(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    groups: dict[str, dict[str, Any]] = {}
    for account in selected:
        snapshot = store.latest_snapshot(account["id"], "orders")
        items = ((snapshot or {}).get("payload") or {}).get("items") or []
        shop = account.get("shop_name") or account.get("alias") or account.get("phone_tail")
        for item in items:
            key = str(item.get(key_field) or "").strip()
            if not key:
                continue
            group = groups.setdefault(
                key,
                {
                    "key": key,
                    "goods_name": "",
                    "image_url": "",
                    "skc_id": str(item.get("skc_id") or ""),
                    "quantity": 0,
                    "declared": 0.0,
                    "gmv": 0.0,
                    "orders": set(),
                    "shops": set(),
                },
            )
            if not group["goods_name"] and item.get("goods_name"):
                group["goods_name"] = str(item["goods_name"])
            if not group["image_url"] and item.get("image_url"):
                group["image_url"] = str(item["image_url"])
            group["quantity"] += int(num(item.get("quantity")))
            group["declared"] += num(item.get("declared_price"))
            group["gmv"] += num(item.get("gmv"))
            parent = str(item.get("parent_order_sn") or "")
            if parent:
                group["orders"].add(parent)
            if shop:
                group["shops"].add(str(shop))

    rows = []
    for group in groups.values():
        rows.append(
            {
                "key": group["key"],
                "goods_name": group["goods_name"],
                "image_url": group["image_url"],
                "skc_id": group["skc_id"],
                "quantity": group["quantity"],
                "order_count": len(group["orders"]),
                "shop_count": len(group["shops"]),
                "gmv": round(group["gmv"], 2),
                "declared": round(group["declared"], 2),
            }
        )
    rows.sort(key=lambda row: (-row["quantity"], -row["gmv"]))
    return {
        "by": "sku" if by == "sku" else "skc",
        "total": len(rows),
        "items": rows[: max(1, min(100, int(limit)))],
        "currency": "CNY",
    }


@app.get("/api/accounts/{account_id}/orders")
def orders(account_id: str) -> dict[str, Any]:
    snapshot = store.latest_snapshot(account_id, "orders")
    if snapshot is None:
        raise HTTPException(404, "还没有采集结果")
    # 把客户端回填的单件成本合并进行数据，服务端页面才能看到成本字段。
    costs = {
        str(row["skc_id"]): row for row in store.list_costs([account_id])
    }
    items = ((snapshot.get("payload") or {}).get("items") or [])
    for item in items:
        if not isinstance(item, dict):
            continue
        cost = costs.get(str(item.get("skc_id") or ""))
        item["unit_cost"] = cost["unit_cost"] if cost else None
        item["cost_currency"] = cost["currency"] if cost else ""
        item["cost_updated_at"] = cost["updated_at"] if cost else ""
    return snapshot


@app.get("/api/accounts/{account_id}/shot")
def shot(account_id: str) -> FileResponse:
    path = runner.shot_path(account_id)
    if not path.exists():
        raise HTTPException(404, "暂无截图")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


IMAGE_HOST_SUFFIXES = (".kwcdn.com", ".temu.com")


@app.get("/api/image")
def image_proxy(url: str = Query(default="")) -> Response:
    """代理商品图：kwcdn 有防盗链，浏览器直接引用大概率拿不到。"""
    parsed = urllib.parse.urlsplit(str(url or ""))
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not any(
        host.endswith(suffix) for suffix in IMAGE_HOST_SUFFIXES
    ):
        raise HTTPException(400, "不允许的图片来源")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Referer": "https://agentseller-us.temu.com/",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = response.read(8 * 1024 * 1024)
            media_type = response.headers.get_content_type() or "image/jpeg"
    except Exception as error:  # noqa: BLE001
        raise HTTPException(502, f"图片获取失败：{error}") from error
    return Response(
        content=data,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=604800"},
    )


@app.post("/api/translate")
def translate_titles(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """把英文商品标题翻成中文；结果落库缓存，翻不出来就返回空。"""
    texts = payload.get("texts") or []
    if not isinstance(texts, list):
        raise HTTPException(400, "texts 必须是数组")
    mapping = translate_many(store, [str(text) for text in texts[:200]])
    return {"items": mapping}


# ---------- 账号 ----------

@app.post("/api/accounts")
def save_account(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        account = store.save_account(
            phone=str(payload.get("phone") or ""),
            password=str(payload.get("password") or ""),
            alias=str(payload.get("alias") or ""),
            site=str(payload.get("site") or "us"),
            account_id=str(payload.get("id") or ""),
        )
    except (ValueError, LookupError) as error:
        raise HTTPException(400, str(error)) from error
    return {"account": account}


@app.delete("/api/accounts/{account_id}")
def delete_account(account_id: str) -> dict[str, Any]:
    return {"deleted": store.delete_account(account_id)}


# ---------- 任务 ----------

@app.post("/api/accounts/{account_id}/login")
def login(account_id: str) -> dict[str, Any]:
    account = store.get_account(account_id)
    if account is None:
        raise HTTPException(404, "账号不存在")
    credentials = store.get_credentials(account_id) or {}
    if not credentials.get("phone") or not credentials.get("password"):
        raise HTTPException(400, "请先补充手机号和密码")
    try:
        return runner.run_login(account_id, credentials, _on_done)
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error


@app.post("/api/accounts/{account_id}/probe")
def probe(account_id: str) -> dict[str, Any]:
    if store.get_account(account_id) is None:
        raise HTTPException(404, "账号不存在")
    try:
        return runner.run_probe(account_id, _on_done)
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error


@app.post("/api/accounts/{account_id}/capture")
def capture(account_id: str) -> dict[str, Any]:
    if store.get_account(account_id) is None:
        raise HTTPException(404, "账号不存在")
    try:
        return runner.run_capture(account_id, _on_done)
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error


@app.post("/api/accounts/{account_id}/ads")
def capture_ads(account_id: str) -> dict[str, Any]:
    if store.get_account(account_id) is None:
        raise HTTPException(404, "账号不存在")
    try:
        return runner.run_ads(account_id, _on_done)
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error


@app.get("/api/accounts/{account_id}/ads")
def ads_result(
    account_id: str,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    view: str = Query("raw", pattern="^(raw|products)$"),
) -> dict[str, Any]:
    if store.get_account(account_id) is None:
        raise HTTPException(404, "账号不存在")
    data = (store.latest_snapshot(account_id, "ads") or {}).get("payload") or {}
    rows = data.get("rows") or []
    if view == "products":
        from ads import aggregate_product_rows

        products, raw_count = aggregate_product_rows(rows)
        summary = {k: v for k, v in data.items() if k not in ("rows", "source")}
        summary.update(raw_record_count=raw_count, product_count=len(products))
        requested = {str(value).strip() for value in (data.get("requested_spus") or []) if str(value).strip()}
        if requested:
            found = {str(item.get("spu_id") or "").strip() for item in products}
            summary["missing_spus"] = sorted(requested - found)
            summary["matched_spus"] = sorted(requested & found)
        else:
            summary.setdefault("missing_spus", [])
        return {**summary, "items": products[(page - 1) * size:page * size],
                "total": len(products), "page": page, "size": size}
    return {**{k: v for k, v in data.items() if k not in ("rows", "source")},
            "rows": rows[(page - 1) * size:page * size], "total": len(rows), "page": page}


@app.post("/api/accounts/{account_id}/settlement")
def settlement(
    account_id: str, payload: dict[str, Any] | None = Body(default=None)
) -> dict[str, Any]:
    """单独验证入口：在对账中心导出历史里下载财务明细，返回结算金额 / 罚款 / 逐笔明细。"""
    if store.get_account(account_id) is None:
        raise HTTPException(404, "账号不存在")
    data = payload or {}
    try:
        days = max(1, min(180, int(data.get("days") or 30)))
    except (TypeError, ValueError):
        days = 30
    try:
        return runner.run_settlement(account_id, _on_done, days=days)
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error


@app.post("/api/accounts/{account_id}/code")
def submit_code(account_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    accepted = runner.submit_code(account_id, str(payload.get("code") or ""))
    if not accepted:
        raise HTTPException(409, "当前不需要验证码")
    return {"accepted": True}


@app.post("/api/accounts/{account_id}/pause")
def pause_account(account_id: str) -> dict[str, Any]:
    """人工接管：让正在执行的自动化停在原地，把浏览器让给实时画面。"""
    if not runner.pause(account_id):
        raise HTTPException(409, "该账号没有正在执行的任务")
    return {"paused": True}


@app.post("/api/accounts/{account_id}/resume")
def resume_account(account_id: str) -> dict[str, Any]:
    if not runner.resume(account_id):
        raise HTTPException(409, "该账号没有正在执行的任务")
    return {"paused": False}


# ---------- 店小秘账号 / 单号对齐 ----------

@app.get("/api/dxm/state")
def dxm_state() -> dict[str, Any]:
    return {
        "accounts": _dxm_accounts_with_jobs(),
        "pair_count": store.count_dxm_pairs(),
    }


@app.post("/api/dxm/accounts")
def save_dxm_account(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        account = store.save_dxm_account(
            username=str(payload.get("username") or ""),
            password=str(payload.get("password") or ""),
            label=str(payload.get("label") or ""),
            account_id=str(payload.get("id") or ""),
        )
    except (ValueError, LookupError) as error:
        raise HTTPException(400, str(error)) from error
    return {"account": account}


@app.delete("/api/dxm/accounts/{account_id}")
def delete_dxm_account(account_id: str) -> dict[str, Any]:
    return {"deleted": store.delete_dxm_account(account_id)}


@app.post("/api/dxm/accounts/{account_id}/login")
def dxm_login(account_id: str) -> dict[str, Any]:
    if store.get_dxm_account(account_id) is None:
        raise HTTPException(404, "店小秘账号不存在")
    credentials = store.get_dxm_credentials(account_id) or {}
    if not credentials.get("username") or not credentials.get("password"):
        raise HTTPException(400, "请先补充店小秘账号和密码")
    try:
        return dxm.run_login(account_id, credentials, _dxm_on_done)
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error


@app.post("/api/dxm/accounts/{account_id}/probe")
def dxm_probe(account_id: str) -> dict[str, Any]:
    if store.get_dxm_account(account_id) is None:
        raise HTTPException(404, "店小秘账号不存在")
    try:
        return dxm.run_probe(account_id, _dxm_on_done)
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error


@app.post("/api/dxm/accounts/{account_id}/discover")
def dxm_discover(account_id: str) -> dict[str, Any]:
    if store.get_dxm_account(account_id) is None:
        raise HTTPException(404, "店小秘账号不存在")
    try:
        return dxm.run_discover(account_id, _dxm_on_done)
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error


@app.post("/api/dxm/accounts/{account_id}/align")
def dxm_align(
    account_id: str, payload: dict[str, Any] | None = Body(default=None)
) -> dict[str, Any]:
    if store.get_dxm_account(account_id) is None:
        raise HTTPException(404, "店小秘账号不存在")
    data = payload or {}
    pos = data.get("pos") or []
    if not isinstance(pos, list):
        raise HTTPException(400, "pos 必须是数组")
    pos = [str(po).strip() for po in pos if str(po).strip()][:2000]
    if not pos:
        raise HTTPException(400, "没有需要对齐的 PO 单号")
    try:
        return dxm.run_align(account_id, pos, _dxm_on_done)
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error


@app.get("/api/dxm/accounts/{account_id}/shot")
def dxm_shot(account_id: str) -> FileResponse:
    path = dxm.shot_path(account_id)
    if not path.exists():
        raise HTTPException(404, "暂无截图")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/api/dxm/pairs")
def dxm_pairs() -> dict[str, Any]:
    return {"pairs": store.list_dxm_pairs()}


# ---------- 发货平台头程金额 ----------

@app.get("/api/freight/accounts")
def freight_accounts() -> dict[str, Any]:
    return {"accounts": _freight_accounts_with_jobs()}


@app.post("/api/freight/accounts")
def freight_save_account(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    site_url = str(payload.get("site_url") or "").strip()
    label = str(payload.get("label") or "").strip()
    adapter = str(payload.get("adapter") or "rtb").strip()
    if not site_url or not username:
        raise HTTPException(400, "网址和登录账号不能为空")
    if adapter not in FREIGHT_ADAPTER_NAMES:
        raise HTTPException(400, "平台类型不支持")
    platform = str(payload.get("platform") or "").strip()
    if not platform:
        slug = re.sub(r"[^a-z0-9]+", "-", username.lower()).strip("-")[:40]
        platform = f"{adapter}-{slug or now_iso()[-8:]}"
    if not store.get_freight_account(platform) and not password:
        raise HTTPException(400, "新增账号必须填写密码")
    account = store.save_freight_account(
        platform=platform,
        username=username,
        password=password,
        label=label or username,
        site_url=site_url,
        adapter=adapter,
    )
    return {"ok": True, "account": account}


@app.delete("/api/freight/accounts/{platform}")
def freight_delete_account(platform: str) -> dict[str, Any]:
    if store.get_freight_account(platform) is None:
        raise HTTPException(404, "发货平台账号不存在")
    store.delete_freight_account(platform)
    return {"ok": True}


def _freight_account_or_404(platform: str) -> dict[str, Any]:
    account = store.get_freight_account(platform)
    if account is None:
        raise HTTPException(404, "发货平台不存在")
    return account


@app.post("/api/freight/{platform}/login")
def freight_login(platform: str) -> dict[str, Any]:
    _freight_account_or_404(platform)
    credentials = store.get_freight_credentials(platform) or {}
    if not credentials.get("username") or not credentials.get("password"):
        raise HTTPException(400, "请先补充发货平台账号和密码")
    try:
        return freight.run_login(platform, credentials, _freight_on_done)
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error


@app.post("/api/freight/{platform}/code")
def freight_code(
    platform: str, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    _freight_account_or_404(platform)
    code = str(payload.get("code") or "").strip()
    if not code:
        raise HTTPException(400, "验证码不能为空")
    try:
        return freight.submit_code(platform, code)
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error


FREIGHT_CAPABILITIES = {"date_range_query": True, "logistics_routing_fallback": True}


def _valid_freight_amount(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def submit_freight_sync(data: dict[str, Any], platforms: list[str] | None = None) -> dict[str, Any]:
    """立即返回 run_id；平台任务及回调等待均在后台线程执行。"""
    options = {}
    for key in ("date_from", "date_to"):
        text = str(data.get(key) or "").strip()
        if text:
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
                    raise ValueError
                date.fromisoformat(text)
            except ValueError as error:
                raise HTTPException(400, f"{key} 必须是有效的 YYYY-MM-DD 日期") from error
        options[key] = text
    if options["date_from"] and options["date_to"] and options["date_from"] > options["date_to"]:
        raise HTTPException(400, "date_from 不能晚于 date_to")
    for key in ("initialize", "missing_only"):
        if key in data and not isinstance(data[key], bool):
            raise HTTPException(400, f"{key} 必须是布尔值")
    initialize = data.get("initialize", False)
    if data.get("timezone", "Asia/Shanghai") != "Asia/Shanghai":
        raise HTTPException(400, "timezone 仅支持 Asia/Shanghai")
    raw = data.get("xms", [])
    if not isinstance(raw, list):
        raise HTTPException(400, "xms 必须是数组")
    xms = list(dict.fromkeys(str(xm).strip() for xm in raw if str(xm).strip()))
    if "xms" not in data and not initialize:
        xms = list(dict.fromkeys(row["xm"] for row in store.list_dxm_pairs() if row["xm"]))
    authorized = sorted(item["platform"] for item in store.list_freight_accounts())
    selected = authorized if platforms is None else sorted(set(platforms) & set(authorized))
    if not selected:
        raise HTTPException(400, "没有可查询的已绑定发货平台")
    if not xms and not initialize:
        return {"ok": True, "run_id": "", "status": "noop", "capabilities": dict(FREIGHT_CAPABILITIES)}
    if data.get("missing_only") and not initialize:
        cached = {q["xm"] for code in authorized for q in store.list_freight_quotes(code)
                  if _valid_freight_amount(q.get("amount"))}
        xms = [xm for xm in xms if xm not in cached]
        if not xms:
            return {"ok": True, "run_id": "", "status": "noop", "capabilities": dict(FREIGHT_CAPABILITIES)}
    # 从持久化对齐数据和报价学习候选；多渠道或跨账号冲突样本不参与。
    import unicodedata
    def channel_key(value: str) -> str:
        value = re.split(r"[（(]?(?:跟踪|追踪|运单)(?:单号|号)?[：:]", value)[0]
        return "".join(c for c in unicodedata.normalize("NFKC", value).lower()
                       if c.isalnum())
    channels_by_xm: dict[str, set[str]] = {}
    for pair in store.list_dxm_pairs():
        channels_by_xm.setdefault(pair["xm"], set()).add(channel_key(pair.get("logistics") or ""))
    platforms_by_xm: dict[str, set[str]] = {}
    for platform in authorized:
        for quote in store.list_freight_quotes(platform):
            if _valid_freight_amount(quote.get("amount")):
                platforms_by_xm.setdefault(quote["xm"], set()).add(platform)
    evidence: dict[str, dict[str, int]] = {}
    for xm, hits in platforms_by_xm.items():
        channels = channels_by_xm.get(xm, set())
        if len(hits) == len(channels) == 1 and "" not in channels:
            counts = evidence.setdefault(next(iter(channels)), {})
            platform = next(iter(hits))
            counts[platform] = counts.get(platform, 0) + 1
    routing = data.get("routing") or {}
    if not isinstance(routing, dict) or not isinstance(routing.get("routes", []), list):
        raise HTTPException(400, "routing.routes 必须是数组")
    candidates: dict[str, list[str]] = {}
    for route in routing.get("routes", []):
        if not isinstance(route, dict) or not isinstance(route.get("candidates", []), list):
            raise HTTPException(400, "路由候选必须是数组")
        xm = str(route.get("xm") or "").strip()
        if xm in xms:
            candidates[xm] = list(dict.fromkeys(p for p in route.get("candidates", [])
                                                if isinstance(p, str) and p in selected))
    route_evidence: dict[str, Any] = {}
    for xm in xms:
        channels = channels_by_xm.get(xm, set())
        counts = evidence.get(next(iter(channels)), {}) if len(channels) == 1 and "" not in channels else {}
        ranked = sorted((p for p in counts if p in selected), key=lambda p: (-counts[p], p))
        if ranked:
            candidates[xm] = ranked
            route_evidence[xm] = {"basis": "server_history", "samples": counts}
        else:
            route_evidence[xm] = {"basis": "client_hint" if candidates.get(xm) else "unknown"}
    payload = {**options, "xms": xms, "platforms": selected, "authorized_platforms": authorized,
               "route_evidence": route_evidence, "source": str(data.get("source") or "manual"),
               "candidates": candidates,
               "initialize": initialize, "missing_only": data.get("missing_only", False),
               "timezone": str(data.get("timezone") or "Asia/Shanghai")}
    try:
        run_id, created = store.create_freight_run(payload)
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error
    if created:
        try:
            threading.Thread(target=_execute_freight_sync, args=(run_id, payload),
                             name=f"freight-sync-{run_id[:8]}", daemon=True).start()
        except Exception:
            store.finish_freight_sync(run_id, {xm: "failed" for xm in xms})
            store.update_freight_run(run_id, "failed", {"message": "后台任务启动失败"})
            raise
    return {"ok": True, "run_id": run_id, "status": "queued" if created else "running",
            "deduplicated": not created, "capabilities": dict(FREIGHT_CAPABILITIES)}


def _execute_freight_sync(run_id: str, payload: dict[str, Any]) -> None:
    remaining = set(payload["xms"])
    found: set[str] = set()
    outcomes: dict[str, dict[str, Any]] = {}
    details: dict[str, Any] = {"platforms": outcomes, "initialization_complete": False,
                               "routing_mode": "logistics_first_with_fallback"}
    plans = {xm: payload.get("candidates", {}).get(xm, []) +
             [p for p in payload["platforms"] if p not in payload.get("candidates", {}).get(xm, [])]
             for xm in remaining}
    pending_initial = list(payload["platforms"]) if payload["initialize"] else []
    store.update_freight_run(run_id, "running", details)
    try:
        while pending_initial or (not payload["initialize"] and any(plans[xm] for xm in remaining)):
            if payload.get("source") == "daily":
                from freight_schedule import BEIJING
                if not 4 <= datetime.now(BEIJING).hour < 6:
                    details["window_exhausted"] = True
                    break
            if payload["missing_only"] and not payload["initialize"]:
                cached = {q["xm"] for a in store.list_freight_accounts()
                          for q in store.list_freight_quotes(a["platform"])
                          if _valid_freight_amount(q.get("amount"))}
                found.update(remaining & cached)
                remaining.difference_update(cached)
                if not remaining:
                    break
            if payload["initialize"]:
                platform = pending_initial.pop(0)
                requested = []
            else:
                platform = next(plans[xm][0] for xm in sorted(remaining) if plans[xm])
                requested = sorted(xm for xm in remaining if plans[xm] and plans[xm][0] == platform)
                for xm in requested:
                    plans[xm].pop(0)
            # 重新读取绑定账号，routing 不能授权查询未绑定平台。
            account = store.get_freight_account(platform)
            if account is None:
                result = {"ok": False, "message": "平台绑定已移除"}
            else:
                event = threading.Event()
                box: list[dict[str, Any]] = []

                def completed(_platform: str, result: dict[str, Any]) -> None:
                    box.append(result)
                    event.set()

                try:
                    freight.run_query(platform, requested, completed,
                                      date_from=payload["date_from"], date_to=payload["date_to"],
                                      initialize=payload["initialize"])
                    if not details.get("attempt_counted"):
                        store.mark_freight_attempted(run_id)
                        details["attempt_counted"] = True
                    # 不按 TTL 释放互斥：超时并不证明平台线程已经停止。
                    while not event.wait(30):
                        store.update_freight_run(run_id, "running", details)
                    result = box[0]
                except Exception as error:
                    result = {"ok": False, "message": f"平台任务提交失败：{type(error).__name__}"}
            _freight_on_done(platform, {**result, "action": "query"})
            quotes = [q for q in result.get("quotes", []) if isinstance(q, dict)
                      and _valid_freight_amount(q.get("amount"))
                      and (payload["initialize"] or q.get("xm") in remaining)]
            store.save_freight_quotes(platform, quotes)
            matched = {str(q["xm"]) for q in quotes if q.get("xm")}
            details["platform_stages"] = details.get("platform_stages", 0) + 1
            if not payload["initialize"]:
                candidate_hits = sum(platform in payload.get("candidates", {}).get(xm, []) for xm in matched)
                details["candidate_hits"] = details.get("candidate_hits", 0) + candidate_hits
                details["fallback_hits"] = details.get("fallback_hits", 0) + len(matched) - candidate_hits
            found.update(matched)
            remaining.difference_update(matched)
            # 未声明覆盖完整的结果绝不能作为「全平台未找到」或初始化完成依据。
            complete = bool(result.get("ok")) and result.get("coverage_complete") is True
            summary = {"ok": bool(result.get("ok")), "coverage_complete": complete,
                       "found_xms": sorted(matched), "requested_xms": requested,
                       "message": str(result.get("message") or "")}
            previous = outcomes.get(platform)
            if previous:
                summary["ok"] = previous["ok"] and summary["ok"]
                summary["coverage_complete"] = previous["coverage_complete"] and complete
                summary["found_xms"] = sorted(set(previous["found_xms"]) | matched)
                summary["requested_xms"] = sorted(set(previous["requested_xms"]) | set(requested))
            outcomes[platform] = summary
            store.save_freight_platform_result(run_id, platform, summary)
            store.update_freight_run(run_id, "running", details)
        current = sorted(a["platform"] for a in store.list_freight_accounts())
        full_scope = set(payload["platforms"]) == set(current) == set(payload["authorized_platforms"])
        all_complete = full_scope and all(outcomes.get(p, {}).get("coverage_complete") for p in current)
        any_ok = any(item["ok"] for item in outcomes.values())
        missing_state = "all_not_found" if all_complete else "partial" if any_ok else "failed"
        cached_found = {q["xm"] for code in current for q in store.list_freight_quotes(code)
                        if _valid_freight_amount(q.get("amount"))}
        states = {xm: "found" if xm in found or xm in cached_found else missing_state for xm in payload["xms"]}
        store.finish_freight_sync(run_id, states)
        details.update({"states": states, "covered_platforms": sorted(outcomes),
                        "authorized_platforms": current, "coverage_complete": bool(all_complete),
                        "initialization_complete": bool(payload["initialize"] and all_complete)})
        if payload["initialize"]:
            status = "succeeded" if all_complete else "partial" if any_ok else "failed"
        else:
            status = "succeeded" if not remaining or all_complete else "partial" if any_ok else "failed"
        if details["initialization_complete"]:
            store.mark_freight_initialized(current, payload["date_from"], payload["date_to"])
        store.update_freight_run(run_id, status, details)
    except Exception as error:
        store.finish_freight_sync(run_id, {xm: "found" if xm in found else "failed" for xm in payload["xms"]})
        details["message"] = f"同步任务失败：{type(error).__name__}"
        store.update_freight_run(run_id, "failed", details)


@app.post("/api/freight/query")
def freight_query_all(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return submit_freight_sync(payload)


@app.get("/api/freight/runs/{run_id}")
def freight_run_status(run_id: str) -> dict[str, Any]:
    result = store.freight_run(run_id)
    if result is None:
        raise HTTPException(404, "头程任务不存在")
    return result


freight_scheduler = FreightSchedule(store, submit_freight_sync)


@app.on_event("startup")
def _start_freight_schedule() -> None:
    # 单进程部署下启动即视为独占运行器：把上次进程遗留的任务标为中断，避免永久占用互斥。
    try:
        recovered = store.recover_interrupted_freight_runs()
        if recovered:
            print(f"[collector] recovered {recovered} interrupted freight runs", flush=True)
    except Exception as error:  # noqa: BLE001 - 恢复失败不应阻止服务启动
        print(f"[collector] recover freight runs failed: {error}", flush=True)
    freight_scheduler.start()


@app.on_event("shutdown")
def _stop_freight_schedule() -> None:
    freight_scheduler.stop()


@app.get("/api/freight/schedule/status")
def freight_schedule_status() -> dict[str, Any]:
    return {**freight_scheduler.status(), "capabilities": dict(FREIGHT_CAPABILITIES)}


@app.post("/api/freight/{platform}/query")
def freight_query(
    platform: str, payload: dict[str, Any] | None = Body(default=None)
) -> dict[str, Any]:
    _freight_account_or_404(platform)
    return submit_freight_sync(payload or {}, platforms=[platform])


@app.get("/api/freight/quotes")
def freight_quotes(platform: str = Query(default="")) -> dict[str, Any]:
    # 不传平台就返回所有发货平台账号的报价合并结果：同一 XM 只会属于其中一个账号。
    if platform:
        _freight_account_or_404(platform)
        platforms = [platform]
    else:
        platforms = [item["platform"] for item in store.list_freight_accounts()]
    quotes: dict[str, dict[str, Any]] = {}
    updated_at = ""
    for code in sorted(platforms):
        for quote in store.list_freight_quotes(code):
            updated_at = max(updated_at, quote.get("updated_at") or "")
            if not _valid_freight_amount(quote.get("amount")):
                continue
            quote["platform"] = code
            xm = str(quote["xm"])
            previous = quotes.get(xm)
            if previous is None or quote["updated_at"] > previous["updated_at"]:
                quotes[xm] = quote
    items = [quotes[xm] for xm in sorted(quotes)]
    return {
        "platform": platform or "all",
        "quotes": items,
        "count": len(items),
        "updated_at": updated_at,
        "capabilities": dict(FREIGHT_CAPABILITIES),
        "sync_records": {record["xm"]: record for record in store.freight_sync_records(
            [item["platform"] for item in store.list_freight_accounts()]
        )},
    }


@app.get("/api/freight/{platform}/shot")
def freight_shot(platform: str) -> FileResponse:
    path = freight.shot_path(platform)
    if not path.exists():
        raise HTTPException(404, "暂无截图")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/api/freight/{platform}/captcha")
def freight_captcha(platform: str) -> FileResponse:
    path = freight.captcha_path(platform)
    if not path.exists():
        raise HTTPException(404, "暂无验证码图片")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/api/orders/status")
def orders_status(accounts: str = Query(default="")) -> dict[str, Any]:
    """PO → 订单状态，取自订单信息采集的最新快照，用于对齐页展示和筛选。"""
    all_accounts = store.list_accounts()
    wanted = [item for item in str(accounts or "").split(",") if item.strip()]
    selected = [a for a in all_accounts if not wanted or a["id"] in wanted]
    statuses: dict[str, dict[str, Any]] = {}
    for account in selected:
        snapshot = store.latest_snapshot(account["id"], "orders")
        items = ((snapshot or {}).get("payload") or {}).get("items") or []
        captured_at = (snapshot or {}).get("captured_at", "")
        for item in items:
            po = str(item.get("parent_order_sn") or "").strip()
            if not po:
                continue
            status = str(item.get("order_status") or item.get("order_status_code") or "").strip()
            row = statuses.setdefault(
                po,
                {"status": "", "shop": account.get("shop_name") or account.get("alias") or "", "captured_at": captured_at},
            )
            if not row["status"] and status:
                row["status"] = status
                row["captured_at"] = captured_at
    # 列表快照覆盖不全时，用详情缓存补齐同一 PO 的状态。
    for po, status in store.order_status_map().items():
        row = statuses.setdefault(po, {"status": "", "shop": "", "captured_at": ""})
        if not row["status"]:
            row["status"] = status
    options = sorted({row["status"] for row in statuses.values() if row["status"]})
    return {"statuses": statuses, "options": options}


@app.get("/api/dxm/debug")
def dxm_debug() -> dict[str, Any]:
    return dxm.debug() or {}


# ---------- 内部写入（供其它服务对接） ----------

@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "temu-collector", "accounts": len(store.list_accounts())}


@app.exception_handler(Exception)
def _unhandled(_request: Any, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": str(error)})


WEB_DIR = ROOT / "web"
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
