import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import type { InboxMessage } from "../api/messagesApi";
import { renderAnnouncementHtml } from "../lib/announcementMarkdown";

/**
 * 登录后的公告大弹窗：居中展示、支持图片轮播，多条未读公告在同一个弹窗里排队逐条看。
 *
 * 「只看一次」的标记存在本机 localStorage：每条公告自动弹过一次后不再自动弹，
 * 但消息中心里仍能随时翻看历史公告（服务端不记录已弹状态）。
 */
const SEEN_PREFIX = "jye_workspace_announcement_seen:";
/** 与 CSS 出场过渡时长保持一致：动画放完再卸载，避免弹窗"瞬移"消失。 */
const CLOSE_ANIMATION_MS = 260;
/** 轮播自动切换节奏：多图时每张停留 4 秒后自动翻下一张。 */
const AUTOPLAY_INTERVAL_MS = 4000;

export function hasSeenAnnouncement(messageId: number): boolean {
  try {
    return window.localStorage.getItem(`${SEEN_PREFIX}${messageId}`) === "1";
  } catch {
    return false;
  }
}

export function markAnnouncementSeen(messageId: number): void {
  try {
    window.localStorage.setItem(`${SEEN_PREFIX}${messageId}`, "1");
  } catch {
    // 无痕模式等场景 localStorage 不可用：忽略，最坏情况是下次登录再弹一次。
  }
}

/** 公告弹窗功能上线时间：只有这之后发布的公告才在登录时自动弹，历史公告仍可在消息中心回看。
 *  必须带时区偏移：公告的 published_at 都是 `+08:00`，若写成 `Z`（UTC）会把门槛整体后移 8 小时，
 *  导致当天下午发布的公告被误判成"上线前的历史公告"而不弹。 */
const POPUP_SINCE = Date.parse("2026-09-17T11:11:35+08:00");

/** 消息中心点击公告 → 请求重开登录大弹窗（由 WorkspaceShell 监听并装配队列）。 */
export const REPLAY_ANNOUNCEMENT_EVENT = "mainpg:replay-announcement";

export function requestAnnouncementReplay(messageId: number): void {
  window.dispatchEvent(
    new CustomEvent(REPLAY_ANNOUNCEMENT_EVENT, { detail: { messageId } }),
  );
}

/** 远程公告的 published_at 是无时区 ISO（实际按 UTC 存），补 "Z" 解析避免国内少 8 小时。 */
function parsePublishedAt(value: string): number {
  if (!value) return Number.NaN;
  const naive = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$/.test(value);
  return new Date(naive ? `${value.replace(" ", "T")}Z` : value).getTime();
}

/** 该公告是否值得在登录时自动弹（上线前的历史公告不打扰用户）。 */
export function isAnnouncementPopupEligible(publishedAt: string): boolean {
  const ts = parsePublishedAt(publishedAt);
  return !Number.isNaN(ts) && ts >= POPUP_SINCE;
}

