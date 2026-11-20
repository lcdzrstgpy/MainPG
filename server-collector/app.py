"""服务端 TEMU 采集器 HTTP 服务。

一套最小的界面 + 后端：录入店铺账号 → 在服务器上登录 → 采集订单 → 结果落库。
不依赖工作台前端、浏览器插件或远程桌面。
"""

from __future__ import annotations

import os
import re
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
)

dxm = DxmRunner(data_dir=DATA_DIR, chrome_path=CHROME_PATH, store=store)
freight = FreightRunner(data_dir=DATA_DIR, chrome_path=CHROME_PATH, store=store)

app = FastAPI(title="TEMU 采集器", docs_url=None, redoc_url=None)
app.include_router(create_bridge_router(store))


def _on_done(account_id: str, result: dict[str, Any]) -> None:
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


@app.post("/api/freight/{platform}/query")
def freight_query(
    platform: str, payload: dict[str, Any] | None = Body(default=None)
) -> dict[str, Any]:
    _freight_account_or_404(platform)
    data = payload or {}
    raw = data.get("xms") or []
    if not isinstance(raw, list):
        raise HTTPException(400, "xms 必须是数组")
    xms = [str(item).strip() for item in raw if str(item).strip()]
    if not xms:
        # 不传就查店小秘已经对齐出来的全部 XM。
        xms = [str(row.get("xm") or "").strip() for row in store.list_dxm_pairs()]
    xms = list(dict.fromkeys(xm for xm in xms if xm))[:2000]
    if not xms:
        raise HTTPException(400, "没有需要查询的 XM 单号")
    try:
        return freight.run_query(platform, xms, _freight_on_done)
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error


@app.get("/api/freight/quotes")
def freight_quotes(platform: str = Query(default="")) -> dict[str, Any]:
    # 不传平台就返回所有发货平台账号的报价合并结果：同一 XM 只会属于其中一个账号。
    if platform:
        _freight_account_or_404(platform)
        platforms = [platform]
    else:
        platforms = [item["platform"] for item in store.list_freight_accounts()]
    quotes: dict[str, dict[str, Any]] = {}
    for code in platforms:
        for quote in store.list_freight_quotes(code):
            quote["platform"] = code
            quotes.setdefault(str(quote["xm"]), quote)
    items = list(quotes.values())
    return {
        "platform": platform or "all",
        "quotes": items,
        "count": len(items),
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
