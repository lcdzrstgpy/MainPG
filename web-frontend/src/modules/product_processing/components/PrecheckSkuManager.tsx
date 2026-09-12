import { useState } from 'react';
import type { DraftVariant, PreviewItem, PreviewTextReviewStatus } from '../types';
import {
  EMPTY_SKU_FILTER,
  collectAttrNames,
  filterActive,
  matchSku,
  variantKey,
  variantLabel,
  type SkuFilterState,
} from '../utils/skuFilter';
import { PrecheckSkuImageCropper } from './PrecheckSkuImageCropper';

export type VariantImageMode = 'source' | 'main' | 'auto';

/** 可用于替换 SKU 规格图的候选图（值写入 variant_image_overrides）。 */
export type VariantImageOption = {
  /** 写入 variant_image_overrides 的值：预览资产 ID 或 http(s) 直链 */
  value: string;
  /** 列表缩略图地址 */
  thumb: string;
  /** 裁剪时读取像素的地址（优先同源预览地址） */
  cropSrc: string;
  /** 来源分组标题，如「AI 处理图」 */
  group: string;
  /** 该候选的具体说明 */
  label: string;
};

export type VariantRef = { draftId: number; variantKey: string };

type Props = {
  /** 当前任务的全部商品项（含 source_variant_records） */
  items: PreviewItem[];
  /** 读取某商品当前的规格图策略 */
  modeOf: (draftId: number) => VariantImageMode;
  /** 该商品的规格图策略是否已保存（false = 仅本地编辑，尚未落库） */
  modeSavedOf: (draftId: number) => boolean;
  /** 应用规格图策略（写入预检页本地 edits，随保存/导出生效） */
  onApplyMode: (mode: VariantImageMode, draftIds: number[]) => void;
  /** 某商品已被整行剔除（不进导出表）的 SKU 变种键 */
  excludedOf: (draftId: number) => string[];
  /** 某商品逐个 SKU 换图的原始值（资产 ID 或图片地址） */
  overridesOf: (draftId: number) => Record<string, string>;
  /** 该 SKU 的原图中文复核状态（flagged=检出中文，需人工处理） */
  textReviewOf: (draftId: number, skuId: string) => PreviewTextReviewStatus;
  /** 某 SKU 当前替换图的可访问地址（无替换返回空串） */
  overrideUrlOf: (draftId: number, key: string) => string;
  /** 某商品可用的替换图候选（AI 处理图 / 本地导入 / 尺寸图 / 其他 SKU 规格原图） */
  optionsOf: (item: PreviewItem) => VariantImageOption[];
  /** 整行剔除选中的 SKU（导出时该变种不出现） */
  onExclude: (entries: VariantRef[]) => void;
  /** 恢复被剔除的 SKU */
  onRestore: (entries: VariantRef[]) => void;
  /** 批量设置/清除替换图（value 为空串表示清除） */
  onSetVariantImage: (entries: Array<VariantRef & { value: string }>) => void;
  /** 把裁剪产物上传为预览资产，返回可直接使用的替换图候选 */
  onUploadCrop: (draftId: number, file: File) => Promise<VariantImageOption>;
  /**
   * 裁剪前把图片地址换成可读像素的同源地址：外部跨域图先经后端图床转存，
   * 原样返回时表示该地址已可裁剪。
   */
  onPrepareCropSrc: (draftId: number, url: string) => Promise<string>;
  /** 点击缩略图放大查看 */
  onPreview: (url: string) => void;
  onClose: () => void;
};

type Scope = 'all' | 'missing' | 'replaced' | 'excluded' | 'text';

/** 单条来源链接（即一个商品）最多允许修改的 SKU 图片数。 */
const MAX_EDITED_VARIANT_IMAGES_PER_LINK = 6;

/** 关键词分隔符：中英文逗号、分号、顿号、空白（含换行）。 */
const KEYWORD_SEPARATOR = /[,，;；、\s]+/;

function splitKeywords(value: string): string[] {
  return value
    .split(KEYWORD_SEPARATOR)
    .map((item) => item.trim())
    .filter(Boolean);
}

const SCOPE_LABELS: Array<{ value: Scope; label: string }> = [
  { value: 'all', label: '全部 SKU' },
  { value: 'missing', label: '仅无规格图' },
  { value: 'replaced', label: '仅已换图' },
  { value: 'excluded', label: '仅已删除' },
  { value: 'text', label: '仅看待审核' },
];

/** 换图按商品逐个进行：每个商品用自己的处理图，避免跨商品错配。 */
type PickerGroup = { draftId: number; title: string; targets: VariantRef[] };

type PickerState = {
  groups: PickerGroup[];
  index: number;
  options: VariantImageOption[];
};

