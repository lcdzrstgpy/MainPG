import type { OutputStrategy } from "@/lib/creation-brief";

export type StepperStepKey = "script" | "assets" | "video" | "export";
export type StepperStepStatus = "main" | "optional";
export interface StepperStepView {
  key: StepperStepKey;
  status: StepperStepStatus | null;
  hint?: string;
}

const STEP_ORDER: readonly StepperStepKey[] = ["script", "assets", "video", "export"];

const step = (key: StepperStepKey, status: StepperStepStatus | null, hint?: string): StepperStepView =>
  hint ? { key, status, hint } : { key, status };

/**
 * 每个出片策略在四步流水线（脚本/素材/视频/导出）里的主次标注。
 * status = "main"（主路径）/ "optional"（可选工具）；"skipped" 不单列，只用
 * hint 文案里的「已跳过」表达。hint 一律是 i18n key（common 命名空间，中英双语文案），
 * 由 ProjectStepper 用 useT 解析，绝不在这里写死任何语言。null/undefined（旧项目）
 * 全部返回 status=null，展示上与今天完全一致（纯胶囊，无徽标、无提示）。
 *
 * 仅影响展示，不改变任何页面的可访问性：可选步骤仍可手动进入。
 */
export function resolveStepperSteps(strategy: OutputStrategy | null | undefined): StepperStepView[] {
  switch (strategy) {
    case "draft":
      return [
        step("script", "main"),
        step("assets", "optional", "stepDraftAssetsHint"),
        step("video", "optional", "stepDraftVideoHint"),
        step("export", "main"),
      ];
    case "controlled-motion":
      return [
        step("script", "main"),
        step("assets", "main"),
        step("video", "main"),
        step("export", "main"),
      ];
    case "native-film":
      return [
        step("script", "main", "stepFilmScriptHint"),
        step("assets", "optional", "stepFilmAssetsHint"),
        step("video", "optional", "stepFilmVideoHint"),
        step("export", "main"),
      ];
    default:
      return STEP_ORDER.map((key) => step(key, null));
  }
}