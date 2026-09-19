import { httpJson } from "../../transport/http/client";

export type InboxMessage = {
  id: number;
  serverId: number;
  title: string;
  content: string;
  publishedAt: string;
  read: boolean;
  kind: string;
};

const TOKEN_KEY = "wh_demo_token";

/** 优先使用登录 token；仅在开发环境未登录时回退到本地开发管理员 token。 */
function resolveToken(): string {
  const stored = window.localStorage.getItem(TOKEN_KEY);
  if (stored) return stored;
  return import.meta.env.DEV ? "dev-admin-token" : "";
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
  };
}

export async function fetchMessages(): Promise<InboxMessage[]> {
  const payload = await httpJson<{ messages?: unknown[] }>("/api/messages", {
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
