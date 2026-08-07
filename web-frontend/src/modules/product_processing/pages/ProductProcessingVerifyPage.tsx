import { useEffect, useMemo, useRef, useState } from 'react';
import { ppRequest, type ApiContext } from '../api/client';
import type {
  DraftSummary,
  DraftVariant,
  ProductProcessingOptions,
} from '../types';
import '../styles/ProductProcessingVerifyPage.css';

const API_BASE = '/api/product-processing';

const PAGE_SIZE = 10;

type DraftEdit = {
  title: string;
  imageUrl: string;
  skuEdits: Record<string, string>;
  skuDeletes: string[];
};

type Props = {
  onStartProcessing?: (draftIds: number[], options: ProductProcessingOptions) => void;
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

export function ProductProcessingVerifyPage({ onStartProcessing }: Props) {
  const ctx = api();
  const [options, setOptions] = useState<ProductProcessingOptions>({
    targetSite: 'US',
    targetLanguage: 'en',
    maxProducts: 0,
    processingScope: ['title', 'details', 'product_dimensions', 'four_grid', 'detail_images', 'qualification'],
    qualificationMode: 'standard',
    includeProductVideo: false,
    skipDuplicates: false,
    ipCheck: true,
    maxParallelDrafts: 5,
  });
  const [drafts, setDrafts] = useState<DraftSummary[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [edits, setEdits] = useState<Record<number, DraftEdit>>({});
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [page, setPage] = useState(1);
  const [jumpPage, setJumpPage] = useState('');
  const [statusFilter, setStatusFilter] = useState<'all' | 'draft' | 'processed' | 'attention_required'>('all');
  const [searchTerm, setSearchTerm] = useState('');
  const draftListRef = useRef<HTMLDivElement>(null);

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

  const notify = (ok: string) => { setMessage(ok); setError(''); };
  const fail = (err: unknown) => { setError(err instanceof Error ? err.message : String(err)); setMessage(''); };

  const refresh = async () => {
    const draftData = await ppRequest<{ drafts: DraftSummary[] }>(ctx, `${API_BASE}/drafts?view=summary&limit=500`);
    setDrafts(draftData.drafts || []);
  };

  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);

  useEffect(() => {
    draftListRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [page]);

  useEffect(() => {
    refresh().catch(fail);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleDraft = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) { next.delete(id); }
      else if (next.size < 100) { next.add(id); }
      else { setError('一次最多处理 100 个草稿'); }
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
      return { ...prev, [draft.id]: { title: draft.title || raw.source_title || '', imageUrl: draft.image_url || raw.main_image_url || '', skuEdits: {}, skuDeletes: [] } };
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
        if (deleteSet.has(skuId) || deleteSet.has(label)) { skuDeletes.push(label); }
        else if (editMap[skuId] !== undefined && editMap[skuId] !== (variant.display_name || label)) { skuEdits[label] = editMap[skuId]; }
      }
    }
    await ppRequest(ctx, `${API_BASE}/drafts/${draft.id}`, { method: 'PATCH', body: { title: edit.title, image_url: edit.imageUrl, sku_name_edits: skuEdits, sku_name_deletes: skuDeletes } });
    return true;
  };

  const saveRow = async (draft: DraftSummary) => {
    setLoading(true);
    try {
      const saved = await saveOneDraft(draft);
      await refresh();
      notify(saved ? '已保存该草稿修改' : '该行没有需要保存的修改');
    } catch (err) { fail(err); } finally { setLoading(false); }
  };

  const saveDrafts = async (onlySelected = false) => {
    const targets = drafts.filter((d) => {
      if (!draftDirty(d, edits)) return false;
      return onlySelected ? selectedIds.has(d.id) : true;
    });
    if (!targets.length) { notify(onlySelected ? '没有需要保存的已选修改' : '没有未保存的修改'); return; }
    setLoading(true);
    try {
      for (const draft of targets) { await saveOneDraft(draft); }
      await refresh();
      notify(`已保存 ${targets.length} 条草稿修改`);
    } catch (err) { fail(err); } finally { setLoading(false); }
  };

  const deleteSelected = async () => {
    if (!selectedIds.size) return;
    setLoading(true);
    try {
      await ppRequest(ctx, `${API_BASE}/drafts/delete`, { body: { draft_ids: Array.from(selectedIds), delete_all: false } });
      setSelectedIds(new Set());
      await refresh();
      notify('已移除勾选草稿');
    } catch (err) { fail(err); } finally { setLoading(false); }
  };

  const handleProcess = (preflightOnly = false) => {
    if (!selectedIds.size) { setError('请先勾选需要处理的草稿'); return; }
    onStartProcessing?.(Array.from(selectedIds), {
      ...options,
      ...(preflightOnly ? { preflightOnly: true } as Partial<ProductProcessingOptions> : {}),
    });
  };

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

      <section className="verify-section" ref={draftListRef}>
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
              onChange={(e) => { setStatusFilter(e.target.value as typeof statusFilter); setPage(1); }}
            >
              <option value="all">全部状态</option>
              <option value="draft">待处理</option>
              <option value="processed">已处理</option>
              <option value="attention_required">需确认</option>
            </select>
            <input
              type="search"
              className="verify-pool-search"
              placeholder="搜索标题 / SKC / SKU / 来源..."
              value={searchTerm}
              onChange={(e) => { setSearchTerm(e.target.value); setPage(1); }}
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
                  <label className="pool-check">
                    <input type="checkbox" checked={isSelected} onChange={onSelect} />
                  </label>
                  <div className="pool-thumb" onClick={onSelect}>
                    {imgUrl ? (
                      <img src={imgUrl} alt="" referrerPolicy="no-referrer" />
                    ) : (
                      <span>暂无主图</span>
                    )}
                    <span className="pool-thumb-overlay">上传/替换主图</span>
                  </div>
                  <div className="pool-info">
                    <div className="pool-title-row">
                      <strong title={displayTitle}>{displayTitle}</strong>
                      <div className="pool-inline-acts">
                        <button className="btn-mini" onClick={() => copy(displayTitle)}>复制</button>
                        <button className="btn-mini" onClick={() => beginEdit(draft)}>{isExpanded ? '收起' : '编辑'}</button>
                        <button className="btn-mini danger" onClick={() => { if (window.confirm('确认删除该草稿？')) deleteSelected(); }}>删除</button>
                      </div>
                    </div>
                    <div className="pool-meta">
                      <span className="tag">{platform}</span>
                      {sourceUrl ? (
                        <a href={sourceUrl} target="_blank" rel="noreferrer">{draft.source_ref || '查看来源'}</a>
                      ) : (
                        <span className="muted">{draft.source_ref || '-'}</span>
                      )}
                    </div>
                    <div className="pool-sku-info">
                      <span className="pool-label">SKU</span>
                      <span className="pool-value">
                        {variants.length > 0 ? (
                          <>
                            {variants.slice(0, 4).map((v, i) => {
                              const attrs = v.attributes || {};
                              const vals = Object.values(attrs).filter(Boolean);
                              const label = vals.length ? vals.join(' / ') : (v.display_name || '-');
                              return (<span key={i} className="sku-chip" title={label}>{label}</span>);
                            })}
                            {variants.length > 4 && (<span className="sku-chip more">+{variants.length - 4} 个变种</span>)}
                          </>
                        ) : (<span>单规格</span>)}
                      </span>
                      <div className="pool-inline-acts">
                        <button className="btn-mini" onClick={() => copy(skcText)}>复制SKC</button>
                        <button className="btn-mini" onClick={() => copy(skuText)}>复制SKU</button>
                      </div>
                    </div>
                    <div className="pool-cat-info">
                      <span className="pool-label">类目</span>
                      <span className="pool-value">{categoryPath || '（参考）'}</span>
                      {categoryPath && <button className="btn-mini" onClick={() => copy(categoryPath)}>复制</button>}
                    </div>
                    <div className="pool-extra">
                      {draft.cost != null && <span>成本 ¥{draft.cost}</span>}
                      {draft.declared_price != null && <span>申报 ¥{draft.declared_price}</span>}
                      {variants.length > 0 && <span>{variants.length} 个变种</span>}
                      {Array.isArray(raw.source_image_urls) && raw.source_image_urls.length > 0 && (<span>{raw.source_image_urls.length} 张来源图</span>)}
                      {draft.status === 'attention_required' && <span className="badge attn">需确认</span>}
                      {draft.status === 'processed' && <span className="badge ok">已处理</span>}
                      {Array.isArray(raw.risk_tags) && raw.risk_tags.length > 0 && (<span className="badge risk">风险: {raw.risk_tags.join('、')}</span>)}
                    </div>
                  </div>
                </div>
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
                                <button onClick={() => setEdits((p) => { const cur = p[draft.id]!; const s = new Set(cur.skuDeletes); s.has(skuId) ? s.delete(skuId) : s.add(skuId); return { ...p, [draft.id]: { ...cur, skuDeletes: Array.from(s) } }; })}>{isDeleted ? '恢复' : '删除'}</button>
                              </header>
                              {!isDeleted ? (
                                <div className="verify-variant-grid">
                                  <label><span>{label || '规格属性'}</span>
                                    <input value={value} onChange={(e) => setEdits((p) => ({ ...p, [draft.id]: { ...edit, skuEdits: { ...edit.skuEdits, [skuId]: e.target.value } } }))} />
                                  </label>
                                  <small>¥{variant.price_cny ?? '-'} · 起订 {variant.min_order_quantity ?? '-'}</small>
                                </div>
                              ) : (<p className="verify-sku-note">该 SKU 不会进入下次处理。</p>)}
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
              第 {pageStart + 1}-{Math.min(pageStart + pageDrafts.length, totalDrafts)} 条，共 {totalDrafts} 条
            </span>
            <div className="verify-pagination-pages">
              <button type="button" onClick={() => setPage(1)} disabled={page <= 1}>首页</button>
              <button type="button" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1}>上一页</button>
              {Array.from({ length: Math.min(totalPages, 7) }, (_, i) => {
                const start = Math.max(1, Math.min(page - 3, totalPages - 6));
                const pn = start + i;
                if (pn > totalPages) return null;
                return (<button key={pn} className={pn === page ? 'active' : ''} onClick={() => setPage(pn)}>{pn}</button>);
              })}
              <button type="button" onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page >= totalPages}>下一页</button>
              <button type="button" onClick={() => setPage(totalPages)} disabled={page >= totalPages}>末页</button>
            </div>
            <span className="verify-pagination-jump">
              跳至
              <input
                type="number"
                min={1}
                max={totalPages}
                value={jumpPage}
                placeholder={String(page)}
                onChange={(e) => setJumpPage(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') { const n = parseInt(jumpPage, 10); if (n >= 1 && n <= totalPages) { setPage(n); setJumpPage(''); } } }}
              />
              页
            </span>
          </footer>
        )}
      </section>

      <section className="verify-quickbar">
        <div className="verify-actions">
          <button className="primary" onClick={() => handleProcess(false)} disabled={loading || !selectedIds.size}>开始处理</button>
          <button onClick={() => handleProcess(true)} disabled={loading || !selectedIds.size}>预检</button>
          <button onClick={() => saveDrafts(true)} disabled={loading}>保存已选</button>
          <button onClick={() => saveDrafts(false)} disabled={loading}>保存全部修改</button>
          <button onClick={deleteSelected} disabled={!selectedIds.size}>移除已选</button>
        </div>
      </section>
    </div>
  );
}

export default ProductProcessingVerifyPage;
