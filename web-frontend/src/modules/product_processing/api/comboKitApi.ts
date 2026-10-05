import { ppDownload, ppRequest, ppUpload, type ApiContext } from '../../product_processing/api/client';
import { getAuthToken } from '../../../transport/http/client';

const API_BASE = '/api/combo-kit';

export type ComboImageRole = { role: string; label: string };
// 生成选型：bundle=多件商品组合成一套；multiview=同一商品多视角（含内部/展开图）。
export type ComboGenerationMode = { mode: string; label: string; description: string };
export type ComboRoles = {
  image_roles: ComboImageRole[];
  default_image_prompts: Record<string, string>;
  // 后端下发的选型清单与各选型的内置默认辅助词，前端据此渲染选型卡片并切换默认提示词。
  generation_modes?: ComboGenerationMode[];
  default_generation_mode?: string;
  default_image_prompts_by_mode?: Record<string, Record<string, string>>;
  editable_prompt_roles?: string[];
  min_images: number;
  max_images: number;
  text_points: number;
  image_points: number;
};

export type ComboKitItem = {
  item_id: string;
  set_id: string;
  item_index: number;
  original_path: string;
  original_url: string;
  subject_keywords: string;
  mask_json: Record<string, unknown>;
  mask_inverted: boolean;
  mask_regex_serial: number;
  subject_parsed_json: Record<string, unknown>;
  spec_text: string;
  is_primary: boolean;
  width: number;
  height: number;
};

export type ComboKitBilling = {
  billing_id: string;
  billing_type: 'text' | 'image';
  freeze_id: string;
  points: number;
  status: string;
  result_status: string;
  created_at: string;
};

export type ComboKitSet = {
  set_id: string;
  workspace_id: string;
  name: string;
  sku: string;
  sku_display: string;
  description: string;
  bullets: string[];
  category_path: string;
  category_id: string;
  attributes: Record<string, unknown>;
  sku_specs: string[];
  sku_specs_json?: string[] | unknown;
  status: string;
  stage: string;
  created_at?: string;
  updated_at?: string;
  fusion_prompt?: string;
  declared_price?: string;
  length_cm?: number;
  width_cm?: number;
  height_cm?: number;
  weight_g?: number;
  stock?: number;
  category_name?: string;
  suggested_price_usd?: number;
  id_type?: string;
  id_code?: string;
  // 生成选型：bundle（默认）/ multiview。
  generation_mode?: string;
  // 成品图文字水印（每个套装各自配置）；生成时烧进图片，改配置后需重新生成才生效。
  watermark_json?: Partial<ComboKitWatermark>;
  text_result_json: Record<string, unknown>;
  image_results_json: Array<{ role: string; label: string; url: string; public_url?: string; provider: string; model: string; attempt_count: number }>;
  items: ComboKitItem[];
  prompt: Record<string, unknown> | Record<string, never>;
  billing: ComboKitBilling[];
  preview: Record<string, unknown> | null;
};

export type ComboKitWatermarkPosition = 'top_left' | 'top_right' | 'bottom_left' | 'bottom_right' | 'center';

// 成品图可选文字水印：纯文字，生成后立即烧进图片像素，
// 因此页面预览 / 下载 / 导出店小秘 / 预检看到的都是同一份带水印的图。
export interface ComboKitWatermark {
  enabled: boolean;
  text: string;
  position: ComboKitWatermarkPosition;
  // 不透明度 0~100（%）。
  opacity: number;
  // 字号占图宽百分比（1~30）。
  size: number;
  // 平铺满图（开启后位置设置失效）。
  tile: boolean;
}

export async function getRoles(ctx: ApiContext): Promise<ComboRoles> {
  return ppRequest<ComboRoles>(ctx, `${API_BASE}/roles`);
}

export async function listSets(ctx: ApiContext): Promise<{ sets: ComboKitSet[]; count: number }> {
  return ppRequest(ctx, `${API_BASE}/sets`);
}