function formatTime(value: string): string {
  const ts = parsePublishedAt(value);
  if (Number.isNaN(ts)) return value;
  const date = new Date(ts);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

type AnnouncementModalProps = {
  /** 待展示的公告队列；多条时按顺序逐条看。 */
  announcements: InboxMessage[];
  /** 单条公告已看过（翻到下一条或关闭时触发），父层负责标记已读。 */
  onSeen: (messageId: number) => void;
  /** 队列看完或用户关闭弹窗，父层据此清空队列。 */
  onClose: () => void;
};

export function AnnouncementModal({ announcements, onSeen, onClose }: AnnouncementModalProps) {
  const [index, setIndex] = useState(0);
  const [slide, setSlide] = useState(0);
  /** 鼠标停在图上时暂停自动切换，方便用户细看截图。 */
  const [autoPaused, setAutoPaused] = useState(false);
  // 进场/出场都由 CSS 过渡驱动：先挂载（透明不可见），下一帧加 is-visible 触发过渡。
  const [phase, setPhase] = useState<"entering" | "visible" | "closing">("entering");
  const closingRef = useRef(false);

  const current = announcements[index];
  const images = current?.images ?? [];
  const hasMultipleItems = announcements.length > 1;
  // 轮播翻页会触发重渲染，正文 HTML 只在公告切换时重算一次。
  const contentHtml = useMemo(
    () => renderAnnouncementHtml(current?.content ?? ""),
    [current?.content],
  );

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => setPhase("visible"));
    return () => window.cancelAnimationFrame(frame);
  }, []);

  // 弹窗打开期间锁住背景滚动，避免滚轮把后面的页面一起带走。
  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, []);

  /** 平滑关闭：播完出场过渡再通知父层卸载。 */
  const close = useCallback(() => {
    if (closingRef.current) return;
    closingRef.current = true;
    setPhase("closing");
    window.setTimeout(onClose, CLOSE_ANIMATION_MS);
  }, [onClose]);

  /** 当前这条已看过：写入本机标记并提交已读，红点随之减少。 */
  const markCurrentSeen = useCallback(() => {
    if (current) onSeen(current.id);
  }, [current, onSeen]);

  /** 下一条：队列还有就换内容，没有就关闭。 */
  const goNext = useCallback(() => {
    markCurrentSeen();
    if (index + 1 < announcements.length) {
      setIndex(index + 1);
      setSlide(0);
      return;
    }
    close();
  }, [announcements.length, close, index, markCurrentSeen]);

  /** × / 蒙层 / Esc：当前这条算看过，队列里剩下的留到消息中心看。 */
  const dismiss = useCallback(() => {
    markCurrentSeen();
    close();
  }, [close, markCurrentSeen]);

  // 键盘操作：Esc 关闭，左右方向键翻图。
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        dismiss();
        return;
      }
      if (images.length < 2) return;
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        setSlide((value) => Math.max(0, value - 1));
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        setSlide((value) => Math.min(images.length - 1, value + 1));
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [dismiss, images.length]);

  // 自动轮播：多图时每 4 秒自动翻下一张，看完最后一张回到第一张。
  // 依赖 slide：每次翻页（手动或自动）都重新计时，手动点完不会马上又被自动翻走。
  useEffect(() => {
    if (images.length < 2 || autoPaused) return;
    const timer = window.setTimeout(() => {
      setSlide((value) => (value + 1) % images.length);
    }, AUTOPLAY_INTERVAL_MS);
    return () => window.clearTimeout(timer);
  }, [autoPaused, images.length, slide]);

  if (!current) return null;

  const phaseClass = phase === "closing" ? "is-closing" : phase === "visible" ? "is-visible" : "";

  return createPortal(
    <div
      className={`announcement-modal-mask ${phaseClass}`.trim()}
      onClick={dismiss}
      role="presentation"
    >
      <div
        className="announcement-modal-panel"
        role="dialog"
        aria-modal="true"
        aria-label={current.title || "平台公告"}
        onClick={(event) => event.stopPropagation()}
      >
        {/* 顶部品牌渐变条：进场时从左向右展开，给弹窗一个"开封"的开场。 */}
        <span className="announcement-modal-accent" aria-hidden="true" />
        <button
          type="button"
          className="announcement-modal-close"
          onClick={dismiss}
          aria-label="关闭公告"
          title="关闭"
        >
          ×
        </button>
        <header className="announcement-modal-head">
          <span className="announcement-modal-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 11v2a1 1 0 0 0 1 1h2l4 4V6L6 10H4a1 1 0 0 0-1 1Z" />
              <path d="M14.5 8.5a4.5 4.5 0 0 1 0 7" />
              <path d="M17.5 5.5a8.5 8.5 0 0 1 0 13" />
            </svg>
          </span>
          <span className="announcement-modal-heading">
            <span className="announcement-modal-eyebrow">平台公告</span>
            <span className="announcement-modal-subtitle">MainPG 官方发布</span>
          </span>
          {hasMultipleItems && (
            <span className="announcement-modal-counter">
              {index + 1} / {announcements.length}
            </span>
          )}
        </header>
        {/* key 换成公告 id：多条排队时切换公告，正文重新播一次淡入，不会"硬切"。 */}
        <div className="announcement-modal-body" key={current.id}>
          <h2 className="announcement-modal-title">{current.title}</h2>
          {images.length > 0 && (
            <div
              className="announcement-modal-carousel"
              onMouseEnter={() => setAutoPaused(true)}
              onMouseLeave={() => setAutoPaused(false)}
            >
              <div
                className="announcement-modal-track"
                style={{ transform: `translateX(-${slide * 100}%)` }}
              >
                {images.map((image, position) => (
                  <div className="announcement-modal-slide" key={`${current.id}-${position}`}>
                    <img
                      src={`data:${image.mime || "image/png"};base64,${image.data}`}
                      alt={image.name || `${current.title} 配图 ${position + 1}`}
                      draggable={false}
                    />
                  </div>
                ))}
              </div>
              {images.length > 1 && (
                <>
                  <button
                    type="button"
                    className="announcement-modal-nav is-prev"
                    aria-label="上一张"
                    disabled={slide === 0}
                    onClick={() => setSlide((value) => Math.max(0, value - 1))}
                  >
                    ‹
                  </button>
                  <button
                    type="button"
                    className="announcement-modal-nav is-next"
                    aria-label="下一张"
                    disabled={slide === images.length - 1}
                    onClick={() => setSlide((value) => Math.min(images.length - 1, value + 1))}
                  >
                    ›
                  </button>
                </>
              )}
            </div>
          )}
          {images.length > 1 && (
            <div className="announcement-modal-dots">
              {images.map((_, position) => (
                <button
                  type="button"
                  key={position}
                  className={`announcement-modal-dot${position === slide ? " is-active" : ""}`}
                  aria-label={`查看第 ${position + 1} 张`}
                  aria-current={position === slide}
                  onClick={() => setSlide(position)}
                />
              ))}
            </div>
          )}
          {contentHtml && (
            <div
              className="announcement-modal-content"
              dangerouslySetInnerHTML={{ __html: contentHtml }}
            />
          )}
        </div>
        <footer className="announcement-modal-foot">
          <span className="announcement-modal-time">{formatTime(current.publishedAt)}</span>
          <button type="button" className="primary-button announcement-modal-action" onClick={goNext}>
            {hasMultipleItems && index + 1 < announcements.length ? "下一条" : "我知道了"}
          </button>
        </footer>
      </div>
    </div>,
    document.body,
  );
}
