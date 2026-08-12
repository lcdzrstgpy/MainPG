import { useMemo, useState } from 'react';
import type { DraftSummary, DraftVariant } from '../types';
import {
  EMPTY_SKU_FILTER,
  collectAttrNames,
  filterActive,
  matchSku,
  variantLabel,
  type SkuFilterState,
} from '../utils/skuFilter';

type Props = {
  /** 勾选的目标草稿（draft 状态） */
  drafts: DraftSummary[];
  /** 每个草稿已存在的删除集合（未保存 edits + 已保存的历史删除） */
  baseDeletes: (draftId: number) => string[];
  /** 保存一个草稿的完整删除集合（后端为全量替换语义） */
  onSaveDeletes: (draftId: number, deletes: string[]) => Promise<void>;
  /** 全部保存完成后回调（页面用于刷新列表） */
  onBatchSaved?: () => void;
  onClose: () => void;
};

function draftVariants(draft: DraftSummary): DraftVariant[] {
  const raw = draft.raw_payload || {};
  return Array.isArray(raw.source_variant_records) ? raw.source_variant_records : [];
}

/** 计算一个草稿应用动作后的新删除集合；返回 null 表示该商品跳过（无命中可保留）。 */
function nextDeletes(
  draftId: number,
  variants: DraftVariant[],
  hitLabels: Set<string>,
  base: Set<string>,
  mode: 'delete' | 'keep'
): string[] | null {
  const next = new Set(base);
  if (mode === 'delete') {
    for (const label of hitLabels) next.add(label);
    // 至少保留 1 个 SKU：若命中会删光该商品，撤销最后一个命中
    if (variants.length - hitLabels.size < 1 && hitLabels.size > 0) {
      next.delete([...hitLabels].pop()!);
    }
  } else {
    if (hitLabels.size === 0) return null;
    for (const variant of variants) {
      const label = variantLabel(variant);
      if (!hitLabels.has(label)) next.add(label);
    }
  }
  return Array.from(next);
}

