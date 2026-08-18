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
    baseUrl: "",
    token: getAuthToken(),
    workspaceId:
      account.workspace_id ||
      account.workspaceId ||
      account.workspace_code ||
      account.workspaceCode ||
      "default",
  };
}
