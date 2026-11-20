"""发货平台头程金额采集：无界 ERP + 浩远国际快递。

无界 ERP：登录 https://erp.wujie-erp.com/login，费用查询 /fees，按客户订单号（XM）批量查总金额。
浩远：登录 https://www.hy-express.com/，运费明细 /usercenter/fee/freight_list.aspx，
      按日期范围批量拉「参考单号（XM）+ 总费用」，本地匹配 XM。

无界 ERP 把登录态放在 sessionStorage（关掉浏览器就丢），所以这里为每个平台
常驻一个采集线程 + 浏览器标签页：登录成功一次后，后续查询直接在同一个标签页里做。
"""

from __future__ import annotations

import queue
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from runner import Job

WUJIE = "wujie"
WUJIE_LOGIN_URL = "https://erp.wujie-erp.com/login"
WUJIE_FEES_URL = "https://erp.wujie-erp.com/fees"
# 搜索框支持多个客户订单号（逗号/空格/换行分隔），默认每页 20 条，按 20 一批最稳。
BATCH_SIZE = 20

# 浩远：登录页在官网，成功后跳到 order.hy-express.com 的订单系统。
HY = "hy"
HY_LOGIN_URL = "https://www.hy-express.com/"
HY_FEE_URL = "https://order.hy-express.com/usercenter/fee/freight_list.aspx"
HY_PAGE_SIZE = 1000
HY_LOOKBACK_DAYS = 400

# rtb56（顺丰 / 义乌煊烊等）：登录页需要图形验证码，由界面人工输入一次。
RTB = "rtb"
RTB_CODE_TIMEOUT = 240
# rtb56 后台：运费明细（已产生的头程费用）/ 未付款（未结算的头程费用）
RTB_FREIGHT_PAGE = "fee/freight_list.aspx"
RTB_NOPAY_PAGE = "fee/nopayment_list.aspx"
RTB_LOOKBACK_DAYS = 1000
RTB_XM_RE = re.compile(r"参考单号[：:]\s*([A-Za-z0-9\-]+)")
RTB_TRACK_RE = re.compile(r"跟踪单号[：:]\s*([A-Za-z0-9\-]+)")
RTB_WEIGHT_RE = re.compile(r"计费重[：:]\s*([\d.]+)")

RTB_ROWS_JS = r"""() => {
    const tables = [...document.querySelectorAll('table')].map((t) =>
        [...t.querySelectorAll('tr')].map((tr) =>
            [...tr.querySelectorAll('th,td')].map((td) =>
                (td.innerText || '').replace(/\s+/g, ' ').trim())));
    tables.sort((a, b) => b.length - a.length);
    return tables.length ? tables[0] : [];
}"""

RESULTS_JS = r"""() => {
    const rows = [...document.querySelectorAll('tbody tr')].map((tr) =>
        [...tr.querySelectorAll('td')].map((td) =>
            (td.innerText || '').replace(/\s+/g, ' ').trim()));
    const total = document.querySelector('.el-pagination__total');
    return { rows, total: total ? (total.innerText || '') : '' };
}"""

NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
HY_XM_RE = re.compile(r"参考单号[：:]\s*([A-Za-z0-9\-]+)")
HY_WEIGHT_RE = re.compile(r"计费重[：:]\s*([\d.]+)")

