import {
  SIMPLE_PRESETS,
  type PanelSelection,
  type PhotoSelection,
} from './imageCardOptions';

/**
 * 生图组合的本地记忆：自动保存上一次选择，并保留最近几次用于「历史预设」。
 *
 * 只存浏览器本地（localStorage），不参与计费、不上传；解析失败一律丢弃，
 * 保证版本升级或脏数据不会卡住面板。
 */

const LAST_KEY = 'pp.image-combo.last.v1';
const HISTORY_KEY = 'pp.image-combo.history.v1';
const HISTORY_LIMIT = 6;

export type ComboMode = 'simple' | 'pro';

export type ComboSnapshot = {
  id: string;
  label: string;
  savedAt: number;
  mode: ComboMode;
  presetId: string;
  category: string;
  layers: PhotoSelection;
  panels: PanelSelection[];
};

function clonePanels(panels: PanelSelection[]): PanelSelection[] {
  return panels.map((panel) => ({
    ...panel,
    props: panel.props ? [...panel.props] : null,
  }));
}

function isPanelSelection(value: unknown): value is PanelSelection {
  if (!value || typeof value !== 'object') return false;
  const panel = value as Record<string, unknown>;
  return (
    typeof panel.frame === 'string'
    && typeof panel.light === 'string'
    && typeof panel.tone === 'string'
    && typeof panel.detail === 'string'
    && typeof panel.style === 'string'
    && typeof panel.scene === 'string'
  );
}

function normalizeSnapshot(raw: unknown): ComboSnapshot | null {
  if (!raw || typeof raw !== 'object') return null;
  const value = raw as Record<string, unknown>;
  const layers = value.layers as Record<string, unknown> | undefined;
  const panels = value.panels;
  if (
    !layers
    || typeof layers.frame !== 'string'
    || typeof layers.light !== 'string'
    || typeof layers.tone !== 'string'
    || typeof layers.detail !== 'string'
    || typeof layers.style !== 'string'
    || !Array.isArray(panels)
    || panels.length !== 4
    || !panels.every(isPanelSelection)
  ) {
    return null;
  }
  return {
    id: typeof value.id === 'string' ? value.id : `combo-${Date.now()}`,
    label: typeof value.label === 'string' ? value.label : '自定义组合',
    savedAt: typeof value.savedAt === 'number' ? value.savedAt : Date.now(),
    mode: value.mode === 'pro' ? 'pro' : 'simple',
    presetId: typeof value.presetId === 'string' ? value.presetId : 'auto',
    category: typeof value.category === 'string' ? value.category : 'auto',
    layers: {
      frame: layers.frame,
      light: layers.light,
      tone: layers.tone,
      detail: layers.detail,
      style: layers.style,
    },
    panels: clonePanels(panels as PanelSelection[]),
  };
}

function readJson(key: string): unknown {
  try {
    const text = window.localStorage.getItem(key);
    return text ? JSON.parse(text) : null;
  } catch {
    return null;
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // 隐私模式 / 配额满：记忆功能失效不影响正常使用。
  }
}

export function presetLabel(presetId: string): string {
  return SIMPLE_PRESETS.find((preset) => preset.id === presetId)?.name ?? '自定义组合';
}

export function buildComboSnapshot(input: {
  mode: ComboMode;
  presetId: string;
  category: string;
  layers: PhotoSelection;
  panels: PanelSelection[];
}): ComboSnapshot {
  const presetId = input.presetId || 'auto';
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    label: presetLabel(presetId),
    savedAt: Date.now(),
    mode: input.mode,
    presetId,
    category: input.category || 'auto',
    layers: { ...input.layers },
    panels: clonePanels(input.panels),
  };
}

/** 历史去重键：同一套组合只保留最近一次。 */
function snapshotKey(snapshot: ComboSnapshot): string {
  return JSON.stringify({
    mode: snapshot.mode,
    presetId: snapshot.presetId,
    category: snapshot.category,
    layers: snapshot.layers,
    panels: snapshot.panels.map((panel) => ({ ...panel, props: panel.props ?? null })),
  });
}

export function loadLastCombo(): ComboSnapshot | null {
  return normalizeSnapshot(readJson(LAST_KEY));
}

export function saveLastCombo(snapshot: ComboSnapshot): void {
  writeJson(LAST_KEY, snapshot);
}

export function loadComboHistory(): ComboSnapshot[] {
  const raw = readJson(HISTORY_KEY);
  if (!Array.isArray(raw)) return [];
  return raw
    .map((item) => normalizeSnapshot(item))
    .filter((item): item is ComboSnapshot => item !== null)
    .slice(0, HISTORY_LIMIT);
}

/** 把一个组合写入历史（去重 + 截断），返回最新历史列表。 */
export function pushComboHistory(snapshot: ComboSnapshot): ComboSnapshot[] {
  const key = snapshotKey(snapshot);
  const next = [
    snapshot,
    ...loadComboHistory().filter((item) => snapshotKey(item) !== key),
  ].slice(0, HISTORY_LIMIT);
  writeJson(HISTORY_KEY, next);
  return next;
}

export function cloneComboPanels(panels: PanelSelection[]): PanelSelection[] {
  return clonePanels(panels);
}
