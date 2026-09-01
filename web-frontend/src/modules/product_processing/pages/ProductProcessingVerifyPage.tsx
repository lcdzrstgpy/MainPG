import { useEffect, useMemo, useRef, useState } from 'react';
import { useChangePoller } from '../../../shared/hooks/useChangePoller';
import { SkuBatchManager } from '../components/SkuBatchManager';
import { ppRequest, type ApiContext } from '../api/client';
import { productProcessingApiContext } from '../api/context';
import { addDraftComboSource } from '../api/comboApi';
import { variantPresentation } from '../data/skuPresentation';
import type {
  DraftSummary,
  DraftVariant,
  ProductProcessingOptions,
} from '../types';
import '../styles/ProductProcessingVerifyPage.css';

const API_BASE = '/api/product-processing';

type DraftEdit = {
  title: string;
  imageUrl: string;
  skuEdits: Record<string, string>;
  skuDeletes: string[];
};

type DeletedDraftBatch = {
  ids: number[];
  selectedIds: number[];
  premiumIds: number[];
};

type Props = {
  onStartProcessing?: (draftIds: number[], options: ProductProcessingOptions, premiumDraftIds: number[]) => boolean;
  isActive?: boolean;
};

function api(): ApiContext {
  return productProcessingApiContext();
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

export function ProductProcessingVerifyPage({ onStartProcessing, isActive = true }: Props) {
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
    autoRepull: false,
  });
  const [drafts, setDrafts] = useState<DraftSummary[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  // 精品模式：勾选后该草稿走一次 4K 智能生图，本地拆成四张高清独立图。
  const [premiumIds, setPremiumIds] = useState<Set<number>>(new Set());
  const [expandedId, setExpandedId] = useState<number | null>(null);
  // SKU 管理抽屉：当前正在管理的草稿 id（null 表示关闭）
  const [skuDrawerDraftId, setSkuDrawerDraftId] = useState<number | null>(null);
  // SKU 管理抽屉：按序号范围批量删除（从第几个到第几个结束）
  const [skuRangeStart, setSkuRangeStart] = useState('');
  const [skuRangeEnd, setSkuRangeEnd] = useState('');
  const [skuRangeTip, setSkuRangeTip] = useState('');
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
  // SKU 数量筛选：0 = 全部，否则为最大变种数（少于 N）
  const [skuCountFilter, setSkuCountFilter] = useState(0);
  // 不看单规格：隐藏无变种 / 仅 1 个变种的草稿
  const [hideSingleSpec, setHideSingleSpec] = useState(false);
  const [isStickyToolbar, setIsStickyToolbar] = useState(false);
  const [deletedBatch, setDeletedBatch] = useState<DeletedDraftBatch | null>(null);
  const draftListRef = useRef<HTMLDivElement>(null);
  const stickyToolbarRef = useRef<HTMLDivElement>(null);
  const stickySpacerRef = useRef<HTMLDivElement>(null);
  const stickyToolbarStateRef = useRef(false);

  useEffect(() => {
    if (isActive) return;
    setSkuDrawerDraftId(null);
    setSkuBatchOpen(false);
  }, [isActive]);

  // 草稿池工具栏按窗口滚动条件吸顶。只在吸顶状态变化时切换布局，避免工具栏尺寸变化
  // 再触发自身的 ResizeObserver，造成 fixed 与普通流之间反复跳变。
  useEffect(() => {
    const toolbar = stickyToolbarRef.current;
    const spacer = stickySpacerRef.current;
    if (!toolbar || !spacer) return;
    const contentCard = document.querySelector('.content-card') as HTMLElement | null;
    const topbar = document.querySelector('.topbar-card') as HTMLElement | null;
    let frame = 0;

    const clearStickyLayout = () => {
      toolbar.style.position = '';
      toolbar.style.top = '';
      toolbar.style.left = '';
      toolbar.style.width = '';
      spacer.style.height = '';
      stickyToolbarStateRef.current = false;
      setIsStickyToolbar(false);
    };

    if (!isActive) {
      clearStickyLayout();
      return;
    }

    const applyStickyLayout = (topbarBottom: number) => {
      const contentRect = contentCard?.getBoundingClientRect();
      const nextTop = `${topbarBottom + 6}px`;
      const nextLeft = contentRect ? `${Math.round(contentRect.left)}px` : '0px';
      const nextWidth = contentRect ? `${Math.round(contentRect.width)}px` : '100%';
      if (toolbar.style.position !== 'fixed') {
        // 先按普通流下的真实高度给 spacer 占位，再切 fixed：
        // 若先改 fixed 再读 offsetHeight，宽度变化会触发 flex-wrap 重排，读到错误高度，
        // spacer 占位跳变导致页面整体高度抖动、滚动上下抽搐。
        spacer.style.height = `${toolbar.offsetHeight}px`;
        toolbar.style.position = 'fixed';
      }
      if (toolbar.style.top !== nextTop) toolbar.style.top = nextTop;
      if (toolbar.style.left !== nextLeft) toolbar.style.left = nextLeft;
      if (toolbar.style.width !== nextWidth) toolbar.style.width = nextWidth;
    };

    const apply = () => {
      let topbarBottom = 0;
      if (topbar) {
        const rect = topbar.getBoundingClientRect();
        if (rect.bottom > 0) topbarBottom = Math.round(rect.bottom);
      }
      const needStick = spacer.getBoundingClientRect().top <= topbarBottom + 6;
      if (needStick) {
        applyStickyLayout(topbarBottom);
        if (!stickyToolbarStateRef.current) {
          stickyToolbarStateRef.current = true;
          setIsStickyToolbar(true);
        }
      } else if (stickyToolbarStateRef.current) {
        clearStickyLayout();
      }
    };

    const scheduleApply = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        apply();
      });
    };

    apply();
    window.addEventListener('scroll', scheduleApply, { passive: true });
    window.addEventListener('resize', scheduleApply);
    const observer = topbar ? new ResizeObserver(scheduleApply) : null;
    if (topbar) observer?.observe(topbar);
    // 吸顶期间工具栏自身高度变化时同步 spacer 占位高度，避免占位与 fixed 高度不一致造成跳动。
    const toolbarObserver = new ResizeObserver(() => {
      if (!stickyToolbarStateRef.current) return;
      const rect = toolbar.getBoundingClientRect();
      const nextHeight = `${Math.round(rect.height)}px`;
      if (spacer.style.height !== nextHeight) spacer.style.height = nextHeight;
    });
    toolbarObserver.observe(toolbar);
    return () => {
      if (frame) window.cancelAnimationFrame(frame);
      window.removeEventListener('scroll', scheduleApply);
      window.removeEventListener('resize', scheduleApply);
      observer?.disconnect();
      toolbarObserver.disconnect();
      clearStickyLayout();
    };
  }, [isActive]);

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
      // SKU 数量筛选：少于 N 个变种
      if (skuCountFilter > 1 && variantCount >= skuCountFilter) return false;
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

  // 加入组合定制：把该条草稿的图片存入「商品自定义组合」来源图暂存区（服务端持久化）
  const addToCombo = async (draft: DraftSummary) => {
    try {
      const rawTitle = String(draft.raw_payload?.source_title || '').trim();
      await addDraftComboSource(ctx, draft.id, draft.title || rawTitle || `草稿 #${draft.id}`);
      notify(`已加入「商品自定义组合」来源图暂存区，可在组合页统一管理`);
    } catch (err) { fail(err); }
  };

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

  // 精品模式勾选：可独立于「选中处理」勾选，两者互不影响；取消选中处理不清除精品标记
  const togglePremium = (id: number) => {
    setPremiumIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) { next.delete(id); }
      else { next.add(id); }
      return next;
    });
  };

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
      const result = await ppRequest<{ deleted_count: number; ids: number[] }>(ctx, `${API_BASE}/drafts/delete`, {
        body: { draft_ids: ids, delete_all: false },
      });
      const deletedIds = result.ids || [];
      if (!deletedIds.length) {
        notify('所选草稿已经不在草稿池中');
        return;
      }
      const deletedSet = new Set(deletedIds);
      setDeletedBatch({
        ids: deletedIds,
        selectedIds: Array.from(selectedIds).filter((id) => deletedSet.has(id)),
        premiumIds: Array.from(premiumIds).filter((id) => deletedSet.has(id)),
      });
      if (targetIds) {
        setSelectedIds((prev) => {
          const next = new Set(prev);
          for (const id of deletedIds) next.delete(id);
          return next;
        });
        setPremiumIds((prev) => {
          const next = new Set(prev);
          for (const id of deletedIds) next.delete(id);
          return next;
        });
      } else {
        setSelectedIds(new Set());
        setPremiumIds((prev) => {
          const next = new Set(prev);
          for (const id of deletedIds) next.delete(id);
          return next;
        });
      }
      await refresh();
      notify(`已移除 ${deletedIds.length} 条草稿，可点击“撤回删除”恢复`);
    } catch (err) { fail(err); } finally { setLoading(false); }
  };

  const undoDelete = async () => {
    if (!deletedBatch?.ids.length) return;
    const batch = deletedBatch;
    setLoading(true);
    try {
      const result = await ppRequest<{ restored_count: number; ids: number[] }>(ctx, `${API_BASE}/drafts/restore`, {
        body: { draft_ids: batch.ids },
      });
      const restoredIds = result.ids || [];
      const restoredSet = new Set(restoredIds);
      setDeletedBatch(null);
      setSelectedIds((prev) => {
        const next = new Set(prev);
        for (const id of batch.selectedIds) {
          if (restoredSet.has(id) && next.size < 100) next.add(id);
        }
        return next;
      });
      setPremiumIds((prev) => {
        const next = new Set(prev);
        for (const id of batch.premiumIds) {
          if (restoredSet.has(id)) next.add(id);
        }
        return next;
      });
      await refresh();
      notify(`已撤回删除，恢复 ${restoredIds.length} 条草稿`);
    } catch (err) { fail(err); } finally { setLoading(false); }
  };

  const handleProcess = async (preflightOnly = false) => {
    if (!selectedIds.size) { setError('请先勾选需要处理的草稿'); return; }
    const ids = Array.from(selectedIds);
    const dirtyTargets = drafts.filter((draft) => selectedIds.has(draft.id) && draftDirty(draft, edits));
    if (dirtyTargets.length) {
      setLoading(true);
      setMessage('');
      setError('');
      try {
        const updated: DraftSummary[] = [];
        for (const draft of dirtyTargets) {
          const saved = await saveOneDraft(draft);
          if (saved) updated.push(saved);
        }
        if (updated.length) {
          setDrafts((prev) => prev.map((draft) => updated.find((item) => item.id === draft.id) || draft));
          setEdits((prev) => {
            const next = { ...prev };
            for (const draft of dirtyTargets) delete next[draft.id];
            return next;
          });
        }
      } catch (err) {
        fail(err);
        setLoading(false);
        return;
      }
      setLoading(false);
    }
    const processOptions: ProductProcessingOptions = {
      ...options,
      ...(preflightOnly ? { preflightOnly: true } as Partial<ProductProcessingOptions> : {}),
    };
    // 精品标记只对本次选中的草稿生效（被取消选中的精品标记暂留，方便下次一并处理）
    const premiumIdsInSelection = Array.from(premiumIds).filter((id) => ids.includes(id));
    const opened = onStartProcessing?.(ids, processOptions, premiumIdsInSelection);
    if (opened === false) return; // 任务面板未打开（如已达上限），草稿保持原样
    // 提交处理即让勾选草稿从池中消失：本地同步置 processing（后端同样置位），
    // 处理完成置 processed 保持隐藏，失败回退 draft 后会自动重新出现。
    setDrafts((prev) => prev.map((d) => (ids.includes(d.id) ? { ...d, status: 'processing' as const } : d)));
    setSelectedIds(new Set());
    setPremiumIds((prev) => {
      const next = new Set(prev);
      for (const id of premiumIdsInSelection) next.delete(id);
      return next;
    });
    setEdits((prev) => { const next = { ...prev }; for (const id of ids) delete next[id]; return next; });
  };

  return (
    <div className="verify-page">
      <header className="verify-commandbar">
        <div className="verify-command-title">
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
        <div className={`verify-sticky-toolbar ${isStickyToolbar ? 'is-stuck' : ''}`} ref={stickyToolbarRef}>
        <div className="verify-section-head">
          <h2><i className="iconfont icon-database" aria-hidden="true" />草稿池</h2>
          <div className="verify-actions">
            <button onClick={selectAll}><i className="iconfont icon-select" aria-hidden="true" />全选本页</button>
            <button onClick={clearSelection}><i className="iconfont icon-close-circle" aria-hidden="true" />取消选择</button>
            <button onClick={openSkuBatch} disabled={!selectedIds.size}><i className="iconfont icon-barcode" aria-hidden="true" />批量管理 SKU</button>
            <button onClick={() => saveDrafts(true)} disabled={loading}><i className="iconfont icon-save" aria-hidden="true" />保存已选</button>
            <button className="primary" onClick={() => handleProcess(false)} disabled={loading || !selectedIds.size}><i className="iconfont icon-rocket" aria-hidden="true" />开始处理</button>
            <button onClick={() => deleteSelected()} disabled={loading || !selectedIds.size}><i className="iconfont icon-delete" aria-hidden="true" />删除选择</button>
            <button className="undo-delete" onClick={undoDelete} disabled={loading || !deletedBatch} title={deletedBatch ? `恢复最近删除的 ${deletedBatch.ids.length} 条草稿` : '暂无可撤回的删除'}><span aria-hidden="true">↶</span>撤回删除{deletedBatch ? `（${deletedBatch.ids.length}）` : ''}</button>
            <button onClick={() => refresh().catch(fail)} disabled={loading}><i className="iconfont icon-sync" aria-hidden="true" />刷新</button>
          </div>
        </div>

        <div className="verify-pool-toolbar">
          <div className="verify-pool-stats">
            <span><i className="iconfont icon-appstore" aria-hidden="true" />待处理 <strong>{selectableDrafts.length}</strong></span>
            <span><i className="iconfont icon-check-circle" aria-hidden="true" />已选 <strong>{selectedIds.size}</strong></span>
            <span><i className="iconfont icon-gem" aria-hidden="true" />精品 <strong>{premiumIds.size}</strong></span>
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
                <option value={2}>小于 2 个</option>
                <option value={3}>小于 3 个</option>
                <option value={5}>小于 5 个</option>
                <option value={10}>小于 10 个</option>
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
            const isPremium = premiumIds.has(draft.id);
            return (
              <article key={draft.id} className={`pool-card ${isSelected ? 'selected' : ''} ${isPremium ? 'premium' : ''}`}>
                <div className="pool-card-body">
                  <label className="pool-check">
                    <input type="checkbox" checked={isSelected} onChange={onSelect} />
                  </label>
                  {/* 精品模式：独立于「选中处理」的勾选入口，走一次 4K 智能生图并本地拆成四张高清图。 */}
                  <div className="pool-premium">
                    <button
                      type="button"
                      className={`premium-toggle ${isPremium ? 'active' : ''}`}
                      onClick={() => togglePremium(draft.id)}
                      title={isPremium ? '取消精品处理' : '精品处理:四张高清独立图'}
                    ><i className="iconfont icon-gem" aria-hidden="true" />{isPremium ? '已选精品' : '精品'}</button>
                  </div>
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
                      {isPremium && <span className="pool-premium-tag"><i className="iconfont icon-gem" aria-hidden="true" />精品</span>}
                      <div className="pool-inline-acts">
                        <button className="btn-mini" onClick={() => copy(displayTitle)}><i className="iconfont icon-file-copy" aria-hidden="true" />复制</button>
                        <button className="btn-mini" onClick={() => beginEdit(draft)} title="修改后续 AI 处理优先参考的中文标题"><i className="iconfont icon-edit" aria-hidden="true" />{isExpanded ? '收起' : '编辑标题'}</button>
                        <button className="btn-mini primary" onClick={() => void addToCombo(draft)} title="把该条草稿图片加入「商品自定义组合」来源图暂存区"><i className="iconfont icon-skin" aria-hidden="true" />加入组合定制</button>
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
                      <label>中文参考标题
                        <input value={edit.title} placeholder="请输入准确的中文商品标题" onChange={(e) => setEdits((p) => ({ ...p, [draft.id]: { ...edit, title: e.target.value } }))} />
                        <small className="verify-title-reference-tip">后续 AI 识别、标题、详情与图片处理会优先参考此标题；开始处理时自动保存。</small>
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
        const totalOrdered = orderedVariants.length;
        const applyRangeDelete = () => {
          const start = Number(skuRangeStart);
          const end = Number(skuRangeEnd);
          if (!Number.isInteger(start) || !Number.isInteger(end) || start < 1 || end < start || end > totalOrdered) {
            setSkuRangeTip(`请输入有效范围（1-${totalOrdered}）`);
            return;
          }
          setSkuRangeStart('');
          setSkuRangeEnd('');
          setSkuRangeTip('');
          setEdits((prev) => {
            const cur = prev[target.id] ?? { title: target.title || '', imageUrl: target.image_url || '', skuEdits: {}, skuDeletes: [] };
            const s = new Set(cur.skuDeletes);
            orderedVariants.forEach((variant, idx) => {
              if (idx + 1 >= start && idx + 1 <= end) {
                const attrs = variant.attributes || {};
                const label = Object.values(attrs).join('/');
                const key = String(variant.sku_id || variant.source_sku_id || label || idx);
                s.add(key);
              }
            });
            return { ...prev, [target.id]: { ...cur, skuDeletes: Array.from(s) } };
          });
        };
        const closeDrawer = () => {
          setSkuDrawerDraftId(null);
          setSkuRangeStart('');
          setSkuRangeEnd('');
          setSkuRangeTip('');
        };
        return (
          <div className="verify-drawer-root">
            <div className="verify-drawer-mask" onClick={closeDrawer} />
            <aside className="verify-drawer">
              <header className="verify-drawer-head">
                <div>
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
                <div className="verify-drawer-range">
                  <span>批量删除</span>
                  <span>从第</span>
                  <input type="number" min="1" value={skuRangeStart} onChange={(e) => setSkuRangeStart(e.target.value)} placeholder="1" aria-label="起始序号" />
                  <span>个到第</span>
                  <input type="number" min="1" value={skuRangeEnd} onChange={(e) => setSkuRangeEnd(e.target.value)} placeholder={totalOrdered > 0 ? String(totalOrdered) : '1'} aria-label="结束序号" />
                  <span>个</span>
                  <button onClick={applyRangeDelete} disabled={totalOrdered === 0}>删除该范围</button>
                </div>
                {skuRangeTip && <p className="verify-drawer-range-tip">{skuRangeTip}</p>}
                {variants.length === 0 && <p className="verify-drawer-status">该草稿没有 SKU（单规格）。</p>}
                {orderedVariants.map((variant, idx) => {
                  const sourcePlatform = String(raw.platform || raw.source_platform || '').toLowerCase();
                  const legacyTemuCurrency = sourcePlatform === 'temu'
                    ? String(raw.currency || raw.price_currency || '')
                    : '';
                  const presentation = variantPresentation(variant, legacyTemuCurrency, target.image_url || raw.image_url || raw.main_image_url || raw.source_image_urls?.[0]);
                  const label = presentation.label;
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
                        <div className="verify-variant-layout">
                          {presentation.imageUrl ? (
                            <img
                              className="verify-sku-image"
                              src={presentation.imageUrl}
                              alt={`${label || `SKU ${idx + 1}`} 规格图`}
                              referrerPolicy="no-referrer"
                            />
                          ) : (
                            <div className="verify-sku-image-placeholder" aria-label="无规格图">无图</div>
                          )}
                          <div className="verify-variant-content">
                            <div className="verify-variant-attributes">
                              {presentation.attributes.map((attribute) => (
                                <span className="verify-variant-attribute" key={`${attribute.name}-${attribute.value}`}>
                                  <b>{attribute.name}</b>
                                  <em>{attribute.value}</em>
                                </span>
                              ))}
                            </div>
                            <div className="verify-variant-grid">
                              <label><span>组合名称</span>
                                <input value={value} onChange={(e) => changeValue(e.target.value)} />
                              </label>
                              <small>{presentation.priceLabel} · 起订 {variant.min_order_quantity ?? '-'}</small>
                            </div>
                          </div>
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

    </div>
  );
}

export default ProductProcessingVerifyPage;
