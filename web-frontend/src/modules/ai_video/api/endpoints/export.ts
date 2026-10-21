import { cfBlob, cfJson, type CfInit } from "../clipforgeClient";
import type {
  CarouselResult,
  ContactSheetResult,
  CoverResult,
  CreditsManifest,
  DubResult,
  EndCardResult,
  GateResult,
  NativeFeelResult,
  PlatformExportPlatform,
  PlatformExportResult,
  PublishCopy,
  PublishMetric,
  PublishMetricInput,
  QcReport,
  ShopQrResult,
} from "../types";

function base(projectId: string) {
  return `/api/project/${encodeURIComponent(projectId)}`;
}

/** POST /api/llm/publish —— 发布文案（3 个标题 + 话题 + 正文 + 评论区话术）。 */
export function generatePublishCopy(
  input: {
    productName: string;
    category?: string | null;
    productDescription?: string | null;
    locale?: "zh" | "en";
    llmConfig: { baseUrl: string; apiKey: string; model: string };
  },
  init?: CfInit,
) {
  return cfJson<PublishCopy>("/api/llm/publish", { ...init, method: "POST", json: input });
}

/** POST /api/project/:id/export-platform —— 按平台规格转码导出。 */
export function exportForPlatform(
  projectId: string,
  input: { compositionId?: string; platform: PlatformExportPlatform },
  init?: CfInit,
) {
  return cfJson<PlatformExportResult>(`${base(projectId)}/export-platform`, {
    ...init,
    method: "POST",
    json: input,
  });
}

/** POST /api/project/:id/qc —— 成片黑帧/静音/响度/流检测。 */
export function runQc(projectId: string, compositionId: string, init?: CfInit) {
  return cfJson<QcReport>(`${base(projectId)}/qc`, {
    ...init,
    method: "POST",
    json: { compositionId },
  });
}

/** POST /api/project/:id/gate —— 发布前门禁（脚本 + 质检 + 授权）。 */
export function runGate(projectId: string, compositionId?: string, init?: CfInit) {
  return cfJson<GateResult>(`${base(projectId)}/gate`, {
    ...init,
    method: "POST",
    json: compositionId ? { compositionId } : {},
  });
}

/** GET /api/project/:id/credits —— 授权清单（JSON）。 */
export function getCredits(projectId: string, init?: CfInit) {
  return cfJson<CreditsManifest>(`${base(projectId)}/credits`, init);
}

/** GET /api/project/:id/credits?format=md —— 可直接下载的 Markdown 授权清单。 */
export function downloadCreditsMarkdown(projectId: string, init?: CfInit) {
  return cfBlob(`${base(projectId)}/credits?format=md&lang=zh`, init);
}

export function generateCover(projectId: string, title: string, init?: CfInit) {
  return cfJson<CoverResult>(`${base(projectId)}/cover`, { ...init, method: "POST", json: { title } });
}

export function generateCarousel(projectId: string, init?: CfInit) {
  return cfJson<CarouselResult>(`${base(projectId)}/carousel`, {
    ...init,
    method: "POST",
    json: { theme: "night" },
  });
}

export function generateShopQr(projectId: string, init?: CfInit) {
  return cfJson<ShopQrResult>(`${base(projectId)}/shop-qr`, { ...init, method: "POST", json: {} });
}

export function generateEndCard(projectId: string, init?: CfInit) {
  return cfJson<EndCardResult>(`${base(projectId)}/end-card`, { ...init, method: "POST", json: {} });
}

export function generateContactSheet(projectId: string, proxy = false, init?: CfInit) {
  return cfJson<ContactSheetResult>(`${base(projectId)}/contact-sheet`, {
    ...init,
    method: "POST",
    json: proxy ? { proxy: true } : {},
  });
}

export function generateNativeFeel(
  projectId: string,
  input: { strength: "subtle" | "medium" | "strong"; halation: boolean; phoneCompress: boolean },
  init?: CfInit,
) {
  return cfJson<NativeFeelResult>(`${base(projectId)}/native-feel`, {
    ...init,
    method: "POST",
    json: input,
  });
}

export function generateDub(
  projectId: string,
  input: {
    targetLang: string;
    llmConfig: { baseUrl: string; apiKey: string; model: string };
  },
  init?: CfInit,
) {
  return cfJson<DubResult>(`${base(projectId)}/dub`, { ...init, method: "POST", json: input });
}

export function listMetrics(projectId: string, init?: CfInit) {
  return cfJson<{ metrics: PublishMetric[] }>(`${base(projectId)}/metrics`, init);
}

export function recordMetrics(projectId: string, input: PublishMetricInput, init?: CfInit) {
  return cfJson<{ metric: PublishMetric }>(`${base(projectId)}/metrics`, {
    ...init,
    method: "POST",
    json: input,
  });
}
