import { httpJson } from "../../transport/http/client";

/**
 * 上报「用户是否确认 POD 使用协议」。
 *
 * 请求打到本地后端（/api/customer/pod-agreement）：本地后端只做转发，
 * 不落库。上报失败静默吞掉——协议弹窗不能因为网络问题把用户卡死。
 */
export async function reportPodAgreement(agreed: boolean): Promise<boolean> {
  try {
    const data = await httpJson<{ ok?: boolean; reported?: boolean }>(
      "/api/customer/pod-agreement",
      { method: "POST", body: { agreed } },
    );
    return data.reported === true;
  } catch {
    return false;
  }
}
