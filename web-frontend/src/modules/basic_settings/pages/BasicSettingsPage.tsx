import { useEffect, useState } from "react";

import { loadSystemConfig, reloadBasicSettingsRuntime, saveBasicSettingsDraft } from "../api/systemConfigApi";
import { ApiKeyPanel } from "../components/ApiKeyPanel";
import type {
  ApiKeyField,
  BasicSettingsFieldErrors,
  BasicSettingsForm,
  BasicSettingsStatus,
  SystemConfigResponse,
} from "../types/systemConfig";
import "../styles/basicSettings.css";

const initialForm: BasicSettingsForm = {
  textModelApiKey: "",
  imageModelApiKey: "",
  textModel: "",
  imageModel: "",
  referenceImageModel: "",
  cosBucket: "",
  cosRegion: "",
  cosSecretId: "",
  cosSecretKey: "",
  publicMediaBaseUrl: "",
};

const initialVisibility: Record<ApiKeyField, boolean> = {
  textModelApiKey: false,
  imageModelApiKey: false,
  cosSecretId: false,
  cosSecretKey: false,
};

const defaultStatus: BasicSettingsStatus = {
  tone: "muted",
  message: "AI 文本与生图密钥由服务端统一托管，客户端只配置导出图床等本地参数。",
};

function validateForm(form: BasicSettingsForm, config: SystemConfigResponse | null): BasicSettingsFieldErrors {
  const errors: BasicSettingsFieldErrors = {};
  const textKey = form.textModelApiKey.trim();
  const imageKey = form.imageModelApiKey.trim();

  if (textKey) {
    if (textKey.length < 16) errors.textModelApiKey = "API Key 通常不少于 16 位";
    else if (/\s/.test(textKey)) errors.textModelApiKey = "不能包含空格";
  }
  if (imageKey) {
    if (imageKey.length < 16) errors.imageModelApiKey = "API Key 通常不少于 16 位";
    else if (/\s/.test(imageKey)) errors.imageModelApiKey = "不能包含空格";
  }
  const cosSecretId = form.cosSecretId.trim();
  const cosSecretKey = form.cosSecretKey.trim();
  const cosBucket = form.cosBucket.trim();
  const cosRegion = form.cosRegion.trim();
  const savedCosBucket = String(config?.cos?.bucket || "").trim();
  const savedCosRegion = String(config?.cos?.region || "").trim();
  const hasCosChange = Boolean(
    cosSecretId
      || cosSecretKey
      || cosBucket !== savedCosBucket
      || cosRegion !== savedCosRegion,
  );
  const hasSavedCosSecrets = Boolean(
    config?.secrets?.cos?.secret_id_configured && config?.secrets?.cos?.secret_key_configured,
  );
  if (hasCosChange && (!cosBucket || !cosRegion)) {
    if (!cosBucket) errors.cosSecretId = "配置 COS 时必须填写存储桶";
    if (!cosRegion) errors.cosSecretKey = "配置 COS 时必须填写所属地域";
  }
  if (!hasSavedCosSecrets && hasCosChange && (!cosSecretId || !cosSecretKey)) {
    if (!cosSecretId) errors.cosSecretId = "首次配置 COS 时必须填写 SecretId";
    if (!cosSecretKey) errors.cosSecretKey = "首次配置 COS 时必须填写 SecretKey";
  }
  const publicBaseUrl = form.publicMediaBaseUrl.trim();
  if (publicBaseUrl && !/^https:\/\//i.test(publicBaseUrl)) {
    errors.cosSecretKey = "公共媒体地址必须使用 HTTPS";
  }
  return errors;
}

