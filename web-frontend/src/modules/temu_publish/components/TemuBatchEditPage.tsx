import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

import type { TemuDraftEdit, TemuDraftEntry } from "../types";
import { BatchColumnDialog, type ColumnKind } from "./BatchColumnDialog";

/**
 * 整页批量编辑（对齐 Temu 后台 / 店小秘的「批量编辑」）。
 *
 * 交互只有两条，别再加第三条：
 *   1. 单个格子：直接在表格里改；
 *   2. 整列：点列头上的「批量改」，在弹窗里按规则改（文字 / 数值 / SKU 序列）。
 *
 * ⚠️ 表头里绝不放输入框。标签 + 规则按钮 + 输入框挤在 `th` 里必然互相压，
 * 之前那版就是这样糊成一团的。
 */

/** 表格里能编辑的字段（都和 TemuDraftEdit 对齐） */
export type BatchEditField =
  | "title"
  | "sku"
  | "declared_price"
  | "suggested_price"
  | "stock"
  | "length_cm"
  | "width_cm"
  | "height_cm"
  | "weight_g";

type Column = {
  key: BatchEditField;
  label: string;
  group: "产品信息" | "变种信息";
  width: number;
  kind: ColumnKind;
  /** number 用：保留几位小数 */
  decimals?: number;
};

const COLUMNS: Column[] = [
  { key: "title", label: "产品标题", group: "产品信息", width: 320, kind: "text" },
  { key: "sku", label: "SKU货号", group: "变种信息", width: 130, kind: "sku" },
  { key: "declared_price", label: "申报价格(元)", group: "变种信息", width: 115, kind: "number", decimals: 2 },
  { key: "suggested_price", label: "建议零售价(元)", group: "变种信息", width: 125, kind: "number", decimals: 2 },
  { key: "stock", label: "库存", group: "变种信息", width: 90, kind: "number", decimals: 0 },
  { key: "length_cm", label: "尺寸长(cm)", group: "变种信息", width: 105, kind: "number", decimals: 1 },
  { key: "width_cm", label: "尺寸宽(cm)", group: "变种信息", width: 105, kind: "number", decimals: 1 },
  { key: "height_cm", label: "尺寸高(cm)", group: "变种信息", width: 105, kind: "number", decimals: 1 },
  { key: "weight_g", label: "重量(g)", group: "变种信息", width: 95, kind: "number", decimals: 0 },
];

const DEFAULT_FIELDS: BatchEditField[] = ["title", "sku", "declared_price", "weight_g"];

type Props = {
  /** 勾选的草稿（已合并过本机编辑） */
  entries: TemuDraftEntry[];
  /** 现有编辑（保存时在它上面合并） */
  edits: Record<string, TemuDraftEdit>;
  onSave: (next: Record<string, TemuDraftEdit>) => void;
  onClose: () => void;
};

/** 一条草稿在表格里显示的值：本机编辑 > 源数据 */
function cellValue(entry: TemuDraftEntry, edit: TemuDraftEdit | undefined, key: BatchEditField): string {
  const local = edit?.[key];
  if (local !== undefined && local !== null && local !== "") return String(local);
  const raw = entry[key as keyof TemuDraftEntry];
  if (raw === undefined || raw === null) return "";
  return String(raw);
}

