"""店小秘（Dianxiaomi）采集：登录并保留登录态，按 PO 单号抓对应的 XM 单号。

登录带图形验证码，不做任何绕过：账号密码自动填入，验证码交给用户在实时画面里完成。
每个店小秘账号一套持久化 profile，登录成功一次后重启服务也不用再登。
"""

from __future__ import annotations

import json
import random
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from runner import Job

ENTRY_URL = "https://www.dianxiaomi.com/"
ORDER_URL = "https://www.dianxiaomi.com/web/order/all"
# 店小秘的搜索框支持一次贴多个订单号（官方上限 1000），分批查更快。
BATCH_SIZE = 50
ORDERS_URL_CANDIDATES = (
    ORDER_URL,
    "https://www.dianxiaomi.com/web/order/all.htm",
    "https://www.dianxiaomi.com/web/order/orderList",
)
XM_PATTERN = r"XM[0-9A-Za-z]{6,}"

DUMP_JS = r"""() => {
    const links = [...document.querySelectorAll('a[href]')].slice(0, 500).map((a) => ({
        text: (a.innerText || '').trim().slice(0, 40),
        href: a.getAttribute('href') || '',
    })).filter((item) => item.text || item.href);
    const inputs = [...document.querySelectorAll('input, textarea')].map((el) => {
        const rect = el.getBoundingClientRect();
        return {
            type: el.type || '', placeholder: el.placeholder || '', name: el.name || '',
            id: el.id || '', cls: String(el.className || '').slice(0, 160),
            visible: !!(rect.width && rect.height && el.offsetParent !== null),
        };
    }).filter((item) => item.type !== 'hidden');
    const buttons = [...document.querySelectorAll('button')]
        .map((el) => (el.innerText || '').trim()).filter(Boolean).slice(0, 200);
    const headers = [...document.querySelectorAll('thead th')]
        .map((el) => (el.innerText || '').replace(/\s+/g, ' ').trim()).filter(Boolean).slice(0, 80);
    const rows = [...document.querySelectorAll('tr.vxe-body--row')].slice(0, 3)
        .map((el) => (el.innerText || '').replace(/\s+/g, ' ').slice(0, 400));
    return {
        url: location.href, title: document.title,
        links, inputs, buttons, headers, rows,
    };
}"""

