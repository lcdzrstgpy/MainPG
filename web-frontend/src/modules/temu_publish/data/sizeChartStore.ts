/**
 * 尺码表模板库（本机存档，按账号隔离）。
 *
 * 解决的问题：尺码表模板只能在 Temu 卖手后台建（`sizecharts.create` 参数结构
 * 没打通，见 service.size_charts），每次发布却要抄那串模板 ID。这里把
 * 「模板 ID + 表长什么样」存下来：选一下就填 ID；新建时行从本商品变种带出、
 * 列按类目测量项勾选，拼好格子去后台照建，不用对着空表想列名。
 *
 * 存的内容不是提交数据 —— 提交时只用 templateId（数字 ID），
 * 行列格子是给人看的「这张表长什么样」的备忘和建表辅助。
 */
import { storageScope } from "./attrPicksStore";

export type SizeChartRow = {
  /** 尺码名：S / M / L / XL / one-size… */
  size: string;
  /** 与 columns 对齐的测量值（cm） */
  values: string[];
};

export type SizeChartTemplate = {
  /** 本机 id */
  id: string;
  name: string;
  /** Temu 尺码表模板 ID（提交时真正用的） */
  templateId: string;
  /** 适用类目提示（叶子 catId）：选模板时排前面 */
  catIds: number[];
  /** 这张表建在哪家店（尺码表模板是店铺级的，ID 跨店不能混用） */
  shopId?: string;
  shopName?: string;
  /** 测量列（不含尺码列）：衣长/胸围/腰围… */
  columns: string[];
  rows: SizeChartRow[];
  savedAt: number;
};

export const SIZE_CHART_TEMPLATES_KEY = "temu_size_chart_templates_v1";

/** 不知道类目测量项时的兜底列预设（上装/下装/鞋类） */
export const SIZE_CHART_PRESETS: Array<{ name: string; columns: string[] }> = [
  { name: "上装", columns: ["衣长", "胸围", "腰围", "肩宽", "袖长"] },
  { name: "下装", columns: ["裤长", "腰围", "臀围", "大腿围", "裤脚口"] },
  { name: "鞋类", columns: ["内长", "脚围", "跟高"] },
];

function key(): string {
  return `${SIZE_CHART_TEMPLATES_KEY}:${storageScope()}`;
}

export function loadSizeChartTemplates(): SizeChartTemplate[] {
  try {
    const raw = window.localStorage.getItem(key());
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(parsed) ? (parsed as SizeChartTemplate[]) : [];
  } catch {
    return [];
  }
}

export function saveSizeChartTemplates(list: SizeChartTemplate[]): void {
  try {
    window.localStorage.setItem(key(), JSON.stringify(list));
  } catch {
    // 存储满了/隐私模式：模板库用不了而已，不影响发布本身
  }
}

export function upsertSizeChartTemplate(template: SizeChartTemplate): SizeChartTemplate[] {
  const list = loadSizeChartTemplates();
  const next = list.some((item) => item.id === template.id)
    ? list.map((item) => (item.id === template.id ? template : item))
    : [template, ...list];
  saveSizeChartTemplates(next);
  return next;
}

export function removeSizeChartTemplate(id: string): SizeChartTemplate[] {
  const next = loadSizeChartTemplates().filter((item) => item.id !== id);
  saveSizeChartTemplates(next);
  return next;
}

/**
 * 新建模板的行成品：优先用本商品变种里的尺码值（一条商品有哪些码自己最清楚），
 * 没有就用类目可选尺码。返回 [{size, values: 列数个空串}]。
 */
export function draftRowsFromSizes(sizes: string[], columnCount: number): SizeChartRow[] {
  return sizes
    .map((size) => size.trim())
    .filter(Boolean)
    .map((size) => ({ size, values: Array.from({ length: columnCount }, () => "") }));
}
