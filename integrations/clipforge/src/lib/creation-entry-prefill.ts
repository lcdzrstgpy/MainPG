import { sanitizeCreationBrief, type CreationBrief, type InputMode } from "@/lib/creation-brief";

/**
 * 次级入口 → 主入口 `/start` 的预填契约（设计 §4 / §8 阶段 5）。
 *
 * 商品库、爆款复刻、话题成片这类入口不再创建自己的项目，只把用户的输入映射成「一份预填简报」，
 * 由 `/start` 带着 `CreationBrief` 创建。这里只做映射，纯函数：不发请求、不碰 DOM，跳转与
 * localStorage 写入交给页面执行。
 */

/** 唯一主创建入口（设计 §4）。 */
export const CREATION_ENTRY_PATH = "/start";

/**
 * 来源参数。取值与 `CreationBrief.inputMode` 相同，主入口据此设置 `inputMode`，
 * 因此不存在第二套「来源枚举」。
 */
export const ENTRY_PARAM = "entry";
/** 一句话主题来源的文本。 */
export const TOPIC_PARAM = "topic";
/** 商品库条目的 id（主入口按 id 读取库内名称/卖点/图片）。 */
export const PRODUCT_ID_PARAM = "productId";
/** 爆款复刻的商品名与卖点：文本较短，走 query。 */
export const PRODUCT_NAME_PARAM = "productName";
export const SELLING_POINTS_PARAM = "sellingPoints";
/**
 * 爆款复刻的参考结构、参考视频与已上传商品图放不进 URL，暂存在 localStorage。
 * 主入口读取后合并进表单，并在提交时交给 `buildScriptRequest`。
 */
export const CLONE_PREFILL_STORAGE_KEY = "clipforge_clone_prefill";

/** 会带预填跳转主入口的来源；取值是 `InputMode` 的子集。 */
export type CreationEntryId = Extract<InputMode, "topic" | "product-library" | "clone">;

export const CREATION_ENTRY_IDS: readonly CreationEntryId[] = ["topic", "product-library", "clone"];

/** 爆款复刻暂存内容（版本化，便于以后扩展或拒绝旧格式）。 */
export interface ClonePrefillPayload {
  version: 1;
  /**
   * 预填进表单的简报字段。只带这一条入口真正决定的内容（来源/风格/时长）——
   * 出片策略与音频策略由用户在提交前亲自选，次级入口不替用户决定。
   */
  brief: Partial<CreationBrief>;
  /** 参考视频解析出的镜头节奏骨架，提交时原样交给脚本接口 */
  referenceStructure?: string;
  referenceVideoUrl?: string;
  /** 已在服务端落盘的商品图（主入口抓成 File 预填，与商品库来源一致） */
  productImages?: string[];
}

export type CreationEntryPrefill =
  | { kind: "topic"; topic: string }
  | { kind: "product-library"; productId: string }
  | {
      kind: "clone";
      productName: string;
      sellingPoints: string;
      /** 复刻用的显式脚本风格（必须是脚本风格白名单里的值） */
      styleType: string;
      /** 参考视频的节奏时长（秒）；非法/越界值按最近合法档位归位 */
      targetDuration: number;
      referenceStructure?: string;
      referenceVideoUrl?: string;
      productImages?: string[];
    };

export interface PrefillTarget {
  /** 主入口地址（含 query） */
  href: string;
  /** 需要随跳转暂存的内容；只有爆款复刻用得上 */
  storage?: { key: string; value: string };
}

/** 目标时长只有 15/30/60（设计 §5.1）。 */
const TARGET_DURATIONS: readonly (15 | 30 | 60)[] = [15, 30, 60];

/**
 * 把任意秒数归位到最近的合法档位。合同里只有三档，所以次级入口算出的节奏时长
 * （例如参考视频 37s）必须归位，且归位结果对用户在简报里可见、可改。
 */
export function snapTargetDuration(seconds: number): 15 | 30 | 60 {
  const value = Number.isFinite(seconds) ? seconds : 30;
  return TARGET_DURATIONS.reduce(
    (best, candidate) => (Math.abs(candidate - value) < Math.abs(best - value) ? candidate : best),
    30 as 15 | 30 | 60
  );
}

const clampText = (value: string, max: number): string => (value ?? "").trim().slice(0, max);

/**
 * 爆款复刻交接用的简报片段：只保留入口决定的三个字段，经共享归一化校验（非法风格/时长落到安全值），
 * 不替用户写出片策略。
 */
