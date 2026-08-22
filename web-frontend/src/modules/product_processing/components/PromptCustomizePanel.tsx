import { useEffect, useMemo, useState } from 'react';
import { ppRequest } from '../api/client';
import { productProcessingApiContext } from '../api/context';
import '../styles/PromptCustomizePanel.css';

const API_BASE = '/api/product-processing';

/**
 * 提示词预设模板面板：按账号（本机全局）保存多个命名模板，可启用一个用于处理任务。
 *
 * 追加指令模式：用户在各板块填写的提示词附加在系统默认提示词之上，不覆盖默认。
 * 生图板块（grid_image/grid_image_b/premium_image）只允许附加「宫内规划」
 * （构图、场景、道具、光影、风格），图片结构、分界线、拆分逻辑由系统固定不可改变。
 */
const PROMPT_SECTIONS: { key: string; label: string; hint: string }[] = [
  { key: 'title', label: '标题优化', hint: '英文商品标题的附加要求（约 180 字母，贴合买家搜索习惯）' },
  { key: 'desc', label: '描述 / 卖点', hint: '五点式描述文案的附加要求（Amazon 风格卖点）' },
  { key: 'grid_image', label: '智能生图 · 标准海报', hint: 'A 模板附加：仅可写宫内规划（构图/场景/光影/风格），图片结构固定' },
  { key: 'grid_image_b', label: '智能生图 · 高端模特', hint: 'B 模板附加：仅可写宫内规划，图片结构固定' },
  { key: 'premium_image', label: '精品生图', hint: '4K 精品生图附加：仅可写宫内规划，结构固定' },
  { key: 'detail_image', label: '详情图', hint: '详情页海报合成的附加要求（主图 + 细节放大 + 说明标签）' },
  { key: 'variant_values', label: '变体翻译', hint: 'SKU 选项翻译的附加要求（颜色 / 规格 / 尺寸等）' },
];

