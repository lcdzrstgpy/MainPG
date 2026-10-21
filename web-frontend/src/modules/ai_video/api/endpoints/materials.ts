import { cfJson, cfUrl, type CfInit } from "../clipforgeClient";
import type { MaterialUploadResult, PublicLocalMaterial } from "../types";

function base(projectId: string) {
  return `/api/project/${encodeURIComponent(projectId)}/materials`;
}

export function listMaterials(projectId: string, init?: CfInit) {
  return cfJson<{ materials: PublicLocalMaterial[] }>(base(projectId), init);
}

export interface MaterialUploadProgress {
  percent: number;
  verifying: boolean;
}

/**
 * 单文件直传：body 即文件流，文件名放 `X-File-Name`（≤80MB）。
 * 用 XHR 而不是 fetch，是为了给大文件展示真实上传进度（规格 §5.6）。
 */
export function uploadMaterial(
  projectId: string,
  file: File,
  options: { signal?: AbortSignal; onProgress?: (progress: MaterialUploadProgress) => void } = {},
): Promise<{ material: PublicLocalMaterial; duplicate: boolean }> {
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
    xhr.timeout = 300_000;
    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) return;
      options.onProgress?.({
        percent: Math.round((event.loaded / event.total) * 100),
        verifying: event.loaded >= event.total,
      });
    };
    xhr.onload = () =>
      finish(() => {
        let data: MaterialUploadResult & { error?: string };
        try {
          data = JSON.parse(xhr.responseText) as MaterialUploadResult & { error?: string };
        } catch {
          reject(new Error("素材上传返回了无法识别的内容"));
          return;
        }
        const material = data.materials?.[0];
        if (xhr.status < 200 || xhr.status >= 300 || !material) {
          reject(new Error(data.error || `素材上传失败（HTTP ${xhr.status}）`));
          return;
        }
        resolve({ material, duplicate: data.duplicate === true });
      });
    xhr.onerror = () => finish(() => reject(new Error("连接中断，可重试上传")));
    xhr.ontimeout = () => finish(() => reject(new Error("上传超时，请重试")));
    xhr.onabort = () => finish(() => reject(aborted()));
    options.signal?.addEventListener("abort", abort, { once: true });
    xhr.send(file);
  });
}

export function updateMaterial(
  projectId: string,
  input: { name: string; originalName: string; tags?: string[] },
  init?: CfInit,
) {
  return cfJson<PublicLocalMaterial>(base(projectId), { ...init, method: "PATCH", json: input });
}
