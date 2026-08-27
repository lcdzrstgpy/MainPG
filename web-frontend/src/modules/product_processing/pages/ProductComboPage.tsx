import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { ppRequest, type ApiContext } from '../api/client';
import { productProcessingApiContext } from '../api/context';
import {
  addDraftComboSource,
  comboSourceImageUrl,
  createComboDraft,
  draftImageUrl,
  generateComboMain,
  listComboSources,
  processCombo,
  removeComboSource,
  updateComboDraft,
  uploadComboSource,
  type ComboDraftInput,
  type ComboSource,
} from '../api/comboApi';
import '../styles/ProductProcessingVerifyPage.css';

const API_BASE = '/api/product-processing';

const ROLE_LABELS: Record<string, { label: string; hint: string; defaultPrompt: string }> = {
  detail: { label: '编辑/详情图', hint: '对应四宫格 Panel 2：展示完整体与一处真实细节', defaultPrompt: '' },
  lifestyle: { label: '生活方式图', hint: '对应四宫格 Panel 3：真实居家使用场景', defaultPrompt: '' },
  dimension: { label: '尺寸标注背景图', hint: '对应四宫格 Panel 4：干净正/侧/顶视图，留出尺寸标注安全区', defaultPrompt: '' },
};

const MAIN_DEFAULT_PROMPT =
  'premium e-commerce hero composition, complete product visible, grounded contact shadow, category-matched premium background, no added text';

type Props = {
  onOpenPrecheck?: (taskId: number) => void;
  isActive?: boolean;
};

function api(): ApiContext {
  return productProcessingApiContext();
}

function isExternal(value: string) {
  return /^https?:\/\//i.test(value);
}

