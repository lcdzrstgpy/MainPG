import { httpJson } from "../../../transport/http/client";
import type {
  TemuAttributes,
  TemuBuildPayloadResult,
  TemuCategory,
  TemuDraftLibrary,
  TemuCategoryPath,
  TemuFreightTemplate,
  TemuGoodsItem,
  TemuGoodsDraft,
  TemuImageUploadResult,
  TemuLowStockResult,
  TemuParentSpec,
  TemuPublishResult,
  TemuSettingsStatus,
  TemuSizeChartInfo,
  TemuSpecCreated,
  TemuTokenInfo,
  TemuWarehouseGroup,
} from "../types";

const BASE = "/api/temu-publish";

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== "") search.set(key, String(value));
  });
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const temuPublishApi = {
  settings: () => httpJson<TemuSettingsStatus>(`${BASE}/settings`),
  saveSettings: (payload: {
    yl_client?: string;
    yl_token?: string;
    access_token?: string;
    clear_yl_client?: boolean;
    clear_yl_token?: boolean;
    clear_access_token?: boolean;
  }) => httpJson<TemuSettingsStatus>(`${BASE}/settings`, { method: "PUT", body: payload }),
  /** 解绑当前账号的店铺（只删自己那份凭证，不影响其他账号）。 */
  clearSettings: () => httpJson<TemuSettingsStatus>(`${BASE}/settings`, { method: "DELETE" }),
  /** refresh=true 跳过服务端 10 分钟缓存（用户点「重新校验」时用） */
  verifyToken: (refresh = false) =>
    httpJson<TemuTokenInfo>(`${BASE}/token/verify?refresh=${refresh ? "true" : "false"}`, {
      method: "POST",
    }),

  categories: (parentCatId = 0) =>
    httpJson<{ categoryDTOList: TemuCategory[] }>(
      `${BASE}/categories${query({ parent_cat_id: parentCatId })}`,
    ),
  matchCategory: (searchText: string, siteId = 100) =>
    httpJson<{ categoryPathDTOS: TemuCategoryPath[] }>(`${BASE}/categories/match`, {
      method: "POST",
      body: { search_text: searchText, site_id: siteId },
    }),
  attributes: (catId: number) =>
    httpJson<TemuAttributes>(`${BASE}/categories/${catId}/attributes`),
  parentSpecs: () =>
    httpJson<{ parentSpecDTOS: TemuParentSpec[] }>(`${BASE}/parent-specs`),
  createSpec: (parentSpecId: number, specName: string) =>
    httpJson<TemuSpecCreated>(`${BASE}/specs`, {
      method: "POST",
      body: { parent_spec_id: parentSpecId, spec_name: specName },
    }),
  freightTemplates: (siteIds: number[]) =>
    httpJson<{ freightTemplates: TemuFreightTemplate[] }>(
      `${BASE}/freight-templates${query({ site_ids: siteIds.join(",") || "100" })}`,
    ),
  warehouses: (siteIds: number[]) =>
    httpJson<{ warehouseDTOList: TemuWarehouseGroup[] }>(
      `${BASE}/warehouses${query({ site_ids: siteIds.join(",") || "100" })}`,
    ),
  /** refresh=true 跳过服务端 60 秒缓存（点「刷新」时用） */
  /**
   * 草稿库（服务端分页）：聚合扫描很贵，后端把全量结果缓存 60 秒，
   * 来源筛选 / 关键字 / 翻页都只是在缓存上切片，不会重扫任务和批次。
   */
  draftLibrary: (params: {
    page?: number;
    pageSize?: number;
    source?: "" | "ai" | "pod";
    q?: string;
    refresh?: boolean;
  } = {}) =>
    httpJson<TemuDraftLibrary>(
      `${BASE}/draft-library${query({
        page: params.page ?? 1,
        page_size: params.pageSize ?? 30,
        source: params.source ?? "",
        q: params.q ?? "",
        refresh: params.refresh ? "true" : "",
      })}`,
    ),
  /** 查店铺里的商品：按 SKC 货号或标题（发布后核对 / 查重） */
  searchGoods: (params: { extCode?: string; title?: string; page?: number; pageSize?: number }) =>
    httpJson<{ total: number; items: TemuGoodsItem[] }>(
      `${BASE}/goods${query({
        ext_code: params.extCode,
        title: params.title,
        page: params.page,
        page_size: params.pageSize,
      })}`,
    ),
  /** 新增店铺：后端先用这条 token 打一次网关，验证通过才保存（失败会抛错，库里不留坏数据） */
  addShop: (accessToken: string, mallName = "") =>
    httpJson<TemuSettingsStatus & { verified?: boolean; mall_name?: string }>(
      `${BASE}/settings/shops`,
      { method: "POST", body: { access_token: accessToken.trim(), mall_name: mallName } },
    ),
  /** 切换店铺：用已保存的那条 token，不要求重新输入 */
  activateShop: (shopId: string) =>
    httpJson<TemuSettingsStatus>(`${BASE}/settings/shops/${shopId}/activate`, { method: "POST" }),
  removeShop: (shopId: string) =>
    httpJson<TemuSettingsStatus>(`${BASE}/settings/shops/${shopId}`, { method: "DELETE" }),

  goodsDetail: (productId: string | number) =>
    httpJson<Record<string, unknown>>(`${BASE}/goods/${productId}`),
  /** 这个类目是否需要尺码表（false → 前端隐藏「尺码表模板 ID」） */
  sizeCharts: (catId: number) => httpJson<TemuSizeChartInfo>(`${BASE}/categories/${catId}/size-charts`),

  /** 待补库存清单：扫店铺商品，列出库存为 0 的 SKU（只读） */
  lowStockGoods: (params: { limit?: number; pageSize?: number; maxPages?: number } = {}) =>
    httpJson<TemuLowStockResult>(
      `${BASE}/goods/low-stock${query({
        limit: params.limit,
        page_size: params.pageSize,
        max_pages: params.maxPages,
      })}`,
    ),

  uploadImage: (image: string, cateId?: number) =>
    httpJson<TemuImageUploadResult>(`${BASE}/images/upload`, {
      method: "POST",
      body: { image, image_biz_type: 0, size_mode: 0, cate_id: cateId },
      timeoutMs: 120_000,
    }),
  /** 详情图：服务端统一缩放到 targetWidth 再上传（商详要求所有楼层等宽）。 */
  uploadDetailImage: (image: string, targetWidth = 800, cateId?: number) =>
    httpJson<TemuImageUploadResult & { width?: number; height?: number }>(
      `${BASE}/images/upload-detail`,
      {
        method: "POST",
        body: { image, target_width: targetWidth, cate_id: cateId },
        timeoutMs: 120_000,
      },
    ),
  uploadImageUrl: (url: string, cateId?: number) =>
    httpJson<TemuImageUploadResult>(`${BASE}/images/upload-url`, {
      method: "POST",
      body: { url, image_biz_type: 0, size_mode: 0, cate_id: cateId },
      timeoutMs: 120_000,
    }),
  buildPayload: (draft: TemuGoodsDraft) =>
    httpJson<TemuBuildPayloadResult>(`${BASE}/payload/build`, {
      method: "POST",
      body: draft,
      timeoutMs: 120_000,
    }),
  /** 发布前按类目必填配置校验（bg.glo.goods.catsmandatory.get），不会提交商品。 */
  checkMandatory: (draft: TemuGoodsDraft) =>
    httpJson<{
      source: string;
      result: Record<string, unknown>;
      summary: TemuBuildPayloadResult["summary"];
      warnings: string[];
    }>(
      `${BASE}/mandatory-check`,
      { method: "POST", body: draft, timeoutMs: 120_000 },
    ),
  /** 真正提交到店铺（bg.glo.goods.add）。有外部副作用：会创建商品，非幂等。 */
  /** force=true 跳过「24 小时内重复提交」拦截（用户已在二次确认里点了继续） */
  publish: (draft: TemuGoodsDraft, force = false) =>
    httpJson<TemuPublishResult>(`${BASE}/publish${force ? "?force=true" : ""}`, {
      method: "POST",
      body: draft,
      timeoutMs: 180_000,
    }),
};
