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
};

const initialVisibility: Record<ApiKeyField, boolean> = {
  textModelApiKey: false,
  imageModelApiKey: false,
};

const defaultStatus: BasicSettingsStatus = {
  tone: "muted",
  message: "填入模型名或 API Key 后保存，修改将应用到产品处理模块。",
};

function validateForm(form: BasicSettingsForm): BasicSettingsFieldErrors {
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
    if (field === "textModelApiKey" || field === "imageModelApiKey") {
      setFieldErrors((current) => ({ ...current, [field]: undefined }));
    }
    setStatus(defaultStatus);
  };

  const toggleVisible = (field: ApiKeyField) => {
    setVisibleFields((current) => ({ ...current, [field]: !current[field] }));
  };

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const errors = validateForm(form);
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
          <p>配置 AI 中转提供方参数：模型名称与 API Key。修改后重启服务或刷新产品处理模块生效。</p>
        </div>
      </section>

      <section className="settings-grid" aria-label="AI 提供方配置">
        {/* 文本模型 API Key（仅保留用户填写入口） */}
        <div className="settings-card">
          <ApiKeyPanel
            fieldId="textModelApiKey"
            title="文本模型 API Key"
            description="用于标题、卖点、描述、翻译和运营文案等文本 AI 能力。"
            value={form.textModelApiKey}
            placeholder="留空不修改文本模型 API Key"
            visible={visibleFields.textModelApiKey}
            configured={Boolean(config?.secrets.ai?.api_key_configured)}
            error={fieldErrors.textModelApiKey}
            onChange={(value) => updateField("textModelApiKey", value)}
            onToggleVisible={() => toggleVisible("textModelApiKey")}
          />
        </div>

        {/* 生图模型 API Key（仅保留用户填写入口） */}
        <div className="settings-card">
          <ApiKeyPanel
            fieldId="imageModelApiKey"
            title="生图模型 API Key"
            description="用于四宫格拼接、详情图生成和图片处理相关能力。"
            value={form.imageModelApiKey}
            placeholder="留空不修改生图模型 API Key"
            visible={visibleFields.imageModelApiKey}
            configured={Boolean(config?.secrets.image?.api_key_configured)}
            error={fieldErrors.imageModelApiKey}
            onChange={(value) => updateField("imageModelApiKey", value)}
            onToggleVisible={() => toggleVisible("imageModelApiKey")}
          />
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
