import type { ApiKeyField } from "../types/systemConfig";

type ApiKeyPanelProps = {
  fieldId: ApiKeyField;
  title: string;
  description: string;
  keyLabel?: string;
  value: string;
  placeholder: string;
  visible: boolean;
  configured: boolean;
  error?: string;
  onChange: (value: string) => void;
  onToggleVisible: () => void;
};

export function ApiKeyPanel({
  fieldId,
  title,
  description,
  keyLabel = "主 API Key",
  value,
  placeholder,
  visible,
  configured,
  error,
  onChange,
  onToggleVisible,
}: ApiKeyPanelProps) {
  return (
    <article className="api-key-panel">
      <div className="api-key-panel-header">
        <span className="iconfont icon-api-fill" aria-hidden="true" />
        <div>
          <h2>{title}</h2>
          <p>{description}</p>
        </div>
        <b className={`api-key-configured-badge ${configured ? "is-configured" : ""}`}>
          {configured ? "已配置" : "未配置"}
        </b>
      </div>
      <label>
        <span>{keyLabel}</span>
        <div className={`api-key-input-row ${error ? "has-error" : ""}`}>
          <input
            id={fieldId}
            type={visible ? "text" : "password"}
            value={value}
            placeholder={placeholder}
            autoComplete="off"
            aria-invalid={Boolean(error)}
            aria-describedby={error ? `${fieldId}-error` : undefined}
            onChange={(event) => onChange(event.target.value)}
          />
          <button
            className={`api-key-visibility-button iconfont ${visible ? "icon-eye-fill" : "icon-eyeclose-fill"}`}
            type="button"
            aria-label={visible ? "隐藏 API Key" : "显示 API Key"}
            title={visible ? "隐藏 API Key" : "显示 API Key"}
            onClick={onToggleVisible}
          >
            <span className="visually-hidden">{visible ? "隐藏" : "显示"}</span>
          </button>
        </div>
        {error && (
          <p className="api-key-field-error" id={`${fieldId}-error`}>
            {error}
          </p>
        )}
      </label>
    </article>
  );
}
