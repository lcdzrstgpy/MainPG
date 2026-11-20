import { getAuthAccount, getAuthToken } from "../../../transport/http/client";
import type { ApiContext } from "./client";

type AccountLike = {
  workspace_id?: string;
  workspaceId?: string;
  workspace_code?: string;
  workspaceCode?: string;
};

export function productProcessingApiContext(): ApiContext {
  const account = getAuthAccount<AccountLike>() ?? {};
  return {
    // 与 transport/http 保持一致：子路径部署（/workbench/）时所有请求也要带前缀。
    baseUrl: (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, ""),
    token: getAuthToken(),
    workspaceId:
      account.workspace_id ||
      account.workspaceId ||
      account.workspace_code ||
      account.workspaceCode ||
      "default",
  };
}
