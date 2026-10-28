import { useEffect, useState } from "react";

import type { TemuParentSpec } from "../types";
import { TemuThumb } from "./TemuImageStrip";

/**
 * 变种信息一行。字段与单位对齐 Temu 刊登表：
 * 预览图 / 颜色 / 尺码 / SKU 货号 / 申报价格（分）/ 长宽高（cm）/ 重量（g）/ 建议售价 / 库存。
 */
export type VariantRow = {
  key: string;
  extCode: string;
  /** 颜色值：同一个颜色的行会合成一个 SKC */
  colorName: string;
  sizeName: string;
  supplierPrice: string;
  suggestedPrice: string;
  stock: string;
  weightG: string;
  lengthCm: string;
  widthCm: string;
  heightCm: string;
  warehouseId: string;
  /** 变种图（ERP的「预览图」）；留空＝取轮播第一张 */
  thumbUrl: string;
};

export type VariantBatch = {
  supplierPrice: string;
  suggestedPrice: string;
  stock: string;
  weightG: string;
  lengthCm: string;
  widthCm: string;
  heightCm: string;
};

export const EMPTY_BATCH: VariantBatch = {
  supplierPrice: "",
  suggestedPrice: "",
  stock: "",
  weightG: "",
  lengthCm: "",
  widthCm: "",
  heightCm: "",
};

let rowSeed = 0;

function nextKey(): string {
  rowSeed += 1;
  return `variant-${rowSeed}`;
}

/**
 * 从本机存档恢复变种行时用：把行号种子推到已有最大值之后。
 *
 * 不这么做的话，刷新后第一行 key 又是 variant-1，与恢复出来的行重复 ——
 * React 会复用错行，改价格时串行。
 */
export function withFreshRowKeys(rows: VariantRow[]): VariantRow[] {
  let max = 0;
  for (const row of rows) {
    const matched = /^variant-(\d+)$/.exec(String(row?.key ?? ""));
    if (matched) max = Math.max(max, Number(matched[1]));
  }
  rowSeed = Math.max(rowSeed, max);
  return rows;
}

/**
 * 货号片段清洗。
 *
 * 颜色/尺码是中文人写的（「透明+磁吸」「苹果18duo(左右折叠)」），直接拼进 SKU 货号会带上
 * `+`、括号、空格、斜杠这些字符 —— Temu 货号只保证字母/数字/`-`/`_` 安全，
 * 这些符号要么被拒，要么在后台搜索/对账时匹配不上。这里只删符号、保留中文与字母数字。
 */
