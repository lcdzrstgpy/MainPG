import { ppRequest, ppUpload, type ApiContext } from './client';

const API_BASE = '/api/product-processing';

export type ComboSource = {
  id: number;
  workspace_id: string;
  source_type: 'draft_pool' | 'upload';
  draft_id: number | null;
  title: string;
  url: string;
  local_path: string;
  created_at: string;
};

export type ComboSourceResponse = {
  sources: ComboSource[];
};

export async function listComboSources(ctx: ApiContext): Promise<ComboSourceResponse> {
  return ppRequest<ComboSourceResponse>(ctx, `${API_BASE}/combo/sources`);
}

export async function addDraftComboSource(
  ctx: ApiContext,
  draftId: number,
  title: string
): Promise<ComboSource> {
  const data = await ppRequest<{ source: ComboSource } & { id?: number }>(ctx, `${API_BASE}/combo/sources`, {
    body: { source_type: 'draft_pool', draft_id: draftId, title },
  });
  return (data.source ?? (data as unknown as ComboSource));
}

export async function uploadComboSource(
  ctx: ApiContext,
  file: File,
  title: string
): Promise<ComboSource> {
  const form = new FormData();
  form.append('image_file', file);
  form.append('source_type', 'upload');
  form.append('title', title);
  const data = await ppUpload<{ source: ComboSource } & { id?: number }>(ctx, `${API_BASE}/combo/sources/upload`, form);
  return (data.source ?? (data as unknown as ComboSource));
}

export async function removeComboSource(ctx: ApiContext, sourceId: number): Promise<void> {
  await ppRequest(ctx, `${API_BASE}/combo/sources/${sourceId}`, { method: 'DELETE' });
}

export function comboSourceImageUrl(sourceId: number): string {
  return `/product-processing/combo/sources/${sourceId}/image`;
}

export function draftImageUrl(draftId: number): string {
  return `/product-processing/drafts/${draftId}/image`;
}

// ---- 组合流程：主图生成 / 提交处理（后端编排）----

export type ComboGenerateMainResponse = {
  draft_id: number;
  main_image_path: string;
  message: string;
};

export type ComboProcessResponse = {
  task_id: number;
  message: string;
};

export type ComboDraftInput = {
  title: string;
  description: string;
  sku: string;
  skc: string;
  product_name: string;
  declared_price?: number;
  cost?: number;
  core_fields: Record<string, string | number | null | undefined>;
  combo_sources: Array<{ source_type: string; draft_id?: number | null; title: string; url: string; local_path: string }>;
  main_prompt: string;
  role_prompts: Record<string, string>;
};

export async function createComboDraft(
  ctx: ApiContext,
  input: ComboDraftInput
): Promise<{ draft: { id: number }; created: boolean }> {
  return ppRequest(ctx, `${API_BASE}/drafts`, {
    body: {
      source_type: 'manual_combo',
      source_ref: '商品自定义组合',
      skc: input.skc,
      sku: input.sku,
      product_name: input.product_name,
      title: input.title,
      description: input.description,
      cost: input.cost,
      declared_price: input.declared_price,
      main_image_url: '',
      // 组合元数据以顶层字段下发，后端会整体存入 raw_payload（与草稿池结构一致）
      combo_sources: input.combo_sources,
      main_prompt: input.main_prompt,
      role_prompts: input.role_prompts,
      ...input.core_fields,
    },
  });
}

export async function generateComboMain(
  ctx: ApiContext,
  draftId: number,
  prompt: string
): Promise<ComboGenerateMainResponse> {
  return ppRequest(ctx, `${API_BASE}/combo/generate-main`, {
    body: { draft_id: draftId, prompt },
  });
}

export async function processCombo(
  ctx: ApiContext,
  draftId: number
): Promise<ComboProcessResponse> {
  return ppRequest(ctx, `${API_BASE}/combo/process`, {
    body: { draft_id: draftId },
  });
}
