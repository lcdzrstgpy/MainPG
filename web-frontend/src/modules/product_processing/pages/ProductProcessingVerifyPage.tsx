import { useEffect, useMemo, useState } from 'react';
import { ppDownload, ppRequest, type ApiContext } from '../api/client';
import type {
  DraftSummary,
  DraftVariant,
  ProductProcessingOptions,
  TaskHistoryItem,
  TaskOutputsResponse,
} from '../types';
import '../styles/ProductProcessingVerifyPage.css';

const API_BASE = '/api/product-processing';

const SITES = [
  { code: 'US', label: '美国站' },
  { code: 'CO', label: '哥伦比亚站' },
  { code: 'EC', label: '厄瓜多尔站' },
] as const;

const SCOPES: { key: string; label: string }[] = [
  { key: 'title', label: '标题' },
  { key: 'details', label: '详情' },
  { key: 'product_dimensions', label: '产品尺寸' },
  { key: 'four_grid', label: '四宫格' },
  { key: 'detail_images', label: '详情图' },
  { key: 'sku_images', label: 'SKU 图' },
  { key: 'qualification', label: '资质' },
];

const DEFAULT_OPTIONS: ProductProcessingOptions = {
  targetSite: 'US',
  targetLanguage: 'en',
  maxProducts: 0,
  processingScope: ['title', 'details', 'product_dimensions', 'four_grid', 'detail_images', 'qualification'],
  qualificationMode: 'standard',
  includeProductVideo: false,
  skipDuplicates: false,
  ipCheck: true,
};

const PAGE_SIZE = 10;

type AiConfigSummary = {
  provider: string;
  base_url: string;
  api_key_masked: string;
  api_key_configured: boolean;
  text_model: string;
  text_model_fallback_order: string[];
  image_model: string;
  reference_image_model: string;
  image_size: string;
  image_quality: string;
  enabled: boolean;
};

function api(): ApiContext {
  return { baseUrl: '', token: '', workspaceId: 'default' };
}

function draftDirty(draft: DraftSummary, edits: Record<number, DraftEdit>): boolean {
  const edit = edits[draft.id];
  if (!edit) return false;
  return (
    edit.title !== (draft.title || '') ||
    edit.imageUrl !== (draft.image_url || '') ||
    Object.keys(edit.skuEdits).length > 0 ||
    edit.skuDeletes.length > 0
  );
}

type DraftEdit = {
  title: string;
  imageUrl: string;
  skuEdits: Record<string, string>;
  skuDeletes: string[];
};

const FAILURE_CLASS_LABELS: Record<string, string> = {
  technical_retryable: '技术失败可重试',
  configuration_blocked: '配置阻断',
  identity_review_required: '身份待复核',
  logistics_review_required: '尺寸待复核',
};

