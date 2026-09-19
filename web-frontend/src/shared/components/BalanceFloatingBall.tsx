import { useCallback, useEffect, useRef, useState } from "react";

import { loadBillingSummary } from "../../modules/personal_center/api/personalCenterApi";
import { BALANCE_CHANGED_EVENT } from "../balanceEvents";
import { useTheme, type ThemeId } from "../hooks/useTheme";
import "./balanceBall.css";

/** 各主题专属 Q 版吉祥物（翻转球背面图）。lime/starry 主题尚未启用，资产已备好。 */
const THEME_MASCOT: Record<ThemeId, string> = {
  classic: "/theme/mascots/01-classic.png",
  sunset: "/theme/mascots/02-warm-orange.png",
  violet: "/theme/mascots/03-sakura-purple.png",
  dessert: "/theme/mascots/04-caramel.png",
  diamond: "/theme/mascots/05-diamond.png",
  quirky: "/theme/mascots/06-sticker.png",
  chinese: "/theme/mascots/07-ink.png",
  peach: "/theme/mascots/08-peach.png",
};

const POSITION_KEY = "mainpg.balanceBall.position";
const DRAG_THRESHOLD_PX = 6;
/** 数字面朝外且 5 分钟无交互时，自动翻到图面；图面保持不动。 */
const IDLE_FLIP_INTERVAL_MS = 5 * 60 * 1000;
const POLL_INTERVAL_MS = 60_000;
const BALL_SIZE = 88;

type BallPosition = { x: number; y: number };

function readPosition(): BallPosition | null {
  try {
    const raw = window.localStorage.getItem(POSITION_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<BallPosition> | null;
    if (parsed && typeof parsed.x === "number" && typeof parsed.y === "number") {
      return { x: parsed.x, y: parsed.y };
    }
  } catch { /* ignore */ }
  return null;
}

function clampPosition(position: BallPosition): BallPosition {
  const margin = 8;
  return {
    x: Math.min(Math.max(position.x, margin), window.innerWidth - BALL_SIZE - margin),
    y: Math.min(Math.max(position.y, margin), window.innerHeight - BALL_SIZE - margin),
  };
}

function defaultPosition(): BallPosition {
  return clampPosition({
    x: 24,
    y: window.innerHeight - BALL_SIZE - 24,
  });
}

/** 悬浮翻转球：余额面朝外随时可见，点击翻到主题吉祥物面，3 秒自动翻回，可拖动。 */
export function BalanceFloatingBall() {
  const { theme } = useTheme();
  const [points, setPoints] = useState<number | null>(null);
  const [flipped, setFlipped] = useState(false);
  const flippedRef = useRef(false);
  const idleTimerRef = useRef<number | null>(null);
  const [position, setPosition] = useState<BallPosition>(() => {
    const saved = readPosition();
    return saved ? clampPosition(saved) : defaultPosition();
  });
  const positionRef = useRef(position);
  positionRef.current = position;
  const dragRef = useRef({ active: false, moved: false, startX: 0, startY: 0, originX: 0, originY: 0 });

  const refresh = useCallback(async () => {
    try {
      const payload = await loadBillingSummary();
      setPoints(payload.wallet.available_points);
    } catch {
      // 静默失败：保留旧值，等下一轮刷新
    }
  }, []);

  const clearIdleTimer = useCallback(() => {
    if (idleTimerRef.current != null) {
      window.clearTimeout(idleTimerRef.current);
      idleTimerRef.current = null;
    }
  }, []);

  // 数字面朝外时启动 5 分钟计时：到点自动翻到图面（图面保持不动）。
  const scheduleIdleFlip = useCallback(() => {
    clearIdleTimer();
    idleTimerRef.current = window.setTimeout(() => {
      idleTimerRef.current = null;
      flippedRef.current = true;
      setFlipped(true);
    }, IDLE_FLIP_INTERVAL_MS);
  }, [clearIdleTimer]);

  useEffect(() => {
    scheduleIdleFlip();
    return clearIdleTimer;
  }, [scheduleIdleFlip, clearIdleTimer]);

  useEffect(() => {
    void refresh();
    const onChanged = () => { void refresh(); };
    window.addEventListener(BALANCE_CHANGED_EVENT, onChanged);
    const pollTimer = window.setInterval(() => { void refresh(); }, POLL_INTERVAL_MS);
    const onResize = () => setPosition((current) => clampPosition(current));
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener(BALANCE_CHANGED_EVENT, onChanged);
      window.clearInterval(pollTimer);
      window.removeEventListener("resize", onResize);
    };
  }, [refresh]);

  // 手动点击：数字面→图面（取消计时，图面常驻）；图面→数字面（重新计时 5 分钟）。
  const flip = useCallback(() => {
    const next = !flippedRef.current;
    flippedRef.current = next;
    setFlipped(next);
    if (next) {
      clearIdleTimer();
    } else {
      scheduleIdleFlip();
    }
  }, [clearIdleTimer, scheduleIdleFlip]);

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    dragRef.current = {
      active: true,
      moved: false,
      startX: event.clientX,
      startY: event.clientY,
      originX: positionRef.current.x,
      originY: positionRef.current.y,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!dragRef.current.active) return;
    const dx = event.clientX - dragRef.current.startX;
    const dy = event.clientY - dragRef.current.startY;
    if (!dragRef.current.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) return;
    dragRef.current.moved = true;
    setPosition(clampPosition({
      x: dragRef.current.originX + dx,
      y: dragRef.current.originY + dy,
    }));
  };

  const onPointerUp = () => {
    if (!dragRef.current.active) return;
    dragRef.current.active = false;
    if (dragRef.current.moved) {
      try {
        window.localStorage.setItem(POSITION_KEY, JSON.stringify(positionRef.current));
      } catch { /* ignore */ }
      // 拖动算交互：数字面时重置 5 分钟计时
      if (!flippedRef.current) scheduleIdleFlip();
    } else {
      flip();
    }
  };

  const mascot = THEME_MASCOT[theme] ?? THEME_MASCOT.classic;
  const displayPoints = points == null ? "…" : String(points);

  return (
    <div
      className={`balance-ball${flipped ? " is-flipped" : ""}`}
      style={{ left: position.x, top: position.y }}
      role="button"
      tabIndex={0}
      aria-label={`可用积分 ${points == null ? "加载中" : points}`}
      title={points == null ? "积分加载中" : `可用积分 ${points}，点击查看今日伙伴`}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
    >
      <div className="balance-ball-inner">
        <div className="balance-ball-face is-front">
          <span className="balance-ball-label">积分</span>
          <b className={displayPoints.length >= 6 ? "is-compact" : undefined}>{displayPoints}</b>
        </div>
        <div className="balance-ball-face is-back">
          <img src={mascot} alt="主题伙伴" draggable={false} />
        </div>
      </div>
    </div>
  );
}
