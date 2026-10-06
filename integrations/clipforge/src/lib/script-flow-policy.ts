import type { OutputStrategy } from "@/lib/creation-brief";

/**
 * 脚本页「当前出片策略下该走哪条路」的唯一判据。
 *
 * - draft：免费静态草稿就是用户选的策略，主操作是免费草稿自动成片；
 * - controlled-motion：主操作是进素材页逐镜生成动态（付费）；
 * - native-film：主操作是整片预览 + 确认付费；
 * - null/undefined（旧项目没有 creationBrief 列）：保留原双入口与断点恢复行为。
 */

export type ScriptPrimaryAction =
  | "draft-auto-finish"
  | "controlled-motion-assets"
  | "native-film-preview"
  | "legacy";

export interface ScriptFlowPolicyArgs {
  outputStrategy: OutputStrategy | null | undefined;
  /** 界面模式只决定界面复杂度，绝不影响出片策略（见 resolveScriptFlowPolicy 的说明）。 */
  uiMode?: "simple" | "pro" | null;
  /** ?auto=1 是否已触发（隐式启动免费链的意图）。 */
  autoMode?: boolean;
  /**
   * 简报读取状态（默认 "loaded"）：loading / failed 时绝不隐式自动启动免费链——
   * 简报读不到的付费项目不能被误判成 legacy 旧项目而误启免费链。
   */
  briefState?: "loading" | "loaded" | "failed";
}

export interface ScriptFlowPolicy {
  /** 归一化后的策略名（旧项目归一为 legacy）。 */
  strategy: "draft" | "controlled-motion" | "native-film" | "legacy";
  /** 该策略下脚本页的主操作。 */
  primaryAction: ScriptPrimaryAction;
  /** ?auto=1 是否允许隐式启动免费草稿链（draft / legacy 且 autoMode）。 */
  allowAutoStart: boolean;
  /** 该策略是否允许免费草稿链（autoFinish 自身的门槛：只有 draft / legacy）。 */
  allowFreeChain: boolean;
  /** 是否展示免费快剪入口（draft / legacy）。 */
  showDraftAction: boolean;
  /** 是否展示「进入素材页逐镜动态」入口（仅 controlled-motion）。 */
  showControlledMotionAction: boolean;
  /** 是否展示整片预览入口（native-film + legacy 保留旧双入口）。 */
  showNativeFilmAction: boolean;
}

export function resolveScriptFlowPolicy(args: ScriptFlowPolicyArgs): ScriptFlowPolicy {
  const autoMode = !!args.autoMode;
  // 简报未读定（loading/failed）时关闭全部生成动作：自动启动、免费链、整片预览、恢复/重跑
  // 都不允许——只有「成功读到简报」（含读到 null=旧项目）才算 settled
  const briefSettled = (args.briefState ?? "loaded") === "loaded";
  // uiMode 被刻意忽略：界面模式（小白/导演）绝不能改判出片策略，
  // 否则切换界面会把付费策略降级成免费草稿、或反之改变计费路径。
  switch (args.outputStrategy) {
    case "draft":
      return {
        strategy: "draft",
        primaryAction: "draft-auto-finish",
        allowAutoStart: autoMode && briefSettled,
        allowFreeChain: briefSettled,
        showDraftAction: briefSettled,
        showControlledMotionAction: false,
        showNativeFilmAction: false,
      };
    case "controlled-motion":
      return {
        strategy: "controlled-motion",
        primaryAction: "controlled-motion-assets",
        allowAutoStart: false,
        allowFreeChain: false,
        showDraftAction: false,
        showControlledMotionAction: briefSettled,
        showNativeFilmAction: false,
      };
    case "native-film":
      return {
        strategy: "native-film",
        primaryAction: "native-film-preview",
        allowAutoStart: false,
        allowFreeChain: false,
        showDraftAction: false,
        showControlledMotionAction: false,
        showNativeFilmAction: briefSettled,
      };
    default:
      // null / undefined（旧项目）与未知字符串都回退到 legacy：保留原双入口与断点恢复行为。
      // 但只有简报读定才放开动作；loading/failed 时双入口全部关闭（见上方 briefSettled）。
      return {
        strategy: "legacy",
        primaryAction: "legacy",
        allowAutoStart: autoMode && briefSettled,
        allowFreeChain: briefSettled,
        showDraftAction: briefSettled,
        showNativeFilmAction: briefSettled,
        showControlledMotionAction: false,
      };
  }
}