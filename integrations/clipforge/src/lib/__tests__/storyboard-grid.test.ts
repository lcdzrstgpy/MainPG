import { describe, it, expect } from "vitest";
import { buildStoryboardGridPrompt, computeGridCells, gridKeyframePrompt, reusableGridCells, GRID_MAX_SHOTS } from "@/lib/storyboard-grid";
import type { Shot, ScriptCharacter } from "@/lib/db/schema";

const shot = (shotId: number, type: string, description: string) =>
  ({ shotId, type, duration: 3, description, voiceover: "词", visualSource: "ai_generate", transition: "cut" }) as unknown as Shot;

describe("buildStoryboardGridPrompt", () => {
  const shots = [shot(1, "hook", "女生对镜头惊讶"), shot(2, "demo", "上手使用产品"), shot(3, "cta", "举起产品推荐")];
  const cast: ScriptCharacter[] = [
    { id: "char_a", name: "小美", gender: "female", persona: "活泼", appearance: "22 岁高马尾白 T 恤" } as ScriptCharacter,
  ];

  it("全局一致性块 + 人物设定 + 逐格行 + 真实感规则 + 无文字硬约束", () => {
    const p = buildStoryboardGridPrompt(shots, cast);
    expect(p).toContain("同一人物、同一发型与同一身衣服、同一房间");
    expect(p).toContain("小美：22 岁高马尾白 T 恤");
    expect(p).toContain("第 1 格（钩子镜）：女生对镜头惊讶");
    expect(p).toContain("第 3 格（转化镜）：举起产品推荐");
    expect(p).toContain("不是精修网红脸"); // REAL_FACE 仍然生效
    expect(p).toContain("光要写满四要素"); // UGC 首帧规则搭车
    expect(p).toContain("不出现任何文字"); // 裁切后当关键帧，文字会毒化画面
  });

  it("超过 9 镜截断到 9 格；无角色时不输出人物设定行", () => {
    const many = Array.from({ length: 12 }, (_, i) => shot(i + 1, "demo", `镜头${i + 1}`));
    const p = buildStoryboardGridPrompt(many);
    expect(p).toContain(`第 ${GRID_MAX_SHOTS} 格`);
    expect(p).not.toContain("第 10 格");
    expect(p).not.toContain("人物设定");
  });

  it("参考图约定：定妆照+商品图按序编号；只有商品图时商品是第 1 张", () => {
    const both = buildStoryboardGridPrompt(shots, cast, { characterSheet: true, productImage: true });
    expect(both).toContain("第 1 张参考图是出镜人物的四视图定妆照");
    expect(both).toContain("第 2 张参考图是商品实拍图");
    const productOnly = buildStoryboardGridPrompt(shots, cast, { productImage: true });
    expect(productOnly).toContain("第 1 张参考图是商品实拍图");
    expect(productOnly).not.toContain("定妆照");
    const none = buildStoryboardGridPrompt(shots, cast);
    expect(none).not.toContain("参考图");
  });
});

describe("computeGridCells", () => {
  it("9 格行主序等分 + inset 内缩几何正确", () => {
    const cells = computeGridCells(900, 1600, { insetRatio: 0 });
    expect(cells.length).toBe(9);
    expect(cells[0]).toEqual({ x: 0, y: 0, w: 300, h: 533 });
    expect(cells[1].x).toBe(300); // 行主序：第二格在右侧
    expect(cells[3].y).toBe(533); // 第四格进入第二行
    expect(cells[8]).toEqual({ x: 600, y: 1067, w: 300, h: 533 });
  });

  it("整图 9:16 时每格也是 9:16（竖屏关键帧免二次裁）", () => {
    const [cell] = computeGridCells(1080, 1920, { insetRatio: 0 });
    expect(cell.w / cell.h).toBeCloseTo(9 / 16, 2);
  });

  it("默认 2% 内缩裁掉格间缝残留", () => {
    const cells = computeGridCells(900, 1600);
    expect(cells[0].x).toBeGreaterThan(0);
    expect(cells[0].w).toBeLessThan(300);
    // 内缩对称：格宽 = 原格宽 - 2*内缩
    expect(cells[0].w).toBe(300 - 2 * Math.round(300 * 0.02));
  });
});

/**
 * 复用判定：整片链每次进入都会重跑九宫格这道工序，没有「已有就复用」就会把同一批分镜
 * 反复重画（真花钱，用户也会觉得上次白做）。判据是「每一镜都有一张内嵌描述与当前脚本
 * 逐字一致的九宫格关键帧」。
 */
describe("reusableGridCells（九宫格复用判定）", () => {
  const shots = [
    { shotId: 1, description: "把口红放进包里" },
    { shotId: 2, description: "对镜头举起化妆包" },
  ];
  const asset = (shotId: number, index: number, description: string, filePath: string | null = `/api/files/p/asset-${shotId}.png`) => ({
    shotId,
    filePath,
    prompt: gridKeyframePrompt(index, description),
  });

  it("每一镜都有匹配当前脚本的关键帧 → 复用，且按分镜顺序返回", () => {
    expect(reusableGridCells(shots, [asset(2, 1, "对镜头举起化妆包"), asset(1, 0, "把口红放进包里")])).toEqual([
      { shotId: 1, filePath: "/api/files/p/asset-1.png" },
      { shotId: 2, filePath: "/api/files/p/asset-2.png" },
    ]);
  });

  it("缺任何一镜的关键帧 → 不复用（重新画）", () => {
    expect(reusableGridCells(shots, [asset(1, 0, "把口红放进包里")])).toBeNull();
  });

  it("描述被判官团改写 → 仍然复用（判据只看来源与格序号，不看描述文案）", () => {
    const existing = [asset(1, 0, "把口红放进包里"), asset(2, 1, "对镜头举起化妆包")];
    expect(reusableGridCells([shots[0], { shotId: 2, description: "改成对镜头微笑" }], existing)).toEqual([
      { shotId: 1, filePath: "/api/files/p/asset-1.png" },
      { shotId: 2, filePath: "/api/files/p/asset-2.png" },
    ]);
  });

  it("格序号错位（第2格的图挂到了第1镜）→ 不复用", () => {
    const swapped = [asset(1, 1, "对镜头举起化妆包"), asset(2, 0, "把口红放进包里")];
    expect(reusableGridCells(shots, swapped)).toBeNull();
  });

  it("不是九宫格产出的关键帧（逐镜生成等）不算复用", () => {
    const foreign = [{ shotId: 1, filePath: "/x.png", prompt: "逐镜生成的画面" }, asset(2, 1, "对镜头举起化妆包")];
    expect(reusableGridCells(shots, foreign)).toBeNull();
  });

  it("资产没有文件路径 → 不算数", () => {
    expect(reusableGridCells(shots, [asset(1, 0, "把口红放进包里", null), asset(2, 1, "对镜头举起化妆包")])).toBeNull();
  });

  it("空脚本不复用", () => {
    expect(reusableGridCells([], [])).toBeNull();
  });
});

describe("gridKeyframePrompt（落库 prompt 兼复用指纹）", () => {
  it("带来源标记与格序号", () => {
    expect(gridKeyframePrompt(0, "第一镜")).toBe("[storyboard-grid 第1格] 第一镜");
    expect(gridKeyframePrompt(2, undefined)).toBe("[storyboard-grid 第3格]");
  });
});
