/**
 * 兼容入口：模块入口统一由 `AiVideoShell` 承担（Phase 3 已删除 iframe 与桥接）。
 * 外层继续从这里取 `AiVideoPage`，避免一次性改动所有引用。
 */
export { AiVideoShell as AiVideoPage } from "./AiVideoShell";
export type { AiVideoNavHref, AiVideoNavRequest } from "./AiVideoShell";
