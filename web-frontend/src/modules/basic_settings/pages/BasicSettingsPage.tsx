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
};

const initialVisibility: Record<ApiKeyField, boolean> = {
  textModelApiKey: false,
  imageModelApiKey: false,
};

const defaultStatus: BasicSettingsStatus = {
  tone: "muted",
  message: "当前为前端演示保存，不会写入真实密钥。",
};

function validateForm(form: BasicSettingsForm): BasicSettingsFieldErrors {
  const errors: BasicSettingsFieldErrors = {};
  const textKey = form.textModelApiKey.trim();
  const imageKey = form.imageModelApiKey.trim();
  const validateKey = (value: string, label: string) => {
    if (value.length < 16) return `请确认${label}是否完整，API Key 通常不少于 16 位。`;
    if (/\s/.test(value)) return `${label}不能包含空格。`;
    if (/[\u4e00-\u9fa5]/.test(value)) return `${label}不能包含中文。`;
    if (/^\d+$/.test(value)) return `${label}不能是纯数字。`;
    if (/^(.)\1+$/.test(value)) return `${label}不能是重复字符。`;
    return "";
  };

  if (!textKey && !imageKey) {
    errors.textModelApiKey = "请至少填写一个模型 API Key。";
    errors.imageModelApiKey = "请至少填写一个模型 API Key。";
    return errors;
  }

  if (textKey) errors.textModelApiKey = validateKey(textKey, "文本模型 API Key") || undefined;
  if (imageKey) errors.imageModelApiKey = validateKey(imageKey, "图生图模型 API Key") || undefined;

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
        setStatus({ tone: "success", message: "已读取后端系统配置状态。" });
      })
      .catch((error) => {
        if (!active) return;
        setStatus({ tone: "error", message: `读取配置失败：${error.message}` });
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, []);

  const updateField = (field: ApiKeyField, value: string) => {
    setForm((current) => ({ ...current, [field]: value }));
    setFieldErrors((current) => ({ ...current, [field]: undefined }));
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
      setStatus({ tone: "error", message: "保存前请先检查 API Key 填写内容。" });
      return;
    }

    try {
      const result = await saveBasicSettingsDraft(form, config);
      setConfig(result.config);
      setForm(initialForm);
      setStatus({ tone: "success", message: `配置已保存到后端：${result.savedAt}` });
    } catch (error) {
      setStatus({ tone: "error", message: `保存失败：${error instanceof Error ? error.message : "请求失败"}` });
    }
  };

  const handleReload = async () => {
    try {
      const result = await reloadBasicSettingsRuntime();
      setConfig(result.config);
      setStatus({ tone: "success", message: `已重新读取后端配置：${result.reloadedAt}` });
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
          <p>当前演示页只开放模型 API Key 填写；其他运行参数由后端和本地运行环境固定管理。</p>
        </div>
      </section>

      <section className="settings-grid" aria-label="模型 API 配置">
        <ApiKeyPanel
          fieldId="textModelApiKey"
          title="文本模型 API"
          description="用于标题、卖点、描述、翻译和运营文案等文本 AI 能力。"
          value={form.textModelApiKey}
          placeholder="留空不修改文本模型 API Key"
          visible={visibleFields.textModelApiKey}
          configured={Boolean(config?.secrets.ai?.api_key_configured)}
          error={fieldErrors.textModelApiKey}
          onChange={(value) => updateField("textModelApiKey", value)}
          onToggleVisible={() => toggleVisible("textModelApiKey")}
        />
        <ApiKeyPanel
          fieldId="imageModelApiKey"
          title="图生图模型 API"
          description="用于参考图生成、精致作图和图片处理相关能力。"
          value={form.imageModelApiKey}
          placeholder="留空不修改图生图模型主 API Key"
          visible={visibleFields.imageModelApiKey}
          configured={Boolean(config?.secrets.image?.api_key_configured)}
          error={fieldErrors.imageModelApiKey}
          onChange={(value) => updateField("imageModelApiKey", value)}
          onToggleVisible={() => toggleVisible("imageModelApiKey")}
        />
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
