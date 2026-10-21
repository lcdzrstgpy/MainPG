/**
 * 抽取自 integrations/clipforge/src/lib/providers/types.ts 的 `GenerationMode`
 * （原文件第 9–14 行）。
 * 只读同步：类型以 ClipForge 上游为准；修改请先改上游再同步，禁止在本文件单向漂移。
 *
 * 只抽取这一个联合类型，不引入 providers 实现（不在第一批复制范围）。
 */

export type GenerationMode =
  | "text-to-image"
  | "image-to-image"
  | "text-to-video"
  | "image-to-video"
  | "video-to-video";
