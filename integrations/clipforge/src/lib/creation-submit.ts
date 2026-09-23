import type { CreationBrief } from "@/lib/creation-brief";
import type { TopicNarrationStyle } from "@/lib/script-engine/prompts";

/**
 * 一句话主题（非带货）链路的请求构造器。
 *
 * 主题项目不再拿空的 productName 去打带货脚本接口：`/api/topic/script` 只认识
 * topic + narrationStyle + targetDuration + llmConfig，两个创建入口（/start、/project/new）
 * 共用这一份键集，脚本页的主题分支也用同一份旁白风格白名单。
 */
export interface TopicScriptRequestInput {
  projectId: string;
  topic: string;
  brief: CreationBrief;
  llmConfig: { baseUrl: string; apiKey: string; model: string };
}

/** 主题引擎的旁白风格白名单，与 `/api/topic/script` 的校验同源（TopicNarrationStyle）。 */
const TOPIC_NARRATION_STYLES: readonly TopicNarrationStyle[] = [
  "knowledge",
  "story",
  "lifestyle",
  "inspiration",
  "travel",
];

export function isTopicNarrationStyle(value: string): value is TopicNarrationStyle {
  return (TOPIC_NARRATION_STYLES as readonly string[]).includes(value);
}

/**
 * 简报里的 `styleType` 是带货脚本风格词表（drama / scenario / …），与主题引擎的旁白风格不同名。
 * 这里只做白名单透传：用户选到的值本身合法时原样保留，认不出的一律落到引擎既有的默认
 * knowledge —— 不从题材或语气猜一个「故事感」风格出来。
 */
export function topicNarrationStyleFor(styleType: string): TopicNarrationStyle {
  return isTopicNarrationStyle(styleType) ? styleType : "knowledge";
}

/** `/api/topic/script` 的请求体：主题文案 + 用户的时长偏好 + 旁白风格 + LLM 配置。 */
export function buildTopicScriptRequest(input: TopicScriptRequestInput): Record<string, unknown> {
  return {
    projectId: input.projectId,
    topic: input.topic.trim(),
    narrationStyle: topicNarrationStyleFor(input.brief.styleType),
    targetDuration: input.brief.targetDuration,
    llmConfig: {
      baseUrl: input.llmConfig.baseUrl,
      apiKey: input.llmConfig.apiKey,
      model: input.llmConfig.model,
    },
  };
}
