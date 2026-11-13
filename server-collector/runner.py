"""服务端采集器：用 Playwright 驱动服务器上的 Chromium。

设计原则：
- 每个店铺一个持久化 profile，登录态天然复用，重启不丢；
- 登录遇到验证码时把控制权交回界面（截图 + 等用户填），不硬编码任何绕过；
- 订单解析直接复用工作台插件的 ``temu_order_profit.js``，不重写一套规则。
"""

from __future__ import annotations

import json
import random
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from page_book import PageBook
from settlement import (
    BILL_URL,
    FINANCE_URLS,
    export_settle_report,
    parse_fund_detail_with_entries,
    seller_login_url,
    summarize,
    tick_consent,
    _on_bill_page,
)

ENTRY_URL = "https://agentseller-us.temu.com/"
# 跨境卖家的登录页默认是扫码页签，只有点开「手机号登录」才会出现账号密码框。
KJ_LOGIN_URL = "https://seller.kuajingmaihuo.com/login?redirectUrl={}"
ORDERS_URL = (
    "https://agentseller-us.temu.com/mmsos/orders.html"
    "?fulfillmentMode=0&queryType=2&sortType=1&timeZone=UTC%2B8"
    "&needBuySignService=0&sellerNoteLabelList=&packageAbnormalTypeList=&is_back=1"
)
DETAIL_URL = (
    "https://agentseller-us.temu.com/mmsos/order-detail.html"
    "?timezone=UTC%2B8&parent_order_sn={po}&fulfillment_mode=0"
)

# 详情页的金额、商品、状态都随 HTML 一起服务端直出，页面内并发 fetch 即可拿到，
# 不需要逐页打开浏览器（单页打开要 4-10 秒，批量请求 24 单约 20 秒）。
# 这里把原始 HTML 解析成页面文本，再复用插件里的 parseOrderDetailText，规则不重复实现。
DETAIL_FETCH_JS = r"""async (items) => {
    const lib = window.WorkbenchTemuOrderProfit;
    if (!lib) return items.map((entry) => ({ po: entry.po, error: 'no-lib' }));
    const BLOCK = new Set(['DIV', 'P', 'TR', 'LI', 'SECTION', 'TABLE', 'THEAD', 'TBODY',
        'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'BR', 'ARTICLE', 'HEADER', 'FOOTER', 'UL', 'OL', 'TD', 'TH']);

    const textOf = (html) => {
        const doc = new DOMParser().parseFromString(html, 'text/html');
        const parts = [];
        const walk = (node) => {
            for (const child of (node.childNodes || [])) {
                if (child.nodeType === 3) { parts.push(child.nodeValue || ''); continue; }
                if (child.nodeType !== 1) continue;
                const tag = String(child.tagName || '').toUpperCase();
                if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'NOSCRIPT') continue;
                const block = BLOCK.has(tag);
                if (block) parts.push('\n');
                walk(child);
                if (block) parts.push('\n');
            }
        };
        walk(doc.body || doc.documentElement);
        return parts.join('')
            .replace(/\u200b/g, '')
            .replace(/[ \t\r\f\v]+/g, ' ')
            .replace(/\n\s*\n+/g, '\n')
            .trim();
    };

    const rawOf = (html) => {
        const marker = 'window.rawData=';
        const idx = html.indexOf(marker);
        if (idx < 0) return null;
        let depth = 0, inStr = false, esc = false, end = -1;
        const start = idx + marker.length;
        for (let i = start; i < html.length; i++) {
            const ch = html[i];
            if (inStr) {
                if (esc) esc = false;
                else if (ch === '\\') esc = true;
                else if (ch === '"') inStr = false;
                continue;
            }
            if (ch === '"') inStr = true;
            else if (ch === '{') depth++;
            else if (ch === '}') { depth--; if (depth === 0) { end = i + 1; break; } }
        }
        if (end < 0) return null;
        try { return JSON.parse(html.slice(start, end)); } catch (e) { return null; }
    };

    return await Promise.all(items.map(async (entry) => {
        const po = entry.po;
        try {
            const resp = await fetch(entry.url, { credentials: 'include' });
            const html = await resp.text();
            if (!resp.ok) return { po, error: 'http ' + resp.status };
            const text = textOf(html);
            let item = lib ? lib.parseOrderDetailText(text) : null;
            const raw = rawOf(html);
            let image = '', tracking = '', declared = null;
            if (raw) {
                const store = (raw.store && raw.store.store) || {};
                const odd = store.orderDetailData || {};
                const orders = odd.orderList || [];
                const parent = odd.parentOrderMap || {};
                const first = orders[0] || {};
                image = first.thumbUrl || '';
                const priceMap = store.orderSupplierPriceMap || {};
                const values = Object.keys(priceMap).map((k) => priceMap[k]).filter((v) => typeof v === 'number');
                // 多个子单时申报价各不相同，交给文本解析结果，避免把某一个子单的价格当成父单价格。
                if (values.length === 1) declared = values[0] / 100;
                const waybills = parent.waybillInfoList || [];
                const matched = waybills.find((w) => item && w.packageSn === item.package_sn)
                    || waybills.find((w) => w.trackingNumber);
                if (matched && matched.trackingNumber) tracking = String(matched.trackingNumber);
                if (!item) {
                    // 已取消等没有金额区块的订单：文本解析不出详情，但 rawData 里仍有商品、包裹、时间。
                    item = {
                        parent_order_sn: String(parent.parentOrderSn || po),
                        sub_order_sn: String(first.orderSn || ''),
                        skc_id: String(first.skcId || ''),
                        sku_id: String(first.skuId || ''),
                        goods_name: String(first.goodsName || first.originalGoodsName || ''),
                        package_sn: String((waybills[0] || {}).packageSn || ''),
                        image_url: image,
                        order_time: String(parent.parentOrderTimeStr || ''),
                        site: '',
                        order_status: '',
                        order_status_code: '',
                        tracking_no: tracking,
                        source: 'ssr-raw',
                    };
                }
            }
            const login = !item && (
                html.indexOf('id="usernameId"') >= 0
                || resp.url.indexOf('/auth/') >= 0
                || resp.url.indexOf('seller-login') >= 0
            );
            return { po, item, image, tracking, declared, login, len: html.length, raw: !!raw };
        } catch (err) {
            return { po, error: String(err) };
        }
    }));
}"""

# 详情页才有申报价与各项回款；这些字段只能从这里补。
DETAIL_AMOUNT_FIELDS = (
    "declared_price",
    "declared_currency",
    "gmv",
    "gmv_currency",
    "sales_payback",
    "sales_reversal",
    "freight_payback",
    "freight_reversal",
    "estimated_income",
    "order_payment",
    "second_payment",
    "tail_freight",
    "tail_currency",
)
DETAIL_BASE_FIELDS = (
    "order_status",
    "order_status_code",
    "order_time",
    "tracking_no",
    "goods_name",
    "image_url",
    "site",
    "package_sn",
    "ext_code",
)

