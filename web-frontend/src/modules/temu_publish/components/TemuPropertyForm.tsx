import type { ReactNode } from "react";

import {
  EMPTY_ATTR,
  buildAttributeGroups,
  visibleProperties,
  type AttrPick,
  type AttrPickMap,
} from "../data/attributeModel";
import type { TemuAttributes, TemuProperty } from "../types";

type Props = {
  attributes: TemuAttributes | null;
  picks: AttrPickMap;
  onChange: (next: AttrPickMap) => void;
  /** 点过发布被拦之后才标红必填项，避免一进页面满屏红框 */
  showErrors?: boolean;
  /** 没有属性表时的占位文案 */
  emptyHint?: string;
  /** 选填区标题里的一句话，发布页和批量弹窗的措辞不一样 */
  optionalHint?: string;
  /**
   * 哪些 pid 的值是「猜的」（草稿库按类目估填时没有依据、取了第一个可用选项）。
   * 这些项旁边标一个「估」，让人优先核对 —— 不标的话用户会以为 8 项都填对了。
   */
  guessedPids?: number[];
};

/**
 * 类目属性表单（必填 + 选填 + 联动分组）。
 *
 * 发布页和批量发布弹窗共用这一份：两边各写一遍迟早漂移，漂移的表现是
 * 「页面说不用填、提交时说缺」，很难查。
 */
export function TemuPropertyForm({
  attributes,
  picks,
  onChange,
  showErrors = false,
  emptyHint = "选到叶子分类后自动加载属性。",
  optionalHint = "不填也能发布",
  guessedPids,
}: Props) {
  const guessed = new Set(guessedPids ?? []);
  const visible = visibleProperties(attributes, picks);
  const groups = buildAttributeGroups(attributes, visible);

  const patchPick = (pid: number, patch: Partial<AttrPick>) => {
    const current = picks[pid] ?? EMPTY_ATTR;
    onChange({ ...picks, [pid]: { ...current, ...patch } });
  };

  /**
   * 一个类目属性的输入控件。三种形态：
   *  - 只有预设值 → 下拉
   *  - 只有单位 → 数值输入框（厚度 mm 这类）
   *  - 两者都有 → 下拉 + 数值输入框（「封面材质」选成分 + 填成分比例 %）
   * 之前只渲染下拉，导致「下拉+比例」的属性永远填不进数值，Temu 回「请输入数值」。
   */
  const attrField = (item: TemuProperty, required: boolean) => {
    const label = item.name || item.propertyName || `属性 ${item.pid}`;
    const pick = picks[item.pid] ?? EMPTY_ATTR;
    const unit = item.valueUnit?.[0] ?? "";
    const unitHint = unit
      ? `${item.numberInputTitle || "请输入数值"}（${unit}）`
      : item.numberInputTitle || "请输入";
    const emptyRequired = required && !pick.vid && !pick.text.trim();
    return (
      <label
        className={`temu-field${showErrors && emptyRequired ? " is-required-empty" : ""}`}
        key={item.pid}
      >
        <span>
          {label}
          {required && <em className="temu-required">*</em>}
          {guessed.has(item.pid) && (
            <em className="temu-attr-guessed" title="这个值是按类目估的（没有依据时取第一个可用选项），发布前请核对">
              估
            </em>
          )}
        </span>
        {item.values?.length ? (
          <div className={unit ? "temu-spec-row" : undefined}>
            <select
              className="temu-input"
              value={pick.vid || ""}
              onChange={(event) => patchPick(item.pid, { vid: Number(event.target.value) || 0 })}
            >
              <option value="">请选择</option>
              {item.values.map((value) => (
                <option key={value.vid} value={value.vid}>
                  {value.value}
                </option>
              ))}
            </select>
            {unit ? (
              <input
                className="temu-input"
                value={pick.text}
                title={unitHint}
                placeholder={unitHint}
                onChange={(event) => patchPick(item.pid, { text: event.target.value })}
              />
            ) : null}
          </div>
        ) : (
          <input
            className="temu-input"
            value={pick.text}
            title={unitHint}
            placeholder={unitHint}
            onChange={(event) => patchPick(item.pid, { text: event.target.value })}
          />
        )}
      </label>
    );
  };

  /** 有联动子属性时，子属性缩进挂在父属性下面（原地展开，不跳区）。 */
  const attrGroup = (item: TemuProperty, required: boolean, depth = 0): ReactNode => {
    const kids = groups.children.get(item.pid) ?? [];
    if (!kids.length) {
      if (!depth) return attrField(item, required);
      return (
        <div className="temu-attr-dependent" key={`attr-dependent-${item.pid}`}>
          {attrField(item, required)}
        </div>
      );
    }
    return (
      <div className={`temu-attr-group${depth ? " is-nested" : ""}`} key={`attr-group-${item.pid}`}>
        {attrField(item, required)}
        <div className="temu-attr-children">
          {kids.map((child) => attrGroup(child, child.required, depth + 1))}
        </div>
      </div>
    );
  };

  if (!attributes) return <p className="temu-empty">{emptyHint}</p>;

  return (
    <>
      {groups.required.length > 0 && (
        // 必填属性单独圈出来：这块是「发不出去」的唯一原因，
        // 原来和选填混在一个平面网格里，一眼看不出哪些必须填。
        <div className="temu-attr-required">
          <div className="temu-attr-required-head">
            <strong>必填属性 {groups.required.length} 项</strong>
            <span>带 <em className="temu-required">*</em> 的都要选，缺一个 Temu 会直接拒</span>
          </div>
          <div className="temu-attr-grid">
            {groups.required.map((item) => attrGroup(item, true))}
          </div>
        </div>
      )}
      {groups.optional.length > 0 && (
        <details className="temu-optional-attrs">
          <summary>
            选填属性 {groups.optional.length} 项（{optionalHint}）
          </summary>
          <div className="temu-attr-grid">
            {groups.optional.map((item) => attrGroup(item, false))}
          </div>
        </details>
      )}
      {groups.required.length === 0 && groups.optional.length === 0 && (
        <p className="temu-empty">这个分类没有可填属性。</p>
      )}
    </>
  );
}
