"""多账号自动轮换。

风控前提：同一出口 IP 同时开多个店铺账号容易触发关联判定，
所以这里严格串行——同一时刻只跑一个账号，账号之间留随机间隔。

每个账号的一轮动作：
1. 先直接采集；登录态失效会返回 need_login；
2. 需要时自动登录（弹验证码就暂停等人工填，超时则跳过该账号）；
3. 登录成功后再采一次；
4. 记录结果，随机等待后处理下一个。
"""

from __future__ import annotations

import random
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

DEFAULTS = {
    "enabled": False,
    "interval_minutes": 120,
    "gap_min_seconds": 40,
    "gap_max_seconds": 90,
    "retry_cooldown_minutes": 30,
    # 登录态失效时是否自动重新登录。默认开启：多数情况下重新提交账密即可，
    # 只有触发风控才需要人工填验证码（那时页面会亮红提示）。
    "auto_relogin": True,
    # 每日时间窗（北京时间）：开启后每天在窗口内随机挑一个时刻跑一轮，
    # 避免固定时刻被平台识别，也避开白天人工使用高峰。
    "window_enabled": True,
    "window_start_hour": 4,
    "window_end_hour": 7,
}

BEIJING = timezone(timedelta(hours=8))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str:
    return value.isoformat(timespec="seconds") if value else ""


