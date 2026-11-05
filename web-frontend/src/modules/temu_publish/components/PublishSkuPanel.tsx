import { useEffect, useRef, useState } from "react";

import type { TemuParentSpec } from "../types";
import { TemuThumb } from "./TemuImageStrip";
import { mergeGeneratedRows, variantCoverage } from "../data/variantMerge";

/**
 * 变种信息一行。字段与单位对齐 Temu 刊登表：
 * 预览图 / 颜色 / 尺码 / SKU 货号 / 申报价格（元）/ 长宽高（cm）/ 重量（g）/ 建议售价（元）/ 库存。
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
  // 库存默认 0：先按「售罄」建品，开卖前再补。
  // 0 是 Temu 接受的合法值，商品会处于售罄/不可售状态；提交后后端也会给一条提醒。
  stock: "0",
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
 * 变种信息表：一行一个 SKU，尺寸 cm、重量 g，价格用「元」（提交前才换算成分）。
 *
 * 优化：尺码列表一键铺行 + 批量编辑一次填满整列；表格长所以给横向滚动。
 */
/** 「分 → 元」提示：15000 显示 ≈ ¥150.00，避免把分当成元看。 */
export function PublishSkuPanel({
  rows,
  onRowsChange,
  onUpdateRow,
  onRemoveRow,
  parentSpecs,
  colorParentId,
  colorOptions = [],
  sizeOptions = [],
  colorSpecName = "属性一",
  sizeSpecName = "属性二",
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
  carouselImages = [],
  onPreviewImage,
  singleSku = false,
}: {
  rows: VariantRow[];
  onRowsChange: (rows: VariantRow[]) => void;
  onUpdateRow: (key: string, patch: Partial<VariantRow>) => void;
  onRemoveRow: (key: string) => void;
  parentSpecs: TemuParentSpec[];
  colorParentId: number;
  /** 类目预设「第一列」值（Temu 只认预设，自己造会被判「不合法的规格属性」） */
  colorOptions?: string[];
  /** 类目预设「第二列」值 */
  sizeOptions?: string[];
  /** 两列在 Temu 那边实际叫什么（颜色 / 款式 / 型号 / 容量…），界面文案要用它 */
  colorSpecName?: string;
  sizeSpecName?: string;
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
  /** 产品轮播图列表：「引用产品轮播图」用，顺序和发布时一致 */
  carouselImages?: string[];
  /** 点缩略图放大（不传就不可点，例如在只读场景） */
  onPreviewImage?: (src: string, alt?: string) => void;
  /**
   * 类目没有销售属性（如「桌布/亚麻布」）：Temu 不给颜色/尺码变种，
   * 只能建单 SKU 商品。此时隐藏变种生成区与颜色/尺码两列，只保留第 1 行的价格尺寸。
   */
  singleSku?: boolean;
}) {
  const [batch, setBatch] = useState<VariantBatch>(EMPTY_BATCH);
  const [batchTouched, setBatchTouched] = useState(false);
  /** 变种图编辑菜单：哪一行打开着、当前展开哪一级 */
  const [thumbMenuKey, setThumbMenuKey] = useState<string | null>(null);
  const [thumbMenuView, setThumbMenuView] = useState<"root" | "carousel" | "url" | "scope">("root");
  /** 已经选好、还没决定应用范围的图 */
  const [pendingThumb, setPendingThumb] = useState("");
  const [thumbUrlDraft, setThumbUrlDraft] = useState("");
  /** 菜单坐标（fixed）：变种表是横向滚动容器，absolute 会被裁掉 */
  const [thumbMenuPos, setThumbMenuPos] = useState<{ top: number; left: number } | null>(null);
  const thumbFileRef = useRef<HTMLInputElement>(null);

  /** 点空白处收起变种图菜单（不然它一直挂在表格上挡下一行） */
  useEffect(() => {
    if (!thumbMenuKey) return;
    const onDown = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest(".temu-thumb-menu") || target?.closest(".temu-thumb-trigger")) return;
      setThumbMenuKey(null);
      setThumbMenuView("root");
      setPendingThumb("");
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [thumbMenuKey]);

  const sizeNames = splitSizes(sizesText);
  const colorNames = splitSizes(colorsText);

  /**
   * 表格与「颜色 × 尺码」的对应关系。
   *
   * 草稿带入时已经自动铺满，这个按钮大多数时候点下去毫无变化 —— 一直亮着
   * 只会让人问「生成变种到底有什么用」。所以组合齐了就置灰显示「已生成」，
   * 只有用户改了上面的取值、或者手动加删过行，才亮起来提示还差几个。
   */
  const coverage = variantCoverage(
    rows,
    colorNames.length ? colorNames : ["Default"],
    sizeNames.length ? sizeNames : ["均码"],
  );

  /**
   * 库存为 0 的行数。
   *
   * Temu 允许 0 库存建品，但建出来的商品是「售罄 / 不可售」，买家看不到也下不了单。
   * 之前只有输入框的 title 和后端提交成功后的 warning 提到这件事 —— 用户一路填完点提交，
   * 看到「发布成功」就以为能卖了。所以在变种表下面直接说清楚。
   */
  const zeroStockCount = rows.filter((row) => {
    const text = String(row.stock ?? "").trim();
    return text === "" || Number(text) <= 0;
  }).length;

  /**
   * 输入里不在类目预设里的值 —— Temu 会直接判「不是合法的规格值」。
   *
   * 只给 datalist 是不够的：不点开输入框根本看不到有哪些可选值，用户照样手填
   * 「苹果4」这种，提交时才被打回。所以预设值要**摆在明面上**让孩子能点。
   */
  const unknownSpecValues = (values: string[], options: string[]) => {
    if (!options.length) return [];
    const allowed = new Set(options.map((item) => item.trim().toLowerCase()));
    return values.filter((item) => item.trim() && !allowed.has(item.trim().toLowerCase()));
  };
  const unknownColors = unknownSpecValues(colorNames, colorOptions);
  const unknownSizes = unknownSpecValues(sizeNames, sizeOptions);

  /** 「Temu 只认这些值」：把类目预设直接列出来，点一下追加（不用自己背）。 */
  const renderSpecChoices = (
    options: string[],
    current: string,
    onPick: (next: string) => void,
    label: string,
    limit = 10,
  ) => {
    if (!options.length) return null;
    const picked = new Set(
      current
        .split(/[,，]/)
        .map((value) => value.trim().toLowerCase())
        .filter(Boolean),
    );
    const pick = (name: string) => {
      if (picked.has(name.toLowerCase())) return;
      const head = current
        .split(/[,，]/)
        .map((value) => value.trim())
        .filter(Boolean);
      onPick([...head, name].join(","));
    };
    return (
      <div className="temu-spec-choices">
        <span className="temu-muted">Temu 只认这些{label}（点一下填入）：</span>
        {options.slice(0, limit).map((name) => (
          <button
            key={name}
            type="button"
            className={`temu-spec-chip${picked.has(name.toLowerCase()) ? " is-picked" : ""}`}
            onClick={() => pick(name)}
            title={picked.has(name.toLowerCase()) ? "已经加进上面的列表了" : `填入「${name}」`}
          >
            {name}
          </button>
        ))}
        {options.length > limit && (
          <span className="temu-muted">
            …还有 {options.length - limit} 个，输入框里可以下拉搜
          </span>
        )}
      </div>
    );
  };

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

  /**
   * 按颜色 × 尺码补齐整表。
   *
   * 以前这里是「重建」：只要点一下，表格里逐行改过的价格/库存/尺寸全被
   * 批量框（或第一行）的值冲掉 —— 用户只是想加一个尺码，回头发现 20 行
   * 的价都变回 150 了。现在按「颜色 + 尺码」认领旧行：同一格已经存在就原样
   * 保留（只同步颜色/尺码文字），只有新组合才用批量默认值填。
   */
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
    onRowsChange(
      mergeGeneratedRows(rows, colors, sizes, (color, size, index) =>
        createVariantRow(
          defaultWarehouseId,
          color,
          size,
          effective,
          index,
          extCodePrefix.trim(),
        ),
      ),
    );
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

  /**
   * 变种预览图：只改这一行 / 同颜色一起改 / 全部改。
   *
   * Temu 那边 SKC（颜色）有一张 colorImageUrl，SKU 还能各带一张 thumb。
   * 所以「应用到同颜色」是这套菜单里最常用的一项 —— 同色不同码通常共用一张图。
   */
  const applyThumb = (key: string, url: string, scope: "row" | "color" | "all") => {
    const target = rows.find((row) => row.key === key);
    if (!target) return;
    const text = url.trim();
    if (scope === "row") {
      onUpdateRow(key, { thumbUrl: text });
    } else {
      const inScope = (row: VariantRow) => scope === "all" || row.colorName === target.colorName;
      onRowsChange(rows.map((row) => (inScope(row) ? { ...row, thumbUrl: text } : row)));
    }
    setThumbMenuKey(null);
    setThumbMenuView("root");
    setPendingThumb("");
    setThumbUrlDraft("");
  };

  /** 本地图先转 dataURL 存在行里，提交时 `resolveImage` 再统一传到图床。 */
  const readLocalThumb = (file: File) => {
    const reader = new FileReader();
    reader.onload = () => {
      setPendingThumb(String(reader.result || ""));
      setThumbMenuView("scope");
    };
    reader.readAsDataURL(file);
  };

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
      {singleSku ? (
        <p className="temu-cat-hint">
          这个类目在 Temu 没有销售属性，不能建颜色/尺码变种：按<strong>单 SKU</strong> 提交，
          只用下表第 1 行的价格、库存、尺寸与重量。
          {/* Temu 接口要求规格列表非空，但这类目拿不到真实规格 —— 后端会自动建一条
              真实的「颜色：默认」规格值来占位。注意：以前这里补的是 id 全 0 的
              「款式/默认」，规格校验能过、但提交永远回 6000002「货品类目属性更新」。 */}
          <br />
          发布时会自动补一条「<strong>颜色：默认</strong>」的规格占位（Temu 接口要求规格非空），
          商品里不会真的多出一个颜色可选。
        </p>
      ) : (
        <div className="temu-variant-spec">
        <label className="temu-field">
          <span>
            变种属性一：{colorSpecName}
            <em className="temu-required">*</em>
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
              className={`temu-input${unknownColors.length ? " is-bad" : ""}`}
              list="temu-color-options"
              value={colorsText}
              onChange={(event) => onColorsTextChange(event.target.value)}
              placeholder={`${colorSpecName}值，多个用逗号分隔`}
            />
          </div>
          {renderSpecChoices(colorOptions, colorsText, onColorsTextChange, colorSpecName)}
          {unknownColors.length > 0 && (
            <p className="temu-spec-bad">
              「{unknownColors.join("、")}」不是合法的{colorSpecName}值（Temu 只认类目预设）——
              点上面任意一个换掉它。
            </p>
          )}
        </label>
        {/* 只有一个销售属性的类目（手机壳只有「型号」、毛巾只有「颜色」）不显示第二列，
            否则用户会以为必须再填一项，而 Temu 那边根本没这个属性 */}
        {sizeParentId > 0 && (
          <>
            <label className="temu-field">
              <span>变种属性二：{sizeSpecName}</span>
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
              <span>{sizeSpecName}值（多个用逗号分隔）</span>
              <input
                className={`temu-input${unknownSizes.length ? " is-bad" : ""}`}
                list="temu-size-options"
                value={sizesText}
                onChange={(event) => onSizesTextChange(event.target.value)}
                placeholder={`${sizeSpecName}值，多个用逗号分隔`}
                onKeyDown={(event) => {
                  if (event.key === "Enter") generate();
                }}
              />
              {renderSpecChoices(sizeOptions, sizesText, onSizesTextChange, sizeSpecName)}
              {unknownSizes.length > 0 && (
                <p className="temu-spec-bad">
                  「{unknownSizes.join("、")}」不是合法的{sizeSpecName}值（Temu 只认类目预设）——
                  点上面任意一个换掉它。
                </p>
              )}
            </label>
          </>
        )}
        <button
          type="button"
          className={`temu-btn temu-btn-primary temu-variant-generate${coverage.inSync ? " is-synced" : ""}`}
          disabled={coverage.inSync}
          title={
            coverage.inSync
              ? "表格已经和上面的颜色 × 尺码一一对应，没有需要补的组合"
              : `还会补 ${coverage.missing} 个新组合${
                  coverage.extra ? `，并去掉 ${coverage.extra} 个多余的` : ""
                }；已经填过的行原样保留`
          }
          onClick={generate}
        >
          {coverage.inSync
            ? `✓ 已生成 ${coverage.expected} 个变种`
            : `生成 ${coverage.expected} 个变种${rows.length ? `（当前 ${rows.length} 个）` : ""}`}
        </button>
        </div>
      )}

      <div className="temu-batch">
        <div className="temu-batch-head">
          <strong>批量编辑</strong>
          <span className="temu-muted">只覆盖你填了值的项；空着的格子保持各行原样</span>
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
        {/* 7 个字段挤一行时每格只有 ~100px，「长/宽/高」根本填不下。
            按语义分两组：价格与库存 / 尺寸与重量。 */}
        <div className="temu-batch-group">
          <span className="temu-batch-group-label">价格与库存</span>
          <div className="temu-batch-grid">
          <label>
            申报价格（元）
            <input
              className="temu-input"
              inputMode="decimal"
              value={batch.supplierPrice}
              onChange={(e) => patchBatch({ supplierPrice: e.target.value })}
              placeholder="12.99"
            />
          </label>
          <label>
            建议售价
            <input className="temu-input" inputMode="numeric" value={batch.suggestedPrice} onChange={(e) => patchBatch({ suggestedPrice: e.target.value })} placeholder="可选" />
          </label>
          <label>
            库存
            <input className="temu-input" inputMode="numeric" value={batch.stock} onChange={(e) => patchBatch({ stock: e.target.value })} placeholder="0" />
          </label>
          </div>
        </div>

        <div className="temu-batch-group">
          <span className="temu-batch-group-label">尺寸与重量</span>
          <div className="temu-batch-grid">
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
      </div>

      <div className="temu-variant-scroll">
        <input
          ref={thumbFileRef}
          type="file"
          accept="image/*"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (file) readLocalThumb(file);
          }}
        />
        <div className="temu-variant-table">
          {/* 列对齐 Temu 的变种信息：预览图 / 尺码 / SKU 货号 / 申报价格 / 长宽高 / 重量 / 建议售价 / 库存。
              颜色由上面的「变种属性值一」统一指定，发货仓库由「默认发货仓库」统一指定，不再逐行重复。 */}
          <div className={`temu-variant-row is-head${singleSku ? " is-single" : ""}`}>
            <span>预览图</span>
            {!singleSku && (
              <>
                <span>颜色</span>
                <span>尺码</span>
              </>
            )}
            <span>SKU 货号</span>
            <span>申报价格(元)</span>
            <span>长</span>
            <span>宽</span>
            <span>高</span>
            <span>重量(g)</span>
            <span>建议售价(元)</span>
            <span>库存</span>
            <span />
          </div>
          {rows.length === 0 && (
            <p className="temu-empty">
              {singleSku ? "还没有 SKU：点上面的「生成变种」补一行。" : "还没有变种：填好上面的尺码，点「生成变种」。"}
            </p>
          )}
          {rows.map((row) => (
            <div className={`temu-variant-row${singleSku ? " is-single" : ""}`} key={row.key}>
              <div className="temu-variant-thumb">
                {/* 分工要清楚：
                    - 点图 = 看大图（TemuThumb 自己就是按钮，这里不再包 click）
                    - 点右下角 ✎ = 换图菜单
                    之前整块图都是「开菜单」，同时又触发放大，两个浮层一起弹。 */}
                <div className="temu-thumb-trigger">
                  <TemuThumb
                    path={row.thumbUrl || fallbackThumb}
                    onOpen={(src) => onPreviewImage?.(src, row.colorName || row.extCode || "变种图")}
                  />
                </div>
                <button
                  type="button"
                  className="temu-thumb-edit"
                  title={row.thumbUrl ? "更换这一行的预览图" : "未单独设置，默认取轮播第一张；点这里可以换图"}
                  aria-label="更换预览图"
                  onClick={(event) => {
                    event.stopPropagation();
                    if (thumbMenuKey === row.key) {
                      setThumbMenuKey(null);
                      return;
                    }
                    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
                    setThumbMenuPos({
                      top: Math.min(rect.bottom + 6, window.innerHeight - 300),
                      left: Math.min(rect.right + 8, window.innerWidth - 210),
                    });
                    setThumbMenuKey(row.key);
                    setThumbMenuView("root");
                    setPendingThumb("");
                    setThumbUrlDraft("");
                  }}
                >
                  ✎
                </button>

                {thumbMenuKey === row.key && (
                  <div
                    className="temu-thumb-menu"
                    role="menu"
                    style={thumbMenuPos ? { top: thumbMenuPos.top, left: thumbMenuPos.left } : undefined}
                  >
                    {thumbMenuView === "root" && (
                      <>
                        <button type="button" onClick={() => thumbFileRef.current?.click()}>
                          本地图片
                        </button>
                        <button type="button" onClick={() => setThumbMenuView("url")}>
                          网络图片
                        </button>
                        <button
                          type="button"
                          disabled={!carouselImages.length}
                          onClick={() => setThumbMenuView("carousel")}
                        >
                          引用产品轮播图{carouselImages.length ? `（${carouselImages.length}）` : "（无）"}
                        </button>
                        {row.thumbUrl ? (
                          <button
                            type="button"
                            className="is-danger"
                            onClick={() => applyThumb(row.key, "", "row")}
                          >
                            恢复默认（轮播首图）
                          </button>
                        ) : null}
                      </>
                    )}

                    {thumbMenuView === "url" && (
                      <div className="temu-thumb-url">
                        <input
                          className="temu-input"
                          autoFocus
                          value={thumbUrlDraft}
                          onChange={(event) => setThumbUrlDraft(event.target.value)}
                          placeholder="https://…"
                          onKeyDown={(event) => {
                            if (event.key === "Enter" && thumbUrlDraft.trim()) {
                              setPendingThumb(thumbUrlDraft.trim());
                              setThumbMenuView("scope");
                            }
                          }}
                        />
                        <button
                          type="button"
                          disabled={!/^https?:\/\//i.test(thumbUrlDraft.trim()) && !thumbUrlDraft.trim().startsWith("data:")}
                          onClick={() => {
                            setPendingThumb(thumbUrlDraft.trim());
                            setThumbMenuView("scope");
                          }}
                        >
                          用这张
                        </button>
                      </div>
                    )}

                    {thumbMenuView === "carousel" && (
                      <div className="temu-thumb-carousel">
                        {carouselImages.map((url) => (
                          <button
                            key={url}
                            type="button"
                            title="用这张轮播图"
                            onClick={() => {
                              setPendingThumb(url);
                              setThumbMenuView("scope");
                            }}
                          >
                            <img src={url} alt="" referrerPolicy="no-referrer" />
                          </button>
                        ))}
                      </div>
                    )}

                    {thumbMenuView === "scope" && pendingThumb && (
                      <div className="temu-thumb-scope">
                        <img src={pendingThumb} alt="" referrerPolicy="no-referrer" />
                        <div>
                          <p>应用到：</p>
                          <button type="button" onClick={() => applyThumb(row.key, pendingThumb, "row")}>
                            仅这一行
                          </button>
                          <button type="button" onClick={() => applyThumb(row.key, pendingThumb, "color")}>
                            同颜色（{row.colorName || "未填"}）
                          </button>
                          <button type="button" onClick={() => applyThumb(row.key, pendingThumb, "all")}>
                            全部 {rows.length} 行
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
              {!singleSku && (
                <>
                  <input
                    className="temu-input"
                    list="temu-color-options"
                    value={row.colorName}
                    onChange={(event) => onUpdateRow(row.key, { colorName: event.target.value })}
                    // 类目没有预设值（化妆包这类走自定义规格）时别写「选预设」，
                    // 否则用户以为必须从下拉里挑，而其实要自己输入
                    placeholder={
                      colorOptions.length ? `选预设${colorSpecName}` : `输入${colorSpecName}，如白色`
                    }
                    title={`Temu 只认类目预设的${colorSpecName}值，自己输入会被拒`}
                  />
                  <input
                    className="temu-input"
                    list="temu-size-options"
                    value={row.sizeName}
                    onChange={(event) => onUpdateRow(row.key, { sizeName: event.target.value })}
                    placeholder={sizeOptions.length ? "选预设值" : `输入${sizeSpecName}`}
                  />
                </>
              )}
              <input
                className="temu-input"
                value={row.extCode}
                onChange={(event) => onUpdateRow(row.key, { extCode: event.target.value })}
              />
              <input
                className="temu-input"
                inputMode="decimal"
                value={row.supplierPrice}
                onChange={(event) => onUpdateRow(row.key, { supplierPrice: event.target.value })}
                placeholder="12.99"
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
                inputMode="decimal"
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
      {zeroStockCount > 0 && (
        <p className="temu-variant-zerohint">
          <strong>{zeroStockCount} 个 SKU 库存为 0</strong>
          ：可以提交，Temu 会建品但商品是「售罄 / 不可售」状态，开卖前补库存即可。
        </p>
      )}
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
