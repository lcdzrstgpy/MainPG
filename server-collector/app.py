"""服务端 TEMU 采集器 HTTP 服务。

一套最小的界面 + 后端：录入店铺账号 → 在服务器上登录 → 采集订单 → 结果落库。
不依赖工作台前端、浏览器插件或远程桌面。
"""

from __future__ import annotations

import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from bridge import create_bridge_router
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


# ---------- 自动轮换 ----------

rotator = AutoRotator(store=store, runner=runner, on_done=_on_done)


@app.on_event("startup")
def _startup() -> None:
    rotator.start()


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
    return {
        "accounts": accounts,
        "rotation": rotator.state(),
        "server_time": now_iso(),
    }


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


@app.post("/api/accounts/{account_id}/code")
def submit_code(account_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    accepted = runner.submit_code(account_id, str(payload.get("code") or ""))
    if not accepted:
        raise HTTPException(409, "当前不需要验证码")
    return {"accepted": True}


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
