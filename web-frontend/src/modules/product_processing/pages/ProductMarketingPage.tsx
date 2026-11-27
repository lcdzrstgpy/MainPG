import { useEffect, useMemo, useState } from 'react';

import { ppRequest, type ApiContext } from '../api/client';
import { productProcessingApiContext } from '../api/context';
import type { DraftCollectionBatch } from '../api/productProcessingApi';
import './ProductMarketingPage.css';

/**
 * 精选处理（独立板块 · 独立界面）。
 *
 * 与 AI 处理的刻意差异（2026-11-23 用户要求「UI 全都不一样」）：
 * - 视觉：墨黑金主题、大画廊卡片，不用 AI 处理的 commandbar + 表格行；
 * - 结构：左「商品画廊」右「粘性设置面板」，一屏内完成选品和配置；
 * - 流程：本页直接提交精选任务，成功后跳到任务详情（进度/预检与 AI 处理共用）。
 *
 * 多方案（2026-11-26）：一批商品常常要用不同版式/画质出图，原来只能全选套一套
 * 配置、提交后重新挑。现在右栏维护方案 A/B/C，每个商品归属一个方案；提交时按
 * 方案逐个调用 /drafts/process（后端配置是批次级），每个方案各创建一个任务。
 *
 * 原图版式分析是内部能力：出图前自动拆解轮播/详情图的版式（只借结构，
 * 不复用原图像素、文案和 logo），不在界面上单独暴露。
 */

const API_BASE = '/api/product-processing';
const SCHEMES_STORAGE_KEY = 'pp.marketing.schemes.v1';
const SCHEME_KEYS = ['A', 'B', 'C'] as const;
const MAX_SCHEMES = SCHEME_KEYS.length;
const PAGE_SIZES = [24, 48, 96] as const;

type SchemeKey = (typeof SCHEME_KEYS)[number];
type SiteCode = 'US' | 'MX';
type ImageTier = 'standard' | 'highest';
type TemplateKey = 'infographic_hero' | 'infographic_details' | 'headline_hero' | 'feature_cards' | 'icon_grid' | 'callout_ring';

const TEMPLATES: Array<{ key: TemplateKey; label: string; hint: string }> = [
  { key: 'infographic_hero', label: '信息图·卖点场景', hint: '标题 + 3 条利益点 + 2×2 场景卡（对标 A+ 样图）' },
  { key: 'infographic_details', label: '信息图·细节标注', hint: '5 条细节清单 + 大图编号引线 + 尺寸标注' },
  { key: 'headline_hero', label: '大字卖点', hint: '顶部大标题 + 产品主视觉' },
  { key: 'feature_cards', label: '功能细节卡', hint: 'hero 大图 + 2×2 功能微距（细节块用真实裁切）' },
  { key: 'icon_grid', label: '功能图标网格', hint: '2×3 卖点短词卡' },
  { key: 'callout_ring', label: '编号标注', hint: '圆环细节 + 引线编号（对标样图）' },
];

const TEMPLATE_KEYS = new Set<string>(TEMPLATES.map((item) => item.key));
const DEFAULT_TEMPLATES: TemplateKey[] = ['infographic_hero', 'infographic_details'];

type StyleTemplate = 'auto' | 'bright' | 'dark' | 'white' | 'lifestyle';

/** 共通模板：不按品类写死亮暗；auto 默认明亮影棚，暗色只在显式选「暗色质感」时用。 */
const STYLE_TEMPLATES: Array<{ key: StyleTemplate; label: string; hint: string }> = [
  { key: 'auto', label: '自动（推荐）', hint: '默认明亮影棚，不出暗黑环境图' },
  { key: 'bright', label: '明亮影棚', hint: '暖白背景、柔光，工具也能用' },
  { key: 'dark', label: '暗色质感', hint: '深灰黑背景、轮廓光' },
  { key: 'white', label: '纯白极简', hint: '纯白无缝背景，干净上架风' },
  { key: 'lifestyle', label: '生活场景', hint: '真实居家/桌面环境' },
];

