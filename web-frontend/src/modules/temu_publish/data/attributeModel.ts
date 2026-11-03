import type { TemuAttributes, TemuProperty } from "../types";

/**
 * 类目属性的「可见性」与「依赖分组」。
 *
 * 抽成纯函数是为了让发布页和批量发布弹窗共用同一套规则 —— 两边各写一遍迟早漂移，
 * 而漂移的表现是「页面说这个属性不用填、提交时说缺」，很难查。
 */

export type AttrPick = { vid: number; text: string };
export type AttrPickMap = Record<number, AttrPick>;

export const EMPTY_ATTR: AttrPick = { vid: 0, text: "" };

/**
 * 按 pid 去重：Temu 偶尔会给同一个类目返回两个 pid 相同、templatePid/名称不同的属性
 * （真实案例：pid=1「材质」required + pid=1「主体材质」optional）。
 *
 * 前端选择、payload、必填校验都以 pid 为键，留着两个会互相覆盖。保留更该填的那个：
 * required 优先，其次 isSale（变种靠销售属性）；顺序沿用第一次出现的位置。
 */
export function dedupeProperties(properties: TemuProperty[]): TemuProperty[] {
  const kept = new Map<number, TemuProperty>();
  const order: number[] = [];
  let changed = false;
  for (const item of properties) {
    const pid = Number(item.pid ?? 0);
    if (!pid) {
      changed = true;
      continue;
    }
    const current = kept.get(pid);
    if (!current) {
      kept.set(pid, item);
      order.push(pid);
      continue;
    }
    changed = true;
    const next = propertyPriority(item);
    const prev = propertyPriority(current);
    if (next[0] > prev[0] || (next[0] === prev[0] && next[1] > prev[1])) {
      kept.set(pid, item);
    }
  }
  if (!changed) return properties;
  return order.map((pid) => kept.get(pid) as TemuProperty);
}

function propertyPriority(item: TemuProperty): [number, number] {
  return [item.required ? 1 : 0, item.isSale ? 1 : 0];
}

/**
 * 当前选择下「适用」的属性（Temu 属性联动）。
 *
 * 子属性带 `templatePropertyValueParentList`，`parentTemplatePid` 指向父属性的 `templatePid`：
 * 父属性没选、或选的 vid 不在 `parentVidList` 里 → 子属性压根不该出现
 * （实测「电池属性=不带电池」时，「可充电电池/太阳能电池/不可充电电池」都是 required，
 *  但填了反而被判不合法）。父值命中时，子属性只保留 `vidList` 里的值。
 */
export function visibleProperties(
  attributes: TemuAttributes | null,
  picks: AttrPickMap,
): TemuProperty[] {
  const props = dedupeProperties(attributes?.properties ?? []);
  const byTemplatePid = new Map<number, TemuProperty>();
  props.forEach((item) => {
    const key = Number(item.templatePid ?? 0);
    if (key) byTemplatePid.set(key, item);
  });
  const pickedVid = (pid: number) => Number(picks[pid]?.vid ?? 0);
  const list: TemuProperty[] = [];
  for (const prop of props) {
    const rules = prop.templatePropertyValueParentList ?? [];
    if (!rules.length) {
      list.push(prop);
      continue;
    }
    const parent = byTemplatePid.get(Number(prop.parentTemplatePid ?? 0));
    if (!parent) {
      // 拿不到父属性：宁可显示也不误杀
      list.push(prop);
      continue;
    }
    const parentVid = pickedVid(parent.pid);
    if (!parentVid) continue;
    const matched = rules.filter((rule) => (rule.parentVidList ?? []).includes(parentVid));
    if (!matched.length) continue;
    const allowed = new Set(matched.flatMap((rule) => rule.vidList ?? []));
    list.push({
      ...prop,
      values: (prop.values ?? []).filter((value) => !allowed.size || allowed.has(value.vid)),
    });
  }
  return list;
}

export type AttributeGroups = {
  /** 父属性 pid → 它按联动规则带出来的可见子属性 */
  children: Map<number, TemuProperty[]>;
  /** 没有父属性的必填项（渲染顺序：父项在前，子项缩进挂在它下面） */
  required: TemuProperty[];
  /** 没有父属性的选填项 */
  optional: TemuProperty[];
};

/**
 * 把「父属性 + 它带出来的子属性」绑成一组。
 *
 * 之前子属性直接混进必填区顶部：选了选填项、结果必填项在别处冒出来，位置对不上，
 * 用户看不出是谁带出来的。现在子属性原地展开在父属性下面（缩进 + 竖线），
 * 必填区/选填区只放「没有父属性」的根项。
 *
 * 子属性什么时候可见仍由 {@link visibleProperties} 决定（父没选就不渲染），这里只管摆放。
 */
export function buildAttributeGroups(
  attributes: TemuAttributes | null,
  visible: TemuProperty[],
): AttributeGroups {
  const byTemplatePid = new Map<number, TemuProperty>();
  for (const item of dedupeProperties(attributes?.properties ?? [])) {
    const key = Number(item.templatePid ?? 0);
    if (key) byTemplatePid.set(key, item);
  }
  const children = new Map<number, TemuProperty[]>();
  const nested = new Set<number>();
  for (const item of visible) {
    const parentTemplatePid = Number(item.parentTemplatePid ?? 0);
    if (!parentTemplatePid) continue;
    const parent = byTemplatePid.get(parentTemplatePid);
    if (!parent) continue;
    const list = children.get(parent.pid) ?? [];
    list.push(item);
    children.set(parent.pid, list);
    nested.add(item.pid);
  }
  return {
    children,
    required: visible.filter((item) => item.required && !nested.has(item.pid)),
    optional: visible.filter((item) => !item.required && !nested.has(item.pid)),
  };
}

/** 必填且没填（带单位的属性只选下拉不算填好）。 */
export function missingRequiredNames(
  visible: TemuProperty[],
  picks: AttrPickMap,
): string[] {
  return visible
    .filter((item) => item.required)
    .filter((item) => {
      const pick = picks[item.pid];
      if (!pick || (pick.vid <= 0 && !pick.text.trim())) return true;
      // 带单位的属性（成分比例 %、厚度 mm）Temu 要的是数值：只选下拉不够。
      // ⚠️ 不能只看 valueUnit.length：Temu 的 attrs.get 会给很多无单位属性返回 [""]
      // （材质、风格这类），数组长度是 1 但单位是空串。只看 length 会把
      // 「下拉已经选了、本来就不需要数值」的必填属性永远判成缺。
      const unit = String(item.valueUnit?.[0] ?? "").trim();
      if (unit && !pick.text.trim()) return true;
      return false;
    })
    .map((item) => item.name || item.propertyName || `pid=${item.pid}`);
}
