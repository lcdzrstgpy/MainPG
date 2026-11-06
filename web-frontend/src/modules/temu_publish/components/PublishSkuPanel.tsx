import { useEffect, useRef, useState } from "react";

import type { TemuParentSpec } from "../types";
import { TemuThumb } from "./TemuImageStrip";
import { mergeGeneratedRows, variantCoverage } from "../data/variantMerge";
import { groupSpecColors, specColorGroup } from "../data/specSwatch";

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
  presetsReady = true,
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
  /**
   * 类目属性是否已经读过。没读过时不能断言「这个类目没有预设」——
   * 那句提示会让用户以为可以自由填，提交时才被 Temu 打回。
   */
  presetsReady?: boolean;
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
    // 第二维关掉 = 每色一行，用空尺码参与比对；还开着但没填值就别假装是「均码」，
    // 否则按钮会显示「✓ 已生成」，用户以为完事了，实际第二维还是空的。
    sizeParentId > 0 ? (sizeNames.length ? sizeNames : ["均码"]) : [""],
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

  /**
   * 给一个不在预设里的值找「最像的」预设值。
   *
   * 用户写「浅绿色」而预设叫「浅绿」时，光说「不合法」帮不上忙 —— 直接给候选，
   * 点一下替换掉。只做包含关系匹配（够用且不会乱猜），最多 3 个。
   */
  const suggestSpecValues = (value: string, options: string[]): string[] => {
    const base = value.trim().toLowerCase();
    if (!base) return [];
    return options
      .filter((option) => {
        const candidate = option.toLowerCase();
        if (candidate === base) return false;
        return candidate.includes(base) || base.includes(candidate);
      })
      .sort((left, right) => Math.abs(left.length - value.length) - Math.abs(right.length - value.length))
      .slice(0, 3);
  };

  /** 把列表里的某个值换成另一个（一个值可能出现在多行里）。 */
  const replaceSpecValue = (current: string, from: string, to: string) =>
    splitSpecValues(current)
      .map((value) => (value.toLowerCase() === from.toLowerCase() ? to : value))
      .join(",");

  /**
   * 「Temu 只认这些值」：把类目预设全量摊开，可搜索、点一下加入/移除。
   *
   * 以前只露前 10 个 + 一句「还有 N 个，输入框里可以下拉搜」——
   * 用户根本不知道该搜什么，只能凭感觉手打「浅绿色」，然后被 Temu 判
   * 「不合法的规格值」（截图里的失败就是这么来的）。
   * 现在默认显示数量与入口，点开就是全部预设 + 搜索框。
   */
  const [specPanel, setSpecPanel] = useState<"color" | "size" | null>(null);
  const [specQuery, setSpecQuery] = useState("");
  /** 面板里的色系筛选：all / 色系 key / other */
  const [specGroupKey, setSpecGroupKey] = useState<string>("all");
  /** 「复制全部预设」后的短暂反馈 */
  const [copiedSpec, setCopiedSpec] = useState(false);

  /** 「生成变种」被拦下时的原因（第二维没填，不能凭空造「均码」） */
  const [genHint, setGenHint] = useState("");

  /** 点空白处收起预设面板（浮层，不收会一直压着下面的表格） */
  useEffect(() => {
    if (!specPanel) return;
    const onDown = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest(".temu-spec-presets")) return;
      setSpecPanel(null);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [specPanel]);

  const splitSpecValues = (text: string) =>
    text
      .split(/[,，]/)
      .map((value) => value.trim())
      .filter(Boolean);

  const renderSpecChoices = (
    options: string[],
    current: string,
    onPick: (next: string) => void,
    label: string,
    kind: "color" | "size",
  ) => {
    const picked = splitSpecValues(current);
    const pickedLower = new Set(picked.map((value) => value.toLowerCase()));
    const toggle = (name: string) => {
      const exists = pickedLower.has(name.toLowerCase());
      const next = exists
        ? picked.filter((value) => value.toLowerCase() !== name.toLowerCase())
        : [...picked, name];
      onPick(next.join(","));
    };
    const query = specPanel === kind ? specQuery.trim().toLowerCase() : "";
    const open = specPanel === kind;

    // 颜色：按色系分组 + 色卡。尺码（S/M/L、容量）没有色系概念，走纯文字列表。
    const groups = kind === "color" ? groupSpecColors(options) : [];
    const useGroups = groups.length > 1;
    const activeGroupKey = open ? specGroupKey : "all";
    const scoped =
      useGroups && activeGroupKey !== "all"
        ? groups.find((item) => (item.group?.key ?? "other") === activeGroupKey)?.values ?? []
        : options;
    const filtered = query ? scoped.filter((name) => name.toLowerCase().includes(query)) : scoped;

    if (!options.length) {
      // 读到了属性但确实没有预设值（走自定义规格的类目）→ 明确说可以自由填；
      // 还没读到就不能这么说，否则用户以为随便填都行
      return (
        <p className="temu-spec-presets-note">
          {presetsReady
            ? `这个类目没有${label}预设值，可以自己填写（Temu 按自定义规格收）。`
            : "正在读取类目预设…"}
        </p>
      );
    }

    return (
      <div className="temu-spec-presets">
        <button
          type="button"
          className={`temu-spec-browse${open ? " is-open" : ""}`}
          onClick={() => {
            setSpecPanel(open ? null : kind);
            setSpecQuery("");
            setSpecGroupKey("all");
          }}
        >
          <span className="temu-spec-browse-count">{options.length}</span>
          {open ? `收起${label}预设` : `浏览全部${label}预设`}
          <span className="temu-spec-browse-caret" aria-hidden="true">
            {open ? "▴" : "▾"}
          </span>
        </button>
        {picked.length ? <span className="temu-spec-picked-note">已选 {picked.length} 个</span> : null}
        {open ? (
          <div className="temu-spec-panel">
            <input
              className="temu-input"
              value={specQuery}
              placeholder={`搜索${label}预设，例如「黑」「浅」`}
              onChange={(event) => setSpecQuery(event.target.value)}
              // 面板里按回车不要触发下面的「生成变种」
              onKeyDown={(event) => {
                if (event.key === "Enter") event.preventDefault();
              }}
            />
            {/* 色系分组：一个类目几十上百个颜色时，先按色系收窄再搜 */}
            {useGroups ? (
              <div className="temu-spec-groups">
                <button
                  type="button"
                  className={`temu-spec-group${activeGroupKey === "all" ? " is-active" : ""}`}
                  onClick={() => setSpecGroupKey("all")}
                >
                  全部<span>{options.length}</span>
                </button>
                {groups.map((item) => {
                  const key = item.group?.key ?? "other";
                  return (
                    <button
                      key={key}
                      type="button"
                      className={`temu-spec-group${activeGroupKey === key ? " is-active" : ""}`}
                      onClick={() => setSpecGroupKey(key)}
                    >
                      {item.group ? (
                        <i
                          className={`temu-spec-dot${item.group.gradient ? " is-multi" : ""}`}
                          style={item.group.gradient ? undefined : { background: item.group.swatch }}
                          aria-hidden="true"
                        />
                      ) : null}
                      {item.label}
                      <span>{item.values.length}</span>
                    </button>
                  );
                })}
              </div>
            ) : null}
            <div className="temu-spec-panel-list">
              {filtered.map((name) => {
                const swatch = kind === "color" ? specColorGroup(name) : null;
                const isPicked = pickedLower.has(name.toLowerCase());
                return (
                  <button
                    key={name}
                    type="button"
                    className={`temu-spec-chip${isPicked ? " is-picked" : ""}`}
                    onClick={() => toggle(name)}
                    title={isPicked ? "再点一下从列表里移除" : `加入「${name}」`}
                  >
                    {swatch ? (
                      <i
                        className={`temu-spec-dot${swatch.gradient ? " is-multi" : ""}`}
                        style={swatch.gradient ? undefined : { background: swatch.swatch }}
                        aria-hidden="true"
                      />
                    ) : null}
                    {name}
                  </button>
                );
              })}
              {!filtered.length ? <span className="temu-muted">没有匹配的预设值</span> : null}
            </div>
            <div className="temu-spec-panel-foot">
              <p className="temu-spec-panel-note">
                只能从这些值里选（点一下加入、再点移除）。自己造的词 Temu 会判「不合法的{label}」。
              </p>
              {/* 有人要拿全量清单去对表 / 报给运营，给个复制口子，别逼他一个个抄 */}
              <button
                type="button"
                className="temu-link-button"
                onClick={() => {
                  const text = options.join("\n");
                  const done = () => {
                    setCopiedSpec(true);
                    window.setTimeout(() => setCopiedSpec(false), 1800);
                  };
                  if (navigator.clipboard?.writeText) {
                    void navigator.clipboard.writeText(text).then(done).catch(() => done());
                  } else {
                    done();
                  }
                }}
              >
                {copiedSpec ? "已复制全部" : `复制全部 ${options.length} 个`}
              </button>
            </div>
          </div>
        ) : null}
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
    setGenHint("");
    const colors = colorNames.length ? colorNames : ["Default"];
    /**
     * 第二维被关掉 → 只按颜色铺行；
     * 第二维还在但用户没填值 → 不再凭空造「均码」：
     * 那不是预设值，提交必被判「不合法的规格属性」，不如当场说清楚。
     * （只有类目预设里真的有「均码」时才用它兜底 —— 这类目卖的就是均码商品。）
     */
    let sizes: string[];
    if (sizeParentId > 0) {
      if (sizeNames.length) sizes = sizeNames;
      else if (sizeOptions.includes("均码")) sizes = ["均码"];
      else {
        setGenHint(
          `先在「变种属性二」里填${sizeSpecName}值（从预设里选），或者把它设成「不使用（单维）」——` +
            `第二维空着不能生成，硬填一个「均码」提交会被 Temu 判为不合法的规格值。`,
        );
        return;
      }
    } else {
      sizes = [""];
    }
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
          {renderSpecChoices(colorOptions, colorsText, onColorsTextChange, colorSpecName, "color")}
          {unknownColors.length > 0 && (
            <div className="temu-spec-bad">
              <p>
                「{unknownColors.join("、")}」不是合法的{colorSpecName}值（Temu 只认类目预设），提交会被拒。
              </p>
              {unknownColors.map((value) => {
                const suggestions = suggestSpecValues(value, colorOptions);
                if (!suggestions.length) return null;
                return (
                  <p key={value} className="temu-spec-bad-fix">
                    把「{value}」换成：
                    {suggestions.map((name) => (
                      <button
                        key={name}
                        type="button"
                        className="temu-spec-chip"
                        onClick={() => onColorsTextChange(replaceSpecValue(colorsText, value, name))}
                        title={`用预设值「${name}」替换「${value}」`}
                      >
                        {name}
                      </button>
                    ))}
                  </p>
                );
              })}
            </div>
          )}
        </label>
        {/* 有第二维就当普通两列用；只有一列销售属性的类目（手机壳只有「型号」）不显示。
            第二维允许手动关掉：只发颜色/款式，一个颜色一个 SKU。 */}
        {parentSpecs.length > 1 && (
          // 和「属性一」保持同一副骨架：标题 + 一行（父属性下拉 + 值输入）+ 预设区。
          // 拆成两个 field 会让网格按顺序排版，标题和输入框错行（截图里就歪了）。
          <label className="temu-field">
            <span>变种属性二：{sizeParentId > 0 ? sizeSpecName : "不使用"}</span>
            <div className="temu-spec-row">
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
                {/* 只要一维（只有颜色、没有尺码）时的出口：以前没有这个选项，
                    用户只能看着尺码列发呆 —— 删不掉也没法忽略。
                    下拉很窄，标签得短；完整解释在右边的虚线提示里。 */}
                <option value={0}>不使用（单维）</option>
              </select>
              {sizeParentId > 0 ? (
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
              ) : (
                <span className="temu-spec-nodim">
                  每个{colorSpecName}一个 SKU，提交时不再带第二维规格
                </span>
              )}
            </div>
            {sizeParentId > 0 ? renderSpecChoices(sizeOptions, sizesText, onSizesTextChange, sizeSpecName, "size") : null}
            {sizeParentId > 0 && unknownSizes.length > 0 && (
              <div className="temu-spec-bad">
                <p>
                  「{unknownSizes.join("、")}」不是合法的{sizeSpecName}值（Temu 只认类目预设），提交会被拒。
                </p>
                {unknownSizes.map((value) => {
                  const suggestions = suggestSpecValues(value, sizeOptions);
                  if (!suggestions.length) return null;
                  return (
                    <p key={value} className="temu-spec-bad-fix">
                      把「{value}」换成：
                      {suggestions.map((name) => (
                        <button
                          key={name}
                          type="button"
                          className="temu-spec-chip"
                          onClick={() => onSizesTextChange(replaceSpecValue(sizesText, value, name))}
                          title={`用预设值「${name}」替换「${value}」`}
                        >
                          {name}
                        </button>
                      ))}
                    </p>
                  );
                })}
              </div>
            )}
          </label>
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
      {genHint ? <p className="temu-spec-bad">{genHint}</p> : null}

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
                {/* 第二维被关掉（只按颜色发）时不要留一列空的「尺码」 */}
                {sizeParentId > 0 ? <span>{sizeSpecName}</span> : null}
              </>
            )}
            <span>SKU 货号</span>
            <span>申报价格(元)</span>
            <span title="单位：厘米">长</span>
            <span title="单位：厘米">宽</span>
            <span title="单位：厘米">高</span>
            <span>重量(g)</span>
            <span>建议售价(元)</span>
            <span title="填 0 也能建品，但商品是售罄/不可售状态">库存(件)</span>
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
                  {sizeParentId > 0 ? (
                    <input
                      className="temu-input"
                      list="temu-size-options"
                      value={row.sizeName}
                      onChange={(event) => onUpdateRow(row.key, { sizeName: event.target.value })}
                      placeholder={sizeOptions.length ? "选预设值" : `输入${sizeSpecName}`}
                    />
                  ) : null}
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
              {/* 长/宽/高 各占一列、各有表头。曾经合成「长 × 宽 × 高」一组，
                  但一列里塞三个框只能分到 39~72px，看着挤成一堆小方块；
                  三列分开每格都有自己的标题，填错列时反而更容易发现。 */}
              <input
                className="temu-input"
                inputMode="decimal"
                aria-label="长（厘米）"
                title="长（厘米）"
                placeholder="长"
                value={row.lengthCm}
                onChange={(event) => onUpdateRow(row.key, { lengthCm: event.target.value })}
              />
              <input
                className="temu-input"
                inputMode="decimal"
                aria-label="宽（厘米）"
                title="宽（厘米）"
                placeholder="宽"
                value={row.widthCm}
                onChange={(event) => onUpdateRow(row.key, { widthCm: event.target.value })}
              />
              <input
                className="temu-input"
                inputMode="decimal"
                aria-label="高（厘米）"
                title="高（厘米）"
                placeholder="高"
                value={row.heightCm}
                onChange={(event) => onUpdateRow(row.key, { heightCm: event.target.value })}
              />
              <input
                className="temu-input"
                inputMode="decimal"
                aria-label="重量（克）"
                placeholder="重量"
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
                  String(row.stock).trim() === "" || Number.isNaN(Number(row.stock)) || Number(row.stock) < 0
                    ? " is-bad"
                    : Number(row.stock) === 0
                      // 0 是合法值但商品会「售罄」，用橙色（不是红色报错）提醒一下
                      ? " is-empty-stock"
                      : ""
                }`}
                inputMode="numeric"
                value={row.stock}
                onChange={(event) => onUpdateRow(row.key, { stock: event.target.value })}
                placeholder="0 也可"
                title={
                  Number(row.stock) > 0
                    ? "库存（件）"
                    : "库存 0：能建品，但商品是「售罄 / 不可售」状态，买家看不到"
                }
              />
              <button
                type="button"
                className="temu-variant-del"
                onClick={() => onRemoveRow(row.key)}
                disabled={rows.length <= 1}
                aria-label="删除这一行变种"
                title={
                  rows.length <= 1
                    ? "至少保留 1 个变种；整件商品只有一个 SKU 的话，用类目旁的「按规格发」开关关掉多规格"
                    : "删除这一行（同色最后一行删掉时，上面的颜色列表也会一起移除）"
                }
              >
                ×
              </button>
            </div>
          ))}
        </div>
      </div>
      <p className="temu-muted">
        共 {rows.length} 个变种
        {/* 单 SKU 类目没有颜色维度，别再说「几个颜色 / 同色下尺码不能重复」 */}
        {singleSku ? "。" : ` / ${new Set(rows.map((row) => row.colorName.trim() || "Default")).size} 个颜色。`}
        Temu 变种上限 20 个{singleSku || sizeParentId <= 0 ? "" : "；同色下尺码不能重复"}，SKU 货号不能重复。
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
