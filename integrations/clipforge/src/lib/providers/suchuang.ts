/** Suchuang (无垠科技) async image/video adapter. */
import { BaseProvider, ProviderError } from "./base";
import type { ImageOptions, ImageResult, MediaType, Model, ProviderConfig, TaskStatus, TaskStatusEnum, VideoOptions, VideoResult } from "./types";

const BASE_URL = "https://api.wuyinkeji.com";
const IMAGE_MODELS: Model[] = [
  { id: "image_gpt", name: "GPT Image", provider: "suchuang", mediaType: "image", modes: ["text-to-image", "image-to-image"] },
  { id: "image_gpt_2.5", name: "GPT Image 2.5", provider: "suchuang", mediaType: "image", modes: ["text-to-image", "image-to-image"] },
];
const VIDEO_MODELS: Model[] = [
  { id: "video_wan_3.0", name: "Wan 3.0", provider: "suchuang", mediaType: "video", modes: ["text-to-video", "image-to-video", "video-to-video"], supportsAudio: true },
  { id: "minimax_h3", name: "MiniMax H3", provider: "suchuang", mediaType: "video", modes: ["text-to-video", "image-to-video"], supportsAudio: true },
  { id: "video_omni", name: "Kling Omni", provider: "suchuang", mediaType: "video", modes: ["text-to-video", "image-to-video"], supportsAudio: true },
  { id: "video_vidu", name: "Vidu", provider: "suchuang", mediaType: "video", modes: ["text-to-video", "image-to-video"], supportsAudio: true },
];

type SuchuangResponse = { code?: number; msg?: string; data?: Record<string, unknown> | string };

export class SuchuangProvider extends BaseProvider {
  readonly name = "suchuang";
  readonly displayName = "速创";

  constructor(config: ProviderConfig) {
    super({ ...config, baseUrl: config.baseUrl || BASE_URL });
  }

  protected getAuthHeaders(): Record<string, string> {
    return { Authorization: this.config.apiKey };
  }

  async listModels(mediaType?: MediaType): Promise<Model[]> {
    const models = [...IMAGE_MODELS, ...VIDEO_MODELS];
    return mediaType ? models.filter((model) => model.mediaType === mediaType) : models;
  }

  async generateImage(options: ImageOptions): Promise<ImageResult> {
    const taskId = await this.submit(options.modelId, {
      prompt: options.prompt,
      size: ratio(options.width, options.height),
      ...(options.referenceImageUrls?.length && { urls: options.referenceImageUrls }),
      ...(!options.referenceImageUrls?.length && options.referenceImageUrl && { urls: [options.referenceImageUrl] }),
      ...options.extra,
    });
    const done = await this.waitForTask(taskId, { interval: 5000 });
    const result = done.result as ImageResult | undefined;
    if (!result) throw new ProviderError("速创图片任务未返回结果", "NO_RESULT", this.name);
    result.modelId = options.modelId;
    return result;
  }

  async submitVideoTask(options: VideoOptions): Promise<{ taskId: string; modelId: string }> {
    const taskId = await this.submit(options.modelId, {
      prompt: options.prompt,
      ...(options.firstFrameUrl && { first_frame: options.firstFrameUrl }),
      ...(options.lastFrameUrl && { last_frame: options.lastFrameUrl }),
      ...(options.referenceImageUrls?.length && { images: options.referenceImageUrls.join(",") }),
      ...(options.referenceVideoUrls?.length && { videos: options.referenceVideoUrls.join(",") }),
      ...(options.referenceAudioUrls?.length && { audios: options.referenceAudioUrls.join(",") }),
      generate_audio: options.audioEnabled ?? true,
      ratio: ratio(options.width, options.height),
      ...(options.duration != null && { duration: options.duration }),
      ...options.extra,
    });
    return { taskId, modelId: options.modelId };
  }

  async generateVideo(options: VideoOptions): Promise<VideoResult> {
    const { taskId } = await this.submitVideoTask(options);
    const done = await this.waitForTask(taskId, { interval: 5000 });
    const result = done.result as VideoResult | undefined;
    if (!result) throw new ProviderError("速创视频任务未返回结果", "NO_RESULT", this.name);
    result.modelId = options.modelId;
    return result;
  }

  async getTaskStatus(taskId: string): Promise<TaskStatus> {
    const response = await this.request<SuchuangResponse>(`/api/async/detail?id=${encodeURIComponent(taskId)}&key=${encodeURIComponent(this.config.apiKey)}`);
    const data = asRecord(response.data);
    const state = String(data.status ?? data.state ?? data.task_status ?? response.msg ?? "").toLowerCase();
    const status = normalizeStatus(state);
    const urls = collectUrls(data);
    const video = urls.filter((url) => /\.(mp4|mov|webm|m4v)(?:\?|$)/i.test(url));
    const result = status === "completed" && urls.length ? (video.length ? { taskId, videoUrls: video, modelId: "" } : { taskId, imageUrls: urls, modelId: "" }) : undefined;
    return { taskId, status, ...(result && { result }), ...(status === "failed" && { error: String(data.message ?? response.msg ?? "速创任务失败") }) };
  }

  private async submit(modelId: string, body: Record<string, unknown>): Promise<string> {
    // The platform documents the key both as the Authorization header and query
    // parameter. Keep both: its console examples use the query parameter while
    // production API calls require Authorization.
    const response = await this.request<SuchuangResponse>(`/api/async/${encodeURIComponent(modelId)}?key=${encodeURIComponent(this.config.apiKey)}`, {
      method: "POST",
      body,
      idempotent: false,
    });
    const data = asRecord(response.data);
    const taskId = String(data.id ?? data.task_id ?? data.taskId ?? "").trim();
    if (!taskId || (response.code != null && response.code !== 200)) {
      throw new ProviderError(`速创任务创建失败: ${response.msg ?? "未返回任务 ID"}`, "SUCHUANG_SUBMIT_ERROR", this.name);
    }
    return taskId;
  }
}

function ratio(width?: number, height?: number): string {
  if (!width || !height) return "9:16";
  if (width === height) return "1:1";
  return width > height ? "16:9" : "9:16";
}
function asRecord(value: unknown): Record<string, unknown> { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}; }
function normalizeStatus(value: string): TaskStatusEnum {
  if (/success|complete|finish|done/.test(value)) return "completed";
  if (/fail|error|reject/.test(value)) return "failed";
  if (/cancel/.test(value)) return "cancelled";
  return /queue|pending|wait/.test(value) ? "pending" : "processing";
}
function collectUrls(value: unknown, depth = 0): string[] {
  if (depth > 5) return [];
  if (typeof value === "string") return /^https?:\/\//.test(value) ? [value] : [];
  if (Array.isArray(value)) return value.flatMap((item) => collectUrls(item, depth + 1));
  if (!value || typeof value !== "object") return [];
  return Object.entries(value as Record<string, unknown>).flatMap(([key, item]) => /url|result|output|video|image|file/i.test(key) ? collectUrls(item, depth + 1) : []);
}
