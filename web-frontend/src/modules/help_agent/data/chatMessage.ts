import type { QaCitation } from "../api/helpAgentApi";

/** 一条渲染用的聊天消息。 */
export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  /** 流式输出中：文本还在追加（用于渲染打字光标 / 等待态）。 */
  streaming?: boolean;
  citations?: QaCitation[];
  /** 服务端 ``request_id``，反馈接口按它定位回答。 */
  requestId?: string;
  /** 用户已提交的评价；提交后按钮不再可点。 */
  feedback?: "up" | "down";
};

/** 把悬浮球位置限制在可视区内，避免拖出屏幕或窗口缩小后找不回来。 */
export function clampBallPosition(
  position: { x: number; y: number },
  viewport: { width: number; height: number },
  ballSize: number,
  margin = 8,
): { x: number; y: number } {
  return {
    x: Math.min(Math.max(position.x, margin), Math.max(margin, viewport.width - ballSize - margin)),
    y: Math.min(Math.max(position.y, margin), Math.max(margin, viewport.height - ballSize - margin)),
  };
}

/** 判断一次指针拖拽是否算「移动过」（超过阈值就不当作点击）。 */
export function isDrag(
  delta: { dx: number; dy: number },
  threshold: number,
): boolean {
  return Math.abs(delta.dx) + Math.abs(delta.dy) >= threshold;
}

/** 校验从 localStorage 读回的悬浮球位置是否可用。 */
export function isValidStoredPosition(value: unknown): value is { x: number; y: number } {
  if (!value || typeof value !== "object") return false;
  const candidate = value as { x?: unknown; y?: unknown };
  return typeof candidate.x === "number" && typeof candidate.y === "number"
    && Number.isFinite(candidate.x) && Number.isFinite(candidate.y);
}