class AutoRotator:
    def __init__(
        self,
        *,
        store: Any,
        runner: Any,
        on_done: Callable[[str, dict[str, Any]], None],
        boot_delay_seconds: int = 20,
    ) -> None:
        self._store = store
        self._runner = runner
        self._on_done = on_done
        self._boot_delay = max(0, int(boot_delay_seconds))
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False
        self._manual_round = threading.Event()
        self._current_account = ""
        self._current_step = ""
        self._last_results: list[dict[str, Any]] = []
        self._last_round_at: datetime | None = None
        self._next_run_at: datetime | None = None
        self._cooldown: dict[str, datetime] = {}
        saved = store.settings().get("rotation") or {}
        self._settings = {**DEFAULTS, **{k: saved[k] for k in DEFAULTS if k in saved}}

    # ---------- 配置 ----------

    def settings(self) -> dict[str, Any]:
        return dict(self._settings)

    def update(self, values: dict[str, Any]) -> dict[str, Any]:
        was_enabled = bool(self._settings.get("enabled"))
        next_values = dict(self._settings)
        if "enabled" in values:
            next_values["enabled"] = bool(values["enabled"])
        if "interval_minutes" in values:
            next_values["interval_minutes"] = max(5, min(1440, int(values["interval_minutes"])))
        if "gap_min_seconds" in values:
            next_values["gap_min_seconds"] = max(5, min(600, int(values["gap_min_seconds"])))
        if "gap_max_seconds" in values:
            next_values["gap_max_seconds"] = max(
                next_values["gap_min_seconds"], min(1800, int(values["gap_max_seconds"]))
            )
        if "retry_cooldown_minutes" in values:
            next_values["retry_cooldown_minutes"] = max(
                5, min(720, int(values["retry_cooldown_minutes"]))
            )
        if "auto_relogin" in values:
            next_values["auto_relogin"] = bool(values["auto_relogin"])
        if "window_enabled" in values:
            next_values["window_enabled"] = bool(values["window_enabled"])
        if "window_start_hour" in values:
            next_values["window_start_hour"] = max(0, min(23, int(values["window_start_hour"])))
        if "window_end_hour" in values:
            next_values["window_end_hour"] = max(1, min(24, int(values["window_end_hour"])))
        if next_values["window_start_hour"] >= next_values["window_end_hour"]:
            next_values["window_start_hour"] = 4
            next_values["window_end_hour"] = 7
        window_changed = any(
            key in values for key in ("window_enabled", "window_start_hour", "window_end_hour")
        )
        self._settings = next_values
        self._store.save_settings({"rotation": next_values})
        if not next_values["enabled"]:
            self._next_run_at = None
        elif not was_enabled or window_changed:
            # 只有「从关到开」或改了时间窗才重新排期；仅改账号间隔不重置。
            self._next_run_at = self._schedule_next(_now(), after_round=False)
        return self.settings()

    # ---------- 状态 ----------

    def state(self) -> dict[str, Any]:
        return {
            "settings": self.settings(),
            "running": self._running,
            "current_account": self._current_account,
            "current_step": self._current_step,
            "last_round_at": _iso(self._last_round_at),
            "next_run_at": _iso(self._next_run_at),
            "last_results": self._last_results[-20:],
        }

    # ---------- 生命周期 ----------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name="collector-rotator", daemon=True
        )
        self._thread.start()

    def trigger_now(self) -> dict[str, Any]:
        self._manual_round.set()
        return self.state()

    def _schedule_next(self, now: datetime, *, after_round: bool) -> datetime:
        """计算下一次运行的 UTC 时刻。

        开启每日时间窗时，把下一轮安排在北京时间 [start, end) 内的随机分钟；
        ``after_round=True`` 表示一轮刚跑完，直接排到下一个窗口，避免同一天跑两轮。
        """
        start_hour = int(self._settings.get("window_start_hour") or 4)
        end_hour = int(self._settings.get("window_end_hour") or 7)
        if not self._settings.get("window_enabled"):
            minutes = int(self._settings.get("interval_minutes") or 1440)
            return now + timedelta(minutes=minutes)

        local = now.astimezone(BEIJING)
        start = local.replace(hour=start_hour, minute=0, second=0, microsecond=0)
        end = local.replace(hour=end_hour, minute=0, second=0, microsecond=0)
        span = max(1, int((end - start).total_seconds() // 60) - 15)

        if after_round or local >= end:
            if local < start:
                target = start
            else:
                target = start + timedelta(days=1)
            target += timedelta(minutes=random.randint(0, span))
        elif local < start:
            target = start + timedelta(minutes=random.randint(0, span))
        else:
            # 正好在窗口内：稍等几分钟就开跑，不占用整个窗口。
            target = min(local + timedelta(minutes=random.randint(1, 5)), end - timedelta(minutes=1))
        return target.astimezone(timezone.utc)

    def _loop(self) -> None:
        if self._boot_delay:
            time.sleep(self._boot_delay)
        if self._settings.get("enabled"):
            self._next_run_at = self._schedule_next(_now(), after_round=False)
        while True:
            try:
                forced = self._manual_round.is_set()
                enabled = bool(self._settings.get("enabled"))
                if not forced and not enabled:
                    self._next_run_at = None
                    time.sleep(5)
                    continue
                if not forced and self._next_run_at and _now() < self._next_run_at:
                    time.sleep(5)
                    continue
                self._manual_round.clear()
                self.run_round()
            except Exception as error:  # noqa: BLE001 - 轮换线程不允许退出
                self._current_step = f"轮换异常：{error}"
                time.sleep(30)

    # ---------- 单轮 ----------

    def run_round(self) -> list[dict[str, Any]]:
        self._running = True
        self._last_results = []
        started = _now()
        accounts = self._store.list_accounts()
        # 自动轮换中途关掉开关就停下；手动触发的那一轮不受开关影响。
        respect_enabled = bool(self._settings.get("enabled"))
        try:
            for account in accounts:
                if respect_enabled and not self._settings.get("enabled"):
                    break
                account_id = str(account.get("id") or "")
                label = account.get("shop_name") or account.get("alias") or account.get("phone_tail")
                credentials = self._store.get_credentials(account_id) or {}
                if not credentials.get("phone") or not credentials.get("password"):
                    self._record(account_id, label, "skipped", "缺少账号密码")
                    continue
                if self._in_cooldown(account_id):
                    self._record(account_id, label, "cooldown", "失败冷却中")
                    continue

                self._current_account = account_id
                # 不再单独探活：探活+采集要开两次浏览器，账号的登录态更容易被踢。
                # 直接采集，页面判定未登录时会返回 need_login，再走重新登录。
                self._current_step = f"{label}：采集"
                result = self._runner.run_sync_capture(account_id, self._on_done)

                # 订单多的时候一轮采不完详情：紧接着补，最多 2 轮，避免占着账号太久。
                passes = 0
                while (
                    result.get("ok")
                    and int(result.get("pending_details") or 0) > 0
                    and passes < 2
                    and (not respect_enabled or self._settings.get("enabled"))
                ):
                    passes += 1
                    self._current_step = f"{label}：补详情（第 {passes} 轮）"
                    time.sleep(5)
                    result = self._runner.run_sync_capture(account_id, self._on_done)
                    if result.get("need_login"):
                        break

                if result.get("need_login"):
                    if not self._settings.get("auto_relogin"):
                        self._record(
                            account_id,
                            label,
                            "need_login",
                            "登录态失效，请手动点「登录」",
                        )
                        continue
                    self._current_step = f"{label}：登录"
                    login_result = self._runner.run_sync_login(
                        account_id, credentials, self._on_done
                    )
                    if not login_result.get("ok"):
                        self._record(
                            account_id,
                            label,
                            "login_failed",
                            str(login_result.get("message") or "登录失败"),
                        )
                        self._start_cooldown(account_id)
                        continue
                    self._current_step = f"{label}：登录后采集"
                    result = self._runner.run_sync_capture(account_id, self._on_done)

                if result.get("ok"):
                    count = len(result.get("items") or [])
                    pending = int(result.get("pending_details") or 0)
                    note = f"{count} 条订单" + (f"，{pending} 单详情待补" if pending else "")
                    self._record(account_id, label, "ok", note)
                else:
                    self._record(
                        account_id,
                        label,
                        "failed",
                        str(result.get("message") or "采集失败"),
                    )
                    self._start_cooldown(account_id)

                gap = random.uniform(
                    float(self._settings.get("gap_min_seconds") or 40),
                    float(self._settings.get("gap_max_seconds") or 90),
                )
                self._current_step = f"等待 {int(gap)} 秒后切换下一个账号"
                time.sleep(gap)
        finally:
            self._running = False
            self._current_account = ""
            self._current_step = ""
            self._last_round_at = started
            if self._settings.get("enabled"):
                self._next_run_at = self._schedule_next(_now(), after_round=True)
            else:
                self._next_run_at = None
            self._manual_round.clear()
        return self._last_results

    def _record(self, account_id: str, label: Any, status: str, message: str) -> None:
        self._last_results.append(
            {
                "account_id": account_id,
                "label": str(label or ""),
                "status": status,
                "message": message,
                "at": _iso(_now()),
            }
        )

    def _in_cooldown(self, account_id: str) -> bool:
        until = self._cooldown.get(account_id)
        return bool(until and until > _now())

    def _start_cooldown(self, account_id: str) -> None:
        minutes = int(self._settings.get("retry_cooldown_minutes") or 30)
        self._cooldown[account_id] = _now() + timedelta(minutes=minutes)