const STYLE_TEMPLATE_KEYS = new Set<string>(STYLE_TEMPLATES.map((item) => item.key));

type Scheme = {
  key: SchemeKey;
  site: SiteCode;
  templates: TemplateKey[];
  imageTier: ImageTier;
  styleTemplate: StyleTemplate;
  /** 用户填写的「重要信息」：进英文文案；场景/氛围影响画面，不画字。 */
  keyInfo: string;
};

type Props = {
  onOpenTask?: (taskId: number) => void;
  onOpenCollection?: () => void;
  isActive?: boolean;
};

type PoolDraft = {
  id: number;
  title: string;
  product_name?: string;
  image_url: string;
  status?: string;
  source_type?: string;
  selection_run_id?: string | null;
  batch_display_name?: string;
  cost?: number | null;
  declared_price?: number | null;
  created_at?: string;
  raw_payload?: {
    price?: string | number;
    currency?: string;
    source_title?: string;
    category?: string;
  } | null;
};

type SourceFilter = 'all' | 'onebound_api' | 'web_manual_capture' | 'other';
type SortKey = 'latest' | 'oldest' | 'price_asc' | 'price_desc';

type SubmitResult = {
  key: SchemeKey;
  count: number;
  taskId?: number;
  error?: string;
};

type SubmitResponse = { task_id: number; message?: string };

function defaultScheme(key: SchemeKey): Scheme {
  return {
    key,
    site: 'US',
    templates: [...DEFAULT_TEMPLATES],
    imageTier: 'highest',
    styleTemplate: 'auto',
    keyInfo: '',
  };
}

/** 本地记忆只存方案配置：商品选择、筛选、页码都不持久化，避免恢复出陈旧草稿。 */
function sanitizeScheme(raw: unknown, key: SchemeKey): Scheme {
  const value = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {};
  const templates = Array.isArray(value.templates)
    ? value.templates.filter((item): item is TemplateKey => typeof item === 'string' && TEMPLATE_KEYS.has(item))
    : [];
  const uniqueTemplates = [...new Set(templates)].slice(0, 4);
  return {
    key,
    site: value.site === 'MX' ? 'MX' : 'US',
    templates: uniqueTemplates.length ? uniqueTemplates : [...DEFAULT_TEMPLATES],
    imageTier: value.imageTier === 'standard' ? 'standard' : 'highest',
    styleTemplate:
      typeof value.styleTemplate === 'string' && STYLE_TEMPLATE_KEYS.has(value.styleTemplate)
        ? (value.styleTemplate as StyleTemplate)
        : 'auto',
    keyInfo: typeof value.keyInfo === 'string' ? value.keyInfo.slice(0, 300) : '',
  };
}

function loadSchemes(): Scheme[] {
  try {
    const text = window.localStorage.getItem(SCHEMES_STORAGE_KEY);
    if (!text) return [defaultScheme('A')];
    const raw: unknown = JSON.parse(text);
    if (!Array.isArray(raw) || !raw.length) return [defaultScheme('A')];
    return raw.slice(0, MAX_SCHEMES).map((item, index) => sanitizeScheme(item, SCHEME_KEYS[index]));
  } catch {
    return [defaultScheme('A')];
  }
}

function saveSchemes(schemes: Scheme[]): void {
  try {
    window.localStorage.setItem(SCHEMES_STORAGE_KEY, JSON.stringify(schemes));
  } catch {
    // 隐私模式 / 配额满：记忆失效不影响提交。
  }
}

/** 与 AI 处理草稿池完全同口径：待处理池（排除 已处理/处理中/已删除）。 */
async function fetchDraftPool(ctx: ApiContext, batchId: string): Promise<PoolDraft[]> {
  const batchQuery = batchId === 'all' ? '' : `&selection_run_id=${encodeURIComponent(batchId)}`;
  const data = await ppRequest<{ drafts: PoolDraft[] }>(
    ctx,
    `${API_BASE}/drafts?view=summary&limit=500${batchQuery}`,
  );
  return (data.drafts || []).filter(
    (draft) => draft.status !== 'deleted' && draft.status !== 'processed' && draft.status !== 'processing',
  );
}

