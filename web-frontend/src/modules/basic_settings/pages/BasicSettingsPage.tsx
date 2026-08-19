import { useEffect, useState } from "react";

import { loadSystemConfig } from "../api/systemConfigApi";
import type { BasicSettingsStatus } from "../types/systemConfig";
import "../styles/basicSettings.css";

const defaultStatus: BasicSettingsStatus = {
  tone: "muted",
  message: "AI 服务与系统运行配置由服务端统一托管。",
};

export function BasicSettingsPage() {
  const [status, setStatus] = useState<BasicSettingsStatus>(defaultStatus);

  useEffect(() => {
    let active = true;
    loadSystemConfig()
      .then(() => {
        if (!active) return;
        setStatus({ tone: "success", message: "已读取后端系统配置状态。" });
      })
      .catch((error) => {
        if (!active) return;
        setStatus({ tone: "error", message: `读取配置失败：${error.message}` });
      })
    return () => { active = false; };
  }, []);

  return (
    <main className="settings-page">
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

      </section>

      <footer className="settings-actions">
        <span className={`settings-status is-${status.tone}`}>{status.message}</span>
      </footer>
    </main>
  );
}
