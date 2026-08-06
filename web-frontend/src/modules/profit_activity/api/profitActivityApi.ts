import type { ProductQueryParams, ProductSources, ProfitActivityProduct, ProfitActivityScope, ProfitActivitySite } from "../types/products";

// 与 ProfitActivityTestPage 保持一致：直连本地利润活动后端。
// token 解析优先级：页面手动设置（whLocalApiToken）→ 登录用户会话
// （wh_demo_token，与核价及货源模块同一工作区）→ 本地开发管理员令牌。
function resolveEndpoint() {
  return {
    apiBase: localStorage.getItem("profitActivityApiBase") || "http://127.0.0.1:8010",
    token: localStorage.getItem("whLocalApiToken") || "dev-admin-token",
  };
}

// 候选令牌：dev-admin-token 优先（本地开发管理员，永不过期兜底）。
function candidateTokens(): string[] {
  const tokens = new Set<string>();
  tokens.add("dev-admin-token");  // 优先：本地开发管理员
  const manual = localStorage.getItem("whLocalApiToken");
  const customer = localStorage.getItem("wh_demo_token");
  if (manual) tokens.add(manual);
  if (customer) tokens.add(customer);
  return [...tokens];
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const { apiBase } = resolveEndpoint();
  const last401 = new Error("invalid bearer token");
  for (const token of candidateTokens()) {
    const headers = new Headers(options.headers);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await fetch(`${apiBase}${path}`, { ...options, headers });
    const text = await response.text();
    let data: unknown = text;
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      // 非 JSON 响应时保留原文
    }
    if (response.status === 401) {
      last401.message = typeof data === "string" ? data : JSON.stringify(data);
      continue; // 会话失效时回退下一个令牌重试
    }
    if (!response.ok) throw new Error(typeof data === "string" ? data : JSON.stringify(data));
    return data as T;
  }
  throw last401;
}

export async function listProfitActivityProducts(params: ProductQueryParams) {
  const query = new URLSearchParams({
    site: params.site,
    scope: params.scope,
    skcs: params.skcs,
  });
  const data = await request<{ products: ProfitActivityProduct[] }>(`/api/profit-activity/products?${query}`);
  return data.products ?? [];
}

export async function listProductSources({ skc, site }: { skc: string; site: ProfitActivitySite }) {
  return request<ProductSources>(
    `/api/profit-activity/products/${encodeURIComponent(skc)}/sources?site=${site}`,
  );
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
  site,
  scope,
}: {
  site: ProfitActivitySite;
  scope: ProfitActivityScope;
}) {
  const { apiBase } = resolveEndpoint();
  const headers = new Headers();
  const token = candidateTokens()[0];
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${apiBase}/api/profit-activity/catalog/rebuild?${new URLSearchParams({ site, scope })}`, {
    method: "POST",
    headers,
  });
  if (!response.ok) throw new Error(await response.text());
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = `${site}_product_catalog.xlsx`;
  anchor.click();
  URL.revokeObjectURL(objectUrl);
}