export function ProductProcessingVerifyPage() {
  const ctx = api();
  const [options, setOptions] = useState<ProductProcessingOptions>(DEFAULT_OPTIONS);
  const [drafts, setDrafts] = useState<DraftSummary[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [edits, setEdits] = useState<Record<number, DraftEdit>>({});
  const [batch, setBatch] = useState<TaskOutputsResponse | null>(null);
  const [history, setHistory] = useState<TaskHistoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [aiConfig, setAiConfig] = useState<AiConfigSummary | null>(null);
  const [aiPing, setAiPing] = useState<{ ok: boolean; detail: string } | null>(null);
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState<'all' | 'draft' | 'processed' | 'attention_required'>('all');
  const [searchTerm, setSearchTerm] = useState('');

  const dirtyCount = useMemo(
    () => drafts.filter((d) => draftDirty(d, edits)).length,
    [drafts, edits]
  );
  const selectableDrafts = useMemo(
    () => drafts.filter((d) => d.status !== 'deleted'),
    [drafts]
  );
  const filteredDrafts = useMemo(() => {
    const keyword = searchTerm.trim().toLowerCase();
    return selectableDrafts.filter((d) => {
      if (statusFilter !== 'all' && d.status !== statusFilter) return false;
      if (!keyword) return true;
      const raw = d.raw_payload || {};
      return [d.title, d.product_name, d.skc, d.sku, d.source_ref, raw.source_title]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(keyword));
    });
  }, [selectableDrafts, statusFilter, searchTerm]);
  const totalDrafts = filteredDrafts.length;
  const totalPages = Math.max(1, Math.ceil(totalDrafts / PAGE_SIZE));
  const pageStart = (page - 1) * PAGE_SIZE;
  const pageDrafts = useMemo(
    () => filteredDrafts.slice(pageStart, pageStart + PAGE_SIZE),
    [filteredDrafts, pageStart]
  );
  const failureItems = useMemo(
    () =>
      batch?.items.filter(
        (item) => item.status === 'attention_required' || item.status === 'failed'
      ) || [],
    [batch]
  );

  const notify = (ok: string) => {
    setMessage(ok);
    setError('');
  };
  const fail = (err: unknown) => {
    setError(err instanceof Error ? err.message : String(err));
    setMessage('');
  };

  const refresh = async () => {
    const [draftData, historyData] = await Promise.all([
      ppRequest<{ drafts: DraftSummary[] }>(ctx, `${API_BASE}/drafts?view=summary&limit=500`),
      ppRequest<{ tasks: TaskHistoryItem[] }>(ctx, `${API_BASE}/tasks/history?limit=20`),
    ]);
    setDrafts(draftData.drafts || []);
    setHistory(historyData.tasks || []);
  };

  // 草稿池数据变化后，将当前页收敛到有效范围内。
  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);

  useEffect(() => {
    refresh().catch(fail);
    ppRequest<AiConfigSummary>(ctx, `${API_BASE}/ai-config`)
      .then(setAiConfig)
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!batch) return;
    const running = ['queued', 'running', 'paused'];
    if (!running.includes(batch.task.status)) return;
    const timer = setInterval(async () => {
      try {
        const data = await ppRequest<TaskOutputsResponse>(ctx, `${API_BASE}/tasks/${batch.task_id}/outputs`);
        setBatch(data);
        if (!running.includes(data.task.status)) {
          clearInterval(timer);
          refresh().catch(() => undefined);
        }
      } catch {
        clearInterval(timer);
      }
    }, 1000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batch?.task_id, batch?.task.status]);

  const toggleDraft = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else if (next.size < 100) {
        next.add(id);
      } else {
        setError('一次最多处理 100 个草稿');
      }
      return next;
    });
  };

  const selectAll = () => setSelectedIds(new Set(pageDrafts.slice(0, 100).map((d) => d.id)));
  const clearSelection = () => setSelectedIds(new Set());

  const beginEdit = (draft: DraftSummary) => {
    const raw = draft.raw_payload || {};
    setExpandedId((current) => (current === draft.id ? null : draft.id));
    setEdits((prev) => {
      if (prev[draft.id]) return prev;
      return {
        ...prev,
        [draft.id]: {
          title: draft.title || raw.source_title || '',
          imageUrl: draft.image_url || raw.main_image_url || '',
          skuEdits: {},
          skuDeletes: [],
        },
      };
    });
  };

  const saveOneDraft = async (draft: DraftSummary): Promise<boolean> => {
    const edit = edits[draft.id];
    if (!edit || !draftDirty(draft, edits)) return false;
    const raw = draft.raw_payload || {};
    const variants: DraftVariant[] = raw.source_variant_records || [];
    const skuEdits: Record<string, string> = {};
    const skuDeletes: string[] = [];
    if (variants.length) {
      const editMap = edit.skuEdits;
      const deleteSet = new Set(edit.skuDeletes);
      for (const variant of variants) {
        const attributes = variant.attributes || {};
        const label = Object.values(attributes).join('/');
        const skuId = String(variant.sku_id || variant.source_sku_id || label);
        if (deleteSet.has(skuId) || deleteSet.has(label)) {
          skuDeletes.push(label);
        } else if (editMap[skuId] !== undefined && editMap[skuId] !== (variant.display_name || label)) {
          skuEdits[label] = editMap[skuId];
        }
      }
    }
    await ppRequest(ctx, `${API_BASE}/drafts/${draft.id}`, {
      method: 'PATCH',
      body: {
        title: edit.title,
        image_url: edit.imageUrl,
        sku_name_edits: skuEdits,
        sku_name_deletes: skuDeletes,
      },
    });
    return true;
  };

  const saveRow = async (draft: DraftSummary) => {
    setLoading(true);
    try {
      const saved = await saveOneDraft(draft);
      await refresh();
      notify(saved ? '已保存该草稿修改' : '该行没有需要保存的修改');
    } catch (err) {
      fail(err);
    } finally {
      setLoading(false);
    }
  };

  const saveDrafts = async (onlySelected = false) => {
    const targets = drafts.filter((d) => {
      if (!draftDirty(d, edits)) return false;
      return onlySelected ? selectedIds.has(d.id) : true;
    });
    if (!targets.length) {
      notify(onlySelected ? '没有需要保存的已选修改' : '没有未保存的修改');
      return;
    }
    setLoading(true);
    try {
      for (const draft of targets) {
        await saveOneDraft(draft);
      }
      await refresh();
      notify(`已保存 ${targets.length} 条草稿修改`);
    } catch (err) {
      fail(err);
    } finally {
      setLoading(false);
    }
  };

  const deleteSelected = async () => {
    if (!selectedIds.size) return;
    setLoading(true);
    try {
      await ppRequest(ctx, `${API_BASE}/drafts/delete`, {
        body: { draft_ids: Array.from(selectedIds), delete_all: false },
      });
      setSelectedIds(new Set());
      await refresh();
      notify('已移除勾选草稿');
    } catch (err) {
      fail(err);
    } finally {
      setLoading(false);
    }
  };

  const startBatch = async (preflightOnly = false) => {
    if (!selectedIds.size) {
      setError('请先勾选需要处理的草稿');
      return;
    }
    setLoading(true);
    setMessage('');
    setError('');
    try {
      const data = await ppRequest<TaskOutputsResponse>(ctx, `${API_BASE}/drafts/process`, {
        body: {
          title: preflightOnly ? '预检任务' : '产品处理任务',
          draft_ids: Array.from(selectedIds),
          max_products: options.maxProducts,
          async_mode: true,
          preflight_only: preflightOnly,
          target_site: options.targetSite,
          target_language: options.targetLanguage,
          processing_scope: options.processingScope,
          qualification_mode: options.qualificationMode,
          include_product_video: options.includeProductVideo,
          skip_duplicates: options.skipDuplicates,
          ip_check: options.ipCheck,
        },
      });
      setBatch(data);
      await refresh();
      notify(data.message || (preflightOnly ? '预检完成' : '批次已提交'));
    } catch (err) {
      fail(err);
    } finally {
      setLoading(false);
    }
  };

  const pauseBatch = async () => {
    if (!batch) return;
    try {
      const data = await ppRequest<TaskOutputsResponse>(ctx, `${API_BASE}/tasks/${batch.task_id}/pause`, { body: {} });
      setBatch(data);
      notify(data.message);
    } catch (err) {
      fail(err);
    }
  };

  const resumeBatch = async () => {
    if (!batch) return;
    try {
      const data = await ppRequest<TaskOutputsResponse>(ctx, `${API_BASE}/tasks/${batch.task_id}/resume`, { body: {} });
      setBatch(data);
      notify(data.message);
    } catch (err) {
      fail(err);
    }
  };

  const retryFailures = async () => {
    if (!batch) return;
    try {
      const data = await ppRequest<TaskOutputsResponse>(ctx, `${API_BASE}/tasks/${batch.task_id}/retry-attention`, { body: {} });
      setBatch(data);
      await refresh();
      notify(data.message);
    } catch (err) {
      fail(err);
    }
  };

  const clearBatch = async () => {
    if (!batch) return;
    try {
      await ppRequest(ctx, `${API_BASE}/tasks/${batch.task_id}/clear`, { body: {} });
      setBatch(null);
      await refresh();
      notify('已清空当前任务');
    } catch (err) {
      fail(err);
    }
  };

  const download = (kind: string, filename: string) => {
    if (!batch) return;
    ppDownload(ctx, `${API_BASE}/tasks/${batch.task_id}/download?kind=${kind}`, filename).catch(fail);
  };

  const probeAi = async () => {
    setAiPing(null);
    try {
      const data = await ppRequest<{ ok: boolean; model_count: number; models_sample: string[] }>(
        ctx,
        `${API_BASE}/ai/ping`,
        { body: {} }
      );
      setAiPing({ ok: true, detail: `连通正常，可用模型 ${data.model_count} 个，示例：${(data.models_sample || []).slice(0, 3).join('、')}` });
    } catch (err) {
      setAiPing({ ok: false, detail: err instanceof Error ? err.message : String(err) });
    }
  };

  const loadHistory = async (taskId: number) => {
    try {
      const data = await ppRequest<TaskOutputsResponse>(ctx, `${API_BASE}/tasks/${taskId}/outputs`);
      setBatch(data);
    } catch (err) {
      fail(err);
    }
  };

  const taskStatusLabel = (status: string) =>
    ({
      queued: '等待处理',
      running: '处理中',
      paused: '已暂停',
      completed: '已完成',
      completed_with_review: '完成，仍有待确认项',
      failed: '任务失败',
    })[status] || status;

  return (
    <div className="verify-page">
      <header className="verify-commandbar">
        <div className="verify-command-title">
          <span className="verify-eyebrow">PRODUCT PROCESSING · 产品处理</span>
          <h1>产品处理草稿池</h1>
          <p>采集先入池，默认只处理勾选草稿；暂停、确认与继续均以本地持久状态为准。</p>
        </div>
        <div className="verify-command-stats">
          <span><strong>{selectableDrafts.length}</strong>待处理</span>
          <span><strong>{selectedIds.size}</strong>已勾选</span>
          <span><strong>{dirtyCount}</strong>未保存</span>
        </div>
      </header>

      {(message || error) && (
        <div className={`verify-message ${error ? 'error' : ''}`}>{error || message}</div>
      )}

      <section className="verify-section">
        <div className="verify-section-head">
          <h2>草稿池</h2>
          <div className="verify-actions">
            <button onClick={selectAll}>全选本页</button>
            <button onClick={clearSelection}>取消选择</button>
            <button onClick={() => saveDrafts(true)} disabled={loading}>保存已选</button>
            <button onClick={() => saveDrafts(false)} disabled={loading}>保存全部修改</button>
            <button onClick={deleteSelected} disabled={!selectedIds.size}>移除已选</button>
            <button onClick={() => refresh().catch(fail)} disabled={loading}>刷新</button>
          </div>
        </div>

        <div className="verify-pool-toolbar">
          <div className="verify-pool-stats">
            <span>待处理 <strong>{selectableDrafts.length}</strong></span>
            <span>已选 <strong>{selectedIds.size}</strong></span>
            <span>本页 <strong>{pageDrafts.length}</strong></span>
            {dirtyCount > 0 && <span className="dirty">未保存修改 <strong>{dirtyCount}</strong></span>}
          </div>
          <div className="verify-pool-controls">
            <select
              aria-label="按状态筛选"
              value={statusFilter}
              onChange={(e) => {
                setStatusFilter(e.target.value as typeof statusFilter);
                setPage(1);
              }}
            >
              <option value="all">全部状态</option>
              <option value="draft">待处理</option>
              <option value="processed">已处理</option>
              <option value="attention_required">需确认</option>
            </select>
            <input
              type="search"
              className="verify-pool-search"
              placeholder="搜索标题 / SKC / SKU / 来源…"
              value={searchTerm}
              onChange={(e) => {
                setSearchTerm(e.target.value);
                setPage(1);
              }}
            />
          </div>
        </div>

        {totalDrafts === 0 && <p className="verify-empty">草稿池为空，请先在每日选品中确认入池。</p>}
        <div className="verify-draft-list">
          {pageDrafts.map((draft) => {
            const raw = draft.raw_payload || {};
            const variants: DraftVariant[] = raw.source_variant_records || [];
            const edit = edits[draft.id];
            const isExpanded = expandedId === draft.id;
            const imgUrl = draft.image_url || raw.main_image_url || '';
            const platform = raw.source_platform || raw.platform || 'manual';
            const selectionCriteria = raw.selection_criteria || {};
            const categoryPath = raw.category || raw.source_category_path || raw.category_path || selectionCriteria.category || '';
            const sourceUrl = raw.source_url || raw.product_link || '';
            const displayTitle = edit?.title || draft.title || raw.source_title || '未命名商品';
            const skcText = draft.skc || '-';
            const skuText = draft.sku || '-';
            const copy = (t: string) => { navigator.clipboard.writeText(t).catch(() => undefined); };
            const onSelect = () => toggleDraft(draft.id);
            const isSelected = selectedIds.has(draft.id);
            return (
              <article key={draft.id} className={`pool-card ${isSelected ? 'selected' : ''}`}>
                <div className="pool-card-body">
                  {/* 选择 */}
                  <label className="pool-check">
                    <input type="checkbox" checked={isSelected} onChange={onSelect} />
                  </label>

                  {/* 主图 */}
                  <div className="pool-thumb" onClick={onSelect}>
                    {imgUrl ? (
                      <img src={imgUrl} alt="" referrerPolicy="no-referrer" />
                    ) : (
                      <span>暂无主图</span>
                    )}
                    <span className="pool-thumb-overlay">上传/替换主图</span>
                  </div>

                  {/* 信息区 */}
                  <div className="pool-info">
                    {/* 标题 + 操作按钮 */}
                    <div className="pool-title-row">
                      <strong title={displayTitle}>{displayTitle}</strong>
                      <div className="pool-inline-acts">
                        <button className="btn-mini" onClick={() => copy(displayTitle)}>复制</button>
                        <button className="btn-mini" onClick={() => beginEdit(draft)}>
                          {isExpanded ? '收起' : '编辑'}
                        </button>
                        <button className="btn-mini danger" onClick={() => {
                          if (window.confirm('确认删除该草稿？')) deleteSelected();
                        }}>删除</button>
                      </div>
                    </div>

                    {/* 平台 + 来源链接 */}
                    <div className="pool-meta">
                      <span className="tag">{platform}</span>
                      {sourceUrl ? (
                        <a href={sourceUrl} target="_blank" rel="noreferrer">{draft.source_ref || '查看来源'}</a>
                      ) : (
                        <span className="muted">{draft.source_ref || '-'}</span>
                      )}
                      {isExpanded && <button className="btn-mini" onClick={() => copy(sourceUrl || draft.source_ref || '')}>复制链接</button>}
                    </div>

                    {/* SKU 信息 */}
                    <div className="pool-sku-info">
                      <span className="pool-label">SKU</span>
                      <span className="pool-value">
                        {variants.length > 0 ? (
                          <>
                            {variants.slice(0, 4).map((v, i) => {
                              const attrs = v.attributes || {};
                              const vals = Object.values(attrs).filter(Boolean);
                              const label = vals.length ? vals.join(' / ') : (v.display_name || '-');
                              return (
                                <span key={i} className="sku-chip" title={label}>
                                  {label}
                                </span>
                              );
                            })}
                            {variants.length > 4 && (
                              <span className="sku-chip more">+{variants.length - 4} 个变种</span>
                            )}
                          </>
                        ) : (
                          <span>单规格</span>
                        )}
                      </span>
                      <div className="pool-inline-acts">
                        <button className="btn-mini" onClick={() => copy(skcText)}>复制SKC</button>
                        <button className="btn-mini" onClick={() => copy(skuText)}>复制SKU</button>
                      </div>
                    </div>

                    {/* 类目 */}
                    <div className="pool-cat-info">
                      <span className="pool-label">类目</span>
                      <span className="pool-value">{categoryPath || '（参考）'}</span>
                      {categoryPath && <button className="btn-mini" onClick={() => copy(categoryPath)}>复制</button>}
                    </div>

                    {/* 价格 / 图片数 等补充信息 */}
                    <div className="pool-extra">
                      {draft.cost != null && <span>成本 ¥{draft.cost}</span>}
                      {draft.declared_price != null && <span>申报 ¥{draft.declared_price}</span>}
                      {variants.length > 0 && <span>{variants.length} 个变种</span>}
                      {Array.isArray(raw.source_image_urls) && raw.source_image_urls.length > 0 && (
                        <span>{raw.source_image_urls.length} 张来源图</span>
                      )}
                      {draft.status === 'attention_required' && <span className="badge attn">需确认</span>}
                      {draft.status === 'processed' && <span className="badge ok">已处理</span>}
                      {Array.isArray(raw.risk_tags) && raw.risk_tags.length > 0 && (
                        <span className="badge risk">风险: {raw.risk_tags.join('、')}</span>
                      )}
                    </div>
                  </div>
                </div>

                {/* 展开编辑面板 */}
                {isExpanded && edit && (
                  <div className="pool-edit-panel">
                    <div className="verify-editor-grid">
                      <label>商品标题
                        <input value={edit.title} onChange={(e) => setEdits((p) => ({ ...p, [draft.id]: { ...edit, title: e.target.value } }))} />
                      </label>
                      <div className="verify-source-ref">
                        <span>来源</span>
                        <p>{draft.source_ref || raw.product_link || raw.source_url || '-'}</p>
                      </div>
                      <div className="verify-source-ref">
                        <span>主图</span>
                        <p>{imgUrl || '待补充'}</p>
                      </div>
                    </div>
                    {variants.length > 0 && (
                      <div className="verify-sku-list">
                        {variants.map((variant, idx) => {
                          const attrs = variant.attributes || {};
                          const label = Object.values(attrs).join('/');
                          const skuId = String(variant.sku_id || variant.source_sku_id || label || idx);
                          const isDeleted = edit.skuDeletes.includes(skuId) || edit.skuDeletes.includes(label);
                          const value = edit.skuEdits[skuId] ?? edit.skuEdits[label] ?? variant.display_name ?? label;
                          return (
                            <section key={idx} className={`verify-sku ${isDeleted ? 'deleted' : ''}`}>
                              <header>
                                <div><strong>SKU {idx + 1}</strong><small>{variant.source_sku_id ?? '无货号'}</small></div>
                                <button onClick={() => setEdits((p) => { const cur = p[draft.id]!; const s = new Set(cur.skuDeletes); s.has(skuId) ? s.delete(skuId) : s.add(skuId); return { ...p, [draft.id]: { ...cur, skuDeletes: Array.from(s) } }; })}>
                                  {isDeleted ? '恢复' : '删除'}
                                </button>
                              </header>
                              {!isDeleted ? (
                                <div className="verify-variant-grid">
                                  <label><span>{label || '规格属性'}</span>
                                    <input value={value} onChange={(e) => setEdits((p) => ({ ...p, [draft.id]: { ...edit, skuEdits: { ...edit.skuEdits, [skuId]: e.target.value } } }))} />
                                  </label>
                                  <small>¥{variant.price_cny ?? '-'} · 起订 {variant.min_order_quantity ?? '-'}</small>
                                </div>
                              ) : (
                                <p className="verify-sku-note">该 SKU 不会进入下次处理。</p>
                              )}
                            </section>
                          );
                        })}
                      </div>
                    )}
                    <div className="verify-row-actions">
                      <button className="primary" onClick={() => saveRow(draft)} disabled={loading}>保存</button>
                    </div>
                  </div>
                )}
              </article>
            );
          })}
        </div>
        {totalDrafts > 0 && (
          <footer className="verify-pagination">
            <span>
              第 {pageStart + 1}–{Math.min(pageStart + pageDrafts.length, totalDrafts)} 条，共 {totalDrafts} 条
            </span>
            <button type="button" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1}>
              上一页
            </button>
            <button type="button" onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page >= totalPages}>
              下一页
            </button>
          </footer>
        )}
      </section>

      <section className="verify-section">
        <div className="verify-section-head">
          <h2>处理设置</h2>
          <span className="verify-sub">选择站点、范围与数量后开始处理</span>
        </div>
        <div className="verify-form-row">
          <label>站点
            <select
              value={options.targetSite}
              onChange={(e) => {
                const site = e.target.value as ProductProcessingOptions['targetSite'];
                setOptions((p) => ({
                  ...p,
                  targetSite: site,
                  targetLanguage: site === 'US' ? 'en' : 'es',
                }));
              }}
            >
              {SITES.map((s) => <option key={s.code} value={s.code}>{s.label}</option>)}
            </select>
          </label>
          <label>语言
            <select
              value={options.targetLanguage}
              onChange={(e) => setOptions((p) => ({ ...p, targetLanguage: e.target.value as any }))}
            >
              <option value="en">英语 · English</option>
              <option value="es">西班牙语 · Español</option>
            </select>
          </label>
          <label>资质模式
            <select
              value={options.qualificationMode}
              onChange={(e) => setOptions((p) => ({ ...p, qualificationMode: e.target.value as any }))}
            >
              <option value="standard">标准</option>
              <option value="strict">严格</option>
            </select>
          </label>
        </div>
        <div className="verify-scope-row">
          <span className="verify-scope-label">处理范围：</span>
          {SCOPES.map((scope) => (
            <label key={scope.key} className="verify-scope-check">
              <input
                type="checkbox"
                checked={options.processingScope.includes(scope.key)}
                onChange={() =>
                  setOptions((p) => {
                    const next = new Set(p.processingScope);
                    if (next.has(scope.key)) next.delete(scope.key);
                    else next.add(scope.key);
                    return { ...p, processingScope: Array.from(next) };
                  })
                }
              />
              {scope.label}
            </label>
          ))}
          <label className="verify-scope-check">
            <input
              type="checkbox"
              checked={options.includeProductVideo}
              onChange={(e) => setOptions((p) => ({ ...p, includeProductVideo: e.target.checked }))}
            />
            生成商品视频
          </label>
          <label className="verify-scope-check">
            <input
              type="checkbox"
              checked={options.skipDuplicates}
              onChange={(e) => setOptions((p) => ({ ...p, skipDuplicates: e.target.checked }))}
            />
            跳过已处理
          </label>
          <label className="verify-scope-check">
            <input
              type="checkbox"
              checked={options.ipCheck}
              onChange={(e) => setOptions((p) => ({ ...p, ipCheck: e.target.checked }))}
            />
            侵权词过滤
          </label>
        </div>
        <div className="verify-slider-row">
          <span className="verify-scope-label">处理数量：</span>
          <input
            className="verify-slider"
            type="range"
            min={0}
            max={100}
            step={1}
            value={options.maxProducts}
            onChange={(e) =>
              setOptions((p) => ({ ...p, maxProducts: Number(e.target.value) || 0 }))
            }
          />
          <span className="verify-slider-value">
            {options.maxProducts === 0 ? '全部（≤100）' : options.maxProducts}
          </span>
        </div>
        <div className="verify-actions">
          <button className="primary" onClick={() => startBatch(false)} disabled={loading || !selectedIds.size}>
            {loading ? '处理中…' : '开始处理'}
          </button>
          <button onClick={() => startBatch(true)} disabled={loading || !selectedIds.size}>预检</button>
          <button onClick={pauseBatch} disabled={!batch}>暂停</button>
          <button onClick={resumeBatch} disabled={!batch}>继续</button>
          <button onClick={retryFailures} disabled={!batch || !failureItems.length}>重试失败项</button>
          <button onClick={clearBatch} disabled={!batch}>清空任务</button>
        </div>
      </section>

      <section className="verify-section">
        <div className="verify-section-head">
          <h2>处理结果</h2>
          <span className="verify-sub">{batch ? taskStatusLabel(batch.task.status) : '暂无任务'}</span>
        </div>
        {batch && (
          <>
            <div className="verify-summary">
              <div className="verify-count">总数 <b>{batch.total_count}</b></div>
              <div className="verify-count success">成功 <b>{batch.success_count}</b></div>
              <div className="verify-count failed">失败 <b>{batch.failed_count}</b></div>
              <div className="verify-count">跳过 <b>{batch.skipped_count}</b></div>
              <div className="verify-count">待确认 <b>{batch.attention_required_count}</b></div>
              <div className="verify-count">技术可重试 <b>{batch.technical_retryable_count}</b></div>
              <div className="verify-count">配置阻断 <b>{batch.configuration_blocked_count}</b></div>
            </div>
            <div className="verify-actions">
              <button onClick={() => download('dxm', `dxm_import_task_${batch.task_id}.xlsx`)}>下载店小秘导入表</button>
              <button onClick={() => download('errors', `error_report_task_${batch.task_id}.csv`)}>下载失败原因表</button>
              <button onClick={() => download('video_manifest', `product_video_manifest_task_${batch.task_id}.csv`)}>下载视频清单</button>
            </div>
          </>
        )}
        {!batch && <p className="verify-empty">尚未提交处理批次。请先在草稿池勾选草稿并点击“开始处理”。</p>}
      </section>

      {failureItems.length > 0 && (
        <section className="verify-section">
          <div className="verify-section-head">
            <h2>失败商品</h2>
            <span className="verify-sub">失败分类与原版保持一致</span>
          </div>
          <table className="verify-table">
            <thead>
              <tr>
                <th>SKC</th>
                <th>标题</th>
                <th>状态</th>
                <th>失败类型</th>
                <th>原因</th>
                <th>操作提示</th>
                <th>可重试</th>
              </tr>
            </thead>
            <tbody>
              {failureItems.map((item) => {
                const result = (item.result as any) || {};
                const failureClass = result.failure_class || 'unknown';
                return (
                  <tr key={item.id}>
                    <td>{item.skc || '-'}</td>
                    <td>{item.title || '-'}</td>
                    <td>{item.status === 'attention_required' ? '待确认' : '失败'}</td>
                    <td>{FAILURE_CLASS_LABELS[failureClass] || failureClass}</td>
                    <td>{item.reason || '-'}</td>
                    <td>{result.operator_hint || '-'}</td>
                    <td>{result.retryable ? '是' : '否'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      )}

      {aiConfig && (
        <section className="verify-section">
          <div className="verify-section-head">
            <h2>AI 中转配置</h2>
            <span className="verify-sub">对照原型 native_product_engine 配置（OpenAI 兼容中转）</span>
          </div>
          <div className="verify-ai-config">
            <div className="verify-ai-item"><span>提供方</span><b>{aiConfig.provider}</b></div>
            <div className="verify-ai-item"><span>Base URL</span><b>{aiConfig.base_url}</b></div>
            <div className="verify-ai-item"><span>API Key</span><b>{aiConfig.api_key_configured ? aiConfig.api_key_masked : '未配置'}</b></div>
            <div className="verify-ai-item"><span>文本模型</span><b>{aiConfig.text_model}</b></div>
            <div className="verify-ai-item"><span>回退模型</span><b>{aiConfig.text_model_fallback_order.join(' → ')}</b></div>
            <div className="verify-ai-item"><span>生图模型</span><b>{aiConfig.image_model} / {aiConfig.reference_image_model}</b></div>
            <div className="verify-ai-item"><span>生图规格</span><b>{aiConfig.image_size} · {aiConfig.image_quality}</b></div>
          </div>
          <div className="verify-actions">
            <button onClick={probeAi}>探测连通性</button>
            {aiPing && (
              <span className={`verify-ai-ping ${aiPing.ok ? 'ok' : 'fail'}`}>{aiPing.detail}</span>
            )}
          </div>
        </section>
      )}

      <section className="verify-section">
        <div className="verify-section-head">
          <h2>高级功能（原版有 · 当前后端覆盖状态）</h2>
        </div>
        <div className="verify-advanced-grid">
          <div className="verify-advanced-card">
            <h4>身份复核（Identity Review）</h4>
            <span className="verify-unsupported">后端暂未提供 · 对应 confirm_identity_review 端点</span>
            <p>原版在商品身份与末级类目存疑时要求人工确认。当前实现以配置阻断失败分类近似替代。</p>
          </div>
          <div className="verify-advanced-card">
            <h4>尺寸复核（Dimensions Review）</h4>
            <span className="verify-unsupported">后端暂未提供 · 对应 confirm_logistics_review 端点</span>
            <p>原版在 SKU 规格缺失时要求人工确认长宽高重量。当前缺失字段会直接进入失败列表。</p>
          </div>
          <div className="verify-advanced-card">
            <h4>附加图片任务（Extra Images）</h4>
            <span className="verify-unsupported">后端暂未提供 · 对应 extra_images 任务组</span>
            <p>原版展示四宫格/详情图/视频等图片任务的积分、尝试次数与导出状态。</p>
          </div>
          <div className="verify-advanced-card">
            <h4>JSON 采集文件导入（Capture）</h4>
            <span className="verify-unsupported">后端暂未提供 · 原版支持 .json 采集文件（≤2MB/单、≤20MB/批）</span>
            <p>当前支持 Excel 导入（/import）与每日选品 API 入池（/intake/daily-selection）。</p>
          </div>
        </div>
      </section>

      <section className="verify-section">
        <div className="verify-section-head">
          <h2>历史任务</h2>
        </div>
        {history.length === 0 && <p className="verify-empty">暂无历史任务</p>}
        <ul className="verify-history">
          {history.map((task) => (
            <li key={task.task_id}>
              <span className="verify-history-title">{task.title}</span>
              <span className="verify-badge">{taskStatusLabel(task.status)}</span>
              <span className="verify-history-counts">总数 {task.total_count} / 成功 {task.success_count} / 失败 {task.failed_count}</span>
              <span className="verify-history-date">{new Date(task.created_at).toLocaleString('zh-CN')}</span>
              <button onClick={() => loadHistory(task.task_id)}>查看</button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

export default ProductProcessingVerifyPage;