export async function getSet(ctx: ApiContext, setId: string): Promise<ComboKitSet> {
  return ppRequest(ctx, `${API_BASE}/sets/${setId}`);
}

export async function createSet(
  ctx: ApiContext,
  input: { name?: string; sku?: string; sku_display?: string; description?: string; category_path?: string; category_id?: string; specs?: string[]; attributes?: Record<string, unknown>; generation_mode?: string }
): Promise<ComboKitSet> {
  return ppRequest(ctx, `${API_BASE}/sets`, { body: input });
}

export async function updateSet(
  ctx: ApiContext,
  setId: string,
  input: Record<string, unknown>
): Promise<ComboKitSet> {
  return ppRequest(ctx, `${API_BASE}/sets/${setId}`, { method: 'PATCH', body: input });
}

export async function uploadItem(
  ctx: ApiContext,
  setId: string,
  file: File,
  extra: { subject_keywords?: string; spec_text?: string; mask?: Record<string, unknown>; mask_inverted?: boolean } = {}
): Promise<ComboKitItem> {
  const form = new FormData();
  form.append('image_file', file);
  if (extra.subject_keywords) form.append('subject_keywords', extra.subject_keywords);
  if (extra.spec_text) form.append('spec_text', extra.spec_text);
  if (extra.mask) form.append('mask', JSON.stringify(extra.mask));
  if (extra.mask_inverted !== undefined) form.append('mask_inverted', String(extra.mask_inverted));
  return ppUpload<ComboKitItem>(ctx, `${API_BASE}/sets/${setId}/items`, form);
}

export async function updateItem(
  ctx: ApiContext,
  setId: string,
  itemId: string,
  input: Record<string, unknown>
): Promise<ComboKitItem> {
  return ppRequest(ctx, `${API_BASE}/sets/${setId}/items/${itemId}`, { method: 'PATCH', body: input });
}

export async function removeItem(ctx: ApiContext, setId: string, itemId: string): Promise<unknown> {
  return ppRequest(ctx, `${API_BASE}/sets/${setId}/items/${itemId}`, { method: 'DELETE' });
}

export async function deleteSet(ctx: ApiContext, setId: string): Promise<unknown> {
  return ppRequest(ctx, `${API_BASE}/sets/${setId}`, { method: 'DELETE' });
}

export async function reorderItems(ctx: ApiContext, setId: string, order: string[]): Promise<{ items: ComboKitItem[] }> {
  return ppRequest(ctx, `${API_BASE}/sets/${setId}/items/order`, { body: { order } });
}

export async function setPrimaryItem(ctx: ApiContext, setId: string, itemId: string): Promise<ComboKitItem> {
  return ppRequest(ctx, `${API_BASE}/sets/${setId}/items/${itemId}/primary`, { method: 'POST' });
}

// 算法预框选：后端本地分割出主体轮廓并直接落库，作为蒙版的初始多边形。
// status 为 unavailable 表示分割不可信（未识别到主体），此时 points 为空，
// 前端回落默认六边形，不视为错误。
export async function autoMaskItem(
  ctx: ApiContext,
  setId: string,
  itemId: string
): Promise<{ item_id: string; points: Array<[number, number]>; status: 'applied' | 'unavailable' }> {
  return ppRequest(ctx, `${API_BASE}/sets/${setId}/items/${itemId}/auto-mask`, { method: 'POST' });
}

// 三个长耗时 AI 动作（主体解析 / 文本 / 生图）单次耗时远超前端 30s HTTP 超时，
// 后端改为「提交任务立即返回 + 前端轮询任务状态」，避免请求超时与重复扣费。
export type ComboKitTaskType = 'subject' | 'text' | 'image';
export type ComboKitTaskStatus = 'queued' | 'running' | 'completed' | 'failed';

export interface ComboKitTaskProgress {
  current: number;
  total: number;
  label: string;
}

