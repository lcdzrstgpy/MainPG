import { useCallback, useEffect, useRef, useState } from "react";

import {
  fetchMessages,
  fetchUnreadCount,
  markAllMessagesRead,
  markMessageRead,
  deleteMessage,
  type InboxMessage,
} from "../api/messagesApi";
import { toAnnouncementSummary } from "../lib/announcementMarkdown";
import { requestAnnouncementReplay } from "./AnnouncementModal";

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

/** 右上角消息中心：铃铛图标 + 未读红点数字，点击展开站内信列表。
 *  公告的「登录后大弹窗」由 AnnouncementModal 负责，这里只做日常回看入口。 */
export function InboxBell() {
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<InboxMessage[]>([]);
  const [loading, setLoading] = useState(false);
  // 弹层本地展开状态：点击消息项展开/收起正文，与服务端 read 状态解耦。
  const [expandedIds, setExpandedIds] = useState<ReadonlySet<number>>(() => new Set());
  const rootRef = useRef<HTMLDivElement>(null);
  const listRequestIdRef = useRef(0);
  // 本地已读变更代际：在途的 refresh 返回旧计数时据此跳过，避免覆盖本地递减/清零结果。
  const unreadEpochRef = useRef(0);

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

  const handleDelete = async (messageId: number) => {
    const target = messages.find((item) => item.id === messageId);
    try {
      await deleteMessage(messageId);
    } catch {
      return;
    }
    setMessages((current) => current.filter((item) => item.id !== messageId));
    setExpandedIds((current) => {
      const next = new Set(current);
      next.delete(messageId);
      return next;
    });
    // 删除的是未读消息时乐观减未读，避免依赖 refreshList 异步返回（失败则红点不更新）。
    if (target && !target.read) setUnread((current) => Math.max(0, current - 1));
    unreadEpochRef.current += 1;
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
    // 公告：不在列表里做行内展开，改为重开登录时的那套大弹窗（Markdown 排版 + 图片轮播一致）。
    if (item.kind === "announcement") {
      setOpen(false);
      requestAnnouncementReplay(item.id);
      return;
    }
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
                // 公告正文是 Markdown：收起时给纯文本摘要，避免列表里露出 `**`、`-` 等记号。
                const summary =
                  item.kind === "announcement"
                    ? toAnnouncementSummary(item.content) || item.content
                    : item.content;
                return (
                  <div
                    key={item.id}
                    role="button"
                    tabIndex={0}
                    className={`inbox-item ${item.read ? "is-read" : "is-unread"} ${expanded ? "is-expanded" : ""}`}
                    onClick={() => handleItemClick(item)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        handleItemClick(item);
                      }
                    }}
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
                      {item.content && <em>{expanded ? item.content : summary}</em>}
                      <time>{formatTime(item.publishedAt)}</time>
                    </span>
                    {item.content && (
                      <span className="inbox-item-toggle" aria-hidden="true">
                        {item.kind === "announcement" ? "查看" : expanded ? "收起" : "展开"}
                      </span>
                    )}
                    {item.kind === "feedback_reply" && (
                      <button
                        type="button"
                        className="inbox-item-delete"
                        aria-label="删除这条消息"
                        title="删除"
                        onClick={(event) => {
                          event.stopPropagation();
                          void handleDelete(item.id);
                        }}
                      >
                        ×
                      </button>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}
