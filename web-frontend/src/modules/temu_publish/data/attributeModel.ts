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
 * 把「同一个 pid 挂了多条属性」的条目拆成唯一键。
 *
 * 服装类目实测（2026-11-05，女装连衣裙 39107）：
 *
 * | 属性 | pid | refPid | templatePid |
 * |---|---|---|---|
 * | 面料纹理1（必填） | 2054 | 6926 | 1292401 |
 * | 面料纹理2 | 2054 | 6927 | 1293592 |
 * | 面料克重1（必填，父=面料纹理1） | 2052 | 6930 | 1296028 |
 * | 面料克重2（必填，父=面料纹理2） | 2052 | 6931 | 1297215 |
 * | 里料克重（必填，父=里料纹理） | 2052 | 6934 | 1298785 |
 * | 成分 / 里衬成分 / 基布成分 | 2 | 15 / 6547 / 7639 | … |
 *
 * `attrPicks` 是按 pid 索引的，pid 一撞就出三个连锁问题：
 * 1. 填「面料克重1」时三行克重一起变（共用一个输入值）；
 * 2. 「面料纹理1」选了光面 → 同 pid 的「面料纹理2」也被当成选了光面 →
 *    面料克重2 被误判为可见且必填，用户被要求填一个根本不该出现的属性；
 * 3. 组装 payload 时按 pid 只能取到**最后一条**（面料纹理2），
 *    于是「面料纹理1」压根没提交 → Temu 回「缺少父属性值」。
 *
 * 所以这里把重复 pid 的条目改成用 **refPid** 当键（refPid 是 Temu 属性实例的唯一标识），
 * 原始 pid 存进 `realPid`，提交时还原。
 *
 * 只在 pid 真的重复时才替换：绝大多数类目 pid 本来唯一，替换会破坏本机存档里
 * 已有的属性值（表现为「必填属性全变成空的」）。
 */
export function normalizeDuplicatePids(attributes: TemuAttributes | null): TemuAttributes | null {
  const props = attributes?.properties;
  if (!attributes || !Array.isArray(props) || !props.length) return attributes;

  const counts = new Map<number, number>();
  for (const prop of props) {
    const pid = Number(prop.pid ?? 0);
    counts.set(pid, (counts.get(pid) ?? 0) + 1);
  }
  let changed = false;
  const used = new Set<number>();
  const properties = props.map((prop) => {
    const pid = Number(prop.pid ?? 0);
    if ((counts.get(pid) ?? 0) <= 1) {
      used.add(pid);
      return prop;
    }
    // 同一个 pid 的多条：refPid 通常各不相同，拿它当键；万一 refPid 也撞，
    // 再退回 templatePid（也是属性实例级的 id），最后才用序号兜底。
    const candidates = [Number(prop.refPid ?? 0), Number(prop.templatePid ?? 0)];
    const key = candidates.find((item) => item > 0 && !used.has(item));
    if (!key) return prop;
    used.add(key);
    changed = true;
    return { ...prop, pid: key, realPid: pid };
  });
  return changed ? { ...attributes, properties } : attributes;
}

/**
 * 老存档（键是原始 pid）→ 新键（重复 pid 已换成 refPid）的兼容映射。
 *
 * 存档是在 normalize 之前写的，键是 2054 / 2052。如果不翻译，
 * 服饰类目里已经填好的纹理/克重会在刷新后全部变空（表现为「必填属性又没了」）。
 * 只在「这个原始 pid 只对应一条属性」时翻译 —— 一条 pid 对上多条时无法判断
 * 用户当初填的是哪一条，宁可清空让他重填。
 */
