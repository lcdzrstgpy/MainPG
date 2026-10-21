import { useEffect, useSyncExternalStore } from "react";

import { getClipForgeDefaults } from "../api/endpoints/settings";
import {
  SETTINGS_STORAGE_KEY,
  configuredDefaultsPatch,
  readSettingsFrom,
  writeSettingsTo,
  type GenAspectRatio,
  type GenResolution,
  type ImageGenParams,
  type LLMSetting,
  type ProviderSetting,
  type SettingsState,
  type SettingsStorage,
  type SettingsUiMode,
  type TTSSetting,
  type VideoGenParams,
} from "./settingsData";

/**
 * 设置 store：localStorage + useSyncExternalStore 广播，与仓库现有偏好 hook 同一套路。
 * 所有写操作立即落盘（没有「保存」按钮），任何页面读到的都是最新值。
 */

function browserStorage(): SettingsStorage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

let current: SettingsState = readSettingsFrom(browserStorage());
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot() {
  return current;
}

function commit(next: SettingsState) {
  current = next;
  writeSettingsTo(browserStorage(), current);
  emit();
}

function update(patch: Partial<SettingsState>) {
  commit({ ...current, ...patch });
}

export const settingsActions = {
  setProvider(name: string, setting: ProviderSetting) {
    update({ providers: { ...current.providers, [name]: setting } });
  },
  setLLM(llm: LLMSetting) {
    update({ llm });
  },
  setTTS(tts: TTSSetting) {
    update({ tts });
  },
  setDefaultImageModel(model: string) {
    update({ defaultImageModel: model });
  },
  setDefaultVideoModel(model: string) {
    update({ defaultVideoModel: model });
  },
  setDefaultResolution(resolution: GenResolution) {
    update({ defaultResolution: resolution });
  },
  setDefaultAspectRatio(ratio: GenAspectRatio) {
    update({ defaultAspectRatio: ratio });
  },
  setImageParams(params: ImageGenParams) {
    update({ imageParams: params });
  },
  setVideoParams(params: VideoGenParams) {
    update({ videoParams: params });
  },
  setSpendCapUsd(usd: number) {
    update({ spendCapUsd: Number.isFinite(usd) && usd >= 0 ? usd : 0 });
  },
  setUiMode(uiMode: SettingsUiMode) {
    update({ uiMode });
  },
} as const;

export type SettingsActions = typeof settingsActions;

let defaultsPromise: Promise<void> | null = null;

/**
 * 首次进入拉取运维默认值（`clipforge.local.json`），只补空字段。
 * 失败不重试、不打扰用户：没有默认值也能手工填写。
 */
export function ensureConfiguredDefaults(): Promise<void> {
  if (!defaultsPromise) {
    defaultsPromise = getClipForgeDefaults()
      .then((defaults) => {
        const patch = configuredDefaultsPatch(current, defaults);
        if (Object.keys(patch).length > 0) commit({ ...current, ...patch });
      })
      .catch(() => undefined);
  }
  return defaultsPromise;
}

export function getSettingsSnapshot(): SettingsState {
  return current;
}

if (typeof window !== "undefined") {
  // 跨标签页写入：一次读回整份设置，避免逐字段同步遗漏。
  window.addEventListener("storage", (event) => {
    if (event.key !== SETTINGS_STORAGE_KEY) return;
    const next = readSettingsFrom(browserStorage());
    if (JSON.stringify(next) === JSON.stringify(current)) return;
    current = next;
    emit();
  });
}

/** 设置页与后续原生页面共用的只读快照 + 写操作。 */
export function useSettingsStore(): SettingsState & SettingsActions {
  const state = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  useEffect(() => {
    void ensureConfiguredDefaults();
  }, []);
  return { ...state, ...settingsActions };
}
