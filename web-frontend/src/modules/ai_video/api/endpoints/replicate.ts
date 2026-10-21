import { cfJson } from "../clipforgeClient";
import type { ReplicateAnalysis } from "../types";

/** POST /api/replicate/analyze —— 上传参考视频，按画面切点解析真实镜头节奏。 */
export function analyzeReplicateVideo(file: File, signal?: AbortSignal) {
  const form = new FormData();
  form.append("file", file);
  return cfJson<ReplicateAnalysis>("/api/replicate/analyze", {
    method: "POST",
    body: form,
    signal,
  });
}
