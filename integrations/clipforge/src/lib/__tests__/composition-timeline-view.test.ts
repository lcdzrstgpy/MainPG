import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import {
  compositionChoiceLabel,
  compositionChoices,
  pickCompositionId,
  type CompositionChoice,
} from "@/lib/composition-timeline-view";

/**
 * video 页成片版本选择器的纯逻辑契约（页面本身只断言接线）。
 */
const choice = (over: Partial<CompositionChoice> & { id: string }): CompositionChoice => ({
  fileName: null,
  label: null,
  createdAt: null,
  ...over,
});

describe("compositionChoices", () => {
  it("保留列表顺序（新到旧），并只取 id 合法的条目", () => {
    const choices = compositionChoices([
      { id: "take-2", fileName: "final_2.mp4" },
      { id: "take-1", label: "免费草稿 · 静态合成" },
    ]);
    expect(choices.map((c) => c.id)).toEqual(["take-2", "take-1"]);
    expect(choices[0].fileName).toBe("final_2.mp4");
    expect(choices[1].label).toBe("免费草稿 · 静态合成");
  });

  it("丢掉脏数据：非数组、非对象、id 非法或重复", () => {
    expect(compositionChoices(null)).toEqual([]);
    expect(compositionChoices("oops")).toEqual([]);
    expect(
      compositionChoices([
        null,
        "take-1",
        { id: "../take-1" },
        { id: "" },
        { id: "take 1" },
        { id: "take-1" },
        { id: "take-1" },
      ]).map((c) => c.id)
    ).toEqual(["take-1"]);
  });

  it("空字符串字段归一化为 null，时间只接受可读字符串", () => {
    const [only] = compositionChoices([{ id: "take-1", fileName: "  ", label: " ", createdAt: 123 }]);
    expect(only).toEqual({ id: "take-1", fileName: null, label: null, createdAt: null });
  });
});

describe("pickCompositionId", () => {
  const list = [choice({ id: "newest" }), choice({ id: "older" })];

  it("默认选最新一条（列表第一条）", () => {
    expect(pickCompositionId(list)).toBe("newest");
    expect(pickCompositionId(list, null)).toBe("newest");
  });

  it("保留用户已选且仍存在的版本", () => {
    expect(pickCompositionId(list, "older")).toBe("older");
  });

  it("用户选中的版本已不存在时退回最新一条，空列表返回 null", () => {
    expect(pickCompositionId(list, "deleted")).toBe("newest");
    expect(pickCompositionId([])).toBeNull();
    expect(pickCompositionId(null)).toBeNull();
    expect(pickCompositionId(undefined, "older")).toBeNull();
  });
});

describe("compositionChoiceLabel", () => {
  it("优先显示变体标签，其次文件名，最后按序号兜底", () => {
    expect(compositionChoiceLabel(choice({ id: "a", label: "疑问钩子×卡拉OK", fileName: "final_1.mp4" }), 0)).toBe("疑问钩子×卡拉OK");
    expect(compositionChoiceLabel(choice({ id: "a", fileName: "final_1.mp4" }), 0)).toBe("final_1.mp4");
    expect(compositionChoiceLabel(choice({ id: "a" }), 2)).toBe("版本 3");
    expect(compositionChoiceLabel(null, 0)).toBe("版本 1");
  });

  it("附加可解析的创建时间，时间非法时只显示名字", () => {
    const label = compositionChoiceLabel(choice({ id: "a", label: "免费草稿", createdAt: "2026-09-22T10:00:00.000Z" }), 0);
    expect(label.startsWith("免费草稿 · ")).toBe(true);
    expect(label).toMatch(/\d{4}-\d{2}-\d{2} \d{2}:\d{2}/);

    expect(compositionChoiceLabel(choice({ id: "a", label: "免费草稿", createdAt: "not-a-date" }), 0)).toBe("免费草稿");
  });
});

describe("video 页接线（源码契约）", () => {
  const page = readFileSync(resolve(process.cwd(), "src/app/project/[id]/video/page.tsx"), "utf8");

  it("音频报告按用户选中的成片版本读取，而不是「最新一条」", () => {
    expect(page).toMatch(/\/compositions\/\$\{selectedCompositionId\}\/timeline/);
    expect(page).toMatch(/pickCompositionId\(/);
  });

  it("渲染成片版本选择器，并在切换版本时重读报告", () => {
    expect(page).toMatch(/data-composition-picker/);
    expect(page).toMatch(/compositionChoiceLabel\(/);
    expect(page).toMatch(/setVoiceReport\(null\)/);
  });
});
