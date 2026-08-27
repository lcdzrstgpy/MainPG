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
  const rootRef = useRef<HTMLDivElement>(null);

  const refreshUnread = useCallback(async () => {
    try {
      setUnread(await fetchUnreadCount());
    } catch {
      // 未登录/离线时静默，保持上次数字
    }
  }, []);

  const refreshList = useCallback(async () => {
    setLoading(true);
    try {
      const items = await fetchMessages();
      setMessages(items);
      setUnread(items.filter((item) => !item.read).length);
    } catch {
      // 静默
    } finally {
      setLoading(false);
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
    const onDocMouseDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, [open]);

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
  };

  const handleMarkAll = async () => {
    try {
      await markAllMessagesRead();
    } catch {
      return;
    }
    setMessages((current) => current.map((item) => ({ ...item, read: true })));
    setUnread(0);
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
              messages.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`inbox-item ${item.read ? "is-read" : "is-unread"}`}
                  onClick={() => {
                    if (!item.read) void handleMarkRead(item.id);
                  }}
                >
                  <span className="inbox-item-dot" aria-hidden="true" />
                  <span className="inbox-item-main">
                    <strong>{item.title}</strong>
                    {item.content && <em>{item.content}</em>}
                    <time>{formatTime(item.publishedAt)}</time>
                  </span>
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
