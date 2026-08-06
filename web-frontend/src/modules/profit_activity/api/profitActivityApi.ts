import type { ProductQueryParams, ProfitActivityProduct, ProfitActivityScope, ProfitActivitySite } from "../types/products";

// 与 ProfitActivityTestPage 保持一致：直连本地利润活动后端，使用 dev-admin-token。
function resolveEndpoint() {
  return {
    apiBase: localStorage.getItem("profitActivityApiBase") || "http://127.0.0.1:8000",
    token: localStorage.getItem("whLocalApiToken") || "dev-admin-token",
  };
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const { apiBase, token } = resolveEndpoint();
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
  if (!response.ok) throw new Error(typeof data === "string" ? data : JSON.stringify(data));
  return data as T;
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
  const { apiBase, token } = resolveEndpoint();
  const headers = new Headers();
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