function cloneBrief(raw: { styleType: unknown; targetDuration: unknown }): Partial<CreationBrief> {
  const sanitized = sanitizeCreationBrief({
    inputMode: "clone",
    styleType: raw.styleType,
    styleSource: "explicit",
    targetDuration: raw.targetDuration,
  });
  return {
    inputMode: "clone",
    styleType: sanitized.styleType,
    styleSource: sanitized.styleSource,
    targetDuration: sanitized.targetDuration,
  };
}

function withQuery(path: string, params: Record<string, string>): string {
  const search = new URLSearchParams(params).toString();
  return search ? `${path}?${search}` : path;
}

/** 入口数据 → 主入口预填参数。`path` 只用于旧深链接（`/project/new` 是同一份表单的兼容路由）。 */
export function toPrefillParams(input: CreationEntryPrefill & { path?: string }): PrefillTarget {
  const path = input.path ?? CREATION_ENTRY_PATH;

  if (input.kind === "topic") {
    return { href: withQuery(path, { [ENTRY_PARAM]: "topic", [TOPIC_PARAM]: clampText(input.topic, 300) }) };
  }

  if (input.kind === "product-library") {
    return { href: withQuery(path, { [ENTRY_PARAM]: "product-library", [PRODUCT_ID_PARAM]: input.productId }) };
  }

  const payload: ClonePrefillPayload = {
    version: 1,
    brief: cloneBrief({ styleType: input.styleType, targetDuration: input.targetDuration }),
    ...(input.referenceStructure ? { referenceStructure: input.referenceStructure.slice(0, 8000) } : {}),
    ...(input.referenceVideoUrl ? { referenceVideoUrl: clampText(input.referenceVideoUrl, 500) } : {}),
    ...(input.productImages?.length ? { productImages: input.productImages.slice(0, 5) } : {}),
  };

  return {
    href: withQuery(path, {
      [ENTRY_PARAM]: "clone",
      [PRODUCT_NAME_PARAM]: clampText(input.productName, 200),
      [SELLING_POINTS_PARAM]: clampText(input.sellingPoints, 400),
    }),
    storage: { key: CLONE_PREFILL_STORAGE_KEY, value: JSON.stringify(payload) },
  };
}

/** 主入口从地址栏读到的预填参数（`entry` 即 `inputMode`）。 */
export interface StartPrefill {
  entry: CreationEntryId | null;
  topic: string;
  productId: string;
  productName: string;
  sellingPoints: string;
}

export function parseStartPrefill(search: string): StartPrefill {
  const params = new URLSearchParams(search.startsWith("?") ? search : `?${search}`);
  const entry = params.get(ENTRY_PARAM);
  return {
    entry: CREATION_ENTRY_IDS.includes(entry as CreationEntryId) ? (entry as CreationEntryId) : null,
    topic: params.get(TOPIC_PARAM)?.trim() ?? "",
    productId: params.get(PRODUCT_ID_PARAM)?.trim() ?? "",
    productName: params.get(PRODUCT_NAME_PARAM)?.trim() ?? "",
    sellingPoints: params.get(SELLING_POINTS_PARAM)?.trim() ?? "",
  };
}

/** 读取爆款复刻暂存。损坏/过期内容一律当作「没有预填」，绝不抛错。 */
export function parseClonePrefill(raw: string | null): ClonePrefillPayload | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const briefRaw = parsed.brief && typeof parsed.brief === "object" ? (parsed.brief as Record<string, unknown>) : {};
    const referenceStructure =
      typeof parsed.referenceStructure === "string" ? parsed.referenceStructure.slice(0, 8000) : "";
    const referenceVideoUrl = typeof parsed.referenceVideoUrl === "string" ? parsed.referenceVideoUrl.trim().slice(0, 500) : "";
    const productImages = Array.isArray(parsed.productImages)
      ? parsed.productImages.filter((item): item is string => typeof item === "string" && item.trim().length > 0).slice(0, 5)
      : [];
    return {
      version: 1,
      brief: cloneBrief({ styleType: briefRaw.styleType, targetDuration: briefRaw.targetDuration }),
      ...(referenceStructure && { referenceStructure }),
      ...(referenceVideoUrl && { referenceVideoUrl }),
      ...(productImages.length && { productImages }),
    };
  } catch {
    return null;
  }
}
