"""发货平台头程金额采集：无界 ERP + 浩远国际快递。

无界 ERP：登录 https://erp.wujie-erp.com/login，费用查询 /fees，按客户订单号（XM）批量查总金额。
浩远：登录 https://www.hy-express.com/，运费明细 /usercenter/fee/freight_list.aspx，
      按日期范围批量拉「参考单号（XM）+ 总费用」，本地匹配 XM。

无界 ERP 把登录态放在 sessionStorage（关掉浏览器就丢），所以这里为每个平台
常驻一个采集线程 + 浏览器标签页：登录成功一次后，后续查询直接在同一个标签页里做。
"""

from __future__ import annotations

import json
import math
import os
import queue
import re
import subprocess
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
# RTB 费用日期在搜索后会被重置为约一个月，按月窗口查询并逐窗验证。
RTB_WINDOW_DAYS = 31
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
TOTAL_RE = re.compile(r"共\s*(\d+)\s*(?:条|记录)")
# 翻页上限只用于防止死循环；超过即视为覆盖不完整，不当作成功。
MAX_PAGES = int(os.environ.get("COLLECTOR_FREIGHT_MAX_PAGES", "200"))
HY_XM_RE = re.compile(r"参考单号[：:]\s*([A-Za-z0-9\-]+)")
HY_WEIGHT_RE = re.compile(r"计费重[：:]\s*([\d.]+)")

HAO_ROWS_JS = r"""() => {
    const table = [...document.querySelectorAll('table')].find((t) => t.querySelectorAll('tbody tr').length > 1);
    if (!table) return { rows: [] };
    return { rows: [...table.querySelectorAll('tbody tr')].map((tr) =>
        [...tr.querySelectorAll('td')].map((td) =>
            (td.innerText || '').replace(/\s+/g, ' ').trim())) };
}"""


def _validate_dates(date_from: str | None, date_to: str | None, initialize: bool) -> None:
    if date_from is None and date_to is None and not initialize:
        return
    for value in (date_from, date_to):
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("日期范围必须同时提供 ISO YYYY-MM-DD 起止日期")
        datetime.strptime(value, "%Y-%m-%d")
    if date_from > date_to:
        raise ValueError("起始日期不能晚于结束日期")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _date_window(date_from: str | None, date_to: str | None,
                 lookback_days: int) -> tuple[datetime, datetime]:
    end = datetime.strptime(date_to, "%Y-%m-%d") if date_to else datetime.now()
    start = (datetime.strptime(date_from, "%Y-%m-%d") if date_from
             else end - timedelta(days=lookback_days))
    return start, end


def _split_windows(start: datetime, end: datetime,
                   max_days: int) -> list[tuple[datetime, datetime]]:
    windows: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=max_days - 1), end)
        windows.append((cursor, stop))
        cursor = stop + timedelta(days=1)
    return windows or [(start, end)]


def _number(text: str) -> float | None:
    match = NUMBER_RE.search(str(text if text is not None else ""))
    if match is None:
        return None
    try:
        value = float(match.group(0))
        return value if math.isfinite(value) else None
    except ValueError:
        return None


def _page_total(page: Any) -> int | None:
    """读取页面声明的总条数；读不到就返回 None，由调用方按无法证明覆盖处理。"""
    try:
        text = page.evaluate("() => document.body ? document.body.innerText : ''")
    except Exception:  # noqa: BLE001
        return None
    matches = TOTAL_RE.findall(str(text or ""))
    if not matches:
        return None
    try:
        return int(matches[-1])
    except ValueError:
        return None


