import { useEffect, useMemo, useRef, useState } from 'react';
import { useChangePoller } from '../../../shared/hooks/useChangePoller';
import { SkuBatchManager } from '../components/SkuBatchManager';
import { ppRequest, type ApiContext } from '../api/client';
import type {
  DraftSummary,
  DraftVariant,
  ProductProcessingOptions,
  TaskHistoryItem,
} from '../types';
import '../styles/ProductProcessingVerifyPage.css';

const API_BASE = '/api/product-processing';

type DraftEdit = {
  title: string;
  imageUrl: string;
  skuEdits: Record<string, string>;
  skuDeletes: string[];
};

type Props = {
  onStartProcessing?: (draftIds: number[], options: ProductProcessingOptions) => boolean;
  onOpenHistoryTasks?: () => void;
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

export function ProductProcessingVerifyPage({ onStartProcessing, onOpenHistoryTasks }: Props) {
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
    maxParallelDrafts: 8,
  });
  const [drafts, setDrafts] = useState<DraftSummary[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [expandedId, setExpandedId] = useState<number | null>(null);
  // SKU 管理抽屉：当前正在管理的草稿 id（null 表示关闭）
  const [skuDrawerDraftId, setSkuDrawerDraftId] = useState<number | null>(null);
  // 批量管理 SKU：跨商品按条件筛选后批量删除/保留
  const [skuBatchOpen, setSkuBatchOpen] = useState(false);
  const [edits, setEdits] = useState<Record<number, DraftEdit>>({});
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [page, setPage] = useState(1);
  const [jumpPage, setJumpPage] = useState('');
  // 单页展示数量：10 / 30 / 50 / 100
  const [pageSize, setPageSize] = useState(10);
  const [viewMode, setViewMode] = useState<'all' | 'selected'>('all');
  const [searchTerm, setSearchTerm] = useState('');
  // SKU 数量筛选：0 = 全部，否则为最少变种数
  const [skuCountFilter, setSkuCountFilter] = useState(0);
  // 不看单规格：隐藏无变种 / 仅 1 个变种的草稿
  const [hideSingleSpec, setHideSingleSpec] = useState(false);
  // 历史采集卡片：草稿池底部按钮唤出的历史任务弹层
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyClosing, setHistoryClosing] = useState(false);
  const [historyTasks, setHistoryTasks] = useState<TaskHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const draftListRef = useRef<HTMLDivElement>(null);
  const stickyToolbarRef = useRef<HTMLDivElement>(null);
  const stickySpacerRef = useRef<HTMLDivElement>(null);
  const historyCloseTimerRef = useRef<number | null>(null);

  // 滚动发生在 window（.content-card 的 overflow:auto 会作为 sticky 的滚动祖先但不参与滚动，
  // 导致纯 CSS sticky 永远吸不住）。这里用 JS 做条件吸顶：
  // 仅当工具栏自然位置滚出导航栏下方时才 position:fixed 吸顶；不需要漂浮时放回
  // 草稿池容器内部（不设 spacer 高度），避免数据少时在容器顶部留下一块空白。
  useEffect(() => {
    const toolbar = stickyToolbarRef.current;
    const spacer = stickySpacerRef.current;
    if (!toolbar || !spacer) return;
    const contentCard = document.querySelector('.content-card') as HTMLElement | null;
    const apply = () => {
      const topbar = document.querySelector('.topbar-card') as HTMLElement | null;
      let topbarBottom = 0;
      if (topbar) {
        const rect = topbar.getBoundingClientRect();
        if (rect.bottom > 0) topbarBottom = Math.round(rect.bottom);
      }
      // spacer 在文档流中始终处于工具栏的自然位置：需要吸顶时其顶部已滚到导航栏下沿之下
      const stickyGap = 0;
      const needStick = spacer.getBoundingClientRect().top <= topbarBottom + stickyGap;
      if (needStick) {
        const contentRect = contentCard?.getBoundingClientRect();
        toolbar.style.position = 'fixed';
        toolbar.style.top = `${topbarBottom + stickyGap}px`;
        toolbar.style.left = contentRect ? `${Math.round(contentRect.left)}px` : '0px';
        toolbar.style.width = contentRect ? `${Math.round(contentRect.width)}px` : '100%';
        spacer.style.height = `${toolbar.offsetHeight}px`;
        toolbar.classList.add('is-stuck');
      } else {
        toolbar.style.position = '';
        toolbar.style.top = '';
        toolbar.style.left = '';
        toolbar.style.width = '';
        spacer.style.height = '';
        toolbar.classList.remove('is-stuck');
      }
    };
    apply();
    window.addEventListener('scroll', apply, { passive: true });
    window.addEventListener('resize', apply);
    const observer = new ResizeObserver(apply);
    if (contentCard) observer.observe(contentCard);
    observer.observe(toolbar);
    observer.observe(document.body);
    return () => {
      window.removeEventListener('scroll', apply);
      window.removeEventListener('resize', apply);
      observer.disconnect();
    };
  }, []);

  const dirtyCount = useMemo(
    () => drafts.filter((d) => draftDirty(d, edits)).length,
    [drafts, edits]
  );
  // 草稿池只保留未处理项：处理完成的草稿自动从列表消失（processed），删除的不再展示。
  // 提交处理中的草稿（processing）同样隐藏，处理失败后回退 draft 会重新出现。
  // 失败/待确认的草稿保持 draft 状态，仍会显示，可勾选后重新处理。
  const selectableDrafts = useMemo(
    () => drafts.filter((d) => d.status !== 'deleted' && d.status !== 'processed' && d.status !== 'processing'),
    [drafts]
  );
  // 批量管理 SKU 的目标草稿：勾选且仍可处理的草稿
  const selectedDrafts = useMemo(
    () => drafts.filter((d) => selectedIds.has(d.id) && d.status !== 'deleted' && d.status !== 'processed' && d.status !== 'processing'),
    [drafts, selectedIds]
  );
  const filteredDrafts = useMemo(() => {
    const keyword = searchTerm.trim().toLowerCase();
    return selectableDrafts.filter((d) => {
      // 「只看已选」视图下过滤掉未勾选草稿，直接呈现全部所选链接
      if (viewMode === 'selected' && !selectedIds.has(d.id)) return false;
      const raw = d.raw_payload || {};
      const variantCount = Array.isArray(raw.source_variant_records) ? raw.source_variant_records.length : 0;
      // SKU 数量筛选：至少 N 个变种
      if (skuCountFilter > 1 && variantCount < skuCountFilter) return false;
      // 不看单规格：隐藏无变种 / 仅 1 个变种的单规格草稿
      if (hideSingleSpec && variantCount <= 1) return false;
      if (!keyword) return true;
      // 搜索框只按标题搜索
      return [d.title, d.product_name, raw.source_title]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(keyword));
    });
  }, [selectableDrafts, viewMode, selectedIds, searchTerm, skuCountFilter, hideSingleSpec]);
  const totalDrafts = filteredDrafts.length;
  const totalPages = Math.max(1, Math.ceil(totalDrafts / pageSize));
  const pageStart = (page - 1) * pageSize;
  const pageDrafts = useMemo(
    () => filteredDrafts.slice(pageStart, pageStart + pageSize),
    [filteredDrafts, pageStart, pageSize]
  );

  const notify = (ok: string) => { setMessage(ok); setError(''); };
  const fail = (err: unknown) => { setError(err instanceof Error ? err.message : String(err)); setMessage(''); };

  const refresh = async () => {
    const draftData = await ppRequest<{ drafts: DraftSummary[] }>(ctx, `${API_BASE}/drafts?view=summary&limit=500`);
    setDrafts(draftData.drafts || []);
  };

  const loadHistory = async () => {
    setHistoryLoading(true);
    setHistoryError('');
    try {
      const data = await ppRequest<{ tasks: TaskHistoryItem[] }>(
        ctx, `${API_BASE}/tasks/history?limit=6`
      );
      setHistoryTasks(data.tasks || []);
    } catch (err) {
      setHistoryTasks([]);
      setHistoryError(err instanceof Error ? err.message : '历史任务读取失败');
    } finally {
      setHistoryLoading(false);
    }
  };

  const openHistory = () => {
    if (historyCloseTimerRef.current !== null) {
      window.clearTimeout(historyCloseTimerRef.current);
      historyCloseTimerRef.current = null;
    }
    setHistoryClosing(false);
    setHistoryOpen(true);
    loadHistory();
  };

  const closeHistory = (onClosed?: () => void) => {
    if (historyClosing) return;
    setHistoryClosing(true);
    historyCloseTimerRef.current = window.setTimeout(() => {
      setHistoryOpen(false);
      setHistoryClosing(false);
      setHistoryTasks([]);
      setHistoryError('');
      historyCloseTimerRef.current = null;
      onClosed?.();
    }, 240);
  };

  useEffect(() => {
    if (!historyOpen) return;
    const previousOverflow = document.body.style.overflow;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeHistory();
    };
    document.body.style.overflow = 'hidden';
    window.addEventListener('keydown', closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', closeOnEscape);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [historyOpen]);

  useEffect(() => () => {
    if (historyCloseTimerRef.current !== null) {
      window.clearTimeout(historyCloseTimerRef.current);
    }
  }, []);

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

  // 容器级自动刷新：插件采集/每日选品确认入池/处理完成后，草稿池 revision 变化即静默重拉列表
  // （revision 指纹不变时不做无意义的全量刷新；仅页面可见时轮询，切走标签页自动暂停）
  // revision 请求必须带与列表相同的 X-Workspace-ID，否则指纹按 local 计算、与展示的 default 列表错位。
  useChangePoller({
    url: `${API_BASE}/drafts/revision`,
    headers: { "X-Workspace-ID": ctx.workspaceId },
    onChange: () => { refresh().catch(() => undefined); },
  });

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

  const saveOneDraft = async (draft: DraftSummary): Promise<DraftSummary | null> => {
    const edit = edits[draft.id];
    if (!edit || !draftDirty(draft, edits)) return null;
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
    const saved = await ppRequest<{ draft: DraftSummary }>(ctx, `${API_BASE}/drafts/${draft.id}`, { method: 'PATCH', body: { title: edit.title, image_url: edit.imageUrl, sku_name_edits: skuEdits, sku_name_deletes: skuDeletes } });
    // 返回更新后的草稿（标题/主图以服务端为准）
    return { ...draft, title: saved.draft.title ?? draft.title, image_url: saved.draft.image_url ?? edit.imageUrl };
  };

  // 保存成功后原位合并更新草稿，保持列表相对顺序不变
  const applySavedDraft = (updated: DraftSummary) => {
    setDrafts((prev) => prev.map((d) => (d.id === updated.id ? { ...d, title: updated.title, image_url: updated.image_url } : d)));
    setEdits((prev) => { const next = { ...prev }; delete next[updated.id]; return next; });
  };

  // 批量管理 SKU 的基准删除集合：已保存（raw_payload.sku_name_deletes）+ 未保存编辑态（edits.skuDeletes）
  const baseDeletes = (draftId: number): string[] => {
    const draft = drafts.find((d) => d.id === draftId);
    const raw = draft?.raw_payload || {};
    const saved = Array.isArray(raw.sku_name_deletes) ? raw.sku_name_deletes : [];
    const edit = edits[draftId];
    const unsaved = edit?.skuDeletes ?? [];
    return Array.from(new Set([...saved, ...unsaved]));
  };

  // 批量管理 SKU：保存一个草稿的完整删除集合（后端为全量替换语义）。
  // 只发送 sku 相关字段，草稿的其他未保存编辑（标题等）保留在原编辑态；
  // 保存成功后本地同步 raw_payload（移除已删除变种 + 记录已保存删除集合），
  // 并从编辑态移除已落库的删除集合，避免之后「保存已选」以旧删除集合二次 PATCH。
  const saveSkuDeletes = async (draftId: number, deletes: string[]) => {
    const draft = drafts.find((d) => d.id === draftId);
    const body: Record<string, unknown> = { sku_name_deletes: deletes };
    if (draft) {
      const edit = edits[draftId];
      if (edit && Object.keys(edit.skuEdits).length > 0) {
        // 草稿有未保存的 SKU 改名：一并提交，防止后端把 sku_name_edits 簿记清空
        const raw = draft.raw_payload || {};
        const variants: DraftVariant[] = raw.source_variant_records || [];
        const skuEdits: Record<string, string> = {};
        for (const variant of variants) {
          const attributes = variant.attributes || {};
          const label = Object.values(attributes).join('/');
          const skuId = String(variant.sku_id || variant.source_sku_id || label);
          const value = edit.skuEdits[skuId] ?? edit.skuEdits[label];
          if (value !== undefined && value !== (variant.display_name || label)) skuEdits[label] = value;
        }
        if (Object.keys(skuEdits).length > 0) body.sku_name_edits = skuEdits;
      }
    }
    await ppRequest(ctx, `${API_BASE}/drafts/${draftId}`, { method: 'PATCH', body });
    // 本地同步：移除已删除变种 + 记录已保存删除集合，保证刷新前的抽屉/编辑态一致
    const del = new Set(deletes);
    setDrafts((prev) => prev.map((d) => {
      if (d.id !== draftId) return d;
      const raw = d.raw_payload || {};
      const variants = Array.isArray(raw.source_variant_records)
        ? raw.source_variant_records.filter((v: DraftVariant) => {
            const attrs = v.attributes || {};
            const label = Object.values(attrs).join('/');
            const vid = String(v.sku_id || v.source_sku_id || label);
            return !del.has(label) && !del.has(vid);
          })
        : raw.source_variant_records;
      return { ...d, raw_payload: { ...raw, source_variant_records: variants, sku_name_deletes: [...del] } };
    }));
    // 删除集合已落库并同步进 raw_payload，无需继续保留在未保存编辑态
    setEdits((prev) => {
      const cur = prev[draftId];
      if (!cur || cur.skuDeletes.length === 0) return prev;
      return { ...prev, [draftId]: { ...cur, skuDeletes: [] } };
    });
  };

  const openSkuBatch = () => {
    if (!selectedIds.size) { setError('请先勾选草稿'); return; }
    setError('');
    setSkuBatchOpen(true);
  };

  const saveRow = async (draft: DraftSummary) => {
    setLoading(true);
    try {
      const saved = await saveOneDraft(draft);
      if (saved) {
        applySavedDraft(saved);
        notify('已保存该草稿修改');
      } else {
        notify('该行没有需要保存的修改');
      }
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
      const updated: DraftSummary[] = [];
      for (const draft of targets) { const saved = await saveOneDraft(draft); if (saved) updated.push(saved); }
      // 原位合并更新，保持列表顺序不变
      if (updated.length) setDrafts((prev) => prev.map((d) => {
        const hit = updated.find((u) => u.id === d.id);
        return hit ? { ...d, title: hit.title, image_url: hit.image_url } : d;
      }));
      setEdits((prev) => { const next = { ...prev }; for (const d of targets) delete next[d.id]; return next; });
      notify(`已保存 ${targets.length} 条草稿修改`);
    } catch (err) { fail(err); } finally { setLoading(false); }
  };

  // 批量删除勾选草稿；单条删除时传入目标 id（targetIds），删除后同步从勾选集中移除
  const deleteSelected = async (targetIds?: Iterable<number>) => {
    const ids = targetIds ? Array.from(targetIds) : Array.from(selectedIds);
    if (!ids.length) return;
    setLoading(true);
    try {
      await ppRequest(ctx, `${API_BASE}/drafts/delete`, { body: { draft_ids: ids, delete_all: false } });
      if (targetIds) {
        setSelectedIds((prev) => {
          const next = new Set(prev);
          for (const id of ids) next.delete(id);
          return next;
        });
      } else {
        setSelectedIds(new Set());
      }
      await refresh();
      notify(`已移除 ${ids.length} 条草稿`);
    } catch (err) { fail(err); } finally { setLoading(false); }
  };

  const handleProcess = (preflightOnly = false) => {
    if (!selectedIds.size) { setError('请先勾选需要处理的草稿'); return; }
    const ids = Array.from(selectedIds);
    const processOptions: ProductProcessingOptions = {
      ...options,
      ...(preflightOnly ? { preflightOnly: true } as Partial<ProductProcessingOptions> : {}),
    };
    const opened = onStartProcessing?.(ids, processOptions);
    if (opened === false) return; // 任务面板未打开（如已达上限），草稿保持原样
    // 提交处理即让勾选草稿从池中消失：本地同步置 processing（后端同样置位），
    // 处理完成置 processed 保持隐藏，失败回退 draft 后会自动重新出现。
    setDrafts((prev) => prev.map((d) => (ids.includes(d.id) ? { ...d, status: 'processing' as const } : d)));
    setSelectedIds(new Set());
    setEdits((prev) => { const next = { ...prev }; for (const id of ids) delete next[id]; return next; });
  };

  return (
    <div className="verify-page">
      <header className="verify-commandbar">
        <div className="verify-command-title">
          <span className="verify-eyebrow">PRODUCT PROCESSING</span>
          <h1>产品处理草稿池</h1>
          <p>采集先入池，默认只处理勾选草稿；暂停、确认与继续均以本地持久状态为准。</p>
        </div>
        <div className="verify-command-stats">
          <span><i className="iconfont icon-appstore" aria-hidden="true" /><strong>{selectableDrafts.length}</strong><em>待处理</em></span>
          <span><i className="iconfont icon-check-circle" aria-hidden="true" /><strong>{selectedIds.size}</strong><em>已勾选</em></span>
          <span><i className="iconfont icon-save" aria-hidden="true" /><strong>{dirtyCount}</strong><em>未保存</em></span>
        </div>
      </header>

      {(message || error) && (
        <div className={`verify-message ${error ? 'error' : ''}`}>{error || message}</div>
      )}

      <section className="verify-section" ref={draftListRef}>
        <div className="verify-sticky-toolbar-spacer" ref={stickySpacerRef}>
        <div className="verify-sticky-toolbar" ref={stickyToolbarRef}>
        <div className="verify-section-head">
          <h2><i className="iconfont icon-database" aria-hidden="true" />草稿池</h2>
          <div className="verify-actions">
            <button onClick={selectAll}><i className="iconfont icon-select" aria-hidden="true" />全选本页</button>
            <button onClick={clearSelection}><i className="iconfont icon-close-circle" aria-hidden="true" />取消选择</button>
            <button onClick={openSkuBatch} disabled={!selectedIds.size}><i className="iconfont icon-barcode" aria-hidden="true" />批量管理 SKU</button>
            <button onClick={() => saveDrafts(true)} disabled={loading}><i className="iconfont icon-save" aria-hidden="true" />保存已选</button>
            <button className="primary" onClick={() => handleProcess(false)} disabled={loading || !selectedIds.size}><i className="iconfont icon-rocket" aria-hidden="true" />开始处理</button>
            <button onClick={() => deleteSelected()} disabled={!selectedIds.size}><i className="iconfont icon-delete" aria-hidden="true" />删除选择</button>
            <button onClick={() => refresh().catch(fail)} disabled={loading}><i className="iconfont icon-sync" aria-hidden="true" />刷新</button>
          </div>
        </div>

        <div className="verify-pool-toolbar">
          <div className="verify-pool-stats">
            <span><i className="iconfont icon-appstore" aria-hidden="true" />待处理 <strong>{selectableDrafts.length}</strong></span>
            <span><i className="iconfont icon-check-circle" aria-hidden="true" />已选 <strong>{selectedIds.size}</strong></span>
            <span><i className="iconfont icon-file-text" aria-hidden="true" />本页 <strong>{pageDrafts.length}</strong></span>
            {(skuCountFilter > 1 || hideSingleSpec || viewMode === 'selected') && (
              <span><i className="iconfont icon-filter" aria-hidden="true" />筛选后 <strong>{totalDrafts}</strong></span>
            )}
            {dirtyCount > 0 && <span className="dirty"><i className="iconfont icon-save" aria-hidden="true" />未保存修改 <strong>{dirtyCount}</strong></span>}
          </div>
          <div className="verify-pool-controls">
            <div className="verify-page-size">
              <i className="iconfont icon-appstore" aria-hidden="true" />
              <select
                className="verify-pool-select"
                value={pageSize}
                title="单页展示数量"
                onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }}
              >
                <option value={10}>每页 10</option>
                <option value={30}>每页 30</option>
                <option value={50}>每页 50</option>
                <option value={100}>每页 100</option>
              </select>
            </div>
            <div className="verify-sku-filter">
              <i className="iconfont icon-filter" aria-hidden="true" />
              <select
                className="verify-pool-select"
                value={skuCountFilter}
                title="按 SKU（变种）数量筛选"
                onChange={(e) => { setSkuCountFilter(Number(e.target.value)); setPage(1); }}
              >
                <option value={0}>SKU 数量：全部</option>
                <option value={2}>≥ 2 个</option>
                <option value={3}>≥ 3 个</option>
                <option value={5}>≥ 5 个</option>
                <option value={10}>≥ 10 个</option>
              </select>
              <label className="verify-hide-single" title="隐藏无变种 / 仅 1 个变种的单规格草稿">
                <input
                  type="checkbox"
                  checked={hideSingleSpec}
                  onChange={(e) => { setHideSingleSpec(e.target.checked); setPage(1); }}
                />
                不看单规格
              </label>
            </div>
            <div className="verify-view-toggle" role="group" aria-label="草稿视图">
              <button
                type="button"
                className={viewMode === 'all' ? 'is-active' : ''}
                onClick={() => { setViewMode('all'); setPage(1); }}
              ><i className="iconfont icon-appstore" aria-hidden="true" />全部草稿</button>
              <button
                type="button"
                className={viewMode === 'selected' ? 'is-active' : ''}
                onClick={() => { setViewMode('selected'); setPage(1); }}
              ><i className="iconfont icon-check-circle" aria-hidden="true" />只看已选{selectedIds.size > 0 ? `（${selectedIds.size}）` : ''}</button>
            </div>
            <div className="verify-pool-search-wrap">
              <i className="iconfont icon-search" aria-hidden="true" />
              <input
                type="search"
                className="verify-pool-search"
                placeholder="搜索标题 / SKC / SKU / 来源..."
                value={searchTerm}
                onChange={(e) => { setSearchTerm(e.target.value); setPage(1); }}
              />
            </div>
          </div>
        </div>
        </div>
        </div>

        {viewMode === 'selected' && (
          <div className="verify-selected-banner">
            <span>正在核对已勾选草稿，共 <strong>{totalDrafts}</strong> 条（未勾选已过滤）</span>
            <div className="verify-selected-banner-acts">
              <button type="button" onClick={() => { setViewMode('all'); setPage(1); }}>查看全部</button>
              <button type="button" onClick={clearSelection}>清空已选</button>
            </div>
          </div>
        )}

        {totalDrafts === 0 && (
          <p className="verify-empty">
            {viewMode === 'selected'
              ? '还没有勾选草稿。先勾选目标草稿，再切换「只看已选」即可核对全部所选链接。'
              : '正在拉取新数据中....'}
          </p>
        )}
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
                      {isSelected && <span className="pool-selected-tag"><i className="iconfont icon-check" aria-hidden="true" />已选</span>}
                      <div className="pool-inline-acts">
                        <button className="btn-mini" onClick={() => copy(displayTitle)}><i className="iconfont icon-file-copy" aria-hidden="true" />复制</button>
                        <button className="btn-mini" onClick={() => beginEdit(draft)}><i className="iconfont icon-edit" aria-hidden="true" />{isExpanded ? '收起' : '编辑'}</button>
                        <button className="btn-mini danger" onClick={() => { if (window.confirm('确认删除该草稿？')) deleteSelected([draft.id]); }}><i className="iconfont icon-delete" aria-hidden="true" />删除</button>
                      </div>
                    </div>
                    <div className="pool-meta">
                      <span className="tag">{platform}</span>
                      <span className="pool-source-link">
                        <span className="pool-link-label"><i className="iconfont icon-link" aria-hidden="true" />链接</span>
                        {sourceUrl ? (
                          <a href={sourceUrl} target="_blank" rel="noreferrer" title={sourceUrl}>{draft.source_ref || sourceUrl}</a>
                        ) : (
                          <span className="muted">{draft.source_ref || '-'}</span>
                        )}
                        {sourceUrl && <button className="btn-mini" onClick={() => copy(sourceUrl)}><i className="iconfont icon-link" aria-hidden="true" />复制链接</button>}
                      </span>
                    </div>
                    <div className="pool-sku-info">
                      <span className="pool-label"><i className="iconfont icon-barcode" aria-hidden="true" />SKU</span>
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
                        {variants.length > 1 && (
                          <button className="btn-mini" onClick={() => { if (!edits[draft.id]) beginEdit(draft); setSkuDrawerDraftId(draft.id); }}><i className="iconfont icon-eye" aria-hidden="true" />管理 SKU</button>
                        )}
                      </div>
                    </div>
                    <div className="pool-cat-info">
                      <span className="pool-label"><i className="iconfont icon-tags" aria-hidden="true" />类目</span>
                      <span className="pool-value">{categoryPath || '（参考）'}</span>
                      {categoryPath && <button className="btn-mini" onClick={() => copy(categoryPath)}><i className="iconfont icon-file-copy" aria-hidden="true" />复制</button>}
                    </div>
                    <div className="pool-extra">
                      {draft.cost != null && <span>成本 ¥{draft.cost}</span>}
                      {draft.declared_price != null && <span>申报 ¥{draft.declared_price}</span>}
                      {variants.length > 0 && <span>{variants.length} 个变种</span>}
                      {Array.isArray(raw.source_image_urls) && raw.source_image_urls.length > 0 && (<span>{raw.source_image_urls.length} 张来源图</span>)}
                      {draft.status === 'attention_required' && <span className="badge attn">需确认</span>}
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
                      <div className="verify-sku-summary">
                        <div className="verify-sku-summary-info">
                          <span><i className="iconfont icon-barcode" aria-hidden="true" /><strong>{variants.length}</strong> 个 SKU</span>
                          {edit.skuDeletes.length > 0 && <span className="dirty">已删除 <strong>{edit.skuDeletes.length}</strong></span>}
                          <span className="muted">点击「管理 SKU」在右侧弹窗中查看全部规格并批量删除</span>
                        </div>
                        <button className="btn-mini primary" onClick={() => setSkuDrawerDraftId(draft.id)}><i className="iconfont icon-eye" aria-hidden="true" />管理 SKU</button>
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
        {/* 草稿池底部历史采集入口：直接在本界面唤出历史任务卡片 */}
        <div className="verify-pool-history-entry">
          <button type="button" onClick={openHistory} disabled={historyLoading}>
            <i className="iconfont icon-clock" aria-hidden="true" />
            <span>
              <strong>历史采集任务</strong>
              <small>查看最近的产品处理任务记录与输出文件</small>
            </span>
            <em aria-hidden="true">›</em>
          </button>
        </div>
      </section>

      <section className="verify-quickbar">
        <div className="verify-actions">
          <button className="primary" onClick={() => handleProcess(false)} disabled={loading || !selectedIds.size}><i className="iconfont icon-rocket" aria-hidden="true" />开始处理</button>
          <button onClick={() => saveDrafts(true)} disabled={loading}><i className="iconfont icon-save" aria-hidden="true" />保存已选</button>
          <button onClick={openSkuBatch} disabled={!selectedIds.size}><i className="iconfont icon-barcode" aria-hidden="true" />批量管理 SKU</button>
          <button onClick={() => deleteSelected()} disabled={!selectedIds.size}><i className="iconfont icon-delete" aria-hidden="true" />删除选择</button>
          <button className="history-collection-trigger" onClick={openHistory}><i className="iconfont icon-clock" aria-hidden="true" />历史采集</button>
        </div>
      </section>

      {skuDrawerDraftId !== null && (() => {
        const target = drafts.find((d) => d.id === skuDrawerDraftId);
        if (!target) return null;
        const raw = target.raw_payload || {};
        const variants: DraftVariant[] = raw.source_variant_records || [];
        const edit = edits[target.id] ?? { title: target.title || '', imageUrl: target.image_url || '', skuEdits: {}, skuDeletes: [] };
        const isDeleted = (variant: DraftVariant, skuId: string, label: string) =>
          edit.skuDeletes.includes(skuId) || edit.skuDeletes.includes(label);
        // 未删除保持原序在前，已删除自动置队尾，便于连续批量删除
        const orderedVariants = [...variants].sort((a, b) => {
          const attrsA = a.attributes || {};
          const labelA = Object.values(attrsA).join('/');
          const skuIdA = String(a.sku_id || a.source_sku_id || labelA);
          const attrsB = b.attributes || {};
          const labelB = Object.values(attrsB).join('/');
          const skuIdB = String(b.sku_id || b.source_sku_id || labelB);
          return (isDeleted(a, skuIdA, labelA) ? 1 : 0) - (isDeleted(b, skuIdB, labelB) ? 1 : 0);
        });
        const deletedCount = variants.filter((v, idx) => {
          const attrs = v.attributes || {};
          const label = Object.values(attrs).join('/');
          const skuId = String(v.sku_id || v.source_sku_id || label || idx);
          return isDeleted(v, skuId, label);
        }).length;
        const closeDrawer = () => setSkuDrawerDraftId(null);
        return (
          <div className="verify-drawer-root">
            <div className="verify-drawer-mask" onClick={closeDrawer} />
            <aside className="verify-drawer">
              <header className="verify-drawer-head">
                <div>
                  <p className="verify-eyebrow">SKU EDITOR</p>
                  <h2 title={target.title || raw.source_title || '未命名商品'}>
                    {target.title || raw.source_title || '未命名商品'}
                  </h2>
                  <p>共 {variants.length} 个 SKU · 已删除 <strong>{deletedCount}</strong> 个</p>
                </div>
                <button className="verify-drawer-close" onClick={closeDrawer} aria-label="关闭">×</button>
              </header>

              <div className="verify-drawer-body">
                <div className="verify-drawer-tip">
                  <i className="iconfont icon-infomation" aria-hidden="true" />
                  已删除的 SKU 会自动移动到列表末尾，便于连续批量删除；恢复后回到原位置。
                </div>
                {variants.length === 0 && <p className="verify-drawer-status">该草稿没有 SKU（单规格）。</p>}
                {orderedVariants.map((variant, idx) => {
                  const attrs = variant.attributes || {};
                  const label = Object.values(attrs).join('/');
                  const skuId = String(variant.sku_id || variant.source_sku_id || label || idx);
                  const deleted = isDeleted(variant, skuId, label);
                  const value = edit.skuEdits[skuId] ?? edit.skuEdits[label] ?? variant.display_name ?? label;
                  const toggleDelete = () => setEdits((p) => {
                    const cur = p[target.id] ?? { title: target.title || '', imageUrl: target.image_url || '', skuEdits: {}, skuDeletes: [] };
                    const s = new Set(cur.skuDeletes);
                    if (s.has(skuId)) s.delete(skuId);
                    else if (s.has(label)) s.delete(label);
                    else s.add(String(variant.sku_id || variant.source_sku_id || label || idx));
                    return { ...p, [target.id]: { ...cur, skuDeletes: Array.from(s) } };
                  });
                  const changeValue = (next: string) => setEdits((p) => {
                    const cur = p[target.id] ?? { title: target.title || '', imageUrl: target.image_url || '', skuEdits: {}, skuDeletes: [] };
                    const key = variant.sku_id || variant.source_sku_id || label || idx;
                    return { ...p, [target.id]: { ...cur, skuEdits: { ...cur.skuEdits, [key]: next } } };
                  });
                  return (
                    <section key={`${skuId}-${idx}`} className={`verify-drawer-sku ${deleted ? 'deleted' : ''}`}>
                      <header>
                        <div>
                          <strong>SKU {idx + 1}</strong>
                          <small>{variant.source_sku_id ?? '无货号'}</small>
                          {deleted && <em>已删除 · 已移至队尾</em>}
                        </div>
                        <button onClick={toggleDelete}>{deleted ? '恢复' : '删除'}</button>
                      </header>
                      {!deleted ? (
                        <div className="verify-variant-grid">
                          <label><span>{label || '规格属性'}</span>
                            <input value={value} onChange={(e) => changeValue(e.target.value)} />
                          </label>
                          <small>¥{variant.price_cny ?? '-'} · 起订 {variant.min_order_quantity ?? '-'}</small>
                        </div>
                      ) : (<p className="verify-sku-note">该 SKU 不会进入下次处理。</p>)}
                    </section>
                  );
                })}
              </div>

              <footer className="verify-drawer-foot">
                <span>已删除 {deletedCount} / {variants.length} 个 SKU</span>
                <button className="primary" onClick={() => { void saveRow(target); closeDrawer(); }} disabled={loading}>保存</button>
              </footer>
            </aside>
          </div>
        );
      })()}

      {skuBatchOpen && selectedDrafts.length > 0 && (
        <SkuBatchManager
          drafts={selectedDrafts}
          baseDeletes={baseDeletes}
          onSaveDeletes={saveSkuDeletes}
          onBatchSaved={() => { void refresh(); }}
          onClose={() => setSkuBatchOpen(false)}
        />
      )}

      {historyOpen && (
        <div className={`verify-history-layer${historyClosing ? ' is-closing' : ''}`}>
          <div className="verify-history-mask" onClick={() => closeHistory()} />
          <section className="verify-history-card" role="dialog" aria-modal="true" aria-label="历史采集任务">
            <header className="verify-history-head">
              <div>
                <p className="verify-eyebrow">COLLECTION HISTORY</p>
                <h2>历史采集</h2>
                <p>最近的产品处理任务记录，点击「打开任务页」可查看完整结果与下载输出文件。</p>
              </div>
              <div className="verify-history-head-acts">
                <button className="btn-mini" onClick={loadHistory} disabled={historyLoading}><i className="iconfont icon-sync" aria-hidden="true" />刷新</button>
                <button className="verify-drawer-close" onClick={() => closeHistory()} aria-label="关闭">×</button>
              </div>
            </header>
            {historyError && <div className="verify-message error">{historyError}</div>}
            <div className="verify-history-body">
              {historyLoading && <p className="verify-empty">正在读取历史任务…</p>}
              {!historyLoading && historyTasks.length === 0 && (
                <p className="verify-empty">暂无历史采集任务。勾选草稿并点击「开始处理」后，任务记录会显示在这里。</p>
              )}
              {!historyLoading && historyTasks.length > 0 && (
                <ul className="verify-history">
                  {historyTasks.map((task) => (
                    <li key={task.task_id} className="history-card-item">
                      <span className="verify-history-title" title={task.title}>{task.title}</span>
                      <span className={`verify-badge status-${task.status}`}>
                        {({
                          queued: '等待处理',
                          running: '处理中',
                          paused: '已暂停',
                          completed: '已完成',
                          completed_with_review: '完成·待确认',
                          failed: '任务失败',
                          partial_failure: '部分失败',
                        } as Record<string, string>)[task.status] || task.status}
                      </span>
                      <span className="verify-history-counts">
                        共 <b>{task.total_count}</b> · 成功 <b className="ok">{task.success_count}</b> · 失败 <b className="bad">{task.failed_count}</b>
                        {task.skipped_count > 0 && <> · 跳过 <b>{task.skipped_count}</b></>}
                      </span>
                      {task.target_site && <span className="verify-history-site">{task.target_site}{task.target_language_label ? ` · ${task.target_language_label}` : ''}</span>}
                      {task.elapsed_seconds !== undefined && <span className="verify-history-elapsed">耗时 {formatHistoryDuration(task.elapsed_seconds)}</span>}
                      <span className="verify-history-date">{new Date(task.created_at).toLocaleString('zh-CN')}</span>
                      <button className="btn-mini" onClick={() => closeHistory(onOpenHistoryTasks)}>打开任务页</button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            {!historyLoading && historyTasks.length > 0 && (
              <footer className="verify-history-foot">
                <span>仅展示最近 6 次，完整列表见任务页。</span>
                <button className="primary" onClick={() => closeHistory(onOpenHistoryTasks)}>打开完整任务页</button>
              </footer>
            )}
          </section>
        </div>
      )}
    </div>
  );
}

function formatHistoryDuration(seconds?: number): string {
  const total = Math.max(0, Math.floor(seconds || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours > 0) return `${hours}小时${minutes}分`;
  if (minutes > 0) return `${minutes}分${secs}秒`;
  return `${secs}秒`;
}

export default ProductProcessingVerifyPage;
