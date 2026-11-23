import { useEffect, useMemo, useState } from 'react';

import { ppRequest, type ApiContext } from '../api/client';
import { productProcessingApiContext } from '../api/context';
import { listProductDrafts, type ProductDraft } from '../api/productProcessingApi';

/**
 * 精选处理（独立板块 · 独立界面）。
 *
 * 与 AI 处理的刻意差异（2026-11-23 用户要求「UI 全都不一样」）：
 * - 视觉：墨黑金主题、大画廊卡片，不用 AI 处理的 commandbar + 表格行；
 * - 结构：左「商品画廊」右「粘性设置面板」，一屏内完成选品和配置；
 * - 流程：本页直接提交精选任务，成功后跳到任务详情（进度/预检与 AI 处理共用）。
 *
 * 产出：4 张标准四宫格轮播 + 3 张精品营销图（版式可选）。
 */

const API_BASE = '/api/product-processing';

type TemplateKey = 'headline_hero' | 'action_proof' | 'icon_grid' | 'material_strip' | 'use_cases';

const TEMPLATES: Array<{ key: TemplateKey; label: string; hint: string }> = [
  { key: 'headline_hero', label: '大字卖点', hint: '顶部大标题 + 产品主视觉' },
  { key: 'action_proof', label: '细节特写', hint: '工作端微距 + 左上卖点' },
  { key: 'icon_grid', label: '功能图标网格', hint: '2×3 卖点短词卡' },
  { key: 'material_strip', label: '材质适用条', hint: '底部材质药丸条' },
  { key: 'use_cases', label: '场景应用', hint: '四角用途标注' },
];

type Props = {
  onOpenTask?: (taskId: number) => void;
  onOpenCollection?: () => void;
  isActive?: boolean;
};

type SubmitResponse = { task_id: number; message?: string };