function batchChannelLabel(batch: DraftCollectionBatch): string {
  if (batch.collection_channel === 'shop_collection') return '整店采集';
  if (batch.collection_channel === 'plugin_capture') return '插件采集';
  if (batch.collection_channel === 'daily_selection') return '每日选品';
  // 历史数据没有 collection_channel：按批次 ID 前缀启发式推断。
  if (batch.batch_id.startsWith('shop-')) return '整店采集';
  if (batch.batch_id.length > 20) return '每日选品';
  return '采集批次';
}

function batchLabel(batch: DraftCollectionBatch): string {
  const channel = batchChannelLabel(batch);
  const name = batch.channel_name || batch.batch_id || '采集批次';
  return `${channel} · ${name} · ${batch.count} 条`;
}

const CURRENCY_SYMBOLS: Record<string, string> = {
  CNY: '¥',
  RMB: '¥',
  USD: '$',
  EUR: '€',
  MXN: 'MX$',
  GBP: '£',
};

function formatDraftPrice(draft: PoolDraft): string {
  const currency = String(draft.raw_payload?.currency || '').trim().toUpperCase();
  const rawPrice = String(draft.raw_payload?.price ?? '').trim();
  if (rawPrice) {
    if (!currency) return rawPrice;
    return `${CURRENCY_SYMBOLS[currency] || `${currency} `}${rawPrice}`;
  }
  const cost = draft.cost ?? draft.declared_price;
  return typeof cost === 'number' && Number.isFinite(cost) ? `¥${cost}` : '';
}

/** 排序用数值：优先成本/申报价，其次原币种价格串；取不到返回 NaN（排最后）。 */
function draftPriceValue(draft: PoolDraft): number {
  for (const value of [draft.cost, draft.declared_price]) {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
  }
  const parsed = Number(String(draft.raw_payload?.price ?? '').replace(/[^\d.]/g, ''));
  return Number.isFinite(parsed) ? parsed : Number.NaN;
}

function draftTimeValue(draft: PoolDraft): number {
  const parsed = Date.parse(String(draft.created_at || ''));
  return Number.isFinite(parsed) ? parsed : 0;
}

function sortDrafts(drafts: PoolDraft[], sort: SortKey): PoolDraft[] {
  const sorted = [...drafts];
  if (sort === 'latest') return sorted.sort((a, b) => draftTimeValue(b) - draftTimeValue(a) || b.id - a.id);
  if (sort === 'oldest') return sorted.sort((a, b) => draftTimeValue(a) - draftTimeValue(b) || a.id - b.id);
  return sorted.sort((a, b) => {
    const left = draftPriceValue(a);
    const right = draftPriceValue(b);
    const leftOk = Number.isFinite(left);
    const rightOk = Number.isFinite(right);
    if (!leftOk && !rightOk) return b.id - a.id;
    if (!leftOk) return 1;
    if (!rightOk) return -1;
    return sort === 'price_asc' ? left - right : right - left;
  });
}

