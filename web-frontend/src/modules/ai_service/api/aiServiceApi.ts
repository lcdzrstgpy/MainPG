import { getAuthToken, httpBlob, httpJson, toUserMessage } from "../../../transport/http/client";

export type ApiAsset = { asset_id: string; filename: string; content_type: string };
export type ApiConversation = { conversation_id: string; title: string; mode: "chat" | "generate" | "edit"; is_pinned: boolean; updated_at: string };
export type AiBootstrap = {
  models: Array<{ id: string; name: string; modes: Array<"chat" | "generate" | "edit">; sizes: string[] }>;
  templates: Array<{ id: string; label: string; description: string; mode: "generate" | "edit"; prompt: string }>;
  conversations: ApiConversation[];
};

function apiUrl(path: string) {
  return `${(import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "")}${path}`;
}

export const aiServiceApi = {
  bootstrap: () => httpJson<AiBootstrap>("/api/ai-service/bootstrap"),
  createConversation: (title: string, mode: ApiConversation["mode"]) => httpJson<{ conversation_id: string }>("/api/ai-service/conversations", { method: "POST", body: { title, mode } }),
  updateConversation: (conversationId: string, body: { title?: string; is_pinned?: boolean }) => httpJson<ApiConversation>(`/api/ai-service/conversations/${encodeURIComponent(conversationId)}`, { method: "PATCH", body }),
  deleteConversation: (conversationId: string) => httpJson<{ conversation_id: string; status: string }>(`/api/ai-service/conversations/${encodeURIComponent(conversationId)}`, { method: "DELETE" }),
  messages: (conversationId: string) => httpJson<{ messages: Array<{ message_id: string; role: "user" | "assistant"; content: string; asset_ids: string[] }> }>(`/api/ai-service/conversations/${encodeURIComponent(conversationId)}/messages`),
  uploadAsset: async (file: File): Promise<ApiAsset> => {
    const headers: Record<string, string> = {};
    const token = getAuthToken();
    if (token) headers.authorization = `Bearer ${token}`;
    const form = new FormData();
    form.append("file", file);
    const response = await fetch(apiUrl("/api/ai-service/assets"), { method: "POST", headers, body: form });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(toUserMessage(typeof payload.detail === "string" ? payload.detail : "图片上传失败"));
    return payload as ApiAsset;
  },
  loadAssetUrl: async (assetId: string) => URL.createObjectURL(await httpBlob(`/api/ai-service/assets/${encodeURIComponent(assetId)}`)),
  createImage: (body: Record<string, unknown>) => httpJson<{ asset_ids: string[]; conversation_id: string }>("/api/ai-service/creations", { method: "POST", body }),
  streamChat: async (body: Record<string, unknown>) => {
    const headers: Record<string, string> = { "content-type": "application/json" };
    const token = getAuthToken();
    if (token) headers.authorization = `Bearer ${token}`;
    const response = await fetch(apiUrl("/api/ai-service/messages/stream"), { method: "POST", headers, body: JSON.stringify(body) });
    if (!response.ok || !response.body) throw new Error(toUserMessage((await response.text()) || "对话请求失败"));
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let raw = "";
    let content = "";
    while (true) {
      const next = await reader.read();
      if (next.done) break;
      raw += decoder.decode(next.value, { stream: true });
      const events = raw.split("\n\n");
      raw = events.pop() ?? "";
      for (const event of events) {
        for (const line of event.split("\n")) {
          if (!line.startsWith("data:")) continue;
          const value = line.slice(5).trim();
          if (!value || value === "[DONE]") continue;
          try {
            const payload = JSON.parse(value);
            if (typeof payload.error === "string") throw new Error(toUserMessage(payload.error));
            const delta = payload.choices?.[0]?.delta?.content;
            if (typeof delta === "string") content += delta;
          } catch (error) {
            if (error instanceof Error) throw error;
          }
        }
      }
    }
    return content;
  },
};