export function ProductComboPage({ onOpenPrecheck, isActive = true }: Props) {
  const ctx = useMemo(() => api(), []);
  const [sources, setSources] = useState<ComboSource[]>([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [draftId, setDraftId] = useState<number | null>(null);
  const [mainImageUrl, setMainImageUrl] = useState('');
  const [generatingMain, setGeneratingMain] = useState(false);
  const [processing, setProcessing] = useState(false);

  // 组合基本信息（与预检导出字段保持一致）
  const [form, setForm] = useState({
    title: '',
    product_name: '',
    description: '',
    skc: '',
    sku: '',
    cost: '',
    declared_price: '',
    suggested_price: '',
    stock: '',
    category_path: '',
    category_id: '',
    length_cm: '',
    width_cm: '',
    height_cm: '',
    weight_g: '',
  });

  const [mainPrompt, setMainPrompt] = useState(MAIN_DEFAULT_PROMPT);
  const [rolePrompts, setRolePrompts] = useState<Record<string, string>>({});

  const notify = useCallback((ok: string) => { setMessage(ok); setError(''); }, []);
  const fail = useCallback((err: unknown) => { setError(err instanceof Error ? err.message : String(err)); setMessage(''); }, []);

  const loadSources = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listComboSources(ctx);
      setSources(data.sources || []);
    } catch (err) {
      fail(err);
    } finally {
      setLoading(false);
    }
  }, [ctx, fail]);

  useEffect(() => {
    if (!isActive) return;
    void loadSources();
  }, [isActive, loadSources]);

  const setField = (key: keyof typeof form, value: string) => setForm((prev) => ({ ...prev, [key]: value }));

  const addFromDraftPool = async (draftId: number, title: string) => {
    try {
      await addDraftComboSource(ctx, draftId, title);
      notify('已加入组合定制来源图（可去「商品自定义组合」页管理）');
      await loadSources();
    } catch (err) {
      fail(err);
    }
  };

  const uploadSource = async (files: FileList | null) => {
    if (!files || !files.length) return;
    try {
      for (const file of Array.from(files)) {
        await uploadComboSource(ctx, file, file.name);
      }
      notify(`已上传 ${files.length} 张来源图`);
      await loadSources();
    } catch (err) {
      fail(err);
    }
  };

  const removeSource = async (sourceId: number) => {
    try {
      await removeComboSource(ctx, sourceId);
      notify('已移除来源图');
      await loadSources();
    } catch (err) {
      fail(err);
    }
  };

  const numeric = (value: string): number | undefined => {
    const parsed = Number(value);
    return value.trim() !== '' && Number.isFinite(parsed) ? parsed : undefined;
  };

  const saveDraft = async () => {
    if (sources.length < 2) {
      fail('组合至少需要 2 张来源图，请先从草稿池加入或手动上传');
      return;
    }
    setLoading(true);
    setMessage('');
    setError('');
    try {
      const input: ComboDraftInput = {
        title: form.title,
        product_name: form.product_name || form.title,
        description: form.description,
        skc: form.skc,
        sku: form.sku,
        cost: numeric(form.cost),
        declared_price: numeric(form.declared_price),
        core_fields: {
          sku: form.sku,
          declared_price: numeric(form.declared_price),
          suggested_price: numeric(form.suggested_price),
          stock: numeric(form.stock),
          category_path: form.category_path,
          category_id: form.category_id,
          length_cm: numeric(form.length_cm),
          width_cm: numeric(form.width_cm),
          height_cm: numeric(form.height_cm),
          weight_g: numeric(form.weight_g),
        },
        combo_sources: sources.map((s) => ({
          source_type: s.source_type,
          draft_id: s.draft_id,
          title: s.title,
          url: s.url,
          local_path: s.local_path,
        })),
        main_prompt: mainPrompt,
        role_prompts: rolePrompts,
      };
      if (draftId != null) {
        await updateComboDraft(ctx, draftId, input);
        notify(`组合草稿 #${draftId} 已更新，可继续生成主图`);
      } else {
        const data = await createComboDraft(ctx, input);
        setDraftId(data.draft.id);
        notify('组合草稿已保存，可继续生成主图');
      }
    } catch (err) {
      fail(err);
    } finally {
      setLoading(false);
    }
  };

  const generateMain = async () => {
    const targetDraftId = draftId;
    if (!targetDraftId) {
      fail('请先保存组合草稿再生成主图');
      return;
    }
    setGeneratingMain(true);
    setError('');
    try {
      const data = await generateComboMain(ctx, targetDraftId, mainPrompt);
      setMainImageUrl(data.main_image_path || '');
      notify(data.message || '主图已生成');
    } catch (err) {
      fail(err);
    } finally {
      setGeneratingMain(false);
    }
  };

  const startProcessing = async () => {
    if (!draftId) {
      fail('请先保存组合草稿并生成主图');
      return;
    }
    setProcessing(true);
    setError('');
    try {
      const data = await processCombo(ctx, draftId);
      notify(data.message || '组合处理已提交');
      if (data.task_id) onOpenPrecheck?.(data.task_id);
    } catch (err) {
      fail(err);
    } finally {
      setProcessing(false);
    }
  };

  const sourceUrl = (source: ComboSource) =>
    source.url || (source.source_type === 'upload' ? comboSourceImageUrl(source.id) : '');

  const renderField = (key: keyof typeof form, label: string, placeholder = '') => (
    <label>
      {label}
      <input
        value={form[key]}
        onChange={(e) => setField(key, e.target.value)}
        placeholder={placeholder}
      />
    </label>
  );

  return (
    <div className="verify-page">
      <header className="verify-commandbar">
        <div className="verify-command-title">
          <h1>商品自定义组合</h1>
          <p>从草稿池「加入到组合定制」或本地上传组合来源图，录入组合信息、生成主图并处理 3 张轮播图，最后过预检导出。</p>
        </div>
      </header>

      {(message || error) && (
        <div className={`verify-message ${error ? 'error' : ''}`}>{error || message}</div>
      )}

      {/* Step 1 组合基本信息 + 来源图 */}
      <section className="verify-section">
        <div className="verify-section-head">
          <h2>① 组合来源图与基本信息</h2>
          <span className="verify-sub">来源图可来自草稿池「加入到组合定制」暂存区，也可本地上传；需 ≥ 2 张。已保存后调整来源图须再次保存，草稿只记录保存时的来源图快照。</span>
        </div>

        <div className="verify-actions">
          <label className="verify-upload-btn">
            <input
              type="file"
              accept="image/*"
              multiple
              hidden
              onChange={(e) => { void uploadSource(e.target.files); e.target.value = ''; }}
            />
            上传来源图
          </label>
          <button onClick={() => void saveDraft()} disabled={loading}>保存组合草稿</button>
          <button onClick={() => void loadSources()} disabled={loading}>刷新来源图</button>
          {draftId != null && <span className="verify-premium-hint">草稿 #{draftId}</span>}
        </div>

        {loading && <p className="verify-empty">加载来源图…</p>}
        {!sources.length && <p className="verify-empty">暂无可用的组合来源图。</p>}
        <div className="verify-draft-list">
          {sources.map((source) => {
            const url = sourceUrl(source);
            return (
              <article key={source.id} className="pool-card">
                <div className="pool-card-body">
                  <div className="pool-thumb">
                    {url ? <img src={url} alt={source.title} referrerPolicy="no-referrer" /> : <span>暂无图</span>}
                  </div>
                  <div className="pool-info">
                    <div className="pool-title-row">
                      <strong title={source.title}>{source.title || '来源图'}</strong>
                      <span className="tag">{source.source_type === 'upload' ? '本地上传' : '草稿池'}</span>
                    </div>
                    <div className="pool-extra">
                      {source.draft_id != null && <span>草稿 #{source.draft_id}</span>}
                      {isExternal(source.url) && <span>{source.url}</span>}
                    </div>
                  </div>
                  <div className="pool-inline-acts">
                    <button type="button" className="btn-mini danger" onClick={() => void removeSource(source.id)}>移除</button>
                  </div>
                </div>
              </article>
            );
          })}
        </div>

        <div className="precheck-core-grid">
          {renderField('title', '组合标题（留空则 AI 生成）', '例如：3 件套厨房收纳组合…')}
          {renderField('skc', 'SKC货号')}
          {renderField('sku', 'SKU货号')}
          {renderField('declared_price', '申报价格')}
          {renderField('suggested_price', '建议售价')}
          {renderField('stock', '库存')}
          {renderField('category_path', '类目路径')}
          {renderField('category_id', '类目ID')}
          {renderField('length_cm', '物流包裹长(cm)')}
          {renderField('width_cm', '物流包裹宽(cm)')}
          {renderField('height_cm', '物流包裹高(cm)')}
          {renderField('weight_g', '重量(g)')}
          {renderField('cost', '成本 ¥')}
        </div>
        <label className="precheck-label">
          <span>产品描述（未填时 AI 生成五点描述）</span>
          <textarea rows={4} value={form.description} onChange={(e) => setField('description', e.target.value)} placeholder="AI 生成五点描述" />
        </label>
      </section>

      {/* Step 2 主图生成 */}
      <section className="verify-section">
        <div className="verify-section-head">
          <h2>② 生成组合主图（固定 1 张）</h2>
          <span className="verify-sub">先选好组合主体图；单个主图生成，当前步骤预扣 40 积分</span>
        </div>
        <div className="verify-form-row">
          <label className="precheck-label">
            <span>主图提示词</span>
            <textarea rows={3} value={mainPrompt} onChange={(e) => setMainPrompt(e.target.value)} />
          </label>
        </div>
        <div className="verify-actions">
          <button className="primary" onClick={() => void generateMain()} disabled={generatingMain || !draftId || !!mainImageUrl}>
            {generatingMain ? '生成中…' : mainImageUrl ? '主图已生成' : '生成主图'}
          </button>
          {mainImageUrl && (
            <div className="pool-thumb">
              <img src={mainImageUrl} alt="组合主图" referrerPolicy="no-referrer" />
              <span className="pool-thumb-overlay">已生成主图</span>
            </div>
          )}
        </div>
        {mainImageUrl && <p className="verify-empty">主图已就绪，可进入第 ③ 步开始处理生成 3 张轮播图。</p>}
        {draftId == null && <p className="verify-empty">先保存组合草稿后才可生成主图。</p>}
      </section>

      {/* Step 3 并行生成 3 张轮播图 */}
      <section className="verify-section">
        <div className="verify-section-head">
          <h2>③ 开始处理：并行生成 3 张轮播图</h2>
          <span className="verify-sub">主图已就绪；并行调 3 次接口生成详情/生活方式/尺寸背景图，此步预扣 60 积分</span>
        </div>
        <div className="verify-form-row">
          {Object.entries(ROLE_LABELS).map(([key, meta]) => (
            <label key={key} className="precheck-label" title={meta.hint}>
              <span>{meta.label}（{meta.hint}）</span>
              <textarea
                rows={2}
                value={rolePrompts[key] ?? meta.defaultPrompt}
                onChange={(e) => setRolePrompts((prev) => ({ ...prev, [key]: e.target.value }))}
              />
            </label>
          ))}
        </div>
        <div className="verify-actions">
          <button className="primary" onClick={() => void startProcessing()} disabled={processing || !draftId}>
            {processing ? '处理中…' : '开始处理并生成 3 张图'}
          </button>
          <span className="verify-premium-hint">整条组合流程共 100 积分（分两步预扣）</span>
        </div>
      </section>
    </div>
  );
}

export default ProductComboPage;
