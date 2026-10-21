import { useEffect, useMemo, useState } from 'react';
import { ppRequest } from '../api/client';
import { productProcessingApiContext } from '../api/context';
import {
  AUX_PROP_OPTIONS,
  AUX_SCENE_OPTIONS,
  DEFAULT_PANEL_SELECTIONS,
  DEFAULT_PHOTO_SELECTION,
  IMAGE_CATEGORY_OPTIONS,
  PANEL_META,
  PHOTO_CARD_OPTIONS,
  PHOTO_LAYERS,
  SIMPLE_PRESETS,
  findSimplePreset,
  imageCategoryLabel,
  type ImageCategoryId,
  type PanelSelection,
  type PhotoLayerKey,
  type PhotoSelection,
} from '../data/imageCardOptions';
import type { ComboSnapshot } from '../data/imageComboHistory';
import '../styles/ImageComboPicker.css';

const API_BASE = '/api/product-processing';

/** 历史预设的时间戳只显示到分钟，够区分批次即可。 */
function formatSavedAt(timestamp: number): string {
  const date = new Date(timestamp);
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

type ImageMode = 'simple' | 'pro';

type PreviewResponse = {
  prompt: string;
  word_count: number;
  over_budget: boolean;
  category_id: string;
  selections: Record<string, unknown>;
  used_defaults: string[];
  panels?: string[];
};

type Props = {
  mode: ImageMode;
  onModeChange: (mode: ImageMode) => void;
  category: ImageCategoryId;
  onCategoryChange: (category: ImageCategoryId) => void;
  selection: PhotoSelection;
  onSelectionChange: (layer: PhotoLayerKey, value: string) => void;
  /** 小白模板会同时改写五层参数与四格逐格卡片。 */
  onApplyPreset: (
    selection: PhotoSelection,
    panels?: PanelSelection[],
    presetId?: string,
  ) => void;
  panels?: PanelSelection[];
  onPanelsChange?: (panels: PanelSelection[]) => void;
  /** 随机模板：开启后本批次每条链接随机分配一个效果模板（四格卡片同步切换）。 */
  randomPreset?: boolean;
  onRandomPresetChange?: (random: boolean) => void;
  /** 最近使用过的生图组合（本地记忆），点击一键回填。 */
  history?: ComboSnapshot[];
  onApplyHistory?: (snapshot: ComboSnapshot) => void;
};

function cloneDefaultPanels(): PanelSelection[] {
  return DEFAULT_PANEL_SELECTIONS.map((panel) => ({
    ...panel,
    props: panel.props ? [...panel.props] : null,
  }));
}

export function ImageComboPicker({
  mode,
  onModeChange,
  category,
  onCategoryChange,
  selection,
  onSelectionChange,
  onApplyPreset,
  panels: panelsProp,
  onPanelsChange,
  randomPreset = false,
  onRandomPresetChange,
  history = [],
  onApplyHistory,
}: Props) {
  const ctx = useMemo(() => productProcessingApiContext(), []);
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [previewError, setPreviewError] = useState('');
  const [previewBusy, setPreviewBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [localPanels, setLocalPanels] = useState<PanelSelection[]>(cloneDefaultPanels);
  const [panelIndex, setPanelIndex] = useState(0);
  const panels = panelsProp && panelsProp.length ? panelsProp : localPanels;
  const panelsKey = JSON.stringify(panels);
  const activePanel = (panels[panelIndex] ?? panels[0] ?? DEFAULT_PANEL_SELECTIONS[0]) as PanelSelection;

  const activePreset = useMemo(
    () => findSimplePreset(selection, panels),
    [selection, panelsKey],
  );
  const allAuto = useMemo(
    () => PHOTO_LAYERS.every((layer) => selection[layer.key] === 'auto'),
    [selection],
  );
  const selectedCategory = IMAGE_CATEGORY_OPTIONS.find((option) => option.id === category);

  const updatePanels = (next: PanelSelection[]) => {
    if (onPanelsChange) onPanelsChange(next);
    else setLocalPanels(next);
  };

  const updateActivePanel = (patch: Partial<PanelSelection>) => {
    updatePanels(
      panels.map((panel, index) =>
        index === panelIndex ? { ...panel, ...patch } : panel,
      ),
    );
  };

  const togglePanelProp = (propId: string) => {
    const current = activePanel.props ?? [];
    const next = current.includes(propId)
      ? current.filter((id) => id !== propId)
      : current.length >= 3
        ? current
        : [...current, propId];
    updateActivePanel({ props: next });
  };

  const resetActivePanel = () => {
    const fallback = DEFAULT_PANEL_SELECTIONS[panelIndex] ?? DEFAULT_PANEL_SELECTIONS[0];
    updateActivePanel({ ...fallback, props: fallback.props ? [...fallback.props] : null });
  };

  useEffect(() => {
    if (mode !== 'pro') return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      setPreviewBusy(true);
      try {
        const data = await ppRequest<PreviewResponse>(
          ctx,
          `${API_BASE}/engine/image-prompt-preview`,
          {
            body: {
              category_id: category,
              frame: selection.frame,
              light: selection.light,
              tone: selection.tone,
              detail: selection.detail,
              style: selection.style,
              preset_id: activePreset?.id ?? 'auto',
              panels: panels.map((panel) => ({
                ...panel,
                white_hero: panel.whiteHero,
                props: panel.props ?? undefined,
              })),
            },
          },
        );
        if (!cancelled) {
          setPreview(data);
          setPreviewError('');
        }
      } catch (err) {
        if (!cancelled) {
          setPreview(null);
          setPreviewError(err instanceof Error ? err.message : '提示词预览失败');
        }
      } finally {
        if (!cancelled) setPreviewBusy(false);
      }
    }, 220);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [mode, category, selection.frame, selection.light, selection.tone, selection.detail, selection.style, activePreset?.id, panelsKey, ctx]);

  const copyPrompt = async () => {
    if (!preview?.prompt) return;
    try {
      await navigator.clipboard.writeText(preview.prompt);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };

  return (
    <section className="image-combo-panel" aria-label="批次生图组合">
      <header className="image-combo-head">
        <div>
          <h3>生图组合</h3>
          <p>先按标题分类，再用五层卡片组装成每条提示词；普通用户选大方向，专业用户可逐层调参。</p>
        </div>
        <div className="image-mode-switch" role="group" aria-label="模式切换">
          <button
            type="button"
            className={mode === 'simple' ? 'is-active' : ''}
            onClick={() => onModeChange('simple')}
          >
            小白模式
          </button>
          <button
            type="button"
            className={mode === 'pro' ? 'is-active' : ''}
            onClick={() => onModeChange('pro')}
          >
            导演模式
          </button>
        </div>
      </header>

      {history.length > 0 && (
        <div className="image-preset-history">
          <span className="image-preset-history-label">最近使用</span>
          {history.map((item) => (
            <button
              key={item.id}
              type="button"
              className="image-preset-history-chip"
              onClick={() => onApplyHistory?.(item)}
              title={`${item.label} · ${formatSavedAt(item.savedAt)} · 点击回填这套组合`}
            >
              <strong>{item.label}</strong>
              <em>{formatSavedAt(item.savedAt)}</em>
            </button>
          ))}
        </div>
      )}

      {mode === 'simple' ? (
        <>
          <div className="image-combo-hint">
            <strong>普通用户</strong>
            选一个最终效果即可，系统会自动套用该效果的五层卡片；不选也可以，默认按品类自动组合。
          </div>
          <div className="image-preset-toolbar">
            <button
              type="button"
              className={`image-preset-random ${randomPreset ? 'is-active' : ''}`}
              onClick={() => onRandomPresetChange?.(!randomPreset)}
              aria-pressed={randomPreset}
              title="开启后本批次每条链接随机分配一个效果模板（含对应四格卡片），同一链接重跑仍是同一模板"
            >
              {randomPreset ? '✓ 随机模板已开启' : '随机模板'}
            </button>
            <small>
              {randomPreset
                ? '本批次每条链接随机分配到不同效果模板，四格卡片同步切换；下面点选暂不生效。'
                : '想让一批链接风格不重样，可以开启随机模板。'}
            </small>
          </div>
          <div className="image-preset-grid">
            {SIMPLE_PRESETS.map((preset) => {
              const active = !randomPreset && activePreset?.id === preset.id;
              return (
                <button
                  key={preset.id}
                  type="button"
                  className={`image-preset-card ${active ? 'is-active' : ''}`}
                  onClick={() => onApplyPreset(preset.layers, preset.panels, preset.id)}
                  aria-pressed={active}
                >
                  <span className="image-preset-media">
                    {preset.image ? (
                      <img src={preset.image} alt={preset.name} loading="lazy" />
                    ) : (
                      <span className="image-preset-placeholder" style={{ background: preset.swatch }}>
                        效果图待替换
                      </span>
                    )}
                    <span className="image-preset-check">{active ? '✓' : ''}</span>
                  </span>
                  <span className="image-preset-copy">
                    <strong>{preset.name}</strong>
                    <em>{preset.tagline}</em>
                    <span className="image-preset-signature">{preset.signature}</span>
                    <small>{preset.description}</small>
                  </span>
                </button>
              );
            })}
          </div>
          {!activePreset && (
            <p className="image-combo-note">
              {allAuto
                ? '未选择时按品类默认自动组合；也可以点一个效果卡。'
                : '当前是自定义组合，切到「导演模式」可以查看和继续调整五层参数。'}
            </p>
          )}
        </>
      ) : (
        <>
          <div className="image-combo-pro-top">
            <label className="image-combo-field">
              <span>批次品类组合</span>
              <select
                value={category}
                onChange={(event) => onCategoryChange(event.target.value as ImageCategoryId)}
              >
                {IMAGE_CATEGORY_OPTIONS.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </select>
              <small>
                {category === 'auto'
                  ? '每个链接按标题分类；下面是通用兜底预览'
                  : `整批统一使用；${selectedCategory?.description ?? ''}`}
              </small>
            </label>
            <button
              type="button"
              className="image-combo-reset"
              onClick={() => onApplyPreset(DEFAULT_PHOTO_SELECTION, DEFAULT_PANEL_SELECTIONS, 'auto')}
            >
              恢复自动
            </button>
          </div>

          <div className="image-layer-list">
            {PHOTO_LAYERS.map((layer) => (
              <label className="image-layer-row" key={layer.key}>
                <span className="image-layer-copy">
                  <strong>{layer.label}</strong>
                  <small>{layer.description}</small>
                </span>
                <select
                  value={selection[layer.key]}
                  onChange={(event) => onSelectionChange(layer.key, event.target.value)}
                >
                  <option value="auto">自动（品类推荐）</option>
                  {PHOTO_CARD_OPTIONS[layer.key].map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.label} · {option.description}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          </div>

          <div className="image-panel-editor">
            <div className="image-panel-editor-head">
              <div>
                <strong>四格逐格卡片</strong>
                <p>每一格单独指定场景、道具和五层；auto 继承上面的全局默认。</p>
              </div>
              <button type="button" onClick={resetActivePanel}>
                恢复本格默认
              </button>
            </div>

            <div className="image-panel-tabs">
              {PANEL_META.map((meta, index) => (
                <button
                  key={meta.label}
                  type="button"
                  className={panelIndex === index ? 'is-active' : ''}
                  onClick={() => setPanelIndex(index)}
                >
                  <strong>{meta.label}</strong>
                  <small>{meta.role}</small>
                </button>
              ))}
            </div>

            <p className="image-panel-note">{PANEL_META[panelIndex]?.note}</p>

            {panelIndex === 0 && (
              <label className="image-panel-white-hero">
                <input
                  type="checkbox"
                  checked={activePanel.whiteHero}
                  onChange={(event) =>
                    updateActivePanel({ whiteHero: event.target.checked })
                  }
                />
                <span>保持纯白底、零道具（平台主图要求）</span>
              </label>
            )}

            <div className="image-panel-grid">
              <label className="image-combo-field">
                <span>本格场景 / 台面</span>
                <select
                  value={activePanel.scene}
                  onChange={(event) =>
                    updateActivePanel({ scene: event.target.value })
                  }
                  disabled={panelIndex === 0 && activePanel.whiteHero}
                >
                  {AUX_SCENE_OPTIONS.map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.label} · {option.description}
                    </option>
                  ))}
                </select>
              </label>
              {PHOTO_LAYERS.map((layer) => (
                <label className="image-combo-field" key={layer.key}>
                  <span>{layer.label}</span>
                  <select
                    value={activePanel[layer.key]}
                    onChange={(event) =>
                      updateActivePanel({
                        [layer.key]: event.target.value,
                      } as Partial<PanelSelection>)
                    }
                  >
                    <option value="auto">auto（继承全局）</option>
                    {PHOTO_CARD_OPTIONS[layer.key].map((option) => (
                      <option key={option.id} value={option.id}>
                        {option.label} · {option.description}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
            </div>

            <div className="image-panel-props">
              <div className="image-panel-props-head">
                <strong>辅助道具</strong>
                <span>
                  {activePanel.props === null
                    ? '按品类默认'
                    : `${activePanel.props.length} / 3`}
                </span>
              </div>
              <div className="image-panel-prop-grid">
                {AUX_PROP_OPTIONS.map((option) => {
                  const selected = (activePanel.props ?? []).includes(option.id);
                  return (
                    <button
                      key={option.id}
                      type="button"
                      className={selected ? 'is-active' : ''}
                      onClick={() => togglePanelProp(option.id)}
                      disabled={panelIndex === 0 && activePanel.whiteHero}
                    >
                      {option.label}
                    </button>
                  );
                })}
              </div>
              <p className="image-combo-note">
                这些卡片会作为提示词参与 AI 生图；道具只作展示搭配，不随商品出售。
              </p>
            </div>
          </div>

          <div className="image-prompt-preview">
            <div className="image-prompt-preview-head">
              <strong>效果提示词</strong>
              <span className={preview?.over_budget ? 'is-warning' : ''}>
                {previewBusy
                  ? '组装中…'
                  : preview
                    ? `${preview.word_count} 词${preview.over_budget ? ' · 略超预算' : ''}`
                    : '等待组装'}
              </span>
              <button type="button" onClick={() => void copyPrompt()} disabled={!preview?.prompt}>
                {copied ? '已复制' : '复制'}
              </button>
            </div>
            <textarea
              readOnly
              value={preview?.prompt ?? ''}
              placeholder="选择五层卡片和四格逐格卡片后，这里会实时显示最终单行提示词"
              rows={8}
            />
            {previewError && <p className="image-prompt-error">{previewError}</p>}
            {preview?.used_defaults?.length ? (
              <p className="image-prompt-note">
                自动使用品类默认：
                {preview.used_defaults
                  .map((layer) => {
                    const [panel, key] = layer.split(':');
                    const label = key
                      ? PHOTO_LAYERS.find((item) => item.key === key)?.label ?? key
                      : PHOTO_LAYERS.find((item) => item.key === layer)?.label ?? layer;
                    return key ? `${panel} ${label}` : label;
                  })
                  .join(' / ')}
              </p>
            ) : null}
          </div>
        </>
      )}

      <p className="image-combo-summary">
        当前：{imageCategoryLabel(category)}
        <span> · </span>
        {allAuto ? '自动组合（按品类默认）' : activePreset ? activePreset.name : '自定义组合'}
        <em>
          {mode === 'simple'
            ? '普通用户只选大方向；需要细调时切到导演模式。'
            : `${PANEL_META[panelIndex]?.label ?? '当前格'} · 场景=${
                AUX_SCENE_OPTIONS.find((option) => option.id === activePanel.scene)?.label ?? '自动'
              } · 道具=${
                activePanel.props === null
                  ? '按品类默认'
                  : activePanel.props.length
                    ? activePanel.props
                        .map(
                          (id) =>
                            AUX_PROP_OPTIONS.find((option) => option.id === id)?.label ?? id,
                        )
                        .join(' / ')
                    : '无'
              }`}
        </em>
      </p>
    </section>
  );
}

export default ImageComboPicker;
