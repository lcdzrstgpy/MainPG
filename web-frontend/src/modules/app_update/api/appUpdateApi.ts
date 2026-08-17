import { httpJson } from "../../../transport/http/client";
import type { AppUpdateStatus } from "../updateState";

const STATUS_PATH = "/api/app-update/status";
const CHECK_PATH = "/api/app-update/check";
const INSTALL_PATH = "/api/app-update/install";

export const appUpdateApi = {
  status: () => httpJson<AppUpdateStatus>(STATUS_PATH),
  check: () => httpJson<AppUpdateStatus>(CHECK_PATH, { method: "POST" }),
  install: () => httpJson<AppUpdateStatus>(INSTALL_PATH, { method: "POST" }),
};
