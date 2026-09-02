import { ppDownload, ppRequest, ppUpload, type ApiContext } from '../../product_processing/api/client';
import { getAuthToken } from '../../../transport/http/client';

const API_BASE = '/api/combo-kit';

export type ComboImageRole = { role: string; label: string };
export type ComboRoles = {
  image_roles: ComboImageRole[];
  default_image_prompts: Record<string, string>;
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
  text_result_json: Record<string, unknown>;
  image_results_json: Array<{ role: string; label: string; url: string; public_url?: string; provider: string; model: string; attempt_count: number }>;
  items: ComboKitItem[];
  prompt: Record<string, unknown> | Record<string, never>;
  billing: ComboKitBilling[];
  preview: Record<string, unknown> | null;
};

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
  input: { name?: string; sku?: string; sku_display?: string; description?: string; category_path?: string; category_id?: string; specs?: string[]; attributes?: Record<string, unknown> }
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

export async function analyzeSubject(ctx: ApiContext, setId: string, itemIds?: string[]): Promise<{ results: unknown[]; items: ComboKitItem[] }> {
  return ppRequest(ctx, `${API_BASE}/sets/${setId}/analyze-subject`, { body: itemIds ? { item_ids: itemIds } : {} });
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

export async function generateText(ctx: ApiContext, setId: string): Promise<{ title: string; description: string; bullets: string[] }> {
  return ppRequest(ctx, `${API_BASE}/sets/${setId}/generate-text`, { method: 'POST' });
}

export async function generateImages(ctx: ApiContext, setId: string, roles?: string[]): Promise<{ images: Array<{ role: string; label: string; url: string; public_url?: string }> }> {
  return ppRequest(ctx, `${API_BASE}/sets/${setId}/generate-images`, { method: 'POST', body: roles && roles.length ? { roles } : {} });
}

export async function deleteGeneratedImage(ctx: ApiContext, setId: string, role: string): Promise<{ images: Array<{ role: string; label: string; url: string; public_url?: string }>; status: string }> {
  return ppRequest(ctx, `${API_BASE}/sets/${setId}/images/${role}`, { method: 'DELETE' });
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
