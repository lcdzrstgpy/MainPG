import type { WorkspaceTab } from "./TopNavigation";

/**
 * 工作台页签的会话恢复（localStorage）。
 *
 * 为什么需要：任务页（处理任务 / 预检 / 尺寸画布）开着时前端会持续轮询后端，这既是进度刷新
 * 也是「还有页面在看这个任务」的凭证——页签一丢（浏览器刷新、前端自动重载、崩溃恢复），
 * 服务器就认为没人看了，任务会被自动暂停（关页面信标 → 宽限期 → 暂停）。
 * 恢复页签就是把这份「有人在看」接回去。
 *
 * 持久化数据一律当**不可信输入**处理（旧版本残留、手工改坏、跨账号登录共享同一份存储）：
 *  - 模块必须存在于当前角色可见的页面清单里（下架模块 / 无权限模块直接丢）；
 *  - 逐字段校验：数字字段必须是有限数字、数组字段必须是数字数组，类型不对就当没有；
 *  - 按 key 去重；解析失败或全部失效时返回 null，由调用方退回默认页签。
 */

export const WORKSPACE_TABS_STORAGE_KEY = "mainpg.workspace.tabs.v1";

/** 恢复上限：正常会话开不到这么多页签，纯粹是给被改坏/被灌水的存储兜底。 */
export const MAX_RESTORED_TABS = 24;

export type WorkspaceTabsSnapshot = {
  tabs: WorkspaceTab[];
  activeTabKey: string;
};

/** 只依赖 localStorage 的读写两个方法：测试里可以换成内存实现。 */
export type WorkspaceTabsStorage = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
};

/** 逐字段清洗清单。key / moduleId / label / icon 单独处理。 */
const STRING_FIELDS = ["directionId", "dimensionBatchId", "dimensionItemId", "dimensionChangeSetId", "initialSetId", "iconClass"] as const;
const NUMBER_FIELDS = ["taskRunId", "taskId", "activeTaskId", "returnTaskId"] as const;
const NUMBER_LIST_FIELDS = ["draftIds", "premiumDraftIds"] as const;
const JSON_OBJECT_FIELDS = ["processingOptions", "profitPrefill"] as const;

function sanitizeTab(raw: unknown, allowedModuleIds: ReadonlySet<string>): WorkspaceTab | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const source = raw as Record<string, unknown>;
  const key = typeof source.key === "string" ? source.key : "";
  const moduleId = typeof source.moduleId === "string" ? source.moduleId : "";
  // 模块清单是唯一的合法性来源：版本升级删掉的页面、当前角色没权限的页面都不恢复。
  if (!key || !moduleId || !allowedModuleIds.has(moduleId)) return null;

  const tab: Record<string, unknown> = {
    key,
    moduleId,
    // 页签标题多是动态文案（"处理·#12"），沿用持久化值；空文案由调用方按模块补默认值。
    label: typeof source.label === "string" ? source.label : "",
    icon: typeof source.icon === "string" ? source.icon : "",
  };
  for (const field of STRING_FIELDS) {
    if (typeof source[field] === "string") tab[field] = source[field];
  }
  for (const field of NUMBER_FIELDS) {
    const value = source[field];
    if (typeof value === "number" && Number.isFinite(value)) tab[field] = value;
  }
  for (const field of NUMBER_LIST_FIELDS) {
    const value = source[field];
    if (Array.isArray(value) && value.every((item) => typeof item === "number" && Number.isFinite(item))) {
      tab[field] = [...value];
    }
  }
  for (const field of JSON_OBJECT_FIELDS) {
    // 值来自 JSON.parse，本身已是可序列化的普通对象；这里只挡掉 null 与基本类型。
    const value = source[field];
    if (value && typeof value === "object") tab[field] = value;
  }
  return tab as unknown as WorkspaceTab;
}

export function sanitizeWorkspaceTabs(raw: unknown, allowedModuleIds: ReadonlySet<string>): WorkspaceTab[] {
  if (!Array.isArray(raw)) return [];
  const seen = new Set<string>();
  const tabs: WorkspaceTab[] = [];
  for (const entry of raw) {
    const tab = sanitizeTab(entry, allowedModuleIds);
    if (!tab || seen.has(tab.key)) continue;
    seen.add(tab.key);
    tabs.push(tab);
  }
  return tabs;
}

/** 超出上限时截断：保留靠前的页签，但当前页签必须在——否则刷新后焦点会莫名跳到别的页。 */
export function trimTabsKeepingActive(tabs: WorkspaceTab[], activeTabKey: string, limit: number): WorkspaceTab[] {
  if (tabs.length <= limit) return tabs;
  const kept = tabs.slice(0, limit);
  if (kept.some((tab) => tab.key === activeTabKey)) return kept;
  const active = tabs.find((tab) => tab.key === activeTabKey);
  if (!active) return kept;
  return [...kept.slice(0, limit - 1), active];
}

export function readWorkspaceTabsSnapshot(
  storage: WorkspaceTabsStorage | null,
  allowedModuleIds: ReadonlySet<string>,
): WorkspaceTabsSnapshot | null {
  if (!storage) return null;
  let parsed: unknown;
  try {
    const raw = storage.getItem(WORKSPACE_TABS_STORAGE_KEY);
    if (!raw) return null;
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object") return null;
  const payload = parsed as { tabs?: unknown; activeTabKey?: unknown };
  const tabs = sanitizeWorkspaceTabs(payload.tabs, allowedModuleIds);
  if (!tabs.length) return null;
  const activeTabKey = typeof payload.activeTabKey === "string" && tabs.some((tab) => tab.key === payload.activeTabKey)
    ? payload.activeTabKey
    : tabs[0].key;
  return { tabs: trimTabsKeepingActive(tabs, activeTabKey, MAX_RESTORED_TABS), activeTabKey };
}

export function writeWorkspaceTabsSnapshot(storage: WorkspaceTabsStorage | null, snapshot: WorkspaceTabsSnapshot): void {
  if (!storage) return;
  try {
    storage.setItem(WORKSPACE_TABS_STORAGE_KEY, JSON.stringify({
      tabs: trimTabsKeepingActive(snapshot.tabs, snapshot.activeTabKey, MAX_RESTORED_TABS),
      activeTabKey: snapshot.activeTabKey,
    }));
  } catch {
    // 隐私模式 / 配额写满：存不下就算了，不影响本次使用。
  }
}

/**
 * 页签 key 末尾的递增序号（`daily-selection-collection-2`、`product-processing-task-17-3`）。
 * 恢复页签后要把序号续上：否则新开的页签会和恢复出来的 key 撞车，React key 重复，
 * 以及各处「按 key 找页签」的分支都会错乱。
 */
export function trailingSequence(keys: readonly string[], prefix: string): number {
  let max = 0;
  for (const key of keys) {
    if (!key.startsWith(prefix)) continue;
    const match = /(\d+)$/.exec(key);
    if (!match) continue;
    max = Math.max(max, Number(match[1]));
  }
  return max;
}