HAO_ROWS_JS = r"""() => {
    const table = [...document.querySelectorAll('table')].find((t) => t.querySelectorAll('tbody tr').length > 1);
    if (!table) return { rows: [] };
    return { rows: [...table.querySelectorAll('tbody tr')].map((tr) =>
        [...tr.querySelectorAll('td')].map((td) =>
            (td.innerText || '').replace(/\s+/g, ' ').trim())) };
}"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _number(text: str) -> float | None:
    match = NUMBER_RE.search(str(text or ""))
    if match is None:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


class FreightRunner:
    def __init__(
        self,
        *,
        data_dir: str | Path,
        chrome_path: str = "",
        store: Any | None = None,
    ) -> None:
        self._dir = Path(data_dir)
        self._profiles = self._dir / "profiles"
        self._shots = self._dir / "shots"
        self._profiles.mkdir(parents=True, exist_ok=True)
        self._shots.mkdir(parents=True, exist_ok=True)
        self._chrome = chrome_path
        self._store = store
        self._jobs: dict[str, Job] = {}
        self._queues: dict[str, queue.Queue] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    # ---------- 对外 ----------

    def job(self, platform: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(platform)
        return job.snapshot() if job else None

    def jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [job.snapshot() for job in self._jobs.values()]

    def shot_path(self, platform: str) -> Path:
        return self._shots / f"freight-{platform}.png"

    def captcha_path(self, platform: str) -> Path:
        return self._shots / f"freight-{platform}-captcha.png"

    def submit_code(self, platform: str, code: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(platform)
            if job is None or not job.need_code:
                raise RuntimeError("当前没有等待输入验证码的登录任务")
            job.code_value = str(code)
            job.code_event.set()
            job.need_code = False
        return {"ok": True, "message": "验证码已提交"}

    def _adapter(self, platform: str) -> str:
        if self._store is not None:
            credentials = self._store.get_freight_credentials(platform) or {}
            return str(credentials.get("adapter") or WUJIE)
        return WUJIE

    def run_login(
        self,
        platform: str,
        credentials: dict[str, str],
        on_done: Callable[[str, dict[str, Any]], None],
    ) -> dict[str, Any]:
        adapter = str(credentials.get("adapter") or WUJIE)

        def worker(job: Job, page: Any) -> dict[str, Any]:
            if adapter == RTB:
                return self.rtb_login_worker(job, page, credentials)
            if adapter == HY:
                return self.hy_login_worker(job, page, credentials)
            return self.login_worker(job, page, credentials)

        return self._submit(platform, "login", worker, on_done)

    def run_query(
        self,
        platform: str,
        xms: list[str],
        on_done: Callable[[str, dict[str, Any]], None],
    ) -> dict[str, Any]:
        adapter = self._adapter(platform)

        def worker(job: Job, page: Any) -> dict[str, Any]:
            if adapter == RTB:
                return self.rtb_query_worker(job, page, xms)
            if adapter == HY:
                return self.hy_query_worker(job, page, xms)
            return self.query_worker(job, page, xms)

        return self._submit(platform, "query", worker, on_done)

    # ---------- 常驻线程 ----------

    def _submit(
        self,
        platform: str,
        action: str,
        worker: Callable[[Job, Any], dict[str, Any]],
        on_done: Callable[[str, dict[str, Any]], None],
    ) -> dict[str, Any]:
        with self._lock:
            current = self._jobs.get(platform)
            if current is not None and current.status == "running":
                raise RuntimeError("该发货平台正在执行任务")
            job = Job(account_id=platform, action=action)
            self._jobs[platform] = job
            self._ensure_thread_locked(platform)
            self._queues[platform].put((job, worker, on_done))
        return job.snapshot()

    def _ensure_thread_locked(self, platform: str) -> None:
        if platform in self._threads:
            return
        self._queues[platform] = queue.Queue()
        thread = threading.Thread(
            target=self._loop, args=(platform,), name=f"freight-{platform}", daemon=True
        )
        self._threads[platform] = thread
        thread.start()

    def _loop(self, platform: str) -> None:
        """独占一个 Playwright 实例；浏览器在两次任务之间保持打开，sessionStorage 才不会丢。"""
        from playwright.sync_api import sync_playwright

        playwright = sync_playwright().start()
        context = None
        page = None
        while True:
            job, worker, on_done = self._queues[platform].get()
            try:
                if context is None or page is None or page.is_closed():
                    context = self._context(playwright, platform)
                    page = context.pages[0] if context.pages else context.new_page()
                result = worker(job, page)
                result.setdefault("action", job.action)
                job.status = "done" if result.get("ok") else "failed"
                job.message = str(result.get("message") or "")
                if result.get("ok"):
                    job.progress = 100
                on_done(platform, result)
            except BaseException as error:  # noqa: BLE001 - 常驻线程不能因单次任务退出
                job.status = "failed"
                job.message = f"{type(error).__name__}: {error}"
                on_done(platform, {"ok": False, "action": job.action, "message": job.message})
                if context is not None:
                    try:
                        context.close()
                    except Exception:  # noqa: BLE001
                        pass
                context = None
                page = None
            finally:
                job.need_code = False
                job.updated_at = _now()

    def _context(self, playwright: Any, platform: str):
        profile = self._profiles / f"freight-{platform}"
        profile.mkdir(parents=True, exist_ok=True)
        options: dict[str, Any] = {
            "user_data_dir": str(profile),
            # 无界 ERP 登录态在 sessionStorage，浏览器常驻即可；用无头模式不占实时画面。
            "headless": True,
            "viewport": {"width": 1440, "height": 900},
            "args": [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--lang=zh-CN",
            ],
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
        }
        if self._chrome:
            options["executable_path"] = self._chrome
        context = playwright.chromium.launch_persistent_context(**options)
        context.set_default_timeout(20_000)
        return context

    # ---------- 页面工具 ----------

    @staticmethod
    def _safe_wait(page: Any, milliseconds: int) -> None:
        try:
            page.wait_for_timeout(milliseconds)
        except Exception:  # noqa: BLE001
            time.sleep(milliseconds / 1000)

    def _shot(self, page: Any, platform: str) -> None:
        try:
            page.screenshot(path=str(self.shot_path(platform)), full_page=False)
        except Exception:  # noqa: BLE001
            pass

    def _logged_in(self, page: Any) -> bool:
        try:
            url = str(page.url or "")
        except Exception:  # noqa: BLE001
            return False
        if not url or "/login" in url.lower():
            return False
        try:
            text = page.evaluate(
                "() => document.body ? (document.body.innerText || '').slice(0, 4000) : ''"
            ) or ""
        except Exception:  # noqa: BLE001
            return False
        return "费用查询" in text or "客户订单" in text or "退出登录" in text

    def _dismiss(self, page: Any) -> None:
        for name in ("我知道了", "稍后再说", "关闭"):
            try:
                button = page.get_by_role("button", name=name).first
                if button.count() and button.is_visible():
                    button.click(timeout=1500)
                    self._safe_wait(page, 300)
            except Exception:  # noqa: BLE001
                continue

    def _login_once(
        self, page: Any, username: str, password: str, *, goto: bool = True
    ) -> tuple[bool, bool]:
        """返回 (是否已登录, 是否成功填入账号密码)。"""
        if goto:
            page.goto(WUJIE_LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
            self._safe_wait(page, 2500)
        if self._logged_in(page):
            return True, False
        if not username or not password:
            return False, False
        filled = False
        try:
            account = page.get_by_placeholder("请输入商家账号").first
            if not account.count():
                account = page.locator("input[type=text]").first
            account.click(timeout=3000)
            account.fill("")
            account.type(username, delay=30)
            secret = page.get_by_placeholder("请输入密码").first
            if not secret.count():
                secret = page.locator("input[type=password]").first
            secret.click(timeout=3000)
            secret.fill("")
            secret.type(password, delay=30)
            filled = True
        except Exception:  # noqa: BLE001
            return False, False
        if filled:
            try:
                page.get_by_role("button", name="登 录").first.click(timeout=6000)
            except Exception:  # noqa: BLE001
                try:
                    page.locator("button:has-text('登')").first.click(timeout=4000)
                except Exception:  # noqa: BLE001
                    pass
            self._safe_wait(page, 6000)
        return self._logged_in(page), filled

    # ---------- 任务 ----------

    def login_worker(
        self, job: Job, page: Any, credentials: dict[str, str]
    ) -> dict[str, Any]:
        job.step = "打开无界 ERP"
        job.progress = 15
        ok, filled = self._login_once(
            page,
            str(credentials.get("username") or ""),
            str(credentials.get("password") or ""),
        )
        self._shot(page, job.account_id)
        if ok:
            job.progress = 100
            return {
                "ok": True,
                "message": "登录成功，登录态已保存在常驻标签页"
                if filled
                else "登录态有效，无需重新登录",
            }
        return {
            "ok": False,
            "message": "无界 ERP 登录失败：账号密码没有通过（页面未出现费用查询），请核对账号",
        }

    def _wait_results(self, page: Any, batch: list[str], timeout: float = 18.0) -> dict[str, Any]:
        """等表格换成这一批的结果，避免读到上一批的旧行。"""
        wanted = set(batch)
        deadline = time.time() + timeout
        data: dict[str, Any] = {"rows": [], "total": ""}
        while time.time() < deadline:
            try:
                data = page.evaluate(RESULTS_JS)
            except Exception:  # noqa: BLE001
                self._safe_wait(page, 500)
                continue
            xms = [
                str(cells[2])
                for cells in data.get("rows") or []
                if len(cells) > 2 and str(cells[2])
            ]
            if xms and all(xm in wanted for xm in xms):
                return data
            if not xms and "0 条" in str(data.get("total") or ""):
                return data
            self._safe_wait(page, 500)
        return data

    def query_worker(
        self, job: Job, page: Any, xms: list[str]
    ) -> dict[str, Any]:
        xms = list(dict.fromkeys(str(xm).strip() for xm in xms if str(xm).strip()))
        if not xms:
            return {"ok": False, "message": "没有需要查询的 XM 单号"}

        job.step = "检查登录态"
        job.progress = 5
        if not self._logged_in(page):
            credentials = (
                self._store.get_freight_credentials(job.account_id) if self._store else None
            ) or {}
            ok, _ = self._login_once(
                page,
                str(credentials.get("username") or ""),
                str(credentials.get("password") or ""),
            )
            if not ok:
                self._shot(page, job.account_id)
                return {"ok": False, "message": "无界 ERP 未登录，请先点「无界登录」"}

        job.step = "打开费用查询"
        job.progress = 8
        page.goto(WUJIE_FEES_URL, wait_until="domcontentloaded", timeout=60_000)
        self._safe_wait(page, 4000)
        self._dismiss(page)
        if not self._logged_in(page):
            self._shot(page, job.account_id)
            return {"ok": False, "message": "无界 ERP 未登录，请先点「无界登录」"}

        box = page.get_by_placeholder("客户订单号（多个用逗号/空格/换行分隔）").first
        if not box.count():
            self._shot(page, job.account_id)
            return {"ok": False, "message": "费用查询页没有找到客户订单号输入框"}

        batches = [xms[i : i + BATCH_SIZE] for i in range(0, len(xms), BATCH_SIZE)]
        total = len(batches)
        found: dict[str, dict[str, Any]] = {}
        for index, batch in enumerate(batches):
            job.step = f"查询头程 {index + 1}/{total}（{len(batch)} 个 XM）"
            job.progress = min(95, 8 + int(index / total * 86))
            try:
                box.click(timeout=4000)
                box.fill("")
                box.fill(" ".join(batch))
                page.get_by_role("button", name="查询").first.click(timeout=6000)
            except Exception:  # noqa: BLE001
                self._safe_wait(page, 1500)
                continue
            data = self._wait_results(page, batch)
            for cells in data.get("rows") or []:
                if len(cells) < 7:
                    continue
                xm = str(cells[2] or "").strip()
                if xm not in batch:
                    continue
                found[xm] = {
                    "xm": xm,
                    "order_no": str(cells[1] or ""),
                    "weight": _number(cells[3]),
                    "amount": _number(cells[4]),
                    "currency": str(cells[5] or "").strip(),
                    "status": str(cells[6] or "").strip(),
                    "account": str(cells[7] or "") if len(cells) > 7 else "",
                }
            if found and self._store is not None:
                self._store.save_freight_quotes(job.account_id, list(found.values()))
            job.message = f"已查到 {len(found)}/{len(xms)} 个 XM 的头程金额"
            self._safe_wait(page, 700)

        self._shot(page, job.account_id)
        job.progress = 100
        missing = [xm for xm in xms if xm not in found]
        return {
            "ok": True,
            "message": f"头程查询完成：{len(found)}/{len(xms)} 个 XM 查到金额",
            "quotes": list(found.values()),
            "missing": missing,
        }

    # ---------- 浩远 ----------

    @staticmethod
    def _hy_logged_in(page: Any) -> bool:
        try:
            url = str(page.url or "")
        except Exception:  # noqa: BLE001
            return False
        return "order.hy-express.com" in url and "/login" not in url.lower()

    def _hy_login_once(
        self, page: Any, username: str, password: str
    ) -> tuple[bool, bool]:
        if self._hy_logged_in(page):
            return True, False
        if not username or not password:
            return False, False
        try:
            page.goto(HY_LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
            self._safe_wait(page, 2500)
            page.fill("#userName", username)
            page.fill("#userPass", password)
            page.get_by_role("button", name="立即登录").first.click(timeout=6000)
        except Exception:  # noqa: BLE001
            return False, False
        self._safe_wait(page, 6000)
        return self._hy_logged_in(page), True

    def hy_login_worker(
        self, job: Job, page: Any, credentials: dict[str, str]
    ) -> dict[str, Any]:
        job.step = "打开浩远登录页"
        job.progress = 15
        ok, filled = self._hy_login_once(
            page,
            str(credentials.get("username") or ""),
            str(credentials.get("password") or ""),
        )
        self._shot(page, job.account_id)
        if ok:
            job.progress = 100
            return {
                "ok": True,
                "message": "登录成功，登录态已保存在常驻标签页"
                if filled
                else "登录态有效，无需重新登录",
            }
        return {"ok": False, "message": "浩远登录失败：账号密码没有通过，请核对账号"}

    def hy_query_worker(
        self, job: Job, page: Any, xms: list[str]
    ) -> dict[str, Any]:
        wanted = list(dict.fromkeys(str(xm).strip() for xm in xms if str(xm).strip()))
        if not wanted:
            return {"ok": False, "message": "没有需要查询的 XM 单号"}
        wanted_set = set(wanted)

        job.step = "检查登录态"
        job.progress = 5
        if not self._hy_logged_in(page):
            credentials = (
                self._store.get_freight_credentials(job.account_id) if self._store else None
            ) or {}
            ok, _ = self._hy_login_once(
                page,
                str(credentials.get("username") or ""),
                str(credentials.get("password") or ""),
            )
            if not ok:
                self._shot(page, job.account_id)
                return {"ok": False, "message": "浩远未登录，请先点「浩远登录」"}

        job.step = "打开运费明细"
        job.progress = 8
        page.goto(HY_FEE_URL, wait_until="domcontentloaded", timeout=60_000)
        self._safe_wait(page, 3000)
        try:
            size = page.locator("#txtPageNum")
            size.fill(str(HY_PAGE_SIZE))
            page.keyboard.press("Tab")
            self._safe_wait(page, 5000)
        except Exception:  # noqa: BLE001
            pass

        end = datetime.now()
        start = end - timedelta(days=HY_LOOKBACK_DAYS)
        found: dict[str, dict[str, Any]] = {}
        page_no = 1
        while page_no <= 20:
            job.step = f"读取运费明细 {page_no}"
            job.progress = min(95, 10 + page_no * 8)
            url = (
                f"{HY_FEE_URL}?startDate={start:%Y-%m-%d}"
                f"&endDate={end:%Y-%m-%d}&dateType=A&page={page_no}"
            )
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            except Exception:  # noqa: BLE001
                break
            self._safe_wait(page, 1500)
            rows = [
                row
                for row in (page.evaluate(HAO_ROWS_JS).get("rows") or [])
                if len(row) >= 14
            ]
            if not rows:
                break
            for cells in rows:
                match = HY_XM_RE.search(str(cells[2]))
                if match is None:
                    continue
                xm = match.group(1)
                if xm not in wanted_set:
                    continue
                weight = HY_WEIGHT_RE.search(str(cells[9]))
                found[xm] = {
                    "xm": xm,
                    "order_no": str(cells[3] or ""),
                    "weight": float(weight.group(1)) if weight else None,
                    "amount": _number(cells[13]),
                    "currency": "CNY",
                    "status": "",
                    "account": str(cells[6] or ""),
                }
            if found and self._store is not None:
                self._store.save_freight_quotes(job.account_id, list(found.values()))
            job.message = f"已匹配 {len(found)}/{len(wanted)} 个 XM"
            page_no += 1

        self._shot(page, job.account_id)
        job.progress = 100
        missing = [xm for xm in wanted if xm not in found]
        return {
            "ok": True,
            "message": f"浩远头程查询完成：{len(found)}/{len(wanted)} 个 XM 查到金额",
            "quotes": list(found.values()),
            "missing": missing,
        }

    # ---------- rtb56（顺丰 / 义乌煊烊）----------

    @staticmethod
    def _rtb_logged_in(page: Any) -> bool:
        try:
            url = str(page.url or "")
        except Exception:  # noqa: BLE001
            return False
        if not url or "/login" in url.lower():
            return False
        try:
            text = page.evaluate(
                "() => document.body ? (document.body.innerText || '').slice(0, 4000) : ''"
            ) or ""
        except Exception:  # noqa: BLE001
            text = ""
        return "退出" in text or "注销" in text

    def _rtb_capture(self, page: Any, platform: str) -> bool:
        try:
            page.locator("#verifycode").first.screenshot(
                path=str(self.captcha_path(platform))
            )
            return True
        except Exception:  # noqa: BLE001
            return False

    def _wait_code(self, job: Job, timeout: float = RTB_CODE_TIMEOUT) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if job.code_event.wait(1):
                code = str(job.code_value or "").strip()
                job.code_value = ""
                job.code_event.clear()
                return code
            if job.status != "running":
                return ""
        return ""

    def rtb_login_worker(
        self, job: Job, page: Any, credentials: dict[str, str]
    ) -> dict[str, Any]:
        site = str(credentials.get("site_url") or "").strip()
        username = str(credentials.get("username") or "")
        password = str(credentials.get("password") or "")
        if not site:
            return {"ok": False, "message": "没有填写登录网址，请先在「发货平台绑定」补充"}
        job.step = "打开登录页"
        job.progress = 10
        page.goto(site, wait_until="domcontentloaded", timeout=60_000)
        self._safe_wait(page, 2500)
        if self._rtb_logged_in(page):
            self._shot(page, job.account_id)
            return {"ok": True, "message": "登录态有效，无需重新登录"}

        for attempt in range(1, 4):
            job.step = f"请输入图形验证码（第 {attempt} 次）"
            job.progress = 20 + attempt * 10
            try:
                page.fill("#txtUserName", username)
                page.fill("#txtPassword", password)
            except Exception:  # noqa: BLE001
                self._shot(page, job.account_id)
                return {"ok": False, "message": "登录页结构变化：找不到账号/密码输入框"}
            self._rtb_capture(page, job.account_id)
            job.need_code = True
            code = self._wait_code(job)
            job.need_code = False
            if not code:
                self._shot(page, job.account_id)
                return {"ok": False, "message": "等待验证码超时，请重新点「登录」"}
            job.step = "提交登录"
            job.progress = 60
            try:
                page.fill("#txtVerifyCode", code)
                page.locator("#btnSubmit").first.click(timeout=6000)
            except Exception:  # noqa: BLE001
                self._shot(page, job.account_id)
                return {"ok": False, "message": "提交登录失败：页面结构变化"}
            self._safe_wait(page, 5000)
            if self._rtb_logged_in(page):
                self._shot(page, job.account_id)
                job.progress = 100
                return {"ok": True, "message": "登录成功，登录态已保存在常驻标签页"}
            # 验证码错了会刷新图片，重试时重新截图给用户输入。
            try:
                page.locator("#verifycode").first.click(timeout=3000)
            except Exception:  # noqa: BLE001
                pass
            self._safe_wait(page, 800)
        self._shot(page, job.account_id)
        return {"ok": False, "message": "验证码连续 3 次未通过，请重新点「登录」"}

    def _rtb_open_list(self, page: Any, site: str, path: str) -> bool:
        """打开 rtb56 的费用列表页，按最近 3 年 + 每页 1000 条查询。"""
        base = site.rsplit("/", 1)[0] + "/"
        page.goto(base + path, wait_until="domcontentloaded", timeout=60_000)
        self._safe_wait(page, 2500)
        end = datetime.now()
        start = end - timedelta(days=RTB_LOOKBACK_DAYS)
        for selector, value in (
            ("#txtStartDate", f"{start:%Y-%m-%d}"),
            ("#txtEndDate", f"{end:%Y-%m-%d}"),
            ("#txtPageNum", "1000"),
        ):
            try:
                page.fill(selector, value)
                page.keyboard.press("Tab")
            except Exception:  # noqa: BLE001
                continue
        try:
            page.select_option("#ddlDateType", "A")
        except Exception:  # noqa: BLE001
            pass
        try:
            page.get_by_text("搜索", exact=True).first.click(timeout=6000)
        except Exception:  # noqa: BLE001
            return False
        self._safe_wait(page, 5000)
        return True

    def rtb_query_worker(
        self, job: Job, page: Any, xms: list[str]
    ) -> dict[str, Any]:
        wanted = list(dict.fromkeys(str(xm).strip() for xm in xms if str(xm).strip()))
        if not wanted:
            return {"ok": False, "message": "没有需要查询的 XM 单号"}
        wanted_set = set(wanted)

        job.step = "检查登录态"
        job.progress = 5
        credentials = (
            self._store.get_freight_credentials(job.account_id) if self._store else None
        ) or {}
        site = str(credentials.get("site_url") or "").strip()
        if not site:
            return {"ok": False, "message": "没有填写登录网址，请先在「发货平台绑定」补充"}
        try:
            page.goto(site, wait_until="domcontentloaded", timeout=60_000)
        except Exception as error:  # noqa: BLE001
            return {"ok": False, "message": f"打开发货平台失败：{error}"}
        self._safe_wait(page, 2500)
        if not self._rtb_logged_in(page):
            self._shot(page, job.account_id)
            return {
                "ok": False,
                "message": "登录已失效，请先在「发货平台绑定」点登录并输入图形验证码",
            }

        found: dict[str, dict[str, Any]] = {}

        job.step = "读取运费明细"
        job.progress = 20
        if self._rtb_open_list(page, site, RTB_FREIGHT_PAGE):
            rows = page.evaluate(RTB_ROWS_JS) or []
            for cells in rows:
                if len(cells) < 16 or not cells[0]:
                    continue
                match = RTB_XM_RE.search(str(cells[2]))
                if match is None or match.group(1) not in wanted_set:
                    continue
                xm = match.group(1)
                weight = RTB_WEIGHT_RE.search(str(cells[10]))
                track = RTB_TRACK_RE.search(str(cells[2]))
                found[xm] = {
                    "xm": xm,
                    "order_no": track.group(1) if track else "",
                    "weight": float(weight.group(1)) if weight else None,
                    "amount": _number(cells[15]),
                    "currency": "CNY",
                    "status": "已结算",
                    "account": str(cells[18] or ""),
                }
            if found and self._store is not None:
                self._store.save_freight_quotes(job.account_id, list(found.values()))
            job.message = f"运费明细已匹配 {len(found)}/{len(wanted)}"
        else:
            job.message = "运费明细页没打开成功"

        job.step = "读取未付款"
        job.progress = 60
        if self._rtb_open_list(page, site, RTB_NOPAY_PAGE):
            rows = page.evaluate(RTB_ROWS_JS) or []
            extra: dict[str, dict[str, Any]] = {}
            for cells in rows:
                if len(cells) < 10 or not cells[0]:
                    continue
                xm = str(cells[1] or "").strip()
                if xm not in wanted_set:
                    continue
                unpaid = _number(cells[9]) or 0.0
                status = "未付款" if unpaid > 0 else "已结算"
                if xm in found:
                    found[xm]["status"] = status
                    continue
                extra[xm] = {
                    "xm": xm,
                    "order_no": str(cells[2] or ""),
                    "weight": _number(cells[6]),
                    "amount": _number(cells[7]),
                    "currency": "CNY",
                    "status": status,
                    "account": str(cells[11] or ""),
                }
            found.update(extra)
            if extra and self._store is not None:
                self._store.save_freight_quotes(job.account_id, list(extra.values()))
            job.message = f"累计匹配 {len(found)}/{len(wanted)}"
        else:
            job.message = "未付款页没打开成功"

        self._shot(page, job.account_id)
        job.progress = 100
        missing = [xm for xm in wanted if xm not in found]
        return {
            "ok": True,
            "message": f"rtb56 头程查询完成：{len(found)}/{len(wanted)} 个 XM 查到金额",
            "quotes": list(found.values()),
            "missing": missing,
        }