function safeImageUrl(value: unknown): string {
  const raw = String(value ?? '').trim();
  if (!raw) return '';
  try {
    const parsed = new URL(raw, window.location.origin);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.href : '';
  } catch {
    return '';
  }
}

function itemVariants(item: PreviewItem): DraftVariant[] {
  return Array.isArray(item.source_variant_records) ? item.source_variant_records : [];
}

/** 规格图只认变种自己的 image_url，不做商品主图回退，便于看出哪些 SKU 缺图。 */
function variantImage(variant: DraftVariant): string {
  return safeImageUrl(variant.image_url ?? variant.imageUrl);
}

function draftIdOf(item: PreviewItem): number {
  return item.product_draft_id ?? item.item_id;
}

function itemTitle(item: PreviewItem, draftId: number): string {
  return String(item.title || item.skc || `商品 ${draftId}`);
}

/**
 * 预检页 SKU 规格图管理侧栏：逐个查看/放大 SKU 规格图，按条件筛选后批量或逐项
 * 删除 SKU 规格（导出时不进表）、用处理之后的图片替换规格图（支持框选裁剪后再替换），
 * 也可整体切换「规格原图 / 全部使用商品主图替代」。所有改动并入预检 edits，
 * 由「保存预检修改」或「完成预审并导出」写入生效。
 */