export function ProductMarketingPage({ onOpenTask, onOpenCollection }: Props) {
  const ctx: ApiContext = productProcessingApiContext();
  const [drafts, setDrafts] = useState<ProductDraft[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [query, setQuery] = useState('');
  const [site, setSite] = useState<'US' | 'MX'>('US');
  const [style, setStyle] = useState<'ornate' | 'lifestyle' | 'minimal' | 'smart'>('ornate');
  const [templates, setTemplates] = useState<TemplateKey[]>(['headline_hero', 'action_proof', 'icon_grid']);
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');

  const load = async () => {
    try {
      setDrafts(await listProductDrafts());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '商品加载失败');
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const visible = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return drafts;
    return drafts.filter((draft) => (draft.title || '').toLowerCase().includes(keyword));
  }, [drafts, query]);

  const toggle = (id: number) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleTemplate = (key: TemplateKey) => {
    setTemplates((current) => {
      if (current.includes(key)) return current.filter((item) => item !== key);
      if (current.length >= 3) return current;
      return [...current, key];
    });
  };

  const start = async () => {
    if (!selected.size || loading) return;
    if (!templates.length) {
      setError('至少选 1 个版式（推荐 3 个）');
      return;
    }
    setLoading(true);
    setError('');
    setNotice('');
    try {
      const body = {
        title: '精选处理任务',
        draft_ids: [...selected],
        max_products: 0,
        async_mode: true,
        target_site: site,
        target_language: site === 'US' ? 'en' : 'es',
        processing_scope: ['title', 'details', 'product_dimensions', 'four_grid', 'detail_images', 'marketing_images'],
        marketing_templates: templates,
        image_generation_count: 4,
        scene_style: style,
        max_parallel_drafts: 8,
      };
      const data = await ppRequest<SubmitResponse>(ctx, `${API_BASE}/drafts/process`, {
        body,
        headers: { 'Idempotency-Key': globalThis.crypto?.randomUUID?.() || `pm-${Date.now()}` },
      });
      setNotice(`已提交：${selected.size} 个商品，每个 7 张图片`);
      setSelected(new Set());
      void load();
      if (data?.task_id) onOpenTask?.(data.task_id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '提交失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="pm-page">
      <section className="pm-hero">
        <div>
          <p className="pm-eyebrow">PREMIUM LISTING</p>
          <h1>精选处理</h1>
          <p className="pm-hero-sub">
            选商品、挑版式，一次产出可直接上架的 <b>7 张图片</b>。
            文案由排版层确定性渲染，产品由 AI 照片层保真还原。
          </p>
        </div>
        <dl className="pm-hero-stats">
          <div><dt>待精选</dt><dd>{drafts.length}</dd></div>
          <div><dt>已选</dt><dd>{selected.size}</dd></div>
          <div><dt>预计产出</dt><dd>{selected.size * 7} 张</dd></div>
        </dl>
      </section>

      {(notice || error) && <div className={`pm-banner${error ? ' is-error' : ''}`}>{error || notice}</div>}

      <div className="pm-grid">
        <section className="pm-gallery">
          <header className="pm-gallery-head">
            <h2>商品画廊</h2>
            <div className="pm-gallery-tools">
              <input
                value={query}
                placeholder="搜标题…"
                aria-label="搜索商品"
                onChange={(event) => setQuery(event.target.value)}
              />
              <button type="button" onClick={() => setSelected(new Set(visible.map((d) => d.id)))}>
                全选
              </button>
              <button type="button" onClick={() => setSelected(new Set())}>清空</button>
              <button type="button" onClick={() => void load()}>刷新</button>
            </div>
          </header>

          {visible.length ? (
            <ul className="pm-cards">
              {visible.map((draft) => {
                const picked = selected.has(draft.id);
                return (
                  <li key={draft.id}>
                    <button
                      type="button"
                      className={`pm-card${picked ? ' is-picked' : ''}`}
                      aria-pressed={picked}
                      onClick={() => toggle(draft.id)}
                    >
                      <img src={draft.image_url || ''} alt="" loading="lazy" />
                      <span className="pm-card-check">{picked ? '✓' : ''}</span>
                      <span className="pm-card-title" title={draft.title}>{draft.title || `商品 #${draft.id}`}</span>
                      <span className="pm-card-meta">#{draft.id}{draft.source_type === 'onebound_api' ? ' · 1688' : ''}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <div className="pm-empty">
              <p>还没有可精选的商品。</p>
              <button type="button" onClick={() => onOpenCollection?.()}>去采集商品</button>
            </div>
          )}
        </section>

        <aside className="pm-panel">
          <h2>出图配置</h2>

          <label className="pm-field">
            目标市场
            <select value={site} onChange={(event) => setSite(event.target.value as 'US' | 'MX')}>
              <option value="US">美国 · English</option>
              <option value="MX">墨西哥 · Español</option>
            </select>
          </label>

          <label className="pm-field">
            场景风格
            <select value={style} onChange={(event) => setStyle(event.target.value as typeof style)}>
              <option value="ornate">花哨 · 花艺/礼赠/丝绒</option>
              <option value="lifestyle">生活 · 桌面/窗影</option>
              <option value="minimal">极简 · 纯色/木石</option>
              <option value="smart">智能混合 · 自动轮换</option>
            </select>
          </label>

          <div className="pm-field">
            <span>精选版式（选 3 个）</span>
            <div className="pm-templates">
              {TEMPLATES.map((item) => {
                const picked = templates.includes(item.key);
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

          <div className="pm-output">
            <span>每个商品产出</span>
            <strong>7 张图片</strong>
            <em>营销图文字由本地排版渲染，绝不错拼；失败可单独重跑</em>
          </div>

          <button
            type="button"
            className="pm-start"
            disabled={!selected.size || loading}
            onClick={() => void start()}
          >
            {loading ? '提交中…' : `开始精选（${selected.size} 个商品）`}
          </button>
        </aside>
      </div>
    </div>
  );
}
