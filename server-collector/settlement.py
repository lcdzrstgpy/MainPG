"""结算资金抓取：复刻「导出列表 + 账务详情」并解析结算金额 / 罚款。

服务端没有浏览器插件，导出请求模板改从 Playwright 的网络事件里录：
在「对账中心 → 导出列表」触发一次导出，页面发出的 /api/merchant/file/export*
请求会被记下来（含 mallid 等鉴权头），之后用同一份请求头/请求体重放即可。
"""

from __future__ import annotations

import base64
import io
import json
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

# 对账中心：美区商家后台优先，其次跨境卖家后台（不同主体登录域不同）。
FINANCE_URLS = (
    "https://agentseller-us.temu.com/labor/bill",
    "https://seller.kuajingmaihuo.com/labor/bill",
)
# 对账中心：跨境后台点「对账中心」会走授权跳转到美区后台，导出历史里才有财务明细。
BILL_URL = "https://seller.kuajingmaihuo.com/labor/bill"
SETTLE_HOST = "agentseller"
# 跨境卖家后台唯一的登录页：/settle/seller-login、/auth/authentication 都不是登录入口，
# 只有 /login 能真正登进去（登录后按 redirectUrl 回到对账中心）。
SELLER_LOGIN_URL = "https://seller.kuajingmaihuo.com/login"


def seller_login_url(bill_url: str) -> str:
    query = urllib.parse.urlencode({"redirectUrl": bill_url})
    return f"{SELLER_LOGIN_URL}?{query}"


def on_auth_page(page: Any) -> bool:
    return "/auth/authentication" in str(getattr(page, "url", "") or "")

TEMPLATE_KEY = "settlement_templates"
POLL_INTERVAL_SECONDS = 4.0
POLL_TIMEOUT_SECONDS = 360
MAX_FILE_BYTES = 30 * 1024 * 1024
FORBIDDEN_HEADERS = {
    "cookie",
    "host",
    "content-length",
    "connection",
    "accept-encoding",
    "referer",
    "content-type",
    "origin",
    "sec-fetch-site",
    "sec-fetch-mode",
    "sec-fetch-dest",
}

BEIJING = timezone(timedelta(hours=8))


# ---------- 通用小工具 ----------


