"""页面 / 弹窗探针。

把见过的页面记成「指纹 + 已知处理方式」，下次出现同样页面时直接按记录走，
不再逐次重新摸索。指纹只取稳定信息（域名 + 路径 + 标题 + 可见按钮 + 输入框），
丢掉查询参数，避免 traceId 之类每次都变导致同一页面被当成新页面。

记录里还会标 ``needs_human``：该页面上次是管理员在实时画面手动处理掉的，
之后遇到就直接把控制权交回管理员，自动化不再尝试点击。
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(text: Any, limit: int = 40) -> str:
    return " ".join(str(text or "").split())[:limit]


BUTTONS_JS = """() => Array.from(document.querySelectorAll('button'))
    .slice(0, 12)
    .map((b) => (b.innerText || '').trim().slice(0, 20))
    .filter(Boolean)"""

INPUTS_JS = """() => Array.from(document.querySelectorAll('input'))
    .slice(0, 8)
    .map((i) => String(i.type || 'text'))"""


class PageBook:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, Any]] = self._load()

    # ---------- 存取 ----------

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self._path.read_text("utf-8"))
        except Exception:  # noqa: BLE001 - 记录文件损坏不该影响采集
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=1), "utf-8"
            )
        except Exception:  # noqa: BLE001
            pass

    # ---------- 指纹 ----------

    @staticmethod
    def fingerprint(page: Any) -> dict[str, Any]:
        url = ""
        title = ""
        buttons: list[str] = []
        inputs: list[str] = []
        try:
            url = str(page.url or "")
        except Exception:  # noqa: BLE001
            pass
        parts = urlsplit(url)
        try:
            title = _norm(page.title())
        except Exception:  # noqa: BLE001
            pass
        try:
            buttons = list(page.evaluate(BUTTONS_JS) or [])
        except Exception:  # noqa: BLE001
            buttons = []
        try:
            inputs = list(page.evaluate(INPUTS_JS) or [])
        except Exception:  # noqa: BLE001
            inputs = []
        return {
            "host": parts.hostname or "",
            "path": (parts.path or "/").rstrip("/") or "/",
            "title": title,
            "buttons": sorted({_norm(item, 20) for item in buttons if _norm(item, 20)}),
            "inputs": sorted({str(item) for item in inputs}),
        }

    @staticmethod
    def key_of(fp: dict[str, Any]) -> str:
        raw = json.dumps(fp, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def key(self, page: Any) -> str:
        return self.key_of(self.fingerprint(page))

    # ---------- 对外 ----------

    def observe(self, page: Any) -> tuple[str, dict[str, Any], bool]:
        """登记一次页面访问，返回 (指纹key, 记录, 是否第一次见)。"""
        fp = self.fingerprint(page)
        key = self.key_of(fp)
        with self._lock:
            entry = self._data.get(key)
            is_new = entry is None
            if entry is None:
                entry = {
                    **fp,
                    "kind": "",
                    "needs_human": False,
                    "visits": 0,
                    "first_seen": _now(),
                    "last_seen": "",
                    "actions": {},
                }
                self._data[key] = entry
            entry["visits"] = int(entry.get("visits") or 0) + 1
            entry["last_seen"] = _now()
            self._save()
            return key, dict(entry), is_new

    def remember(
        self,
        key: str,
        *,
        kind: str = "",
        action: str = "",
        ok: bool = True,
        needs_human: bool | None = None,
    ) -> None:
        """把页面归类 / 记录一次动作结果。"""
        with self._lock:
            entry = self._data.setdefault(key, {"actions": {}})
            if kind:
                entry["kind"] = kind
            if needs_human is not None:
                entry["needs_human"] = bool(needs_human)
            if action:
                actions = entry.setdefault("actions", {})
                row = actions.setdefault(action, {"ok": 0, "fail": 0, "updated_at": ""})
                field = "ok" if ok else "fail"
                row[field] = int(row.get(field) or 0) + 1
                row["updated_at"] = _now()
            entry["last_seen"] = _now()
            self._save()

    def best_action(self, entry: dict[str, Any]) -> str:
        actions = entry.get("actions") or {}
        ranked = sorted(
            actions.items(),
            key=lambda item: (int((item[1] or {}).get("ok") or 0), -int((item[1] or {}).get("fail") or 0)),
            reverse=True,
        )
        for name, row in ranked:
            if int((row or {}).get("ok") or 0) > 0:
                return name
        return ""

    def summary(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {"key": key, **entry}
                for key, entry in sorted(
                    self._data.items(),
                    key=lambda item: str(item[1].get("last_seen") or ""),
                    reverse=True,
                )
            ]
