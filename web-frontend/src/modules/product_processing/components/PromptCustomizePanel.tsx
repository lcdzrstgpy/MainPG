import { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { ppRequest } from '../api/client';
import { productProcessingApiContext } from '../api/context';
import '../styles/PromptCustomizePanel.css';

const API_BASE = '/api/product-processing';

/**
 * 提示词预设模板面板：按账号（本机全局）保存多个命名模板，可启用一个用于处理任务。
 *
 * 追加指令模式：用户在各板块填写的提示词附加在系统默认提示词之上，不覆盖默认。
 * 生图板块（grid_image/grid_image_b/grid_image_c/grid_image_d/premium_image）只允许附加「宫内规划」
 * （构图、场景、道具、光影、风格），图片结构、分界线、拆分逻辑由系统固定不可改变。
 */
const PROMPT_SECTIONS: { key: string; label: string; hint: string; generator?: boolean }[] = [
  { key: 'title', label: '标题优化', hint: '英文商品标题的附加要求（约 180 字母，贴合买家搜索习惯）' },
  { key: 'desc', label: '描述 / 卖点', hint: '五点式描述文案的附加要求（Amazon 风格卖点）' },
  { key: 'grid_image', label: '智能生图 · 影棚无缝主图', hint: 'A 模板附加：仅可写宫内规划（构图/场景/光影/风格），图片结构固定' },
  { key: 'grid_image_b', label: '智能生图 · 人景叙事', hint: 'B 模板附加：仅可写宫内规划，图片结构固定' },
  { key: 'grid_image_c', label: '智能生图 · 静物平铺俯拍', hint: 'C 模板附加：仅可写宫内规划，图片结构固定' },
  { key: 'grid_image_d', label: '智能生图 · 环境远景', hint: 'D 模板附加：仅可写宫内规划，图片结构固定' },
  { key: 'premium_image', label: '精品生图', hint: '4K 精品生图附加：仅可写宫内规划，结构固定' },
  { key: 'detail_image', label: '详情图', hint: '详情页海报合成的附加要求（主图 + 细节放大 + 说明标签）' },
  {
    key: 'batch_image_prompt',
    label: '本批次辅助提词（区域辅助出图）',
    hint: '按本批次商品标题生成，作为附加要求注入本批轮播/精品/详情生图，用于定点爆破、规避异形/搞笑画面；可编辑可清空',
    generator: true,
  },
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

export function PromptCustomizePanel({ draftIds }: { draftIds?: number[] } = {}) {
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
  const [batchPromptBusy, setBatchPromptBusy] = useState(false);
  const [batchPromptNotice, setBatchPromptNotice] = useState('');

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

  // 「根据本批次标题生成」：取当前所选/本批次商品标题，调后端申请批次级短期 Ark 密钥后
  // 调用 AI 生成辅助提词并回填文本框（会调用 AI；申请密钥时的积分冻结会自动全额退回，
  // 不扣积分）。生成结果可继续手动编辑/清空。
  const generateBatchPrompt = async () => {
    if (!draftIds?.length) {
      setBatchPromptNotice('当前没有可用的本批次商品标题，请先在处理设置页选择商品');
      return;
    }
    setBatchPromptBusy(true);
    setBatchPromptNotice('');
    try {
      const data = await ppRequest<{ batch_image_prompt?: string; message?: string }>(
        ctx,
        `${API_BASE}/engine/batch-image-prompt`,
        { body: { draft_ids: draftIds } }
      );
      if (data.batch_image_prompt) {
        setValue('batch_image_prompt', data.batch_image_prompt);
        setBatchPromptNotice(data.message || '已生成，可继续手动编辑后再保存模板');
      } else {
        setBatchPromptNotice(data.message || 'AI 未返回有效提词，请稍后重试');
      }
    } catch (err) {
      setBatchPromptNotice(err instanceof Error ? err.message : '生成失败，请稍后重试');
    } finally {
      setBatchPromptBusy(false);
    }
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

  // 一键填入：点选模板即载入编辑区，并立即设为当前使用（对后续所有新任务生效）。
  const applyAndActivate = async (template: PromptTemplate) => {
    applyTemplate(template);
    if (template.is_active) return;
    setSaving(true);
    setError('');
    setMessage('');
    try {
      const data = await ppRequest<TemplatesResponse>(ctx, `${API_BASE}/engine/prompt-templates/${template.id}/activate`, { body: {} });
      refreshList(data);
      setMessage(data.message || `已启用「${template.name}」`);
    } catch (err) {
      setError(err instanceof Error ? err.message : '启用模板失败');
    } finally {
      setSaving(false);
    }
  };

  // 停用当前模板：回到只用系统默认提示词，模板本身保留在列表里，随时可再启用。
  const deactivate = async () => {
    setSaving(true);
    setError('');
    setMessage('');
    try {
      const data = await ppRequest<TemplatesResponse>(ctx, `${API_BASE}/engine/prompt-templates/deactivate`, { body: {} });
      setTemplates(data.templates || []);
      setMessage(data.message || '已停用模板，后续任务使用系统默认提示词');
    } catch (err) {
      setError(err instanceof Error ? err.message : '停用模板失败');
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
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  return (
    <>
      <section className={`prompt-panel ${open ? 'is-open' : ''}`}>
        <button type="button" className="prompt-panel-toggle" onClick={toggleOpen} aria-expanded={open}>
          <span className="prompt-panel-toggle-title">
            提示词预设模板
            <small>
              高级 · 按账号保存多个命名模板{activeTemplate ? `，当前使用「${activeTemplate.name}」` : '，当前使用系统默认提示词'}
            </small>
          </span>
          <span className="prompt-panel-toggle-caret">{open ? '关闭 ✕' : '配置 ▸'}</span>
        </button>
      </section>
      {/* portal 到 body：workspace-tab-panel 的 fill-mode 入场动画创建层叠上下文，
          会把 fixed 抽屉的 z-index(400) 锁在面板内、被 sticky 顶栏(z:18)盖住头部 */}
      {open && createPortal(
        <div className="prompt-drawer-layer">
          <button type="button" className="prompt-drawer-backdrop" onClick={() => setOpen(false)} aria-label="关闭提示词预设模板" />
          <aside className="prompt-drawer" role="dialog" aria-modal="true" aria-label="提示词预设模板">
            <header className="prompt-drawer-header">
              <div>
                <span>PROMPT PRESETS</span>
                <h2>提示词预设模板</h2>
                <p>按账号保存多个命名模板，启用后对所有新任务生效</p>
              </div>
              <button type="button" onClick={() => setOpen(false)} aria-label="关闭">×</button>
            </header>
            <div className="prompt-drawer-body">
          <p className="prompt-panel-note">
            填写的内容是<strong>附加要求</strong>，会拼在系统默认提示词之后，不会覆盖默认逻辑。
            生图板块只允许写<strong>宫内规划</strong>（构图、场景、道具、光影、风格）；图片结构、
            拆分逻辑与产品保真规则由系统固定，不可被提示词改变。模板保存在当前账号本机，
            不随工作区变化，启用后对所有新任务生效，下次打开自动带出。
          </p>
          <div className="prompt-template-picker">
            <p className="prompt-template-picker-hint">点选即一键填入并启用，对后续所有新任务生效。</p>
            <div className="prompt-template-cards">
              <button
                type="button"
                className={`prompt-template-card ${!activeTemplate ? 'is-active' : ''}`}
                onClick={() => void deactivate()}
                disabled={saving || loading || !activeTemplate}
              >
                <strong>系统默认提示词</strong>
                <small>{activeTemplate ? '不使用任何模板' : '当前使用'}</small>
              </button>
              {templates.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  className={`prompt-template-card ${t.is_active ? 'is-active' : ''} ${selectedId === t.id ? 'is-selected' : ''}`}
                  onClick={() => void applyAndActivate(t)}
                  disabled={saving || loading}
                >
                  <strong>{t.name}</strong>
                  <small>{t.is_active ? '当前使用' : '一键启用'}</small>
                </button>
              ))}
            </div>
            <div className="prompt-template-actions">
              <button type="button" className="prompt-btn" onClick={resetValues} disabled={loading}>新建空白</button>
              <button type="button" className="prompt-btn" onClick={() => save(true)} disabled={saving || loading}>另存为新模板</button>
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
                  {section.generator && (
                    <div className="prompt-block-generate">
                      <button
                        type="button"
                        className="prompt-btn"
                        onClick={() => void generateBatchPrompt()}
                        disabled={batchPromptBusy || loading}
                      >
                        {batchPromptBusy ? '生成中…' : '根据本批次标题生成'}
                      </button>
                      <small className="prompt-block-generate-hint">
                        点击会调用 AI 生成（申请密钥时会短暂冻结一笔积分用于领取短期密钥，随后自动全额退回，不扣积分）；结果自动填入上方文本框，可手动编辑，保存模板后随模板持久化
                      </small>
                      {batchPromptNotice && (
                        <small className="prompt-block-generate-notice">{batchPromptNotice}</small>
                      )}
                    </div>
                  )}
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
          </aside>
        </div>, document.body)}
    </>
  );
}

export default PromptCustomizePanel;