def parse_json(text: Any) -> Any:
    raw = str(text or "").strip()
    if not raw or not re.match(r"^[\[{]", raw):
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def response_failed(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return True
    if payload.get("success") is False:
        return True
    code = payload.get("errorCode")
    return code is not None and str(code) not in ("0", "1000000")


def response_message(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("errorMsg") or payload.get("msg") or payload.get("message") or "").strip()


def temu_result(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return None
    if payload.get("result") is not None:
        return payload["result"]
    if payload.get("data") is not None:
        return payload["data"]
    return None


def temu_result_list(payload: Any) -> list[dict[str, Any]]:
    result = temu_result(payload)
    if isinstance(result, list):
        return [row for row in result if isinstance(row, dict)]
    if not isinstance(result, dict):
        return []
    for key in (
        "list",
        "records",
        "items",
        "rows",
        "data",
        # 导出历史接口把列表放在这个字段里。
        "merchantMerchantFileExportHistoryList",
    ):
        value = result.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    # 字段名可能随版本变化：兜底取第一个「字典列表」。
    for value in result.values():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return [row for row in value if isinstance(row, dict)]
    return []


def export_kind(url: str) -> str:
    text = str(url or "")
    if "/api/merchant/file/export/history/page" in text:
        return "history"
    if "/api/merchant/file/export/download" in text:
        return "download"
    if re.search(r"/api/merchant/file/export(\?|$)", text):
        return "create"
    return ""


def clean_headers(headers: dict[str, Any]) -> dict[str, str]:
    output: dict[str, str] = {}
    for key, value in (headers or {}).items():
        name = str(key or "").strip()
        if not name or name.lower() in FORBIDDEN_HEADERS:
            continue
        output[name] = str(value)
    return output


def _beijing_now() -> datetime:
    return datetime.now(BEIJING)


def format_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


# ---------- 录制导出请求模板 ----------


class ExportRecorder:
    """挂在浏览器上下文上的请求录制器：只留导出相关请求。"""

    def __init__(self) -> None:
        self.templates: dict[str, dict[str, Any]] = {}
        self.open_url = ""

    def attach(self, context: Any) -> None:
        try:
            context.on("page", self._on_page)
            for page in context.pages:
                self._on_page(page)
        except Exception:  # noqa: BLE001 - 录制失败不影响主流程
            pass

    def _on_page(self, page: Any) -> None:
        try:
            page.on("request", self._on_request)
        except Exception:  # noqa: BLE001
            pass

    def _on_request(self, request: Any) -> None:
        try:
            url = str(request.url or "")
            method = str(request.method or "POST").upper()
            kind = export_kind(url)
            if kind:
                self.templates[kind] = {
                    "url": url,
                    "method": method,
                    "headers": clean_headers(dict(request.headers or {})),
                    "body": str(request.post_data or "")[:20000],
                }
                return
            # 直链下载（新标签打开的文件地址）只作为兜底文件地址模板。
            if method == "GET" and not self.open_url and (
                "/download" in url or "params=" in url
            ) and re.search(r"download|export", url, re.I):
                self.open_url = url
        except Exception:  # noqa: BLE001
            pass

    def snapshot(self) -> dict[str, Any]:
        return {
            "create": self.templates.get("create"),
            "history": self.templates.get("history"),
            "download": self.templates.get("download"),
            "openUrl": self.open_url,
        }


def load_templates(store: Any, account_id: str) -> dict[str, Any]:
    try:
        data = store.settings().get(TEMPLATE_KEY) or {}
    except Exception:  # noqa: BLE001
        return {}
    entry = data.get(account_id) if isinstance(data, dict) else None
    return dict(entry) if isinstance(entry, dict) else {}


def save_templates(
    store: Any, account_id: str, templates: dict[str, Any], shop_name: str = ""
) -> None:
    if not templates.get("create") or not templates.get("history"):
        return
    try:
        data = dict(store.settings().get(TEMPLATE_KEY) or {})
        data[account_id] = {
            "create": templates.get("create"),
            "history": templates.get("history"),
            "download": templates.get("download"),
            "openUrl": templates.get("openUrl") or "",
            "shop_name": shop_name,
            "saved_at": format_time(_beijing_now()),
        }
        store.save_settings({TEMPLATE_KEY: data})
    except Exception:  # noqa: BLE001
        pass


# ---------- 页面内重放导出接口 ----------

CALL_API_JS = """async (payload) => {
  const init = {
    method: payload.method || 'POST',
    credentials: 'include',
    headers: { ...(payload.headers || {}), 'content-type': 'application/json' },
  };
  if (payload.body) init.body = payload.body;
  const response = await fetch(payload.url, init);
  const text = await response.text();
  return { ok: response.ok, status: response.status, text: text.slice(0, 40000) };
}"""

FETCH_FILE_JS = """async (url) => {
  const response = await fetch(url, { credentials: 'include' });
  if (!response.ok) return { ok: false, status: response.status };
  const buffer = await response.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let index = 0; index < bytes.length; index += 0x8000) {
    binary += String.fromCharCode.apply(null, bytes.subarray(index, index + 0x8000));
  }
  return { ok: true, size: bytes.length, base64: btoa(binary) };
}"""


def call_export_api(page: Any, template: dict[str, Any], body: dict[str, Any]) -> Any:
    result = page.evaluate(
        CALL_API_JS,
        {
            "url": str(template.get("url") or ""),
            "method": str(template.get("method") or "POST").upper(),
            "headers": clean_headers(template.get("headers") or {}),
            "body": json.dumps(body or {}, ensure_ascii=False),
        },
    )
    if not isinstance(result, dict):
        raise RuntimeError("导出接口调用失败：页面没有返回结果")
    payload = parse_json(result.get("text"))
    if not result.get("ok") or response_failed(payload):
        message = response_message(payload) or f"HTTP {result.get('status')}"
        raise RuntimeError(f"导出接口返回失败：{message[:120]}")
    return payload


def read_export_history(
    page: Any, template: dict[str, Any], task_type: Any = None
) -> list[dict[str, Any]]:
    base = parse_json(template.get("body") or "") or {"pageNum": 1, "pageSize": 20}
    if not isinstance(base, dict):
        base = {"pageNum": 1, "pageSize": 20}
    try:
        size = max(10, int(base.get("pageSize") or 10))
    except (TypeError, ValueError):
        size = 20
    body = {**base, "pageNum": 1, "pageSize": size}
    if task_type is not None:
        body["taskType"] = task_type
    return temu_result_list(call_export_api(page, template, body))


def create_export(
    page: Any, template: dict[str, Any], days: int, task_type: Any = None
) -> tuple[dict[str, Any], Any]:
    source = parse_json(template.get("body") or "") or {}
    body = dict(source) if isinstance(source, dict) else {}
    end = _beijing_now()
    begin = end - timedelta(days=max(1, int(days or 30)))
    # 导出接口的 beginTime/endTime 是毫秒时间戳（Long），不是格式化字符串。
    begin = begin.replace(hour=0, minute=0, second=0, microsecond=0)
    end = end.replace(hour=23, minute=59, second=59, microsecond=0)
    body["fundDetailExport"] = True
    if "beginTime" in body:
        body["beginTime"] = int(begin.timestamp() * 1000)
    if "endTime" in body:
        body["endTime"] = int(end.timestamp() * 1000)
    if task_type is not None:
        body["taskType"] = task_type
    if body.get("taskType") is None:
        raise RuntimeError("录到的导出请求里没有 taskType，请先手动导出一次再重试")
    return body, call_export_api(page, template, body)


def poll_export_history(
    page: Any,
    template: dict[str, Any],
    task_type: Any = None,
    baseline_ids: list[str] | None = None,
    on_tick: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    started = time.time()
    baseline = {str(item) for item in (baseline_ids or [])}
    last_text = ""
    while time.time() < deadline:
        try:
            items = read_export_history(page, template, task_type)
            ready = [row for row in items if str(row.get("status")) == "2"]
            fresh = [row for row in ready if str(row.get("id")) not in baseline]
            fresh_detailed = [row for row in fresh if row.get("fundDetailExport") is not False]
            candidate = next(iter(fresh_detailed + fresh), None)
            if candidate is None and time.time() - started > 90:
                # 等了 90 秒还没出现本次新任务，才退回历史里任意已生成的任务。
                ready_detailed = [
                    row for row in ready if row.get("fundDetailExport") is not False
                ]
                candidate = next(iter(ready_detailed + ready), None)
            if candidate:
                return candidate
            last_text = (
                f"共 {len(items)} 条导出记录，仍在生成中" if items else "导出记录为空"
            )
        except Exception as error:  # noqa: BLE001 - 轮询期间继续等
            last_text = str(error)
        if on_tick:
            on_tick(last_text)
        time.sleep(POLL_INTERVAL_SECONDS)
    raise RuntimeError(f"导出文件生成超时：{last_text or '请稍后重试'}")


def resolve_file_url(
    page: Any, templates: dict[str, Any], item: dict[str, Any], task_type: Any = None
) -> str:
    download = templates.get("download")
    if not isinstance(download, dict) or not download.get("url"):
        # 只录到了「创建导出」时，下载接口就是同一个路径 + /download，
        # 请求头（mallid 等）保持一致，请求体只带 id 与 taskType。
        create = templates.get("create")
        create_url = str((create or {}).get("url") or "").rstrip("/")
        if create_url.endswith("/api/merchant/file/export"):
            download = {
                "url": f"{create_url}/download",
                "method": "POST",
                "headers": (create or {}).get("headers") or {},
                "body": "",
            }
    if isinstance(download, dict) and download.get("url"):
        base = parse_json(download.get("body") or "") or {}
        body = dict(base) if isinstance(base, dict) else {}
        body["id"] = item.get("id")
        if task_type is not None:
            body["taskType"] = task_type
        try:
            result = temu_result(call_export_api(page, download, body))
            if isinstance(result, str):
                file_url = result
            elif isinstance(result, dict):
                file_url = str(
                    result.get("fileUrl") or result.get("url") or result.get("downloadUrl") or ""
                )
            else:
                file_url = ""
            if re.match(r"^https?://", file_url):
                return file_url
        except Exception:  # noqa: BLE001 - 下载接口不适用时走直链兜底
            pass

    open_url = str(templates.get("openUrl") or "")
    params = item.get("agentSellerExportParams")
    if open_url and params:
        # 录到的直链自带上次导出的 params/sign，先去掉再换成本次任务的。
        parts = urllib.parse.urlsplit(open_url)
        query = [
            (key, value)
            for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
            if key not in ("params", "sign")
        ]
        query.append(("params", str(params)))
        if item.get("agentSellerExportSign"):
            query.append(("sign", str(item["agentSellerExportSign"])))
        return urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), "")
        )
    raise RuntimeError("没有拿到导出文件地址：请先在页面点一次「下载财务明细」以录制直链")


