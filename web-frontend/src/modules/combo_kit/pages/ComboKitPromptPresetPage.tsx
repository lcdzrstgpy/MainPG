import { useEffect, useState } from 'react';
import {
  COMBO_PRESET_TEMPLATES,
  getActiveTemplateId,
  loadCustomTemplate,
  saveCustomTemplate,
  setActiveTemplateId,
  type ComboPresetTemplate,
} from '../presetTemplates';
import '../styles/comboKit.css';

type Props = { isActive?: boolean };

const EMPTY_CUSTOM: ComboPresetTemplate = {
  id: 'custom',
  name: '自定义模板',
  description: '当前账号在本机保存的组合生图模板。',
  base_prompt_a: '',
  role_directions: { carousel_2: '', carousel_3: '', white_bg: '' },
};

export function ComboKitPromptPresetPage({ isActive = true }: Props) {
  const [activeId, setActiveId] = useState(getActiveTemplateId());
  const [selectedId, setSelectedId] = useState(getActiveTemplateId());
  const [custom, setCustom] = useState<ComboPresetTemplate>(() => loadCustomTemplate() || EMPTY_CUSTOM);
  const [message, setMessage] = useState('');

  useEffect(() => {
    if (!isActive) return;
    const nextActive = getActiveTemplateId();
    setActiveId(nextActive);
    setSelectedId(nextActive);
    setCustom(loadCustomTemplate() || EMPTY_CUSTOM);
  }, [isActive]);

  const activate = (templateId: string) => {
    setActiveTemplateId(templateId);
    setActiveId(templateId);
    setSelectedId(templateId);
    setMessage('已设为当前使用模板，新建或未自定义的组合套装会自动采用该模板。');
  };

  const saveCustom = () => {
    if (!custom.base_prompt_a.trim()) {
      setMessage('请先填写基础提示词。');
      return;
    }
    const saved = saveCustomTemplate(custom);
    setCustom(saved);
    activate(saved.id);
  };

  const selected = selectedId === 'custom'
    ? custom
    : COMBO_PRESET_TEMPLATES.find((template) => template.id === selectedId) || COMBO_PRESET_TEMPLATES[0];

  return (
    <div className="combo-kit-page">
      <header className="combo-kit-header">
        <div>
          <h1>提示词模板预设</h1>
          <p>选择组合生图默认模板；已手动修改过提示词的套装不会被覆盖。</p>
        </div>
      </header>

      {message && <div className="combo-kit-message">{message}</div>}
      <div className="combo-preset-tabs">
        {COMBO_PRESET_TEMPLATES.map((template) => (
          <button key={template.id} type="button" className={`combo-preset-tab ${selectedId === template.id ? 'is-active' : ''}`} onClick={() => setSelectedId(template.id)}>
            {template.name}
          </button>
        ))}
        <button type="button" className={`combo-preset-tab ${selectedId === 'custom' ? 'is-active' : ''}`} onClick={() => setSelectedId('custom')}>
          自定义模板
        </button>
      </div>

      <div className="combo-preset-body">
        <section className={`combo-preset-card ${activeId === selected.id ? 'is-active' : ''}`}>
          <div className="combo-preset-card-head">
            <h2>{selected.name}</h2>
            {activeId === selected.id && <span className="combo-preset-badge">当前使用</span>}
          </div>
          <p className="combo-preset-subtitle">{selected.description}</p>

          {selectedId === 'custom' ? (
            <div className="combo-preset-custom">
              <label>模板名称<input value={custom.name} onChange={(event) => setCustom((value) => ({ ...value, name: event.target.value }))} maxLength={50} /></label>
              <label>基础提示词<textarea value={custom.base_prompt_a} onChange={(event) => setCustom((value) => ({ ...value, base_prompt_a: event.target.value }))} rows={4} /></label>
              <label>轮播图 2<textarea value={custom.role_directions.carousel_2} onChange={(event) => setCustom((value) => ({ ...value, role_directions: { ...value.role_directions, carousel_2: event.target.value } }))} rows={3} /></label>
              <label>轮播图 3<textarea value={custom.role_directions.carousel_3} onChange={(event) => setCustom((value) => ({ ...value, role_directions: { ...value.role_directions, carousel_3: event.target.value } }))} rows={3} /></label>
              <label>白底图<textarea value={custom.role_directions.white_bg} onChange={(event) => setCustom((value) => ({ ...value, role_directions: { ...value.role_directions, white_bg: event.target.value } }))} rows={3} /></label>
              <div className="combo-actions"><button type="button" className="primary" onClick={saveCustom}>保存并设为当前使用</button></div>
            </div>
          ) : (
            <>
              <h3>基础提示词</h3><pre className="combo-preset-prompt">{selected.base_prompt_a}</pre>
              <h3>轮播图 2</h3><pre className="combo-preset-prompt">{selected.role_directions.carousel_2}</pre>
              <h3>轮播图 3</h3><pre className="combo-preset-prompt">{selected.role_directions.carousel_3}</pre>
              <h3>白底图</h3><pre className="combo-preset-prompt">{selected.role_directions.white_bg}</pre>
              <div className="combo-actions"><button type="button" className="primary" onClick={() => activate(selected.id)}>设为当前使用</button></div>
            </>
          )}
          <p className="combo-preset-tip">模板保存在当前电脑的浏览器数据中；切换模板不会改写已手动自定义的历史套装。</p>
        </section>
      </div>
    </div>
  );
}

export default ComboKitPromptPresetPage;
