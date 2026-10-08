/** Suchuang (无垠科技) async image/video adapter. */
import { BaseProvider, ProviderError } from "./base";
import {
  decodeDataUri,
  deleteTemporaryReference,
  isPublicHttpUrl,
  publishTemporaryReference,
  resolveTemporaryReferenceConfig,
  type TemporaryReferenceConfig,
} from "./temporary-reference";
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

/** 图片参考图的临时公网 URL 有效期（生成过程通常 1–3 分钟）。 */
const IMAGE_REFERENCE_TTL_SECONDS = 1800;
/** 视频任务期间参考图必须一直可抓，有效期给长一些；到期后再尽力清理。 */
const VIDEO_REFERENCE_TTL_SECONDS = 7200;
const VIDEO_REFERENCE_CLEANUP_DELAY_MS = 90 * 60 * 1000;

/**
 * `image_gpt_2.5` 只提供 1K 档，尺寸字段是像素串 `aspectRatio`（没有 `size`）。
 * 与 MainPG 媒体模块 `infrastructure/media.py` 的 `WUYIN_IMAGE_ASPECT_RATIO_2_5` 同一张表。
 */
const IMAGE_ASPECT_RATIO_2_5: Record<string, string> = {
  "1:1": "1024x1024",
  "16:9": "1280x720",
  "9:16": "720x1280",
  "4:3": "1152x864",
  "3:4": "864x1152",
  "3:2": "1536x1024",
  "2:3": "1024x1536",
  "5:4": "1120x896",
  "4:5": "896x1120",
  "21:9": "1456x624",
  "9:21": "624x1456",
  "1:3": "688x2048",
  "3:1": "2048x688",
  "2:1": "1536x768",
  "1:2": "768x1536",
};

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
    // 参考图按端点分两种写法（与 MainPG 媒体模块一致）：两个端点都只认 prompt / 尺寸 / urls，
    // 传多余字段会让上游 500，所以这里不再透传 options.extra。
    const refs = options.referenceImageUrls?.length
      ? options.referenceImageUrls
      : (options.referenceImageUrl ? [options.referenceImageUrl] : []);
    const relayed = await relayReferences(refs, IMAGE_REFERENCE_TTL_SECONDS);
    try {
      const aspect = ratio(options.width, options.height);
      const body = options.modelId === "image_gpt_2.5"
        // 2.5 端点：urls 必须逗号拼接字符串，尺寸走像素串 aspectRatio（传数组 / 传 size 上游返 500）
        ? {
            prompt: options.prompt,
            aspectRatio: IMAGE_ASPECT_RATIO_2_5[aspect] ?? "1024x1024",
            ...(relayed.urls.length && { urls: relayed.urls.join(",") }),
          }
        // 旧端点：urls 传数组，尺寸走 size（比例串）
        : {
            prompt: options.prompt,
            size: aspect,
            ...(relayed.urls.length && { urls: relayed.urls }),
          };
      const taskId = await this.submit(options.modelId, body);
      const done = await this.waitForTask(taskId, { interval: 5000 });
      const result = done.result as ImageResult | undefined;
      if (!result) throw new ProviderError("速创图片任务未返回结果", "NO_RESULT", this.name);
      result.modelId = options.modelId;
      return result;
    } finally {
      await relayed.release();
    }
  }

  async submitVideoTask(options: VideoOptions): Promise<{ taskId: string; modelId: string }> {
    const firstFrame = await relayReferences([options.firstFrameUrl], VIDEO_REFERENCE_TTL_SECONDS);
    const lastFrame = await relayReferences([options.lastFrameUrl], VIDEO_REFERENCE_TTL_SECONDS);
    const images = await relayReferences(options.referenceImageUrls ?? [], VIDEO_REFERENCE_TTL_SECONDS);
    const releaseAll = async () => {
      await firstFrame.release();
      await lastFrame.release();
      await images.release();
    };
    try {
      const taskId = await this.submit(options.modelId, {
        prompt: options.prompt,
        ...(firstFrame.urls[0] && { first_frame: firstFrame.urls[0] }),
        ...(lastFrame.urls[0] && { last_frame: lastFrame.urls[0] }),
        ...(images.urls.length && { images: images.urls.join(",") }),
        ...(options.referenceVideoUrls?.length && { videos: options.referenceVideoUrls.join(",") }),
        ...(options.referenceAudioUrls?.length && { audios: options.referenceAudioUrls.join(",") }),
        generate_audio: options.audioEnabled ?? true,
        ratio: ratio(options.width, options.height),
        ...(options.duration != null && { duration: options.duration }),
        ...options.extra,
      });
      // 任务执行期间上游还要抓这些图，不能立刻删；到期后再尽力清理（进程若中途退出就交给桶的生命周期规则）
      const timer = setTimeout(() => void releaseAll(), VIDEO_REFERENCE_CLEANUP_DELAY_MS);
      timer.unref?.();
      return { taskId, modelId: options.modelId };
    } catch (error) {
      await releaseAll();
      throw error;
    }
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
    // 速创的 detail 会把我们提交的 request（含参考图 URL）原样回显，绝不能当成产出；
    // 产出只认 result 字段（实测：数字 status=2 + result 里给出成图 URL 才代表完成）。
    const rest = Object.fromEntries(Object.entries(data).filter(([key]) => key !== "request"));
    const resultUrls = collectUrls(data.result);
    const urls = resultUrls.length ? resultUrls : collectUrls(rest);
    const rawState = String(data.status ?? data.state ?? data.task_status ?? response.msg ?? "").toLowerCase();
    const failed = !urls.length && /fail|error|reject|cancel/.test(rawState);
    const status: TaskStatusEnum = urls.length ? "completed" : failed ? "failed" : normalizeStatus(rawState);
    const video = urls.filter((url) => /\.(mp4|mov|webm|m4v)(?:\?|$)/i.test(url));
    const result = status === "completed" ? (video.length ? { taskId, videoUrls: video, modelId: "" } : { taskId, imageUrls: urls, modelId: "" }) : undefined;
    return {
      taskId,
      status,
      ...(result && { result }),
      // 数字状态没有可读文案，失败时把原始状态一起带出来，便于定位
      ...(status === "failed" && { error: String(data.message ?? data.error ?? `速创任务失败（status=${rawState || "unknown"}）`) }),
    };
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

interface RelayedReferences {
  /** 上游可直接抓取的地址（原本就是公网 URL 的原样透传）。 */
  urls: string[];
  /** 尽力清理本次发布的中转对象；异步任务期间不能马上清，见 submitVideoTask。 */
  release: () => Promise<void>;
}

/**
 * 速创的 `urls` / `first_frame` / `last_frame` / `images` 都是交给上游去**抓**的地址：
 * data URI 与本地路径一律抓不到，提交阶段就会被判 500「转发请求失败: 目标服务器返回 500 错误」
 * （已实测复现）。所以本地图先发布成临时公网 URL（MainPG 媒体模块一直这么做）。
 * 没有中转配置时明确报错，绝不发一个抓不到的地址去换一个看不懂的 500。
 */
async function relayReferences(
  values: readonly (string | undefined)[],
  expiresSeconds: number
): Promise<RelayedReferences> {
  const refs = values.filter((value): value is string => Boolean(value && value.trim()));
  if (!refs.length || refs.every(isPublicHttpUrl)) {
    return { urls: refs, release: async () => {} };
  }
  const config = resolveTemporaryReferenceConfig();
  if (!config) {
    throw new ProviderError(
      "速创要求参考图/首尾帧是公网可访问地址，但本地没有可用的对象存储中转配置（cos.local.json）——请配置后重试，或改用支持内联参考图的平台",
      "REFERENCE_RELAY_UNAVAILABLE",
      "suchuang"
    );
  }
  const urls: string[] = [];
  const keys: string[] = [];
  try {
    for (const ref of refs) {
      if (isPublicHttpUrl(ref)) {
        urls.push(ref);
        continue;
      }
      const decoded = decodeDataUri(ref);
      if (!decoded) {
        throw new ProviderError("参考图既不是公网地址也不是内联图片，无法交给速创抓取", "REFERENCE_UNRELAYABLE", "suchuang");
      }
      const published = await publishTemporaryReference(config, decoded.bytes, decoded.contentType, expiresSeconds);
      keys.push(published.key);
      urls.push(published.url);
    }
  } catch (error) {
    await releaseKeys(config, keys);
    throw error;
  }
  return { urls, release: () => releaseKeys(config, keys) };
}

async function releaseKeys(config: TemporaryReferenceConfig, keys: string[]): Promise<void> {
  for (const key of keys) await deleteTemporaryReference(config, key);
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
