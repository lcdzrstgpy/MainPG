"use server";

import { recordCreationEvent } from "@/lib/creation-analytics";
import type { CreationBrief } from "@/lib/creation-brief";

/**
 * Server action：创建入口页是客户端组件，而 `recordCreationEvent` 直接写库，只能在服务端执行。
 * 这里把它包成一个可以 fire-and-forget 的动作，把「本次选定的出片策略」记成 `strategy_selected`。
 *
 * 遥测永远不能阻断创建流程：写库失败在 recordCreationEvent 内部已被吞掉，这里再兜一层，
 * 保证调用方拿不到异常。
 */
export async function recordStrategySelected(input: {
  projectId: string;
  creationBrief: CreationBrief;
}): Promise<void> {
  try {
    recordCreationEvent({
      projectId: input.projectId,
      kind: "strategy_selected",
      payload: {
        outputStrategy: input.creationBrief.outputStrategy,
        audioStrategy: input.creationBrief.audioStrategy,
        inputMode: input.creationBrief.inputMode,
        styleType: input.creationBrief.styleType,
        styleSource: input.creationBrief.styleSource,
      },
    });
  } catch (error) {
    console.warn("strategy_selected 记录失败（已忽略）:", error);
  }
}
