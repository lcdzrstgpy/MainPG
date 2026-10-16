import { httpJson } from "../../../transport/http/client";

/** 手机端采集链接（云端账号维度存档，PC 端「手机端导入」拉取）。 */
export type MobileCapture = {
  capture_id: string;
  source_url: string;
  source_site: string;
  title: string;
  note: string;
  status: "pending" | "imported";
  client_type: string;
  device_name: string;
  platform: string;
  app_version: string;
  created_at: string;
  imported_at: string;
};

export type MobileCapturePage = {
  ok: boolean;
  captures: MobileCapture[];
  total: number;
  limit: number;
  offset: number;
};

export function listMobileCaptures(limit = 100, offset = 0, status = "pending") {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (status) params.set("status", status);
  return httpJson<MobileCapturePage>(`/api/customer/captures?${params.toString()}`);
}

/** PC 端导入成功后回执：把该条手机采集链接标记为已导入。 */
export function markMobileCaptureImported(captureId: string) {
  return httpJson<{ ok: boolean; capture_id: string; status: string }>(
    `/api/customer/captures/${encodeURIComponent(captureId)}/imported`,
    { method: "POST" },
  );
}

export function deleteMobileCapture(captureId: string) {
  return httpJson<{ ok: boolean; capture_id: string }>(
    `/api/customer/captures/${encodeURIComponent(captureId)}`,
    { method: "DELETE" },
  );
}
