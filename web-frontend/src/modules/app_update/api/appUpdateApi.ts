import { httpJson, toUserMessage } from "../../../transport/http/client";
import type { AppUpdateStatus, PatchStatus } from "../updateState";

const STATUS_PATH = "/api/app-update/status";
const CHECK_PATH = "/api/app-update/check";
const INSTALL_PATH = "/api/app-update/install";
const SNOOZE_PATH = "/api/app-update/snooze";
const PATCH_STATUS_PATH = "/api/app-update/patch/status";
const PATCH_CHECK_PATH = "/api/app-update/patch/check";
const PATCH_INSTALL_PATH = "/api/app-update/patch/install";

/**
 * 后端把更新失败原因放在 `error` 里，内容是英文技术文案
 * （如 `Downloaded installer SHA-256 does not match the signed manifest.`），
 * 界面以前直接原样渲染。这里统一翻成中文，各展示点拿到的就是人话。
 */
function localizeError<T extends { error?: string | null }>(status: T): T {
  if (!status?.error) return status;
  return { ...status, error: toUserMessage(status.error) };
}

export const appUpdateApi = {
  status: () => httpJson<AppUpdateStatus>(STATUS_PATH).then(localizeError),
  check: () => httpJson<AppUpdateStatus>(CHECK_PATH, { method: "POST" }).then(localizeError),
  install: () => httpJson<AppUpdateStatus>(INSTALL_PATH, { method: "POST" }).then(localizeError),
  snooze: () => httpJson<AppUpdateStatus>(SNOOZE_PATH, { method: "POST" }).then(localizeError),
  patchStatus: () => httpJson<PatchStatus>(PATCH_STATUS_PATH).then(localizeError),
  patchCheck: () => httpJson<PatchStatus>(PATCH_CHECK_PATH, { method: "POST" }).then(localizeError),
  patchInstall: () => httpJson<PatchStatus>(PATCH_INSTALL_PATH, { method: "POST" }).then(localizeError),
};
