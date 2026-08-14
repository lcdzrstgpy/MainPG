export type ApiKeyField = "textModelApiKey" | "imageModelApiKey" | "cosSecretId" | "cosSecretKey";

export type BasicSettingsForm = {
  textModelApiKey: string;
  imageModelApiKey: string;
  textModel: string;
  imageModel: string;
  referenceImageModel: string;
  cosBucket: string;
  cosRegion: string;
  cosSecretId: string;
  cosSecretKey: string;
  publicMediaBaseUrl: string;
};

export type BasicSettingsFieldErrors = Partial<Record<ApiKeyField, string>>;

export type BasicSettingsStatus = {
  tone: "muted" | "success" | "error";
  message: string;
};

export type SaveBasicSettingsPayload = {
  textModelApiKey?: string;
  imageModelApiKey?: string;
  cosSecretId?: string;
  cosSecretKey?: string;
};

export type TextAiConfig = {
  base_url: string;
  model: string;
  api_key?: string | null;
  clear_api_key?: boolean;
};

export type ImageModelConfig = {
  base_url: string;
  model: string;
  reference_model: string;
  api_key?: string | null;
  clear_api_key?: boolean;
};

export type CosConfig = {
  bucket: string;
  region: string;
  secret_id?: string | null;
  secret_key?: string | null;
  clear_secret_id?: boolean;
  clear_secret_key?: boolean;
};

export type RuntimeLimits = {
  text_workers: number;
  image_workers: number;
  text_request_limit: number;
  image_request_limit: number;
  image_retry_attempts: number;
  image_provider_strategy: "balanced" | "primary_first" | "backup_first" | "cost_first";
  provider_backup_share_percent: number;
  image_stop_after_billable_failure: boolean;
};

export type UpdateConfig = {
  cos_prefix: string;
  public_base_url: string;
};

export type SystemConfigResponse = {
  ok: boolean;
  ai: TextAiConfig;
  image: ImageModelConfig;
  backup_image: ImageModelConfig;
  cos: CosConfig;
  limits: RuntimeLimits;
  updates: UpdateConfig;
  secrets: {
    ai?: { api_key_configured?: boolean };
    image?: { api_key_configured?: boolean };
    backup_image?: { api_key_configured?: boolean };
    cos?: { secret_id_configured?: boolean; secret_key_configured?: boolean };
  };
  summary: {
    ai_configured?: boolean;
    image_configured?: boolean;
    backup_image_configured?: boolean;
    cos_configured?: boolean;
    text_workers?: number;
    image_workers?: number;
    cos_region?: string;
    update_public_base_url_configured?: boolean;
  };
  message?: string;
};

export type SystemConfigUpdatePayload = {
  ai: TextAiConfig;
  image: ImageModelConfig;
  backup_image: ImageModelConfig;
  cos: CosConfig;
  limits: RuntimeLimits;
  updates: UpdateConfig;
};
