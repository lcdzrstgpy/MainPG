import { httpJson } from "../../../transport/http/client";

export type MobileAssetType = "photo" | "gallery" | "file";

/** 资产元数据（列表接口返回，不含原图数据）。 */
export type MobileAssetMeta = {
  asset_id: string;
  asset_type: MobileAssetType;
  file_name: string;
  mime: string;
  size_bytes: number;
  note: string;
  source_site: string;
  device_name: string;
  client_type: string;
  platform: string;
  app_version: string;
  created_at: string;
};

/** 资产详情（含 base64 原图数据，用于预览 / 下载 / 送入业务）。 */
export type MobileAssetDetail = MobileAssetMeta & { data_b64: string };

export function loadMobileAssets(limit = 48, offset = 0) {
  return httpJson<{
    ok: boolean;
    assets: MobileAssetMeta[];
    total: number;
    limit: number;
    offset: number;
  }>(`/api/customer/assets?limit=${limit}&offset=${offset}`);
}

export function loadMobileAssetDetail(assetId: string) {
  return httpJson<{ ok: boolean; asset: MobileAssetDetail }>(
    `/api/customer/assets/${encodeURIComponent(assetId)}`,
  );
}

export function deleteMobileAsset(assetId: string) {
  return httpJson<{ ok: boolean; asset_id: string }>(
    `/api/customer/assets/${encodeURIComponent(assetId)}`,
    { method: "DELETE" },
  );
}
