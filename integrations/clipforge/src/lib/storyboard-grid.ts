/**
 * 3x3 storyboard grid — single-image consistency anchoring for multi-shot videos.
 *
 * Why: chained keyframes and text appearance anchors fight identity drift shot by
 * shot; a storyboard GRID kills it at the source — one generation renders all
 * shots in one image, so the person, outfit, room and light are physically the
 * same pixels-era subject in every cell. Each cell is then cropped out and saved
 * as that shot's keyframe, and the existing i2v pass animates them.
 *
 * Geometry trick: a 3x3 grid at 9:16 overall yields cells that are each exactly
 * 9:16 — the cropped cells drop straight into our vertical pipeline.
 *
 * Pure functions (prompt building + crop geometry); the route does the I/O.
 */
import type { Shot, ScriptCharacter } from "@/lib/db/schema";
import { REAL_FACE_CONSTRAINT, UGC_FIRST_FRAME_RULES } from "@/lib/presenters";

export const GRID_ROWS = 3;
export const GRID_COLS = 3;
export const GRID_MAX_SHOTS = GRID_ROWS * GRID_COLS;

/** Shot-type label used in per-cell prompt lines (bilingual not needed: grid prompt is zh-first). */
const SHOT_TYPE_LABELS: Record<string, string> = {
  hook: "钩子镜",
  pain_point: "痛点镜",
  product_reveal: "商品镜",
  demo: "演示镜",
  social_proof: "背书镜",
  cta: "转化镜",
};

/**
 * Build the one-shot 3x3 storyboard-grid image prompt: global consistency block
 * (same person / outfit / room / light) + one numbered line per cell + realism
 * rules + hard grid-layout constraints (equal cells, thin gutters, no text —
 * cells get cropped into keyframes, so any text or borders would poison them).
 */
export function buildStoryboardGridPrompt(
  shots: Shot[],
  characters?: ScriptCharacter[] | null,
  refs?: { characterSheet?: boolean; productImage?: boolean }
): string {
  const cells = shots.slice(0, GRID_MAX_SHOTS);
  const cast = (characters ?? [])
    .map((c) => `${c.name}：${c.appearance}`)
    .filter(Boolean)
    .join("；");

  const cellLines = cells.map((s, i) => {
    const label = SHOT_TYPE_LABELS[String(s.type)] ?? "分镜";
    return `第 ${i + 1} 格（${label}）：${s.description}`;
  });

  // reference-image contract: the images array order is [character sheet?, product photo?],
  // so the prompt cites them by position (field-proven with gpt-image-2/edit)
  const refLines: string[] = [];
  if (refs?.characterSheet || refs?.productImage) {
    let n = 0;
    if (refs.characterSheet) {
      n += 1;
      refLines.push(
        `第 ${n} 张参考图是出镜人物的四视图定妆照——九格中的人物脸型、发型、体型与服装必须与其完全一致（定妆照只作人物参考，不作为分镜画面）。人物需自然融入各格自身的场景与光线，不得把定妆照的浅灰影棚背景、四格分格或边框带进任何一格。`
      );
    }
    if (refs.productImage) {
      n += 1;
      refLines.push(`第 ${n} 张参考图是商品实拍图——九格中的商品外观、配色与包装必须与其完全一致。`);
    }
  }

  return [
    `一张 ${GRID_ROWS}x${GRID_COLS} 等分九宫格分镜图，整图 9:16 竖版，格与格之间只留极细的白色分隔缝。`,
    `全局一致性（最重要）：九格是同一支视频的分镜——同一人物、同一发型与同一身衣服、同一房间、同一光线方向与色调，道具与商品在各格间保持完全一致。`,
    ...refLines,
    cast ? `人物设定：${cast}。` : "",
    `各格内容（每格是一个独立镜头的画面，构图按竖屏 9:16 设计；每格都是该镜动作即将开始前一瞬的定格，姿态里留着正要发生的势能）：`,
    ...cellLines,
    REAL_FACE_CONSTRAINT.zh + "。",
    UGC_FIRST_FRAME_RULES,
    `硬性要求：严格等分九宫格；画面里不出现任何文字、字幕、编号、水印或边框装饰；每格都是完整可独立使用的镜头画面。`,
  ]
    .filter(Boolean)
    .join("\n");
}

export interface GridCell {
  x: number;
  y: number;
  w: number;
  h: number;
}

/**
 * Crop rectangles for the grid cells, inset by `insetRatio` of the cell size on
 * every edge — generated grids never have pixel-perfect gutters, so a small
 * inset (default 2%) trims the seam residue instead of keeping it as a border.
 * Row-major order (left→right, top→bottom) matching the prompt's cell numbering.
 */
export function computeGridCells(
  width: number,
  height: number,
  opts: { rows?: number; cols?: number; insetRatio?: number } = {}
): GridCell[] {
  const rows = opts.rows ?? GRID_ROWS;
  const cols = opts.cols ?? GRID_COLS;
  const inset = opts.insetRatio ?? 0.02;
  const cellW = width / cols;
  const cellH = height / rows;
  const dx = cellW * inset;
  const dy = cellH * inset;
  const cells: GridCell[] = [];
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      cells.push({
        x: Math.round(c * cellW + dx),
        y: Math.round(r * cellH + dy),
        w: Math.round(cellW - 2 * dx),
        h: Math.round(cellH - 2 * dy),
      });
    }
  }
  return cells;
}

/**
 * 九宫格关键帧落库时写入的 prompt：带来源标记与格序号（复用判定只认这两者，见 isGridKeyframeFor）。
 */
export function gridKeyframePrompt(index: number, description?: string): string {
  return `[storyboard-grid 第${index + 1}格] ${description ?? ""}`.trim();
}

/**
 * 这张资产是不是「第 index 格」的九宫格关键帧？
 *
 * 只看**来源标记 + 格序号**，绝不比对描述文案：判官团会把 description 重写一遍，
 * 拿描述当指纹会让复用永远失效（实测 7 镜里只有 1 镜逐字相同）。
 * 「这批图是否仍属于当前脚本」由调用方用 project_events 里的 grid_generated 记录判断。
 */
export function isGridKeyframeFor(prompt: string | null, index: number): boolean {
  return (prompt ?? "").startsWith(`[storyboard-grid 第${index + 1}格]`);
}

/** 复用判定需要的最小资产字段 */
export interface GridKeyframeAsset {
  shotId: number;
  filePath: string | null;
  prompt: string | null;
}

/**
 * 已有的九宫格关键帧能不能直接复用，而不是重新画一遍？
 *
 * 判据：**每一镜**都有一张「由九宫格裁出来的、且格序号与镜头位置对应」的关键帧。
 * 用户换了脚本 → 调用方的脚本指纹对不上 → 根本不走这里；画面描述被判官改写过不算失效。
 * 为什么需要这道墙：整片链每次进入都会重跑，没有它就会把同一批分镜反复重画（真金白银）。
 */
export function reusableGridCells(
  shots: { shotId: number; description?: string }[],
  assets: GridKeyframeAsset[]
): { shotId: number; filePath: string }[] | null {
  if (shots.length === 0) return null;
  const byShot = new Map(assets.map((a) => [a.shotId, a]));
  const cells: { shotId: number; filePath: string }[] = [];
  for (const [index, shot] of shots.entries()) {
    const asset = byShot.get(shot.shotId);
    if (!asset?.filePath) return null;
    if (!isGridKeyframeFor(asset.prompt, index)) return null;
    cells.push({ shotId: shot.shotId, filePath: asset.filePath });
  }
  return cells;
}