export function ProductMarketingPage({ onOpenTask, onOpenCollection }: Props) {
  const ctx: ApiContext = productProcessingApiContext();
  const [drafts, setDrafts] = useState<PoolDraft[]>([]);
  const [batches, setBatches] = useState<DraftCollectionBatch[]>([]);
  const [schemes, setSchemes] = useState<Scheme[]>(() => loadSchemes());
  const [activeKey, setActiveKey] = useState<SchemeKey>('A');
  // 商品归属的唯一数据源：一个商品只能属于一个方案。
  const [assignments, setAssignments] = useState<Record<number, SchemeKey>>({});
  const [batchId, setBatchId] = useState<string>('all');
  const [sourceType, setSourceType] = useState<SourceFilter>('all');
  const [sort, setSort] = useState<SortKey>('latest');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<number>(PAGE_SIZES[0]);
  const [listLoading, setListLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [results, setResults] = useState<SubmitResult[]>([]);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    saveSchemes(schemes);
  }, [schemes]);

  const loadDrafts = async (nextBatchId: string = batchId) => {
    setListLoading(true);
    try {
      setDrafts(await fetchDraftPool(ctx, nextBatchId));
      setError('');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '商品加载失败');
    } finally {
      setListLoading(false);
    }
  };

  const loadBatches = async () => {
    try {
      const data = await ppRequest<{ batches: DraftCollectionBatch[] }>(
        ctx,
        `${API_BASE}/draft-batches?limit=200`,
      );
      setBatches(data.batches || []);
    } catch {
      // 批次只是筛选辅助：拿不到时仍可按全部草稿选品。
      setBatches([]);
    }
  };

  useEffect(() => {
    void loadDrafts('all');
    void loadBatches();
  }, []);

  const activeScheme = useMemo(
    () => schemes.find((scheme) => scheme.key === activeKey) || schemes[0] || defaultScheme('A'),
    [schemes, activeKey],
  );

  const filtered = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    const matched = drafts.filter((draft) => {
      if (sourceType !== 'all') {
        const source = draft.source_type || '';
        if (sourceType === 'other') {
          if (source === 'onebound_api' || source === 'web_manual_capture') return false;
        } else if (source !== sourceType) {
          return false;
        }
      }
      if (!keyword) return true;
      return [draft.title, draft.product_name, draft.raw_payload?.source_title]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(keyword));
    });
    return sortDrafts(matched, sort);
  }, [drafts, sourceType, query, sort]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  // page 可能因为筛选/翻页尺寸变化越界：展示时夹紧，effect 再同步回 state。
  const safePage = Math.min(page, totalPages);
  const pageDrafts = useMemo(
    () => filtered.slice((safePage - 1) * pageSize, safePage * pageSize),
    [filtered, safePage, pageSize],
  );

  useEffect(() => {
    setPage(1);
  }, [batchId, sourceType, sort, query, pageSize]);

  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);

  const counts = useMemo(() => {
    const next: Record<SchemeKey, number> = { A: 0, B: 0, C: 0 };
    for (const key of Object.values(assignments)) next[key] += 1;
    return next;
  }, [assignments]);

  const totalSelected = Object.keys(assignments).length;
  const totalImages = useMemo(
    () => schemes.reduce((sum, scheme) => sum + counts[scheme.key] * (scheme.templates.length + 1), 0),
    [schemes, counts],
  );

  // 当前方案里已勾选、但被搜索/筛选隐藏的商品数：提交时仍会一起提交，必须显式提示。
  const hiddenSelected = useMemo(() => {
    const visible = new Set(filtered.map((draft) => draft.id));
    let hidden = 0;
    for (const [id, key] of Object.entries(assignments)) {
      if (key === activeKey && !visible.has(Number(id))) hidden += 1;
    }
    return hidden;
  }, [assignments, activeKey, filtered]);

  const updateActiveScheme = (updater: (scheme: Scheme) => Scheme) => {
    setSchemes((current) => current.map((scheme) => (scheme.key === activeKey ? updater(scheme) : scheme)));
  };

  const toggleDraft = (id: number) => {
    setAssignments((current) => {
      const next = { ...current };
      if (next[id] === activeKey) delete next[id];
      else next[id] = activeKey;
      return next;
    });
  };

  const addToActiveScheme = (ids: number[]) => {
    // 批量只补「还没归属方案」的商品：不把其他方案已选中的商品偷偷搬走。
    const addable = ids.filter((id) => !assignments[id]);
    if (!addable.length) {
      setNotice(ids.length ? '这些商品都已归属其他方案' : '当前筛选没有可加入的商品');
      setError('');
      return;
    }
    if (addable.length > 100 && !window.confirm(`将 ${addable.length} 个商品加入方案 ${activeKey}，是否继续？`)) return;
    setAssignments((current) => {
      const next = { ...current };
      for (const id of addable) if (!next[id]) next[id] = activeKey;
      return next;
    });
    setNotice(`已把 ${addable.length} 个商品加入方案 ${activeKey}`);
    setError('');
  };

  const clearActiveScheme = () => {
    setAssignments((current) => {
      const next: Record<number, SchemeKey> = {};
      for (const [id, key] of Object.entries(current)) {
        if (key !== activeKey) next[Number(id)] = key;
      }
      return next;
    });
    setNotice(`已清空方案 ${activeKey} 的选择`);
  };

  const toggleTemplate = (key: TemplateKey) => {
    updateActiveScheme((scheme) => {
      if (scheme.templates.includes(key)) {
        return { ...scheme, templates: scheme.templates.filter((item) => item !== key) };
      }
      if (scheme.templates.length >= 4) return scheme;
      return { ...scheme, templates: [...scheme.templates, key] };
    });
  };

  const addScheme = () => {
    if (schemes.length >= MAX_SCHEMES) return;
    const key = SCHEME_KEYS[schemes.length];
    setSchemes((current) => [...current, defaultScheme(key)]);
    setActiveKey(key);
  };

  const removeScheme = () => {
    if (schemes.length <= 1) return;
    if (counts[activeKey] > 0 && !window.confirm(`方案 ${activeKey} 有 ${counts[activeKey]} 个已选商品，删除后选择会一起清除，是否继续？`)) {
      return;
    }
    const remaining = schemes.filter((scheme) => scheme.key !== activeKey);
    // 删除中间方案后，后面的方案顺延字母，归属关系同步映射。
    const mapping = new Map<SchemeKey, SchemeKey>();
    remaining.forEach((scheme, index) => mapping.set(scheme.key, SCHEME_KEYS[index]));
    setAssignments((current) => {
      const next: Record<number, SchemeKey> = {};
      for (const [id, key] of Object.entries(current)) {
        if (key === activeKey) continue;
        const mapped = mapping.get(key);
        if (mapped) next[Number(id)] = mapped;
      }
      return next;
    });
    setSchemes(remaining.map((scheme, index) => ({ ...scheme, key: SCHEME_KEYS[index] })));
    setActiveKey(SCHEME_KEYS[0]);
    setNotice('');
    setError('');
  };

  const changeBatch = (value: string) => {
    setBatchId(value);
    void loadDrafts(value);
  };

  const refresh = () => {
    void loadDrafts();
    void loadBatches();
  };

  const start = async () => {
    if (submitting) return;
    const entries = schemes
      .map((scheme) => ({
        scheme,
        ids: Object.entries(assignments)
          .filter(([, key]) => key === scheme.key)
          .map(([id]) => Number(id)),
      }))
      .filter((entry) => entry.ids.length > 0);
    if (!entries.length) {
      setError('先在画廊里选择商品');
      return;
    }
    const missingTemplates = entries.find((entry) => !entry.scheme.templates.length);
    if (missingTemplates) {
      setError(`方案 ${missingTemplates.scheme.key} 至少选 1 个版式`);
      return;
    }
    setSubmitting(true);
    setError('');
    setNotice('');
    setResults([]);
    const collected: SubmitResult[] = [];
    for (const entry of entries) {
      const body = {
        title: `精选处理任务 · 方案${entry.scheme.key}`,
        draft_ids: entry.ids,
        max_products: 0,
        async_mode: true,
        target_site: entry.scheme.site,
        target_language: entry.scheme.site === 'US' ? 'en' : 'es',
        // 精选处理替代普通四宫格：这里不再请求 four_grid，轮播直接用精选图。
        processing_scope: ['title', 'details', 'product_dimensions', 'marketing_images'],
        marketing_templates: entry.scheme.templates,
        marketing_image_tier: entry.scheme.imageTier,
        marketing_style_template: entry.scheme.styleTemplate,
        marketing_key_info: entry.scheme.keyInfo.trim(),
        image_generation_count: 4,
        max_parallel_drafts: 8,
      };
      try {
        const data = await ppRequest<SubmitResponse>(ctx, `${API_BASE}/drafts/process`, {
          body,
          headers: {
            'Idempotency-Key':
              globalThis.crypto?.randomUUID?.() || `pm-${entry.scheme.key}-${Date.now()}`,
          },
        });
        collected.push({ key: entry.scheme.key, count: entry.ids.length, taskId: data?.task_id });
        // 只清掉提交成功的方案选择；失败方案保留，方便修正后重试。
        setAssignments((current) => {
          const next = { ...current };
          for (const id of entry.ids) if (next[id] === entry.scheme.key) delete next[id];
          return next;
        });
      } catch (cause) {
        collected.push({
          key: entry.scheme.key,
          count: entry.ids.length,
          error: cause instanceof Error ? cause.message : '提交失败',
        });
      }
      setResults([...collected]);
    }
    const succeeded = collected.filter((item) => item.taskId).length;
    setNotice(`已提交 ${succeeded}/${collected.length} 个方案`);
    setSubmitting(false);
    await loadDrafts();
    void loadBatches();
    const onlyTaskId = collected.length === 1 ? collected[0].taskId : undefined;
    if (succeeded === 1 && onlyTaskId) {
      // 单方案仍保持原来的「提交后直接打开任务详情」。
      onOpenTask?.(onlyTaskId);
    }
  };

  const banner = error || notice;

  return (
    <div className="pm-page">
      <section className="pm-hero">
        <div>
          <p className="pm-eyebrow">PREMIUM LISTING</p>
          <h1>精选处理</h1>
          <p className="pm-hero-sub">
            参考原图的版式结构，由 AI 重新拍摄与排版。文案由排版层确定性渲染，
            产品由 AI 照片层保真还原，每个商品按所属方案产出可直接上架的图片。
          </p>
        </div>
        <dl className="pm-hero-stats">
          <div><dt>待精选</dt><dd>{drafts.length}</dd></div>
          <div><dt>已选</dt><dd>{totalSelected}</dd></div>
          <div><dt>预计产出</dt><dd>{totalImages} 张</dd></div>
        </dl>
      </section>

      {banner && <div className={`pm-banner${error ? ' is-error' : ''}`}>{banner}</div>}

      {results.length > 0 && (
        <div className="pm-results">
          {results.map((item) => (
            <div key={item.key} className={`pm-result${item.error ? ' is-error' : ''}`}>
              <span>方案 {item.key} · {item.count} 个商品</span>
              {item.taskId ? (
                <button type="button" onClick={() => onOpenTask?.(item.taskId!)}>
                  打开任务 #{item.taskId}
                </button>
              ) : (
                <em>{item.error || '未创建任务'}</em>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="pm-grid">
        <section className="pm-gallery">
          <header className="pm-gallery-head">
            <div className="pm-gallery-title">
              <h2>商品画廊</h2>
              <p className="pm-gallery-sub">
                当前筛选 {filtered.length} 条 · 方案 {activeKey} 已选 {counts[activeKey]} 个
                {listLoading ? ' · 加载中…' : ''}
              </p>
            </div>
            <div className="pm-gallery-tools">
              <input
                value={query}
                placeholder="搜标题 / 来源标题…"
                aria-label="搜索商品"
                onChange={(event) => setQuery(event.target.value)}
              />
              <button type="button" onClick={refresh}>刷新</button>
            </div>
          </header>

          <div className="pm-filters">
            <select value={batchId} aria-label="按采集批次筛选" onChange={(event) => changeBatch(event.target.value)}>
              <option value="all">全部批次</option>
              <option value="__unassigned__">未分组</option>
              {batches
                .filter((batch) => batch.batch_id !== '__unassigned__')
                .map((batch) => (
                  <option key={batch.batch_id} value={batch.batch_id}>{batchLabel(batch)}</option>
                ))}
            </select>
            <select
              value={sourceType}
              aria-label="按来源筛选"
              onChange={(event) => setSourceType(event.target.value as SourceFilter)}
            >
              <option value="all">全部来源</option>
              <option value="onebound_api">1688 接口</option>
              <option value="web_manual_capture">插件采集</option>
              <option value="other">其他</option>
            </select>
            <select value={sort} aria-label="排序" onChange={(event) => setSort(event.target.value as SortKey)}>
              <option value="latest">最新入池</option>
              <option value="oldest">最早入池</option>
              <option value="price_asc">价格低→高</option>
              <option value="price_desc">价格高→低</option>
            </select>
            <div className="pm-bulk">
              <button type="button" onClick={() => addToActiveScheme(pageDrafts.map((draft) => draft.id))}>
                全选本页
              </button>
              <button type="button" onClick={() => addToActiveScheme(filtered.map((draft) => draft.id))}>
                全选筛选结果
              </button>
              <button type="button" onClick={clearActiveScheme}>清空当前方案</button>
            </div>
          </div>

          {hiddenSelected > 0 && (
            <p className="pm-hidden-note">
              另有 {hiddenSelected} 个已选商品不在当前筛选结果中，提交时仍会一起提交。
            </p>
          )}

          {pageDrafts.length ? (
            <ul className="pm-cards">
              {pageDrafts.map((draft) => {
                const owner = assignments[draft.id];
                const picked = owner === activeKey;
                const price = formatDraftPrice(draft);
                return (
                  <li key={draft.id}>
                    <button
                      type="button"
                      className={`pm-card${picked ? ' is-picked' : ''}${owner && !picked ? ' is-other' : ''}`}
                      aria-pressed={picked}
                      title={owner && !picked ? `点击把商品从方案 ${owner} 移入方案 ${activeKey}` : undefined}
                      onClick={() => toggleDraft(draft.id)}
                    >
                      {/* alicdn 防盗链：带 Referer 直接 403，必须 no-referrer */}
                      <img
                        src={draft.image_url || ''}
                        alt=""
                        loading="lazy"
                        referrerPolicy="no-referrer"
                      />
                      {owner && (
                        <span className={`pm-card-scheme is-${owner.toLowerCase()}`}>{owner}</span>
                      )}
                      <span className="pm-card-check">{picked ? '✓' : ''}</span>
                      <span className="pm-card-title" title={draft.title}>{draft.title || `商品 #${draft.id}`}</span>
                      <span className="pm-card-batch" title={draft.batch_display_name || '未分组'}>
                        {draft.batch_display_name || '未分组'}
                      </span>
                      <span className="pm-card-meta">
                        #{draft.id}
                        {draft.source_type === 'onebound_api' ? ' · 1688' : draft.source_type === 'web_manual_capture' ? ' · 插件' : ''}
                        {price ? ` · ${price}` : ''}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <div className="pm-empty">
              {drafts.length ? (
                <>
                  <p>当前筛选没有可精选的商品。</p>
                  <button
                    type="button"
                    onClick={() => {
                      setQuery('');
                      setSourceType('all');
                      if (batchId !== 'all') changeBatch('all');
                    }}
                  >
                    清除筛选
                  </button>
                </>
              ) : (
                <>
                  <p>还没有可精选的商品。</p>
                  <button type="button" onClick={() => onOpenCollection?.()}>去采集商品</button>
                </>
              )}
            </div>
          )}

          <div className="pm-pager">
            <span>共 {filtered.length} 条 · 第 {safePage}/{totalPages} 页</span>
            <div>
              <select
                value={pageSize}
                aria-label="每页数量"
                onChange={(event) => setPageSize(Number(event.target.value))}
              >
                {PAGE_SIZES.map((size) => (
                  <option key={size} value={size}>每页 {size}</option>
                ))}
              </select>
              <button type="button" disabled={safePage <= 1} onClick={() => setPage(safePage - 1)}>
                上一页
              </button>
              <button type="button" disabled={safePage >= totalPages} onClick={() => setPage(safePage + 1)}>
                下一页
              </button>
            </div>
          </div>
        </section>

        <aside className="pm-panel">
          <div className="pm-panel-head">
            <h2>出图配置</h2>
            {schemes.length > 1 && (
              <button type="button" className="pm-scheme-remove" onClick={removeScheme}>
                删除当前方案
              </button>
            )}
          </div>

          <div className="pm-schemes" role="tablist" aria-label="配置方案">
            {schemes.map((scheme) => (
              <button
                key={scheme.key}
                type="button"
                role="tab"
                aria-selected={scheme.key === activeKey}
                className={`pm-scheme-tab${scheme.key === activeKey ? ' is-active' : ''}`}
                onClick={() => setActiveKey(scheme.key)}
              >
                方案 {scheme.key}
                <em>{counts[scheme.key]}</em>
              </button>
            ))}
            {schemes.length < MAX_SCHEMES && (
              <button type="button" className="pm-scheme-add" onClick={addScheme}>+ 新增方案</button>
            )}
          </div>

          <label className="pm-field">
            目标市场
            <select
              value={activeScheme.site}
              onChange={(event) =>
                updateActiveScheme((scheme) => ({ ...scheme, site: event.target.value as SiteCode }))
              }
            >
              <option value="US">美国 · English</option>
              <option value="MX">墨西哥 · Español</option>
            </select>
          </label>

          <div className="pm-field">
            <span>精选版式（选 1–4 个，另自动产出 1 张纯白底主图）</span>
            <div className="pm-templates">
              {TEMPLATES.map((item) => {
                const picked = activeScheme.templates.includes(item.key);
                return (
                  <button
                    key={item.key}
                    type="button"
                    className={`pm-template${picked ? ' is-picked' : ''}`}
                    aria-pressed={picked}
                    title={item.hint}
                    onClick={() => toggleTemplate(item.key)}
                  >
                    <strong>{item.label}</strong>
                    <em>{item.hint}</em>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="pm-field">
            <span>画质档位</span>
            <div className="pm-templates">
              <button
                type="button"
                className={`pm-template${activeScheme.imageTier === 'standard' ? ' is-picked' : ''}`}
                aria-pressed={activeScheme.imageTier === 'standard'}
                onClick={() => updateActiveScheme((scheme) => ({ ...scheme, imageTier: 'standard' }))}
              >
                <strong>标准</strong>
                <em>更快更省</em>
              </button>
              <button
                type="button"
                className={`pm-template${activeScheme.imageTier === 'highest' ? ' is-picked' : ''}`}
                aria-pressed={activeScheme.imageTier === 'highest'}
                onClick={() => updateActiveScheme((scheme) => ({ ...scheme, imageTier: 'highest' }))}
              >
                <strong>最高</strong>
                <em>保真与质感更好</em>
              </button>
            </div>
          </div>

          <div className="pm-field">
            <span>共通模板（不按品类写死，工具也能用亮色）</span>
            <div className="pm-templates">
              {STYLE_TEMPLATES.map((item) => {
                const picked = activeScheme.styleTemplate === item.key;
                return (
                  <button
                    key={item.key}
                    type="button"
                    className={`pm-template${picked ? ' is-picked' : ''}`}
                    aria-pressed={picked}
                    title={item.hint}
                    onClick={() =>
                      updateActiveScheme((scheme) => ({ ...scheme, styleTemplate: item.key }))
                    }
                  >
                    <strong>{item.label}</strong>
                    <em>{item.hint}</em>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="pm-field">
            <span>重要信息（选填）</span>
            <textarea
              className="pm-keyinfo"
              rows={4}
              maxLength={300}
              value={activeScheme.keyInfo}
              placeholder="写必须体现的信息：材质 / 尺寸 / 使用场景 / 卖点；只写商品真实具备的，留空交给 AI。"
              onChange={(event) =>
                updateActiveScheme((scheme) => ({ ...scheme, keyInfo: event.target.value }))
              }
            />
            <em>卖点写进英文文案，场景/氛围影响画面；文字不会画进图里。</em>
          </div>

          <button
            type="button"
            className="pm-start"
            disabled={!totalSelected || submitting}
            onClick={() => void start()}
          >
            {submitting ? '提交中…' : `开始精选（共 ${totalSelected} 个商品 / ${totalImages} 张图）`}
          </button>
        </aside>
      </div>
    </div>
  );
}
