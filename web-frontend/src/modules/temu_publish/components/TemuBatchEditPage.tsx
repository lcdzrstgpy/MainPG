import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import type { TemuDraftEdit, TemuDraftEntry } from "../types";
import { BatchColumnDialog, type ColumnKind } from "./BatchColumnDialog";
import {
  SKU_FIELD_COLUMNS,
  resolveSkuField,
  variantKeysOf,
  type SkuField,
  type SkuFieldMap,
  type SkuFieldStore,
} from "../data/skuPricing";

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

/** 拆行模式可改的列（价/货号/库存/尺寸/重量），复用同一套批量改弹窗 */
const SKU_COLUMNS: Column[] = SKU_FIELD_COLUMNS.map((item) => ({
  key: item.key,
  label: item.label,
  group: "变种信息",
  width: item.width,
  kind: item.kind,
  decimals: item.decimals,
}));

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

/** 默认全部勾上：进来先看到全表，不要的再取消（默认藏着字段反而让人找不到） */
const DEFAULT_FIELDS: BatchEditField[] = COLUMNS.map((column) => column.key);

/**
 * 未保存改动的暂存（sessionStorage）。
 *
 * 切工作区页签 / 刷新会把整个编辑页连同 state 一起卸载，改到一半的东西全没 ——
 * 「取消」也只是关页不该丢数据（下次进来接着改，保存才落库并清掉暂存）。
 */