export function BasicSettingsPage() {
  const [form, setForm] = useState<BasicSettingsForm>(initialForm);
  const [visibleFields, setVisibleFields] = useState<Record<ApiKeyField, boolean>>(initialVisibility);
  const [fieldErrors, setFieldErrors] = useState<BasicSettingsFieldErrors>({});
  const [status, setStatus] = useState<BasicSettingsStatus>(defaultStatus);
  const [config, setConfig] = useState<SystemConfigResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    loadSystemConfig()
      .then((nextConfig) => {
        if (!active) return;
        setConfig(nextConfig);
        // 当前配置中的模型回填表单
        setForm((prev) => ({
          ...prev,
          textModel: nextConfig.ai?.model || prev.textModel,
          imageModel: nextConfig.image?.model || prev.imageModel,
          referenceImageModel: nextConfig.image?.reference_model || prev.referenceImageModel,
          cosBucket: nextConfig.cos?.bucket || prev.cosBucket,
          cosRegion: nextConfig.cos?.region || prev.cosRegion,
          publicMediaBaseUrl: nextConfig.updates?.public_base_url || prev.publicMediaBaseUrl,
        }));
        setStatus({ tone: "success", message: "已读取后端系统配置状态。" });
      })
      .catch((error) => {
        if (!active) return;
        setStatus({ tone: "error", message: `读取配置失败：${error.message}` });
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, []);

  const updateField = (field: keyof BasicSettingsForm, value: string) => {
    setForm((current) => ({ ...current, [field]: value }));
    if (field === "textModelApiKey" || field === "imageModelApiKey" || field === "cosSecretId" || field === "cosSecretKey") {
      setFieldErrors((current) => ({ ...current, [field]: undefined }));
    }
    setStatus(defaultStatus);
  };

  const toggleVisible = (field: ApiKeyField) => {
    setVisibleFields((current) => ({ ...current, [field]: !current[field] }));
  };

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const errors = validateForm(form, config);
    setFieldErrors(errors);
    if (Object.values(errors).some(Boolean)) {
      setStatus({ tone: "error", message: "保存前请先检查输入内容。" });
      return;
    }
    try {
      const result = await saveBasicSettingsDraft(form, config);
      setConfig(result.config);
      setForm(initialForm);
      setStatus({ tone: "success", message: `配置已保存：${result.savedAt}` });
    } catch (error) {
      setStatus({ tone: "error", message: `保存失败：${error instanceof Error ? error.message : "请求失败"}` });
    }
  };

  const handleReload = async () => {
    try {
      const result = await reloadBasicSettingsRuntime();
      setConfig(result.config);
      setForm((prev) => ({
        ...prev,
        textModel: result.config.ai?.model || prev.textModel,
        imageModel: result.config.image?.model || prev.imageModel,
        referenceImageModel: result.config.image?.reference_model || prev.referenceImageModel,
        cosBucket: result.config.cos?.bucket || prev.cosBucket,
        cosRegion: result.config.cos?.region || prev.cosRegion,
        publicMediaBaseUrl: result.config.updates?.public_base_url || prev.publicMediaBaseUrl,
      }));
      setStatus({ tone: "success", message: `已重新读取：${result.reloadedAt}` });
    } catch (error) {
      setStatus({ tone: "error", message: `重新读取失败：${error instanceof Error ? error.message : "请求失败"}` });
    }
  };

  return (
    <form className="settings-page" onSubmit={handleSubmit}>
      <section className="settings-hero">
        <span className="settings-hero-icon iconfont icon-key" aria-hidden="true" />
        <div>
          <p className="eyebrow">BASIC SETTINGS</p>
          <h1>系统配置</h1>
          <p>AI 文本模型、生图模型和上游密钥由服务端统一托管，用户侧不需要也不应该填写模型 API Key。</p>
        </div>
      </section>

      <section className="settings-grid" aria-label="AI 提供方配置">
        <div className="settings-card settings-card-wide">
          <div className="settings-card-head">
            <div>
              <h3>AI 服务密钥</h3>
              <p className="settings-card-description">
                文本识别、生图、标题生成等调用统一走服务端密钥与积分账本；本地页面不再展示或保存 AI API Key，避免用户自行配置导致串账。
              </p>
            </div>
            <span className="api-key-configured-badge is-configured">服务端托管</span>
          </div>
        </div>

        <div className="settings-card settings-card-wide">
          <div className="settings-card-head">
            <div>
              <h3>导出图床（腾讯 COS）</h3>
              <p className="settings-card-description">预检通过并导出时，产品图片会上传到该 COS 桶；密钥仅加密保存在本机，不会回显。</p>
            </div>
            <span className={`api-key-configured-badge ${config?.summary?.cos_configured ? "is-configured" : ""}`}>
              {config?.summary?.cos_configured ? "已配置" : "未配置"}
            </span>
          </div>
          <div className="settings-row">
            <label className="settings-field">
              <span>存储桶</span>
              <input value={form.cosBucket} placeholder="例如 temu-images-123-1429573868" onChange={(event) => updateField("cosBucket", event.target.value)} />
            </label>
            <label className="settings-field">
              <span>所属地域</span>
              <input value={form.cosRegion} placeholder="例如 ap-guangzhou" onChange={(event) => updateField("cosRegion", event.target.value)} />
            </label>
          </div>
          <div className="settings-grid settings-grid-inner">
            <ApiKeyPanel
              fieldId="cosSecretId"
              title="COS SecretId"
              description="创建“仅该桶读写”子账号密钥后填入；留空不修改已保存密钥。"
              keyLabel="SecretId"
              value={form.cosSecretId}
              placeholder="留空不修改 SecretId"
              visible={visibleFields.cosSecretId}
              configured={Boolean(config?.secrets?.cos?.secret_id_configured)}
              error={fieldErrors.cosSecretId}
              onChange={(value) => updateField("cosSecretId", value)}
              onToggleVisible={() => toggleVisible("cosSecretId")}
            />
            <ApiKeyPanel
              fieldId="cosSecretKey"
              title="COS SecretKey"
              description="请不要在聊天里发送密钥，直接在本页输入并保存。"
              keyLabel="SecretKey"
              value={form.cosSecretKey}
              placeholder="留空不修改 SecretKey"
              visible={visibleFields.cosSecretKey}
              configured={Boolean(config?.secrets?.cos?.secret_key_configured)}
              error={fieldErrors.cosSecretKey}
              onChange={(value) => updateField("cosSecretKey", value)}
              onToggleVisible={() => toggleVisible("cosSecretKey")}
            />
          </div>
          <label className="settings-field">
            <span>公共媒体地址（可选，仅静态图床兜底）</span>
            <input value={form.publicMediaBaseUrl} placeholder="https://你的公开域名" onChange={(event) => updateField("publicMediaBaseUrl", event.target.value)} />
          </label>
        </div>
      </section>

      <footer className="settings-actions">
        <span className={`settings-status is-${status.tone}`}>{status.message}</span>
        <div className="settings-action-buttons">
          <button className="primary-button" type="submit" disabled={loading}>
            保存配置
          </button>
          <button className="settings-secondary-button" type="button" disabled={loading} onClick={handleReload}>
            重新读取
          </button>
        </div>
      </footer>
    </form>
  );
}
