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
  const date = new Date(value);
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
  // 弹层本地展开状态：点击消息项展开/收起正文，与服务端 read 状态解耦。
  const [expandedIds, setExpandedIds] = useState<ReadonlySet<number>>(() => new Set());
  const rootRef = useRef<HTMLDivElement>(null);
  const listRequestIdRef = useRef(0);

  const refreshUnread = useCallback(async () => {
    try {
      setUnread(await fetchUnreadCount());
    } catch {
      // 未登录/离线时静默，保持上次数字
    }
  }, []);

  const refreshList = useCallback(async () => {
    const requestId = ++listRequestIdRef.current;
    setLoading(true);
    try {
      const items = await fetchMessages();
      if (requestId !== listRequestIdRef.current) return;
      setMessages(items);
      setUnread(items.filter((item) => !item.read).length);
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

  const togglePanel = () => {
    const next = !open;
    setOpen(next);
    if (next) void refreshList();
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
                      <strong>{item.title}</strong>
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
    </div>
  );
}
