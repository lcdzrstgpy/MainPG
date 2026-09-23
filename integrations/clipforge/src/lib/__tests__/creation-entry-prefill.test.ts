import { describe, expect, it } from "vitest";
import { STYLE_VALUES } from "@/lib/ad-templates";
import { isOutputStrategy } from "@/lib/creation-brief";
import { resolveScriptStyle, SCRIPT_STYLE_VALUES } from "@/lib/script-style";
import { buildVariationPlan } from "@/lib/variation-plan";
import {
  CLONE_PREFILL_STORAGE_KEY,
  CREATION_ENTRY_PATH,
  ENTRY_PARAM,
  parseClonePrefill,
  parseStartPrefill,
  snapTargetDuration,
  toPrefillParams,
} from "@/lib/creation-entry-prefill";
import {
  batchAudioStrategyFor,
  batchAutoComposeEnabled,
  batchItemTargetPath,
  BATCH_OUTPUT_STRATEGIES,
  buildBatchItemBrief,
  resolveBatchItemStyle,
  type BatchItemStyle,
} from "@/lib/batch-creation-brief";

/**
 * 设计 §8 阶段 5：次级入口（话题成片 / 商品库 / 爆款复刻）不再创建自己的项目，只把用户输入
 * 映射成「一份预填 CreationBrief」交给主入口 /start；批量出片复用同一策略与项目执行器。
 *
 * 仓库没有组件渲染器，因此这里只测两件事：预填映射是可单测的纯函数；批量每一件的简报
 * （策略 / 风格 / 时长）都显式合法，经 resolveScriptStyle 一定不会落到 409。
 */

const styleOf = (source: { styleType: string }): string => source.styleType;

describe("次级入口只产出预填参数，且 inputMode 取值正确", () => {
  it("一句话主题：entry=topic + topic 文本，无其它创建语义", () => {
    const target = toPrefillParams({ kind: "topic", topic: "  在家如何泡一杯手冲咖啡  " });
    expect(target.href.startsWith(`${CREATION_ENTRY_PATH}?`)).toBe(true);
    expect(target.storage).toBeUndefined();
    const prefill = parseStartPrefill(target.href.slice(target.href.indexOf("?")));
    expect(prefill.entry).toBe("topic");
    expect(prefill.topic).toBe("在家如何泡一杯手冲咖啡");
    expect(prefill.productId).toBe("");
  });

  it("商品库：entry=product-library + productId（库内数据由主入口读取）", () => {
    const target = toPrefillParams({ kind: "product-library", productId: "p-1" });
    const prefill = parseStartPrefill(target.href.slice(target.href.indexOf("?")));
    expect(prefill.entry).toBe("product-library");
    expect(prefill.productId).toBe("p-1");
    expect(target.storage).toBeUndefined();
  });

  it("爆款复刻：entry=clone + 商品文本走 query，参考结构与商品图走 localStorage", () => {
    const target = toPrefillParams({
      kind: "clone",
      productName: "桂花乌龙茶",
      sellingPoints: "0 糖 0 卡",
      styleType: "scenario",
      targetDuration: 37,
      referenceStructure: "3 镜：0-3s / 3-8s / 8-15s",
      productImages: ["/api/files/clone-prefill-x/1.png"],
    });
    const prefill = parseStartPrefill(target.href.slice(target.href.indexOf("?")));
    expect(prefill.entry).toBe("clone");
    expect(prefill.productName).toBe("桂花乌龙茶");
    expect(prefill.sellingPoints).toBe("0 糖 0 卡");

    expect(target.storage?.key).toBe(CLONE_PREFILL_STORAGE_KEY);
    const payload = parseClonePrefill(target.storage?.value ?? null);
    expect(payload?.brief.inputMode).toBe("clone");
    expect(payload?.brief.styleType).toBe("scenario");
    expect(payload?.referenceStructure).toBe("3 镜：0-3s / 3-8s / 8-15s");
    expect(payload?.productImages).toEqual(["/api/files/clone-prefill-x/1.png"]);
    // 37s 不是合法档位：按最近档位归位，不静默改风格或策略
    expect(payload?.brief.targetDuration).toBe(30);
    expect(payload?.brief.outputStrategy).toBeUndefined();
  });

  it("暂存内容损坏/为空时不抛错，也不产生预填", () => {
    expect(parseClonePrefill(null)).toBeNull();
    expect(parseClonePrefill("not-json")).toBeNull();
    expect(parseClonePrefill('{"brief":"nope"}')?.brief.styleType).toBe("");
  });

  it("目标时长归位只落在 15/30/60", () => {
    expect(snapTargetDuration(15)).toBe(15);
    expect(snapTargetDuration(25)).toBe(30);
    expect(snapTargetDuration(40)).toBe(30);
    expect(snapTargetDuration(75)).toBe(60);
    expect(snapTargetDuration(Number.NaN)).toBe(30);
  });

  it("未知 entry 参数不被当成来源", () => {
    expect(parseStartPrefill(`?${ENTRY_PARAM}=telepathy`).entry).toBeNull();
    expect(parseStartPrefill("?productId=x").entry).toBeNull();
  });

  it("可以指向兼容路由 /project/new（旧深链接同表单）", () => {
    const target = toPrefillParams({ kind: "product-library", productId: "p-2", path: "/project/new" });
    expect(target.href.startsWith("/project/new?")).toBe(true);
    expect(parseStartPrefill(target.href.slice(target.href.indexOf("?"))).productId).toBe("p-2");
  });
});