def file_name_from(file_url: str, item: dict[str, Any]) -> str:
    try:
        name = urllib.parse.unquote(urllib.parse.urlsplit(file_url).path.rsplit("/", 1)[-1])
        if re.search(r"\.(xlsx|xlsm|xls|csv)$", name, re.I):
            return name
    except ValueError:
        pass
    return f"FundDetail-{item.get('id') or int(time.time())}.xlsx"


def fetch_file_bytes(context: Any, page: Any, file_url: str) -> tuple[bytes, str]:
    try:
        response = context.request.get(file_url, timeout=180_000)
        if response.ok:
            data = response.body()
            if len(data) > MAX_FILE_BYTES:
                raise RuntimeError(f"导出文件过大（{len(data)} 字节），已跳过")
            return data, str((response.headers or {}).get("content-type") or "")
    except RuntimeError:
        raise
    except Exception:  # noqa: BLE001 - 共享 cookie 的请求失败就退回页面内 fetch
        pass

    value = page.evaluate(FETCH_FILE_JS, file_url)
    if not isinstance(value, dict) or not value.get("ok"):
        status = (value or {}).get("status") if isinstance(value, dict) else ""
        raise RuntimeError(f"下载导出文件失败：HTTP {status or '未知'}")
    if int(value.get("size") or 0) > MAX_FILE_BYTES:
        raise RuntimeError(f"导出文件过大（{value.get('size')} 字节），已跳过")
    return base64.b64decode(str(value.get("base64") or "")), ""


# ---------- 对账中心导出（导出历史 → 下载财务明细） ----------


