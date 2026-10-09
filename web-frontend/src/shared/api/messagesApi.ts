import { httpJson } from "../../transport/http/client";

export type InboxMessageImage = {
  name: string;
  mime: string;
  size: number;
  /** 不带 data: 前缀的 base64 本体。 */
  data: string;
};

export type InboxMessage = {
  id: number;
  serverId: number;
  title: string;
  content: string;
  publishedAt: string;
  read: boolean;
  kind: string;
  /** 服务端图片张数；images 为空（未按需拉取）时也能知道有没有图。 */
  imageCount: number;
  imageRev: number;
  images: InboxMessageImage[];
};

const TOKEN_KEY = "wh_demo_token";

/** 优先使用登录 token；仅在开发环境未登录时回退到本地开发管理员 token。 */
function resolveToken(): string {
  const stored = window.localStorage.getItem(TOKEN_KEY);
  if (stored) return stored;
  return import.meta.env.DEV ? "dev-admin-token" : "";
}

function mapImage(value: unknown): InboxMessageImage {
  const raw = (value && typeof value === "object" ? value : {}) as Record<string, unknown>;
  return {
    name: String(raw.name ?? ""),
    mime: String(raw.mime ?? "image/png"),
    size: Number(raw.size ?? 0),
    data: String(raw.data ?? ""),
  };
}

function mapMessage(value: unknown): InboxMessage {
  const raw = (value && typeof value === "object" ? value : {}) as Record<string, unknown>;
  const rawRead = raw.read;
  // 后端若把布尔序列化为字符串（"false"/"0"），Boolean("false") 会误判为已读，
  // 导致未读红点失效。显式归一所有可能形态。
  const read = rawRead === true || rawRead === 1 || rawRead === "1" || rawRead === "true";
  return {
    id: Number(raw.id ?? 0),
    serverId: Number(raw.server_id ?? raw.serverId ?? 0),
    title: String(raw.title ?? ""),
    content: String(raw.content ?? ""),
    publishedAt: String(raw.published_at ?? raw.publishedAt ?? ""),
    read,
    kind: String(raw.kind ?? "announcement"),
    imageCount: Number(raw.image_count ?? raw.imageCount ?? 0),
    imageRev: Number(raw.image_rev ?? raw.imageRev ?? 0),
    images: Array.isArray(raw.images)
      ? raw.images.map(mapImage).filter((image) => image.data)
      : [],
  };
}

/**
 * 立即从发布后台同步一次（公告 + 反馈回复），并按服务端在线列表撤回本地陈旧消息。
 *
 * 本地消息表没有账号维度（同一台机器所有账号共用一张表），换号后上一个账号的
 * 定向公告会残留在本地，直到下一轮后台同步（180s）才被撤回——这就是「新用户
 * 短暂看到别人公告」的窗口。登录成功 / 进入工作台后先立刻调用本接口，把撤回
 * 提前到消息渲染之前。
 *
 * 同一个登录周期内多次调用共用同一个在飞请求（单飞），避免重复打后端；
 * 失败（离线等）静默降级，不阻断登录与消息展示。
 */
let messagesSyncInFlight: Promise<void> | null = null;

export function syncMessages(): Promise<void> {
  if (!messagesSyncInFlight) {
    messagesSyncInFlight = httpJson("/api/messages/sync", {
      method: "POST",
      body: {},
      token: resolveToken(),
      timeoutMs: 8000,
    })
      .then(() => undefined)
      .catch(() => undefined);
  }
  return messagesSyncInFlight;
}

/** 登录/登出后重置单飞缓存：换了账号必须重新同步一次，不能复用上一个账号的结果。 */
export function resetMessagesSync(): void {
  messagesSyncInFlight = null;
}

/** 拉取消息列表；withImages=true 时一并带上公告图片（base64），供弹窗轮播。 */
export async function fetchMessages(options?: { withImages?: boolean }): Promise<InboxMessage[]> {
  const path = options?.withImages ? "/api/messages?with_images=1" : "/api/messages";
  const payload = await httpJson<{ messages?: unknown[] }>(path, {
    token: resolveToken(),
  });
  return Array.isArray(payload.messages) ? payload.messages.map(mapMessage) : [];
}

export async function fetchUnreadCount(): Promise<number> {
  const payload = await httpJson<{ count?: number }>("/api/messages/unread-count", {
    token: resolveToken(),
  });
  return Number(payload.count ?? 0);
}

export async function markMessageRead(messageId: number): Promise<void> {
  await httpJson(`/api/messages/${messageId}/read`, {
    method: "POST",
    body: {},
    token: resolveToken(),
  });
}

export async function markAllMessagesRead(): Promise<void> {
  await httpJson("/api/messages/read-all", {
    method: "POST",
    body: {},
    token: resolveToken(),
  });
}

export async function deleteMessage(messageId: number): Promise<void> {
  await httpJson(`/api/messages/${messageId}`, {
    method: "DELETE",
    token: resolveToken(),
  });
}
