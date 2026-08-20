import { httpJson } from "../../../transport/http/client";
import type { AppUpdateStatus, PatchStatus } from "../updateState";

const STATUS_PATH = "/api/app-update/status";
const CHECK_PATH = "/api/app-update/check";
const INSTALL_PATH = "/api/app-update/install";
const SNOOZE_PATH = "/api/app-update/snooze";
const PATCH_STATUS_PATH = "/api/app-update/patch/status";
const PATCH_CHECK_PATH = "/api/app-update/patch/check";
const PATCH_INSTALL_PATH = "/api/app-update/patch/install";

export const appUpdateApi = {
  status: () => httpJson<AppUpdateStatus>(STATUS_PATH),
  check: () => httpJson<AppUpdateStatus>(CHECK_PATH, { method: "POST" }),
  install: () => httpJson<AppUpdateStatus>(INSTALL_PATH, { method: "POST" }),
  snooze: () => httpJson<AppUpdateStatus>(SNOOZE_PATH, { method: "POST" }),
  patchStatus: () => httpJson<PatchStatus>(PATCH_STATUS_PATH),
  patchCheck: () => httpJson<PatchStatus>(PATCH_CHECK_PATH, { method: "POST" }),
  patchInstall: () => httpJson<PatchStatus>(PATCH_INSTALL_PATH, { method: "POST" }),
};