type PromptTemplate = {
  id: number;
  name: string;
  prompts: Record<string, string>;
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

type TemplatesResponse = { templates: PromptTemplate[]; template?: PromptTemplate; message?: string };

export function PromptCustomizePanel() {
  const ctx = productProcessingApiContext();
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [templates, setTemplates] = useState<PromptTemplate[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [name, setName] = useState('');
  const [values, setValues] = useState<Record<string, string>>({});
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  const activeTemplate = useMemo(
    () => templates.find((t) => t.is_active) ?? null,
    [templates]
  );
  const selectedTemplate = useMemo(
    () => templates.find((t) => t.id === selectedId) ?? null,
    [templates, selectedId]
  );

  const applyTemplate = (template: PromptTemplate | null) => {
    setSelectedId(template?.id ?? null);
    setName(template?.name ?? '');
    const next: Record<string, string> = {};
    for (const section of PROMPT_SECTIONS) {
      next[section.key] = template?.prompts?.[section.key] ?? '';
    }
    setValues(next);
  };

  const load = async () => {
    setLoading(true);
    setError('');
    setMessage('');
    try {
      const data = await ppRequest<TemplatesResponse>(ctx, `${API_BASE}/engine/prompt-templates`);
      const list = data.templates || [];
      setTemplates(list);
      const active = list.find((t) => t.is_active) ?? list[0] ?? null;
      applyTemplate(active);
      setLoaded(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : '模板加载失败');
    } finally {
      setLoading(false);
    }
  };

  const toggleOpen = () => {
    const next = !open;
    setOpen(next);
    if (next && !loaded) void load();
  };

  const setValue = (key: string, value: string) => {
    setValues((prev) => ({ ...prev, [key]: value }));
  };

  const refreshList = (data: TemplatesResponse) => {
    const list = data.templates || [];
    setTemplates(list);
    if (data.template) {
      applyTemplate(data.template);
    }
  };

  const save = async (asNew: boolean) => {
    const cleanName = name.trim();
    if (!cleanName) {
      setError('请先填写模板名称');
      return;
    }
    setSaving(true);
    setError('');
    setMessage('');
    try {
      const data = await ppRequest<TemplatesResponse>(ctx, `${API_BASE}/engine/prompt-templates`, {
        body: {
          template_id: asNew ? null : selectedId,
          name: cleanName,
          prompts: { ...values },
          activate: true,
        },
      });
      refreshList(data);
      setMessage(data.message || '预设模板已保存');
    } catch (err) {
      setError(err instanceof Error ? err.message : '模板保存失败');
    } finally {
      setSaving(false);
    }
  };

  const activate = async () => {
    if (selectedId == null) return;
    setSaving(true);
    setError('');
    setMessage('');
    try {
      const data = await ppRequest<TemplatesResponse>(ctx, `${API_BASE}/engine/prompt-templates/${selectedId}/activate`, { body: {} });
      refreshList(data);
      setMessage(data.message || '已设为当前使用模板');
    } catch (err) {
      setError(err instanceof Error ? err.message : '启用模板失败');
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (selectedId == null) return;
    if (!window.confirm(`确定删除模板「${selectedTemplate?.name ?? ''}」吗？`)) return;
    setSaving(true);
    setError('');
    setMessage('');
    try {
      const data = await ppRequest<TemplatesResponse>(ctx, `${API_BASE}/engine/prompt-templates/${selectedId}/delete`, { body: {} });
      const list = data.templates || [];
      setTemplates(list);
      applyTemplate(list.find((t) => t.is_active) ?? list[0] ?? null);
      setMessage(data.message || '模板已删除');
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除模板失败');
    } finally {
      setSaving(false);
    }
  };

  const resetValues = () => {
    setSelectedId(null);
    setName('');
    const empty: Record<string, string> = {};
    for (const section of PROMPT_SECTIONS) empty[section.key] = '';
    setValues(empty);
  };

  useEffect(() => {
    if (!open) return;
    setError('');
  }, [open]);

  return (
    <section className={`prompt-panel ${open ? 'is-open' : ''}`}>
      <button type="button" className="prompt-panel-toggle" onClick={toggleOpen} aria-expanded={open}>
        <span className="prompt-panel-toggle-title">
          提示词预设模板
          <small>
            高级 · 按账号保存多个命名模板{activeTemplate ? `，当前使用「${activeTemplate.name}」` : '，暂未启用'}
          </small>
        </span>
        <span className="prompt-panel-toggle-caret">{open ? '收起 ▾' : '展开 ▸'}</span>
      </button>
      {open && (
        <div className="prompt-panel-body">
          <p className="prompt-panel-note">
            填写的内容是<strong>附加要求</strong>，会拼在系统默认提示词之后，不会覆盖默认逻辑。
            生图板块只允许写<strong>宫内规划</strong>（构图、场景、道具、光影、风格）；图片结构、
            拆分逻辑与产品保真规则由系统固定，不可被提示词改变。模板保存在当前账号本机，
            不随工作区变化，启用后对所有新任务生效，下次打开自动带出。
          </p>
          <div className="prompt-template-bar">
            <label className="prompt-template-select-label">
              编辑模板
              <select
                value={selectedId ?? ''}
                onChange={(e) => {
                  const id = e.target.value ? Number(e.target.value) : null;
                  applyTemplate(templates.find((t) => t.id === id) ?? null);
                }}
                disabled={loading}
              >
                {templates.length === 0 && <option value="">（暂无模板，可新建）</option>}
                {templates.map((t) => (
                  <option key={t.id} value={t.id}>{t.name}{t.is_active ? '（当前使用）' : ''}</option>
                ))}
              </select>
            </label>
            <div className="prompt-template-actions">
              <button type="button" className="prompt-btn" onClick={resetValues} disabled={loading}>新建</button>
              <button type="button" className="prompt-btn" onClick={() => save(true)} disabled={saving || loading}>另存为新模板</button>
              <button type="button" className="prompt-btn" onClick={activate} disabled={saving || loading || selectedId == null}>设为当前使用</button>
              <button type="button" className="prompt-btn danger" onClick={remove} disabled={saving || loading || selectedId == null}>删除</button>
            </div>
          </div>
          <div className="prompt-template-name-row">
            <label className="prompt-template-name-label">
              模板名称
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="例如：夏季清爽风 / 高端质感款"
                maxLength={50}
              />
            </label>
            {selectedId != null && (
              <span className="prompt-template-save-mode">修改「{selectedTemplate?.name ?? ''}」后保存</span>
            )}
          </div>
          {loading && <p className="prompt-panel-message">模板加载中…</p>}
          {!loading && (
            <div className="prompt-blocks">
              {PROMPT_SECTIONS.map((section) => (
                <div className="prompt-block" key={section.key}>
                  <div className="prompt-block-head">
                    <span className="prompt-block-label">{section.label}</span>
                    <small className="prompt-block-hint">{section.hint}</small>
                    <button
                      type="button"
                      className="prompt-block-reset"
                      onClick={() => setValue(section.key, '')}
                      disabled={!values[section.key]}
                    >清空</button>
                  </div>
                  <textarea
                    className="prompt-block-input"
                    value={values[section.key] ?? ''}
                    onChange={(e) => setValue(section.key, e.target.value)}
                    placeholder="留空 = 不附加，使用系统默认；填入 = 附加为额外要求"
                    rows={3}
                    spellCheck={false}
                  />
                </div>
              ))}
            </div>
          )}
          <div className="prompt-panel-actions">
            <button type="button" className="prompt-save primary" onClick={() => save(selectedId == null)} disabled={saving || loading}>
              {saving ? '保存中…' : selectedId == null ? '保存为模板' : '保存模板'}
            </button>
          </div>
          {message && <p className="prompt-panel-message ok">{message}</p>}
          {error && <p className="prompt-panel-message error">{error}</p>}
        </div>
      )}
    </section>
  );
}

export default PromptCustomizePanel;
