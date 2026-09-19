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
  return {
    id: Number(raw.id ?? 0),
    serverId: Number(raw.server_id ?? raw.serverId ?? 0),
    title: String(raw.title ?? ""),
    content: String(raw.content ?? ""),
    publishedAt: String(raw.published_at ?? raw.publishedAt ?? ""),
    read: Boolean(raw.read),
    kind: String(raw.kind ?? "announcement"),
    imageCount: Number(raw.image_count ?? raw.imageCount ?? 0),
    imageRev: Number(raw.image_rev ?? raw.imageRev ?? 0),
    images: Array.isArray(raw.images)
      ? raw.images.map(mapImage).filter((image) => image.data)
      : [],
  };
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