def _click_button(page: Any, name: str, timeout: int = 8000) -> bool:
    try:
        button = page.get_by_role("button", name=name).first
        if not button.count():
            return False
        button.click(timeout=timeout, force=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def _clear_guide(page: Any) -> None:
    """新手引导的 driver.js 遮罩会挡住所有点击，直接移除。"""
    try:
        page.evaluate(
            "() => document.querySelectorAll('[class*=driver-overlay]')"
            ".forEach(node => node.remove())"
        )
    except Exception:  # noqa: BLE001
        pass


def _page_has_button(page: Any, name: str) -> bool:
    try:
        return bool(page.get_by_role("button", name=name).first.count())
    except Exception:  # noqa: BLE001
        return False


def _find_page(context: Any, button: str) -> Any | None:
    """授权页可能新开标签，需要在上下文里找到真正挂着该按钮的那一页。"""
    try:
        pages = list(context.pages)
    except Exception:  # noqa: BLE001
        return None
    for candidate in pages:
        if _page_has_button(candidate, button):
            return candidate
    return None


def _switch_tab(page: Any, name: str) -> bool:
    """结算中心顶部有「待处理款项 / 已到账款项」两个页签，导出内容跟着当前页签走。"""
    try:
        loc = page.get_by_text(name, exact=True)
        count = min(3, loc.count())
    except Exception:  # noqa: BLE001
        return False
    for index in range(count):
        try:
            loc.nth(index).click(timeout=5000, force=True)
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


TICK_CONSENT_JS = """() => {
  const label = document.querySelector('label[class*=CBX_outerWrapper]');
  if (label) { label.click(); return true; }
  const input = document.querySelector('input[type=checkbox]');
  if (input) { input.click(); return true; }
  return false;
}"""

CONFIRM_AUTH_JS = """() => {
  const button = Array.from(document.querySelectorAll('button'))
      .find((b) => (b.innerText || '').includes('确认授权并前往'));
  if (button) { button.click(); return true; }
  return false;
}"""


def tick_consent(page: Any) -> bool:
    """授权/登录页的协议勾选框：不勾选直接点按钮没反应。"""
    try:
        checkbox = page.locator("input[type=checkbox]").first
        if not checkbox.count() or checkbox.is_checked():
            return False
    except Exception:  # noqa: BLE001
        return False
    try:
        text = str(page.evaluate("() => document.body.innerText || ''"))
    except Exception:  # noqa: BLE001
        text = ""
    if "隐私政策" not in text and "账号使用须知" not in text:
        return False
    try:
        clicked = bool(page.evaluate(TICK_CONSENT_JS))
    except Exception:  # noqa: BLE001
        clicked = False
    try:
        page.wait_for_timeout(600)
    except Exception:  # noqa: BLE001
        pass
    return clicked


def _pass_sso(page: Any) -> bool:
    """跨境后台跳美区后台前会弹「授权」页：必须勾选协议再点确认，否则按钮点不动。"""
    if not _page_has_button(page, "确认授权并前往"):
        return False
    tick_consent(page)
    page.wait_for_timeout(800)
    try:
        page.get_by_role("button", name="确认授权并前往").first.click(
            force=True, timeout=8000
        )
        return True
    except Exception:  # noqa: BLE001
        pass
    try:
        return bool(page.evaluate(CONFIRM_AUTH_JS))
    except Exception:  # noqa: BLE001
        return False


CLICK_TEXT_JS = """(word) => {
  const buttons = Array.from(document.querySelectorAll('button'))
      .filter((b) => (b.innerText || '').trim() === word);
  if (buttons.length) { buttons[0].click(); return 'button'; }
  const leaves = [];
  const walk = (node) => {
    for (const child of (node.children || [])) walk(child);
    if ((node.innerText || '').trim() === word && !node.children.length) leaves.push(node);
  };
  walk(document.body);
  for (let i = leaves.length - 1; i >= 0; i -= 1) {
    const leaf = leaves[i];
    if (leaf.closest('nav, [class*=side], [class*=Side], [class*=menu], [class*=Menu]')) continue;
    leaf.click();
    return 'leaf';
  }
  return 'none';
}"""

HAS_BUTTON_JS = """(needle) => Array.from(document.querySelectorAll('button'))
    .some((b) => (b.innerText || '').includes(needle))"""

# 对账中心「导出历史」里按站点给按钮：财务明细才是 PO 级结算 + 违规扣款。
BILL_DETAIL_LABELS = ("下载财务明细(美国)", "下载财务明细(全球)", "下载财务明细(欧区)")


def _click_text_js(page: Any, text: str) -> str:
    try:
        return str(page.evaluate(CLICK_TEXT_JS, text) or "none")
    except Exception:  # noqa: BLE001
        return "none"


def _has_button_text(page: Any, needle: str) -> bool:
    try:
        return bool(page.evaluate(HAS_BUTTON_JS, needle))
    except Exception:  # noqa: BLE001
        return False


def _open_export_history(page: Any) -> bool:
    """点工具栏的「导出历史」，弹出报表列表；列表里同时有「下载财务明细」按钮。"""
    for attempt in range(2):
        mode = _click_text_js(page, "导出历史")
        page.wait_for_timeout(2500)
        if _has_button_text(page, "下载财务明细"):
            return True
        if mode == "none":
            break
        if attempt == 0:
            try:
                page.keyboard.press("Escape")
            except Exception:  # noqa: BLE001
                pass
            page.wait_for_timeout(1200)
    return _has_button_text(page, "下载财务明细")


def _create_bill_export(page: Any) -> bool:
    """对账中心「导出」→ 选「导出列表 + 账务详情」→ 确认。"""
    if not _click_text_js(page, "导出"):
        return False
    page.wait_for_timeout(2500)
    try:
        page.get_by_text("导出列表 + 账务详情", exact=False).first.click(timeout=6000, force=True)
    except Exception:  # noqa: BLE001
        pass
    page.wait_for_timeout(800)
    return _click_button(page, "确认", 8000)


HISTORY_VISIBLE_JS = """() => /账务明细生成时间|生成中/.test(document.body.innerText)"""

DOWNLOAD_STATE_JS = """() => Array.from(document.querySelectorAll('button'))
    .map((b) => ({text: (b.innerText || '').trim(), disabled: !!b.disabled, visible: !!b.offsetParent}))
    .filter((x) => x.text.indexOf('下载') === 0)"""

CLICK_ENABLED_TEXT_JS = """(word) => {
  const buttons = Array.from(document.querySelectorAll('button'))
      .filter((b) => (b.innerText || '').trim() === word && !b.disabled && b.offsetParent);
  if (!buttons.length) return 'unavailable';
  buttons[0].click();
  return 'button';
}"""


def _history_visible(page: Any) -> bool:
    """导出历史弹层是否已经打开（生成中的报表不出现「下载财务明细」按钮）。"""
    try:
        return bool(page.evaluate(HISTORY_VISIBLE_JS))
    except Exception:  # noqa: BLE001
        return False


def _detail_download_ready(page: Any) -> bool:
    """导出历史里已有可点击的「下载财务明细(...)」；生成中/已过期的按钮是禁用的。"""
    try:
        states = page.evaluate(DOWNLOAD_STATE_JS)
    except Exception:  # noqa: BLE001
        return False
    return any(
        (not item.get("disabled")) and item.get("visible")
        and "财务明细" in str(item.get("text") or "")
        for item in states
    )


def _click_enabled_text(page: Any, text: str) -> str:
    try:
        return str(page.evaluate(CLICK_ENABLED_TEXT_JS, text) or "unavailable")
    except Exception:  # noqa: BLE001
        return "unavailable"


def _download_bill_label(
    context: Any, page: Any, label: str, timeout: float = 75.0
) -> tuple[bytes, str] | None:
    captured: dict[str, Any] = {}

    def _on_download(download: Any) -> None:
        if captured.get("path"):
            return
        path = f"/tmp/wh-bill-{int(time.time() * 1000)}.xlsx"
        try:
            download.save_as(path)
            captured["path"] = path
            captured["name"] = str(download.suggested_filename or "bill.xlsx")
        except Exception as error:  # noqa: BLE001
            captured["error"] = str(error)

    context.on("download", _on_download)
    try:
        # 生成中 / 已过期的报表按钮是禁用的，点了不会触发下载，直接跳过避免白等。
        if _click_enabled_text(page, label) != "button":
            return None
        deadline = time.time() + timeout
        while time.time() < deadline and not captured.get("path"):
            sso = _find_page(context, "确认授权并前往")
            if sso is not None:
                _pass_sso(sso)
            page.wait_for_timeout(2000)
    finally:
        try:
            context.remove_listener("download", _on_download)
        except Exception:  # noqa: BLE001
            pass

    path = captured.get("path")
    if not path:
        return None
    name = str(captured.get("name") or "bill.xlsx")
    try:
        content = Path(path).read_bytes()
    finally:
        try:
            Path(path).unlink()
        except Exception:  # noqa: BLE001
            pass
    if len(content) > MAX_FILE_BYTES:
        raise RuntimeError(f"导出文件过大（{len(content)} 字节），已跳过")
    return content, name


def _on_bill_page(page: Any) -> bool:
    url = str(getattr(page, "url", "") or "")
    return "/labor/bill" in url and "/labor/settle" not in url


def _ensure_bill_page(
    context: Any,
    page: Any,
    step: Callable[[str], None],
    gate: Callable[[], None] | None = None,
) -> Any:
    """确保停在「对账中心」：跨境域会先弹授权页，授权后可能落到结算中心，要拉回来。"""

    def wait(seconds: float) -> None:
        if callable(gate):
            gate()
        page.wait_for_timeout(int(seconds * 1000))

    for attempt in range(4):
        if _on_bill_page(page):
            return page
        if on_auth_page(page):
            # 授权页（选择中国地区）的 JS 经常失效，直接去唯一可用的 /login 登录页。
            step("站点授权")
            try:
                page.goto(
                    seller_login_url(FINANCE_URLS[0]),
                    wait_until="domcontentloaded",
                    timeout=45_000,
                )
            except Exception:  # noqa: BLE001
                pass
            wait(4.0)
        sso = _find_page(context, "确认授权并前往")
        if sso is not None:
            step("确认站点授权")
            _pass_sso(sso)
            wait(5.0)
            for candidate in list(context.pages):
                if _on_bill_page(candidate):
                    return candidate
            continue
        step("打开对账中心")
        try:
            page.goto(
                BILL_URL if attempt % 2 == 0 else FINANCE_URLS[0],
                wait_until="domcontentloaded",
                timeout=45_000,
            )
        except Exception:  # noqa: BLE001
            pass
        wait(4.0)
    return page


def export_settle_report(
    context: Any,
    page: Any,
    on_step: Callable[[str], None] | None = None,
    on_page: Callable[[str], None] | None = None,
    gate: Callable[[], None] | None = None,
) -> tuple[bytes, str]:
    """对账中心导出报表：导出历史里下载「财务明细」，返回 PO 级结算 + 违规扣款文件。

    ``gate`` 是人工接管闸门：管理员在实时画面操作时，流程停在动作之间不点页面。
    """

    def wait(seconds: float) -> None:
        if callable(gate):
            gate()
        page.wait_for_timeout(int(seconds * 1000))
        if callable(gate):
            gate()

    def step(text: str) -> None:
        if callable(on_step):
            on_step(text)
        if callable(gate):
            gate()

    step("打开对账中心")
    try:
        page.goto(BILL_URL, wait_until="domcontentloaded", timeout=45_000)
    except Exception:  # noqa: BLE001
        pass
    wait(4.0)
    for name in ("我知道了", "稍后再说", "关闭", "×"):
        if _click_button(page, name, 3000):
            break
    _clear_guide(page)
    page = _ensure_bill_page(context, page, step, gate)
    if not _on_bill_page(page):
        try:
            needs_login = bool(page.locator("input[type=password]").count())
        except Exception:  # noqa: BLE001
            needs_login = False
        if needs_login or (
            "/login" in str(page.url)
            and not _page_has_button(page, "确认授权并前往")
        ):
            raise RuntimeError(
                "对账中心登录态已失效：请先在实时画面重新登录该店铺"
            )
        raise RuntimeError(
            f"没有停在「对账中心」：当前页面 {page.url}，请确认授权页已点「确认授权并前往」"
        )
    if callable(on_page):
        on_page(str(page.url))

    step("打开导出历史")
    opened = _open_export_history(page)
    # 旧记录存在但下载按钮禁用（生成中/已过期）时不能直接下载，必须重新发起导出。
    if not opened or not _detail_download_ready(page):
        step("创建导出任务")
        if not _create_bill_export(page):
            raise RuntimeError("点击「导出」后没有出现导出确认弹窗")
        deadline = time.time() + 300
        while time.time() < deadline:
            try:
                page.keyboard.press("Escape")
            except Exception:  # noqa: BLE001
                pass
            wait(3.0)
            if not _history_visible(page):
                _click_text_js(page, "导出历史")
                page.wait_for_timeout(2500)
            if _detail_download_ready(page):
                break
            wait(5.0)
        else:
            raise RuntimeError(
                "导出任务已创建但报表仍在生成中：请稍后在「对账中心 → 导出历史」确认可下载的财务明细"
            )

    step("下载财务明细")
    for label in BILL_DETAIL_LABELS:
        if not _history_visible(page):
            _open_export_history(page)
        result = _download_bill_label(context, page, label)
        if result is not None:
            return result

    # 有可点击按钮但没触发下载（例如弹出授权页）：补建一次当期导出再重试。
    step("重新导出后重试")
    if _create_bill_export(page):
        deadline = time.time() + 180
        while time.time() < deadline:
            try:
                page.keyboard.press("Escape")
            except Exception:  # noqa: BLE001
                pass
            wait(3.0)
            if not _history_visible(page):
                _click_text_js(page, "导出历史")
                page.wait_for_timeout(2500)
            if _detail_download_ready(page):
                for label in BILL_DETAIL_LABELS:
                    result = _download_bill_label(context, page, label, timeout=30.0)
                    if result is not None:
                        return result
            wait(5.0)

    raise RuntimeError(
        "导出历史里没有可下载的财务明细：请在实时画面点一次「对账中心 → 导出历史」确认报表已生成"
    )


# ---------- 解析账务明细（与本地 runtime 的 settlement_file 保持一致） ----------

MONEY_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
PO_RE = re.compile(r"PO-[A-Za-z0-9-]+", re.I)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\u00a0", " ").strip()


def _money(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = _text(value).replace(",", "").replace("¥", "").replace("￥", "")
    negative = text.startswith("-") or (text.startswith("(") and text.endswith(")"))
    match = MONEY_RE.search(text.strip("-() "))
    if not match:
        return None
    amount = float(match.group(0).replace(",", ""))
    return -amount if negative else amount


def _sheet_rows(content: bytes) -> dict[str, list[list[Any]]]:
    try:
        from openpyxl import load_workbook
    except ImportError as error:  # pragma: no cover - 部署环境需带 openpyxl
        raise RuntimeError("服务器缺少 openpyxl，无法解析 Excel 导出文件") from error
    workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    return {
        sheet.title: [list(row) for row in sheet.iter_rows(values_only=True)]
        for sheet in workbook.worksheets
    }


def _cell(row: list[Any], index: int) -> Any:
    if index < 0 or index >= len(row):
        return None
    return row[index]


def _pick(
    header: list[str], exact: tuple[str, ...] = (), contains: tuple[str, ...] = ()
) -> int:
    for name in exact:
        for index, value in enumerate(header):
            if value == name:
                return index
    for keyword in contains:
        for index, value in enumerate(header):
            if keyword in value:
                return index
    return -1


def _pick_tail(header: list[str], keyword: str) -> int:
    """按「列名最后一段」匹配：两行表头拼出来是「商品信息 * 销售件数/件数」这种。"""
    for index, name in enumerate(header):
        if name == keyword or name.split("/")[-1] == keyword:
            return index
    return -1


def _find_row_with(rows: list[list[Any]], *keywords: str, limit: int = 40) -> int:
    for index, row in enumerate(rows[:limit]):
        joined = " ".join(_text(cell) for cell in row)
        if all(keyword in joined for keyword in keywords):
            return index
    return -1


def _is_sub_header(row: list[Any]) -> bool:
    joined = " ".join(_text(cell) for cell in row)
    if PO_RE.search(joined):
        return False
    return any(keyword in joined for keyword in ("SKU", "件数", "申报价格"))


def _merge_header(top: list[Any], bottom: list[Any]) -> list[str]:
    """两行表头：上行合并单元格只有首列有值，向右填充后与子列名拼接。"""
    size = max(len(top), len(bottom))
    names: list[str] = []
    carry = ""
    for index in range(size):
        upper = _text(_cell(top, index))
        lower = _text(_cell(bottom, index))
        base = upper or carry
        carry = base
        names.append(f"{base}/{lower}" if lower and lower != base else base)
    return names


def _parse_rows(
    header: list[str], rows: list[list[Any]], start: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    index = {
        "flow_id": _pick(header, ("结算流水ID",), ("结算流水ID",)),
        "batch": _pick(header, ("批次号",), ("批次号",)),
        "po": _pick(header, ("PO单号",), ("PO单号",)),
        "doc": _pick(header, ("单据号",), ("单据号",)),
        "trade": _pick(header, ("交易类型",), ("交易类型",)),
        "amount": _pick(header, ("结算金额",), ("结算金额",)),
        "currency": _pick(header, ("币种",), ("币种",)),
        "sku": _pick(header, ("商品信息/SKU ID",), ("SKU ID", "SKU")),
        "sku_name": _pick(header, ("商品信息/SKU名称",), ("SKU名称",)),
        "quantity": _pick_tail(header, "件数"),
        "declared": _pick(header, ("商品信息/申报价格",), ("/申报价格",)),
        "arrive": _pick(header, ("到账时间",), ("到账时间",)),
        "site": _pick(header, ("站点", "国家"), ("站点", "国家")),
        # 结算中心导出没有「结算金额 / 交易类型」，按各回款列直接累加。
        "sales_payback": _pick(header, ("销售回款",), ("销售回款",)),
        "sales_reversal": _pick(header, ("销售冲回",), ("销售冲回",)),
        "freight_payback": _pick(header, ("运费回款",), ("运费回款",)),
        "freight_reversal": _pick(header, ("运费冲回",), ("运费冲回",)),
    }
    if index["po"] < 0:
        return [], []
    if index["amount"] < 0 and index["sales_payback"] < 0:
        return [], []

    def cell_money(row: list[Any], key: str) -> float | None:
        column = index.get(key, -1)
        if column < 0:
            return None
        return _money(_cell(row, column))

    records: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    entries: list[dict[str, Any]] = []
    for row in rows[start:]:
        if not any(_text(cell) for cell in row):
            continue
        po = _text(_cell(row, index["po"])).upper()
        if not po:
            continue
        record = records.get(po)
        if record is None:
            record = records[po] = {
                "parent_order_sn": po,
                "currency": _text(_cell(row, index["currency"])) or "CNY",
                "settlement_amount": 0.0,
                "sales_payback": 0.0,
                "freight_payback": 0.0,
                "sales_reversal": 0.0,
                "freight_reversal": 0.0,
                "declared_price": None,
                "quantity": 0,
                "goods_name": "",
                "sku_id": "",
                "batch_no": "",
                "flow_id": "",
                "doc_no": "",
                "arrive_time": "",
                "site": _text(_cell(row, index["site"])),
                "trade_types": [],
            }
            order.append(po)
        amount = _money(_cell(row, index["amount"])) if index["amount"] >= 0 else None
        if amount is None:
            amount = sum(
                value
                for value in (
                    cell_money(row, "sales_payback"),
                    cell_money(row, "sales_reversal"),
                    cell_money(row, "freight_payback"),
                    cell_money(row, "freight_reversal"),
                )
                if value
            )
        trade = _text(_cell(row, index["trade"]))
        entries.append(
            {
                "kind": "settlement",
                "flow_id": _text(_cell(row, index["flow_id"])),
                "batch_no": _text(_cell(row, index["batch"])),
                "parent_order_sn": po,
                "doc_no": _text(_cell(row, index["doc"])),
                "trade_type": trade or "结算",
                "amount": round(amount, 2),
                "currency": _text(_cell(row, index["currency"])) or "CNY",
                "sku_id": _text(_cell(row, index["sku"])),
                "sku_name": _text(_cell(row, index["sku_name"])),
                "quantity": int(_money(_cell(row, index["quantity"])) or 0),
                "declared_price": _money(_cell(row, index["declared"])),
                "arrive_time": _text(_cell(row, index["arrive"])),
            }
        )
        record["settlement_amount"] += amount
        if trade and trade not in record["trade_types"]:
            record["trade_types"].append(trade)
        if index["trade"] < 0:
            # 结算中心导出没有交易类型列，各回款列本身就是明细。
            for key in ("sales_payback", "sales_reversal", "freight_payback", "freight_reversal"):
                record[key] += cell_money(row, key) or 0.0
        elif "冲回" in trade:
            if "运费" in trade:
                record["freight_reversal"] += amount
            else:
                record["sales_reversal"] += amount
        elif "运费" in trade:
            record["freight_payback"] += amount
        elif "销售" in trade:
            record["sales_payback"] += amount
        quantity = _money(_cell(row, index["quantity"]))
        if quantity:
            record["quantity"] += int(quantity)
        declared = _money(_cell(row, index["declared"]))
        if declared is not None:
            record["declared_price"] = declared
        name = _text(_cell(row, index["sku_name"]))
        if name and not record["goods_name"]:
            record["goods_name"] = name[:200]
        sku = _text(_cell(row, index["sku"]))
        if sku and not record["sku_id"]:
            record["sku_id"] = sku
        batch = _text(_cell(row, index["batch"]))
        if batch and not record["batch_no"]:
            record["batch_no"] = batch
        flow = _text(_cell(row, index["flow_id"]))
        if flow and not record["flow_id"]:
            record["flow_id"] = flow
        doc = _text(_cell(row, index["doc"]))
        if doc and not record["doc_no"]:
            record["doc_no"] = doc
        arrive = _text(_cell(row, index["arrive"]))
        if arrive > record["arrive_time"]:
            record["arrive_time"] = arrive

    parsed = [records[po] for po in order]
    for record in parsed:
        for key in (
            "settlement_amount",
            "sales_payback",
            "freight_payback",
            "sales_reversal",
            "freight_reversal",
        ):
            record[key] = round(record[key], 2)
    return parsed, entries


def _parse_penalty_entries(sheets: dict[str, list[list[Any]]]) -> list[dict[str, Any]]:
    """违规扣款 sheet 的每一行都是一笔支出，逐笔保留，供界面展示。"""
    entries: list[dict[str, Any]] = []
    for title, rows in sheets.items():
        if "结算" in title or not rows:
            continue
        header_index = _find_row_with(rows, "订单编号", "支出金额")
        if header_index < 0:
            continue
        header = [_text(cell) for cell in rows[header_index]]
        order_col = _pick(header, ("订单编号",), ("订单编号", "订单号"))
        amount_col = _pick(header, ("支出金额",), ("支出金额", "扣款金额"))
        if order_col < 0 or amount_col < 0:
            continue
        type_col = _pick(header, ("违规类型",), ("违规类型", "扣款类型"))
        code_col = _pick(header, ("违规编号",), ("违规编号",))
        time_col = _pick(header, ("账务时间",), ("账务时间",))
        currency_col = _pick(header, ("币种",), ("币种",))
        for row in rows[header_index + 1:]:
            sn = _text(_cell(row, order_col)).upper()
            amount = _money(_cell(row, amount_col))
            if not sn or amount is None:
                continue
            entries.append(
                {
                    "kind": "penalty",
                    "flow_id": "",
                    "batch_no": "",
                    "parent_order_sn": sn,
                    "doc_no": _text(_cell(row, code_col)),
                    "trade_type": _text(_cell(row, type_col)) or "违规扣款",
                    "amount": round(amount, 2),
                    "currency": _text(_cell(row, currency_col)) or "CNY",
                    "sku_id": "",
                    "sku_name": "",
                    "quantity": 0,
                    "declared_price": None,
                    "arrive_time": _text(_cell(row, time_col)),
                    "sheet": title,
                }
            )
    return entries


def _parse_fund_detail(
    content: bytes, filename: str = ""
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """解析财务明细文件，返回「按 PO 聚合的行」和「逐笔明细」。"""
    if not content:
        raise RuntimeError("导出文件是空的")
    if not str(filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise RuntimeError(f"导出文件格式不支持：{filename or '未知'}")
    sheets = _sheet_rows(content)

    matched = False
    for title, rows in sheets.items():
        if not rows:
            continue
        header_index = _find_row_with(rows, "PO单号", "结算金额")
        if header_index < 0:
            # 旧版结算中心导出的表头是「PO单号 + 销售回款」，没有结算金额列。
            header_index = _find_row_with(rows, "PO单号", "销售回款")
        if header_index < 0:
            continue
        matched = True
        bottom = rows[header_index + 1] if header_index + 1 < len(rows) else []
        has_sub = bool(bottom) and _is_sub_header(bottom)
        header = (
            _merge_header(rows[header_index], bottom)
            if has_sub
            else [_text(cell) for cell in rows[header_index]]
        )
        parsed, entries = _parse_rows(header, rows, header_index + (2 if has_sub else 1))
        if not parsed:
            continue
        penalty_entries = _parse_penalty_entries(sheets)
        penalties: dict[str, float] = {}
        for entry in penalty_entries:
            sn = entry["parent_order_sn"]
            penalties[sn] = round(penalties.get(sn, 0.0) + float(entry["amount"]), 2)
        for record in parsed:
            record["penalty_amount"] = penalties.pop(record["parent_order_sn"], 0.0)
        # 违规单没对上 PO 的（例如子单号）单独留一行，保证罚款合计不缺。
        for sn, amount in penalties.items():
            parsed.append(
                {
                    "parent_order_sn": sn,
                    "currency": "CNY",
                    "settlement_amount": 0.0,
                    "sales_payback": 0.0,
                    "freight_payback": 0.0,
                    "sales_reversal": 0.0,
                    "freight_reversal": 0.0,
                    "declared_price": None,
                    "quantity": 0,
                    "goods_name": "",
                    "sku_id": "",
                    "batch_no": "",
                    "flow_id": "",
                    "doc_no": "",
                    "arrive_time": "",
                    "site": "",
                    "trade_types": [],
                    "penalty_amount": amount,
                }
            )
        entries.extend(penalty_entries)
        return parsed, entries
    if matched:
        # 表头认得出来但没有任何数据行：该店铺这段时间确实没有结算记录。
        return [], []
    raise RuntimeError("没有找到包含「PO单号 / 结算金额」的结算表，请确认导出时勾选了账务详情")


def parse_fund_detail_with_entries(
    content: bytes, filename: str = ""
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return _parse_fund_detail(content, filename)


def parse_fund_detail(content: bytes, filename: str = "") -> list[dict[str, Any]]:
    """把「导出列表 + 账务详情」的 Excel 解析成按 PO 聚合的结算行。"""
    return _parse_fund_detail(content, filename)[0]



def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    amount = round(sum(float(row.get("settlement_amount") or 0) for row in rows), 2)
    penalty = round(sum(float(row.get("penalty_amount") or 0) for row in rows), 2)
    return {
        "po_count": len(rows),
        "settlement_amount": amount,
        "penalty_amount": penalty,
    }