def _click_next(page: Any) -> bool:
    """点下一页；没有可用的下一页控件时返回 False。"""
    for selector in (
        "a:has-text('下一页')",
        "button:has-text('下一页')",
        ".el-pagination .btn-next",
        "#btnNext",
    ):
        try:
            element = page.locator(selector).first
            if element.count() and element.is_visible() and element.is_enabled():
                element.click(timeout=5000)
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


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
        self._ocr_lock = threading.Lock()
        self._auto_login_path = self._dir / "rtb-auto-login.json"
        if self._auto_login_path.exists():
            self._auto_login_state = json.loads(self._auto_login_path.read_text("utf-8"))
        else:
            self._auto_login_state = {}

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
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        initialize: bool = False,
    ) -> dict[str, Any]:
        def done(account: str, result: dict[str, Any]) -> None:
            result.setdefault("coverage_complete", False)
            result.setdefault("date_from", date_from)
            result.setdefault("date_to", date_to)
            result.setdefault("message", "")
            if result.get("ok") and not result["coverage_complete"]:
                result["message"] = str(result["message"]) + "；覆盖未完成，不能判定全平台无结果或初始化成功"
            result["complete"] = bool(result.get("ok") and result["coverage_complete"])
            on_done(account, result)

        def worker(job: Job, page: Any) -> dict[str, Any]:
            try:
                _validate_dates(date_from, date_to, initialize)
                adapter = self._adapter(platform)
                target = {RTB: self.rtb_query_worker, HY: self.hy_query_worker}.get(
                    adapter, self.query_worker
                )
                return target(job, page, xms, date_from=date_from,
                              date_to=date_to, initialize=initialize)
            except Exception as error:
                return {"ok": False, "message": f"{type(error).__name__}: {error}"}

        return self._submit(platform, "query", worker, done)

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
            try:
                self._ensure_thread_locked(platform)
                self._queues[platform].put((job, worker, on_done))
                error = None
            except Exception as exc:
                self._threads.pop(platform, None)
                job.status = "failed"
                job.message = f"线程启动失败：{exc}"
                error = {"ok": False, "action": action, "message": job.message}
        if error is not None:
            on_done(platform, error)
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
        playwright = None
        context = None
        page = None
        while True:
            job, worker, on_done = self._queues[platform].get()
            try:
                if playwright is None:
                    from playwright.sync_api import sync_playwright

                    playwright = sync_playwright().start()
                if context is None or page is None or page.is_closed():
                    context = self._context(playwright, platform)
                    page = context.pages[0] if context.pages else context.new_page()
                result = worker(job, page)
                result.setdefault("action", job.action)
                job.status = "done" if result.get("ok") else "failed"
                job.message = str(result.get("message") or "")
                if result.get("ok"):
                    job.progress = 100
            except BaseException as error:  # noqa: BLE001 - 常驻线程不能因单次任务退出
                job.status = "failed"
                job.message = f"{type(error).__name__}: {error}"
                result = {"ok": False, "action": job.action, "message": job.message}
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
            try:
                on_done(platform, result)
            except Exception as error:
                job.status = "failed"
                job.message = f"结果回调失败：{error}"

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

    def _read_pages(
        self, job: Job, page: Any, title: str,
        read_page: Callable[[], dict[str, Any]], page_size: int,
    ) -> tuple[list[dict[str, Any]], bool, int | None, int]:
        """逐页读取当前结果；无法证明读完时返回覆盖不完整。"""
        total = _page_total(page)
        quotes: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        raw = 0
        for index in range(1, MAX_PAGES + 1):
            job.step = f"{title} 第 {index} 页"
            try:
                captured = read_page() or {}
            except Exception:  # noqa: BLE001
                return quotes, False, total, raw
            for quote in captured.get("quotes") or []:
                key = (str(quote.get("xm") or ""), str(quote.get("order_no") or ""))
                if key not in seen:
                    seen.add(key)
                    quotes.append(quote)
            raw += int(captured.get("raw") or 0)
            if total is not None and raw >= total:
                return quotes, True, total, raw
            if raw == 0:
                return quotes, total == 0, total, raw
            if not _click_next(page):
                # 没有下一页控件：只有总条数已满足或本页未占满每页条数才算读完。
                return quotes, (total is not None and raw >= total) or (total is None and raw < page_size), total, raw
            self._safe_wait(page, 1500)
        return quotes, False, total, raw

    def _wujie_set_page_size(self, page: Any, size: int = 100) -> int:
        """尽量把每页条数调到最大，减少分页轮数；调不到就保持默认。"""
        try:
            select = page.locator(".el-pagination .el-select").first
            if not select.count():
                return 0
            select.click(timeout=3000)
            option = page.locator(".el-select-dropdown__item", has_text=f"{size}条/页").last
            if not option.count():
                page.keyboard.press("Escape")
                return 0
            option.click(timeout=3000)
            self._safe_wait(page, 1500)
            if f"{size}条/页" in (page.inner_text(".el-pagination") or ""):
                return size
        except Exception:  # noqa: BLE001
            try:
                page.keyboard.press("Escape")
            except Exception:  # noqa: BLE001
                pass
        return 0

    def query_worker(
        self, job: Job, page: Any, xms: list[str], *,
        date_from: str | None = None, date_to: str | None = None,
        initialize: bool = False,
    ) -> dict[str, Any]:
        xms = list(dict.fromkeys(str(xm).strip() for xm in xms if str(xm).strip()))
        if not xms and not initialize:
            return {"ok": False, "coverage_complete": False, "message": "没有需要查询的 XM 单号"}
        try:
            _validate_dates(date_from, date_to, initialize)
        except ValueError as error:
            return {"ok": False, "coverage_complete": False, "message": str(error)}
        if initialize and not (date_from and date_to):
            return {"ok": False, "coverage_complete": False,
                    "message": "无界全量初始化必须提供日期范围"}

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

        if not self._apply_wujie_dates(page, date_from, date_to):
            self._shot(page, job.account_id)
            return {"ok": False, "coverage_complete": False,
                    "message": "无界费用查询未识别日期控件，未执行查询（不能声称日期已生效）"}

        def read_wujie_page(batch: list[str] | None) -> dict[str, Any]:
            data = page.evaluate(RESULTS_JS) or {}
            rows = data.get("rows") or []
            quotes: list[dict[str, Any]] = []
            for cells in rows:
                if len(cells) < 7:
                    continue
                xm = str(cells[2] or "").strip()
                if batch is not None and xm not in batch:
                    continue
                quotes.append({
                    "xm": xm,
                    "order_no": str(cells[1] or ""),
                    "weight": _number(cells[3]),
                    "amount": _number(cells[4]),
                    "currency": str(cells[5] or "").strip(),
                    "status": str(cells[6] or "").strip(),
                    "account": str(cells[7] or "") if len(cells) > 7 else "",
                })
            return {"quotes": quotes, "raw": len(rows)}

        if initialize:
            job.step = "全量读取费用查询"
            job.progress = 12
            try:
                box.click(timeout=4000)
                box.fill("")
                page.get_by_role("button", name="查询").first.click(timeout=6000)
            except Exception:  # noqa: BLE001
                return {"ok": False, "coverage_complete": False,
                        "message": "无界全量查询未能提交"}
            self._safe_wait(page, 2500)
            page_size = self._wujie_set_page_size(page) or 20
            quotes, complete, total, _raw = self._read_pages(
                job, page, "读取费用明细", lambda: read_wujie_page(None), page_size)
            unique: dict[str, dict[str, Any]] = {}
            for quote in quotes:
                unique.setdefault(quote["xm"], quote)
            # 全量查询返回 0 条通常是筛选条件被清空或查询未生效，不能当作初始化完成。
            if not unique:
                complete = False
            if unique and self._store is not None:
                self._store.save_freight_quotes(job.account_id, list(unique.values()))
            self._shot(page, job.account_id)
            job.progress = 100
            return {
                "ok": True, "coverage_complete": complete,
                "message": f"无界全量读取 {len(unique)} 个 XM（共 {total if total is not None else '未知'} 条）"
                           + ("" if complete else "，分页未读完"),
                "quotes": list(unique.values()), "missing": [],
            }

        batches = [xms[i : i + BATCH_SIZE] for i in range(0, len(xms), BATCH_SIZE)]
        total = len(batches)
        found: dict[str, dict[str, Any]] = {}
        complete = True
        for index, batch in enumerate(batches):
            job.step = f"查询头程 {index + 1}/{total}（{len(batch)} 个 XM）"
            job.progress = min(95, 12 + int(index / total * 84))
            try:
                box.click(timeout=4000)
                box.fill("")
                box.fill(" ".join(batch))
                page.get_by_role("button", name="查询").first.click(timeout=6000)
            except Exception:  # noqa: BLE001
                complete = False
                self._safe_wait(page, 1500)
                continue
            self._wait_results(page, batch)
            quotes, ok, _total, _raw = self._read_pages(
                job, page, "读取头程", lambda: read_wujie_page(batch), 20)
            complete = complete and ok
            for quote in quotes:
                found.setdefault(quote["xm"], quote)
            if found and self._store is not None:
                self._store.save_freight_quotes(job.account_id, list(found.values()))
            job.message = f"已查到 {len(found)}/{len(xms)} 个 XM 的头程金额"
            self._safe_wait(page, 700)

        self._shot(page, job.account_id)
        job.progress = 100
        missing = [xm for xm in xms if xm not in found]
        return {
            "ok": True, "coverage_complete": complete,
            "message": f"头程查询完成：{len(found)}/{len(xms)} 个 XM 查到金额"
                       + ("" if complete else "（分页未完整覆盖）"),
            "quotes": list(found.values()),
            "missing": missing,
        }

    def _apply_wujie_dates(
        self, page: Any, date_from: str | None, date_to: str | None
    ) -> bool:
        """把日期写入无界费用查询的日期控件，并读回验证；未生效返回 False。"""
        if not date_from and not date_to:
            return True
        if not (date_from and date_to):
            return False
        apply_js = r"""({from, to}) => {
            const inputs = [...document.querySelectorAll('input')];
            const ranged = inputs.filter((el) => el.classList.contains('el-range-input'));
            const target = ranged.length >= 2 ? ranged.slice(0, 2)
                : inputs.filter((el) => /日期|时间|date/i.test(
                    (el.placeholder || '') + (el.getAttribute('aria-label') || ''))).slice(0, 2);
            if (target.length < 2) return 0;
            target.forEach((el, i) => {
                el.removeAttribute('readonly');
                el.value = i === 0 ? from : to;
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new Event('blur', {bubbles: true}));
            });
            return target.length;
        }"""
        verify_js = r"""() => [...document.querySelectorAll('input')]
            .filter((el) => /^\d{4}-\d{2}-\d{2}$/.test(el.value || ''))
            .map((el) => el.value)"""
        try:
            if not page.evaluate(apply_js, {"from": date_from, "to": date_to}):
                return False
            values = page.evaluate(verify_js) or []
        except Exception:  # noqa: BLE001
            return False
        return date_from in values and date_to in values

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
        self, job: Job, page: Any, xms: list[str], *,
        date_from: str | None = None, date_to: str | None = None,
        initialize: bool = False,
    ) -> dict[str, Any]:
        wanted = list(dict.fromkeys(str(xm).strip() for xm in xms if str(xm).strip()))
        if not wanted and not initialize:
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

        _validate_dates(date_from, date_to, initialize)
        end = datetime.strptime(date_to, "%Y-%m-%d") if date_to else datetime.now()
        start = datetime.strptime(date_from, "%Y-%m-%d") if date_from else end - timedelta(days=HY_LOOKBACK_DAYS)
        found: dict[str, dict[str, Any]] = {}
        job.step = "读取运费明细"
        job.progress = 15
        url = (
            f"{HY_FEE_URL}?startDate={start:%Y-%m-%d}"
            f"&endDate={end:%Y-%m-%d}&dateType=A&page=1"
        )
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            self._safe_wait(page, 1500)
        except Exception:  # noqa: BLE001
            return {"ok": False, "coverage_complete": False,
                    "message": "浩远运费明细打不开，无法确认日期范围"}

        def read_hy_page() -> dict[str, Any]:
            rows = [
                row
                for row in (page.evaluate(HAO_ROWS_JS).get("rows") or [])
                if len(row) >= 14
            ]
            quotes: list[dict[str, Any]] = []
            for cells in rows:
                match = HY_XM_RE.search(str(cells[2]))
                if match is None:
                    continue
                xm = match.group(1)
                if not initialize and xm not in wanted_set:
                    continue
                weight = HY_WEIGHT_RE.search(str(cells[9]))
                quotes.append({
                    "xm": xm,
                    "order_no": str(cells[3] or ""),
                    "weight": float(weight.group(1)) if weight else None,
                    "amount": _number(cells[13]),
                    "currency": "CNY",
                    "status": "",
                    "account": str(cells[6] or ""),
                })
            return {"quotes": quotes, "raw": len(rows)}

        quotes, complete, total, raw = self._read_pages(job, page, "读取运费明细", read_hy_page, HY_PAGE_SIZE)
        if initialize and raw == 0:
            complete = False
        for quote in quotes:
            found.setdefault(quote["xm"], quote)
        if found and self._store is not None:
            self._store.save_freight_quotes(job.account_id, list(found.values()))
        job.message = f"已匹配 {len(found)}/{len(wanted)} 个 XM（共 {total if total is not None else '未知'} 条）"

        self._shot(page, job.account_id)
        job.progress = 100
        missing = [xm for xm in wanted if xm not in found]
        if not complete:
            return {
                "ok": True, "coverage_complete": False,
                "message": f"浩远分页未读完，覆盖不完整：已匹配 {len(found)}/{len(wanted)} 个 XM",
                "quotes": list(found.values()), "missing": missing,
            }
        return {
            "ok": True, "coverage_complete": True,
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

    def _rtb_auto_login(self, job: Job, page: Any,
                        credentials: dict[str, str]) -> dict[str, Any]:
        """每任务最多提交一次；限额跨进程重启保留，不循环刷新验证码。"""
        with self._ocr_lock:
            now = time.time()
            state = self._auto_login_state.setdefault(job.account_id, {})
            attempts = [t for t in state.get("attempts", []) if now - t < 86400]
            latest = max((max(s.get("attempts", [0]) or [0])
                          for s in self._auto_login_state.values()), default=0)
            if now - latest < 60:
                return {"ok": False, "message": "自动登录全局冷却中，请稍后或人工登录"}
            if (state.get("failures", 0) >= 2 or len(attempts) >= 8
                    or sum(now - t < 3600 for t in attempts) >= 2
                    or (attempts and now - attempts[-1] < 900)):
                return {"ok": False, "message": "自动登录已限流或熔断，请人工登录；不会刷新验证码重试"}
            # 在识别和提交前持久化占用额度，异常/重启也不能绕过限制。
            state["attempts"] = attempts + [now]
            state["failures"] = state.get("failures", 0) + 1
            self._auto_login_path.write_text(json.dumps(self._auto_login_state), "utf-8")
            try:
                image = page.locator("#verifycode").first.screenshot()
                # 独立解释器隔离 OCR 的 numpy/onnx 依赖，不改动其它服务环境。
                completed = subprocess.run(
                    [os.environ.get("COLLECTOR_OCR_PYTHON", str(self._dir.parent / ".ocr-venv" / "bin" / "python")),
                     "-c", "import sys,ddddocr; print(ddddocr.DdddOcr(show_ad=False).classification(sys.stdin.buffer.read()))"],
                    input=image, capture_output=True, timeout=45, check=True,
                )
                code = completed.stdout.decode("utf-8").strip()
                if not re.fullmatch(r"[A-Za-z0-9]{4,6}", code):
                    return {"ok": False, "message": "验证码识别结果不合法，已停止自动提交，请人工登录"}
                job.step = "自动识别验证码并登录（限一次）"
                page.fill("#txtUserName", credentials.get("username", ""))
                page.fill("#txtPassword", credentials.get("password", ""))
                page.fill("#txtVerifyCode", code)
                page.locator("#btnSubmit").first.click(timeout=6000)
                self._safe_wait(page, 5000)
                if not self._rtb_logged_in(page):
                    return {"ok": False, "message": "自动登录未成功，已停止重试，请人工登录"}
                state["failures"] = 0
                self._auto_login_path.write_text(json.dumps(self._auto_login_state), "utf-8")
                return {"ok": True, "message": "验证码自动登录成功"}
            except Exception as error:
                return {"ok": False, "message": f"自动登录停止（{type(error).__name__}），请人工登录"}

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
                with self._ocr_lock:
                    self._auto_login_state.setdefault(job.account_id, {})["failures"] = 0
                    self._auto_login_path.write_text(json.dumps(self._auto_login_state), "utf-8")
                return {"ok": True, "message": "登录成功，登录态已保存在常驻标签页"}
            # 验证码错了会刷新图片，重试时重新截图给用户输入。
            try:
                page.locator("#verifycode").first.click(timeout=3000)
            except Exception:  # noqa: BLE001
                pass
            self._safe_wait(page, 800)
        self._shot(page, job.account_id)
        return {"ok": False, "message": "验证码连续 3 次未通过，请重新点「登录」"}

    def _rtb_search_with_dates(self, page: Any, start: datetime,
                               end: datetime) -> bool:
        """按指定日期搜索并校验；页面控件 id 不同或搜索后重置时返回 False。"""
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
        # 平台可能在搜索时把日期重置为默认区间，必须在搜索后验证。
        try:
            values = page.evaluate(
                """() => [...document.querySelectorAll('input')]
                    .map((el) => el.value || '')
                    .filter((v) => /^\\d{4}-\\d{2}-\\d{2}$/.test(v))"""
            ) or []
        except Exception:  # noqa: BLE001
            return False
        return f"{start:%Y-%m-%d}" in values and f"{end:%Y-%m-%d}" in values

    def _rtb_apply_dates_fallback(self, page: Any, start: datetime,
                                  end: datetime) -> bool:
        """备用方案：按页面上的日期输入框顺序写入起止日期，再次搜索。"""
        script = r"""({from, to}) => {
            const inputs = [...document.querySelectorAll('input')];
            const dateLike = inputs.filter((el) => {
                const v = el.value || '';
                return /^\d{4}-\d{2}-\d{2}$/.test(v)
                    || /日期|date/i.test((el.placeholder || '') + (el.className || ''));
            }).slice(0, 2);
            if (dateLike.length < 2) return 0;
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            dateLike.forEach((el, i) => {
                setter.call(el, i === 0 ? from : to);
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new Event('blur', {bubbles: true}));
            });
            return dateLike.length;
        }"""
        try:
            if not page.evaluate(script, {"from": f"{start:%Y-%m-%d}",
                                          "to": f"{end:%Y-%m-%d}"}):
                return False
            page.get_by_text("搜索", exact=True).first.click(timeout=6000)
        except Exception:  # noqa: BLE001
            return False
        self._safe_wait(page, 5000)
        try:
            values = page.evaluate(
                """() => [...document.querySelectorAll('input')]
                    .map((el) => el.value || '')
                    .filter((v) => /^\\d{4}-\\d{2}-\\d{2}$/.test(v))"""
            ) or []
        except Exception:  # noqa: BLE001
            return False
        return f"{start:%Y-%m-%d}" in values and f"{end:%Y-%m-%d}" in values

    def _rtb_open_list(self, page: Any, site: str, path: str,
                       start: datetime, end: datetime) -> bool:
        """应用一个日期窗口并搜索；搜索后读回日期，未生效返回 False。"""
        base = site.rsplit("/", 1)[0] + "/"
        page.goto(base + path, wait_until="domcontentloaded", timeout=60_000)
        self._safe_wait(page, 2500)
        if self._rtb_search_with_dates(page, start, end):
            return True
        return self._rtb_apply_dates_fallback(page, start, end)

    def rtb_query_worker(
        self, job: Job, page: Any, xms: list[str], *,
        date_from: str | None = None, date_to: str | None = None,
        initialize: bool = False,
    ) -> dict[str, Any]:
        wanted = list(dict.fromkeys(str(xm).strip() for xm in xms if str(xm).strip()))
        if not wanted and not initialize:
            return {"ok": False, "message": "没有需要查询的 XM 单号"}
        try:
            _validate_dates(date_from, date_to, initialize)
        except ValueError as error:
            return {"ok": False, "coverage_complete": False, "message": str(error)}
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
            login = self._rtb_auto_login(job, page, credentials)
            if not login.get("ok"):
                self._shot(page, job.account_id)
                return login

        start, end = _date_window(date_from, date_to, RTB_LOOKBACK_DAYS)
        windows = _split_windows(start, end, RTB_WINDOW_DAYS)
        found: dict[str, dict[str, Any]] = {}
        complete = True
        freight_total = nopay_total = None
        freight_raw = nopay_raw = 0
        freight_ok = nopay_ok = 0

        def read_rtb_freight() -> dict[str, Any]:
            rows = page.evaluate(RTB_ROWS_JS) or []
            quotes: list[dict[str, Any]] = []
            for cells in rows:
                if len(cells) < 16 or not cells[0]:
                    continue
                match = RTB_XM_RE.search(str(cells[2]))
                if match is None or (not initialize and match.group(1) not in wanted_set):
                    continue
                weight = RTB_WEIGHT_RE.search(str(cells[10]))
                track = RTB_TRACK_RE.search(str(cells[2]))
                quotes.append({
                    "xm": match.group(1),
                    "order_no": track.group(1) if track else "",
                    "weight": float(weight.group(1)) if weight else None,
                    "amount": _number(cells[15]),
                    "currency": "CNY",
                    "status": "已结算",
                    "account": str(cells[18] or ""),
                })
            return {"quotes": quotes, "raw": len(rows)}

        def read_rtb_nopay() -> dict[str, Any]:
            rows = page.evaluate(RTB_ROWS_JS) or []
            quotes: list[dict[str, Any]] = []
            for cells in rows:
                if len(cells) < 10 or not cells[0]:
                    continue
                xm = str(cells[1] or "").strip()
                if not initialize and xm not in wanted_set:
                    continue
                unpaid = _number(cells[9]) or 0.0
                quotes.append({
                    "xm": xm,
                    "order_no": str(cells[2] or ""),
                    "weight": _number(cells[6]),
                    "amount": _number(cells[7]),
                    "currency": "CNY",
                    "status": "未付款" if unpaid > 0 else "已结算",
                    "account": str(cells[11] or ""),
                })
            return {"quotes": quotes, "raw": len(rows)}

        for index, (w_start, w_end) in enumerate(windows, 1):
            job.step = f"读取运费明细 {index}/{len(windows)}"
            job.progress = min(50, 15 + int(index / len(windows) * 35))
            if not self._rtb_open_list(page, site, RTB_FREIGHT_PAGE, w_start, w_end):
                complete = False
                job.message = f"运费明细日期未生效：{w_start:%Y-%m-%d}~{w_end:%Y-%m-%d}"
                continue
            quotes, ok, freight_total, raw = self._read_pages(
                job, page, "读取运费明细", read_rtb_freight, 1000)
            complete = complete and ok
            freight_ok += 1 if ok else 0
            freight_raw += raw
            for quote in quotes:
                found.setdefault(quote["xm"], quote)
            if quotes and self._store is not None:
                self._store.save_freight_quotes(job.account_id, list(found.values()))
            job.message = (f"运费明细已匹配 {len(found)}/{len(wanted)}"
                           f"（已读 {freight_raw} 行，平台总数 "
                           f"{freight_total if freight_total is not None else '未识别'}）")

        for index, (w_start, w_end) in enumerate(windows, 1):
            job.step = f"读取未付款 {index}/{len(windows)}"
            job.progress = min(90, 55 + int(index / len(windows) * 35))
            if not self._rtb_open_list(page, site, RTB_NOPAY_PAGE, w_start, w_end):
                complete = False
                job.message = f"未付款日期未生效：{w_start:%Y-%m-%d}~{w_end:%Y-%m-%d}"
                continue
            extra, ok, nopay_total, raw = self._read_pages(
                job, page, "读取未付款", read_rtb_nopay, 1000)
            complete = complete and ok
            nopay_ok += 1 if ok else 0
            nopay_raw += raw
            merged: dict[str, dict[str, Any]] = {}
            for quote in extra:
                if quote["xm"] in found:
                    found[quote["xm"]]["status"] = quote["status"]
                else:
                    merged.setdefault(quote["xm"], quote)
            found.update(merged)
            if merged and self._store is not None:
                self._store.save_freight_quotes(job.account_id, list(merged.values()))
            job.message = f"累计匹配 {len(found)}/{len(wanted)}"

        self._shot(page, job.account_id)
        job.progress = 100
        if initialize and freight_raw == 0:
            complete = False
        missing = [xm for xm in wanted if xm not in found]
        coverage_note = ""
        if not complete:
            coverage_note = (f"（日期窗口 {freight_ok}/{len(windows)} 运费、"
                             f"{nopay_ok}/{len(windows)} 未付款生效；"
                             f"运费已读 {freight_raw} 行/"
                             f"总数 {freight_total if freight_total is not None else '未识别'}，"
                             f"未付款已读 {nopay_raw} 行/"
                             f"总数 {nopay_total if nopay_total is not None else '未识别'}）")
        return {
            "ok": True,
            "coverage_complete": complete,
            "message": f"rtb56 头程查询完成：{len(found)}/{len(wanted)} 个 XM 查到金额" + coverage_note,
            "quotes": list(found.values()),
            "missing": missing,
        }
