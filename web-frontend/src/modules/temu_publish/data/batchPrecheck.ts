/**
 * 批量发布的**本地预检**：不传图、不发网络请求，纯算。
 *
 * 为什么单独一个文件：判「这条能不能发」不需要网关，但需要能被单元测试直接跑
 * （batchPublish.ts 依赖 api 层，Node 起不来）。这里只依赖价格换算这一处纯函数。
 *
 * 判断口径必须和 buildDraftForPublish 的实际校验一致，否则会出现
 * 「预检说能发、结果还是失败」或者反过来「预检拦了、其实能发」。
 */
import { yuanToCents } from "../utils/price.ts";
import type { TemuPublishPrefill } from "../types";
import type { ProductTemplate, VariantTemplate } from "./localStore";

export type BatchPrecheck = {
  /** 阻塞项：命中任何一条，这条发出去必定失败（本地就能判，不用等网关） */
  blocked: string[];
  /** 提醒项：能发，但结果可能不是用户想要的（库存 0、只有单 SKU…） */
  warnings: string[];
};

export function splitList(text: string): string[] {
  return text
    .split(/[\n,，、;；]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function positive(value: string, label: string): number {
  const number = Number(String(value ?? "").trim());
  if (!Number.isFinite(number) || number <= 0) throw new Error(`${label}必须大于 0`);
  return number;
}

/** 跑一下会抛错的校验，把错误文案拿回来（预检用：不想为了读个 reason 写一堆 try）。 */
function checkError(fn: () => unknown): string {
  try {
    fn();
    return "";
  } catch (cause) {
    return cause instanceof Error ? cause.message : String(cause);
  }
}

/**
 * 一条草稿的预检结果。
 *
 * 覆盖真实批量失败里的绝大多数：缺图、缺价、缺尺寸、模板没配全。
 * 剩下的（属性不合法、规格值和类目预设对不上）只能靠网关回话，仍然走失败台账。
 */
export function precheckBatchItem(
  prefill: TemuPublishPrefill,
  productTemplate: ProductTemplate,
  variantTemplate: VariantTemplate | null,
): BatchPrecheck {
  const blocked: string[] = [];
  const warnings: string[] = [];

  if (!(productTemplate.categoryPath ?? []).length) blocked.push("产品模板里没有类目");
  if (!productTemplate.warehouseId) blocked.push("产品模板里没有默认发货仓库");
  if (!productTemplate.freightId) blocked.push("产品模板里没有运费模板");

  if (!String(prefill.title ?? "").trim()) blocked.push("没有标题");

  const carousel = splitList(prefill.images.join("\n"));
  if (carousel.length < 3) blocked.push(`轮播图只有 ${carousel.length} 张（Temu 要求至少 3 张）`);

  const batch = variantTemplate?.batch;
  const priceError = checkError(() =>
    yuanToCents(batch?.supplierPrice || prefill.supplierPrice || "", "申报价格"),
  );
  if (priceError) blocked.push(priceError);

  const weightError = checkError(() =>
    positive(batch?.weightG || String(Number(prefill.weightKg || 0) * 1000), "重量"),
  );
  if (weightError) blocked.push(weightError);
  for (const [label, value] of [
    ["长度", batch?.lengthCm || prefill.lengthCm || ""],
    ["宽度", batch?.widthCm || prefill.widthCm || ""],
    ["高度", batch?.heightCm || prefill.heightCm || ""],
  ] as const) {
    const error = checkError(() => positive(value, label));
    if (error) blocked.push(error);
  }

  const colors = splitList(
    variantTemplate?.colorsText ||
      (prefill.specValues1?.length ? prefill.specValues1.join("\n") : prefill.specValue1 || ""),
  );
  const sizes = splitList(
    variantTemplate?.sizesText ||
      (prefill.specValues2?.length ? prefill.specValues2.join("\n") : prefill.specValue2 || ""),
  );
  if (!colors.length && !sizes.length) warnings.push("没有颜色/尺码值，只会建 1 个 SKU");

  const stock = Number(batch?.stock || prefill.stock || "0");
  if (!Number.isFinite(stock) || stock <= 0) warnings.push("库存 0（会以售罄状态创建）");

  return { blocked, warnings };
}
