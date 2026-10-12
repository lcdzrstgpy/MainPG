from __future__ import annotations

import logging
import threading
from typing import Any, Callable
from urllib.parse import quote

import httpx

from ..config import is_ip_literal_host
from .repository import MessagesRepository

logger = logging.getLogger("wh_local.messages")


def _lookup(provider: Callable[[], str] | None) -> str:
    """调用身份提供器取值；查询失败一律退化为空串，不影响同步主流程。"""
    try:
        return (provider() or "").strip() if provider is not None else ""
    except Exception:
        return ""


class AnnouncementSyncService:
    """从公告发布后台拉取公告并写入本地消息表。

    服务器不可达时静默降级（仅记录日志），不影响工作台任何功能。
    定向发送：通过 account_id_provider 提供当前登录账号，同步时带上
    ``?account_id=``，后台只返回全员公告 + 发给该账号的定向公告。

    ⚠️ ``account_id`` 由邮箱哈希推导、可被伪造（详见安全审计），
    因此同时带上 ``x-auth-token``（远端会话令牌）作为**真正身份**；
    后台按令牌解析账号，令牌缺失/无效时只给全员公告。``?account_id=`` 仅
    保留给未升级的旧后台，新版后台会忽略它。
    """

    def __init__(
        self,
        repository: MessagesRepository,
        base_url: str,
        *,
        interval_seconds: int = 300,
        account_id_provider: Callable[[], str] | None = None,
        auth_token_provider: Callable[[], str] | None = None,
    ) -> None:
        self.repository = repository
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.interval_seconds = interval_seconds
        self.account_id_provider = account_id_provider
        self.auth_token_provider = auth_token_provider
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def configured(self) -> bool:
        return bool(self.base_url)

    def _request_kwargs(self) -> dict[str, Any]:
        # IP-direct connections to a test/staging host cannot match the public
        # certificate's hostname; skip verification only for bare IP literals.
        if is_ip_literal_host(self.base_url):
            return {"timeout": 10, "verify": False}
        return {"timeout": 10}

    def _auth_headers(self, auth_token: str | None = None) -> dict[str, str]:
        """构造身份头。显式传入的令牌优先（请求级同步可精确定位当前会话）。"""
        token = (auth_token or "").strip() or _lookup(self.auth_token_provider)
        return {"x-auth-token": token} if token else {}

    def sync_once(self, auth_token: str | None = None) -> int:
        """执行一次同步，返回新增消息数；失败返回 0。

        ``auth_token`` 为调用方已知的远端会话令牌（登录/进工作台这类请求级
        同步可精确带上）；不传则回退 ``auth_token_provider``，供后台定时线程
        在无请求上下文时使用。
        """
        if not self.configured():
            return 0
        url = f"{self.base_url}/api/announcements/public"
        headers = self._auth_headers(auth_token)
        account_id = _lookup(self.account_id_provider)
        if account_id:
            url += f"?account_id={quote(account_id)}"
        try:
            response = httpx.get(url, headers=headers, **self._request_kwargs())
            response.raise_for_status()
            payload = response.json()
            items = payload.get("announcements") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                logger.warning("announcement sync: unexpected payload from %s", url)
                return 0
            new_count = self.repository.upsert_server_announcements(items)
            # 撤回：服务器返回完整在线列表时，把已下线/已删除的本地消息一并移除。
            # id 异常（非数字）的条目单独跳过，避免单个坏数据让整轮撤回清理被跳过。
            active_ids: list[int] = []
            for item in items:
                try:
                    active_ids.append(int(item.get("id") or 0))
                except (TypeError, ValueError):
                    logger.warning("announcement sync: bad server id %r", item.get("id"))
            # 撤回只在**带身份**的那一轮做：不带令牌时服务端只返回全员公告，定向公告
            # 天然缺席，按它撤回会把用户已缓存的定向公告当成"已下架"误删。
            # （远端令牌只存在进程内存里，所以每次重启后的第一轮必然是匿名的。）
            if headers.get("x-auth-token"):
                self.repository.prune_retracted(active_ids)
            else:
                logger.info("announcement sync: anonymous pass, prune skipped")
            self._sync_pending_images(headers)
            return new_count
        except Exception as exc:  # 离线/服务器未就绪：静默降级
            logger.info("announcement sync unavailable (%s): %s", url, exc)
            return 0

    def _sync_pending_images(self, headers: dict[str, str] | None = None) -> None:
        """按需拉取带图公告的图片本体，存本地缓存供弹窗直接读取。

        列表接口只回 image_count/image_rev，避免每轮同步都搬运 base64；
        这里只补本地缺图的公告（每轮最多 10 条），失败静默跳过下次再试。
        带上同一份身份头：定向公告的图片后台同样按身份校验。
        """
        auth_headers = dict(headers or {}) or self._auth_headers()
        try:
            pending = self.repository.messages_missing_images()
        except Exception:
            logger.exception("announcement images: scan pending failed")
            return
        for row in pending:
            server_id = int(row.get("server_id") or 0)
            if server_id <= 0:
                continue
            try:
                response = httpx.get(
                    f"{self.base_url}/api/announcements/{server_id}/images",
                    headers=auth_headers,
                    **self._request_kwargs(),
                )
                response.raise_for_status()
                payload = response.json()
                images = payload.get("images") if isinstance(payload, dict) else None
                if not isinstance(images, list):
                    continue
                image_rev = int(payload.get("image_rev") or row.get("image_rev") or 0)
                self.repository.save_message_images(server_id, image_rev, images)
            except Exception as exc:  # 单条失败不影响其余公告
                logger.info("announcement images unavailable (%s): %s", server_id, exc)

    def start(self) -> None:
        if self._thread is not None or not self.configured():
            return
        self._stop.clear()

        def _run() -> None:
            while not self._stop.is_set():
                try:
                    self.sync_once()
                except Exception:
                    logger.exception("announcement sync crashed")
                self._stop.wait(self.interval_seconds)

        self._thread = threading.Thread(
            target=_run, name="announcement-sync", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None


class FeedbackReplySyncService:
    """从后台拉取管理员对用户反馈的回复，写入本地消息表（kind=feedback_reply）。

    与 AnnouncementSyncService 同构，仅拉取端点与消息类型不同：
    - 端点：{base_url}/api/feedback-replies/public
    - kind：feedback_reply（前端据此在消息中心做轻微样式区分）

    同样带 ``x-auth-token``：反馈回复是发给具体账号的，后台按令牌解析身份。
    """

    def __init__(
        self,
        repository: MessagesRepository,
        base_url: str,
        *,
        interval_seconds: int = 180,
        account_id_provider: Callable[[], str] | None = None,
        auth_token_provider: Callable[[], str] | None = None,
    ) -> None:
        self.repository = repository
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.interval_seconds = interval_seconds
        self.account_id_provider = account_id_provider
        self.auth_token_provider = auth_token_provider
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def configured(self) -> bool:
        return bool(self.base_url)

    def _auth_headers(self, auth_token: str | None = None) -> dict[str, str]:
        token = (auth_token or "").strip() or _lookup(self.auth_token_provider)
        return {"x-auth-token": token} if token else {}

    def sync_once(self, auth_token: str | None = None) -> int:
        """执行一次同步，返回新增消息数；失败返回 0。"""
        if not self.configured():
            return 0
        url = f"{self.base_url}/api/feedback-replies/public"
        headers = self._auth_headers(auth_token)
        account_id = _lookup(self.account_id_provider)
        if account_id:
            url += f"?account_id={quote(account_id)}"
        try:
            request_kwargs = {"timeout": 10, "verify": False} if is_ip_literal_host(self.base_url) else {"timeout": 10}
            response = httpx.get(url, headers=headers, **request_kwargs)
            response.raise_for_status()
            payload = response.json()
            items = payload.get("announcements") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                logger.warning("feedback reply sync: unexpected payload from %s", url)
                return 0
            # 反馈回复以 feedback_replies 的 id 作为 server_id，加 10 亿偏移
            # 与公告（announcements.id）共享同一 messages 表但不冲突。
            reply_items = []
            for item in items:
                try:
                    reply_items.append(
                        {**item, "id": int(item.get("id") or 0) + 1_000_000_000}
                    )
                except (TypeError, ValueError):
                    # 与公告同步保持一致：坏 id 单条跳过，不让一条坏数据废掉整轮同步。
                    logger.warning("feedback reply sync: bad server id %r", item.get("id"))
            new_count = self.repository.upsert_server_announcements(
                reply_items, kind="feedback_reply"
            )
            # 撤回：服务器已删除的反馈回复，本地对应消息一并移除。
            # 同样要求带身份：匿名轮该接口返回的是合法空列表，按它撤回会清空整类消息。
            active_ids = [int(item.get("id") or 0) for item in reply_items]
            if headers.get("x-auth-token"):
                self.repository.prune_retracted(active_ids, kind="feedback_reply")
            else:
                logger.info("feedback reply sync: anonymous pass, prune skipped")
            return new_count
        except Exception as exc:  # 离线/服务器未就绪：静默降级
            logger.info("feedback reply sync unavailable (%s): %s", url, exc)
            return 0

    def start(self) -> None:
        if self._thread is not None or not self.configured():
            return
        self._stop.clear()

        def _run() -> None:
            while not self._stop.is_set():
                try:
                    self.sync_once()
                except Exception:
                    logger.exception("feedback reply sync crashed")
                self._stop.wait(self.interval_seconds)

        self._thread = threading.Thread(
            target=_run, name="feedback-reply-sync", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None