SITE_LABELS = {"us": "美国", "eu": "欧区", "global": "全球"}
PAGE_LABELS = {
    "bill": "对账中心",
    "auth": "站点授权页",
    "login": "登录页",
    "challenge": "安全验证页",
    "unknown": "未识别页面",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    account_id: str
    action: str
    status: str = "running"
    step: str = ""
    message: str = ""
    progress: int = 0
    need_code: bool = False
    # 人工接管：paused=True 时自动化停在原地，不再点击页面。
    paused: bool = False
    page_label: str = ""
    updated_at: str = field(default_factory=_now)
    code_value: str = ""
    code_event: threading.Event = field(default_factory=threading.Event)
    resume_event: threading.Event = field(default_factory=threading.Event)

    def snapshot(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "action": self.action,
            "status": self.status,
            "step": self.step,
            "message": self.message,
            "progress": self.progress,
            "need_code": self.need_code,
            "paused": self.paused,
            "page_label": self.page_label,
            "updated_at": self.updated_at,
        }


class CollectorRunner:
    def __init__(
        self,
        *,
        data_dir: str | Path,
        order_script: str | Path,
        chrome_path: str = "",
        site_key: str = "us",
        max_pages: int = 20,
        max_details: int = 2000,
        detail_budget_seconds: int = 2400,
        detail_batch: int = 24,
        detail_http: bool = True,
        store: Any | None = None,
        temu_workflow: bool = False,
    ) -> None:
        self._dir = Path(data_dir)
        self._profiles = self._dir / "profiles"
        self._shots = self._dir / "shots"
        self._profiles.mkdir(parents=True, exist_ok=True)
        self._shots.mkdir(parents=True, exist_ok=True)
        self._order_script = Path(order_script)
        self._chrome = chrome_path
        self._site_key = site_key
        self._max_pages = max(1, int(max_pages))
        self._max_details = max(0, int(max_details))
        self._detail_budget = max(60, int(detail_budget_seconds))
        self._detail_batch = max(4, min(48, int(detail_batch)))
        self._detail_http = bool(detail_http)
        self._store = store
        self._temu_workflow = bool(temu_workflow)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        # 已见过的页面 / 弹窗记录，重复出现时按记录处理。
        self._book = PageBook(self._dir / "page_book.json")
        # 每个任务跑在自己的线程里，等待类操作要知道"当前是哪个任务被暂停了"。
        self._local = threading.local()

    # ---------- 对外 ----------

    def job(self, account_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(account_id)
        return job.snapshot() if job else None

    def pause(self, account_id: str) -> bool:
        """人工接管：让自动化停在原地，把浏览器让给管理员。"""
        with self._lock:
            job = self._jobs.get(account_id)
        if job is None or job.status != "running":
            return False
        job.paused = True
        job.updated_at = _now()
        return True

    def resume(self, account_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(account_id)
        if job is None or job.status != "running":
            return False
        job.paused = False
        job.resume_event.set()
        job.updated_at = _now()
        return True

    def jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [job.snapshot() for job in self._jobs.values()]

    def shot_path(self, account_id: str) -> Path:
        return self._shots / f"{account_id}.png"

    def submit_code(self, account_id: str, code: str) -> bool:
        with self._lock:
            job = self._jobs.get(account_id)
        if job is None or not job.need_code:
            return False
        job.code_value = str(code or "").strip()
        job.code_event.set()
        return True

    def start(
        self,
        account_id: str,
        action: str,
        worker: Callable[[Job], dict[str, Any]],
        on_done: Callable[[str, dict[str, Any]], None],
    ) -> Job:
        with self._lock:
            current = self._jobs.get(account_id)
            if current is not None and current.status == "running":
                raise RuntimeError("该账号正在执行任务")
            job = Job(account_id=account_id, action=action)
            self._jobs[account_id] = job

        def run() -> None:
            self._local.job = job
            try:
                result = worker(job)
                result.setdefault("action", job.action)
                job.status = "done" if result.get("ok") else "failed"
                job.message = str(result.get("message") or "")
                job.progress = 100 if result.get("ok") else job.progress
                on_done(account_id, result)
            except BaseException as error:  # noqa: BLE001 - 后台任务必须收口
                job.status = "failed"
                name = type(error).__name__
                if "TargetClosed" in name:
                    job.message = "浏览器页面被关闭：若已登录成功，直接点「采集订单」验证"
                else:
                    job.message = f"{name}: {error}"
                on_done(account_id, {"ok": False, "action": job.action, "message": job.message})
            finally:
                job.need_code = False
                job.updated_at = _now()

        threading.Thread(target=run, name=f"collector-{account_id}", daemon=True).start()
        return job

    # ---------- 浏览器 ----------

    def _current_job(self) -> Job | None:
        return getattr(self._local, "job", None)

    def _is_paused(self) -> bool:
        job = self._current_job()
        return bool(job is not None and job.paused)

    def _settle_gate(self) -> None:
        """给 settlement 导出流程用的闸门：被人工接管时停在动作之间等恢复。"""
        while self._is_paused():
            time.sleep(0.3)

    def _launch(self):
        from playwright.sync_api import sync_playwright

        return sync_playwright().start()

    def _context(self, playwright: Any, account_id: str):
        profile = self._profiles / f"acc-{account_id}"
        profile.mkdir(parents=True, exist_ok=True)
        options: dict[str, Any] = {
            "user_data_dir": str(profile),
            "headless": bool(self._temu_workflow),
            "viewport": {"width": 1440, "height": 900},
            "args": [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--lang=zh-CN",
                # 固定窗口位置和尺寸，保证 noVNC 里看到的是完整页面而不是偏移的一角。
                "--window-position=0,0",
                "--window-size=1440,900",
            ],
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
        }
        if self._chrome:
            options["executable_path"] = self._chrome
        context = playwright.chromium.launch_persistent_context(**options)
        context.set_default_timeout(20_000)
        # 详情页要遍历几百次，把纯装饰资源拦掉能省一大半加载时间；
        # 商品图（kwcdn）保留，否则读不到图片尺寸。
        blocked_types = {"font", "media", "texttrack", "manifest"}

        def _route(route: Any) -> None:
            try:
                request = route.request
                if request.resource_type in blocked_types:
                    route.abort()
                    return
                if request.resource_type == "image" and "kwcdn.com" not in request.url:
                    route.abort()
                    return
            except Exception:  # noqa: BLE001
                pass
            route.continue_()

        context.route("**/*", _route)
        return context

    def _safe_wait(self, page: Any, milliseconds: int) -> None:
        """页面/标签可能被站点自己关掉（登录成功后弹窗会关），等待不能因此抛错。

        被管理员接管时在这里停住：自动化不会在用户操作期间继续点击页面。
        """
        self._settle_gate()
        try:
            page.wait_for_timeout(milliseconds)
        except Exception:  # noqa: BLE001
            time.sleep(milliseconds / 1000)
        self._settle_gate()

    @staticmethod
    def _alive(page: Any) -> bool:
        try:
            return bool(page) and not page.is_closed()
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _pick_page(context: Any, fallback: Any) -> Any:
        for candidate in context.pages:
            try:
                if not candidate.is_closed():
                    return candidate
            except Exception:  # noqa: BLE001
                continue
        try:
            return context.new_page()
        except Exception:  # noqa: BLE001
            return fallback

    def _dismiss_popups(self, page: Any) -> None:
        """关掉语言提示、公告、授权提醒等遮挡层；失败不算错误。"""
        if self._is_paused():
            return
        for selector in (
            "button:has-text('知道了')",
            "button:has-text('我知道了')",
            "button:has-text('关闭')",
            "button:has-text('授权')",
            "button:has-text('确定')",
            ".ICN_outerWrapper_5-123-0",
        ):
            try:
                locator = page.locator(selector).first
                if locator.count() and locator.is_visible():
                    locator.click(timeout=1500)
                    page.wait_for_timeout(300)
            except Exception:  # noqa: BLE001 - 弹窗不是主流程
                continue

    def _shot(self, page: Any, account_id: str) -> None:
        try:
            page.screenshot(path=str(self.shot_path(account_id)), full_page=False)
        except Exception:  # noqa: BLE001
            pass

    # ---------- 页面探针 ----------

    def _has_button_text(self, page: Any, needle: str) -> bool:
        try:
            return bool(
                page.evaluate(
                    """(needle) => Array.from(document.querySelectorAll('button'))
                        .some((b) => (b.innerText || '').includes(needle))""",
                    needle,
                )
            )
        except Exception:  # noqa: BLE001
            return False

    def _page_kind(self, page: Any) -> str:
        """把当前页面归类；用于查历史记录和决定是否需要人工。"""
        url = str(getattr(page, "url", "") or "")
        if _on_bill_page(page):
            return "bill"
        if "/auth/authentication" in url or self._has_button_text(page, "确认授权并前往"):
            return "auth"
        if "/settle/seller-login" in url or "/login" in url or self._has_password_field(page):
            return "login"
        try:
            text = str(page.evaluate("() => (document.body && document.body.innerText) || ''"))[:4000]
        except Exception:  # noqa: BLE001
            text = ""
        if any(word in text for word in ("验证码", "安全验证", "滑动", "拖动", "人机验证")):
            return "challenge"
        return "unknown"

    def _observe_page(self, page: Any, job: Job) -> tuple[str, dict[str, Any], bool, str]:
        key, entry, is_new = self._book.observe(page)
        kind = str(entry.get("kind") or "")
        if not kind:
            kind = self._page_kind(page)
            self._book.remember(key, kind=kind)
        job.page_label = PAGE_LABELS.get(kind, kind)
        return key, entry, is_new, kind

    def _wait_for_admin(
        self, page: Any, job: Job, reason: str = "", timeout_seconds: int = 900
    ) -> None:
        """把浏览器交回管理员：暂停自动化，等页面被人工处理或点「继续」。"""
        key, entry, _, _ = self._observe_page(page, job)
        self._book.remember(key, needs_human=True)
        job.paused = True
        job.need_code = True
        job.step = "等待人工处理"
        job.message = reason or (
            f"遇到需要人工确认的页面（{job.page_label}）：{entry.get('title') or page.url}"
        )
        job.updated_at = _now()
        self._shot(page, job.account_id)
        job.resume_event.clear()
        deadline = time.time() + max(30, int(timeout_seconds))
        resolved = False
        while time.time() < deadline:
            if not self._alive(page):
                break
            if job.resume_event.is_set():
                resolved = True
                break
            # 管理员在实时画面里处理完，页面指纹变了就说明可以继续了。
            if self._book.key(page) != key:
                resolved = True
                break
            time.sleep(1.0)
        job.paused = False
        job.need_code = False
        job.updated_at = _now()
        if not resolved:
            raise RuntimeError(f"{job.message}（等待人工处理超时）")
        job.step = "检测到页面已处理，继续"
        job.message = ""

    def _gate_page(self, page: Any, job: Job, allowed: set[str]) -> str:
        """认识的页面直接放行；陌生页 / 验证页 / 上次人工处理过的页面，交给管理员。"""
        key, entry, is_new, kind = self._observe_page(page, job)
        if kind in allowed:
            return kind
        # 页面还没加载出来（空白页）时不打扰管理员。
        if not entry.get("title") and not entry.get("buttons"):
            return kind
        if kind == "challenge":
            self._wait_for_admin(page, job, "页面出现验证码 / 安全验证，请在实时画面手动完成")
        elif entry.get("needs_human"):
            self._wait_for_admin(page, job, f"「{job.page_label}」上次由人工处理，请在实时画面继续")
        elif is_new:
            self._wait_for_admin(
                page, job, f"遇到未记录的页面（{job.page_label}），请在实时画面处理后再继续"
            )
        return self._page_kind(page)

    # ---------- 登录 ----------

    def login_worker(self, job: Job) -> dict[str, Any]:
        creds = getattr(job, "_creds", None) or {}
        job_site = creds.get("site") or self._site_key
        phone = creds.get("phone") or ""
        password = creds.get("password") or ""

        playwright = self._launch()
        context = None
        try:
            context = self._context(playwright, job.account_id)
            page = context.pages[0] if context.pages else context.new_page()
            job.step = "打开卖家中心"
            job.progress = 10
            page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=60_000)
            self._safe_wait(page, 2500)
            self._dismiss_popups(page)

            # 未登录时先出现「中国地区 → 商家中心」入口，点它会新开登录页。
            login_page = page
            if page.locator("#usernameId").count() == 0:
                goto_entry = page.locator('[class*="authentication_goto"]')
                if goto_entry.count():
                    job.step = "进入登录页"
                    job.progress = 20
                    before = len(context.pages)
                    goto_entry.first.evaluate("el => el.click()")
                    for _ in range(20):
                        if len(context.pages) > before:
                            break
                        self._safe_wait(page, 500)
                    if len(context.pages) > before:
                        login_page = context.pages[-1]
                        try:
                            login_page.wait_for_load_state("domcontentloaded", timeout=30_000)
                        except Exception:  # noqa: BLE001
                            pass
                        login_page.wait_for_timeout(2000)
                    if login_page is not page:
                        # 登录页是新标签；原来那页可能已经没用了，后续统一看新页。
                        page = self._pick_page(context, page)
                    self._safe_wait(login_page, 3000)

            if login_page.locator("#usernameId").count():
                job.step = "填写账号密码"
                job.progress = 35
                if not phone or not password:
                    return {"ok": False, "message": "该账号没有保存密码，请先补充"}
                # 默认可能是扫码登录，先切到手机号登录页签（没有就跳过）。
                try:
                    tab = login_page.locator('[class*="tabItem"]', has_text="手机号登录")
                    if tab.count():
                        tab.first.click(timeout=3000)
                        self._safe_wait(login_page, 1200)
                except Exception:  # noqa: BLE001
                    pass
                login_page.fill("#usernameId", phone)
                login_page.fill("#passwordId", password)
                # 协议框的真实 input 被隐藏，点它的方框才生效；不行就改原生 checked。
                try:
                    login_page.locator('[class*="CBX_square"]').first.click(
                        force=True, timeout=3000
                    )
                except Exception:  # noqa: BLE001
                    try:
                        login_page.locator("input[type=checkbox]").first.evaluate(
                            """el => {
                                const desc = Object.getOwnPropertyDescriptor(
                                    HTMLInputElement.prototype, 'checked');
                                if (desc && desc.set) desc.set.call(el, true);
                                else el.checked = true;
                                el.dispatchEvent(new Event('input', {bubbles: true}));
                                el.dispatchEvent(new Event('change', {bubbles: true}));
                            }"""
                        )
                    except Exception:  # noqa: BLE001
                        pass
                job.step = "提交登录"
                job.progress = 45
                login_page.locator(
                    "button:has-text('登录'), button:has-text('授权登录')"
                ).first.click()
                self._safe_wait(login_page, 4000)

            # 已登录但还没授权站点的账号，会停在「即将前往 Seller Central…确认授权并前往」。
            # 这个页面本身就带店铺名，点掉授权就等于登录成功，不必再回入口页判断。
            job.step = "检查站点授权"
            job.progress = 50
            authorize_shop = self._read_authorize_shop(login_page) or self._read_authorize_shop(page)
            confirmed = self._confirm_authorize(login_page)
            if not confirmed and page is not login_page:
                confirmed = self._confirm_authorize(page)
            if confirmed:
                job.step = "确认站点授权"
                self._safe_wait(login_page, 5000)
                job.progress = 100
                self._shot(page if self._alive(page) else login_page, job.account_id)
                return {
                    "ok": True,
                    "message": "登录成功（站点已授权）",
                    "shop_name": authorize_shop,
                    "site": job_site,
                }

            job.step = "检查验证码"
            job.progress = 55
            if self._needs_code(login_page):
                # Temu 的短信不会自动发：必须先点「获取验证码」，否则用户永远收不到。
                job.step = "发送验证码"
                sent = self._request_code(login_page)
                if not sent:
                    self._dump_code_popup(login_page, job.account_id)
                self._shot(login_page, job.account_id)
                job.need_code = True
                job.step = "等待验证码"
                job.message = (
                    "短信已发送，请输入 6 位验证码"
                    if sent
                    else "请先在弹窗点「获取验证码」，再输入验证码"
                )
                job.code_event.clear()
                # 等待期间同时盯着登录页：用户也可能在实时画面里自己填完验证码，
                # 那时弹窗会关闭或跳走，这里要能接上而不是干等 5 分钟。
                manual_done = False
                deadline = time.time() + 300
                while time.time() < deadline:
                    if job.code_event.wait(timeout=2):
                        break
                    if not self._alive(login_page):
                        manual_done = True
                        break
                    try:
                        current = login_page.url
                    except Exception:  # noqa: BLE001
                        manual_done = True
                        break
                    if "seller-login" not in current and "/auth/" not in current:
                        manual_done = True
                        break
                job.need_code = False

                if manual_done:
                    job.step = "检测到已手动完成登录"
                elif not job.code_event.is_set():
                    return {"ok": False, "message": "等待验证码超时"}
                else:
                    code = job.code_value
                    if not code:
                        return {"ok": False, "message": "验证码为空"}
                    job.step = "提交验证码"
                    self._fill_code(login_page, code)
                    self._safe_wait(login_page, 4500)

            self._dismiss_popups(login_page)
            if self._needs_code(login_page):
                self._shot(login_page, job.account_id)
                return {"ok": False, "message": "验证码未通过，请重试"}

            # 验证码之后再确认一次授权（有的账号是登录 → 验证码 → 授权）。
            confirmed = self._confirm_authorize(login_page)
            if not confirmed and page is not login_page:
                confirmed = self._confirm_authorize(page)
            if confirmed:
                job.step = "确认站点授权"
                self._safe_wait(login_page, 5000)

            # 登录成功后回到卖家中心入口页，那里才看得到店铺名与站点入口。
            job.step = "返回卖家中心"
            job.progress = 75
            if not self._alive(page):
                page = self._pick_page(context, login_page)
            try:
                page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=60_000)
            except Exception:  # noqa: BLE001 - 标签被关掉时换一个页面
                page = self._pick_page(context, login_page)
                try:
                    page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=60_000)
                except Exception:  # noqa: BLE001
                    pass
            self._safe_wait(page, 4000)
            self._dismiss_popups(page)

            if self._needs_code(page):
                self._shot(page, job.account_id)
                return {"ok": False, "message": "登录后仍要求验证码，请重试"}

            # 判成功：入口页只剩「商家中心」链接说明还是未登录态。
            still_guest = (
                page.locator('[class*="authentication_goto"]').count() > 0
                and page.locator('[class*="site-main_us"], [class*="mallName"]').count() == 0
            )
            if still_guest:
                self._shot(page, job.account_id)
                return {
                    "ok": False,
                    "message": self._read_login_error(login_page) or "登录未通过，请检查账号密码",
                }

            job.step = "读取店铺"
            job.progress = 90
            shop = self._read_shop_name(page)
            self._shot(page, job.account_id)
            return {"ok": True, "message": "登录成功", "shop_name": shop, "site": job_site}
        finally:
            if context is not None:
                context.close()
            playwright.stop()

    @staticmethod
    def _needs_code(page: Any) -> bool:
        for selector in (
            "input[placeholder*='验证码']",
            "input[placeholder*='校验码']",
            "input[name*='code']",
            "input[id*='code']",
        ):
            try:
                if page.locator(selector).count():
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    @staticmethod
    def _read_authorize_shop(page: Any) -> str:
        """从「即将前往 Seller Central 的「XXX」店铺」里取店铺名。"""
        try:
            text = page.evaluate("() => document.body.innerText || ''")
        except Exception:  # noqa: BLE001
            return ""
        match = re.search(r"「\s*([^」]{1,60}?)\s*」", text or "")
        return match.group(1).strip() if match else ""

    def _confirm_authorize(self, page: Any, timeout_seconds: int = 12) -> bool:
        """点掉「确认授权并前往」：账号已登录、只差站点授权时会卡在这里。

        这个页面比登录表单渲染得慢，必须等按钮真的出现再点，否则会误判成「未登录」。
        """
        deadline = time.time() + max(3, timeout_seconds)
        while time.time() < deadline:
            if self._is_paused():
                return False
            try:
                if page.is_closed():
                    return False
            except Exception:  # noqa: BLE001
                return False
            tick_consent(page)
            for selector in (
                "button:has-text('确认授权并前往')",
                "button:has-text('确认授权')",
            ):
                try:
                    locator = page.locator(selector).first
                    if locator.count() and locator.is_visible():
                        locator.click(timeout=4000)
                        return True
                except Exception:  # noqa: BLE001
                    continue
            try:
                node = page.locator("text=确认授权并前往").last
                if node.count():
                    box = node.bounding_box()
                    if box:
                        page.mouse.click(
                            box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                        )
                        return True
            except Exception:  # noqa: BLE001
                pass
            try:
                page.wait_for_timeout(1000)
            except Exception:  # noqa: BLE001
                return False
        return False

    @staticmethod
    def _request_code(page: Any) -> bool:
        """点「获取验证码」触发平台下发短信。

        这个按钮是普通节点不是 <button>，用 text 定位后按坐标点击最稳；
        点完必须校验文案是否变成倒计时，否则会误报「已发送」。
        """
        target = None
        for selector in ("text=获取验证码", "text=发送验证码", "text=重新获取"):
            try:
                candidate = page.locator(selector).last
                if candidate.count() and candidate.is_visible():
                    target = candidate
                    break
            except Exception:  # noqa: BLE001
                continue
        if target is None:
            return False
        try:
            box = target.bounding_box()
        except Exception:  # noqa: BLE001
            box = None
        try:
            if box and box.get("width") and box.get("height"):
                page.mouse.click(
                    box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                )
            else:
                target.click(force=True, timeout=3000)
        except Exception:  # noqa: BLE001
            try:
                target.click(force=True, timeout=3000)
            except Exception:  # noqa: BLE001
                return False
        page.wait_for_timeout(2500)

        try:
            text = page.evaluate("() => document.body.innerText || ''")
        except Exception:  # noqa: BLE001
            text = ""
        # 发送成功后按钮会变成「60s 后重新获取」「重新获取(xx秒)」之类。
        return any(
            marker in text
            for marker in ("重新获取", "秒后", "秒重新", "已发送", "发送成功")
        )

    def _dump_code_popup(self, page: Any, account_id: str) -> None:
        """把验证码弹窗的 HTML 存下来，便于按真实结构补选择器。"""
        try:
            html = page.evaluate(
                """() => {
                    const nodes = [...document.querySelectorAll('*')]
                        .filter(e => (e.innerText || '').trim() === '获取验证码');
                    const el = nodes[nodes.length - 1];
                    if (!el) return '';
                    const parts = [el.outerHTML];
                    let parent = el.parentElement;
                    for (let i = 0; i < 3 && parent; i += 1) {
                        parts.push('<!-- LEVEL ' + (i + 1) + ' -->');
                        parts.push(parent.outerHTML.slice(0, 4000));
                        parent = parent.parentElement;
                    }
                    return parts.join('\\n');
                }"""
            )
        except Exception:  # noqa: BLE001
            return
        if not html:
            return
        try:
            path = self._shots / f"{account_id}.code.html"
            path.write_text(html, encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _fill_code(page: Any, code: str) -> None:
        for selector in (
            "input[placeholder*='验证码']",
            "input[placeholder*='校验码']",
            "input[name*='code']",
            "input[id*='code']",
        ):
            locator = page.locator(selector).first
            try:
                if locator.count():
                    locator.fill(code)
                    # 弹窗里的确认按钮文案是「继续登录」，不是「登录」。
                    for button in (
                        "button:has-text('继续登录')",
                        "button:has-text('确认')",
                        "button:has-text('确定')",
                        "button:has-text('登录')",
                    ):
                        target = page.locator(button).first
                        if target.count():
                            target.click()
                            return
                    return
            except Exception:  # noqa: BLE001
                continue

    @staticmethod
    def _enter_site(page: Any, site_key: str) -> None:
        label = SITE_LABELS.get(site_key, "美国")
        try:
            entry = page.locator(f".site-main_{site_key}").first
            if entry.count():
                entry.click()
                return
        except Exception:  # noqa: BLE001
            pass
        for selector in (
            f"div:has-text('{label}')",
            f"span:has-text('{label}')",
        ):
            try:
                locator = page.locator(selector).last
                if locator.count():
                    locator.click()
                    return
            except Exception:  # noqa: BLE001
                continue

    @staticmethod
    def _read_login_error(page: Any) -> str:
        """从登录页读一条人能看懂的错误提示，读不到就返回空串。"""
        for selector in ('[class*="errorText"]', '[class*="error"]'):
            try:
                locator = page.locator(selector).first
                if locator.count():
                    text = (locator.inner_text() or "").strip()
                    if text and len(text) <= 80:
                        return text
            except Exception:  # noqa: BLE001
                continue
        try:
            body = page.evaluate("() => document.body.innerText || ''")
        except Exception:  # noqa: BLE001
            return ""
        for keyword in (
            "密码错误",
            "账号或密码",
            "不存在",
            "频繁",
            "锁定",
            "账号异常",
            "无法登录",
            "验证码错误",
            "请完成验证",
            "不同意",
        ):
            index = body.find(keyword)
            if index >= 0:
                return body[max(0, index - 12) : index + 28].replace("\n", " ").strip()
        return ""

    @staticmethod
    def _read_shop_name(page: Any) -> str:
        for selector in (
            '[class*="mallName"] span',
            '[class*="mallName"]',
            "span[class*='elli_']",
        ):
            try:
                locator = page.locator(selector).first
                if locator.count():
                    text = (locator.inner_text() or "").strip()
                    if text and len(text) <= 40:
                        return text
            except Exception:  # noqa: BLE001
                continue
        return ""

    # ---------- 订单采集 ----------

    def capture_worker(self, job: Job) -> dict[str, Any]:
        workflow_session = getattr(job, "_workflow_session", None)
        owns_context = workflow_session is None
        playwright = self._launch() if owns_context else None
        context = None
        try:
            context = workflow_session["context"] if workflow_session else self._context(playwright, job.account_id)
            page = context.pages[0] if context.pages else context.new_page()
            job.step = "打开订单列表"
            job.progress = 10
            page.goto(ORDERS_URL, wait_until="domcontentloaded", timeout=60_000)
            self._dismiss_popups(page)
            page.add_script_tag(path=str(self._order_script))

            # SPA 首屏会先显示「加载中」，此时既没表格也没登录框，不能直接当成功。
            # 服务重启后的第一个浏览器是冷启动，页面偶尔要 30-40 秒才出列表，等久一点。
            job.step = "等待订单页就绪"
            state = self._wait_order_state(page, timeout_seconds=45)
            if state == "authorize":
                # 站点还没授权过：点掉授权后页面会自己跳到订单列表。
                job.step = "确认站点授权"
                self._confirm_authorize(page, timeout_seconds=8)
                self._safe_wait(page, 6000)
                state = self._wait_order_state(page, timeout_seconds=45)
            if state == "timeout":
                # 超时不一定真的失败：截图前再等一轮，避免冷启动把整轮判死。
                job.step = "订单页仍在加载，再等 20 秒"
                state = self._wait_order_state(page, timeout_seconds=20)
            if state == "login":
                self._shot(page, job.account_id)
                return {
                    "ok": False,
                    "need_login": True,
                    "message": "登录态已失效，请先重新登录",
                }
            if state == "timeout":
                self._shot(page, job.account_id)
                return {
                    "ok": False,
                    "message": "订单页加载超时，请检查网络或登录态",
                }
            collected: list[dict[str, Any]] = []
            seen: set[tuple[str, str, str]] = set()
            pages_done = 0
            truncated_tabs: list[str] = []
            # 「全部」标签在部分账号下只给出一部分，必须按状态逐个标签采再合并。
            for label in ("全部", "待发货", "已发货", "已签收", "已取消"):
                tab = page.locator(f"text={label}").last
                if not tab.count():
                    continue
                job.step = f"切换到「{label}」"
                try:
                    tab.click(timeout=5000)
                except Exception:  # noqa: BLE001
                    continue
                self._safe_wait(page, 4500)
                for index in range(self._max_pages):
                    job.step = f"{label} · 第 {index + 1} 页"
                    job.progress = min(88, 15 + pages_done * 6)
                    self._safe_wait(page, 1200)
                    chunk = page.evaluate(
                        "() => window.WorkbenchTemuOrderProfit.extractFromDom(document)"
                    )
                    items = (chunk or {}).get("table_items") or (chunk or {}).get("text_items") or []
                    for item in items:
                        key = (
                            str(item.get("parent_order_sn") or ""),
                            str(item.get("sub_order_sn") or ""),
                            str(item.get("sku_id") or ""),
                        )
                        if key in seen:
                            continue
                        seen.add(key)
                        collected.append(item)
                    pages_done += 1
                    if not self._next_page(page):
                        break
                else:
                    # 跑满页数上限还没到底，说明这个状态的订单可能被截断。
                    truncated_tabs.append(label)

            # 列表只有订单号/SKU/数量，申报价和各项回款只在详情页里。
            parents: list[str] = []
            for row in collected:
                po = str(row.get("parent_order_sn") or "")
                if po and po not in parents:
                    parents.append(po)
            details: dict[str, dict[str, Any]] = {}
            matched = 0
            if parents and self._max_details:
                job.step = f"采集详情（{len(parents)} 个父订单）"
                details = self._collect_details(
                    page, parents[: self._max_details], job, 60, 28
                )
                matched = self._merge_details(collected, details)

            job.step = "汇总订单"
            job.progress = 92
            summary = page.evaluate(
                """items => {
                    const lib = window.WorkbenchTemuOrderProfit;
                    return lib.summarize(items);
                }""",
                collected,
            )
            self._shot(page, job.account_id)
            pending = sum(1 for po in parents if po not in details)
            notes = []
            if pending:
                notes.append(f"详情待补齐 {pending} 单（下轮继续）")
            if truncated_tabs:
                notes.append(f"「{'、'.join(truncated_tabs)}」达到页数上限，可能未采完")
            message = f"采集完成，共 {pages_done} 页 / {len(collected)} 条（详情匹配 {matched}）"
            if notes:
                message += "；" + "；".join(notes)
            return {
                "ok": True,
                "message": message,
                "pages": pages_done,
                "items": collected,
                "details": details,
                "pending_details": pending,
                "summary": summary or {},
            }
        finally:
            if owns_context:
                if context is not None:
                    context.close()
                if playwright is not None:
                    playwright.stop()

    @staticmethod
    def _wait_order_state(page: Any, timeout_seconds: int = 25) -> str:
        """等订单页出结果：'login' 未登录 / 'authorize' 待授权 / 'ready' 可解析 / 'timeout'。"""
        deadline = time.time() + max(5, timeout_seconds)
        while time.time() < deadline:
            if page.locator("#usernameId").count():
                return "login"
            if "/auth/" in page.url or "seller-login" in page.url:
                return "login"
            try:
                if page.locator('[class*="authentication_goto"]').count():
                    return "login"
            except Exception:  # noqa: BLE001
                pass
            try:
                text = page.evaluate("() => document.body.innerText || ''")
            except Exception:  # noqa: BLE001
                text = ""
            if text and "确认授权并前往" in text:
                return "authorize"
            if text and "加载中" not in text:
                has_table = page.locator(
                    "table, [role='table'], [class*='table'], [class*='Table']"
                ).count()
                if has_table or "订单号" in text or "Parent order" in text:
                    return "ready"
            page.wait_for_timeout(1000)
        return "timeout"

    # ---------- 订单详情 ----------

    def _collect_details(
        self,
        page: Any,
        parents: list[str],
        job: "Job",
        progress_base: int,
        progress_span: int,
    ) -> dict[str, dict[str, Any]]:
        """补齐申报价、各项回款、物流与状态。

        详情页有金额信息，但一次只展示一个子单；父单下多个子单时，
        金额字段不做跨行复制（否则会重复计账），只补父单级字段。
        """
        details: dict[str, dict[str, Any]] = {}
        cached: dict[str, dict[str, Any]] = {}
        if self._store is not None:
            try:
                cached = self._store.get_order_details(parents)
            except Exception:  # noqa: BLE001
                cached = {}
        for po, payload in cached.items():
            details[po] = payload
        todo = [po for po in parents if po not in details]
        total = len(todo)
        if not total:
            job.progress = progress_base + progress_span
            return details
        deadline = time.time() + self._detail_budget
        login_lost = False
        if self._detail_http:
            remaining, login_lost = self._details_via_fetch(
                page, todo, details, job, progress_base, progress_span, deadline
            )
            # 并发请求偶尔会被站点限流，只拿到空壳页面；隔 2 秒用更小并发重试一次，
            # 仍拿不到的才逐页兜底，避免大批订单落入慢路径。
            if remaining and not login_lost and time.time() < deadline:
                job.step = f"批量重试 {len(remaining)} 单"
                time.sleep(2)
                remaining, login_lost = self._details_via_fetch(
                    page,
                    remaining,
                    details,
                    job,
                    progress_base + int(progress_span * 0.85),
                    int(progress_span * 0.05),
                    deadline,
                    batch_size=max(4, self._detail_batch // 3),
                )
        else:
            remaining = todo
        # 并发请求拿不到的少数订单（页面结构特殊/请求失败）再用浏览器逐页兜底。
        if remaining and not login_lost:
            self._details_via_browser(
                page, remaining, details, job,
                progress_base + int(progress_span * 0.85),
                int(progress_span * 0.15),
                deadline,
            )
        return details

    def _save_details(self, fresh: dict[str, dict[str, Any]]) -> None:
        if self._store is None or not fresh:
            return
        try:
            self._store.save_order_details(fresh)
            fresh.clear()
        except Exception:  # noqa: BLE001
            pass

    def _details_via_fetch(
        self,
        page: Any,
        todo: list[str],
        details: dict[str, dict[str, Any]],
        job: "Job",
        progress_base: int,
        progress_span: int,
        deadline: float,
        batch_size: int | None = None,
    ) -> tuple[list[str], bool]:
        """在订单页内并发 fetch 详情页 HTML，按批解析；返回仍需逐页采的订单。"""
        total = len(todo)
        fresh: dict[str, dict[str, Any]] = {}
        done = 0
        login_lost = False
        batch = max(1, int(batch_size or self._detail_batch))
        for start in range(0, total, batch):
            if time.time() > deadline:
                job.step = f"详情待补齐（剩 {total - done} 个，下轮继续）"
                break
            chunk = todo[start:start + batch]
            job.step = f"详情 {start + 1}-{start + len(chunk)}/{total}（并发）"
            job.progress = progress_base + int(progress_span * 0.85 * done / max(1, total))
            payload = [{"po": po, "url": DETAIL_URL.format(po=po)} for po in chunk]
            try:
                results = page.evaluate(DETAIL_FETCH_JS, payload) or []
            except Exception:  # noqa: BLE001 - 页面被关/超时，剩下的交给逐页兜底
                job.step = "批量取详情中断，改用逐页采集"
                break
            got = 0
            login_flags = 0
            empty_shells = 0
            for result in results:
                po = str(result.get("po") or "")
                if result.get("login"):
                    login_flags += 1
                item = result.get("item")
                if not item or not po:
                    # 没有 rawData 说明拿到的是空壳页（多半是并发过猛被限流），重试能救回。
                    if result.get("raw") is False:
                        empty_shells += 1
                    continue
                if not item.get("image_url") and result.get("image"):
                    item["image_url"] = result["image"]
                if result.get("tracking") and not item.get("tracking_no"):
                    item["tracking_no"] = result["tracking"]
                if result.get("declared") is not None:
                    item["declared_price"] = result["declared"]
                    item["declared_currency"] = "CNY"
                details[po] = item
                fresh[po] = item
                got += 1
            done += len(chunk)
            if empty_shells:
                job.step = (
                    f"详情 {start + 1}-{start + len(chunk)}/{total}"
                    f"（并发，空壳 {empty_shells}）"
                )
            if login_flags >= max(2, len(chunk) // 2):
                # 采集中途登录态被踢：保留已采到的，下一轮继续补。
                job.step = "登录态失效，停止本次详情采集（已采部分已保留）"
                login_lost = True
                self._save_details(fresh)
                break
            if len(fresh) >= 20:
                self._save_details(fresh)
            if got == 0 and start == 0:
                # 一条都解析不出来，说明这条路径在这台机器上不可用，直接退回逐页。
                job.step = "批量取详情无结果，改用逐页采集"
                break
        self._save_details(fresh)
        return [po for po in todo if po not in details], login_lost

    def _details_via_browser(
        self,
        page: Any,
        todo: list[str],
        details: dict[str, dict[str, Any]],
        job: "Job",
        progress_base: int,
        progress_span: int,
        deadline: float,
    ) -> None:
        """逐页打开详情（兜底路径）：慢，但页面结构变化时仍能采到。"""
        total = len(todo)
        fresh: dict[str, dict[str, Any]] = {}
        durations: list[float] = []
        nav_failures = 0
        for index, po in enumerate(todo):
            if time.time() > deadline:
                job.step = f"详情待补齐（剩 {total - index} 个，下轮继续）"
                break
            started = time.time()
            job.step = f"详情 {index + 1}/{total}"
            job.progress = progress_base + int(progress_span * index / max(1, total))
            try:
                page.goto(DETAIL_URL.format(po=po), wait_until="domcontentloaded", timeout=45_000)
            except Exception:  # noqa: BLE001
                nav_failures += 1
                if nav_failures >= 5:
                    job.step = "详情页连续打开失败，停止本次详情采集（可能触发风控）"
                    break
                continue
            self._dismiss_popups(page)
            if self._is_login_page(page):
                job.step = "登录态失效，停止本次详情采集（已采部分已保留）"
                break
            if not self._wait_detail_ready(page, timeout_seconds=10):
                # 没有金额区块（已取消/未结算）也照常解析，状态、时间、商品图仍能拿到。
                pass
            try:
                page.add_script_tag(path=str(self._order_script))
                item = page.evaluate(
                    """() => {
                        const lib = window.WorkbenchTemuOrderProfit;
                        if (!lib) return null;
                        const dom = lib.extractFromDom(document);
                        return dom.detail_item
                            || lib.parseOrderDetailText(document.body.innerText || '');
                    }"""
                )
            except Exception:  # noqa: BLE001
                item = None
            if item:
                details[po] = item
                fresh[po] = item
                image = self._detail_image(page)
                if image:
                    item["image_url"] = image
            time.sleep(random.uniform(0.3, 0.8))
            durations.append(time.time() - started)
            if durations:
                average = sum(durations) / len(durations)
                job.step = f"详情 {index + 1}/{total} · 均 {average:.1f}s"
            # 边采边存，避免中途被登录态踢掉后白跑。
            if len(fresh) >= 20:
                self._save_details(fresh)
        self._save_details(fresh)

    @staticmethod
    def _detail_image(page: Any) -> str:
        """详情页里挑最大的一张商品图（img.kwcdn.com），拿不到就返回空。"""
        try:
            return str(
                page.evaluate(
                    """() => {
                        let best = '';
                        let bestArea = 0;
                        let fallback = '';
                        for (const el of Array.from(document.images || [])) {
                            const src = el.currentSrc || el.src || '';
                            if (!src.includes('kwcdn.com')) continue;
                            if (!fallback) fallback = src;
                            const w = el.naturalWidth || Number(el.getAttribute('width')) || 0;
                            const h = el.naturalHeight || Number(el.getAttribute('height')) || 0;
                            if (w && h && w * h > bestArea) {
                                bestArea = w * h;
                                best = src;
                            }
                        }
                        return best || fallback;
                    }"""
                )
                or ""
            )
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _is_login_page(page: Any) -> bool:
        """当前页面是不是被踢回登录/授权入口。"""
        try:
            if page.locator("#usernameId").count():
                return True
        except Exception:  # noqa: BLE001
            pass
        url = str(getattr(page, "url", "") or "")
        return "/auth/" in url or "seller-login" in url or "/login" in url

    @staticmethod
    def _wait_detail_ready(page: Any, timeout_seconds: int = 10) -> bool:
        """等详情页出内容。金额区块不是所有订单都有（已取消/未结算），所以只等页面渲染完。"""
        deadline = time.time() + max(3, timeout_seconds)
        while time.time() < deadline:
            try:
                text = page.evaluate("() => document.body.innerText || ''")
            except Exception:  # noqa: BLE001
                return False
            if "申报价格" in text or "销售回款" in text:
                return True
            if "加载中" not in text and ("订单号" in text or "Parent order" in text):
                return True
            try:
                page.wait_for_timeout(600)
            except Exception:  # noqa: BLE001
                return False
        return False

    def _merge_details(
        self,
        rows: list[dict[str, Any]],
        details: dict[str, dict[str, Any]],
    ) -> int:
        """把详情字段并回列表行，返回成功匹配的父单数。"""
        counts: dict[str, int] = {}
        for row in rows:
            po = str(row.get("parent_order_sn") or "")
            counts[po] = counts.get(po, 0) + 1
        matched = 0
        for row in rows:
            po = str(row.get("parent_order_sn") or "")
            detail = details.get(po)
            if not detail or detail.get("error"):
                continue
            matched += 1
            # 详情按父单缓存；只有 SKU 一致时才能把商品级 SPU 回填到该行。
            if (not row.get("spu_id") and detail.get("spu_id")
                    and str(row.get("sku_id") or "").strip()
                    and str(row.get("sku_id")).strip() == str(detail.get("sku_id") or "").strip()):
                row["spu_id"] = str(detail["spu_id"]).strip()
            for key in DETAIL_BASE_FIELDS:
                if row.get(key) in (None, "") and detail.get(key) not in (None, ""):
                    row[key] = detail[key]
            row["tracking_no"] = self._clean_tracking(row.get("tracking_no"))
            # 父单只有一个子单时金额归它；多子单时详情只反映其中一个，不能复制。
            if counts.get(po, 0) <= 1:
                for key in DETAIL_AMOUNT_FIELDS:
                    if detail.get(key) not in (None, ""):
                        row[key] = detail[key]
        return matched

    @staticmethod
    def _clean_tracking(value: Any) -> str:
        """平台对不满 5 天的订单隐藏单号（页面显示「***」或只有承运商名），这些不算单号。"""
        text = str(value or "").strip().strip("，,;； ")
        if not text or "***" in text:
            return ""
        if not any(char.isdigit() for char in text):
            return ""
        return text

    @staticmethod
    def _next_page(page: Any) -> bool:
        for selector in (
            "li[data-testid='beast-core-pagination-next']",
            "li.PGT_next_123",
            "li[class*='PGT_next']",
        ):
            try:
                locator = page.locator(selector).first
                if not locator.count():
                    continue
                classes = locator.get_attribute("class") or ""
                if "disabled" in classes:
                    return False
                locator.click()
                page.wait_for_timeout(2500)
                return True
            except Exception:  # noqa: BLE001
                continue
        return False

    # ---------- 调度 ----------

    def run_login(
        self,
        account_id: str,
        credentials: dict[str, str],
        on_done: Callable[[str, dict[str, Any]], None],
    ) -> dict[str, Any]:
        def worker(job: Job) -> dict[str, Any]:
            job._creds = credentials  # type: ignore[attr-defined]
            return self.login_worker(job)

        job = self.start(account_id, "login", worker, on_done)
        return job.snapshot()

    # ---------- 登录态探活 ----------

    def probe_worker(self, job: Job) -> dict[str, Any]:
        """只打开卖家中心判断登录态，不采集，用于失效预警与保活。"""
        playwright = self._launch()
        context = None
        try:
            context = self._context(playwright, job.account_id)
            page = context.pages[0] if context.pages else context.new_page()
            job.step = "检查登录态"
            job.progress = 40
            page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=45_000)
            self._safe_wait(page, 3500)
            self._dismiss_popups(page)

            shop = ""
            if page.locator('[class*="authentication_goto"]').count():
                state = "guest"
            elif (
                page.locator('[class*="mallName"]').count()
                or page.locator('[class*="site-main_us"], [class*="site-main_glo"]').count()
            ):
                state = "ready"
                shop = self._read_shop_name(page)
            elif self._read_authorize_shop(page):
                # 账号密码有效、只差站点授权：采集时会自动点掉，算可用。
                state = "authorize"
                shop = self._read_authorize_shop(page)
            else:
                state = "guest"

            job.progress = 100
            return {
                "ok": True,
                "action": "probe",
                "state": state,
                "shop_name": shop,
                "message": {
                    "ready": "登录有效",
                    "authorize": "登录有效（待站点授权）",
                    "guest": "登录已失效，需要重新登录",
                }[state],
            }
        finally:
            if context is not None:
                context.close()
            playwright.stop()

    def run_probe(
        self,
        account_id: str,
        on_done: Callable[[str, dict[str, Any]], None],
    ) -> dict[str, Any]:
        job = self.start(account_id, "probe", self.probe_worker, on_done)
        return job.snapshot()

    # ---------- 结算资金（复刻导出） ----------

    def _login_page_of(self, page: Any) -> Any:
        """登录页有时开在新标签里，返回真正带登录表单的那个页面。"""
        candidates = [page]
        try:
            candidates += [item for item in page.context.pages if item is not page]
        except Exception:  # noqa: BLE001
            pass
        for candidate in candidates:
            try:
                if candidate.locator("input[type=password]").count():
                    return candidate
            except Exception:  # noqa: BLE001
                continue
        return page

    def _has_password_field(self, page: Any) -> bool:
        """当前标签或其它标签里有没有真实存在的密码框。"""
        try:
            pages = [page] + [item for item in page.context.pages if item is not page]
        except Exception:  # noqa: BLE001
            pages = [page]
        for item in pages:
            try:
                if item.locator("input[type=password]").count():
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _click_login_tab(self, page: Any) -> bool:
        """登录页默认停在扫码/邮箱页签，切到「手机号登录」才有账号密码框。"""
        for name in ("手机号登录", "账号登录", "密码登录"):
            try:
                tab = page.get_by_text(name, exact=True).first
                if tab.count():
                    tab.click(timeout=4000)
                    self._safe_wait(page, 1500)
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _fill_login_fields(
        self, page: Any, phone: str, password: str, wait_seconds: int = 20
    ) -> bool:
        """兼容两套登录页：Temu 卖家中心(#usernameId/#passwordId) 与跨境卖家(手机号/密码)。"""
        try:
            page.wait_for_selector(
                "input[type=password]:visible", timeout=int(wait_seconds * 1000)
            )
        except Exception:  # noqa: BLE001 - 页面可能还在渲染，继续尝试
            pass

        username = None
        for selector in (
            "#usernameId",
            "input[type=tel]",
            "input[inputmode=tel]",
            "input[placeholder*='手机']",
            "input[name*='phone' i]",
        ):
            try:
                locator = page.locator(selector).first
                if locator.count() and locator.is_visible():
                    username = locator
                    break
            except Exception:  # noqa: BLE001 - 换下一个候选
                continue
        if username is None:
            # 兜底只在确认有密码框时取第一个可见文本框，否则会把手机号填进页头搜索框。
            try:
                if not page.locator("input[type=password]:visible").count():
                    return False
                inputs = page.locator("input:visible")
                for index in range(min(inputs.count(), 8)):
                    element = inputs.nth(index)
                    kind = str(element.get_attribute("type") or "text").lower()
                    if kind in ("text", "tel", "number", "email"):
                        username = element
                        break
            except Exception:  # noqa: BLE001
                return False
        if username is None:
            return False

        try:
            username.fill(phone, timeout=6000)
            page.locator("input[type=password]:visible").first.fill(password, timeout=6000)
        except Exception:  # noqa: BLE001
            return False

        # 协议框的真实 input 被隐藏，直接改原生 checked 再派发事件。
        try:
            page.evaluate(
                """() => {
                    document.querySelectorAll('input[type=checkbox]').forEach((el) => {
                        if (el.checked) return;
                        const desc = Object.getOwnPropertyDescriptor(
                            HTMLInputElement.prototype, 'checked');
                        if (desc && desc.set) desc.set.call(el, true);
                        else el.checked = true;
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                    });
                }"""
            )
        except Exception:  # noqa: BLE001
            pass
        return True

    def _click_login_button(self, page: Any) -> bool:
        pattern = re.compile("授权登录|登录|Sign\\s*in|Log\\s*in|Continue", re.I)
        buttons = page.locator("button:visible, [role=button]:visible")
        for index in range(min(buttons.count(), 20)):
            element = buttons.nth(index)
            try:
                text = str(element.inner_text(timeout=1000) or "").strip()
            except Exception:  # noqa: BLE001
                continue
            if not pattern.search(text):
                continue
            try:
                element.click(timeout=4000)
                return True
            except Exception:  # noqa: BLE001
                continue
        try:
            page.locator("button:has-text('登录'), button:has-text('授权登录')").first.click(
                timeout=5000
            )
            return True
        except Exception:  # noqa: BLE001
            return False

    def _confirm_agree(self, page: Any) -> bool:
        """登录弹窗「是否阅读并同意《账号使用须知》《隐私政策》」点同意。"""
        for name in ("同意并登录", "同意并继续", "我同意"):
            try:
                button = page.get_by_text(name, exact=True).first
                if button.count():
                    button.click(timeout=4000)
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _login_with_saved_credentials(self, page: Any, job: Job, target: str = "") -> str:
        """在登录页用库里保存的手机号/密码自动填好并提交；返回错误信息，成功返回空串。"""
        self._settle_gate()
        account = self._store.get_account(job.account_id) if self._store else None
        creds = (self._store.get_credentials(job.account_id) if self._store else {}) or {}
        phone = str(creds.get("phone") or (account or {}).get("phone") or "")
        password = str(creds.get("password") or "")
        if not phone or not password:
            return "该账号没有保存手机号/密码，无法自动登录，请先在左侧补充"
        # 登录页国家码是单独的 +86 选择器，号码里带 86 前缀要去掉。
        digits = re.sub(r"\D", "", phone)
        if len(digits) == 13 and digits.startswith("86"):
            digits = digits[2:]
        if digits:
            phone = digits

        # 被踢回的大多是「站点选择」页或扫码登录页，都没有密码框；
        # 直接打开跨境卖家的登录页（登录后按 redirectUrl 回到原页面），再切到手机号页签。
        if not self._has_password_field(page):
            login_url = KJ_LOGIN_URL.format(quote(target or BILL_URL, safe=""))
            try:
                page.goto(login_url, wait_until="domcontentloaded", timeout=45_000)
            except Exception:  # noqa: BLE001
                pass
            self._safe_wait(page, 3000)
            self._dismiss_popups(page)

        job.step = "自动填写账号密码"
        job.progress = 14
        page = self._login_page_of(page)
        if not self._has_password_field(page):
            self._click_login_tab(page)
            page = self._login_page_of(page)
        if not self._fill_login_fields(page, phone, password):
            self._shot(page, job.account_id)
            return "登录页找不到账号/密码输入框，请在实时画面手动登录"

        job.step = "提交登录"
        job.progress = 20
        tick_consent(page)
        if not self._click_login_button(page):
            return "登录页找不到「登录」按钮"
        self._safe_wait(page, 5000)
        self._dismiss_popups(page)

        # 手机号登录会弹「是否阅读并同意《账号使用须知》《隐私政策》」；
        # 「同意并登录」只是关掉弹窗并勾上协议，要再点一次「登录」才真正提交。
        if self._confirm_agree(page):
            job.step = "同意账号使用须知"
            self._safe_wait(page, 1500)
            self._click_login_button(page)
            self._safe_wait(page, 6000)
            self._dismiss_popups(page)

        if self._confirm_authorize(page):
            job.step = "确认站点授权"
            self._safe_wait(page, 5000)

        if self._needs_code(page):
            # Temu 短信不会自动发，要先点「获取验证码」。
            job.step = "发送验证码"
            self._request_code(page)
            self._shot(page, job.account_id)
            job.need_code = True
            job.step = "等待验证码"
            job.message = "自动登录需要短信验证码，请在店铺卡片里填写"
            job.code_event.clear()
            manual_done = False
            deadline = time.time() + 300
            while time.time() < deadline:
                if job.code_event.wait(timeout=2):
                    break
                if not self._alive(page) or not self._needs_code(page):
                    manual_done = True
                    break
            job.need_code = False
            if manual_done:
                job.step = "检测到已手动完成登录"
            elif not job.code_event.is_set():
                return "等待短信验证码超时"
            elif job.code_value:
                job.step = "提交验证码"
                self._fill_code(page, job.code_value)
                self._safe_wait(page, 5000)

        self._dismiss_popups(page)
        if self._is_login_page(page):
            self._shot(page, job.account_id)
            reason = self._read_login_error(page) or "自动登录未通过"
            if job.action != "settlement":
                return f"{reason}，请检查账号密码"
            # 对账中心被风控（账号异常）或密码失效挡住时，交给实时画面人工登录，成功后自动接上。
            job.need_code = True
            job.step = "等待手动登录"
            job.message = f"{reason}，请在实时画面手动登录"
            deadline = time.time() + 600
            while time.time() < deadline:
                if not self._alive(page) or not self._is_login_page(page):
                    break
                if job.code_event.wait(timeout=2):
                    job.code_event.clear()
            job.need_code = False
            if self._alive(page) and not self._is_login_page(page):
                job.step = "检测到已手动完成登录"
            else:
                return f"{reason}，请在实时画面手动登录后重试"
        return ""

    def _enter_bill_center(self, page: Any, job: Job) -> str:
        """对账中心常被「授权页 / 登录页」挡住：直接走跨境域唯一可用的 /login 登录页。"""
        if _on_bill_page(page):
            return ""
        # 只登记页面，不在这里拦截：入口页还没走完，贸然等人工反而卡住正常流程。
        key, _, _, _ = self._observe_page(page, job)
        job.step = "站点授权"
        job.progress = 10
        try:
            page.goto(
                seller_login_url(FINANCE_URLS[0]),
                wait_until="domcontentloaded",
                timeout=45_000,
            )
        except Exception:  # noqa: BLE001
            pass
        self._safe_wait(page, 4000)
        self._dismiss_popups(page)
        if self._confirm_authorize(page):
            self._book.remember(key, action="confirm-authorize", ok=True)
            self._safe_wait(page, 5000)
        if _on_bill_page(page):
            return ""
        if self._has_password_field(page) or self._is_login_page(page):
            return self._login_with_saved_credentials(page, job, BILL_URL)
        return ""

    def lookup_order_spus(self, account_id: str, items: list[dict[str, Any]], *,
                          page: Any | None = None, context: Any | None = None) -> dict[str, Any]:
        """复用店铺登录态，以商品管理 SKU 精确查询同一商品行中的 SPU。

        优先用订单行自带的 SPU，其次读本地精确映射缓存，只有仍未命中的 SKU 才进商品列表查询。
        """
        sku_spus: dict[str, str] = {}
        for item in items:
            sku = str(item.get("sku_id") or "").strip()
            spu = str(item.get("spu_id") or "").strip()
            if sku and (sku not in sku_spus or spu):
                sku_spus[sku] = spu
        sku_rows = list(sku_spus.items())
        resolved = {sku: spu for sku, spu in sku_rows if spu}
        unresolved = [sku for sku, spu in sku_rows if not spu]
        if unresolved and self._store is not None:
            try:
                resolved.update(self._store.order_spu_mapping(account_id, unresolved))
            except Exception:  # noqa: BLE001 - 缓存不可用不影响实时查询
                pass
            unresolved = [sku for sku in unresolved if not resolved.get(sku)]
        if not unresolved:
            return {"ok": True, "spus": sorted(set(resolved.values())),
                    "matched": len(sku_rows), "sku_count": len(sku_rows), "unmatched": []}
        if not self._temu_workflow:
            return {"ok": False, "message": "订单 SPU 缺失，当前任务未启用 TEMU 联动"}
        try:
            from ads import lookup_spus_by_sku
            mapping = lookup_spus_by_sku(self, account_id, unresolved, page=page, context=context)
        except Exception as error:
            return {"ok": False, "waiting_human": True,
                    "message": f"商品管理 SKU→SPU 查询未完成：{type(error).__name__}"}
        found = mapping.get("mapping", {}) or {}
        resolved.update(found)
        if found and self._store is not None:
            try:
                self._store.add_order_spu_mapping(account_id, found, source="goods_list")
            except Exception:  # noqa: BLE001
                pass
        missing = [sku for sku in unresolved if not resolved.get(sku)]
        return {"ok": not missing, "spus": sorted(set(resolved.values())),
                "matched": len(resolved), "sku_count": len(sku_rows), "unmatched": missing,
                "waiting_human": bool(mapping.get("waiting_human")),
                "message": "" if not missing else str(mapping.get("message") or
                    f"有 {len(missing)} 个出单 SKU 未能精确映射到 SPU")}

    def settlement_worker(self, job: Job) -> dict[str, Any]:
        """在对账中心「导出历史」里下载财务明细，解析 PO 级结算与违规扣款。"""
        options = getattr(job, "_settlement", None) or {}
        days = max(1, int(options.get("days") or 30))
        workflow_session = options.get("workflow_session")
        owns_context = workflow_session is None
        playwright = self._launch() if owns_context else None
        context = self._context(playwright, job.account_id) if owns_context else workflow_session["context"]
        try:
            page = context.pages[0] if context.pages else context.new_page()

            job.step = "打开对账中心"
            job.progress = 8
            try:
                page.goto(BILL_URL, wait_until="domcontentloaded", timeout=45_000)
            except Exception:  # noqa: BLE001 - 页面打不开时交给后面的检查给出原因
                pass
            self._safe_wait(page, 3000)
            self._dismiss_popups(page)
            if self._is_login_page(page):
                if self._temu_workflow:
                    self._shot(page, job.account_id)
                    return {"ok": False, "action": "settlement", "need_login": True,
                            "message": "TEMU联动流程的店铺登录态失效；为保持同店账号流程，不自动切换或重新登录"}
                # 对账中心在跨境后台，登录失效时先用库里的手机号/密码自动登录。
                login_error = self._login_with_saved_credentials(page, job, BILL_URL)
                if login_error:
                    self._shot(page, job.account_id)
                    return {
                        "ok": False,
                        "action": "settlement",
                        "message": login_error,
                    }
            if not _on_bill_page(page) and self._temu_workflow:
                self._shot(page, job.account_id)
                return {"ok": False, "action": "settlement", "need_login": True,
                        "message": "未在现有登录态中到达对账中心；TEMU联动流程不会切换账号或发起登录"}
            if not _on_bill_page(page):
                # 站点授权页 / 授权登录页会挡在对账中心前面，这里补齐授权和自动登录。
                enter_error = self._enter_bill_center(page, job)
                if enter_error:
                    self._shot(page, job.account_id)
                    return {
                        "ok": False,
                        "action": "settlement",
                        "message": enter_error,
                    }

            if not _on_bill_page(page):
                # 页面探针兜底：陌生页 / 验证页先交给管理员，处理完再判断能不能继续。
                self._gate_page(page, job, allowed={"bill", "login", "auth"})
                if not _on_bill_page(page) and self._has_password_field(page):
                    login_error = self._login_with_saved_credentials(page, job, BILL_URL)
                    if login_error:
                        self._shot(page, job.account_id)
                        return {
                            "ok": False,
                            "action": "settlement",
                            "message": login_error,
                        }
                if not _on_bill_page(page):
                    self._shot(page, job.account_id)
                    return {
                        "ok": False,
                        "action": "settlement",
                        "message": (
                            f"没有停在「对账中心」：当前页面 {page.url}。"
                            "已在实时画面等待人工处理后仍未回到对账中心，请手动回到对账中心再重试"
                        ),
                    }

            job.step = "下载财务明细"
            job.progress = 30
            source = {"url": ""}
            content, file_name = export_settle_report(
                context,
                page,
                on_step=lambda text: setattr(job, "step", text),
                on_page=lambda url: source.update(url=url),
                gate=self._settle_gate,
            )

            job.step = "解析结算明细"
            job.progress = 90
            rows, entries = parse_fund_detail_with_entries(content, file_name)
            totals = summarize(rows)
            self._shot(page, job.account_id)
            settlement = {
                "ok": True,
                "file": file_name,
                "days": days,
                "fetched_at": _now(),
                "source_url": source["url"],
                **totals,
                "entry_count": len(entries),
                "entries": entries,
                "rows": rows,
            }
            return {
                "ok": True,
                "action": "settlement",
                "message": (
                    f"结算明细已同步：{file_name} · "
                    f"结算金额 ¥{totals['settlement_amount']:.2f} · "
                    f"罚款 ¥{totals['penalty_amount']:.2f} · {totals['po_count']} 个 PO"
                    f" · {len(entries)} 笔"
                ),
                "settlement": settlement,
            }
        except (RuntimeError, ValueError) as error:
            return {"ok": False, "action": "settlement", "message": str(error)}
        finally:
            if owns_context:
                if context is not None:
                    context.close()
                if playwright is not None:
                    playwright.stop()

    def temu_workflow_worker(self, job: Job) -> dict[str, Any]:
        """同一店铺登录态串行跑完 TEMU 全链路：订单 → 对账 → SKU→SPU → 广告。

        Playwright 同步对象只能在创建它的线程里使用，所以整条链路必须放在同一个
        job 线程内完成，不能把 context 交给其它阶段任务。
        """
        from ads import collect_ads

        on_done = getattr(job, "_workflow_on_done", None)
        stages: list[dict[str, Any]] = []

        def record_step(step: str, status: str, message: str = "") -> None:
            stages.append({"step": step, "status": status, "message": message, "at": _now()})

        def persist(result: dict[str, Any]) -> dict[str, Any]:
            """每个阶段都先落库，后续阶段失败不覆盖已成功的数据。"""
            if on_done is not None:
                on_done(job.account_id, result)
            return result

        def stop(step: str, result: dict[str, Any], *, partial: bool) -> dict[str, Any]:
            message = str(result.get("message") or "阶段未完成")
            waiting = bool(result.get("need_login") or result.get("waiting_human"))
            record_step(step, "waiting_human" if waiting else "failed", message)
            return {"ok": False, "action": "workflow", "need_login": waiting,
                    "waiting_human": waiting, "partial": partial,
                    "stages": stages, "message": message}

        playwright = self._launch()
        context = None
        try:
            context = self._context(playwright, job.account_id)
            job._workflow_session = {"context": context}  # type: ignore[attr-defined]

            job.step = "订单采集"
            orders = persist(self.capture_worker(job))
            if not orders.get("ok"):
                return stop("orders", orders, partial=False)
            record_step("orders", "succeeded", f"{len(orders.get('items') or [])} 条订单")
            pending = int(orders.get("pending_details") or 0)
            if pending:
                message = f"订单详情仍有 {pending} 单未完成，暂停后续联动步骤"
                record_step("orders", "partial", message)
                return {"ok": False, "action": "workflow", "partial": True,
                        "stages": stages, "message": message}

            job.step = "对账中心采集"
            job._settlement = {"days": 90, "workflow_session": job._workflow_session}  # type: ignore[attr-defined]
            settlement = persist(self.settlement_worker(job))
            if not settlement.get("ok"):
                return stop("settlement", settlement, partial=True)
            record_step("settlement", "succeeded", str(settlement.get("message") or "对账采集完成"))

            job.step = "查询出单 SKU 对应 SPU"
            # 商品列表与推广平台需要完整资源（页面自带 CSP/脚本），解除采集订单时的资源拦截。
            try:
                context.unroute("**/*")
            except Exception:  # noqa: BLE001
                pass
            page = context.pages[0] if context.pages else context.new_page()
            mapping = self.lookup_order_spus(
                job.account_id, orders.get("items") or [], page=page, context=context
            )
            if not mapping.get("ok"):
                return stop("spu_mapping", mapping, partial=True)
            record_step("spu_mapping", "succeeded",
                        f"匹配 {mapping.get('matched', 0)} / {mapping.get('sku_count', 0)} 个 SKU")

            job.step = "按出单 SPU 查询广告费用"
            ads = persist(collect_ads(self, job, spus=mapping.get("spus") or [],
                                      page=page, context=context))
            if not ads.get("ok"):
                return stop("ads", ads, partial=True)
            record_step("ads", "succeeded", str(ads.get("message") or "匹配广告费用查询完成"))
            return {
                "ok": True,
                "action": "workflow",
                "stages": stages,
                "message": (
                    f"TEMU 全链路完成：{len(orders.get('items') or [])} 条订单；"
                    f"{len(mapping.get('spus') or [])} 个出单 SPU；广告只匹配已出单 SPU，未计入利润"
                ),
            }
        except Exception as error:  # noqa: BLE001 - 阶段异常也要给出可恢复结果
            message = f"TEMU 联动流程中断（{type(error).__name__}）：{error}"
            record_step("workflow", "failed", message)
            return {"ok": False, "action": "workflow", "stages": stages, "message": message}
        finally:
            if context is not None:
                context.close()
            playwright.stop()

    def run_sync_temu_workflow(
        self,
        account_id: str,
        on_done: Callable[[str, dict[str, Any]], None],
        poll_seconds: float = 1.0,
    ) -> dict[str, Any]:
        holder: dict[str, Any] = {}

        def capture_result(aid: str, result: dict[str, Any]) -> None:
            holder.update(result)
            on_done(aid, result)

        def worker(job: Job) -> dict[str, Any]:
            job._workflow_on_done = on_done  # type: ignore[attr-defined]
            return self.temu_workflow_worker(job)

        self.start(account_id, "workflow", worker, capture_result)
        return self._wait(account_id, holder, poll_seconds)

    def run_ads(self, account_id: str, on_done: Callable[[str, dict[str, Any]], None]) -> dict[str, Any]:
        from ads import collect_ads

        job = self.start(account_id, "ads", lambda job: collect_ads(self, job), on_done)
        return job.snapshot()

    def run_settlement(
        self,
        account_id: str,
        on_done: Callable[[str, dict[str, Any]], None],
        days: int = 30,
    ) -> dict[str, Any]:
        def worker(job: Job) -> dict[str, Any]:
            job._settlement = {"days": days}  # type: ignore[attr-defined]
            return self.settlement_worker(job)

        job = self.start(account_id, "settlement", worker, on_done)
        return job.snapshot()

    def run_sync_probe(
        self,
        account_id: str,
        on_done: Callable[[str, dict[str, Any]], None],
        poll_seconds: float = 1.0,
    ) -> dict[str, Any]:
        holder: dict[str, Any] = {}

        def capture_result(aid: str, result: dict[str, Any]) -> None:
            holder.update(result)
            on_done(aid, result)

        self.start(account_id, "probe", self.probe_worker, capture_result)
        return self._wait(account_id, holder, poll_seconds)

    def run_capture(
        self,
        account_id: str,
        on_done: Callable[[str, dict[str, Any]], None],
    ) -> dict[str, Any]:
        job = self.start(account_id, "capture", self.capture_worker, on_done)
        return job.snapshot()

    def run_sync_capture(
        self,
        account_id: str,
        on_done: Callable[[str, dict[str, Any]], None],
        poll_seconds: float = 1.0,
    ) -> dict[str, Any]:
        """同线程等待采集结束，供轮换器按顺序调用。"""
        holder: dict[str, Any] = {}

        def capture_result(aid: str, result: dict[str, Any]) -> None:
            holder.update(result)
            on_done(aid, result)

        self.start(account_id, "capture", self.capture_worker, capture_result)
        return self._wait(account_id, holder, poll_seconds)

    def run_sync_login(
        self,
        account_id: str,
        credentials: dict[str, str],
        on_done: Callable[[str, dict[str, Any]], None],
        poll_seconds: float = 1.0,
    ) -> dict[str, Any]:
        holder: dict[str, Any] = {}

        def capture_result(aid: str, result: dict[str, Any]) -> None:
            holder.update(result)
            on_done(aid, result)

        def worker(job: Job) -> dict[str, Any]:
            job._creds = credentials  # type: ignore[attr-defined]
            return self.login_worker(job)

        self.start(account_id, "login", worker, capture_result)
        return self._wait(account_id, holder, poll_seconds)

    def _wait(
        self, account_id: str, holder: dict[str, Any], poll_seconds: float
    ) -> dict[str, Any]:
        while True:
            job = self.job(account_id)
            if job is None or job.get("status") != "running":
                break
            time.sleep(max(0.2, poll_seconds))
        if holder:
            return holder
        job = self.job(account_id) or {}
        return {"ok": False, "message": job.get("message") or "任务未返回结果"}