export function SkuBatchManager({ drafts, baseDeletes, onSaveDeletes, onBatchSaved, onClose }: Props) {
  const [filter, setFilter] = useState<SkuFilterState>(EMPTY_SKU_FILTER);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState('');

  const rows = useMemo(
    () =>
      drafts.map((draft) => {
        const variants = draftVariants(draft);
        const hitLabels = new Set(
          variants.filter((v) => matchSku(v, filter)).map((v) => variantLabel(v))
        );
        return { draft, variants, hitLabels };
      }),
    [drafts, filter]
  );

  const attrNames = useMemo(() => collectAttrNames(rows.map((r) => r.variants)), [rows]);

  const hitCount = useMemo(() => rows.reduce((sum, r) => sum + r.hitLabels.size, 0), [rows]);
  const totalVariants = useMemo(() => rows.reduce((sum, r) => sum + r.variants.length, 0), [rows]);
  const active = filterActive(filter);

  const run = async (mode: 'delete' | 'keep') => {
    if (!active || saving) return;
    const label = mode === 'delete' ? '删除命中的 SKU' : '仅保留命中的 SKU（删除其余）';
    if (!window.confirm(`确定执行「${label}」？共命中 ${hitCount} 个 SKU（涉及 ${rows.length} 个商品）。`)) return;
    setSaving(true);
    setNotice('');
    let saved = 0;
    let skipped = 0;
    let failed = '';
    try {
      for (const row of rows) {
        const next = nextDeletes(row.draft.id, row.variants, row.hitLabels, new Set(baseDeletes(row.draft.id)), mode);
        if (next === null) { skipped += 1; continue; }
        try {
          await onSaveDeletes(row.draft.id, next);
          saved += 1;
        } catch (err) {
          failed = failed || (err instanceof Error ? err.message : String(err));
        }
      }
      setNotice(`已保存 ${saved} 个商品` + (skipped ? ` · 跳过 ${skipped} 个（无可保留命中）` : '') + (failed ? ` · 失败：${failed}` : ''));
      onBatchSaved?.();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="verify-drawer-root">
      <div className="verify-drawer-mask" onClick={onClose} />
      <section className="sku-batch-panel" role="dialog" aria-modal="true" aria-label="批量管理 SKU">
        <header className="sku-batch-head">
          <div>
            <p className="verify-eyebrow">SKU BATCH MANAGER</p>
            <h2>批量管理 SKU</h2>
            <p>对 {drafts.length} 个商品 · 共 {totalVariants} 个 SKU 按条件筛选后批量删除或保留。</p>
          </div>
          <button className="verify-drawer-close" onClick={onClose} aria-label="关闭">×</button>
        </header>

        <div className="sku-batch-body">
          <div className="sku-batch-filters">
            <label>
              <span>属性名</span>
              <select
                value={filter.attrName}
                onChange={(e) => setFilter((f) => ({ ...f, attrName: e.target.value }))}
              >
                <option value="">任意属性</option>
                {attrNames.map((name) => <option key={name} value={name}>{name}</option>)}
              </select>
            </label>
            <label>
              <span>属性值包含</span>
              <input
                placeholder={filter.attrName ? `如：白 / L / 30*20` : '先选择属性名'}
                value={filter.attrValue}
                disabled={!filter.attrName}
                onChange={(e) => setFilter((f) => ({ ...f, attrValue: e.target.value }))}
              />
            </label>
            <label>
              <span>全局搜索</span>
              <input
                placeholder="在所有属性值/显示名中匹配，可与上方叠加"
                value={filter.global}
                onChange={(e) => setFilter((f) => ({ ...f, global: e.target.value }))}
              />
            </label>
            <button
              type="button"
              className="btn-mini"
              onClick={() => setFilter(EMPTY_SKU_FILTER)}
              disabled={!active}
            >清空条件</button>
          </div>

          <div className="sku-batch-summary">
            {active
              ? <>当前命中 <strong>{hitCount}</strong> 个 SKU（{rows.length} 个商品）</>
              : '设置筛选条件后预览命中 SKU，再执行批量操作。'}
          </div>

          {active && (
            <div className="sku-batch-preview">
              {rows.map((row) => {
                const base = new Set(baseDeletes(row.draft.id));
                const afterDelete = nextDeletes(row.draft.id, row.variants, row.hitLabels, base, 'delete');
                const afterKeep = nextDeletes(row.draft.id, row.variants, row.hitLabels, base, 'keep');
                const remainingOnDelete = afterDelete ? row.variants.length - (afterDelete.length - base.size) : row.variants.length;
                const remainingOnKeep = afterKeep ? row.hitLabels.size : 0;
                const title = row.draft.title || row.draft.product_name || row.draft.source_ref || '未命名商品';
                const titleStr = (row.variants.find((v) => v.attributes && Object.keys(v.attributes).length)?.display_name) || '';
                return (
                  <section key={row.draft.id} className="sku-batch-draft">
                    <header>
                      <div>
                        <strong title={String(title)}>{String(title).slice(0, 60)}</strong>
                        {titleStr && <small>{titleStr}</small>}
                      </div>
                      <span>命中 <b>{row.hitLabels.size}</b> / {row.variants.length}</span>
                    </header>
                    {row.hitLabels.size > 0 && (
                      <div className="sku-batch-chips">
                        {[...row.hitLabels].map((label) => <span key={label} title={label}>{label}</span>)}
                      </div>
                    )}
                    <p className="sku-batch-remaining">
                      删除命中后剩 <b>{remainingOnDelete}</b> 个 · 仅保留命中后剩 <b>{remainingOnKeep}</b> 个
                      {remainingOnKeep === 0 && <em>（无可保留，将跳过）</em>}
                    </p>
                  </section>
                );
              })}
            </div>
          )}

          {notice && <div className="verify-message">{notice}</div>}
        </div>

        <footer className="sku-batch-foot">
          <button type="button" onClick={onClose} disabled={saving}>关闭</button>
          <button
            type="button"
            className="danger"
            onClick={() => void run('delete')}
            disabled={!active || saving}
          >删除命中（{hitCount}）</button>
          <button
            type="button"
            className="primary"
            onClick={() => void run('keep')}
            disabled={!active || saving}
          >仅保留命中（删除其余）</button>
        </footer>
      </section>
    </div>
  );
}