describe("批量每一件都携带显式合法策略与风格", () => {
  /** 批量入口的选择器只会给出这些值（11 个白名单风格 + 显式「智能推荐」）。 */
  const PICKER_VALUES = [...SCRIPT_STYLE_VALUES, "auto"];
  /** buildVariationPlan 实际会轮换出来的风格（引擎词表，含 pain_point / scene 两种别名拼写）。 */
  const POOL_VALUES = [...new Set(buildVariationPlan({
    count: 8,
    category: "beauty",
    seed: 7,
  }).flatMap((slot) => (slot.styleType ? [slot.styleType] : [])))];

  it("变量矩阵确实会轮换出引擎词表的风格（保证下面的断言不是空跑）", () => {
    expect(POOL_VALUES.length).toBeGreaterThan(1);
    expect(POOL_VALUES).toContain("pain_point");
  });

  it("变量矩阵给的风格经共享解析器归一化后不会 409", () => {
    for (const slotStyle of POOL_VALUES) {
      const style = resolveBatchItemStyle({ slotStyleType: slotStyle, chosenStyle: "auto" });
      expect(style.styleSource).toBe("explicit");
      expect(style.resolvesWithoutData).toBe(true);
      expect(resolveScriptStyle({ requestedStyle: style.styleType })).toMatchObject({
        kind: "resolved",
        styleType: style.styleType,
      });
    }
  });

  it("用户手选的风格（含显式 auto）语义正确：手选即显式，auto 才是有意的推荐请求", () => {
    for (const chosen of PICKER_VALUES) {
      const style = resolveBatchItemStyle({ chosenStyle: chosen });
      if (chosen === "auto") {
        expect(style.styleSource).toBe("performance-recommendation");
        expect(style.resolvesWithoutData).toBe(false);
        continue;
      }
      expect(style).toMatchObject({ styleSource: "explicit", resolvesWithoutData: true });
      // 白名单里的 UI 风格（含 pain-point）解析结果必须还是它自己
      expect(styleOf(style)).toBe(chosen);
    }
  });

  it("锁定风格时变量矩阵不再覆盖它", () => {
    const locked = resolveBatchItemStyle({ slotStyleType: undefined, chosenStyle: "comparison" });
    expect(locked.styleType).toBe("comparison");
    expect(locked.styleSource).toBe("explicit");
  });

  it("三种出片策略每一件都是显式合法值，并写入统一简报", () => {
    const style: BatchItemStyle = { styleType: "scenario", styleSource: "explicit", resolvesWithoutData: true };
    expect(BATCH_OUTPUT_STRATEGIES).toEqual(["draft", "controlled-motion", "native-film"]);
    for (const strategy of BATCH_OUTPUT_STRATEGIES) {
      expect(isOutputStrategy(strategy)).toBe(true);
      const item = buildBatchItemBrief({
        style,
        baseDuration: 30,
        durationOffset: 3,
        strategy,
        audience: "上班族",
      });
      expect(item.brief.outputStrategy).toBe(strategy);
      expect(item.brief.audioStrategy).toBe(batchAudioStrategyFor(strategy));
      expect(item.brief.inputMode).toBe("product-library");
      expect(item.brief.styleType).toBe("scenario");
      expect([15, 30, 60]).toContain(item.brief.targetDuration);
      expect(item.brief.targetAudience).toEqual(["上班族"]);
    }
    // 原生整片自带音轨；其余策略保留统一默认，不被静默改写
    expect(batchAudioStrategyFor("native-film")).toBe("native-audio");
    expect(batchAudioStrategyFor("controlled-motion")).toBe("volcengine-tts");
    expect(batchAudioStrategyFor("draft")).toBe("volcengine-tts");
  });

  it("秒级时长抖动按 15/30/60 档位归位，并回报实际生效的差值", () => {
    const style: BatchItemStyle = { styleType: "story", styleSource: "explicit", resolvesWithoutData: true };
    for (const offset of [0, 3, -3, 5]) {
      const item = buildBatchItemBrief({ style, baseDuration: 30, durationOffset: offset, strategy: "draft" });
      expect([15, 30, 60]).toContain(item.brief.targetDuration);
      expect(item.appliedDurationOffset).toBe(item.brief.targetDuration - 30);
    }
  });

  it("免费合成链只服务于显式 draft，其余策略回到项目页显式确认", () => {
    expect(batchAutoComposeEnabled("draft", true)).toBe(true);
    expect(batchAutoComposeEnabled("draft", false)).toBe(false);
    expect(batchAutoComposeEnabled("controlled-motion", true)).toBe(false);
    expect(batchAutoComposeEnabled("native-film", true)).toBe(false);

    expect(batchItemTargetPath("p1", "draft", true)).toBe("/project/p1/export");
    expect(batchItemTargetPath("p1", "draft", false)).toBe("/project/p1/script");
    expect(batchItemTargetPath("p1", "controlled-motion", true)).toBe("/project/p1/script");
    expect(batchItemTargetPath("p1", "native-film", true)).toBe("/project/p1/script");
  });

  it("批量可选风格不超出脚本风格白名单（避免出现接口不认识的风格）", () => {
    const uiWhitelist = [...STYLE_VALUES].filter((value) => value !== "auto");
    expect([...SCRIPT_STYLE_VALUES].sort()).toEqual([...uiWhitelist].sort());
  });
});
