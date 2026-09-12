import { useCallback, useEffect, useRef, useState } from "react";

import {
  fetchMessages,
  fetchUnreadCount,
  markAllMessagesRead,
  markMessageRead,
  type InboxMessage,
} from "../api/messagesApi";

const POLL_INTERVAL = 15_000;

function formatTime(value: string): string {
  if (!value) return "";
  // 远程 announcement-admin 返回的 published_at 是无时区的 ISO 字符串
  // （如 "2026-09-08 06:57:34"），但实际是 UTC（不是本地时间）。直接交给
  // new Date() 会被浏览器当作本地时间解析，导致国内用户看到的时间少 8 小时。
  // 显式识别"无时区 ISO"并补 "Z" 当 UTC 解析；带时区的字符串保持原样。
  const looksLikeNaiveDatetime = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$/.test(value);
  const normalized = looksLikeNaiveDatetime ? value.replace(" ", "T") + "Z" : value;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return value;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** 右上角消息中心：铃铛图标 + 未读红点数字，点击展开站内信列表。 */
export function InboxBell() {
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<InboxMessage[]>([]);
  const [loading, setLoading] = useState(false);
  // 新消息自动提醒弹窗：展示最新一条未读公告，× 仅关闭、不改已读状态。
  const [announcement, setAnnouncement] = useState<{ message: InboxMessage; total: number } | null>(
    null,
  );
  // 弹层本地展开状态：点击消息项展开/收起正文，与服务端 read 状态解耦。
  const [expandedIds, setExpandedIds] = useState<ReadonlySet<number>>(() => new Set());
  const rootRef = useRef<HTMLDivElement>(null);
  const listRequestIdRef = useRef(0);
  // 本地已读变更代际：在途的 refresh 返回旧计数时据此跳过，避免覆盖本地递减/清零结果。
  const unreadEpochRef = useRef(0);
  // open 的镜像：供 async 回调读取最新值（消息中心已打开时不弹提醒）。
  const openRef = useRef(false);
  useEffect(() => {
    openRef.current = open;
  }, [open]);
  // 上一次未读数：用于检测"新增未读"触发弹窗；null 表示尚未拿到首个计数。
  const prevUnreadRef = useRef<number | null>(null);
  // 本次会话已弹过提醒的消息 id：同一条消息不重复弹。
  const announcedIdsRef = useRef<Set<number>>(new Set());

  const refreshUnread = useCallback(async () => {
    const epoch = unreadEpochRef.current;
    try {
      const count = await fetchUnreadCount();
      if (epoch === unreadEpochRef.current) setUnread(count);
    } catch {
      // 未登录/离线时静默，保持上次数字
    }
  }, []);

  const refreshList = useCallback(async () => {
    const requestId = ++listRequestIdRef.current;
    const epoch = unreadEpochRef.current;
    setLoading(true);
    try {
      const items = await fetchMessages();
      if (requestId !== listRequestIdRef.current) return;
      setMessages(items);
      if (epoch === unreadEpochRef.current) setUnread(items.filter((item) => !item.read).length);
    } catch {
      // 静默
    } finally {
      if (requestId === listRequestIdRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshUnread();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") refreshUnread();
    }, POLL_INTERVAL);
    const handleFocus = () => refreshUnread();
    const handleLocalChange = () => refreshUnread();
    window.addEventListener("focus", handleFocus);
    window.addEventListener("mainpg:messages-change", handleLocalChange);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", handleFocus);
      window.removeEventListener("mainpg:messages-change", handleLocalChange);
    };
  }, [refreshUnread]);

  useEffect(() => {
    if (!open) return;
    const refreshTimer = window.setInterval(refreshList, POLL_INTERVAL);
    const onDocMouseDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocMouseDown);
    return () => {
      window.clearInterval(refreshTimer);
      document.removeEventListener("mousedown", onDocMouseDown);
    };
  }, [open, refreshList]);

  // 新消息自动提醒：拉取列表，找最新一条"未读且未弹过"的消息弹出。
  const maybeAnnounce = useCallback(
    async (totalNew: number) => {
      if (openRef.current) return; // 消息中心正打开，视为已看到，不打扰
      try {
        const items = await fetchMessages();
        const fresh = items.filter(
          (item) => !item.read && !announcedIdsRef.current.has(item.id),
        );
        if (fresh.length === 0) return;
        announcedIdsRef.current = new Set([
          ...announcedIdsRef.current,
          ...fresh.map((item) => item.id),
        ]);
        setAnnouncement({ message: fresh[0], total: Math.max(totalNew, fresh.length) });
      } catch {
        // 静默：拉取失败不弹
      }
    },
    [],
  );

  // 监听未读数变化：增加时（含首次发现有未读）弹出最新一条新消息。
  useEffect(() => {
    const prev = prevUnreadRef.current;
    prevUnreadRef.current = unread;
    if (prev === null) {
      if (unread > 0) void maybeAnnounce(unread);
      return;
    }
    if (unread > prev) void maybeAnnounce(unread - prev);
  }, [unread, maybeAnnounce]);

  const togglePanel = () => {
    const next = !open;
    setOpen(next);
    if (next) {
      // 提醒弹窗与消息中心位置完全重合（top:44px/right:0/width:330px）且 z-index 更高，
      // 两者同时挂载时提醒会把消息列表整块盖住。打开列表前先撤掉提醒。
      setAnnouncement(null);
      void refreshList();
    }
  };

  const handleMarkRead = async (messageId: number) => {
    try {
      await markMessageRead(messageId);
    } catch {
      return;
    }
    setMessages((current) =>
      current.map((item) => (item.id === messageId ? { ...item, read: true } : item)),
    );
    unreadEpochRef.current += 1;
    setUnread((current) => Math.max(0, current - 1));
    void refreshList();
  };

  const handleMarkAll = async () => {
    try {
      await markAllMessagesRead();
    } catch {
      return;
    }
    setMessages((current) => current.map((item) => ({ ...item, read: true })));
    unreadEpochRef.current += 1;
    setUnread(0);
    void refreshList();
  };

  const toggleExpanded = (messageId: number) => {
    setExpandedIds((current) => {
      const next = new Set(current);
      if (next.has(messageId)) {
        next.delete(messageId);
      } else {
        next.add(messageId);
      }
      return next;
    });
  };

  const handleItemClick = (item: InboxMessage) => {
    if (!item.read) void handleMarkRead(item.id);
    toggleExpanded(item.id);
  };

  const handleAnnouncementOpen = () => {
    // 点击卡片主体：关闭提醒并打开消息中心（已读状态不动，红点仍在列表内点消息才消）。
    setAnnouncement(null);
    setOpen(true);
    void refreshList();
  };

  return (
    <div className="inbox-menu" ref={rootRef}>
      <button
        type="button"
        className={`icon-button inbox-bell ${open ? "is-active" : ""}`}
        onClick={togglePanel}
        aria-label="消息中心"
        aria-expanded={open}
        title="消息中心"
      >
        <span className="iconfont icon-bell" aria-hidden="true" />
        {unread > 0 && (
          <span className="inbox-badge" aria-label={`${unread} 条未读消息`}>
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>
      {open && (
        <div className="inbox-popover" role="dialog" aria-label="消息中心">
          <header className="inbox-popover-header">
            <strong>消息中心</strong>
            {unread > 0 && (
              <button type="button" className="inbox-read-all" onClick={handleMarkAll}>
                全部已读
              </button>
            )}
          </header>
          <div className="inbox-list">
            {loading && messages.length === 0 ? (
              <div className="inbox-empty">加载中…</div>
            ) : messages.length === 0 ? (
              <div className="inbox-empty">暂无消息</div>
            ) : (
              messages.map((item) => {
                const expanded = expandedIds.has(item.id);
                return (
                  <button
                    key={item.id}
                    type="button"
                    className={`inbox-item ${item.read ? "is-read" : "is-unread"} ${expanded ? "is-expanded" : ""}`}
                    onClick={() => handleItemClick(item)}
                    aria-expanded={expanded}
                  >
                    <span className="inbox-item-dot" aria-hidden="true" />
                    <span className="inbox-item-main">
                      <strong>
                        {item.kind === "feedback_reply" && (
                          <span className="inbox-kind-badge">回复</span>
                        )}
                        {item.title}
                      </strong>
                      {item.content && <em>{item.content}</em>}
                      <time>{formatTime(item.publishedAt)}</time>
                    </span>
                    {item.content && (
                      <span className="inbox-item-toggle" aria-hidden="true">
                        {expanded ? "收起" : "展开"}
                      </span>
                    )}
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}
      {/* 渲染层互斥：announcement 与 popover 位置完全重合（top:44px/right:0/width:330px），
          且 announcement z-index:4 高于 popover:3，两者同时挂载时 reminder 会把列表
          整块盖住。popover 打开时直接不渲染 announcement，行为上更直观。 */}
      {!open && announcement && (
        <div className="inbox-announcement" role="alert" aria-label="新消息提醒">
          <button
            type="button"
            className="inbox-announcement-close"
            aria-label="关闭提醒"
            title="关闭提醒"
            onClick={() => setAnnouncement(null)}
          >
            ×
          </button>
          <button
            type="button"
            className="inbox-announcement-body"
            onClick={handleAnnouncementOpen}
            title="点击查看消息中心"
          >
            <strong className="inbox-announcement-title">{announcement.message.title}</strong>
            {announcement.message.content && (
              <em className="inbox-announcement-content">{announcement.message.content}</em>
            )}
            <span className="inbox-announcement-meta">
              {formatTime(announcement.message.publishedAt)}
              {announcement.total > 1 ? ` · 共 ${announcement.total} 条新消息` : ""}
            </span>
            <span className="inbox-announcement-hint">点击查看消息中心</span>
          </button>
        </div>
      )}
    </div>
  );
}
