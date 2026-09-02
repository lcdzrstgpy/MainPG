import { useState } from "react";

import {
  COMBO_PRESET_TEMPLATES,
  getActiveTemplateId,
  setActiveTemplateId,
} from "../presetTemplates";
import "../styles/comboKit.css";

type Props = { isActive?: boolean };

export function ComboKitPromptPresetPage({ isActive = true }: Props) {
  const [activeTemplateId, setActiveTemplate] = useState(getActiveTemplateId);

  const selectTemplate = (templateId: string) => {
    setActiveTemplateId(templateId);
    setActiveTemplate(templateId);
  };

  return (
    <div className="combo-kit-page">
      <header className="combo-kit-header">
        <div>
          <h1>提示词模板预设</h1>
          <p>选择默认图片提示词模板；新建套装及未自定义的套装会自动使用当前预设。</p>
        </div>
      </header>

      <div className="combo-preset-tabs" role="tablist" aria-label="组合套装提示词模板">
        {COMBO_PRESET_TEMPLATES.map((template) => (
          <button
            key={template.id}
            type="button"
            role="tab"
            aria-selected={activeTemplateId === template.id}
            className={`combo-preset-tab${activeTemplateId === template.id ? " is-active" : ""}`}
            onClick={() => selectTemplate(template.id)}
            disabled={!isActive}
          >
            {template.name}
          </button>
        ))}
      </div>

      {COMBO_PRESET_TEMPLATES.filter((template) => template.id === activeTemplateId).map((template) => (
        <section className="combo-preset-card is-active" key={template.id}>
          <div className="combo-preset-card-head">
            <h2>{template.name}</h2>
            <span className="combo-preset-badge">当前使用</span>
          </div>
          <p className="combo-preset-subtitle">{template.subtitle}</p>
          <h3>基础提示词</h3>
          <p className="combo-preset-prompt">{template.base_prompt_a}</p>
          <h3>轮播图 A</h3>
          <p className="combo-preset-prompt">{template.role_directions.carousel_2}</p>
          <h3>轮播图 B</h3>
          <p className="combo-preset-prompt">{template.role_directions.carousel_3}</p>
          <h3>白底图</h3>
          <p className="combo-preset-prompt">{template.role_directions.white_bg}</p>
          <p className="combo-preset-tip">已在当前浏览器保存。套装内手动修改并保存的提示词不会被此处预设覆盖。</p>
        </section>
      ))}
    </div>
  );
}

export default ComboKitPromptPresetPage;
