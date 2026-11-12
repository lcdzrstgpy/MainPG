import type { TemuCategory } from "../types";

/**
 * 发布页的本机数据：模板 + 发布失败记录。
 * 全部按登录账号分 key：换账号登录不会看到别人的模板和失败记录。
 */

function scope(): string {
  try {
    const account = JSON.parse(window.localStorage.getItem("wh_demo_account") ?? "null") as
      | { user_id?: string }
      | null;
    return account?.user_id || "local";
  } catch {
    return "local";
  }
}

function read<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(`${key}:${scope()}`);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function write(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(`${key}:${scope()}`, JSON.stringify(value));
  } catch {
    // 本机存储不可用时不影响主流程
  }
}

/** 产品模板：一套「每款都一样」的产品信息，套用时只填空字段。 */
export type ProductTemplate = {
  name: string;
  categoryPath: TemuCategory[];
  /** pid → vid（下拉型）或文本（输入型） */
  attrs: Record<string, { vid: number; text: string }>;
  freightId: string;
  warehouseId: string;
  shipmentDays: string;
  origin: string;
  sizeTemplates: string;
  isCustom: boolean;
  isSensitive: boolean;
  /** 变种属性的父规格（颜色族 / 尺码族），批量发布建规格时要用 */
  colorParentId?: number;
  colorParentName?: string;
  sizeParentId?: number;
  sizeParentName?: string;
  savedAt: string;
};

/** 变种模板：颜色 × 尺码 + 批量值，套用时直接铺出变种行。 */
export type VariantTemplate = {
  name: string;
  colorsText: string;
  sizesText: string;
  batch: {
    /** 单位：元（提交时换算成分） */
    supplierPrice: string;
    suggestedPrice: string;
    stock: string;
    weightG: string;
    lengthCm: string;
    widthCm: string;
    heightCm: string;
  };
  savedAt: string;
};

/** 发布失败记录：列表里展示失败原因 + 解决方案，可重试。 */
export type PublishFailure = {
  id: string;
  draftTitle: string;
  image: string;
  siteLabel: string;
  categoryLabel: string;
  skuLabel: string;
  priceLabel: string;
  at: string;
  reason: string;
  traceId: string;
  /** 重试时直接带回发布页的预填数据 */
  prefill?: Record<string, unknown>;
};

const PRODUCT_KEY = "temu_publish_product_templates";
const VARIANT_KEY = "temu_publish_variant_templates";
const FAILURE_KEY = "temu_publish_failures";

const MAX_FAILURES = 100;

/**
 * 草稿 → Temu 类目路径。
 *
 * `auto: true` 表示这条是**按标题自动匹配**出来的（入草稿库时补的），不是人工选过的：
 * 只是「大概方向」，发布前最好扫一眼。人工选过的 memo 不带这个标记。
 */
export type DraftCategoryMemo = {
  path: TemuCategory[];
  label: string;
  at: string;
  auto?: boolean;
};

const DRAFT_CATEGORY_KEY = "temu_draft_category_v1";

export const localStore = {
  /** 当前登录账号的本机存储后缀（别处自己拼 key 时用）。 */
  storageScope: scope,
  draftCategories: (): Record<string, DraftCategoryMemo> => read(DRAFT_CATEGORY_KEY, {}),
  /** 记住某条草稿的类目：下次「用于发布」直接选中，草稿库也能显示路径。 */
  rememberDraftCategory: (draftId: string, path: TemuCategory[], options?: { auto?: boolean }) => {
    if (!draftId || !path.length) return;
    const next = { ...localStore.draftCategories() };
    next[draftId] = {
      path,
      label: path.map((item) => item.catName || String(item.catId)).join(" > "),
      at: new Date().toLocaleString("zh-CN"),
      ...(options?.auto ? { auto: true } : {}),
    };
    write(DRAFT_CATEGORY_KEY, next);
  },

  productTemplates: () => read<ProductTemplate[]>(PRODUCT_KEY, []),
  saveProductTemplate: (template: ProductTemplate) => {
    const next = [template, ...localStore.productTemplates().filter((item) => item.name !== template.name)];
    write(PRODUCT_KEY, next.slice(0, 20));
  },
  removeProductTemplate: (name: string) =>
    write(PRODUCT_KEY, localStore.productTemplates().filter((item) => item.name !== name)),

  variantTemplates: () => read<VariantTemplate[]>(VARIANT_KEY, []),
  saveVariantTemplate: (template: VariantTemplate) => {
    const next = [template, ...localStore.variantTemplates().filter((item) => item.name !== template.name)];
    write(VARIANT_KEY, next.slice(0, 20));
  },
  removeVariantTemplate: (name: string) =>
    write(VARIANT_KEY, localStore.variantTemplates().filter((item) => item.name !== name)),

  failures: () => read<PublishFailure[]>(FAILURE_KEY, []),
  addFailure: (failure: PublishFailure) => {
    const next = [failure, ...localStore.failures()].slice(0, MAX_FAILURES);
    write(FAILURE_KEY, next);
    return next;
  },
  removeFailure: (id: string) => {
    const next = localStore.failures().filter((item) => item.id !== id);
    write(FAILURE_KEY, next);
    return next;
  },
  clearFailures: () => write(FAILURE_KEY, []),
};

/**
 * Temu 的报错常带 Trace_id=xxx，把那一长串留给排查；正文转成人话。
 */
/**
 * 把发布异常拆成「原因」和 «Trace id»。
 *
 * 这里**不再**自己猜一份「怎么改」的建议：后端 `_gateway_message` 已经把结论、
 * 原话和建议拼进 message 了，前端再按关键词猜一套只会出现两种说法打架。
 * （猜出来的那份以前存在失败记录的 hint 字段里，但历史页从来没渲染过 —— 纯死数据。）
 */
export function parsePublishError(error: unknown): { reason: string; traceId: string } {
  const text = error instanceof Error ? error.message : String(error ?? "");
  const trace = /Trace[_\s]?id[=:：]\s*([A-Za-z0-9-]+)/i.exec(text)?.[1] ?? "";
  const reason = text.replace(/Trace[_\s]?id[=:：]\s*[A-Za-z0-9-]+/gi, "").trim() || "未知错误";
  return { reason: reason.slice(0, 400), traceId: trace };
}

