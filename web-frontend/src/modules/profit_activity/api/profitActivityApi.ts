import type { ProductQueryParams, ProductSources, ProfitActivityProduct, ProfitActivityScope, ProfitActivitySite } from "../types/products";
import {
  AUTH_RETRY_MANAGED,
  getAuthToken,
  isSessionExpiredDetail,
  notifySessionExpired,
  toUserMessage,
} from "../../../transport/http/client";

export type ProfitActivitySiteOption = { site_code: ProfitActivitySite; display_name: string; builtin: boolean };

// 与 ProfitActivityTestPage 保持一致：默认同源请求（后端由当前站点服务，
// 如 8000 的 wh_local；可通过 localStorage profitActivityApiBase 覆盖）。
// token 解析优先级：页面手动设置（whLocalApiToken）→ 登录用户会话
// （wh_demo_token，与核价及货源模块同一工作区）→ 仅开发环境兜底本地管理员令牌。
function resolveEndpoint() {
  return {
    apiBase: localStorage.getItem("profitActivityApiBase") || "",
    token: localStorage.getItem("whLocalApiToken") || (import.meta.env.DEV ? "dev-admin-token" : ""),
  };
}

// 候选令牌：优先当前登录用户；本地开发管理员令牌仅在开发环境、未登录时兜底。
// 生产环境绝不注入 dev-admin-token，避免会话失效后静默回退获得管理员权限。
function candidateTokens(): string[] {
  const tokens = new Set<string>();
  const manual = localStorage.getItem("whLocalApiToken");
  const customer = localStorage.getItem("wh_demo_token");
  if (manual) tokens.add(manual);
  if (customer) tokens.add(customer);
  if (import.meta.env.DEV) tokens.add("dev-admin-token");
  return [...tokens];
}

/**
 * 带令牌的「受管」fetch：401 不交给全局拦截器就地判定会话失效（本模块会
 * 换候选令牌重试，单个候选被拒≠登录失效）。只有「登录会话令牌」被拒且文案
 * 命中会话失效关键词时，才在这里通知会话失效——避免历史遗留令牌
 * （whLocalApiToken）失效时把用户误踢回登录页。
 */
async function authedFetch(url: string, init: RequestInit, token: string): Promise<Response> {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(url, { ...init, headers, [AUTH_RETRY_MANAGED]: true } as RequestInit);
  if (response.status === 401 && token && token === getAuthToken()) {
    let detail = "";
    try {
      const body = (await response.clone().json()) as { detail?: unknown } | null;
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      // 非 JSON 响应（如图片二进制）无法判定会话是否失效，不误判
    }
    if (isSessionExpiredDetail(detail)) notifySessionExpired(detail);
  }
  return response;
}

// 从响应体里取出可读的错误文本：优先 detail 字符串，其次整段文本，
// 最后才回退到状态码。绝不要把 JSON 原文交给用户（以前会直接显示
// {"detail":"product_id_already_exists"}）。
function errorText(data: unknown, status: number): string {
  if (typeof data === "string" && data.trim()) return data;
  if (data && typeof data === "object") {
    const detail = (data as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) return detail;
  }
  return `请求失败 (HTTP ${status})`;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const { apiBase } = resolveEndpoint();
  const last401 = new Error("invalid bearer token");
  for (const token of candidateTokens()) {
    const response = await authedFetch(`${apiBase}${path}`, options, token);
    const text = await response.text();
    let data: unknown = text;
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      // 非 JSON 响应时保留原文
    }
    if (response.status === 401) {
      last401.message = errorText(data, response.status);
      continue; // 会话失效时回退下一个令牌重试
    }
    if (!response.ok) throw new Error(toUserMessage(errorText(data, response.status)));
    return data as T;
  }
  throw new Error(toUserMessage(last401.message));
}

export async function listProfitActivityProducts(params: ProductQueryParams) {
  const query = new URLSearchParams({
    site: params.site,
    scope: params.scope,
    product_ids: params.productIds ?? params.skcs,
  });
  const data = await request<{ products: ProfitActivityProduct[] }>(`/api/profit-activity/products?${query}`);
  return data.products ?? [];
}

export async function listProfitActivitySites() {
  const data = await request<{ sites: ProfitActivitySiteOption[] }>("/api/profit-activity/sites");
  return data.sites ?? [];
}

export async function getProfitActivitySettings() {
  return request<Record<string, unknown>>("/api/profit-activity/settings");
}

