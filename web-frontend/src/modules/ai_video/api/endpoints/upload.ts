import { cfJson } from "../clipforgeClient";
import type { UploadResult } from "../types";

/** POST /api/upload —— 每张图片 ≤20MB，返回 `/api/files/...` 相对路径。 */
export function uploadImages(projectId: string, files: readonly File[], signal?: AbortSignal) {
  const form = new FormData();
  form.set("projectId", projectId);
  for (const file of files) form.append("files", file);
  return cfJson<UploadResult>("/api/upload", { method: "POST", body: form, signal });
}