const PENDING_KEY = "temu_batch_edit_pending_v1";
type PendingState = { draft: Record<string, TemuDraftEdit>; skuDraft: SkuFieldStore };
function loadPending(): PendingState | null {
  try {
    const raw = sessionStorage.getItem(PENDING_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PendingState;
    if (!parsed || typeof parsed !== "object") return null;
    return { draft: parsed.draft ?? {}, skuDraft: parsed.skuDraft ?? {} };
  } catch {
    return null;
  }
}

type Props = {
  /** 勾选的草稿（已合并过本机编辑） */
  entries: TemuDraftEntry[];
  /** 现有编辑（保存时在它上面合并） */
  edits: Record<string, TemuDraftEdit>;
  /** 现有变种覆盖（草稿 id → 变种 key → 改过的字段） */
  skuFields: SkuFieldStore;
  onSave: (next: Record<string, TemuDraftEdit>) => void;
  onSaveSkuFields: (next: SkuFieldStore) => void;
  onClose: () => void;
  /** 从预检的「去补」进来：高亮这几列（用户是带着「缺什么」来的，别让他再满表找） */
  focusFields?: BatchEditField[];
};

/** 一条草稿在表格里显示的值：本机编辑 > 源数据 */
function cellValue(entry: TemuDraftEntry, edit: TemuDraftEdit | undefined, key: BatchEditField): string {
  const local = edit?.[key];
  if (local !== undefined && local !== null && local !== "") return String(local);
  const raw = entry[key as keyof TemuDraftEntry];
  if (raw === undefined || raw === null) return "";
  return String(raw);
}

export function TemuBatchEditPage({
  entries,
  edits,
  skuFields,
  onSave,
  onSaveSkuFields,
  onClose,
  focusFields,
}: Props) {
  /** 「去补」进来的目标列要整列高亮（表头 + 格子） */
  const isFocus = (key: BatchEditField) => Boolean(focusFields?.includes(key));
  /** 本地待保存的改动：id → 字段 → 值（'' = 清掉覆盖，回落到源数据） */
  const [draft, setDraft] = useState<Record<string, TemuDraftEdit>>(() => loadPending()?.draft ?? {});
  const [fields, setFields] = useState<BatchEditField[]>(DEFAULT_FIELDS);
  /** 被「移除」暂时不看的行（只是从这张表里去掉，不动草稿本身） */
  const [removed, setRemoved] = useState<string[]>([]);
  /**
   * 勾选的行（默认全勾）。
   *
   * 「改这一列」不该强迫改全部：几十条里通常只想动其中几条。
   * 默认全勾 = 行为和不加勾选时完全一致，先取消几条再改才是常用路径。
   */
  const [picked, setPicked] = useState<string[]>(() => entries.map((entry) => entry.id));
  /** 正在批量改哪一列（null = 没开弹窗） */
  const [activeColumn, setActiveColumn] = useState<Column | null>(null);
  /** 上一次批量改动改前的值：填错了能整批回退 */
  const [lastFill, setLastFill] = useState<
    null | { key: BatchEditField; previous: Record<string, string | undefined> }
  >(null);
  /**
   * 「按变种拆行」：一条草稿 4 个颜色 → 表格里 4 行，每个变种单独定价。
   *
   * 拆行只放开**申报价格**（其余字段是整条共用的，拆开改会产生
   * 「同一件商品两个标题」这种矛盾数据）。所以拆行模式下表格只保留
   * 图片 / 变种 / 申报价 / 库存(只读) 四列。
   */
  const [splitSku, setSplitSku] = useState(false);
  /** 变种覆盖草稿：保存时整体提交（空值 = 取消该变种的覆盖，回落整条值） */
  const [skuDraft, setSkuDraft] = useState<SkuFieldStore>(
    () => loadPending()?.skuDraft ?? JSON.parse(JSON.stringify(skuFields)),
  );
  /** 刚才是从暂存里捞回来的：提示一句，免得以为丢了又重改 */
  const [restoredNote, setRestoredNote] = useState(() => Boolean(loadPending()));
  /** 改动同步进暂存：切页签/刷新都丢不了；保存时清掉 */
  useEffect(() => {
    const hasDraft = Object.keys(draft).length > 0;
    const hasSku = Object.values(skuDraft).some((map) => Object.keys(map ?? {}).length > 0);
    try {
      if (hasDraft || hasSku) {
        sessionStorage.setItem(PENDING_KEY, JSON.stringify({ draft, skuDraft } satisfies PendingState));
      } else {
        sessionStorage.removeItem(PENDING_KEY);
      }
    } catch {
      // 存储满了就当没有暂存，别影响编辑本身
    }
  }, [draft, skuDraft]);
  /** 拆行模式下勾选的变种：`草稿id::变种key` */
  const [pickedSku, setPickedSku] = useState<string[]>(() =>
    entries.flatMap((entry) =>
      variantKeysOf(entry).map((variant) => `${entry.id}::${variant.key}`),
    ),
  );

  const rows = useMemo(
    () => entries.filter((entry) => !removed.includes(entry.id)),
    [entries, removed],
  );
  const visibleColumns = useMemo(
    () => COLUMNS.filter((column) => fields.includes(column.key)),
    [fields],
  );
  /** 勾选的行：批量改只动这些（单个格子照旧想改哪行改哪行） */
  const pickedRows = useMemo(
    () => rows.filter((entry) => picked.includes(entry.id)),
    [rows, picked],
  );
  const allPicked = rows.length > 0 && rows.every((entry) => picked.includes(entry.id));
  /** 拆行模式的行：草稿 × 变种 */
  const skuRows = useMemo(
    () =>
      rows.flatMap((entry) =>
        variantKeysOf(entry).map((variant) => ({
          entry,
          variant,
          rowKey: `${entry.id}::${variant.key}`,
        })),
      ),
    [rows],
  );
  const pickedSkuRows = useMemo(
    () => skuRows.filter((row) => pickedSku.includes(row.rowKey)),
    [skuRows, pickedSku],
  );
  const allSkuPicked = skuRows.length > 0 && skuRows.every((row) => pickedSku.includes(row.rowKey));
  /** 只勾了一部分时，表头那个全选框要显示成「半选」，否则看不出状态 */
  const selectAllRef = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    if (selectAllRef.current) {
      selectAllRef.current.indeterminate = !allPicked && pickedRows.length > 0;
    }
  }, [allPicked, pickedRows.length]);

  const togglePick = (id: string) =>
    setPicked((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
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

  /** 改一个变种的某个字段：空串 = 取消覆盖（回落整条值） */
  const setSkuField = (entryId: string, variantKey: string, field: SkuField, value: string) => {
    setSkuDraft((current) => {
      const map = { ...(current[entryId] ?? {}) };
      const item = { ...(map[variantKey] ?? {}) };
      if (value.trim() === "") delete item[field];
      else item[field] = value;
      if (Object.keys(item).length) map[variantKey] = item;
      else delete map[variantKey];
      return { ...current, [entryId]: map };
    });
  };

  /** 拆行模式的批量改：按顺序写回勾选的变种（某一个字段） */
  const applySkuValues = (field: SkuField, values: string[]) => {
    setSkuDraft((current) => {
      const next = { ...current };
      pickedSkuRows.forEach(({ entry, variant }, index) => {
        const map = { ...(next[entry.id] ?? {}) };
        const item = { ...(map[variant.key] ?? {}) };
        const value = String(values[index] ?? "").trim();
        if (value === "") delete item[field];
        else item[field] = value;
        if (Object.keys(item).length) map[variant.key] = item;
        else delete map[variant.key];
        next[entry.id] = map;
      });
      return next;
    });
    setActiveColumn(null);
  };

  /**
   * 拆行模式某格的「回落值」：草稿自带的变体值优先于整条值
   * （和发布链路一致：人设的变种覆盖 > 草稿变体值 > 整条值）。
   */
  const skuBaseOf = (
    entry: TemuDraftEntry,
    variant: { color: string; size: string },
    field: SkuField,
  ): string => {
    const found = (entry.variants ?? []).find(
      (item) => String(item.color ?? "").trim() === variant.color && String(item.size ?? "").trim() === variant.size,
    );
    if (found) {
      if (field === "declared_price" && typeof found.price === "number" && found.price > 0) {
        return String(found.price);
      }
      if (field === "stock" && typeof found.stock === "number") return String(found.stock);
      if (field === "sku" && found.sku) return found.sku;
    }
    return valueOf(entry, field);
  };

  const applyColumnValues = (key: BatchEditField, values: string[]) => {
    const previous: Record<string, string | undefined> = {};
    const next = { ...draft };
    pickedRows.forEach((entry, index) => {
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
  /** 变种覆盖改过几条（和进来时比） */
  const dirtySkuRows = entries.filter((entry) => {
    const before = skuFields[entry.id] ?? {};
    const after = skuDraft[entry.id] ?? {};
    const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
    return Array.from(keys).some((key) => {
      const a = after[key] ?? {};
      const b = before[key] ?? {};
      const fields = new Set([...Object.keys(a), ...Object.keys(b)]);
      return Array.from(fields).some(
        (field) => String(a[field as SkuField] ?? "").trim() !== String(b[field as SkuField] ?? "").trim(),
      );
    });
  }).length;

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

    // 变种覆盖：只留非空值；一条草稿一个变种都没设就整条删掉
    const nextSku: SkuFieldStore = {};
    Object.entries(skuDraft).forEach(([id, map]) => {
      const clean: SkuFieldMap = {};
      Object.entries(map ?? {}).forEach(([key, fields]) => {
        const item: Record<string, string> = {};
        Object.entries(fields ?? {}).forEach(([field, value]) => {
          const text = String(value ?? "").trim();
          if (text) item[field] = text;
        });
        if (Object.keys(item).length) clean[key] = item;
      });
      if (Object.keys(clean).length) nextSku[id] = clean;
    });
    onSaveSkuFields(nextSku);
    // 落库成功才清暂存；「取消」/切页签不清，下次进来接着改
    try {
      sessionStorage.removeItem(PENDING_KEY);
    } catch {
      // 清不掉也无妨：下次保存会覆盖
    }
    setRestoredNote(false);
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
            保存
            {dirtyRows || dirtySkuRows
              ? `（改了 ${dirtyRows} 条${dirtySkuRows ? ` / ${dirtySkuRows} 条变种` : ""}）`
              : ""}
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
          {restoredNote ? (
            <>
              <b className="temu-batch-edit-focus-legend">已恢复上次未保存的改动</b>（切页签/刷新不再丢；点保存才生效）。
            </>
          ) : null}
          {focusFields?.length ? (
            <>
              <b className="temu-batch-edit-focus-legend">
                已定位到：
                {focusFields.map((key) => COLUMNS.find((column) => column.key === key)?.label ?? key).join("、")}
              </b>
              （从预检的「去补」进来，这几列已高亮）。
            </>
          ) : null}
          {splitSku ? (
            <>
              一条草稿按变种拆成多行：<b>价格 / 货号 / 库存 / 尺寸 / 重量</b> 都可按变种单独设。
              输入框留空 = 跟随整条值；批量改只动勾选的变种（{pickedSkuRows.length}/{skuRows.length}）。
            </>
          ) : (
            <>
              格子直接改；<b className="temu-batch-edit-dirty-legend">黄底</b> = 改过（点保存才生效）。
              列头「批量改」只动<b>勾选的行</b>（{pickedRows.length}/{rows.length} 条）；
              输入框留空 = 取消这条的覆盖，回到源数据。
            </>
          )}
        </p>
        <div className="temu-batch-edit-tools-right">
          <label className="temu-batch-edit-split">
            <input
              type="checkbox"
              checked={splitSku}
              onChange={(event) => {
                setSplitSku(event.target.checked);
                setActiveColumn(null);
              }}
            />
            按变种拆行（单独定价）
          </label>
          {lastFill && !splitSku ? (
            <button type="button" className="temu-link" onClick={undoFill}>
              撤销上次批量改（{COLUMNS.find((column) => column.key === lastFill.key)?.label}）
            </button>
          ) : null}
        </div>
      </section>

      <div className="temu-batch-edit-table-wrap">
        {splitSku ? (
          <table className="temu-batch-edit-table temu-batch-edit-sku-table">
            <thead>
              <tr>
                <th className="col-pick">
                  <input
                    type="checkbox"
                    checked={allSkuPicked}
                    onChange={(event) =>
                      setPickedSku(event.target.checked ? skuRows.map((row) => row.rowKey) : [])
                    }
                    title={allSkuPicked ? "取消全选变种" : "全选变种"}
                  />
                </th>
                <th className="col-thumb">产品图片</th>
                <th style={{ width: 170 }}>变种</th>
                {SKU_COLUMNS.map((column) => (
                  <th
                    key={column.key}
                    style={{ width: column.width }}
                    className={isFocus(column.key) ? "is-focus" : undefined}
                  >
                    <span className="temu-batch-edit-th-label">{column.label}</span>
                    <button
                      type="button"
                      className="temu-batch-edit-th-batch"
                      disabled={!pickedSkuRows.length}
                      onClick={() => setActiveColumn(column)}
                      title={
                        pickedSkuRows.length
                          ? `批量改勾选的 ${pickedSkuRows.length} 个变种的${column.label}`
                          : "先勾选要改的变种"
                      }
                    >
                      批量改
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {skuRows.map(({ entry, variant, rowKey }) => (
                <tr key={rowKey} className={pickedSku.includes(rowKey) ? "" : "is-unpicked"}>
                  <td className="col-pick">
                    <input
                      type="checkbox"
                      checked={pickedSku.includes(rowKey)}
                      onChange={() =>
                        setPickedSku((current) =>
                          current.includes(rowKey)
                            ? current.filter((item) => item !== rowKey)
                            : [...current, rowKey],
                        )
                      }
                      title="勾上的变种才会被「批量改」改到"
                    />
                  </td>
                  <td className="col-thumb">
                    {entry.main_image || entry.images?.[0] ? (
                      <img src={entry.main_image || entry.images[0]} alt="" />
                    ) : (
                      <span className="temu-muted">无图</span>
                    )}
                  </td>
                  <td>
                    <strong>{variant.label}</strong>
                    <span className="temu-muted">{(entry.title || "").slice(0, 18)}</span>
                  </td>
                  {SKU_COLUMNS.map((column) => {
                    const field = column.key as SkuField;
                    const base = skuBaseOf(entry, variant, field);
                    const own = skuDraft[entry.id]?.[variant.key]?.[field] ?? "";
                    return (
                      <td key={column.key} className={isFocus(column.key) ? "is-focus" : undefined}>
                        <input
                          className={`temu-batch-edit-input${own ? " is-dirty" : ""}`}
                          value={own}
                          placeholder={base || "—"}
                          title={`留空 = 用整条值 ${base || "（未设）"}${base ? `；当前生效 ${own || base}` : ""}`}
                          onChange={(event) =>
                            setSkuField(entry.id, variant.key, field, event.target.value)
                          }
                        />
                      </td>
                    );
                  })}
                </tr>
              ))}
              {!skuRows.length ? (
                <tr>
                  <td className="temu-muted" colSpan={SKU_COLUMNS.length + 3}>
                    勾选的草稿没有变种（单 SKU）——关掉「按变种拆行」直接改整条值即可。
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        ) : null}
        <table className="temu-batch-edit-table" hidden={splitSku}>
          <thead>
            <tr>
              <th className="col-pick">
                <input
                  ref={selectAllRef}
                  type="checkbox"
                  checked={allPicked}
                  onChange={(event) =>
                    setPicked(event.target.checked ? rows.map((entry) => entry.id) : [])
                  }
                  title={allPicked ? "取消全选" : "全选"}
                />
              </th>
              <th className="col-thumb">产品图片</th>
              {visibleColumns.map((column) => (
                <th
                  key={column.key}
                  style={{ width: column.width }}
                  className={isFocus(column.key) ? "is-focus" : undefined}
                >
                  <span className="temu-batch-edit-th-label">{column.label}</span>
                  <button
                    type="button"
                    className="temu-batch-edit-th-batch"
                    disabled={!pickedRows.length}
                    onClick={() => setActiveColumn(column)}
                    title={
                      !pickedRows.length
                        ? "先勾选要改的行"
                        : column.kind === "sku"
                          ? `按规则批量生成货号（勾选的 ${pickedRows.length} 行）`
                          : column.kind === "number"
                            ? `批量改这一列：直接改 / 按现有值加减乘（勾选的 ${pickedRows.length} 行）`
                            : `批量把这一列改成同一段文字（勾选的 ${pickedRows.length} 行）`
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
              <tr key={entry.id} className={picked.includes(entry.id) ? "" : "is-unpicked"}>
                <td className="col-pick">
                  <input
                    type="checkbox"
                    checked={picked.includes(entry.id)}
                    onChange={() => togglePick(entry.id)}
                    title="勾上的行才会被「批量改」改到"
                  />
                </td>
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
                      <td key={column.key} className={isFocus(column.key) ? "is-focus" : undefined}>
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
                    <td key={column.key} className={isFocus(column.key) ? "is-focus" : undefined}>
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
                <td className="temu-muted" colSpan={visibleColumns.length + 4}>
                  没有要编辑的行（可能都被移除了）
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      {activeColumn ? (
        splitSku ? (
          <BatchColumnDialog
            label={activeColumn.label}
            kind={activeColumn.kind}
            decimals={activeColumn.decimals ?? 0}
            rows={pickedSkuRows.map(({ entry, variant }) => ({
              value: resolveSkuField(
                skuDraft[entry.id]?.[variant.key],
                activeColumn.key as SkuField,
                skuBaseOf(entry, variant, activeColumn.key as SkuField),
              ),
              color: variant.color,
              size: variant.size,
            }))}
            onApply={(values) => applySkuValues(activeColumn.key as SkuField, values)}
            onClose={() => setActiveColumn(null)}
          />
        ) : (
          <BatchColumnDialog
            label={activeColumn.label}
            kind={activeColumn.kind}
            decimals={activeColumn.decimals ?? 0}
            rows={pickedRows.map((entry) => ({
              value: valueOf(entry, activeColumn.key),
              color: entry.spec_value_1 || entry.variants?.[0]?.color,
              size: entry.spec_value_2 || entry.variants?.[0]?.size,
            }))}
            onApply={(values) => applyColumnValues(activeColumn.key, values)}
            onClose={() => setActiveColumn(null)}
          />
        )
      ) : null}
    </div>,
    document.body,
  );
}
