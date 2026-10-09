/**
 * video 页「按指定成片版本查看逐镜音频报告」的纯归约逻辑。
 *
 * 为什么单独成模块：仓库没有 @testing-library/react，把「哪些成片可选 / 默认选哪一条 / 选择项怎么
 * 显示」抽成不依赖 React 与网络的纯函数，就能直接单测；页面只保留拉取与渲染。
 *
 * 约定：全部函数不抛错。数据缺失（列表为空、字段脏、时间无法解析）时返回显式空值/回退文案，
 * 让页面回退到「暂无音频报告」，而不是报错。
 */

/** 成片版本选择器需要的最小字段（来自 GET /api/project/[id]/compositions）。 */
export interface CompositionChoice {
  id: string;
  /** 成片文件名（接口只给文件名，不含本机路径） */
  fileName: string | null;
  /** 变体标签（多变体批量合成时写入） */
  label: string | null;
  /** ISO 创建时间字符串 */
  createdAt: string | null;
}

/** 与仓库其它读接口一致：项目/成片 id 只允许字母、数字与连字符。 */
const SAFE_ID = /^[a-zA-Z0-9-]+$/;

/**
 * 从成片列表响应里挑出可用于版本选择器的条目：丢掉结构非法与 id 重复的条目，
 * 保留列表顺序（接口按新到旧返回，页面据此默认选最新一条）。
 */
export function compositionChoices(value: unknown): CompositionChoice[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  const choices: CompositionChoice[] = [];
  for (const item of value) {
    if (!item || typeof item !== "object" || Array.isArray(item)) continue;
    const row = item as Record<string, unknown>;
    const id = typeof row.id === "string" ? row.id.trim() : "";
    if (!SAFE_ID.test(id) || seen.has(id)) continue;
    seen.add(id);
    const fileName = typeof row.fileName === "string" ? row.fileName.trim() : "";
    const label = typeof row.label === "string" ? row.label.trim() : "";
    choices.push({
      id,
      fileName: fileName || null,
      label: label || null,
      createdAt: typeof row.createdAt === "string" && row.createdAt.trim() ? row.createdAt : null,
    });
  }
  return choices;
}

/**
 * 默认选中的成片版本：优先保留用户已选且仍存在的版本；否则取列表第一条
 * （与迁移前「读最新一条成片」的默认行为一致）。没有成片时返回 null。
 */
export function pickCompositionId(
  choices: readonly CompositionChoice[] | null | undefined,
  preferredId?: string | null
): string | null {
  const list = Array.isArray(choices) ? choices.filter(Boolean) : [];
  if (preferredId && list.some((choice) => choice.id === preferredId)) return preferredId;
  return list[0]?.id ?? null;
}

function two(value: number): string {
  return String(value).padStart(2, "0");
}

/** 本地时间戳（成片版本之间通常只差几分钟，没有时间就分不清谁是谁）。解析失败返回空串。 */
function localTimestamp(createdAt: string | null): string {
  if (!createdAt) return "";
  const at = new Date(createdAt);
  if (Number.isNaN(at.getTime())) return "";
  return `${at.getFullYear()}-${two(at.getMonth() + 1)}-${two(at.getDate())} ${two(at.getHours())}:${two(at.getMinutes())}`;
}

/**
 * 版本选择器里的显示名：成片标签 → 文件名 → 序号兜底；能解析出创建时间时附加在末尾。
 */
export function compositionChoiceLabel(
  choice: CompositionChoice | null | undefined,
  index = 0
): string {
  const name = choice?.label || choice?.fileName || `版本 ${index + 1}`;
  const when = localTimestamp(choice?.createdAt ?? null);
  return when ? `${name} · ${when}` : name;
}
