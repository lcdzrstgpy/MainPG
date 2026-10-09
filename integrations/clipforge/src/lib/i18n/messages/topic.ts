import type { NamespaceMessages } from "../config";

// topic 命名空间词条（zh 为原文，en 为翻译）
export const topic: NamespaceMessages = {
  zh: {
    // 页面标题区
    heroBadge: "无需商品 · 一句话成片",
    heroTitle: "一句话主题成片",
    heroSubtitle:
      "输入一句话主题，接下来在视频工作台里补全创作简报（脚本风格、目标时长、出片策略）并生成脚本，再自动配齐画面、合成竖屏短视频。任何主题都能做，不局限于带货。",
    // 未配置 LLM 引导
    llmBannerTitle: "先配置 LLM 才能生成脚本",
    llmBannerDesc: "需要在「设置」里填写用于写脚本的 LLM（baseUrl / API Key / 模型）。",
    llmBannerCta: "点击前往设置 →",
    // 主题输入
    topicLabel: "一句话主题",
    topicPlaceholder: "例如：在家如何泡一杯手冲咖啡",
    tryLabel: "试试：",
    exampleTopic1: "在家如何泡一杯手冲咖啡",
    exampleTopic2: "城市夜景为什么这么治愈",
    exampleTopic3: "三个让早晨更高效的小习惯",
    exampleTopic4: "雨天适合做的五件小事",
    exampleTopic5: "为什么我们总是怀念童年",
    // 交接说明与按钮
    entryNote:
      "主题会预填进视频工作台的创作简报——脚本风格、目标时长与出片策略都在那里显式选择，确认后再统一创建项目。",
    ctaContinue: "去工作台补全简报",
    // 流程提示
    flowStep1: "1 预填主题",
    flowStep2: "2 选定风格与出片策略",
    flowStep3: "3 生成脚本与成片",
    // 错误提示
    errorNoLlm: "尚未配置 LLM，请先到「设置」填写 API Key",
  },
  en: {
    // 页面标题区
    heroBadge: "No product needed · One sentence to video",
    heroTitle: "One-sentence to video",
    heroSubtitle:
      "Type one topic, then finish the creation brief in the studio (script style, target length, output strategy), generate the script, and auto-fill footage to render a vertical short. Works for any topic, not just commerce.",
    // 未配置 LLM 引导
    llmBannerTitle: "Set up an LLM to generate scripts",
    llmBannerDesc: "Add the script-writing LLM (base URL / API key / model) in Settings.",
    llmBannerCta: "Go to Settings →",
    // 主题输入
    topicLabel: "Your topic in one sentence",
    topicPlaceholder: "e.g. How to brew a pour-over coffee at home",
    tryLabel: "Try:",
    exampleTopic1: "How to brew a pour-over coffee at home",
    exampleTopic2: "Why city nightscapes feel so soothing",
    exampleTopic3: "Three small habits for a more productive morning",
    exampleTopic4: "Five little things to do on a rainy day",
    exampleTopic5: "Why we always miss our childhood",
    // 交接说明与按钮
    entryNote:
      "Your topic is pre-filled into the studio's creation brief — script style, target length and output strategy are picked there, and the project is only created after you confirm.",
    ctaContinue: "Continue in the studio",
    // 流程提示
    flowStep1: "1 Pre-fill topic",
    flowStep2: "2 Pick style & strategy",
    flowStep3: "3 Script & video",
    // 错误提示
    errorNoLlm: "No LLM configured yet — add your API key in Settings first",
  },
};
