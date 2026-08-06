import { httpBlob, httpJson } from "../../../transport/http/client";
import type { ProductQueryParams, ProfitActivityProduct, ProfitActivityScope, ProfitActivitySite } from "../types/products";

export async function listProfitActivityProducts(params: ProductQueryParams) {
  const query = new URLSearchParams({
    site: params.site,
    scope: params.scope,
    skcs: params.skcs,
  });
  const data = await httpJson<{ products: ProfitActivityProduct[] }>(`/api/profit-activity/products?${query}`);
  return data.products ?? [];
}

export async function deleteProfitActivityProducts({
  site,
  skcs,
}: {
  site: ProfitActivitySite;
  skcs: string[];
}) {
  return httpJson<{ deleted?: number }>("/api/profit-activity/products", {
    method: "DELETE",
    body: { site, skcs },
  });
}

export async function downloadProfitActivityCatalog({
  site,
  scope,
}: {
  site: ProfitActivitySite;
  scope: ProfitActivityScope;
}) {
  const query = new URLSearchParams({ site, scope });
  const blob = await httpBlob(`/api/profit-activity/catalog/rebuild?${query}`, { method: "POST" });
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = `${site}_product_catalog.xlsx`;
  anchor.click();
  URL.revokeObjectURL(objectUrl);
}