export async function saveProfitActivitySettings(payload: Record<string, unknown>) {
  return request<Record<string, unknown>>("/api/profit-activity/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function updateProfitActivitySite(siteCode: ProfitActivitySite, payload: Record<string, unknown>) {
  return request<{ site: Record<string, unknown> }>(`/api/profit-activity/sites/${encodeURIComponent(siteCode)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function createProfitActivitySite(payload: Record<string, unknown>) {
  return request<{ site: Record<string, unknown> }>("/api/profit-activity/sites", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function recalculateProfitActivityProducts({ sites, scope }: { sites: ProfitActivitySite[]; scope: ProfitActivityScope }) {
  return request<{ updated?: number; failed?: number; failures?: Array<Record<string, unknown>> }>("/api/profit-activity/products/recalculate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sites: sites.join(","), scope }),
  });
}

export async function listProductSources({ skc, site }: { skc: string; site: ProfitActivitySite }) {
  return request<ProductSources>(
    `/api/profit-activity/products/${encodeURIComponent(skc)}/sources?site=${site}`,
  );
}

export type ProductImageKind = "product" | "source" | "attachment";

/**
 * 加载产品/货源图片。接口需要 Bearer 鉴权，不能直接用 <img src>，
 * 这里带 token 拉取后转成 object URL 供前端展示。
 */
export async function loadProductImage({
  skc,
  site,
  kind,
  group = 0,
  index = 0,
  version,
}: {
  skc: string;
  site: ProfitActivitySite;
  kind: ProductImageKind;
  group?: number;
  index?: number;
  /** 图片路径变更时传入，避免同一接口地址命中浏览器旧缓存。 */
  version?: string;
}): Promise<string> {
  const { apiBase } = resolveEndpoint();
  const token = candidateTokens()[0];
  const query = new URLSearchParams({ site, kind, group: String(group), index: String(index) });
  if (version) query.set("v", version);
  const response = await authedFetch(`${apiBase}/api/profit-activity/products/${encodeURIComponent(skc)}/image?${query}`, {
    cache: "no-store",
  }, token);
  if (!response.ok) throw new Error(toUserMessage(await response.text()));
  return URL.createObjectURL(await response.blob());
}

/**
 * 手动上传/替换产品的 SKC 对应图（主图）。
 * 走 multipart form，携带产品现有数值字段，仅替换 image。
 */
export async function updateProductImage({
  site,
  skc,
  image,
  selling_price,
  cost_price,
  weight_kg,
  note,
  source_url,
  source_groups,
}: {
  site: ProfitActivitySite;
  skc: string;
  image: File;
  selling_price?: number | null;
  cost_price?: number | null;
  weight_kg?: number | null;
  note?: string | null;
  source_url?: string | null;
  source_groups?: Array<{ source_url?: string; image_paths?: string[]; cost?: number | null }>;
}) {
  const { apiBase } = resolveEndpoint();
  const token = candidateTokens()[0];
  const form = new FormData();
  form.set("site", site);
  form.set("skc", skc);
  if (selling_price != null) form.set("selling_price", String(selling_price));
  if (cost_price != null) form.set("cost_price", String(cost_price));
  if (weight_kg != null) form.set("weight_kg", String(weight_kg));
  if (note) form.set("note", note);
  if (source_url) form.set("source_url", source_url);
  if (source_groups?.length) form.set("source_groups_json", JSON.stringify(source_groups));
  form.set("image", image);
  const response = await authedFetch(`${apiBase}/api/profit-activity/products/${encodeURIComponent(skc)}/update`, {
    method: "POST",
    body: form,
  }, token);
  const text = await response.text();
  let data: unknown = text;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    // 非 JSON 响应时保留原文
  }
  if (!response.ok) throw new Error(toUserMessage(errorText(data, response.status)));
  return data as { product: ProfitActivityProduct };
}

export async function updateProfitActivityProduct({
  site,
  skc,
  selling_price,
  cost_price,
  weight_kg,
  note,
  store_name,
}: {
  site: ProfitActivitySite;
  skc: string;
  selling_price?: string;
  cost_price?: string;
  weight_kg?: string;
  note?: string;
  store_name?: string;
}) {
  const body: Record<string, unknown> = { site };
  if (selling_price !== undefined && selling_price !== "") body.selling_price = selling_price;
  if (cost_price !== undefined && cost_price !== "") body.cost_price = cost_price;
  if (weight_kg !== undefined && weight_kg !== "") body.weight_kg = weight_kg;
  if (note !== undefined) body.note = note;
  if (store_name !== undefined) body.store_name = store_name;
  return request<{ product: ProfitActivityProduct }>(`/api/profit-activity/products/${encodeURIComponent(skc)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** 产品库统一编辑弹窗保存：数值、备注和两类图片使用同一个 multipart 请求提交。 */
export async function saveProfitActivityProductEdit({
  site,
  currentSite,
  skc,
  productId,
  sellingPrice,
  costPrice,
  weightKg,
  note,
  storeName,
  productImage,
  attachmentImage,
  clearProductImage,
  clearAttachmentImage,
}: {
  site: ProfitActivitySite;
  currentSite?: ProfitActivitySite;
  skc: string;
  productId?: string;
  sellingPrice: string;
  costPrice: string;
  weightKg: string;
  note: string;
  storeName?: string;
  productImage?: File | null;
  attachmentImage?: File | null;
  clearProductImage?: boolean;
  clearAttachmentImage?: boolean;
}) {
  const { apiBase } = resolveEndpoint();
  const token = candidateTokens()[0];
  const form = new FormData();
  form.set("site", site);
  if (currentSite) form.set("current_site", currentSite);
  form.set("current_skc", skc);
  form.set("product_id", productId || skc);
  form.set("selling_price", sellingPrice);
  form.set("cost_price", costPrice);
  form.set("weight_kg", weightKg);
  form.set("note", note);
  form.set("store_name", storeName ?? "");
  if (productImage) form.set("image", productImage);
  if (attachmentImage) form.set("attachment_image", attachmentImage);
  if (clearProductImage) form.set("clear_product_image", "true");
  if (clearAttachmentImage) form.set("clear_attachment_image", "true");
  const response = await authedFetch(`${apiBase}/api/profit-activity/products/${encodeURIComponent(skc)}/update`, {
    method: "POST",
    body: form,
  }, token);
  const text = await response.text();
  let data: unknown = text;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    // 非 JSON 响应时保留原文
  }
  if (!response.ok) throw new Error(toUserMessage(errorText(data, response.status)));
  return data as { product: ProfitActivityProduct };
}

/**
 * 更新产品的货源组（链接 + 截图）：非核价入库产品在货源侧边栏修改
 * “图片和对应链接”后保存。走 multipart，仅更新 source_groups_json 与
 * 目标组的截图，其他字段沿用当前记录。groupImages 支持一次提交多个
 * 货源组的新截图（键为组号），与 source_groups_json 重建后的组号对应。
 */
export async function updateProductSourceGroup({
  site,
  skc,
  group,
  sourceGroups,
  image,
  groupImages,
}: {
  site: ProfitActivitySite;
  skc: string;
  group: number;
  sourceGroups: Array<{ source_url?: string; image_paths?: string[]; cost?: number | null }>;
  image?: File | null;
  groupImages?: Record<number, File>;
}) {
  const { apiBase } = resolveEndpoint();
  const token = candidateTokens()[0];
  const form = new FormData();
  form.set("site", site);
  form.set("skc", skc);
  form.set("source_groups_json", JSON.stringify(sourceGroups));
  if (image) form.set(`source_group_image_${group}`, image);
  if (groupImages) {
    for (const [groupIndex, file] of Object.entries(groupImages)) {
      form.set(`source_group_image_${groupIndex}`, file);
    }
  }
  const response = await authedFetch(`${apiBase}/api/profit-activity/products/${encodeURIComponent(skc)}/update`, {
    method: "POST",
    body: form,
  }, token);
  const text = await response.text();
  let data: unknown = text;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    // 非 JSON 响应时保留原文
  }
  if (!response.ok) throw new Error(toUserMessage(errorText(data, response.status)));
  return data as { product: ProfitActivityProduct };
}

export async function deleteProfitActivityProducts({
  site,
  skcs,
}: {
  site: ProfitActivitySite;
  skcs: string[];
}) {
  return request<{ deleted?: number }>("/api/profit-activity/products", {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ site, skcs }),
  });
}

export async function downloadProfitActivityCatalog({
  sites,
  scope,
}: {
  sites: ProfitActivitySite[];
  scope: ProfitActivityScope;
}) {
  const { apiBase } = resolveEndpoint();
  const token = candidateTokens()[0];
  const response = await authedFetch(`${apiBase}/api/profit-activity/catalog/rebuild?${new URLSearchParams({ sites: sites.join(","), scope })}`, {
    method: "POST",
  }, token);
  if (!response.ok) throw new Error(toUserMessage(await response.text()));
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = "product_catalog.xlsx";
  anchor.click();
  URL.revokeObjectURL(objectUrl);
}