export function PrecheckSkuManager({
  items,
  modeOf,
  modeSavedOf,
  onApplyMode,
  excludedOf,
  overridesOf,
  textReviewOf,
  overrideUrlOf,
  optionsOf,
  onExclude,
  onRestore,
  onSetVariantImage,
  onUploadCrop,
  onPrepareCropSrc,
  onPreview,
  onClose,
}: Props) {
  const [filter, setFilter] = useState<SkuFilterState>(EMPTY_SKU_FILTER);
  const [scope, setScope] = useState<Scope>('all');
  const [keywordInput, setKeywordInput] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [notice, setNotice] = useState('');
  const [picker, setPicker] = useState<PickerState | null>(null);
  const [crop, setCrop] = useState<{ draftId: number; variantKey: string; label: string; option: VariantImageOption } | null>(null);
  const [cropBusy, setCropBusy] = useState(false);
  const [cropPreparing, setCropPreparing] = useState(false);
  const [cropError, setCropError] = useState('');

  const rows = items
    .map((item) => {
      const draftId = draftIdOf(item);
      const excluded = new Set(excludedOf(draftId));
      const overrides = overridesOf(draftId);
      const variants = itemVariants(item).map((variant, index) => {
        const label = variantLabel(variant) || variant.display_name || `SKU ${index + 1}`;
        const key = variantKey(variant, label) || `index-${index}`;
        const sourceImage = variantImage(variant);
        const overrideValue = String(overrides[key] ?? '').trim();
        const overrideUrl = overrideValue ? overrideUrlOf(draftId, key) : '';
        const skuId = String(variant.sku_id || '');
        const display = overrideUrl || sourceImage;
        return {
          variant,
          label,
          key,
          sourceImage,
          overrideValue,
          overrideUrl,
          display,
          excluded: excluded.has(key),
          hit: matchSku(variant, filter),
          /** 原图中文复核状态，用于「仅看待审核」筛选与徽标。 */
          textReview: skuId ? textReviewOf(draftId, skuId) : ('' as PreviewTextReviewStatus),
        };
      });
      /** 商品主图：策略回退商品的最终取图（与导出侧 preview_image 同源）。 */
      const mainImageUrl = safeImageUrl(item.main_image);
      return {
        item,
        draftId,
        title: itemTitle(item, draftId),
        variants,
        mainImageUrl,
        /** 草稿池可用性判定结论（后端 task_preview 已带出），用于解释 auto 的实际取值。 */
        availability: item.sku_availability ?? null,
        /** 本商品已换图的 SKU 数，用于对照「单条链接最多修改 6 个」的限制。 */
        replacedCount: variants.filter((entry) => entry.overrideValue).length,
      };
    })
    .filter((row) => row.variants.length > 0);

  const visibleRows = rows
    .map((row) => ({
      ...row,
      variants: row.variants.filter((entry) => {
        if (scope === 'missing' && (entry.display || entry.excluded)) return false;
        if (scope === 'replaced' && !entry.overrideValue) return false;
        if (scope === 'excluded' && !entry.excluded) return false;
        if (scope === 'text' && entry.textReview !== 'flagged') return false;
        return true;
      }),
    }))
    .filter((row) => row.variants.length > 0);

  const attrNames = collectAttrNames(rows.map((row) => row.variants.map((entry) => entry.variant)));
  const allEntries = rows.flatMap((row) => row.variants.map((entry) => ({ ...entry, draftId: row.draftId })));
  const totalVariants = allEntries.length;
  const missingVariants = allEntries.filter((entry) => !entry.sourceImage).length;
  const replacedVariants = allEntries.filter((entry) => entry.overrideValue).length;
  const excludedVariants = allEntries.filter((entry) => entry.excluded).length;
  /** 原图检出中文、建议重新锚定规格图的 SKU 数。 */
  const textReviewVariants = allEntries.filter((entry) => entry.textReview === 'flagged').length;
  const active = filterActive(filter);
  const hitEntries = allEntries.filter((entry) => entry.hit);
  const hitVariantCount = hitEntries.length;
  const affected = active ? rows.filter((row) => row.variants.some((entry) => entry.hit)) : rows;
  /** 批量动作作用对象：设置了筛选条件时只作用于命中的 SKU，否则作用于全部。 */
  const batchRefs: VariantRef[] = (active ? hitEntries : allEntries).map((entry) => ({
    draftId: entry.draftId,
    variantKey: entry.key,
  }));

  const selectedRefs: VariantRef[] = [];
  for (const row of rows) {
    for (const entry of row.variants) {
      if (selected.has(`${row.draftId}::${entry.key}`)) {
        selectedRefs.push({ draftId: row.draftId, variantKey: entry.key });
      }
    }
  }

  /**
   * 该 SKU 在导出时实际会写进表格的图，以及它的来源。
   *
   * 与后端 `_dxm_single_export_row` 的取值顺序保持一致：
   * 逐 SKU 人工换图 > main 策略 > auto 策略 > 规格原图（缺失则回退主图）。
   *
   * auto 的判定结论是**草稿级别**（`item.sku_availability`，由后端 task_preview 带出）：
   * 只有「已判定且判定干净、且指纹未失效」才用规格原图，其余情况（含从未判定）
   * 一律回退主图。所以这里能如实显示 auto 的实际结果，不再需要「待判定」兜底。
   */
  const resolveExportImage = (
    mode: VariantImageMode,
    entry: { overrideUrl: string; sourceImage: string },
    mainImageUrl: string,
    availability: { usable_source: boolean; reason: string } | null,
  ): { url: string; kind: 'manual' | 'main' | 'source'; fallbackReason?: string } => {
    if (entry.overrideUrl) return { url: entry.overrideUrl, kind: 'manual' };
    if (mode === 'main') {
      // 主图缺失时后端仍会写规格原图，此处如实反映。
      return mainImageUrl
        ? { url: mainImageUrl, kind: 'main' }
        : { url: entry.sourceImage, kind: 'source' };
    }
    if (mode === 'auto') {
      const usable = Boolean(availability?.usable_source);
      if (usable && entry.sourceImage) return { url: entry.sourceImage, kind: 'source' };
      // 判定不可用 / 从未判定：回退主图（与后端一致）。
      return mainImageUrl
        ? { url: mainImageUrl, kind: 'main', fallbackReason: availability?.reason || 'unknown' }
        : { url: entry.sourceImage, kind: 'source' };
    }
    return { url: entry.sourceImage, kind: 'source' };
  };

  /** 勾选项中是否已有换图 / 已删除：决定勾选栏只显示当前有意义的动作按钮。 */
  const selectedSet = new Set(selected);
  const selectedHasReplacement = allEntries.some(
    (entry) => selectedSet.has(`${entry.draftId}::${entry.key}`) && entry.overrideValue,
  );
  const selectedHasExcluded = allEntries.some(
    (entry) => selectedSet.has(`${entry.draftId}::${entry.key}`) && entry.excluded,
  );

  const toggleSelected = (ref: VariantRef) => {
    const id = `${ref.draftId}::${ref.variantKey}`;
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const VARIANT_MODE_LABELS: Record<VariantImageMode, string> = {
    source: '使用规格原图（可能含中文）',
    main: '全部使用主图替代',
    auto: '按可用性判断自动选择（默认·推荐）',
  };

  /** 模式短标签：逐商品卡片内空间紧张，用短名。 */
  const VARIANT_MODE_SHORT: Record<VariantImageMode, string> = {
    source: '规格原图',
    main: '全用主图',
    auto: '自动',
  };

  /**
   * 应用规格图策略。逐商品与底部批量走同一条路径，保证交互一致：
   * 都需确认、都给出「待保存」提示，避免「点了没反应」。
   */
  const applyModeTo = (mode: VariantImageMode, draftIds: number[], scopeHint: string) => {
    if (draftIds.length === 0) return;
    const label = VARIANT_MODE_LABELS[mode];
    if (!window.confirm(`确定对${scopeHint}执行「${label}」？`)) return;
    onApplyMode(mode, draftIds);
    setNotice(`已对${scopeHint}应用「${label}」，点击预检页「保存预检修改」或「完成预审并导出」后生效。`);
  };

  const applyMode = (mode: VariantImageMode) => {
    applyModeTo(mode, affected.map((row) => row.draftId), ` ${affected.length} 个商品`);
  };

  const excludeRefs = (refs: VariantRef[], message: string) => {
    if (refs.length === 0) return;
    if (!window.confirm(`确定删除选中的 ${refs.length} 个 SKU？导出表格里将不再包含这些规格行。`)) return;
    onExclude(refs);
    setNotice(message);
    setSelected(new Set());
  };

  const restoreSelected = () => {
    if (selectedRefs.length === 0) return;
    onRestore(selectedRefs);
    setNotice(`已恢复 ${selectedRefs.length} 个 SKU 规格。`);
    setSelected(new Set());
  };

  const clearReplacementSelected = () => {
    if (selectedRefs.length === 0) return;
    onSetVariantImage(selectedRefs.map((ref) => ({ ...ref, value: '' })));
    setNotice(`已清除 ${selectedRefs.length} 个 SKU 的替换图，恢复为规格原图。`);
    setSelected(new Set());
  };

  /**
   * 关键词精简：同一关键词在全部商品中只保留最先出现的那条链接里的第一个 SKU，
   * 其余链接（以及该链接内后续重复）命中该关键词的 SKU 整行剔除；
   * 未命中任何关键词的 SKU 保持不动。
   */
  const applyKeywordPrune = () => {
    const keywords = Array.from(new Set(splitKeywords(keywordInput)));
    if (keywords.length === 0) {
      setNotice('请先输入关键词，用逗号或空格分隔，例如：红色 蓝色 黑色。');
      return;
    }

    // 第一轮：为每个关键词确定唯一保留的 SKU（按侧栏商品顺序取第一条命中的链接里的第一个）。
    const keptKeys = new Set<string>();
    const keptNotes: string[] = [];
    const missing: string[] = [];
    for (const keyword of keywords) {
      let found = false;
      for (const row of rows) {
        const match = row.variants.find((entry) => !entry.excluded && entry.label.includes(keyword));
        if (!match) continue;
        keptKeys.add(`${row.draftId}::${match.key}`);
        keptNotes.push(`${keyword} → 「${row.title.slice(0, 20)}」${match.label}`);
        found = true;
        break;
      }
      if (!found) missing.push(keyword);
    }

    // 第二轮：命中关键词、不属于保留项、且尚未删除的 SKU 全部整行剔除。
    const refs: VariantRef[] = [];
    const deletedDrafts = new Set<number>();
    for (const row of rows) {
      for (const entry of row.variants) {
        if (entry.excluded) continue;
        const id = `${row.draftId}::${entry.key}`;
        if (keptKeys.has(id)) continue;
        if (!keywords.some((keyword) => entry.label.includes(keyword))) continue;
        deletedDrafts.add(row.draftId);
        refs.push({ draftId: row.draftId, variantKey: entry.key });
      }
    }

    const tail = [
      missing.length > 0 ? `未找到关键词：${missing.join('、')}（已跳过，相关链接不做处理）。` : '',
      '未命中关键词的 SKU 保持不变。点击「保存预检修改」或「完成预审并导出」后生效。',
    ].filter(Boolean).join('');

    if (refs.length === 0) {
      setNotice(`没有需要删除的 SKU。保留结果：${keptNotes.join('；') || '无'}。${tail}`);
      return;
    }
    const confirmed = window.confirm(
      `将整行删除 ${refs.length} 个 SKU（涉及 ${deletedDrafts.size} 个商品），导出表格里不再包含这些规格行。\n\n`
      + `每个关键词保留一个 SKU：\n${keptNotes.join('\n') || '（无）'}\n`
      + (missing.length > 0 ? `\n未找到关键词：${missing.join('、')}\n` : ''),
    );
    if (!confirmed) return;
    onExclude(refs);
    setNotice(`已删除 ${refs.length} 个重复 SKU（涉及 ${deletedDrafts.size} 个商品）。保留结果：${keptNotes.join('；') || '无'}。${tail}`);
    setSelected(new Set());
  };

  /** 打开换图选择器：按商品分组排队，一次只展示当前商品自己的处理图。 */
  const openPicker = (targets: VariantRef[], label: string) => {
    if (targets.length === 0) return;
    const grouped = new Map<number, VariantRef[]>();
    for (const ref of targets) {
      const list = grouped.get(ref.draftId) ?? [];
      list.push(ref);
      grouped.set(ref.draftId, list);
    }
    const groups: PickerGroup[] = [];
    for (const [draftId, refs] of grouped) {
      const item = items.find((candidate) => draftIdOf(candidate) === draftId);
      groups.push({
        draftId,
        title: item ? itemTitle(item, draftId) : `商品 ${draftId}`,
        targets: refs,
      });
    }
    const first = groups[0];
    const firstItem = items.find((candidate) => draftIdOf(candidate) === first.draftId);
    const options = firstItem ? optionsOf(firstItem) : [];
    if (options.length === 0) {
      setNotice(`「${first.title}」当前没有可用作替换的图片（AI 处理图 / 本地导入 / 尺寸图 / 其他 SKU 规格原图）。`);
      return;
    }
    setPicker({ groups, index: 0, options });
    setNotice(label ? `换图来源：${label}。` : '');
  };

  const applyOption = (option: VariantImageOption) => {
    if (!picker) return;
    const current = picker.groups[picker.index];
    onSetVariantImage(current.targets.map((ref) => ({ ...ref, value: option.value })));
    const nextIndex = picker.index + 1;
    const next = picker.groups[nextIndex];
    if (!next) {
      const total = picker.groups.reduce((sum, group) => sum + group.targets.length, 0);
      setNotice(`已为 ${total} 个 SKU 换上所选图片，保存或导出后生效。`);
      setPicker(null);
      setSelected(new Set());
      return;
    }
    const nextItem = items.find((candidate) => draftIdOf(candidate) === next.draftId);
    const options = nextItem ? optionsOf(nextItem) : [];
    if (options.length === 0) {
      setNotice(`「${next.title}」没有可用作替换的图片，已跳过该商品。`);
      setPicker(null);
      setSelected(new Set());
      return;
    }
    setPicker({ groups: picker.groups, index: nextIndex, options });
    setNotice(`已完成「${current.title}」的 ${current.targets.length} 个 SKU，继续下一个商品。`);
  };

  const runCrop = async (file: File) => {
    if (!crop) return;
    setCropBusy(true);
    setCropError('');
    try {
      const option = await onUploadCrop(crop.draftId, file);
      onSetVariantImage([{ draftId: crop.draftId, variantKey: crop.variantKey, value: option.value }]);
      setNotice(`已用裁剪图替换「${crop.label}」的规格图，保存或导出后生效。`);
      setCrop(null);
    } catch (err) {
      setCropError(err instanceof Error ? err.message : String(err));
    } finally {
      setCropBusy(false);
    }
  };

  /**
   * 打开裁剪弹层：外部跨域图片先经后端图床转存成同源地址，
   * 否则浏览器读不到像素、canvas 无法导出裁剪结果。
   */
  const openCrop = async (target: VariantRef, label: string, option: VariantImageOption) => {
    const source = option.cropSrc || option.thumb;
    if (!source) {
      setNotice(`「${label}」这张候选图没有可用地址，无法裁剪。`);
      return;
    }
    setCropPreparing(true);
    setNotice('正在通过图床转存这张图片，请稍候…');
    try {
      const sameOrigin = await onPrepareCropSrc(target.draftId, source);
      setCropError('');
      setCrop({
        draftId: target.draftId,
        variantKey: target.variantKey,
        label,
        option: { ...option, cropSrc: sameOrigin, thumb: option.thumb || sameOrigin },
      });
      setPicker(null);
      setNotice('');
    } catch (err) {
      setNotice(err instanceof Error ? err.message : String(err));
    } finally {
      setCropPreparing(false);
    }
  };

  const currentGroup = picker ? picker.groups[picker.index] : null;
  const optionGroups = picker
    ? Array.from(
        picker.options.reduce((groups, option) => {
          const list = groups.get(option.group) ?? [];
          list.push(option);
          groups.set(option.group, list);
          return groups;
        }, new Map<string, VariantImageOption[]>()),
      )
    : [];

  return (
    <div className="verify-drawer-root">
      <div className="verify-drawer-mask" onClick={onClose} />
      <section className="sku-batch-panel" role="dialog" aria-modal="true" aria-label="管理 SKU 规格图">
        <header className="sku-batch-head">
          <div>
            <h2>管理 SKU 规格图</h2>
            <p>
              共 {rows.length} 个商品 · {totalVariants} 个 SKU；有规格图 {totalVariants - missingVariants} 个，
              无图 {missingVariants} 个（导出时回退商品主图）；已换图 {replacedVariants} 个，
              已删除 {excludedVariants} 个。
            </p>
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
                placeholder={filter.attrName ? '如：白 / L / 30*20' : '先选择属性名'}
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
            <label>
              <span>显示范围</span>
              <select value={scope} onChange={(e) => setScope(e.target.value as Scope)}>
                {SCOPE_LABELS.map((entry) => (
                  <option key={entry.value} value={entry.value}>{entry.label}</option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="btn-mini"
              onClick={() => { setFilter(EMPTY_SKU_FILTER); setScope('all'); }}
              disabled={!active && scope === 'all'}
            >清空条件</button>
          </div>

          <div className="sku-batch-summary">
            {active
              ? <>当前命中 <strong>{hitVariantCount}</strong> 个 SKU（{affected.length} 个商品），批量动作只作用于命中项。</>
              : '可按条件筛选 SKU；未设置条件时批量动作作用于全部商品。'}
            {' '}提示：点击图片可放大查看；「换图」可从处理之后的图片里挑选或框选裁剪。
          </div>

          {textReviewVariants > 0 && (
            <div className="sku-batch-review">
              检测到 <strong>{textReviewVariants}</strong> 个 SKU 原图带中文（尺寸/款式等标注），
              这类图直接当规格图容易出错，建议重新锚定后再换图。
              <button type="button" className="btn-mini" onClick={() => setScope('text')}>只看待审核</button>
            </div>
          )}

          <div className="sku-batch-limit">
            注意：单条链接最多允许修改 <strong>{MAX_EDITED_VARIANT_IMAGES_PER_LINK}</strong> 个 SKU 图片，
            请优先改需要换图的 SKU（裁剪图也算一次修改）。
          </div>

          <div className="sku-batch-actions">
            <button
              type="button"
              className="btn-mini primary"
              onClick={() => openPicker(batchRefs, active ? '命中的 SKU' : '全部 SKU')}
              disabled={batchRefs.length === 0}
            >批量换图（{batchRefs.length}）</button>
            <button
              type="button"
              className="btn-mini"
              onClick={() => excludeRefs(batchRefs, `已删除 ${batchRefs.length} 个 SKU 规格，导出时不会出现在表格中。`)}
              disabled={batchRefs.length === 0}
            >批量删除（{batchRefs.length}）</button>
            <span className="sku-batch-hint">换图按商品逐个进行，每个商品使用它自己的处理图。</span>
          </div>

          <div className="sku-batch-keyword">
            <label>
              <span>关键词精简</span>
              <input
                placeholder="如：红色,蓝色,黑色（逗号或空格分隔，可多个）"
                value={keywordInput}
                onChange={(e) => setKeywordInput(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="btn-mini"
              onClick={applyKeywordPrune}
              disabled={splitKeywords(keywordInput).length === 0}
            >每个关键词只留 1 个 SKU</button>
            <span className="sku-batch-hint">
              同一关键词在全部商品中只保留最先出现的那条链接里的第一个 SKU，其余命中该关键词的 SKU 整行删除；未命中关键词的 SKU 保持不动。
            </span>
          </div>

          {selectedRefs.length > 0 && (
            <div className="sku-batch-selection">
              <span>已勾选 <strong>{selectedRefs.length}</strong> 个 SKU</span>
              <div className="sku-batch-selection-actions">
                <button type="button" className="btn-mini" onClick={() => openPicker(selectedRefs, '勾选的 SKU')}>换图</button>
                {selectedHasReplacement && (
                  <button type="button" className="btn-mini" onClick={clearReplacementSelected}>清除换图</button>
                )}
                {selectedHasExcluded ? (
                  <button type="button" className="btn-mini" onClick={restoreSelected}>恢复</button>
                ) : (
                  <button type="button" className="btn-mini" onClick={() => excludeRefs(selectedRefs, `已删除 ${selectedRefs.length} 个 SKU 规格，导出时不会出现在表格中。`)}>删除规格</button>
                )}
                <button type="button" className="btn-mini" onClick={() => setSelected(new Set())}>取消勾选</button>
              </div>
            </div>
          )}

          <div className="precheck-sku-list">
            {visibleRows.length === 0 && (
              <div className="precheck-sku-empty">当前显示范围内没有 SKU，可调整上方筛选条件。</div>
            )}
            {visibleRows.map((row) => {
              const mode = modeOf(row.draftId);
              const hitCount = row.variants.filter((entry) => entry.hit).length;
              return (
                <section key={row.draftId} className="sku-batch-draft">
                  <header>
                    <div>
                      <strong title={row.title}>{row.title.slice(0, 60)}</strong>
                      <small>
                        {`当前：${VARIANT_MODE_LABELS[mode]}`}
                        {modeSavedOf(row.draftId)
                          ? <span className="sku-mode-state is-saved" title="该策略已保存到草稿">已生效</span>
                          : <span className="sku-mode-state is-dirty" title="仅本地编辑，需点「保存预检修改」或「完成预审并导出」才生效">待保存</span>}
                        {' · '}
                        <span className={row.replacedCount > MAX_EDITED_VARIANT_IMAGES_PER_LINK ? 'is-over' : undefined}>
                          已换图 {row.replacedCount} / {MAX_EDITED_VARIANT_IMAGES_PER_LINK}
                        </span>
                      </small>
                    </div>
                    <span className="precheck-sku-headright">
                      {row.mainImageUrl && (
                        <img
                          className="precheck-sku-main"
                          src={row.mainImageUrl}
                          alt="商品主图"
                          referrerPolicy="no-referrer"
                          onClick={() => onPreview(row.mainImageUrl)}
                          title="点击放大商品主图"
                        />
                      )}
                      {active && hitCount > 0 ? <>命中 <b>{hitCount}</b> / </> : null}
                      <b>{row.variants.length}</b> 个 SKU
                    </span>
                  </header>
                  <div className="sku-batch-draft-actions">
                    <div className="sku-mode-seg" role="group" aria-label="本商品规格图策略">
                      {(Object.keys(VARIANT_MODE_SHORT) as VariantImageMode[]).map((option) => (
                        <button
                          key={option}
                          type="button"
                          className={`sku-mode-seg-btn${mode === option ? ' is-active' : ''}`}
                          aria-pressed={mode === option}
                          title={VARIANT_MODE_LABELS[option]}
                          onClick={() => applyModeTo(option, [row.draftId], `「${row.title.slice(0, 20)}」`)}
                        >{VARIANT_MODE_SHORT[option]}</button>
                      ))}
                    </div>
                    <button
                      type="button"
                      className="btn-mini"
                      onClick={() => openPicker(
                        row.variants.map((entry) => ({ draftId: row.draftId, variantKey: entry.key })),
                        row.title,
                      )}
                    >批量换图</button>
                  </div>
                  {mode === 'auto' && !row.availability?.usable_source && (
                    <p className="sku-mode-hint is-warn">
                      {row.availability?.reason === 'media_unavailable'
                        ? '本商品规格原图素材不可用，自动模式下导出将全部回退主图。'
                        : row.availability?.reason === 'scope_relaxed'
                          ? '本商品当前按「宽松口径」判定，不足以直接使用规格原图，自动模式下导出将全部回退主图。'
                          : row.availability?.reason === 'never_judged'
                            ? '本商品尚未做过「SKU 规格图可用性判断」，自动模式下无法确认规格原图是否可用，导出将全部回退主图（即 AI 处理后的商品主图，不含中文）。'
                            : row.availability?.judged
                              ? '本商品判定结论为「不可用」，自动模式下导出将全部回退主图。'
                              : '当前无法确认规格原图是否可用，自动模式下导出将全部回退主图。'}
                      {row.availability?.reason === 'never_judged' && ' 可先执行一次可用性判断，判定干净后会自动改用规格原图。'}
                    </p>
                  )}
                  <div className="precheck-sku-grid">
                    {row.variants.map((entry) => {
                      const id = `${row.draftId}::${entry.key}`;
                      const checked = selected.has(id);
                      /** 该 SKU 当前策略下实际会导出的图（与后端取值口径一致）。 */
                      const resolved = resolveExportImage(mode, entry, row.mainImageUrl, row.availability);
                      const effective = { ...resolved, display: resolved.url };
                      return (
                        <div
                          key={id}
                          className={`precheck-sku-card${entry.hit && active ? ' is-hit' : ''}${entry.excluded ? ' is-excluded' : ''}`}
                        >
                          <label className="precheck-sku-check" title="勾选后可批量操作">
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() => toggleSelected({ draftId: row.draftId, variantKey: entry.key })}
                            />
                          </label>
                          {effective.display
                            ? (
                              <button
                                type="button"
                                className={`precheck-sku-thumb is-from-${effective.kind}`}
                                onClick={() => onPreview(effective.display)}
                                title={
                                  effective.kind === 'manual' ? '点击放大（人工换图）'
                                    : effective.kind === 'main' ? '点击放大（当前策略：商品主图）'
                                      : '点击放大（规格原图）'
                                }
                              >
                                <img
                                  className="verify-sku-image"
                                  src={effective.display}
                                  alt={`${entry.label} 导出图`}
                                  referrerPolicy="no-referrer"
                                />
                                {effective.kind === 'main' && (
                                  <span className="precheck-sku-thumb-flag tone-main" aria-hidden="true">主图</span>
                                )}
                              </button>
                            )
                            : (
                              <button
                                type="button"
                                className="precheck-sku-thumb"
                                onClick={() => openPicker([{ draftId: row.draftId, variantKey: entry.key }], entry.label)}
                                title="暂无规格图，点击选择替换图"
                              >
                                <span className="verify-sku-image-placeholder" aria-label="无规格图">无图</span>
                              </button>
                            )}
                          <span className="precheck-sku-label" title={entry.label}>{entry.label || '—'}</span>
                          <div className="precheck-sku-badges">
                            {effective.kind === 'manual' && <span className="precheck-sku-badge tone-replaced">已换图</span>}
                            {effective.kind === 'main' && (
                              <span className="precheck-sku-badge tone-main" title="当前策略下该 SKU 导出时使用商品主图">
                                导出用主图
                              </span>
                            )}
                            {entry.excluded && <span className="precheck-sku-badge tone-excluded">已删除</span>}
                            {entry.textReview === 'flagged' && (
                              <span className="precheck-sku-badge tone-text" title="该 SKU 原图检出中文，建议重新锚定后再换图">
                                待审核原图
                              </span>
                            )}
                          </div>
                          <div className="precheck-sku-card-actions">
                            <button
                              type="button"
                              className="btn-mini"
                              onClick={() => openPicker([{ draftId: row.draftId, variantKey: entry.key }], entry.label)}
                            >换图</button>
                            {entry.excluded
                              ? (
                                <button
                                  type="button"
                                  className="btn-mini"
                                  onClick={() => {
                                    onRestore([{ draftId: row.draftId, variantKey: entry.key }]);
                                    setNotice(`已恢复「${entry.label}」。`);
                                  }}
                                >恢复</button>
                              )
                              : (
                                <button
                                  type="button"
                                  className="btn-mini"
                                  onClick={() => excludeRefs(
                                    [{ draftId: row.draftId, variantKey: entry.key }],
                                    `已删除「${entry.label}」这一 SKU 规格。`,
                                  )}
                                >删除</button>
                              )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </section>
              );
            })}
          </div>

          {notice && <div className="verify-message">{notice}</div>}
        </div>

        <footer className="sku-batch-foot">
          <button type="button" onClick={onClose}>关闭</button>
          <button
            type="button"
            className="primary"
            onClick={() => applyMode('source')}
            disabled={affected.length === 0}
          >使用规格原图（{affected.length}）</button>
          <button
            type="button"
            onClick={() => applyMode('main')}
            disabled={affected.length === 0}
          >全部使用主图替代（{affected.length}）</button>
          <button
            type="button"
            onClick={() => applyMode('auto')}
            disabled={affected.length === 0}
            title="规格图已通过「SKU 可用性判断」的用规格原图，其余回退商品主图"
          >按可用性自动选择（{affected.length}）</button>
        </footer>

        {picker && currentGroup && (
          <div className="precheck-sku-picker-root" role="dialog" aria-modal="true" aria-label="选择替换图">
            <div className="precheck-sku-picker-mask" onClick={() => setPicker(null)} />
            <section className="precheck-sku-picker">
              <header>
                <div>
                  <h4>选择替换图</h4>
                  <p>
                    {currentGroup.title.slice(0, 40)} · 作用于 {currentGroup.targets.length} 个 SKU
                    {picker.groups.length > 1 ? `（第 ${picker.index + 1} / ${picker.groups.length} 个商品）` : ''}
                    ；选中后会自动进入下一个商品。
                  </p>
                </div>
                <button type="button" className="verify-drawer-close" onClick={() => setPicker(null)} aria-label="关闭">×</button>
              </header>
              <div className="precheck-sku-picker-body">
                {optionGroups.map(([group, options]) => (
                  <div key={group} className="precheck-sku-picker-group">
                    <h5>{group}</h5>
                    <div className="precheck-sku-picker-grid">
                      {options.map((option) => (
                        <div key={option.value} className="precheck-sku-picker-item">
                          <button
                            type="button"
                            className="precheck-sku-picker-thumb"
                            onClick={() => onPreview(option.thumb || option.cropSrc)}
                            title="点击放大查看"
                          >
                            <img src={option.thumb || option.cropSrc} alt={option.label} referrerPolicy="no-referrer" />
                          </button>
                          <span className="precheck-sku-picker-label" title={option.label}>{option.label}</span>
                          <div className="precheck-sku-picker-actions">
                            <button type="button" className="btn-mini primary" onClick={() => applyOption(option)}>
                              直接使用{currentGroup.targets.length > 1 ? `（${currentGroup.targets.length} 个）` : ''}
                            </button>
                            {currentGroup.targets.length === 1 && (
                              <button
                                type="button"
                                className="btn-mini"
                                disabled={cropPreparing}
                                onClick={() => {
                                  const target = currentGroup.targets[0];
                                  void openCrop(target, currentGroup.title, option);
                                }}
                              >{cropPreparing ? '转存中…' : '框选裁剪'}</button>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
                {optionGroups.length === 0 && (
                  <div className="precheck-sku-empty">该商品当前没有可用作替换的图片。</div>
                )}
              </div>
            </section>
          </div>
        )}

        {crop && (
          <PrecheckSkuImageCropper
            url={crop.option.cropSrc || crop.option.thumb}
            label={crop.label}
            busy={cropBusy}
            error={cropError}
            onCancel={() => { setCrop(null); setCropError(''); }}
            onConfirm={(file) => { void runCrop(file); }}
          />
        )}
      </section>
    </div>
  );
}
