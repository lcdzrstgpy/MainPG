import { describe, it, expect } from "vitest";
import { resolveStepperSteps, type StepperStepView } from "@/lib/stepper-flow-policy";

const statusOf = (rows: StepperStepView[], key: string) => rows.find((r) => r.key === key)?.status;
const hintOf = (rows: StepperStepView[], key: string) => rows.find((r) => r.key === key)?.hint;

describe("resolveStepperSteps：draft（免费草稿）", () => {
  it("脚本/导出是主路径，素材/视频是可选手动进入", () => {
    const rows = resolveStepperSteps("draft");
    expect(rows.map((r) => r.key)).toEqual(["script", "assets", "video", "export"]);
    expect(statusOf(rows, "script")).toBe("main");
    expect(statusOf(rows, "assets")).toBe("optional");
    expect(statusOf(rows, "video")).toBe("optional");
    expect(statusOf(rows, "export")).toBe("main");
  });

  it("素材/视频的可选提示经 i18n key 给出（已跳过/手动进入语义在文案里）", () => {
    const rows = resolveStepperSteps("draft");
    expect(hintOf(rows, "assets")).toBe("stepDraftAssetsHint");
    expect(hintOf(rows, "video")).toBe("stepDraftVideoHint");
  });
});

describe("resolveStepperSteps：controlled-motion（受控动态）", () => {
  it("四步全为主路径，且不带任何提示", () => {
    const rows = resolveStepperSteps("controlled-motion");
    for (const key of ["script", "assets", "video", "export"]) {
      expect(statusOf(rows, key)).toBe("main");
      expect(hintOf(rows, key)).toBeUndefined();
    }
  });
});

describe("resolveStepperSteps：native-film（原生整片）", () => {
  it("脚本/导出主路径，素材/视频为可选工具", () => {
    const rows = resolveStepperSteps("native-film");
    expect(statusOf(rows, "script")).toBe("main");
    expect(statusOf(rows, "assets")).toBe("optional");
    expect(statusOf(rows, "video")).toBe("optional");
    expect(statusOf(rows, "export")).toBe("main");
  });

  it("脚本/素材/视频的提示经 i18n key 给出", () => {
    const rows = resolveStepperSteps("native-film");
    expect(hintOf(rows, "script")).toBe("stepFilmScriptHint");
    expect(hintOf(rows, "assets")).toBe("stepFilmAssetsHint");
    expect(hintOf(rows, "video")).toBe("stepFilmVideoHint");
  });
});

describe("resolveStepperSteps：旧项目（null/undefined）", () => {
  it.each([null, undefined])("返回四步全 status=null，无提示（与今天展示一致）", (strategy) => {
    const rows = resolveStepperSteps(strategy);
    expect(rows.map((r) => r.key)).toEqual(["script", "assets", "video", "export"]);
    for (const row of rows) {
      expect(row.status).toBeNull();
      expect(row.hint).toBeUndefined();
    }
  });
});

describe("resolveStepperSteps：纯函数（只影响展示，无副作用）", () => {
  it("strategy 只决定返回值的展示字段，不影响四步顺序与 keys", () => {
    const keys = ["script", "assets", "video", "export"];
    const strategies = ["draft", "controlled-motion", "native-film"] as const;
    for (const strategy of strategies) {
      expect(resolveStepperSteps(strategy).map((r) => r.key)).toEqual(keys);
    }
  });

  it("每次调用返回独立数组，调用方改动不会污染下一次结果", () => {
    const a = resolveStepperSteps("draft");
    a[0].status = "optional";
    a.push({ key: "script", status: "main" });
    expect(resolveStepperSteps("draft")[0].status).toBe("main");
  });
});