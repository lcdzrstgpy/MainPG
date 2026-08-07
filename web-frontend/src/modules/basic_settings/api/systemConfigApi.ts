import { httpJson } from "../../../transport/http/client";
import type {
  BasicSettingsForm,
  SaveBasicSettingsPayload,
  SystemConfigResponse,
  SystemConfigUpdatePayload,
} from "../types/systemConfig";

const SYSTEM_CONFIG_PATH = "/desktop/basic-settings/system-config";
// 产品处理 AI 为运营转售模式：用户通过 https://station-88.aicoming.top/ 购买 key。
// 仅作为"后端未配置 base_url"时的展示/兜底默认值，保存时不强制覆盖后端已有 base_url。
const PRIMARY_AI_BASE_URL = "https://station-88.aicoming.top/v1";

const fallbackConfig: SystemConfigResponse = {
  ok: true,
  ai: { base_url: PRIMARY_AI_BASE_URL, model: "gpt-5.6-terra" },
  image: {
    base_url: PRIMARY_AI_BASE_URL,
    model: "gpt-image-2",
    reference_model: "gpt-image-2-1k",
  },
  backup_image: { base_url: "", model: "", reference_model: "" },
  cos: { bucket: "", region: "ap-guangzhou" },
  limits: {
    text_workers: 30,
    image_workers: 15,
    text_request_limit: 30,
    image_request_limit: 15,
    image_retry_attempts: 3,
    image_provider_strategy: "balanced",
    provider_backup_share_percent: 0,
    image_stop_after_billable_failure: true,
  },
  updates: { cos_prefix: "temu-y2-control", public_base_url: "" },
  secrets: {},
  summary: {},
};

export function toSaveBasicSettingsPayload(form: BasicSettingsForm): SaveBasicSettingsPayload {
  const textModelApiKey = form.textModelApiKey.trim();
  const imageModelApiKey = form.imageModelApiKey.trim();

  return {
    ...(textModelApiKey ? { textModelApiKey } : {}),
    ...(imageModelApiKey ? { imageModelApiKey } : {}),
  };
}

export async function loadSystemConfig() {
  return httpJson<SystemConfigResponse>(SYSTEM_CONFIG_PATH);
}

export function createSystemConfigUpdatePayload(
  form: BasicSettingsForm,
  currentConfig: SystemConfigResponse | null,
): SystemConfigUpdatePayload {
  const base = currentConfig ?? fallbackConfig;
  const payload = toSaveBasicSettingsPayload(form);

  return {
    ai: {
      // 保留后端已有 base_url（系统配置/环境变量优先），避免覆盖成前端写死的默认值
      base_url: base.ai?.base_url || PRIMARY_AI_BASE_URL,
      model: form.textModel.trim() || base.ai.model,
      ...(payload.textModelApiKey ? { api_key: payload.textModelApiKey } : {}),
    },
    image: {
      base_url: base.image?.base_url || PRIMARY_AI_BASE_URL,
      model: form.imageModel.trim() || base.image.model,
      reference_model: form.referenceImageModel.trim() || base.image.reference_model,
      ...(payload.imageModelApiKey ? { api_key: payload.imageModelApiKey } : {}),
    },
    backup_image: {
      base_url: base.backup_image.base_url,
      model: base.backup_image.model,
      reference_model: base.backup_image.reference_model,
    },
    cos: {
      bucket: base.cos.bucket,
      region: base.cos.region,
    },
    limits: base.limits,
    updates: base.updates,
  };
}

export async function saveBasicSettingsDraft(form: BasicSettingsForm, currentConfig: SystemConfigResponse | null) {
  const payload = createSystemConfigUpdatePayload(form, currentConfig);
  const config = await httpJson<SystemConfigResponse>(SYSTEM_CONFIG_PATH, {
    method: "PUT",
    body: payload,
  });

  return {
    savedAt: new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
    config,
  };
}

export async function reloadBasicSettingsRuntime() {
  const config = await loadSystemConfig();

  return {
    reloadedAt: new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
    config,
  };
}