export interface ComboKitTask<T = Record<string, unknown>> {
  task_id: string;
  set_id: string;
  task_type: ComboKitTaskType;
  status: ComboKitTaskStatus;
  progress: ComboKitTaskProgress | null;
  result: T | null;
  error_kind: string;
  error_message: string;
  attempt_count: number;
  started_at: string | null;
  finished_at: string | null;
}

export async function startAnalyzeSubject(ctx: ApiContext, setId: string, itemIds?: string[]): Promise<ComboKitTask> {
  return ppRequest(ctx, `${API_BASE}/sets/${setId}/analyze-subject`, { body: itemIds ? { item_ids: itemIds } : {} });
}

export async function getComboKitTask(ctx: ApiContext, setId: string, taskType: ComboKitTaskType): Promise<ComboKitTask> {
  return ppRequest(ctx, `${API_BASE}/sets/${setId}/tasks/${taskType}`);
}

export async function getPrompt(ctx: ApiContext, setId: string): Promise<Record<string, unknown>> {
  return ppRequest(ctx, `${API_BASE}/sets/${setId}/prompt`);
}

export async function savePrompt(
  ctx: ApiContext,
  setId: string,
  input: { base_prompt_a?: string; base_prompt_b?: string; image_prompts?: Record<string, string> }
): Promise<Record<string, unknown>> {
  return ppRequest(ctx, `${API_BASE}/sets/${setId}/prompt`, { body: input });
}

export async function startGenerateText(ctx: ApiContext, setId: string): Promise<ComboKitTask> {
  return ppRequest(ctx, `${API_BASE}/sets/${setId}/generate-text`, { method: 'POST' });
}

export async function startGenerateImages(ctx: ApiContext, setId: string, roles?: string[]): Promise<ComboKitTask> {
  return ppRequest(ctx, `${API_BASE}/sets/${setId}/generate-images`, { method: 'POST', body: roles && roles.length ? { roles } : {} });
}

export async function deleteGeneratedImage(ctx: ApiContext, setId: string, role: string): Promise<{ images: Array<{ role: string; label: string; url: string; public_url?: string }>; status: string }> {
  return ppRequest(ctx, `${API_BASE}/sets/${setId}/images/${role}`, { method: 'DELETE' });
}

// 把当前水印配置立即烧到已生成的成品图上（不重新生图、不计费）。
export async function applyWatermark(ctx: ApiContext, setId: string): Promise<{ images: ComboKitSet['image_results_json']; applied: number; enabled: boolean }> {
  return ppRequest(ctx, `${API_BASE}/sets/${setId}/watermark/apply`, { method: 'POST' });
}

export async function createPreview(ctx: ApiContext, setId: string): Promise<Record<string, unknown>> {
  return ppRequest(ctx, `${API_BASE}/sets/${setId}/preview`, { method: 'POST' });
}

export async function reviewPreview(
  ctx: ApiContext,
  setId: string,
  input: { decision: 'pass' | 'reject'; reason?: string }
): Promise<ComboKitSet> {
  return ppRequest(ctx, `${API_BASE}/sets/${setId}/preview`, { method: 'PATCH', body: input });
}

export async function exportComboDianxiaomi(ctx: ApiContext, setId: string): Promise<void> {
  await ppDownload(ctx, `${API_BASE}/sets/${setId}/export-dianxiaomi`, `combo_dxm_${setId.slice(0, 8)}.xlsx`);
}

function assetToken(): string {
  const t = getAuthToken();
  return t ? `?token=${encodeURIComponent(t)}` : '';
}

export function comboKitOriginUrl(setId: string, name: string): string {
  return `/api/combo-kit/originals/${setId}/${name}${assetToken()}`;
}

export function comboKitGeneratedUrl(setId: string, name: string): string {
  return `/api/combo-kit/generated/${setId}/${name}${assetToken()}`;
}