export function migrateLegacyPicks(
  picks: AttrPickMap,
  attributes: TemuAttributes | null,
): { picks: AttrPickMap; migrated: number } {
  const properties = attributes?.properties ?? [];
  const byRealPid = new Map<number, TemuProperty[]>();
  const keys = new Set<number>();
  for (const prop of properties) {
    const key = Number(prop.pid ?? 0);
    keys.add(key);
    const real = Number(prop.realPid ?? 0);
    if (!real) continue;
    const list = byRealPid.get(real) ?? [];
    list.push(prop);
    byRealPid.set(real, list);
  }
  let migrated = 0;
  const out: AttrPickMap = {};
  for (const [rawKey, pick] of Object.entries(picks ?? {})) {
    const key = Number(rawKey);
    if (keys.has(key)) {
      out[key] = pick;
      continue;
    }
    const targets = byRealPid.get(key) ?? [];
    if (targets.length === 1 && pick) {
      out[Number(targets[0].pid)] = pick;
      migrated += 1;
      continue;
    }
    // 翻译不了就原样留着：上层（pruneStaleAttrPicks）会把它报成「失效」，
    // 这里悄悄吞掉会让用户看不出哪一项没了。
    out[key] = pick;
  }
  return { picks: out, migrated };
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
  const props = attributes?.properties ?? [];
  const byTemplatePid = new Map<number, TemuProperty>();
  const byRefPid = new Map<number, TemuProperty>();
  props.forEach((item) => {
    const key = Number(item.templatePid ?? 0);
    if (key) byTemplatePid.set(key, item);
    const ref = Number(item.refPid ?? 0);
    if (ref) byRefPid.set(ref, item);
  });
  const pickedVid = (pid: number) => Number(picks[pid]?.vid ?? 0);
  const list: TemuProperty[] = [];
  for (const prop of props) {
    // ① showCondition：按父属性的 refPid 判断（与下面那条规则是两套，各查各的索引）
    const conditions = prop.showCondition ?? [];
    if (conditions.length) {
      const visible = conditions.some((cond) => {
        const parent = byRefPid.get(Number(cond.parentRefPid ?? 0));
        const vid = parent ? pickedVid(parent.pid) : 0;
        return vid > 0 && (cond.parentVids ?? []).includes(vid);
      });
      if (!visible) continue;
    }
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
  for (const item of attributes?.properties ?? []) {
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

/**
 * 丢掉「当前类目属性表里已经查不到」的选择。
 *
 * 草稿库里的属性是按**估填那一刻**的 pid/vid 存的，Temu 后台会调整类目属性
 * （选项下线、pid 重建）。这类脏值有两个要命的表现：
 * 1. 界面上下拉框空白 —— 因为 values 里找不到那个 vid，`<option>` 根本没渲染；
 * 2. 内部 picks 仍算「有值」→ 本地必填校验放行 → 一路走到 goods.add，
 *    被 Temu 回一句「请求的内容不存在或已被删除」，用户完全猜不到是属性失效。
 *
 * 所以铺值之前先按当前属性表过一遍，查不到的直接丢掉，让它正常报「缺少必填属性」。
 * 纯输入型属性（values 为空表）不做 vid 校验，只把空值丢掉。
 */
export function pruneStaleAttrPicks(
  picks: AttrPickMap,
  attributes: TemuAttributes | null,
): { picks: AttrPickMap; dropped: string[] } {
  // 老存档的键是原始 pid；服装类目「同 pid 多条」的键已被换成 refPid。
  // 先按「唯一对应」翻译一遍，否则草稿里填好的纹理/克重会全部变成失效值。
  const migrated = migrateLegacyPicks(picks, attributes).picks;
  const byPid = new Map<number, TemuProperty>();
  for (const item of attributes?.properties ?? []) byPid.set(Number(item.pid), item);
  const next: AttrPickMap = {};
  const dropped: string[] = [];
  for (const [key, pick] of Object.entries(migrated ?? {})) {
    const pid = Number(key);
    const prop = byPid.get(pid);
    const name = prop ? prop.name || prop.propertyName || `pid=${pid}` : `pid=${pid}`;
    if (!prop || !pick) {
      dropped.push(name);
      continue;
    }
    const vid = Number(pick.vid ?? 0);
    const text = String(pick.text ?? "").trim();
    if (vid > 0) {
      const values = prop.values ?? [];
      if (values.length && !values.some((value) => Number(value.vid) === vid)) {
        dropped.push(name);
        continue;
      }
      next[pid] = { vid, text };
      continue;
    }
    if (!text) {
      dropped.push(name);
      continue;
    }
    next[pid] = { vid: 0, text };
  }
  return { picks: next, dropped };
}

/**
 * 类目属性表变了之后，尽量把用户原来的选择「翻译」到新表上。
 *
 * Temu 改类目属性常见两种情况：
 * 1. **只是重建了 vid**（选项文字没变）→ 拿旧 vid 对应的文字去新表里找，能自动对上，
 *    用户完全不用动；
 * 2. 选项真的被删/改名了 → 对不上，只能丢掉让他重选。
 *
 * 之前一律丢掉，所以一遇到 6000002 就要求「从头重选一遍」—— 明明大部分值根本没变，
 * 这种提示只会让人骂「太傻了」。
 */
export function remapAttrPicks(
  previous: TemuAttributes | null,
  next: TemuAttributes | null,
  picks: AttrPickMap,
): { picks: AttrPickMap; remapped: string[]; dropped: string[] } {
  const before = new Map((previous?.properties ?? []).map((item) => [Number(item.pid), item]));
  const after = new Map((next?.properties ?? []).map((item) => [Number(item.pid), item]));
  const out: AttrPickMap = {};
  const remapped: string[] = [];
  const dropped: string[] = [];

  for (const [key, pick] of Object.entries(picks ?? {})) {
    const pid = Number(key);
    const prop = after.get(pid);
    const name = prop?.name || prop?.propertyName || `pid=${pid}`;
    if (!prop || !pick) {
      dropped.push(name);
      continue;
    }
    const vid = Number(pick.vid ?? 0);
    const text = String(pick.text ?? "").trim();
    const values = prop.values ?? [];

    if (vid > 0) {
      // 还在新表里（或这个属性本来就没有选项表）→ 原样保留
      if (!values.length || values.some((value) => Number(value.vid) === vid)) {
        out[pid] = { vid, text };
        continue;
      }
      // vid 失效：拿它原来代表的值去新表里找同名项
      const oldValue = (before.get(pid)?.values ?? []).find(
        (value) => Number(value.vid) === vid,
      )?.value;
      const byValue = oldValue
        ? values.find((value) => String(value.value ?? "").trim() === oldValue.trim())
        : undefined;
      if (byValue) {
        out[pid] = { vid: Number(byValue.vid), text };
        remapped.push(name);
        continue;
      }
      // 没有旧表可比对，但人工填过文字：按文字再找一次
      const byText = text ? values.find((value) => String(value.value ?? "").trim() === text) : undefined;
      if (byText) {
        out[pid] = { vid: Number(byText.vid), text };
        remapped.push(name);
        continue;
      }
      dropped.push(name);
      continue;
    }

    if (!text) {
      dropped.push(name);
      continue;
    }
    out[pid] = { vid: 0, text };
  }

  return { picks: out, remapped, dropped };
}

export type AttributeDiff = {
  /** 这次才变成必填 / 新加进来的必填属性 */
  addedRequired: string[];
  /** 旧表里有、新表里没了的属性（Temu 删了或者重新划分了） */
  removed: string[];
  /** 已选的值在新表里查不到了（必须重选） */
  staleChoices: string[];
  /** vid 被重建但选值文字没变：已自动对上，用户不用动 */
  remapped: string[];
};

/**
 * 对比「重拉前 / 重拉后」的类目属性表，说清楚到底变了什么。
 *
 * 为什么需要：Temu 回 6000002「货品类目属性更新」时，光让用户「重新选一遍」是句废话 ——
 * 他打开属性区一看跟原来一模一样，不知道该动哪一项，只能盲选，然后又被拒。
 * 把新增的必填项、被删的属性、失效的已选值点名出来，才是能照着做的提示。
 */
export function describeAttributeDiff(
  previous: TemuAttributes | null,
  next: TemuAttributes | null,
  picks: AttrPickMap,
): AttributeDiff {
  const before = new Map<number, TemuProperty>();
  for (const item of previous?.properties ?? []) before.set(Number(item.pid), item);
  const after = new Map<number, TemuProperty>();
  for (const item of next?.properties ?? []) after.set(Number(item.pid), item);

  const label = (item: TemuProperty) => item.name || item.propertyName || `pid=${item.pid}`;

  const addedRequired =
    before.size > 0
      ? [...after.values()]
          .filter((item) => item.required && !before.get(Number(item.pid))?.required)
          .map(label)
      : [];
  const removed =
    after.size > 0
      ? [...before.values()].filter((item) => !after.has(Number(item.pid))).map(label)
      : [];
  // 失效判定走 remap：只是 vid 被重建（文字没变）的能自动对上，不算「要重选」
  const { remapped, dropped } = remapAttrPicks(previous, next, picks);

  return { addedRequired, removed, staleChoices: dropped, remapped };
}

/** 把 {@link AttributeDiff} 说成一句话；没有差异时返回空串。 */
export function attributeDiffText(diff: AttributeDiff, limit = 5): string {
  const parts: string[] = [];
  if (diff.remapped.length) {
    parts.push(`「${diff.remapped.slice(0, limit).join("、")}」已自动对上（选值没变，不用改）`);
  }
  if (diff.addedRequired.length) {
    parts.push(`新增必填「${diff.addedRequired.slice(0, limit).join("、")}」`);
  }
  if (diff.staleChoices.length) {
    parts.push(`「${diff.staleChoices.slice(0, limit).join("、")}」原来的选择已失效（已清空）`);
  }
  if (diff.removed.length) {
    parts.push(`「${diff.removed.slice(0, limit).join("、")}」Temu 已经删掉了`);
  }
  return parts.join("；");
}

/** 必填且没填（带单位的属性只选下拉不算填好）。 */export function missingRequiredNames(
  visible: TemuProperty[],
  picks: AttrPickMap,
): string[] {
  return visible
    .filter((item) => item.required)
    .filter((item) => {
      const pick = picks[item.pid];
      if (!pick || (pick.vid <= 0 && !pick.text.trim())) return true;
      // 带单位的属性（成分比例 %、厚度 mm）Temu 要的是数值：只选下拉不够。
      // ⚠️ 必须看「单位本身非空」，不能用 valueUnit.length：
      // Temu 对无单位属性也会返回 `valueUnit: [""]`，长度是 1 但内容是空串。
      // 用 length 判断会把这批纯下拉属性误判成「必须填数值」，而界面按
      // `valueUnit[0]` 判断根本不渲染数值框 —— 等于永远填不完、永远发不出去。
      const unit = String(item.valueUnit?.[0] ?? "").trim();
      if (unit && !pick.text.trim()) return true;
      return false;
    })
    .map((item) => item.name || item.propertyName || `pid=${item.pid}`);
}