function safeCodePart(value: string): string {
  return String(value || "")
    .replace(/[+()（）[\]{}<>《》「」,，.。;；:：/\\|*?"'`~!@#$%^&=\s]+/g, "")
    .trim();
}

/** 造一行变种；有产品货号就拼成「货号-尺码」，否则用 SKU-001。 */
export function createVariantRow(
  warehouseId: string,
  colorName: string,
  sizeName: string,
  batch: VariantBatch = EMPTY_BATCH,
  index = 0,
  extCodePrefix = "",
): VariantRow {
  const parts = [extCodePrefix || "SKU", safeCodePart(colorName), safeCodePart(sizeName)].filter(
    Boolean,
  );
  const code = parts.length > 1 ? parts.join("-") : `SKU-${String(index + 1).padStart(3, "0")}`;
  return {
    key: nextKey(),
    extCode: code,
    colorName,
    sizeName,
    supplierPrice: batch.supplierPrice,
    suggestedPrice: batch.suggestedPrice,
    stock: batch.stock,
    weightG: batch.weightG,
    lengthCm: batch.lengthCm,
    widthCm: batch.widthCm,
    heightCm: batch.heightCm,
    warehouseId,
    thumbUrl: "",
  };
}

function splitSizes(text: string): string[] {
  return text
    .split(/[\n,，、;；\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

/**
 * 变种信息表：一行一个 SKU，单位与 Temu 一致（cm / g / 分）。
 *
 * 优化：尺码列表一键铺行 + 批量编辑一次填满整列；表格长所以给横向滚动。
 */
/** 「分 → 元」提示：15000 显示 ≈ ¥150.00，避免把分当成元看。 */
function yuanHint(value: string): string {
  const cents = Number((value || "").trim());
  if (!Number.isFinite(cents) || cents <= 0) return "";
  return `≈ ¥${(cents / 100).toFixed(2)}`;
}

export function PublishSkuPanel({
  rows,
  onRowsChange,
  onUpdateRow,
  onRemoveRow,
  parentSpecs,
  colorParentId,
  colorOptions = [],
  sizeOptions = [],
  onColorParentChange,
  sizeParentId,
  onSizeParentChange,
  defaultWarehouseId,
  sizesText,
  onSizesTextChange,
  colorsText,
  onColorsTextChange,
  extCodePrefix,
  fallbackThumb,
}: {
  rows: VariantRow[];
  onRowsChange: (rows: VariantRow[]) => void;
  onUpdateRow: (key: string, patch: Partial<VariantRow>) => void;
  onRemoveRow: (key: string) => void;
  parentSpecs: TemuParentSpec[];
  colorParentId: number;
  /** 类目预设颜色值（Temu 只认预设，自己造会被判「不合法的规格属性」） */
  colorOptions?: string[];
  /** 类目预设尺码/长度值 */
  sizeOptions?: string[];
  onColorParentChange: (id: number) => void;
  sizeParentId: number;
  onSizeParentChange: (id: number) => void;
  defaultWarehouseId: string;
  sizesText: string;
  onSizesTextChange: (value: string) => void;
  /** 颜色列表：每个颜色生成一个 SKC（多颜色多 SKC） */
  colorsText: string;
  onColorsTextChange: (value: string) => void;
  /** 产品货号（ParentSKU），用来拼每个变种的 SKU 货号 */
  extCodePrefix: string;
  /** 变种图留空时显示的图（轮播第一张） */
  fallbackThumb: string;
}) {
  const [batch, setBatch] = useState<VariantBatch>(EMPTY_BATCH);
  const [batchTouched, setBatchTouched] = useState(false);

  const sizeNames = splitSizes(sizesText);
  const colorNames = splitSizes(colorsText);

  // 批量编辑默认镜像第一行（草稿带入的价格/尺寸/重量能直接看到并沿用），
  // 一旦手动改过就停止同步，避免把用户填的值又冲掉。
  useEffect(() => {
    if (batchTouched) return;
    const base = rows[0];
    if (!base) return;
    setBatch({
      supplierPrice: base.supplierPrice,
      suggestedPrice: base.suggestedPrice,
      stock: base.stock,
      weightG: base.weightG,
      lengthCm: base.lengthCm,
      widthCm: base.widthCm,
      heightCm: base.heightCm,
    });
  }, [rows, batchTouched]);

  const patchBatch = (patch: Partial<VariantBatch>) => {
    setBatchTouched(true);
    setBatch((current) => ({ ...current, ...patch }));
  };

  /** 按尺码列表重建整表；空列表就按当前行保留一个。 */
  const generate = () => {
    const sizes = sizeNames.length ? sizeNames : ["均码"];
    const colors = colorNames.length ? colorNames : ["Default"];
    const base = rows[0];
    const effective: VariantBatch = {
      supplierPrice: batch.supplierPrice.trim() || base?.supplierPrice || "",
      suggestedPrice: batch.suggestedPrice.trim() || base?.suggestedPrice || "",
      stock: batch.stock.trim() || base?.stock || "",
      weightG: batch.weightG.trim() || base?.weightG || "",
      lengthCm: batch.lengthCm.trim() || base?.lengthCm || "",
      widthCm: batch.widthCm.trim() || base?.widthCm || "",
      heightCm: batch.heightCm.trim() || base?.heightCm || "",
    };
    const next: VariantRow[] = [];
    for (const color of colors) {
      for (const size of sizes) {
        next.push(
          createVariantRow(
            defaultWarehouseId,
            color,
            size,
            effective,
            next.length,
            extCodePrefix.trim(),
          ),
        );
      }
    }
    onRowsChange(next);
  };

  /**
   * 批量编辑里填了值、但还没点「应用到全部」的字段。
   * 这些值不会进提交（表格里那格还是空的），界面上必须点出来，否则用户以为已经填好了。
   */
  const pendingBatchFields = !batchTouched
    ? []
    : (Object.keys(batch) as Array<keyof VariantBatch>).filter((key) => {
        // 只统计「用户真的在批量框里改过」的字段；没动过时 batch 是镜像第一行，
        // 拿它跟别的行比会把正常差异报成「未应用」。
        const typed = batch[key].trim();
        if (!typed) return false;
        return rows.some((row) => String(row[key] ?? "").trim() !== typed);
      });

  /** 批量编辑 → 覆盖所有行（只覆盖填了值的字段）。 */
  const applyAll = () => {
    if (!rows.length) return;
    onRowsChange(
      rows.map((row) => {
        const next = { ...row };
        for (const key of Object.keys(batch) as Array<keyof VariantBatch>) {
          const value = batch[key].trim();
          if (value) next[key] = value;
        }
        if (defaultWarehouseId) next.warehouseId = defaultWarehouseId;
        return next;
      }),
    );
  };

  return (
    <div className="temu-variant-panel">
      <div className="temu-variant-spec">
        <label className="temu-field">
          <span>
            变种属性名称一 / 值一<em className="temu-required">*</em>
          </span>
          <div className="temu-spec-row">
            <select
              className="temu-input"
              value={colorParentId}
              onChange={(event) => onColorParentChange(Number(event.target.value))}
            >
              {parentSpecs.map((item) => (
                <option key={item.parentSpecId} value={item.parentSpecId}>
                  {item.parentSpecName}
                </option>
              ))}
            </select>
            <input
              className="temu-input"
              value={colorsText}
              onChange={(event) => onColorsTextChange(event.target.value)}
              placeholder="颜色值，多个用逗号分隔：Black,Brown"
            />
          </div>
        </label>
        <label className="temu-field">
          <span>变种属性名称二</span>
          <select
            className="temu-input"
            value={sizeParentId}
            onChange={(event) => onSizeParentChange(Number(event.target.value))}
          >
            {parentSpecs.map((item) => (
              <option key={item.parentSpecId} value={item.parentSpecId}>
                {item.parentSpecName}
              </option>
            ))}
          </select>
        </label>
        <label className="temu-field">
          <span>变种属性值二（尺码，逗号分隔）</span>
          <input
            className="temu-input"
            value={sizesText}
            onChange={(event) => onSizesTextChange(event.target.value)}
            placeholder="S,M,L 或 30cm,40cm"
            onKeyDown={(event) => {
              if (event.key === "Enter") generate();
            }}
          />
        </label>
        <button type="button" className="temu-btn temu-btn-primary temu-variant-generate" onClick={generate}>
          生成 {(colorNames.length || 1) * (sizeNames.length || 1)} 个变种
        </button>
      </div>

      <div className="temu-batch">
        <div className="temu-batch-head">
          <strong>批量编辑</strong>
          <span className="temu-muted">填了才覆盖；价格单位分（¥12.99 填 1299），尺寸 cm，重量 g</span>
          {pendingBatchFields.length > 0 && (
            <em className="temu-batch-pending">有 {pendingBatchFields.length} 项还没应用</em>
          )}
          <button
            type="button"
            className={`temu-btn temu-btn-sm${pendingBatchFields.length ? " is-urgent" : ""}`}
            onClick={applyAll}
            disabled={!rows.length}
          >
            应用到全部
          </button>
        </div>
        <div className="temu-batch-grid">
          <label>
            申报价格（分）
            <input className="temu-input" inputMode="numeric" value={batch.supplierPrice} onChange={(e) => patchBatch({ supplierPrice: e.target.value })} placeholder="1299" />
            <em className="temu-price-preview">{yuanHint(batch.supplierPrice)}</em>
          </label>
          <label>
            建议售价
            <input className="temu-input" inputMode="numeric" value={batch.suggestedPrice} onChange={(e) => patchBatch({ suggestedPrice: e.target.value })} placeholder="可选" />
          </label>
          <label>
            库存
            <input className="temu-input" inputMode="numeric" value={batch.stock} onChange={(e) => patchBatch({ stock: e.target.value })} placeholder="50" />
          </label>
          <label>
            长 cm
            <input className="temu-input" inputMode="decimal" value={batch.lengthCm} onChange={(e) => patchBatch({ lengthCm: e.target.value })} />
          </label>
          <label>
            宽 cm
            <input className="temu-input" inputMode="decimal" value={batch.widthCm} onChange={(e) => patchBatch({ widthCm: e.target.value })} />
          </label>
          <label>
            高 cm
            <input className="temu-input" inputMode="decimal" value={batch.heightCm} onChange={(e) => patchBatch({ heightCm: e.target.value })} />
          </label>
          <label>
            重量 g
            <input className="temu-input" inputMode="decimal" value={batch.weightG} onChange={(e) => patchBatch({ weightG: e.target.value })} />
          </label>
        </div>
      </div>

      <div className="temu-variant-scroll">
        <div className="temu-variant-table">
          {/* 列对齐 Temu 的变种信息：预览图 / 尺码 / SKU 货号 / 申报价格 / 长宽高 / 重量 / 建议售价 / 库存。
              颜色由上面的「变种属性值一」统一指定，发货仓库由「默认发货仓库」统一指定，不再逐行重复。 */}
          <div className="temu-variant-row is-head">
            <span>预览图</span>
            <span>颜色</span>
            <span>尺码</span>
            <span>SKU 货号</span>
            <span>申报价格(分)</span>
            <span>长</span>
            <span>宽</span>
            <span>高</span>
            <span>重量(g)</span>
            <span>建议售价</span>
            <span>库存</span>
            <span />
          </div>
          {rows.length === 0 && <p className="temu-empty">还没有变种：填好上面的尺码，点「生成变种」。</p>}
          {rows.map((row) => (
            <div className="temu-variant-row" key={row.key}>
              <span className="temu-variant-thumb" title={row.thumbUrl || "未单独设置，取轮播第一张"}>
                <TemuThumb path={row.thumbUrl || fallbackThumb} onOpen={() => undefined} />
              </span>
              <input
                className="temu-input"
                list="temu-color-options"
                value={row.colorName}
                onChange={(event) => onUpdateRow(row.key, { colorName: event.target.value })}
                placeholder="选类目预设颜色"
                title="Temu 只认类目预设颜色，自己输入会被拒"
              />
              <input
                className="temu-input"
                list="temu-size-options"
                value={row.sizeName}
                onChange={(event) => onUpdateRow(row.key, { sizeName: event.target.value })}
                placeholder="选预设值"
              />
              <input
                className="temu-input"
                value={row.extCode}
                onChange={(event) => onUpdateRow(row.key, { extCode: event.target.value })}
              />
              <input
                className="temu-input"
                inputMode="numeric"
                value={row.supplierPrice}
                onChange={(event) => onUpdateRow(row.key, { supplierPrice: event.target.value })}
                placeholder="1299"
              />
              <input
                className="temu-input"
                inputMode="decimal"
                value={row.lengthCm}
                onChange={(event) => onUpdateRow(row.key, { lengthCm: event.target.value })}
              />
              <input
                className="temu-input"
                inputMode="decimal"
                value={row.widthCm}
                onChange={(event) => onUpdateRow(row.key, { widthCm: event.target.value })}
              />
              <input
                className="temu-input"
                inputMode="decimal"
                value={row.heightCm}
                onChange={(event) => onUpdateRow(row.key, { heightCm: event.target.value })}
              />
              <input
                className="temu-input"
                inputMode="decimal"
                value={row.weightG}
                onChange={(event) => onUpdateRow(row.key, { weightG: event.target.value })}
              />
              <input
                className="temu-input"
                inputMode="numeric"
                value={row.suggestedPrice}
                onChange={(event) => onUpdateRow(row.key, { suggestedPrice: event.target.value })}
                placeholder="可选"
              />
              <input
                className={`temu-input${
                  String(row.stock).trim() === "" || Number(row.stock) < 0 ? " is-bad" : ""
                }`}
                inputMode="numeric"
                value={row.stock}
                onChange={(event) => onUpdateRow(row.key, { stock: event.target.value })}
                placeholder="0 也可"
                title="可以填 0：Temu 会建品但前台不可售（售罄），卖之前补库存即可"
              />
              <button
                type="button"
                className="temu-variant-del"
                onClick={() => onRemoveRow(row.key)}
                disabled={rows.length <= 1}
                title="删除这一行"
              >
                ×
              </button>
            </div>
          ))}
        </div>
      </div>
      <p className="temu-muted">
        共 {rows.length} 个变种 / {new Set(rows.map((row) => row.colorName.trim() || "Default")).size} 个颜色。
        Temu 变种上限 20 个；同色下尺码不能重复、SKU 货号不能重复。
      </p>
      {/* Temu 只认类目预设规格值，这两个列表给输入框做下拉候选 */}
      <datalist id="temu-color-options">
        {colorOptions.map((option) => (
          <option key={option} value={option} />
        ))}
      </datalist>
      <datalist id="temu-size-options">
        {sizeOptions.map((option) => (
          <option key={option} value={option} />
        ))}
      </datalist>
    </div>
  );
}