# 店小秘订单表里，一个订单会拆成多行：first-level-row 是订单头（带 XM），
# 下面的商品行带平台单号 PO。按“订单头 + 后续商品行”分组，才能把 XM 和 PO 对上。
GROUPS_JS = r"""() => {
    const rows = [...document.querySelectorAll('tr.vxe-body--row')];
    const xmRe = /XM[0-9A-Za-z]{6,}/g;
    const poRe = /PO-\d{3}-\d{10,}/g;
    const headers = [...document.querySelectorAll('thead th')].map((th) =>
        (th.innerText || '').replace(/\s+/g, ' ').trim());
    const colOf = (name) => headers.findIndex((text) => text.indexOf(name) >= 0);
    const colLogistics = colOf('物流方式');
    const colStatus = colOf('状态');
    const cellsByCol = (row) => {
        const map = {};
        let col = 0;
        for (const td of row.querySelectorAll('td')) {
            if (!(col in map)) map[col] = (td.innerText || '').replace(/\s+/g, ' ').trim();
            col += td.colSpan || 1;
        }
        return map;
    };
    const groups = [];
    let current = null;
    for (const row of rows) {
        const text = (row.innerText || '').replace(/\s+/g, ' ');
        const cells = cellsByCol(row);
        const xms = text.match(xmRe) || [];
        const isFirst = row.classList.contains('first-level-row');
        if (isFirst && xms.length) {
            current = { xm: xms[0], pos: [], logistics: '', status: '' };
            groups.push(current);
            for (const po of text.match(poRe) || []) {
                if (current.pos.indexOf(po) < 0) current.pos.push(po);
            }
            continue;
        }
        if (!current) continue;
        for (const po of text.match(poRe) || []) {
            if (current.pos.indexOf(po) < 0) current.pos.push(po);
        }
        const logistics = colLogistics >= 0 ? (cells[colLogistics] || '') : '';
        if (!current.logistics && logistics) current.logistics = logistics;
        const status = colStatus >= 0 ? (cells[colStatus] || '') : '';
        if (!current.status && status) current.status = status;
    }
    // 一个 PO 拆多个包裹时，后面的订单头可能没重复平台单号，向前/向后继承一次。
    for (let i = 0; i < groups.length; i++) {
        if (groups[i].pos.length) continue;
        let found = null;
        for (let j = i + 1; j < groups.length; j++) {
            if (groups[j].pos.length) { found = groups[j].pos; break; }
        }
        if (!found) {
            for (let j = i - 1; j >= 0; j--) {
                if (groups[j].pos.length) { found = groups[j].pos; break; }
            }
        }
        if (found) groups[i].pos = found.slice();
        if (!groups[i].logistics) {
            const source = groups.find((item) => item.logistics && item.pos.join() === groups[i].pos.join());
            if (source) groups[i].logistics = source.logistics;
        }
    }
    return groups;
}"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DxmRunner:
    def __init__(
        self,
        *,
        data_dir: str | Path,
        chrome_path: str = "",
        store: Any | None = None,
        login_timeout: int = 600,
    ) -> None:
        self._dir = Path(data_dir)
        self._profiles = self._dir / "profiles"
        self._shots = self._dir / "shots"
        self._profiles.mkdir(parents=True, exist_ok=True)
        self._shots.mkdir(parents=True, exist_ok=True)
        self._chrome = chrome_path
        self._store = store
        self._login_timeout = max(120, int(login_timeout))
        self._debug_path = self._dir / "dxm-debug.json"
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    # ---------- 对外 ----------

    def job(self, account_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(account_id)
        return job.snapshot() if job else None

    def jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [job.snapshot() for job in self._jobs.values()]

    def shot_path(self, account_id: str) -> Path:
        return self._shots / f"dxm-{account_id}.png"

    def debug(self) -> dict[str, Any] | None:
        if not self._debug_path.exists():
            return None
        try:
            return json.loads(self._debug_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _start(
        self,
        account_id: str,
        action: str,
        worker: Callable[[Job], dict[str, Any]],
        on_done: Callable[[str, dict[str, Any]], None],
    ) -> dict[str, Any]:
        with self._lock:
            current = self._jobs.get(account_id)
            if current is not None and current.status == "running":
                raise RuntimeError("该店小秘账号正在执行任务")
            job = Job(account_id=account_id, action=action)
            self._jobs[account_id] = job

        def run() -> None:
            try:
                result = worker(job)
                result.setdefault("action", job.action)
                job.status = "done" if result.get("ok") else "failed"
                job.message = str(result.get("message") or "")
                job.progress = 100 if result.get("ok") else job.progress
                on_done(account_id, result)
            except BaseException as error:  # noqa: BLE001 - 后台任务必须收口
                job.status = "failed"
                job.message = f"{type(error).__name__}: {error}"
                on_done(account_id, {"ok": False, "message": job.message})
            finally:
                job.need_code = False
                job.updated_at = _now()

        threading.Thread(target=run, name=f"dxm-{account_id}", daemon=True).start()
        return job.snapshot()

    def run_login(
        self,
        account_id: str,
        credentials: dict[str, str],
        on_done: Callable[[str, dict[str, Any]], None],
    ) -> dict[str, Any]:
        def worker(job: Job) -> dict[str, Any]:
            job._creds = credentials  # type: ignore[attr-defined]
            return self.login_worker(job)

        return self._start(account_id, "login", worker, on_done)

    def run_probe(
        self, account_id: str, on_done: Callable[[str, dict[str, Any]], None]
    ) -> dict[str, Any]:
        return self._start(account_id, "probe", self.probe_worker, on_done)

    def run_discover(
        self, account_id: str, on_done: Callable[[str, dict[str, Any]], None]
    ) -> dict[str, Any]:
        return self._start(account_id, "discover", self.discover_worker, on_done)

    def run_align(
        self,
        account_id: str,
        pos: list[str],
        on_done: Callable[[str, dict[str, Any]], None],
    ) -> dict[str, Any]:
        def worker(job: Job) -> dict[str, Any]:
            job._align = {"pos": pos}  # type: ignore[attr-defined]
            return self.align_worker(job)

        return self._start(account_id, "align", worker, on_done)

    # ---------- 浏览器 ----------

    def _launch(self):
        from playwright.sync_api import sync_playwright

        return sync_playwright().start()

    def _context(self, playwright: Any, account_id: str):
        profile = self._profiles / f"dxm-{account_id}"
        profile.mkdir(parents=True, exist_ok=True)
        options: dict[str, Any] = {
            "user_data_dir": str(profile),
            "headless": False,
            "viewport": {"width": 1440, "height": 900},
            "args": [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--lang=zh-CN",
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
        return context

    @staticmethod
    def _safe_wait(page: Any, milliseconds: int) -> None:
        try:
            page.wait_for_timeout(milliseconds)
        except Exception:  # noqa: BLE001
            time.sleep(milliseconds / 1000)

    @staticmethod
    def _alive(page: Any) -> bool:
        try:
            return bool(page) and not page.is_closed()
        except Exception:  # noqa: BLE001
            return False

    def _pick_page(self, context: Any, fallback: Any) -> Any:
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

    def _shot(self, page: Any, account_id: str) -> None:
        try:
            page.screenshot(path=str(self.shot_path(account_id)), full_page=False)
        except Exception:  # noqa: BLE001
            pass

    def _dismiss_popups(self, page: Any) -> None:
        """店小秘会弹「店铺授权异常」等提示，不关掉会挡住搜索框的点击。"""
        selectors = (
            ".ant-modal-close",
            "button:has-text('稍后处理')",
            "button:has-text('不再提醒')",
            "button:has-text('我知道了')",
            "button:has-text('知道了')",
        )
        for _ in range(5):
            closed = False
            for selector in selectors:
                try:
                    locator = page.locator(selector).first
                    if locator.count() and locator.is_visible():
                        locator.click(timeout=1500)
                        self._safe_wait(page, 500)
                        closed = True
                        break
                except Exception:  # noqa: BLE001
                    continue
            if not closed:
                try:
                    if page.locator(".ant-modal-wrap:visible").count():
                        page.keyboard.press("Escape")
                        self._safe_wait(page, 400)
                        continue
                except Exception:  # noqa: BLE001
                    pass
                return

    # ---------- 登录态 ----------

    def _logged_in(self, page: Any) -> bool:
        try:
            url = str(page.url or "")
        except Exception:  # noqa: BLE001
            return False
        if not url or "login" in url.lower():
            return False
        # 店小秘登录后都在 /web/ 工作台里；未登录会被弹回官网首页。
        if "dianxiaomi.com/web/" in url:
            return True
        # 店小秘官网首页本身就有登录框，能看到密码框就说明还在登录页。
        try:
            password_fields = page.locator("input[type=password]")
            for index in range(min(3, password_fields.count())):
                if password_fields.nth(index).is_visible():
                    return False
        except Exception:  # noqa: BLE001
            pass
        try:
            text = page.evaluate(
                "() => document.body ? (document.body.innerText || '').slice(0, 6000) : ''"
            ) or ""
        except Exception:  # noqa: BLE001
            return False
        if not text or "欢迎登录" in text:
            return False
        markers = ("退出登录", "全部订单", "采购单", "我的店铺", "店铺授权")
        return any(marker in text for marker in markers)

    def _fill_login(self, page: Any, username: str, password: str) -> bool:
        """把账号密码填进登录页；图形验证码与提交都留给实时画面里的用户。"""
        if not username or not password:
            return False
        user_field = None
        for selector in (
            "#exampleInputName",
            "input[name='account']",
            "input[placeholder*='用户名']",
            "input[placeholder*='账号']",
            "input[placeholder*='邮箱']",
            "input[placeholder*='手机']",
            "input[name*='user']",
            "input#username",
            "input[type=text]",
        ):
            try:
                locator = page.locator(selector).first
                if locator.count() and locator.is_visible():
                    user_field = locator
                    break
            except Exception:  # noqa: BLE001
                continue
        if user_field is None:
            return False
        try:
            user_field.click(timeout=3000)
            user_field.fill("")
            user_field.type(username, delay=30)
        except Exception:  # noqa: BLE001
            return False
        for selector in (
            "#exampleInputPassword",
            "input[name='password']",
            "input[type=password]",
        ):
            try:
                password_field = page.locator(selector).first
                if password_field.count() and password_field.is_visible():
                    password_field.click(timeout=3000)
                    password_field.fill("")
                    password_field.type(password, delay=30)
                    break
            except Exception:  # noqa: BLE001
                continue
        return True

    def login_worker(self, job: Job) -> dict[str, Any]:
        creds = getattr(job, "_creds", None) or {}
        username = str(creds.get("username") or "")
        password = str(creds.get("password") or "")

        playwright = self._launch()
        context = None
        try:
            context = self._context(playwright, job.account_id)
            page = context.pages[0] if context.pages else context.new_page()
            job.step = "打开店小秘"
            job.progress = 10
            page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=60_000)
            self._safe_wait(page, 3000)
            self._dismiss_popups(page)
            self._shot(page, job.account_id)

            if self._logged_in(page):
                job.progress = 100
                return {"ok": True, "message": "登录态有效，无需重新登录"}

            job.step = "填写账号密码"
            job.progress = 30
            filled = self._fill_login(page, username, password)
            self._shot(page, job.account_id)
            job.need_code = True
            job.step = "等待人工验证"
            job.message = (
                "已自动填入账号密码，请在实时画面补充图形验证码并点「登录」"
                if filled
                else "请在实时画面里填写账号密码与图形验证码并登录"
            )

            deadline = time.time() + self._login_timeout
            last_shot = time.time()
            while time.time() < deadline:
                if not self._alive(page):
                    page = self._pick_page(context, page)
                if self._logged_in(page):
                    break
                if time.time() - last_shot > 15:
                    self._shot(page, job.account_id)
                    last_shot = time.time()
                self._safe_wait(page, 2000)
            else:
                self._shot(page, job.account_id)
                return {"ok": False, "message": "等待人工登录超时，请重试"}

            job.need_code = False
            job.step = "确认登录态"
            job.progress = 80
            self._safe_wait(page, 2000)
            if self._alive(page):
                try:
                    page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=45_000)
                    self._safe_wait(page, 2500)
                except Exception:  # noqa: BLE001
                    pass
            if not self._logged_in(page):
                self._shot(page, job.account_id)
                return {"ok": False, "message": "没有检测到登录成功，请重试"}
            job.progress = 100
            self._shot(page, job.account_id)
            return {"ok": True, "message": "登录成功，登录态已保存"}
        finally:
            job.need_code = False
            if context is not None:
                context.close()
            playwright.stop()

    def probe_worker(self, job: Job) -> dict[str, Any]:
        playwright = self._launch()
        context = None
        try:
            context = self._context(playwright, job.account_id)
            page = context.pages[0] if context.pages else context.new_page()
            job.step = "检查登录态"
            job.progress = 50
            page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=45_000)
            self._safe_wait(page, 3000)
            self._dismiss_popups(page)
            self._shot(page, job.account_id)
            if self._logged_in(page):
                job.progress = 100
                return {"ok": True, "message": "登录态有效"}
            return {"ok": False, "message": "登录已失效，请重新登录"}
        finally:
            if context is not None:
                context.close()
            playwright.stop()

    # ---------- 页面探查 ----------

    def _open_orders_page(self, page: Any) -> str:
        """直接打开全部订单页；地址已知，不依赖菜单文案。"""
        for url in ORDERS_URL_CANDIDATES:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                self._safe_wait(page, 4000)
                self._dismiss_popups(page)
                if page.locator("tr.vxe-body--row").count():
                    return str(page.url)
            except Exception:  # noqa: BLE001
                continue
        return ""

    def discover_worker(self, job: Job) -> dict[str, Any]:
        playwright = self._launch()
        context = None
        try:
            context = self._context(playwright, job.account_id)
            page = context.pages[0] if context.pages else context.new_page()
            job.step = "打开店小秘"
            job.progress = 15
            page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=60_000)
            self._safe_wait(page, 3000)
            self._dismiss_popups(page)
            if not self._logged_in(page):
                return {"ok": False, "message": "店小秘未登录，请先登录"}

            home = page.evaluate(DUMP_JS)
            job.step = "打开全部订单"
            job.progress = 55
            orders_url = self._open_orders_page(page)
            self._safe_wait(page, 2000)
            orders = page.evaluate(DUMP_JS)
            self._shot(page, job.account_id)

            # 搜索框候选：可见文本输入框里优先带「订单号」的。
            search_candidates = []
            for item in orders.get("inputs", []):
                if not item.get("visible"):
                    continue
                placeholder = str(item.get("placeholder") or "")
                if "订单号" in placeholder:
                    search_candidates.insert(0, item)
                elif item.get("type") in ("text", ""):
                    search_candidates.append(item)

            data = {
                "updated_at": _now(),
                "home": home,
                "orders": orders,
                "orders_url": orders_url,
                "search_candidates": search_candidates,
            }
            self._debug_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            if self._store is not None and orders_url:
                self._store.save_settings(
                    {"dxm_orders_url": orders_url, "dxm_search_selector": "#orderSearchInput"}
                )
            job.progress = 100
            return {
                "ok": True,
                "message": f"页面探查完成：{orders_url or '没找到订单页'}",
                "orders_url": orders_url,
            }
        finally:
            if context is not None:
                context.close()
            playwright.stop()

    # ---------- 按 PO 抓 XM ----------

    def _search_batch(self, page: Any, pos: list[str]) -> dict[str, Any]:
        """一次把多个 PO 贴进搜索框，返回 {pairs: {po: [xm...]}, logistics: {xm: 物流方式}}。"""
        field = page.locator("#orderSearchInput")
        if not field.count():
            return {}
        try:
            field.click(timeout=4000)
            field.fill("")
            field.fill(" ".join(pos))
            button = page.get_by_role("button", name="搜索", exact=True).first
            if button.count() and button.is_visible():
                button.click(timeout=5000)
            else:
                page.locator("button:has-text('搜索')").first.click(timeout=5000)
            self._safe_wait(page, 4500)
            self._dismiss_popups(page)
        except Exception:  # noqa: BLE001
            return {}

        wanted: dict[str, list[str]] = {po: [] for po in pos}
        logistics: dict[str, str] = {}
        for group in page.evaluate(GROUPS_JS) or []:
            xm = str(group.get("xm") or "").strip()
            if not xm:
                continue
            channel = str(group.get("logistics") or "").strip()
            if channel:
                logistics[xm] = channel
            for po in group.get("pos") or []:
                code = str(po)
                if code in wanted and xm not in wanted[code]:
                    wanted[code].append(xm)
        return {"pairs": wanted, "logistics": logistics}

    def align_worker(self, job: Job) -> dict[str, Any]:
        payload = getattr(job, "_align", None) or {}
        pos = [str(po).strip() for po in payload.get("pos") or [] if str(po).strip()]
        if not pos:
            return {"ok": False, "message": "没有需要对齐的 PO 单号"}
        cfg = self._store.settings() if self._store is not None else {}
        orders_url = str(cfg.get("dxm_orders_url") or "").strip() or ORDER_URL

        playwright = self._launch()
        context = None
        try:
            context = self._context(playwright, job.account_id)
            page = context.pages[0] if context.pages else context.new_page()
            job.step = "打开店小秘全部订单"
            job.progress = 5
            page.goto(orders_url, wait_until="domcontentloaded", timeout=60_000)
            self._safe_wait(page, 6000)
            self._dismiss_popups(page)
            self._safe_wait(page, 2000)
            self._dismiss_popups(page)
            if not self._logged_in(page):
                self._shot(page, job.account_id)
                return {"ok": False, "message": "店小秘登录已失效，请重新登录"}

            job.step = "切换到搜索模式"
            job.progress = 10
            try:
                tab = page.locator(".switch-search-mode--item", has_text="搜索").first
                if tab.count():
                    tab.click(timeout=4000, force=True)
                    self._safe_wait(page, 1500)
            except Exception:  # noqa: BLE001
                pass
            self._dismiss_popups(page)
            if not page.locator("#orderSearchInput").count():
                self._shot(page, job.account_id)
                return {
                    "ok": False,
                    "message": "店小秘订单页没有找到搜索框，页面结构可能变了",
                }

            pairs: dict[str, list[str]] = {po: [] for po in pos}
            logistics: dict[str, str] = {}
            batches = [pos[i : i + BATCH_SIZE] for i in range(0, len(pos), BATCH_SIZE)]
            total = len(batches)
            for index, batch in enumerate(batches):
                job.step = f"批量搜索 {index + 1}/{total}（{len(batch)} 个 PO）"
                job.progress = min(96, 12 + int(index / total * 84))
                result = self._search_batch(page, batch)
                for po, xms in (result.get("pairs") or {}).items():
                    if po in pairs:
                        pairs[po] = xms
                logistics.update(result.get("logistics") or {})
                matched = len([po for po in pos if pairs.get(po)])
                job.message = f"已找到 {matched}/{len(pos)} 个 PO 的 XM"
                self._safe_wait(page, random.randint(600, 1200))

            self._shot(page, job.account_id)
            job.progress = 100
            missing = [po for po in pos if not pairs.get(po)]
            return {
                "ok": True,
                "message": f"对齐完成：{len(pos) - len(missing)}/{len(pos)} 个 PO 找到 XM",
                "pairs": pairs,
                "logistics": logistics,
                "missing": missing,
            }
        finally:
            if context is not None:
                context.close()
            playwright.stop()
