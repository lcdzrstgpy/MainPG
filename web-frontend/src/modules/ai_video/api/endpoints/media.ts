import { cfBlob, cfJson, cfUrl, type CfInit } from "../clipforgeClient";
import type {
  MediaEdit,
  MediaSource,
  TranscriptClipsResult,
  TranscriptEditPlan,
  TranscriptEditResponse,
} from "../types";

function base(projectId: string) {
  return `/api/project/${encodeURIComponent(projectId)}/media`;
}

function mediaBase(projectId: string, mediaId: string) {
  return `${base(projectId)}/${encodeURIComponent(mediaId)}`;
}

export function listMedia(projectId: string, init?: CfInit) {
  return cfJson<{ sources: MediaSource[] }>(base(projectId), init);
}

/**
 * 导入长视频：body 即文件流，文件名放 `X-File-Name`（≤1GB）。
 * XHR 只为展示真实上传进度；取消时中止请求。
 */
export function uploadMedia(
  projectId: string,
  file: File,
  options: { signal?: AbortSignal; onProgress?: (percent: number) => void } = {},
): Promise<MediaSource> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const aborted = () => new DOMException("Upload cancelled", "AbortError");
    let settled = false;
    const finish = (callback: () => void) => {
      if (settled) return;
      settled = true;
      options.signal?.removeEventListener("abort", abort);
      callback();
    };
    const abort = () => {
      xhr.abort();
      finish(() => reject(aborted()));
    };
    if (options.signal?.aborted) {
      reject(aborted());
      return;
    }
    xhr.open("POST", cfUrl(base(projectId)));
    xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");
    xhr.setRequestHeader("X-File-Name", encodeURIComponent(file.name));
    xhr.timeout = 600_000;
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) options.onProgress?.(Math.round((event.loaded / event.total) * 100));
    };
    xhr.onload = () =>
      finish(() => {
        try {
          const data = JSON.parse(xhr.responseText) as MediaSource & { error?: string };
          if (xhr.status < 200 || xhr.status >= 300 || !data.id) {
            reject(new Error(data.error || `视频导入失败（HTTP ${xhr.status}）`));
            return;
          }
          resolve(data);
        } catch {
          reject(new Error("视频导入返回了无法识别的内容"));
        }
      });
    xhr.onerror = () => finish(() => reject(new Error("连接中断，可重试导入")));
    xhr.ontimeout = () => finish(() => reject(new Error("导入超时，请重试")));
    xhr.onabort = () => finish(() => reject(aborted()));
    options.signal?.addEventListener("abort", abort, { once: true });
    xhr.send(file);
  });
}

/** POST /media/:mid/transcript —— start / heartbeat / checkpoint / complete / fail / cancel。 */
export function updateTranscript(
  projectId: string,
  mediaId: string,
  body: Record<string, unknown>,
  init?: CfInit,
) {
  return cfJson<MediaSource & { resumeCheckpoint?: unknown; ok?: boolean; progress?: number; checkpoint?: unknown }>(
    `${mediaBase(projectId, mediaId)}/transcript`,
    { ...init, method: "POST", json: body },
  );
}

/** GET /media/:mid/audio —— 取指定区间的 16k 单声道 PCM。 */
export async function fetchAudioChunk(
  projectId: string,
  mediaId: string,
  start: number,
  duration: number,
  signal?: AbortSignal,
): Promise<ArrayBuffer> {
  const blob = await cfBlob(
    `${mediaBase(projectId, mediaId)}/audio?start=${start.toFixed(3)}&duration=${duration.toFixed(3)}`,
    { signal },
  );
  return blob.arrayBuffer();
}

export function previewTranscriptEdit(
  projectId: string,
  mediaId: string,
  input: { operationId: string; baseRevision: number; plan: TranscriptEditPlan },
  init?: CfInit,
) {
  return cfJson<TranscriptEditResponse>(`${mediaBase(projectId, mediaId)}/edit`, {
    ...init,
    method: "POST",
    json: { action: "preview", actor: "human", ...input },
  });
}

export function applyTranscriptEdit(
  projectId: string,
  mediaId: string,
  input: { operationId: string; baseRevision: number; plan: TranscriptEditPlan },
  init?: CfInit,
) {
  return cfJson<TranscriptEditResponse>(`${mediaBase(projectId, mediaId)}/edit`, {
    ...init,
    method: "POST",
    json: { action: "apply", actor: "human", ...input },
  });
}

export function cancelTranscriptEdit(projectId: string, mediaId: string, editId: string, init?: CfInit) {
  return cfJson<{ edit: MediaEdit | null }>(`${mediaBase(projectId, mediaId)}/edit`, {
    ...init,
    method: "POST",
    json: { action: "cancel", editId },
  });
}

export function retryTranscriptEdit(projectId: string, mediaId: string, editId: string, init?: CfInit) {
  return cfJson<{ edit: MediaEdit | null }>(`${mediaBase(projectId, mediaId)}/edit`, {
    ...init,
    method: "POST",
    json: { action: "retry", editId },
  });
}

/** POST /media/:mid/timeline —— OTIO / EDL / CSV 草稿导出（二进制下载）。 */
export async function exportTimelineDraft(
  projectId: string,
  mediaId: string,
  format: "otio" | "edl" | "csv",
  plan: TranscriptEditPlan,
  signal?: AbortSignal,
): Promise<Blob> {
  return cfBlob(`${mediaBase(projectId, mediaId)}/timeline`, {
    method: "POST",
    json: { format, plan },
    signal,
  });
}

/** GET /media/:mid/clips —— 本地逐字稿片段建议（不调用模型）。 */
export function listTranscriptClips(
  projectId: string,
  mediaId: string,
  input: { query?: string; targetSeconds?: number; limit?: number } = {},
  init?: CfInit,
) {
  const query = new URLSearchParams();
  if (input.query) query.set("query", input.query);
  if (input.targetSeconds) query.set("targetSeconds", String(input.targetSeconds));
  if (input.limit) query.set("limit", String(input.limit));
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return cfJson<TranscriptClipsResult>(`${mediaBase(projectId, mediaId)}/clips${suffix}`, init);
}

/** POST /media/:mid/batch —— 一次输出多条命名片段。 */
export function applyTranscriptBatch(
  projectId: string,
  mediaId: string,
  input: {
    action: "preview" | "apply";
    batchId: string;
    baseRevision: number;
    items: Array<{ label: string; plan: TranscriptEditPlan }>;
  },
  init?: CfInit,
) {
  return cfJson<{
    batchId: string;
    items?: Array<{ proposal: unknown }>;
    edits?: MediaEdit[];
  }>(`${mediaBase(projectId, mediaId)}/batch`, {
    ...init,
    method: "POST",
    json: input,
  });
}
