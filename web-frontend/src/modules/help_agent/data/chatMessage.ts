import type { HelpAgentCandidate, HelpAgentSearchResult } from "../api/helpAgentApi";

/** 一条渲染用的聊天消息。 */
export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  category?: string;
  candidates?: HelpAgentCandidate[];
  fallback?: boolean;
  /** 触发这条回复的原始提问，兜底消息用它来预填反馈内容 */
  sourceQuestion?: string;
};

/**
 * 把 ``/search`` 的三态响应转成一条助手消息。
 *
 * 三态映射是这层唯一的业务逻辑，单独抽出来便于测试：
 * - ``hit``        → 直接给正文答案 + 分类标签
 * - ``candidates`` → 给候选问题列表（**不含答案**，点选后走 confirm）
 * - ``fallback``   → 给兜底文案 + 反馈按钮标记，并带上原问题供预填
 *
 * ``allowFeedback=false`` 用于登录前（注册/登录页）：那里没有反馈面板，
 * 兜底文案不能再说「点下面的按钮」，要改成「登录后去哪反馈」。
 */
export function toAssistantMessage(
  result: HelpAgentSearchResult,
  id: string,
  question = "",
  options: { allowFeedback?: boolean } = {},
): ChatMessage {
  if (result.type === "hit") {
    return { id, role: "assistant", text: result.answer, category: result.category };
  }
  if (result.type === "candidates") {
    return {
      id,
      role: "assistant",
      text: "没有找到完全匹配的答案，你是想问下面这几个吗？",
      candidates: result.candidates,
    };
  }
  return {
    id,
    role: "assistant",
    text: options.allowFeedback === false
      ? "这个问题我暂时答不上来。登录后可以在「个人中心」的「意见反馈」里提交给我们，我们会补充到常见问题里。"
      : "这个问题我暂时答不上来。可以点下面的按钮把问题反馈给我们，我们会补充到常见问题里。",
    fallback: true,
    sourceQuestion: question,
  };
}

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