export function TemuBatchEditPage({ entries, edits, onSave, onClose }: Props) {
  /** 本地待保存的改动：id → 字段 → 值（'' = 清掉覆盖，回落到源数据） */
  const [draft, setDraft] = useState<Record<string, TemuDraftEdit>>({});
  const [fields, setFields] = useState<BatchEditField[]>(DEFAULT_FIELDS);
  /** 被「移除」暂时不看的行（只是从这张表里去掉，不动草稿本身） */
  const [removed, setRemoved] = useState<string[]>([]);
  /** 正在批量改哪一列（null = 没开弹窗） */
  const [activeColumn, setActiveColumn] = useState<Column | null>(null);
  /** 上一次批量改动改前的值：填错了能整批回退 */
  const [lastFill, setLastFill] = useState<
    null | { key: BatchEditField; previous: Record<string, string | undefined> }
  >(null);

  const rows = useMemo(
    () => entries.filter((entry) => !removed.includes(entry.id)),
    [entries, removed],
  );
  const visibleColumns = useMemo(
    () => COLUMNS.filter((column) => fields.includes(column.key)),
    [fields],
  );

  const valueOf = (entry: TemuDraftEntry, key: BatchEditField) =>
    draft[entry.id]?.[key] !== undefined
      ? String(draft[entry.id]?.[key] ?? "")
      : cellValue(entry, edits[entry.id], key);

  const setCell = (entry: TemuDraftEntry, key: BatchEditField, value: string) => {
    setDraft((current) => {
      const row = { ...(current[entry.id] ?? {}) };
      row[key] = value;
      return { ...current, [entry.id]: row };
    });
  };

  const applyColumnValues = (key: BatchEditField, values: string[]) => {
    const previous: Record<string, string | undefined> = {};
    const next = { ...draft };
    rows.forEach((entry, index) => {
      previous[entry.id] = draft[entry.id]?.[key];
      next[entry.id] = { ...(next[entry.id] ?? {}), [key]: values[index] ?? "" };
    });
    setDraft(next);
    setLastFill({ key, previous });
    setActiveColumn(null);
  };

  const undoFill = () => {
    if (!lastFill) return;
    const next = { ...draft };
    Object.entries(lastFill.previous).forEach(([id, value]) => {
      const row = { ...(next[id] ?? {}) };
      if (value === undefined) delete row[lastFill.key];
      else row[lastFill.key] = value;
      next[id] = row;
    });
    setDraft(next);
    setLastFill(null);
  };

  const dirtyRows = Object.keys(draft).length;

  const toggleField = (key: BatchEditField) => {
    setFields((current) =>
      current.includes(key) ? current.filter((item) => item !== key) : [...current, key],
    );
  };
  const groupFields = (group: Column["group"]) =>
    COLUMNS.filter((column) => column.group === group).map((column) => column.key);
  const groupAllOn = (group: Column["group"]) =>
    groupFields(group).every((key) => fields.includes(key));

  const save = () => {
    const next: Record<string, TemuDraftEdit> = { ...edits };
    Object.entries(draft).forEach(([id, patch]) => {
      const merged: TemuDraftEdit = { ...(next[id] ?? {}) };
      (Object.keys(patch) as BatchEditField[]).forEach((key) => {
        const value = String(patch[key] ?? "");
        if (value === "") delete merged[key];
        else merged[key] = value;
      });
      if (Object.keys(merged).length) next[id] = merged;
      else delete next[id];
    });
    onSave(next);
  };

  // Esc 关闭：整页覆盖时必须留一条键盘退路（弹窗开着时先关弹窗）
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !activeColumn) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, activeColumn]);

  // 切页签时自己收掉：整页浮层是 portal 到 body 的，页签只是 hidden
  useEffect(() => {
    const onSwitch = () => onClose();
    window.addEventListener("mainpg:tab-switch", onSwitch);
    return () => window.removeEventListener("mainpg:tab-switch", onSwitch);
  }, [onClose]);

  return createPortal(
    <div className="temu-batch-edit" role="dialog" aria-modal="true" aria-label="批量编辑">
      <header className="temu-batch-edit-head">
        <div>
          <strong>批量编辑</strong>
          <span>
            Temu半托管 / 成品库 · 已选 {entries.length} 条
            {removed.length ? `，本页移除 ${removed.length} 条` : ""}
          </span>
        </div>
        <div className="temu-batch-edit-head-actions">
          <button type="button" className="temu-btn" onClick={onClose}>
            取消
          </button>
          <button type="button" className="temu-btn temu-btn-primary" onClick={save}>
            保存{dirtyRows ? `（改了 ${dirtyRows} 条）` : ""}
          </button>
        </div>
      </header>

      <section className="temu-batch-edit-fields">
        <div className="temu-batch-edit-fields-head">
          <strong>选择批量编辑的信息</strong>
          <span className="temu-muted">勾上的字段会出现在下面的表格里；只保存真的改过的值</span>
        </div>
        <label className="temu-batch-edit-all">
          <input
            type="checkbox"
            checked={COLUMNS.every((column) => fields.includes(column.key))}
            onChange={(event) =>
              setFields(event.target.checked ? COLUMNS.map((column) => column.key) : [])
            }
          />
          全部
        </label>
        <div className="temu-batch-edit-groups">
          {(["产品信息", "变种信息"] as const).map((group) => (
            <div key={group} className="temu-batch-edit-group">
              <label className="temu-batch-edit-group-head">
                <input
                  type="checkbox"
                  checked={groupAllOn(group)}
                  onChange={(event) =>
                    setFields((current) => {
                      const keys = groupFields(group);
                      return event.target.checked
                        ? Array.from(new Set([...current, ...keys]))
                        : current.filter((key) => !keys.includes(key));
                    })
                  }
                />
                {group}
              </label>
              <div className="temu-batch-edit-group-items">
                {COLUMNS.filter((column) => column.group === group).map((column) => (
                  <label key={column.key}>
                    <input
                      type="checkbox"
                      checked={fields.includes(column.key)}
                      onChange={() => toggleField(column.key)}
                    />
                    {column.label}
                  </label>
                ))}
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="temu-batch-edit-tools">
        <p className="temu-batch-edit-tip">
          格子直接改；<b className="temu-batch-edit-dirty-legend">黄底</b> = 改过（点保存才生效）；
          整列改点列头上的「批量改」。输入框留空 = 取消这条的覆盖，回到源数据。
        </p>
        {lastFill ? (
          <button type="button" className="temu-link" onClick={undoFill}>
            撤销上次批量改（{COLUMNS.find((column) => column.key === lastFill.key)?.label}）
          </button>
        ) : null}
      </section>

      <div className="temu-batch-edit-table-wrap">
        <table className="temu-batch-edit-table">
          <thead>
            <tr>
              <th className="col-thumb">产品图片</th>
              {visibleColumns.map((column) => (
                <th key={column.key} style={{ width: column.width }}>
                  <span className="temu-batch-edit-th-label">{column.label}</span>
                  <button
                    type="button"
                    className="temu-batch-edit-th-batch"
                    onClick={() => setActiveColumn(column)}
                    title={
                      column.kind === "sku"
                        ? "按规则批量生成货号（前缀/起始值/位数/后缀）"
                        : column.kind === "number"
                          ? "批量改这一列：直接改 / 按现有值加减乘"
                          : "批量把这一列改成同一段文字"
                    }
                  >
                    批量改
                  </button>
                </th>
              ))}
              <th className="col-variants">变种</th>
              <th className="col-act">操作</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((entry) => (
              <tr key={entry.id}>
                <td className="col-thumb">
                  {entry.main_image || entry.images?.[0] ? (
                    <img src={entry.main_image || entry.images[0]} alt="" />
                  ) : (
                    <span className="temu-muted">无图</span>
                  )}
                </td>
                {visibleColumns.map((column) => {
                  const value = valueOf(entry, column.key);
                  const dirty = draft[entry.id]?.[column.key] !== undefined;
                  // 标题很长：单行输入只能看到半截，用会自动长高的多行框
                  if (column.key === "title") {
                    return (
                      <td key={column.key}>
                        <textarea
                          className={`temu-batch-edit-input temu-batch-edit-text${dirty ? " is-dirty" : ""}`}
                          value={value}
                          rows={Math.min(5, Math.max(2, Math.ceil(value.length / 42)))}
                          placeholder="—"
                          onChange={(event) => setCell(entry, column.key, event.target.value)}
                        />
                      </td>
                    );
                  }
                  return (
                    <td key={column.key}>
                      <input
                        className={`temu-batch-edit-input${dirty ? " is-dirty" : ""}`}
                        value={value}
                        placeholder="—"
                        onChange={(event) => setCell(entry, column.key, event.target.value)}
                      />
                    </td>
                  );
                })}
                <td className="col-variants">
                  {entry.variants?.length
                    ? `${entry.variants.length} 个：${entry.variants
                        .slice(0, 4)
                        .map((item) => item.color || item.sku || "—")
                        .join(" / ")}${entry.variants.length > 4 ? " …" : ""}`
                    : "单 SKU"}
                </td>
                <td className="col-act">
                  <button
                    type="button"
                    className="temu-link"
                    onClick={() => setRemoved((current) => [...current, entry.id])}
                    title="只是从这张表里去掉，草稿本身不动"
                  >
                    移除
                  </button>
                </td>
              </tr>
            ))}
            {!rows.length ? (
              <tr>
                <td className="temu-muted" colSpan={visibleColumns.length + 3}>
                  没有要编辑的行（可能都被移除了）
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      {activeColumn ? (
        <BatchColumnDialog
          label={activeColumn.label}
          kind={activeColumn.kind}
          decimals={activeColumn.decimals ?? 0}
          rows={rows.map((entry) => ({
            value: valueOf(entry, activeColumn.key),
            color: entry.spec_value_1 || entry.variants?.[0]?.color,
            size: entry.spec_value_2 || entry.variants?.[0]?.size,
          }))}
          onApply={(values) => applyColumnValues(activeColumn.key, values)}
          onClose={() => setActiveColumn(null)}
        />
      ) : null}
    </div>,
    document.body,
  );
}
