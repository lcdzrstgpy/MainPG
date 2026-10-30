import { getAuthToken, httpJson } from "../../../transport/http/client";

/**
 * 操作答疑（远端服务端）的客户端契约。
 *
 * 接口说明见 ``docs/superpowers/specs/答疑接口-客户端对接说明.md``。
 * 渲染层不直连远端，统一打本地 runtime 的 ``/api/help-agent/*``（见
 * ``local-runtime/wh_local/modules/help_agent/router.py``），由它转发并透传 SSE。
 */

export type QaCitation = { title: string; url: string };

export type QaMeta = {
  request_id: string;
  intent?: string;
  cached?: boolean;
};

type ChatStreamArgs = {
  /** 悬浮窗会话 id：一个面板生命周期内复用，服务端据此保留最近 3 轮上下文。 */
  sessionId: string;
  message: string;
  signal?: AbortSignal;
  onMeta?: (meta: QaMeta) => void;
  onDelta?: (text: string) => void;
  onCitation?: (citation: QaCitation) => void;
  onDone?: (done: { requestId: string; latencyMs?: number }) => void;
};

function apiUrl(path: string) {
  return `${(import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "")}${path}`;
}

/** 生成会话 id；``crypto.randomUUID`` 在非安全上下文里不可用时退回随机串。 */
export function newSessionId(): string {
  const uuid = globalThis.crypto?.randomUUID?.();
  if (uuid) return uuid;
  return `qa-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

/**
 * 把错误响应翻成用户能看懂的文案。
 *
 * ``Retry-After`` 由服务端给出秒数，直接透传；缺失时按 60 秒兜底（与服务端默认一致）。
 */
async function errorMessage(response: Response): Promise<string> {
  if (response.status === 429) {
    const header = response.headers.get("retry-after") ?? "";
    const wait = Number.parseInt(header, 10);
    return `提问太频繁，请 ${Number.isFinite(wait) && wait > 0 ? wait : 60} 秒后再试`;
  }
  if (response.status === 401) return "登录已失效，请重新登录";
  if (response.status === 413) return "问题太长了，请精简到 1000 字以内";
  if (response.status === 400) return "请先输入要问的问题";
  // 先看服务端有没有给 detail（代理层/远端都可能带），再退回通用文案。
  const payload = await response.json().catch(() => null);
  const detail = payload && typeof payload === "object" ? (payload as { detail?: unknown }).detail : null;
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  return "答疑服务暂时不可用，请稍后再试";
}

/** 从流式 `error` 事件里取用户可读文案：字段名服务端没定死，按常见几种兜。 */
function errorEventMessage(data: any): string {
  const raw = data?.message ?? data?.detail ?? data?.error ?? data?.reason;
  const text = typeof raw === "string" ? raw.trim() : "";
  return text || "回答生成失败了，请稍后再试";
}

export const helpAgentApi = {
  /** 对某条回答点「有用 / 没用」。 */
  feedback: (requestId: string, helpful: boolean) =>
    httpJson<{ ok?: boolean }>("/api/help-agent/feedback", {
      method: "POST",
      body: { request_id: requestId, helpful },
    }),

  /**
   * 提问并按 SSE 事件回调逐段渲染。
   *
   * ⚠️ 失败**不做自动重试**（服务端明确要求）：请求一发出就可能产生模型调用，
   * 由用户手动再点一次发送。用户主动关闭面板导致的 abort 会原样抛出 ``AbortError``，
   * 调用方据此区分「用户取消」和「真失败」。
   */
  chatStream: async ({
    sessionId,
    message,
    signal,
    onMeta,
    onDelta,
    onCitation,
    onDone,
  }: ChatStreamArgs): Promise<void> => {
    const token = getAuthToken();
    if (!token) throw new Error("请先登录后再提问");

    let response: Response;
    try {
      response = await fetch(apiUrl("/api/help-agent/chat"), {
        method: "POST",
        headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
        body: JSON.stringify({ session_id: sessionId, message, stream: true }),
        signal,
      });
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
      throw new Error("网络不可用，请检查网络后重试");
    }

    if (!response.ok || !response.body) throw new Error(await errorMessage(response));

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let requestId = "";

    const drain = (chunk: string) => {
      buffer += chunk;
      let index = buffer.indexOf("\n\n");
      while (index >= 0) {
        const block = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);
        index = buffer.indexOf("\n\n");

        const eventLine = block.split("\n").find((line) => line.startsWith("event: "));
        const dataLine = block.split("\n").find((line) => line.startsWith("data: "));
        if (!eventLine || !dataLine) continue;

        const event = eventLine.slice(7).trim();
        let data: any;
        try {
          data = JSON.parse(dataLine.slice(6));
        } catch {
          // 心跳/半截数据行跳过，不因为一行坏数据把整段回答作废。
          continue;
        }

        if (event === "meta") {
          requestId = String(data?.request_id ?? "");
          onMeta?.({ request_id: requestId, intent: data?.intent, cached: data?.cached });
        } else if (event === "delta") {
          if (typeof data?.text === "string") onDelta?.(data.text);
        } else if (event === "citation") {
          if (typeof data?.title === "string" && typeof data?.url === "string") {
            onCitation?.({ title: data.title, url: data.url });
          }
        } else if (event === "error") {
          // 服务端新增的错误事件：字段名没有定死，按常见几种取，取不到用兜底文案。
          throw new Error(errorEventMessage(data));
        } else if (event === "done") {
          // finish 可能是 stop / error / blocked：后两种不能当正常结束，否则用户会看到空气泡。
          const finish = String(data?.finish ?? "");
          if (finish === "blocked") throw new Error("这个问题被内容安全策略拦下了，换个问法试试");
          if (finish === "error") throw new Error("回答生成失败了，请稍后再试");
          onDone?.({ requestId, latencyMs: typeof data?.latency_ms === "number" ? data.latency_ms : undefined });
        }
      }
    };

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        drain(decoder.decode(value, { stream: true }));
      }
      // 流结束时 flush 残留：最后一段 delta 可能没有 \n\n 结尾。
      drain(decoder.decode());
    } finally {
      // 正常读完或中途抛错（error/blocked 事件、用户中止）都释放连接。
      void reader.cancel().catch(() => undefined);
    }
  },
};
